#!/usr/bin/env python
"""Astro Coverage Planner: Flask server.

Serves an Aladin Lite viewer over a sky-coverage manifest describing which
targets you've imaged, in what filters, for how long. See README.md for the
manifest schema.

Endpoints:
- GET /                          frontend
- GET /api/manifest              slim manifest (no large path lists)
- GET /api/target/<id>           full per-target detail
- GET /api/catalogs              optional overlay catalogs
- GET /api/catalog-registry      declarative list of catalogues to surface in the rail
- GET /api/tile-sources          metadata for every registered PrioritisedTilesSource
- GET /api/tiles/<source_id>     tile list for one source (filtered server-side optionally)
- GET /api/saved-searches, POST  CRUD for saved Inventory filter bundles
- DELETE /api/saved-searches/<id>
- GET /api/sources               list of registered coverage sources
- GET /api/moc/<source_id>       FITS MOC blob for a survey source (lazy-fetched, cached)
- GET /api/observability         altaz for all targets at (lat, lon, time)
- GET /api/visibility            12-month per-target visibility bins for a site
- GET /api/visibility/point      same bins for an arbitrary (ra, dec) point
- POST /api/visibility/panels    aggregated bins for a list of mosaic panels
- POST /api/visibility/panels/batch  the same for several plans in one request
- GET /api/sites, POST           CRUD for saved observing sites
- GET /api/hidden, POST          hide or unhide a plan, project or coverage target
- GET /api/export/priority       CSV of gap-mode candidates
- GET /api/gaps                  multi-source gap-finder (JSON)
- GET /api/gaps/moc.fits         gap MOC as raw FITS bytes
- GET /api/scan/status           state of the archive rescan, with stage progress
- POST /api/scan/start           start one scan now (the setup page's Scan button)
- GET /setup                     first-run and settings page
- POST /api/settings/check-path  check one image folder, write nothing
- POST /api/catalogues/fetch     download the catalogue overlays in the background
- GET /api/catalogues/status     state of that download
- GET/PUT /api/settings           settings file: folders, sources, locks (docs/api.md)
- POST /api/settings/adopt-env    copy FITS_ROOTS into the settings file
- POST /api/quit                 stop a packaged ACP (404 unless ACP_PACKAGED=1)
- GET /api/version               app version + plans/manifest mtimes, for the NINA plugin's poll loop

Env:
- MANIFEST_PATH   path to archive manifest JSON  (default: ./data/manifest.json)
- CATALOGS_PATH   path to catalogs JSON          (default: ./data/catalogs.json)
- ACP_SURVEYS_PATH  path to survey registry JSON (default: ./data/surveys.json)
- HOST            bind host                      (default: 127.0.0.1)
- PORT            bind port                      (default: 5555)
- ACP_EXTENSIONS_DIR  directory of optional extension modules (default: unset)
- ACP_FRIEND_MANIFESTS  semicolon-separated paths to sanitised friend manifests (default: unset)
- ACP_SCAN_CRON   cron expression for an automatic archive rescan (default: unset, no rescan)
"""
from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import hmac
import json
import logging
import math
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path

from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   send_from_directory)
from werkzeug.exceptions import HTTPException

# Reconfigure stdout/stderr to UTF-8 so non-ASCII characters in log/print
# output don't crash on Windows where the default console codec (cp1252)
# can't encode characters like em-dashes or arrows.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass

try:
    from mocpy import MOC  # noqa: F401
    _MOCPY_AVAILABLE = True
except ImportError:
    _MOCPY_AVAILABLE = False
    logging.warning(
        "mocpy is not installed: MOC overlays disabled. "
        "Install with `pip install mocpy>=0.13` to enable /api/moc/<id>."
    )

import acp_settings
import activity
import shortcuts as acp_shortcuts
import balancer
import filter_rows as fr
import gear_templates as gt
from gaps import candidates_in_moc, compute_gap_moc

# Ceiling on a single acquired-hours report. Ten years of continuous imaging is
# about 87,600 hours, so nothing honest comes close. It exists because progress
# only ever raises a goal without `force`: one absurd value would write a number
# this endpoint could never correct again.
MAX_ACQUIRED_HOURS = 100_000.0

# No git tag or Dockerfile label carries a version today; this is the
# first one, bumped by hand until a release process picks it up. The NINA
# plugin polls GET /api/version to know when to refetch plans, so this
# constant matters more for cache-busting than for human version pride.
VERSION = "1.0.0"

REPO_ROOT = Path(__file__).resolve().parent
data_path = acp_settings.data_path
MANIFEST_PATH = Path(acp_settings.resolve_all()["manifest_path"]["value"]
                     or data_path("manifest.json"))
CATALOGS_PATH = Path(os.environ.get("CATALOGS_PATH", data_path("catalogs.json")))
GEAR_PATH = Path(os.environ.get("GEAR_PATH", data_path("gear.json")))
PLANS_PATH = Path(os.environ.get("PLANS_PATH", data_path("plans.json")))
# Last gear fingerprint each NINA profile reported to /api/plans/match.
FINGERPRINTS_PATH = Path(os.environ.get(
    "FINGERPRINTS_PATH", data_path("fingerprints.json")))
# Where the live-page publisher writes shooting.json before uploading it.
LIVE_OUT_DIR = Path(os.environ.get("ACP_LIVE_OUT_DIR", data_path("live")))
SITES_PATH = Path(os.environ.get("SITES_PATH", data_path("sites.json")))
DESTINATIONS_PATH = Path(os.environ.get(
    "DESTINATIONS_PATH", data_path("destinations.json")))
SAVED_SEARCHES_PATH = Path(os.environ.get(
    "SAVED_SEARCHES_PATH", data_path("saved_searches.json")))
TARGET_OVERRIDES_PATH = Path(os.environ.get(
    "TARGET_OVERRIDES_PATH", data_path("target_overrides.json")))
# Plans, projects and coverage targets the user has hidden (test shots and
# the like). Its own file, not a key in plans.json, because the plan save
# route replaces the whole plan and would drop a flag the editor never sent.
HIDDEN_PATH = Path(os.environ.get("HIDDEN_PATH", data_path("hidden.json")))
TS_TEMPLATES_PATH = Path(os.environ.get(
    "TS_TEMPLATES_PATH", data_path("ts_templates.json")))
TS_DB_PATH = os.environ.get(
    "TS_DB_PATH",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "NINA" / "SchedulerPlugin" / "schedulerdb.sqlite"),
)
ZIP_OUTPUT_DIR = Path(os.environ.get("ZIP_OUTPUT_DIR", data_path("exports")))
SURVEYS_PATH = Path(os.environ.get("ACP_SURVEYS_PATH", REPO_ROOT / "data" / "surveys.json"))
CATALOG_REGISTRY_PATH = Path(os.environ.get(
    "ACP_CATALOG_REGISTRY", REPO_ROOT / "data" / "catalog_registry.json"))
MOC_CACHE_DIR = Path(os.environ.get("ACP_MOC_CACHE_DIR", data_path("moc_cache")))
SCAN_SCRIPT_PATH = REPO_ROOT / "scripts" / "build_archive_manifest.py"

# Hostname allowlist for MOC fetches. Both entries point at the same CDS
# infrastructure: alasky.u-strasbg.fr is the legacy hostname kept alive for
# backward compat. New entries should be reviewed in PR: this is the trust
# boundary for the only ACP feature that fetches over the network at runtime.
_MOC_ALLOWED_HOSTS = frozenset({"alasky.cds.unistra.fr", "alasky.u-strasbg.fr"})
_MOC_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_MOC_FETCH_TIMEOUT_S = 30
_MOC_CACHE_TTL_S = 30 * 24 * 3600  # 30 days

# Browsers refuse to run a module script unless it arrives as JavaScript.
# Flask picks the Content-Type from Python's mimetypes table, and on Windows
# that table also absorbs registry entries, where some programs register
# .mjs and .js as text/plain. Pin both so the front end loads everywhere
# (issue #46).
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
# 0 makes browsers revalidate every load (a 304 when unchanged). The page's
# modules carry no version in their URLs, so any cache lifetime lets a
# reload after a deploy run old scripts against the new API, which wiped a
# plan's filters on 13 Oct 2026.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = int(os.environ.get("ACP_STATIC_MAX_AGE_S", 0))

# Every write endpoint takes a small JSON document. The largest legitimate
# body is a 400-panel mosaic sent to /api/visibility/panels, around 25 KB,
# and a full gear document, around 10 KB. One megabyte leaves room for an
# archive far larger than any reported and still refuses the case that
# prompted this: an unauthenticated caller posting megabytes that
# /api/plans/match then wrote to disk and kept forever.
MAX_BODY_BYTES = int(os.environ.get("ACP_MAX_BODY_BYTES", 1024 * 1024))
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES

# Every route that reads plans.json, changes it and writes it back holds this
# lock, and so does the NINA Target Scheduler extension's push and import,
# which adopts it in register(app). Without it AAC's /accepted could write
# back an older copy over a push's, and the reverse. ACP runs as one process
# under waitress, so a thread lock is enough.
PLANS_LOCK = threading.RLock()
app.plans_lock = PLANS_LOCK

# Every write to gear.json holds this (docs/specs/gear-templates.md section
# 4). A gear write that also changes plans takes GEAR_LOCK first and
# PLANS_LOCK second, and code that holds PLANS_LOCK never takes GEAR_LOCK
# except the one-off migration, which runs at startup before any request.
GEAR_LOCK = threading.RLock()
app.gear_lock = GEAR_LOCK


# Activity events a route wants recorded wait here until PLANS_LOCK is let go,
# so the SQLite write never holds up a plans.json save (docs/specs/activity-tab.md,
# Storage). A thread that is not inside a plans route records straight away.
_activity_pending = threading.local()


def _run_activity(fn) -> None:
    try:
        if activity.enabled():
            fn()
    except Exception:
        logging.exception("could not record an activity event")


def _activity_later(fn) -> None:
    calls = getattr(_activity_pending, "calls", None)
    if calls is None:
        _run_activity(fn)
    else:
        calls.append(fn)


def _holds_plans_lock(fn):
    """Run a plans.json read-modify-write route under PLANS_LOCK. Looked up
    per call, so a test can swap the lock. Activity events the route queued
    with _activity_later are recorded once the lock is released."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        outermost = getattr(_activity_pending, "calls", None) is None
        if outermost:
            _activity_pending.calls = []
        try:
            with PLANS_LOCK:
                result = fn(*args, **kwargs)
        except BaseException:
            if outermost:
                _activity_pending.calls = None
            raise
        if outermost:
            calls, _activity_pending.calls = _activity_pending.calls, None
            for call in calls:
                _run_activity(call)
        return result
    return wrapper


def record_activity(**fields):
    """ACP's own entry in the Activity tab. Extensions call it as
    ``app.record_activity`` and skip when the attribute is missing."""
    return activity.record(**fields)


app.record_activity = record_activity
activity.set_default_db_path(lambda: PLANS_PATH.with_name("activity.sqlite"))

# NINA profiles that have reported a fingerprint. A real user has a handful.
# The key is caller-supplied, so without a ceiling anyone on the LAN can grow
# the file without limit.
MAX_FINGERPRINT_PROFILES = int(os.environ.get("ACP_MAX_FINGERPRINT_PROFILES", 50))


@app.errorhandler(413)
def _body_too_large(_exc):
    """JSON, not Flask's HTML page. The NINA plugin parses every response it
    gets from ACP, so an HTML error body reads to it as a broken server.

    Reads ``request.max_content_length`` rather than the global
    ``MAX_BODY_BYTES``, because a route can raise its own limit for one
    request by setting that attribute before reading the body (Flask 3.1+).
    Flask falls back to ``app.config["MAX_CONTENT_LENGTH"]`` when a route
    sets nothing, so this names the right limit either way.
    """
    limit = request.max_content_length
    return jsonify({"error": f"request body larger than {limit} bytes",
                    "max_bytes": limit}), 413



def _api_auth_token() -> str:
    """Read fresh on every request rather than caching at import time, so
    tests (and anyone reconfiguring the environment around a long-lived
    process) see ACP_API_TOKEN changes take effect immediately."""
    return (os.environ.get("ACP_API_TOKEN") or "").strip()


def _token_matches(supplied: str, token: str) -> bool:
    """Constant-time comparison of a bearer token that may not be ASCII.

    Comparing the two as str raised TypeError on any non-ASCII token, which
    turned every request into a 500 whether the token was right or wrong.

    WSGI hands a header value over already decoded as latin-1, one character
    per byte, so encoding it back with latin-1 recovers the bytes the client
    actually sent. HTTP clients disagree about how to put a non-ASCII token
    on the wire: curl sends UTF-8, some .NET and Java clients send latin-1.
    Both encodings of the configured token are accepted, since both derive
    from the same secret and refusing one would just look like a broken
    server to whoever picked that client.
    """
    on_the_wire = supplied.encode("latin-1", "replace")
    candidates = {token.encode("utf-8")}
    try:
        # Strict, not "replace": a lossy encoding would let a caller sending
        # a question mark where a character does not fit latin-1 through.
        candidates.add(token.encode("latin-1"))
    except UnicodeEncodeError:
        pass
    return any(hmac.compare_digest(on_the_wire, c) for c in candidates)


if _api_auth_token():
    logging.info("[acp] API auth: ON: /api/* requires a matching ACP_API_TOKEN bearer token")
    if not _api_auth_token().isascii():
        logging.warning(
            "[acp] ACP_API_TOKEN contains non-ASCII characters. It works here, but "
            "HTTP clients disagree about how to encode such a header, so a plugin "
            "or curl on another machine may fail to authenticate. ASCII is safer.")
    if len(_api_auth_token()) < 16:
        logging.warning(
            "[acp] ACP_API_TOKEN is shorter than 16 characters. It is the only thing "
            "standing between your archive and anyone who can reach the port.")
else:
    logging.info("[acp] API auth: OFF: set ACP_API_TOKEN to require a bearer token on /api/*")


SESSION_COOKIE_NAME = "acp_session"
# Thirty days. Long enough that a home user signs in once and forgets about
# it, short enough that a browser left on a machine that changes hands does
# not stay signed in forever.
SESSION_COOKIE_MAX_AGE_S = 30 * 24 * 3600


def _session_cookie_value(token: str) -> str:
    """The cookie that stands in for the token in a browser.

    Derived from the token rather than being the token, so the value in the
    cookie jar is not the thing the user pastes into their NINA plugin. It is
    still credential-equivalent, which is why the cookie is HttpOnly: it does
    not need to be reachable from page JavaScript, and the page never reads
    it.
    """
    return hmac.new(token.encode("utf-8"), b"acp-browser-session",
                    hashlib.sha256).hexdigest()


def _request_is_https() -> bool:
    return request.is_secure or (
        request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https")


def _wants_html() -> bool:
    """A browser navigating, rather than the plugin or curl calling.

    Checked against the Accept header alone. A plugin that asks for JSON, or
    sends no Accept at all, keeps getting 401 JSON rather than a login page
    it cannot read.
    """
    accept = request.headers.get("Accept", "")
    return "text/html" in accept


# Paths that answer before any credential exists. The login form itself, and
# the favicon, so a signed-out tab is not a broken-image icon.
_UNGATED_PATHS = ("/login", "/favicon.ico")


@app.before_request
def _api_gate():
    """Access gate for the whole app, off by default.

    This used to cover /api/* only, on the reasoning that the API is the
    surface a plugin or curl would hit. The effect was that setting
    ACP_API_TOKEN served the page shell and then 401d every fetch it made,
    so the browser UI was dead and nobody would turn the token on. The token
    is the only thing that closes the LAN exposure, so it has to be usable.

    Every path is gated now. A browser navigating in with no credential gets
    a login form, which exchanges the token for a session cookie once. The
    plugin and curl keep sending Authorization: Bearer and never see the
    form.

    OPTIONS used to be answered here with a 204 and no auth check, to serve
    a CORS preflight. The preflight is gone (see the note below where the
    CORS headers used to be), so OPTIONS is now treated like any other
    method and Flask answers it. That removes an unauthenticated path
    through the gate for no loss."""
    token = _api_auth_token()
    if not token:
        return None
    if request.path in _UNGATED_PATHS:
        return None

    auth_header = request.headers.get("Authorization", "")
    supplied = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else ""
    if supplied and _token_matches(supplied, token):
        return None

    cookie = request.cookies.get(SESSION_COOKIE_NAME, "")
    if cookie and hmac.compare_digest(cookie, _session_cookie_value(token)):
        return None

    if _wants_html() and request.method == "GET":
        # Rendered in place rather than redirected to, so the address bar
        # keeps the URL the user asked for and signing in lands them there.
        return render_template("login.html", next_path=request.full_path), 401
    return jsonify({"error": "unauthorized"}), 401


# Bodies below this are left alone: the gzip header and the CPU cost are not
# worth it, and the manifest is the only response where size matters.
_GZIP_MIN_BYTES = int(os.environ.get("ACP_GZIP_MIN_BYTES", 2048))


@app.after_request
def _compress_and_tag(resp):
    """Gzip and ETag JSON and text responses.

    Waitress compresses nothing, and the manifest and visibility JSON shrink
    about tenfold. GET and HEAD responses also get a strong ETag over the
    body, and a matching If-None-Match turns into a 304 before any
    compression happens. The ETag carries a "-gzip" suffix when the body is
    sent compressed, because the two encodings are different
    representations. Streamed responses, files (send_file hands back a
    pass-through response with its own validators), responses already
    encoded or sent as attachments, and anything but a 200 are untouched.
    """
    if resp.status_code != 200 or resp.direct_passthrough or resp.is_streamed:
        return resp
    if resp.headers.get("Content-Encoding") or resp.headers.get("Content-Disposition"):
        return resp
    mimetype = resp.mimetype or ""
    if not (mimetype == "application/json" or
            (mimetype.startswith("text/") and mimetype != "text/event-stream")):
        return resp
    body = resp.get_data()
    if len(body) < _GZIP_MIN_BYTES:
        return resp

    resp.vary.add("Accept-Encoding")
    use_gzip = request.accept_encodings["gzip"] > 0
    if request.method in ("GET", "HEAD") and "ETag" not in resp.headers:
        tag = hashlib.sha1(body).hexdigest()
        resp.set_etag(tag + "-gzip" if use_gzip else tag)
        if mimetype == "application/json" and "Cache-Control" not in resp.headers:
            # Always revalidate: the manifest changes under a running page
            # after a scan, and a 304 makes the check cheap.
            resp.headers["Cache-Control"] = "no-cache"
        resp.make_conditional(request)
        if resp.status_code == 304:
            return resp
    if use_gzip:
        resp.set_data(gzip.compress(body, compresslevel=5, mtime=0))
        resp.headers["Content-Encoding"] = "gzip"
    return resp


def _safe_next_path(candidate: str) -> str:
    """A same-app path to send the browser back to after sign-in, or "/".

    ``candidate`` comes from the login form, so it is attacker-controlled.
    A scheme or a host sends the browser off this app entirely; a backslash
    is rejected too, because browsers treat "/\\evil.com" as protocol-relative
    even though urlsplit does not parse it that way, and a percent-encoded
    backslash or slash (``%5c``, ``%2f``) would smuggle the same trick past a
    check that only looks at the raw form value, so the check runs on the
    decoded string as well. A third leading slash ("///evil.com") is also
    rejected: some browsers collapse it to "//evil.com" and treat it as
    protocol-relative even though urlsplit parses it as a plain path.

    The return value is rebuilt from the validated, parsed pieces rather
    than handed back verbatim, so nothing that reached this function
    unexamined can reach the redirect.
    """
    if not candidate:
        return "/"
    decoded = urllib.parse.unquote(candidate)
    for value in (candidate, decoded):
        if "\\" in value or any(ord(ch) < 0x20 for ch in value) or value.startswith("//"):
            return "/"
    parsed = urllib.parse.urlsplit(decoded)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return "/"
    return urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, parsed.fragment))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Exchange the access token for a session cookie.

    A GET here when no token is configured, or when the caller already has a
    valid cookie, just goes to the app: there is nothing to sign in to.
    """
    token = _api_auth_token()
    if not token:
        return redirect("/")
    if request.method == "GET":
        return render_template("login.html", next_path="/"), 200

    supplied = (request.form.get("token") or "").strip()
    if not _token_matches(supplied, token):
        logging.warning("[acp] failed sign-in attempt from %s", _safe_log(request.remote_addr))
        return render_template("login.html",
                               next_path=_safe_next_path(request.form.get("next") or "/"),
                               error="That token was not accepted."), 401

    target = _safe_next_path(request.form.get("next") or "/")
    resp = redirect(target)
    resp.set_cookie(
        SESSION_COOKIE_NAME, _session_cookie_value(token),
        max_age=SESSION_COOKIE_MAX_AGE_S,
        httponly=True,
        # Strict, not Lax. Lax would send the cookie on a top-level
        # navigation from another site, which is exactly the cross-origin
        # write path commit 55b9841 closed by removing CORS.
        samesite="Strict",
        secure=_request_is_https(),
    )
    return resp


@app.route("/logout", methods=["POST"])
def logout():
    resp = redirect("/login")
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


# There is deliberately no CORS here. An earlier version of this file sent
# `Access-Control-Allow-Origin: *` on API reads and on the preflight, reasoning
# that withholding it from write responses kept writes closed. That is half
# right: it stops an attacker reading the reply, not sending the request. The
# preflight was answering 204 and listing PUT and DELETE as allowed, so any web
# page the user happened to visit could delete their plans or flip one public
# and publish it, silently, with no LAN access needed, because the browser runs
# on the same machine as ACP. Proved end to end on 2026-09-05.
#
# Nothing needs the header. The NINA plugin is a desktop HTTP client and CORS is
# a browser mechanism it never sees; ACP's own page is same-origin. If a
# browser-based client is ever wanted, allow that one origin by name, never "*",
# and never on a preflight for a method that writes.


if not CATALOGS_PATH.exists():
    print(
        f"[acp] WARN: catalogs file not found at {CATALOGS_PATH} -- "
        "right-rail catalog overlays (Green SNR / SMGPS / WISE HII / Messier / etc.) will be empty. "
        "Run scripts/fetch_catalogs.py to populate (network I/O, ~30s)."
    )

_manifest_cache: dict | None = None
_manifest_cache_mtime: float | None = None
_catalogs_cache: dict | None = None
_catalogs_cache_mtime: float | None = None
_gear_cache: dict | None = None
_gear_cache_mtime: float | None = None
_plans_cache: dict | None = None
_plans_cache_mtime: float | None = None
_fingerprints_cache: dict | None = None
_fingerprints_cache_mtime: float | None = None
_target_overrides_cache: dict | None = None
_target_overrides_cache_mtime: float | None = None
_hidden_cache: dict | None = None
_hidden_cache_mtime: float | None = None
_sites_cache: dict | None = None
_sites_cache_mtime: float | None = None
_destinations_cache: dict | None = None
_destinations_cache_mtime: float | None = None
_saved_searches_cache: dict | None = None
_saved_searches_cache_mtime: float | None = None
# Per-(site, manifest, year) cache of computed visibility bins. Visibility is
# essentially constant year-to-year (sun barycentric position is fixed
# relative to RA/Dec) so a one-shot compute per site is fine, and the entry
# is invalidated implicitly when the manifest mtime ticks over.
_visibility_cache: dict[tuple, dict] = {}


def _atomic_write_json(path: Path, data: dict, **dumps_kwargs) -> None:
    """Write JSON to ``path`` atomically (temp file + rename).

    A plain ``write_text`` truncates the target file before writing, so a
    crash or kill mid-write (OOM, power loss, container kill) leaves a
    half-written, unparseable file: the next ``load_*`` call then throws
    and every endpoint touching that store 500s until someone manually
    repairs the file. Writing to a sibling temp file first and renaming
    over the target means the target is only ever replaced by a complete,
    validated write; ``os.replace`` is atomic on both POSIX and Windows.
    """
    text = json.dumps(data, **dumps_kwargs)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _site_cache_key(site: dict) -> tuple:
    """The site-derived portion of a visibility cache key.

    Rounded to swallow float noise from repeated site lookups while
    staying stable across requests for the "same" site. Callers append
    their own endpoint-specific fields (ra/dec/year, manifest mtime, ...).
    """
    return (
        round(site["lat"], 4), round(site["lon"], 4), round(site["elev_m"], 1),
        round(site["min_alt_deg"], 2),
    )

_catalog_registry_cache: dict | None = None
_catalog_registry_cache_mtime: float | None = None

DEFAULT_SITES = {
    "version": 1,
    "sites": [
        {"id": "mauna_kea",  "name": "Mauna Kea, Hawaii",   "lat":  19.82, "lon": -155.47, "elev_m": 4205, "min_alt_deg": 30},
        {"id": "paranal",    "name": "Cerro Paranal, Chile", "lat": -24.63, "lon":  -70.40, "elev_m": 2635, "min_alt_deg": 30},
    ],
}


def load_manifest() -> dict | None:
    """Load the archive manifest from disk. Returns None if the file is
    missing OR malformed (JSON parse error): callers already handle
    None as "no manifest", so an unparseable file degrades to the same
    code path instead of bubbling a JSONDecodeError up the stack and
    crashing every endpoint that touches the manifest with a 500.

    The malformed-file case is logged at WARN level so the maintainer
    can diagnose without a crash report.
    """
    global _manifest_cache, _manifest_cache_mtime
    if not MANIFEST_PATH.exists():
        return None
    mtime = MANIFEST_PATH.stat().st_mtime
    if _manifest_cache is None or _manifest_cache_mtime != mtime:
        try:
            _manifest_cache = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logging.warning(
                "manifest at %s is malformed (%s): treating as missing",
                MANIFEST_PATH, exc,
            )
            _manifest_cache = None
            _manifest_cache_mtime = mtime  # don't re-parse until the file changes
            return None
        _manifest_cache_mtime = mtime
    return _manifest_cache


def load_catalogs() -> dict:
    """Read data/catalogs.json. Returns an empty dict if the file is
    missing OR malformed: same graceful-degradation contract as
    load_manifest. A partially-bad fetch_catalogs.py output should
    NOT take every catalogue-touching endpoint down with it.

    Per-entry validation is the caller's responsibility: this
    function only guarantees the top-level structure is a dict.
    """
    global _catalogs_cache, _catalogs_cache_mtime
    if not CATALOGS_PATH.exists():
        _catalogs_cache = {}
        _catalogs_cache_mtime = None
        return _catalogs_cache
    mtime = CATALOGS_PATH.stat().st_mtime
    if _catalogs_cache is None or _catalogs_cache_mtime != mtime:
        try:
            parsed = json.loads(CATALOGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logging.warning(
                "catalogs file at %s is malformed (%s): treating as empty",
                CATALOGS_PATH, exc,
            )
            _catalogs_cache = {}
            _catalogs_cache_mtime = mtime
            return _catalogs_cache
        # Top-level structure must be a dict mapping catalogue name → list.
        # Anything else is treated as empty (graceful degradation).
        if not isinstance(parsed, dict):
            logging.warning(
                "catalogs file at %s is not a JSON object (got %s): treating as empty",
                CATALOGS_PATH, type(parsed).__name__,
            )
            _catalogs_cache = {}
        else:
            _catalogs_cache = parsed
        _catalogs_cache_mtime = mtime
    return _catalogs_cache


_gear_migration_failed: tuple | None = None


def _read_gear_file() -> dict:
    global _gear_cache, _gear_cache_mtime
    mtime = GEAR_PATH.stat().st_mtime
    if _gear_cache is None or _gear_cache_mtime != mtime:
        _gear_cache = json.loads(GEAR_PATH.read_text(encoding="utf-8"))
        _gear_cache_mtime = mtime
    return _gear_cache


def _backup_gear_json() -> bool:
    """Timestamped copy of gear.json, taken before the template migration
    rewrites it. Returns whether the copy was made. Without one the
    migration does not run: it is the step that is hard to undo."""
    if not GEAR_PATH.exists():
        return True
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = GEAR_PATH.with_name(f"{GEAR_PATH.name}.bak-{stamp}")
    n = 2
    while backup_path.exists():
        backup_path = GEAR_PATH.with_name(f"{GEAR_PATH.name}.bak-{stamp}-{n}")
        n += 1
    try:
        backup_path.write_bytes(GEAR_PATH.read_bytes())
    except OSError as exc:
        logging.warning("could not back up %s before migration: %s", GEAR_PATH, exc)
        return False
    return True


def _maybe_migrate_gear() -> None:
    """One-shot: a version 2 gear.json becomes version 3, step 1 of
    docs/specs/gear-templates.md section 11. The caller holds GEAR_LOCK and
    has just re-read the file. Guarded by `templates_migrated: 1` on the
    document itself, takes a timestamped backup first, and leaves the old
    shape in place (served as it is) if the backup cannot be written."""
    global _gear_migration_failed
    doc = _gear_cache
    if not isinstance(doc, dict) or doc.get("templates_migrated"):
        return
    key = (str(GEAR_PATH), _gear_cache_mtime)
    if _gear_migration_failed == key:
        return
    if not _backup_gear_json():
        logging.error("Not moving gear into templates, because %s could not be backed up first", GEAR_PATH)
        _gear_migration_failed = key
        return
    new = gt.migrate_v2(doc)
    save_gear(new)
    logging.info("Moved gear filters into templates (%d cameras)", len(new.get("cameras") or []))


def _load_gear_stored() -> dict:
    """The gear document as it is stored, migrated on first sight. Callers
    must not change it; copy first. A caller holding PLANS_LOCK (and not
    GEAR_LOCK) gets an unmigrated file as stored, because the migration
    takes GEAR_LOCK and the lock order forbids that from under PLANS_LOCK."""
    if not GEAR_PATH.exists():
        return {"version": 1, "presets": []}
    doc = _read_gear_file()
    if isinstance(doc, dict) and not doc.get("templates_migrated"):
        if PLANS_LOCK._is_owned() and not GEAR_LOCK._is_owned():
            # Code holding PLANS_LOCK never takes GEAR_LOCK (spec section 11).
            # Serve the file as stored; startup and the GEAR_LOCK-first
            # paths do the migration.
            return doc
        with GEAR_LOCK:
            # Another thread may have migrated while this one waited.
            doc = _read_gear_file()
            if isinstance(doc, dict) and not doc.get("templates_migrated"):
                _maybe_migrate_gear()
                doc = _read_gear_file()
    return doc


def _peek_plans() -> list:
    """The plans list for a read that must not run the plans migrations
    (load_gear is called from inside them)."""
    try:
        mtime = PLANS_PATH.stat().st_mtime
    except OSError:
        return []
    if _plans_cache is not None and _plans_cache_mtime == mtime:
        plans = _plans_cache.get("plans")
    else:
        try:
            parsed = json.loads(PLANS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        plans = parsed.get("plans") if isinstance(parsed, dict) else None
    return plans if isinstance(plans, list) else []


def load_gear() -> dict:
    """The gear document with its derived keys: the old per-filter scalars,
    each template's `status` and `used_by`, each camera's `wheel` block. It
    is a copy, so a caller may change it. `save_gear` strips every derived
    key again. A document that has not been migrated comes back as stored."""
    stored = _load_gear_stored()
    if not gt.is_migrated(stored):
        return stored
    try:
        profiles = load_fingerprints().get("profiles") or {}
    except (OSError, ValueError):
        profiles = {}
    return gt.derive(stored, fingerprints=profiles, ts_lists=_load_ts_templates_store(),
                     plans=_peek_plans(), norm=_norm_gear_name)


def _heal_nonfinite_floats(value, path_parts: list[str], healed: list[str]):
    """Recursively replace non-finite floats (NaN/Infinity) with None.

    ``healed`` collects a dotted-path string for every value replaced, so
    the caller can log one clear warning per plan naming exactly which
    field was poisoned. Returns the (possibly replaced) value; dicts and
    lists are rebuilt, everything else is passed through unchanged.
    """
    if isinstance(value, float) and not math.isfinite(value):
        healed.append(".".join(path_parts) or "<root>")
        return None
    if isinstance(value, dict):
        return {k: _heal_nonfinite_floats(v, path_parts + [str(k)], healed) for k, v in value.items()}
    if isinstance(value, list):
        return [_heal_nonfinite_floats(v, path_parts + [f"[{i}]"], healed) for i, v in enumerate(value)]
    return value


def _heal_plan_list(plans: list) -> list:
    """Heal legacy plans.json entries written before NaN/Infinity were
    rejected on the way in (_validate_plan_payload). Every write path
    (POST/PUT/DELETE/sync) re-serialises the FULL plan list with
    allow_nan=False, so a single poisoned plan would 500 every subsequent
    write, and GET would hand the browser a NaN literal its own
    JSON.parse can't handle either. Replace non-finite floats with None
    and log which plan/field so the breakage is discoverable, rather than
    crashing the next time anything touches plans.json. The next save
    (for any reason) then persists the healed, JSON-strict version.
    """
    healed_plans = []
    for p in plans:
        if not isinstance(p, dict):
            healed_plans.append(p)
            continue
        healed_fields: list[str] = []
        healed_p = _heal_nonfinite_floats(p, [], healed_fields)
        if healed_fields:
            plan_id = repr(p.get("id", "<unknown>"))
            fields = ", ".join(map(repr, healed_fields))
            logging.warning(
                "plan %s: replaced non-finite value(s) with null in field(s): %s",
                plan_id, fields,
            )
        healed_plans.append(healed_p)
    return healed_plans


# The four TS project states ACP now owns, per docs/specs/ts-project-settings.md
# section 1. Absent on a plan means "active", the same as TS.
PLAN_STATES = ("draft", "active", "inactive", "closed")


def _backup_plans_json() -> bool:
    """Timestamped copy of plans.json, taken before a one-shot migration
    touches it. Returns whether the copy was made (a missing plans.json has
    nothing to lose and counts as made). The draft->active migration goes
    ahead without one, because refusing would leave the 47 plans stuck under
    the old draft meaning; the filter row migration does not."""
    if not PLANS_PATH.exists():
        return True
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = PLANS_PATH.with_name(f"{PLANS_PATH.name}.bak-{stamp}")
    n = 2
    while backup_path.exists():
        backup_path = PLANS_PATH.with_name(f"{PLANS_PATH.name}.bak-{stamp}-{n}")
        n += 1
    try:
        backup_path.write_bytes(PLANS_PATH.read_bytes())
    except OSError as exc:
        logging.warning("could not back up %s before migration: %s", PLANS_PATH, exc)
        return False
    return True


_row_rewrite_backed_up: set = set()


def _backup_before_row_rewrite() -> bool:
    """plans.json backup ahead of the first gear-template row rewrite
    (template_id, template_unresolved, renames, sub carries) in this process,
    so spec section 11 Rollback can restore plans.json beside gear.json. Call
    it only when rows will actually change. One backup per process and path,
    holding the rows as they were before any rewrite. Returns False when the
    copy could not be made."""
    key = str(PLANS_PATH)
    if key in _row_rewrite_backed_up:
        return True
    if not _backup_plans_json():
        return False
    _row_rewrite_backed_up.add(key)
    return True


def _maybe_migrate_draft_plans_to_active() -> None:
    """One-shot: every plan left as `state: "draft"` under the old meaning
    becomes `"active"`, the first time ACP loads plans.json after this
    version starts. See docs/specs/ts-project-settings.md section 1 for
    why: the NINA plugin never honoured draft, so 46 of the user's 47 draft
    plans were already live in TS. Leaving them "draft" under the new
    meaning (TS keeps the project but never schedules it) would pause
    them on the next push. The 9 plans TS itself has inactive are not
    touched here; they come back into ACP as inactive on the next upload,
    per the push rule in section 5 (a push never writes a setting ACP
    hasn't changed since the last sync).

    Guarded by a `settings_migrated: 1` key on the plans.json document
    itself, so it runs once ever and a second load changes nothing. Takes
    a timestamped backup first, because the migration is the one step in
    this change that's hard to undo by hand.
    """
    global _plans_cache
    if not isinstance(_plans_cache, dict) or _plans_cache.get("settings_migrated"):
        return
    plans = _plans_cache.get("plans")
    if not isinstance(plans, list):
        plans = []
    _backup_plans_json()
    moved = 0
    for p in plans:
        if isinstance(p, dict) and p.get("state") == "draft":
            p["state"] = "active"
            moved += 1
    _plans_cache["plans"] = plans
    _plans_cache["settings_migrated"] = 1
    logging.info(
        "Moved %d plans from draft to active, because the NINA plugin was already syncing them",
        moved,
    )
    save_plans({**_plans_cache, "version": _plans_cache.get("version", 1), "plans": plans})


def _maybe_migrate_filter_rows() -> None:
    """One-shot: every plan's `filter_goals` becomes `filter_rows`.

    docs/specs/goal-panel-and-ts-order.md section 3. Each goal key becomes a
    row with a new id, ordered L, R, G, B, Ha, OIII, SII, and `ts_links`
    exposure plan keys move from "<target id>_<goal key>" to
    "<target id>_<row id>" so the next push finds the plans it wrote.
    Guarded by `filter_rows_migrated: 1` on the plans.json document, so it
    runs once ever, and takes a timestamped backup first. It ships with the
    importer change in the sync, because a push between the two would write
    a second exposure plan for every star row.
    """
    global _plans_cache
    if not isinstance(_plans_cache, dict) or _plans_cache.get("filter_rows_migrated"):
        return
    plans = _plans_cache.get("plans")
    if not isinstance(plans, list):
        plans = []
    todo = [p for p in plans if isinstance(p, dict) and not isinstance(p.get("filter_rows"), list)]
    cameras = {}
    if any(isinstance(p.get("filter_goals"), dict) and p["filter_goals"] for p in todo):
        if not _backup_plans_json():
            # No backup, no migration: it is the step that is hard to undo.
            # Plans keep their filter_goals, which GET still serves, and the
            # next load tries again.
            logging.error(
                "Not moving filter goals into filter rows, because %s could not be backed up first",
                PLANS_PATH,
            )
            return
        cameras = {c.get("id"): c for c in (load_gear().get("cameras") or []) if isinstance(c, dict)}
    moved = 0
    for p in todo:
        id_by_key = fr.migrate_plan(p, cameras.get(p.get("camera_id") or ""))
        fr.rekey_links(p, id_by_key)
        moved += len(id_by_key)
    _plans_cache["plans"] = plans
    _plans_cache["filter_rows_migrated"] = 1
    logging.info("Moved %d filter goals into filter rows", moved)
    save_plans({**_plans_cache, "version": _plans_cache.get("version", 1), "plans": plans})


def _repair_row_templates() -> bool:
    """Step 3 of the gear migration: give every plan row its `template_id`
    (docs/specs/gear-templates.md sections 3 and 11). Runs when plans.json
    loads and again whenever gear learns new templates. A row nothing matches
    is flagged `template_unresolved` and logged by plan id. Returns whether
    any row changed, in which case plans.json is written."""
    if not isinstance(_plans_cache, dict):
        return False
    stored = _load_gear_stored()
    if not gt.is_migrated(stored):
        return False
    cameras = {c.get("id"): c for c in stored.get("cameras") or [] if isinstance(c, dict)}
    # Work on a copy: the cache and plans.json stay as they are on disk until
    # the backup and the write have both succeeded.
    plans = copy.deepcopy(_plans_cache.get("plans"))
    changed = False
    for p in plans if isinstance(plans, list) else []:
        if isinstance(p, dict) and fr.repair_rows(p.get("filter_rows"), cameras.get(p.get("camera_id") or ""),
                                                  p.get("id"), True):
            changed = True
    if changed:
        # The file on disk still holds the old rows, so the backup taken here
        # is the pre-rewrite copy.
        if not _backup_before_row_rewrite():
            logging.error("Not writing template ids into plan rows, because %s could not be backed up first", PLANS_PATH)
            return False
        save_plans({**_plans_cache, "version": _plans_cache.get("version", 1), "plans": plans})
    return changed


def repair_plan_templates() -> bool:
    """Run the row repair on the plans now, for a caller that has just given
    gear new templates (adoption in the sync). Takes PLANS_LOCK."""
    with PLANS_LOCK:
        load_plans()
        return _repair_row_templates()


def load_plans() -> dict:
    global _plans_cache, _plans_cache_mtime
    if not PLANS_PATH.exists():
        return {"version": 1, "plans": []}
    mtime = PLANS_PATH.stat().st_mtime
    if _plans_cache is None or _plans_cache_mtime != mtime:
        _plans_cache = json.loads(PLANS_PATH.read_text(encoding="utf-8"))
        _plans_cache_mtime = mtime
        plans = _plans_cache.get("plans")
        if isinstance(plans, list):
            _plans_cache["plans"] = _heal_plan_list(plans)
        _maybe_migrate_draft_plans_to_active()
        _maybe_migrate_filter_rows()
        _repair_row_templates()
    # One-shot backfill: the first time someone declares destinations,
    # every existing plan without a destination_id picks up the first
    # destination's id and gets persisted. Flag stored in destinations.json
    # so the migration never re-runs after the user opts a plan out
    # explicitly. Skipped silently when destinations.json is absent.
    _maybe_backfill_plan_destinations()
    return _plans_cache


def _maybe_backfill_plan_destinations() -> None:
    global _plans_cache
    if _plans_cache is None:
        return
    dests_doc = load_destinations()
    if not isinstance(dests_doc, dict):
        return
    if dests_doc.get("backfilled_at"):
        return
    dests = dests_doc.get("destinations") or []
    if not dests:
        return
    default_id = dests[0].get("id")
    if not default_id:
        return
    plans = _plans_cache.get("plans") if isinstance(_plans_cache, dict) else None
    if not isinstance(plans, list):
        return
    changed = False
    for p in plans:
        if not isinstance(p, dict):
            continue
        if not p.get("destination_id"):
            p["destination_id"] = default_id
            changed = True
    # Mark the migration done either way: once destinations exist, future
    # plans must get an explicit destination_id from the UI; we don't want
    # to silently re-assign every plan on every load.
    dests_doc["backfilled_at"] = datetime.now(timezone.utc).isoformat()
    save_destinations(dests_doc)
    if changed:
        save_plans({**_plans_cache, "version": _plans_cache.get("version", 1), "plans": plans})


def save_plans(data: dict) -> None:
    global _plans_cache, _plans_cache_mtime
    PLANS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False is a belt-and-braces tripwire: _validate_plan_payload
    # already rejects NaN/Infinity on the way in, but if some other code
    # path ever calls save_plans() with unvalidated data, this raises
    # ValueError instead of silently writing JSON that strict clients (and
    # our own int(math.ceil(...)) in the sync builder) can't handle.
    _atomic_write_json(PLANS_PATH, data, indent=2, allow_nan=False)
    _plans_cache = data
    _plans_cache_mtime = PLANS_PATH.stat().st_mtime


def load_fingerprints() -> dict:
    """Last gear fingerprint reported by each NINA profile.

    Same mtime-keyed cache as the other JSON stores. Absent file means no
    NINA install has ever called /api/plans/match, which is the normal
    state for a stock checkout, so an empty document is returned rather
    than an error.
    """
    global _fingerprints_cache, _fingerprints_cache_mtime
    if not FINGERPRINTS_PATH.exists():
        return {"version": 1, "profiles": {}}
    mtime = FINGERPRINTS_PATH.stat().st_mtime
    if _fingerprints_cache is None or _fingerprints_cache_mtime != mtime:
        parsed = json.loads(FINGERPRINTS_PATH.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            logging.warning(
                "fingerprints file at %s is not a JSON object (got %s), treating as empty",
                FINGERPRINTS_PATH, type(parsed).__name__)
            parsed = {"version": 1, "profiles": {}}
        parsed.setdefault("version", 1)
        if not isinstance(parsed.get("profiles"), dict):
            parsed["profiles"] = {}
        _fingerprints_cache = parsed
        _fingerprints_cache_mtime = mtime
    return _fingerprints_cache


def save_fingerprints(data: dict) -> None:
    global _fingerprints_cache, _fingerprints_cache_mtime
    FINGERPRINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(FINGERPRINTS_PATH, data, indent=2, allow_nan=False)
    _fingerprints_cache = data
    _fingerprints_cache_mtime = FINGERPRINTS_PATH.stat().st_mtime


def load_target_overrides() -> dict:
    global _target_overrides_cache, _target_overrides_cache_mtime
    if not TARGET_OVERRIDES_PATH.exists():
        return {"version": 1, "overrides": {}}
    mtime = TARGET_OVERRIDES_PATH.stat().st_mtime
    if _target_overrides_cache is None or _target_overrides_cache_mtime != mtime:
        _target_overrides_cache = json.loads(TARGET_OVERRIDES_PATH.read_text(encoding="utf-8"))
        _target_overrides_cache_mtime = mtime
    return _target_overrides_cache


def save_target_overrides(data: dict) -> None:
    global _target_overrides_cache, _target_overrides_cache_mtime
    TARGET_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(TARGET_OVERRIDES_PATH, data, indent=2)
    _target_overrides_cache = data
    _target_overrides_cache_mtime = TARGET_OVERRIDES_PATH.stat().st_mtime


HIDDEN_KINDS = ("plans", "projects", "targets")
_HIDDEN_KEY_MAX = 200


def load_hidden() -> dict:
    """Hidden marks as {"version": 1, "plans": {...}, "projects": {...},
    "targets": {...}}, each keyed by plan id, project name or target_id
    (as a string). A missing or unreadable file means nothing is hidden."""
    global _hidden_cache, _hidden_cache_mtime
    empty = {"version": 1, **{k: {} for k in HIDDEN_KINDS}}
    if not HIDDEN_PATH.exists():
        return empty
    mtime = HIDDEN_PATH.stat().st_mtime
    if _hidden_cache is None or _hidden_cache_mtime != mtime:
        try:
            raw = json.loads(HIDDEN_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logging.warning("hidden marks at %s are unreadable (%s): showing everything",
                            HIDDEN_PATH, exc)
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        data = {"version": raw.get("version", 1)}
        for k in HIDDEN_KINDS:
            v = raw.get(k)
            data[k] = v if isinstance(v, dict) else {}
        _hidden_cache = data
        _hidden_cache_mtime = mtime
    return _hidden_cache


def save_hidden(data: dict) -> None:
    global _hidden_cache, _hidden_cache_mtime
    HIDDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(HIDDEN_PATH, data, indent=2)
    _hidden_cache = data
    _hidden_cache_mtime = HIDDEN_PATH.stat().st_mtime


def hidden_target_ids() -> set[str]:
    """Target ids (as strings) the user has hidden from coverage."""
    return set(load_hidden().get("targets", {}))


def load_sites() -> dict:
    global _sites_cache, _sites_cache_mtime
    if not SITES_PATH.exists():
        return json.loads(json.dumps(DEFAULT_SITES))
    mtime = SITES_PATH.stat().st_mtime
    if _sites_cache is None or _sites_cache_mtime != mtime:
        _sites_cache = json.loads(SITES_PATH.read_text(encoding="utf-8"))
        _sites_cache_mtime = mtime
    return _sites_cache


def save_sites(data: dict) -> None:
    global _sites_cache, _sites_cache_mtime
    SITES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(SITES_PATH, data, indent=2)
    _sites_cache = data
    _sites_cache_mtime = SITES_PATH.stat().st_mtime


def load_destinations() -> dict:
    """Read data/destinations.json. Returns the empty default ({version: 1,
    destinations: []}) when the file is absent: keeps single-rig users on
    the legacy nina_ts_sync single-DB flow with no destination concept
    exposed in the UI.

    Schema per destination:
      id              str  (stable key)
      label           str  (user-visible)
      kind            "local_db" | "shared_file"
      ts_db_path      str  (for kind=local_db): direct write target
      export_path     str  (for kind=shared_file): file written by sync
      acquired_path   str  (for kind=shared_file, optional): file the NUC
                            daemon writes back; ACP polls it for live progress
      notes           str  (optional, free-form)
    """
    global _destinations_cache, _destinations_cache_mtime
    if not DESTINATIONS_PATH.exists():
        return {"version": 1, "destinations": []}
    mtime = DESTINATIONS_PATH.stat().st_mtime
    if _destinations_cache is None or _destinations_cache_mtime != mtime:
        _destinations_cache = json.loads(DESTINATIONS_PATH.read_text(encoding="utf-8"))
        _destinations_cache_mtime = mtime
    return _destinations_cache


def save_destinations(data: dict) -> None:
    global _destinations_cache, _destinations_cache_mtime
    DESTINATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(DESTINATIONS_PATH, data, indent=2)
    _destinations_cache = data
    _destinations_cache_mtime = DESTINATIONS_PATH.stat().st_mtime


def load_saved_searches() -> dict:
    global _saved_searches_cache, _saved_searches_cache_mtime
    if not SAVED_SEARCHES_PATH.exists():
        return {"version": 1, "searches": []}
    mtime = SAVED_SEARCHES_PATH.stat().st_mtime
    if _saved_searches_cache is None or _saved_searches_cache_mtime != mtime:
        _saved_searches_cache = json.loads(
            SAVED_SEARCHES_PATH.read_text(encoding="utf-8"))
        _saved_searches_cache_mtime = mtime
    return _saved_searches_cache


def save_saved_searches(data: dict) -> None:
    global _saved_searches_cache, _saved_searches_cache_mtime
    SAVED_SEARCHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(SAVED_SEARCHES_PATH, data, indent=2)
    _saved_searches_cache = data
    _saved_searches_cache_mtime = SAVED_SEARCHES_PATH.stat().st_mtime


def load_catalog_registry() -> dict:
    """Load the declarative catalogue registry.

    Returns the file's contents (cached on mtime) or an empty registry if
    the file is absent. Out-of-tree code injecting catalogues at runtime
    (see `app.extra_catalogues`) is merged on top by api_catalog_registry.
    """
    global _catalog_registry_cache, _catalog_registry_cache_mtime
    if not CATALOG_REGISTRY_PATH.exists():
        return {"version": 1, "catalogues": []}
    mtime = CATALOG_REGISTRY_PATH.stat().st_mtime
    if _catalog_registry_cache is None or _catalog_registry_cache_mtime != mtime:
        _catalog_registry_cache = json.loads(
            CATALOG_REGISTRY_PATH.read_text(encoding="utf-8"))
        _catalog_registry_cache_mtime = mtime
    return _catalog_registry_cache


class JsonManifestSource:
    """Coverage source backed by a JSON manifest file on local disk.

    Drives both the user's own archive (path = MANIFEST_PATH) and any
    sanitised friend manifests configured via ACP_FRIEND_MANIFESTS. The
    manifest shape is identical for both: the only difference is the
    surfaced metadata (label, kind, attribution).
    """

    def __init__(self, *, source_id: str, label: str, color: str,
                 attribution: str, enabled_default: bool, path: Path | str,
                 kind: str = "manifest", hidden_ids=None) -> None:
        self._source_id = source_id
        # Callable returning target ids (strings) to leave out of coverage.
        # Only the user's own archive passes one; friend manifests don't.
        self._hidden_ids = hidden_ids
        self._label = label
        self._color = color
        self._attribution = attribution
        self._enabled_default = enabled_default
        self._path = Path(path)
        self._kind = kind
        self._cache: dict | None = None
        self._cache_mtime: float | None = None
        # Phase 4: per-filter MOC cache keyed by (filter_name, manifest mtime).
        # Tuple-keyed so an mtime change auto-invalidates without bookkeeping.
        self._moc_cache: dict[tuple[str, float], object] = {}

    def id(self) -> str:
        return self._source_id

    def metadata(self) -> dict:
        return {
            "label": self._label,
            "color": self._color,
            "kind": self._kind,
            "attribution": self._attribution,
            "enabled_default": self._enabled_default,
        }

    def _load(self) -> dict | None:
        if not self._path.exists():
            return None
        mtime = self._path.stat().st_mtime
        if self._cache is None or self._cache_mtime != mtime:
            self._cache = json.loads(self._path.read_text(encoding="utf-8"))
            self._cache_mtime = mtime
        return self._cache

    def coverage(self):
        manifest = self._load()
        if not manifest:
            return
        for t in manifest.get("targets", []) or []:
            corners = t.get("corners_icrs") or []
            vertices = [(float(ra), float(dec)) for ra, dec in corners]
            filters = {
                fname: {
                    "hours": float(d.get("total_hours", 0.0)),
                    "files": int(d.get("files", 0)),
                }
                for fname, d in (t.get("filters") or {}).items()
            }
            yield {
                "kind": "polygon",
                "vertices": vertices,
                "filters": filters,
                "name": ", ".join(t.get("objects") or []),
                "metadata": {
                    "target_id": t.get("target_id"),
                    "telescopes": list(t.get("telescopes") or []),
                },
            }

    def coverage_moc(self, filter_name: str):
        """Union of every target polygon that has >0 hours at `filter_name`.

        Cached per (filter, manifest-mtime). Returns None if mocpy is missing,
        the manifest is absent, or no target carries this filter.
        """
        if not _MOCPY_AVAILABLE:
            return None
        manifest = self._load()
        if not manifest:
            return None
        hidden = frozenset(self._hidden_ids()) if self._hidden_ids else frozenset()
        cache_key = (filter_name, self._cache_mtime or 0.0, hidden)
        if cache_key in self._moc_cache:
            return self._moc_cache[cache_key]

        from astropy.coordinates import SkyCoord  # local: keep app boot cheap
        import astropy.units as u

        per_target: list = []
        for t in manifest.get("targets", []) or []:
            if hidden and str(t.get("target_id")) in hidden:
                continue
            f = (t.get("filters") or {}).get(filter_name)
            if not f or float(f.get("total_hours", 0.0)) <= 0.0:
                continue
            corners = t.get("corners_icrs") or []
            if len(corners) < 3:
                continue
            ras = [float(ra) for ra, _ in corners]
            decs = [float(dec) for _, dec in corners]
            sc = SkyCoord(ras, decs, unit=u.deg, frame="icrs")
            per_target.append(MOC.from_polygon_skycoord(sc, max_depth=10))

        if not per_target:
            self._moc_cache[cache_key] = None
            return None
        # union(another, *rest): single arg is fine for a 2-element list,
        # variadic for >2. Slice into head + tail to satisfy both.
        merged = per_target[0] if len(per_target) == 1 \
            else per_target[0].union(*per_target[1:])
        self._moc_cache[cache_key] = merged
        return merged


def ManifestCoverageSource() -> JsonManifestSource:
    """Built-in source backed by the user's own archive manifest."""
    return JsonManifestSource(
        source_id="manifest",
        label="Your archive",
        color="",
        attribution="Local archive scan",
        enabled_default=True,
        path=MANIFEST_PATH,
        kind="manifest",
        hidden_ids=hidden_target_ids,
    )


def FriendManifestSource(*, source_id: str, label: str, color: str,
                         path: Path | str) -> JsonManifestSource:
    """Sanitised manifest shared by another imager."""
    return JsonManifestSource(
        source_id=source_id,
        label=label,
        color=color,
        attribution=f"Shared by {label}",
        enabled_default=True,
        path=path,
        kind="friend",
    )


def _validate_moc_url(moc_url: str) -> None:
    """HTTPS-only, hostname-allowlisted. Raises ValueError on rejection.

    Run at config-load time (registration) and again before every fetch: the
    on-fetch check defends against an attacker who can swap in a registered
    source's URL between boot and first hit.
    """
    if not isinstance(moc_url, str) or not moc_url:
        raise ValueError("moc_url is empty")
    parsed = urllib.parse.urlparse(moc_url)
    if parsed.scheme != "https":
        raise ValueError(f"moc_url must use https:// (got {parsed.scheme!r})")
    if parsed.hostname not in _MOC_ALLOWED_HOSTS:
        raise ValueError(
            f"moc_url host {parsed.hostname!r} not in allowlist "
            f"({sorted(_MOC_ALLOWED_HOSTS)})"
        )


class _MocFetchError(Exception):
    """Upstream fetch / validation failed in a way the route surfaces as 502."""


def _fetch_moc_bytes(moc_url: str) -> bytes:
    """Download a MOC over HTTPS with a streamed size cap.

    Refuses Content-Length headers above the cap up front, and aborts mid-read
    if a server with no/wrong Content-Length tries to feed us more than the
    cap. The bytes returned are the raw FITS payload: the caller is
    responsible for handing them to MOC.from_fits for validation.
    """
    _validate_moc_url(moc_url)
    req = urllib.request.Request(moc_url, headers={"User-Agent": "ACP-MOC/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_MOC_FETCH_TIMEOUT_S) as resp:
            cl = resp.headers.get("Content-Length")
            if cl is not None:
                try:
                    if int(cl) > _MOC_MAX_BYTES:
                        raise _MocFetchError(
                            f"Content-Length {cl} exceeds cap {_MOC_MAX_BYTES}"
                        )
                except ValueError:
                    pass  # malformed header → fall through to streaming check
            buf = BytesIO()
            # 64 KiB chunks: small enough to abort early on a runaway body,
            # large enough that a 200 KB MOC takes ~3 reads.
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if buf.tell() + len(chunk) > _MOC_MAX_BYTES:
                    raise _MocFetchError(
                        f"response body exceeded cap {_MOC_MAX_BYTES}"
                    )
                buf.write(chunk)
            return buf.getvalue()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise _MocFetchError(f"fetch failed: {exc}") from exc


class MocCoverageSource:
    """Coverage source backed by a CDS-hosted FITS MOC blob.

    Lazy fetch on first hit to /api/moc/<id>; cached on disk under
    data/moc_cache/<id>.fits with a 30-day TTL sidecar. The Phase 3 frontend
    consumes the FITS bytes directly via Aladin Lite and never needs the
    coverage() iterator: see comment in coverage() for Phase 4 plans.
    """

    def __init__(self, *, source_id: str, label: str, color: str,
                 attribution: str, enabled_default: bool, moc_url: str,
                 filter_name: str | None = None,
                 cache_dir: Path | None = None) -> None:
        # Re-validate on construct so a malformed URL slipped past the loader
        # still fails loudly, not at first /api/moc hit.
        _validate_moc_url(moc_url)
        self._source_id = source_id
        self._label = label
        self._color = color
        self._attribution = attribution
        self._enabled_default = enabled_default
        self._moc_url = moc_url
        self._filter_name = filter_name
        self._cache_dir = Path(cache_dir) if cache_dir else MOC_CACHE_DIR
        self._lock = threading.Lock()
        self._parsed_moc = None  # lazy parse of the cached FITS for coverage_moc

    def id(self) -> str:
        return self._source_id

    def metadata(self) -> dict:
        return {
            "label": self._label,
            "color": self._color,
            "kind": "moc",
            "attribution": self._attribution,
            "enabled_default": self._enabled_default,
        }

    def coverage(self):
        # Phase 4's gap-finder will need real region yielding from MOC
        # union/intersection. For Phase 3 we leave this empty: the frontend
        # consumes /api/moc/<id> directly, not coverage().
        return iter(())

    def coverage_moc(self, filter_name: str):
        """Return the cached MOC if it exists on disk and `filter_name` matches.

        Lazy: never triggers a network fetch. Caller (gap-finder) decides
        whether to pre-warm the cache via ensure_cached() in advance.
        """
        if not _MOCPY_AVAILABLE:
            return None
        if filter_name != self._filter_name:
            return None
        if self._parsed_moc is not None:
            return self._parsed_moc
        fits_path, _ = self._cache_paths()
        if not fits_path.exists():
            return None
        self._parsed_moc = MOC.from_fits(str(fits_path))
        return self._parsed_moc

    @property
    def moc_url(self) -> str:
        return self._moc_url

    def _cache_paths(self) -> tuple[Path, Path]:
        return (
            self._cache_dir / f"{self._source_id}.fits",
            self._cache_dir / f"{self._source_id}.meta.json",
        )

    def _cache_fresh(self, fits_path: Path, meta_path: Path) -> bool:
        if not fits_path.exists() or not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        # URL drift in surveys.json must invalidate the cached blob.
        if meta.get("url") != self._moc_url:
            return False
        try:
            fetched_at = datetime.fromisoformat(meta["fetched_at"])
        except (KeyError, ValueError):
            return False
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        return age < _MOC_CACHE_TTL_S

    def ensure_cached(self) -> Path:
        """Return the path to a fresh local FITS MOC, fetching if needed.

        Per-source lock prevents two concurrent first-hit requests from racing
        the same network fetch. Raises _MocFetchError on upstream / validation
        failure; the route translates that to 502.
        """
        if not _MOCPY_AVAILABLE:
            # Caller (the route) checks this first; re-checking here keeps the
            # invariant local: no half-fetched bytes hit disk without mocpy.
            raise _MocFetchError("mocpy not available")
        fits_path, meta_path = self._cache_paths()
        with self._lock:
            if self._cache_fresh(fits_path, meta_path):
                return fits_path
            data = _fetch_moc_bytes(self._moc_url)
            try:
                MOC.from_fits(BytesIO(data))
            except Exception as exc:
                # Don't write malformed bytes to disk: next hit refetches.
                raise _MocFetchError(f"invalid MOC FITS: {exc}") from exc
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            fits_path.write_bytes(data)
            meta_path.write_text(json.dumps({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_sha256": hashlib.sha256(data).hexdigest(),
                "url": self._moc_url,
            }, indent=2), encoding="utf-8")
            return fits_path


# Caps applied during friend-manifest validation. Loose enough that any
# legitimate hobby archive will fit, tight enough that a malformed (or
# malicious) file can't blow up parse cost or memory.
_FRIEND_MAX_TARGETS = 10000
_FRIEND_MAX_VERTICES = 64
_FRIEND_MAX_POLYGONS = 64


def _validate_friend_manifest(data: object, source_label: str) -> None:
    """Reject any sanitised manifest that fails the safety checks.

    Raises ValueError on the first failure. The caller logs and skips , 
    a bad friend manifest must not block the app from starting.
    """
    if not isinstance(data, dict):
        raise ValueError("top-level value is not an object")
    # Tripwire: refuse anything not explicitly produced by the sanitiser.
    # If a user accidentally points ACP_FRIEND_MANIFESTS at their own raw
    # manifest, their filesystem paths would surface in the public UI.
    if not data.get("sanitised"):
        raise ValueError("missing 'sanitised: true' flag: refusing to load "
                         "(probably an unsanitised manifest)")
    targets = data.get("targets")
    if not isinstance(targets, list):
        raise ValueError("'targets' is not a list")
    if len(targets) > _FRIEND_MAX_TARGETS:
        raise ValueError(
            f"target count {len(targets)} exceeds cap {_FRIEND_MAX_TARGETS}"
        )
    for i, t in enumerate(targets):
        if not isinstance(t, dict):
            raise ValueError(f"targets[{i}] is not an object")
        corners = t.get("corners_icrs") or []
        if not isinstance(corners, list):
            raise ValueError(f"targets[{i}].corners_icrs is not a list")
        # corners_icrs in the sanitiser is a single polygon (list of [ra,dec]
        # pairs). Treat the whole list as one polygon for vertex counting,
        # but defend against a future shape with multiple polygons too.
        if corners and isinstance(corners[0], (list, tuple)) and corners[0] \
                and isinstance(corners[0][0], (list, tuple)):
            polygons = corners
        else:
            polygons = [corners]
        if len(polygons) > _FRIEND_MAX_POLYGONS:
            raise ValueError(
                f"targets[{i}] has {len(polygons)} polygons "
                f"(cap {_FRIEND_MAX_POLYGONS})"
            )
        for j, poly in enumerate(polygons):
            if not isinstance(poly, list):
                raise ValueError(
                    f"targets[{i}].corners_icrs[{j}] is not a list"
                )
            if len(poly) > _FRIEND_MAX_VERTICES:
                raise ValueError(
                    f"targets[{i}] polygon {j} has {len(poly)} vertices "
                    f"(cap {_FRIEND_MAX_VERTICES})"
                )
    # Final safety net: scan the entire object for path-shaped strings.
    # Imported lazily so app startup doesn't pay the cost when the env var
    # is unset.
    _scripts_dir = str(REPO_ROOT / "scripts")
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from sanitise_manifest import validate_no_paths  # noqa: WPS433
    validate_no_paths(data)


# Coverage-source registry. Extensions can append their own sources to this
# list inside their `register(app)` body; the built-in manifest source is
# only pre-registered when MANIFEST_PATH actually exists on disk so a user
# running ACP off pure extension-supplied sources (e.g. a private
# survey-tile extension with no FITS archive of their own) doesn't see an
# empty "Your archive" entry in the Sources rail.
app.coverage_sources = []
if MANIFEST_PATH.exists():
    app.coverage_sources.append(ManifestCoverageSource())
# Out-of-tree extensions can append catalogue registry entries here at
# `register(app)` time. Same shape as `data/catalog_registry.json` entries.
# Defaults to an empty list: extensions add to it, never replace it.
app.extra_catalogues: list[dict] = []
# PrioritisedTilesSource registry. Empty by default; extensions append via
# `app.tile_sources.append(...)` in their register() callback. Each source
# implements the Protocol declared in sources.py.
app.tile_sources: list = []
# CategorisedCatalogSource registry. Same registration pattern as above.
app.catalog_sources: list = []
# Extension UI manifest registry. Extensions append entries describing the
# buttons / toggles they want surfaced in core's UI; the frontend fetches the
# combined list via /api/extensions/manifest and renders an "Extensions" rail
# panel. Entries may declare ``replaces: "<core-button-id>"`` to swap a core
# button in place (handler/label come from the extension). See docs/extensions.md.
app.extensions_manifest: list[dict] = []


# Friend manifests: semicolon-separated paths in ACP_FRIEND_MANIFESTS. Each
# is validated (sanitised flag + caps + path-leak scan) before joining the
# registry; failures log a warning and skip without crashing the app.
_friend_paths = os.environ.get("ACP_FRIEND_MANIFESTS", "").strip()
if _friend_paths:
    for raw_path in _friend_paths.split(";"):
        raw_path = raw_path.strip()
        if not raw_path:
            continue
        p = Path(raw_path).expanduser()
        if not p.is_file():
            logging.warning(
                "ACP_FRIEND_MANIFESTS: path not found, skipping: %s", p
            )
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _validate_friend_manifest(data, source_label=p.stem)
        except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            logging.warning(
                "ACP_FRIEND_MANIFESTS: skipping %s: %s", p, exc
            )
            continue
        label = (data.get("friend_label") or p.stem) or "Friend"
        # Path-traversal-safe: stem already strips dirs; sanitise to alnum/_/-
        # so the id is URL-safe even for adversarial filenames. Skip the
        # `friend_` prefix if the filename already starts with one (case- and
        # whitespace-insensitive) to avoid `friend_friend_dave` ids.
        stem_lower = p.stem.lower()
        stem_canonical = stem_lower.replace(" ", "_")
        raw_id = stem_lower if stem_canonical.startswith("friend_") else f"friend_{stem_lower}"
        source_id = "".join(
            c if c.isalnum() or c in "_-" else "_" for c in raw_id
        )
        app.coverage_sources.append(FriendManifestSource(
            source_id=source_id, label=label, color="", path=p,
        ))
        logging.info("Loaded friend manifest: %s (%s)", label, p)


# Survey MOC sources: declarative registry committed at data/surveys.json (or
# ACP_SURVEYS_PATH). The file is intentionally tracked: PRs welcome to add
# more surveys. Per-entry validation failures log a warning and skip; bad
# entries must not block app boot.
def _load_surveys_registry() -> list[dict]:
    if not SURVEYS_PATH.exists():
        logging.info("Surveys registry not found at %s: no MOC sources", SURVEYS_PATH)
        return []
    try:
        data = json.loads(SURVEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Surveys registry %s unreadable: %s", SURVEYS_PATH, exc)
        return []
    if not isinstance(data, list):
        logging.warning("Surveys registry %s: top-level value must be a list", SURVEYS_PATH)
        return []
    return data


for _entry in _load_surveys_registry():
    _eid = (_entry or {}).get("id", "<unknown>")
    try:
        if not isinstance(_entry, dict):
            raise ValueError("entry is not an object")
        for _required in ("id", "label", "moc_url", "attribution"):
            if not _entry.get(_required):
                raise ValueError(f"missing required field {_required!r}")
        app.coverage_sources.append(MocCoverageSource(
            source_id=_entry["id"],
            label=_entry["label"],
            color=_entry.get("color", ""),
            attribution=_entry["attribution"],
            enabled_default=bool(_entry.get("enabled_default", False)),
            moc_url=_entry["moc_url"],
            filter_name=_entry.get("filter"),
        ))
        logging.info("Loaded MOC source: %s (%s)", _entry["label"], _entry["id"])
    except (ValueError, TypeError) as exc:
        logging.warning("Skipping MOC source %s: %s", _eid, exc)


# Optional extensions: load any user-supplied modules from the directory
# named by ACP_EXTENSIONS_DIR. No-op when the env var is unset or the loader
# is absent, so a stock checkout runs unchanged. Loaded after the built-in
# source registers so extensions can read or extend `app.coverage_sources`.
try:
    from extensions import load_bundled_extensions, load_extensions
    load_extensions(app)
    # The extensions that ship inside ACP (the Target Scheduler sync) load
    # after the user's, which win on a name clash. ACP_DISABLE_BUNDLED_EXTENSIONS=1
    # switches them off.
    load_bundled_extensions(app)
except ImportError:
    pass


def _fov_arcmin(telescope: dict | None, camera: dict | None) -> list[float]:
    """Derive FOV from a telescope + camera pair. arcsec/px = 206.265 × pixel_um / fl_mm."""
    if not telescope or not camera:
        return [0.0, 0.0]
    try:
        fl = float(telescope["focal_length_mm"])
        px_um = float(camera["pixel_size_um"])
        w_px, h_px = camera["sensor_px"]
    except (KeyError, ValueError, TypeError):
        return [0.0, 0.0]
    if fl <= 0:
        return [0.0, 0.0]
    arcsec_per_px = 206.265 * px_um / fl
    return [round(w_px * arcsec_per_px / 60.0, 2), round(h_px * arcsec_per_px / 60.0, 2)]


def _with_sensor_scalars(camera: dict) -> dict:
    """Add scalar sensor_width_px / sensor_height_px alongside sensor_px.

    ACP's own frontend reads the [w, h] pair, but the NINA companion plugin
    expects flat scalar fields and ignores sensor_px entirely: without
    this every camera showed up on the NINA side with a zero sensor size.
    sensor_px stays the source of truth; the scalars are derived here
    rather than stored twice, so there's no way for them to drift apart.
    """
    out = dict(camera)
    sensor = camera.get("sensor_px")
    if isinstance(sensor, (list, tuple)) and len(sensor) == 2:
        try:
            out["sensor_width_px"] = int(sensor[0])
            out["sensor_height_px"] = int(sensor[1])
        except (TypeError, ValueError):
            pass
    return out


def save_gear(data: dict) -> None:
    """Write gear.json with every derived key stripped, so none is stored."""
    global _gear_cache, _gear_cache_mtime
    data = gt.strip_derived(data)
    GEAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(GEAR_PATH, data, indent=2)
    _gear_cache = data
    _gear_cache_mtime = GEAR_PATH.stat().st_mtime


def commit_gear(doc: dict) -> int:
    """Write a changed stored document with `rev` one higher and return the
    new rev. The caller holds GEAR_LOCK."""
    before_gear = copy.deepcopy(_load_gear_stored()) if activity.enabled() else None
    doc["rev"] = int(doc.get("rev") or 0) + 1
    save_gear(doc)
    if before_gear is not None:
        after_gear = copy.deepcopy(doc)
        night = activity.current_night()
        _activity_later(lambda: activity.record_changes(
            etype="gear_changed", key=f"gear_changed:{night}", night=night,
            changes=activity.diff_gear(before_gear, after_gear), prefix="Gear: "))
    return doc["rev"]


def _gear_for_write() -> dict:
    """A private copy of the stored document to change and commit. A store
    with no file yet starts as an empty version 3 document."""
    stored = _load_gear_stored()
    if gt.is_migrated(stored):
        return copy.deepcopy(stored)
    if not GEAR_PATH.exists():
        return gt.empty_doc()
    return copy.deepcopy(stored)


# Normalize a telescope/camera name for fuzzy matching. Mirrors _normTelName
# in static/app.js so client and server agree on what counts as "the same rig".
_NORM_STRIP = re.compile(r"\b(apo|pro|mk[\s-]*[ivx]+|edge[\s-]*hd|hd|f\/?\d+(\.\d+)?|mm|inch|in|\"|zwo|qhy|celestron|svbony|sky[-\s]*watcher|williams?[\s-]*optics?|askar|takahashi)\b")
_NORM_WS = re.compile(r"[^a-z0-9]+")

def _norm_gear_name(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = _NORM_STRIP.sub(" ", s)
    s = _NORM_WS.sub(" ", s)
    return s.strip()


def _slug_id(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip()).strip("-").lower()
    return s or "item"


def _looks_truncated(name: str) -> bool:
    if not name:
        return True
    return name.count("(") != name.count(")") or name.count("[") != name.count("]")


def _extract_gear_from_manifest() -> dict:
    """Walk the manifest and collect telescope + camera metadata observed in the
    data. Pulls whatever the manifest exposes today (telescope names, filters per
    telescope, pix_arcsec, fov_arcmin) and also looks for richer FITS-derived
    fields (camera, focal_length_mm, pixel_size_um, aperture_mm) if the upstream
    manifest builder has started surfacing them: safe if absent."""
    manifest = load_manifest()
    if not manifest:
        return {"telescopes": [], "cameras": []}
    tels: dict[str, dict] = {}
    cams: dict[str, dict] = {}
    for target in manifest.get("targets", []) or []:
        for name in (target.get("telescopes") or []):
            if name and not _looks_truncated(name):
                tels.setdefault(name, {"filters": set(), "pix_arcsecs": [], "fovs": [],
                                       "focal_mm": [], "aperture_mm": [], "cameras_seen": set()})
        # Targets built from light frames alone have no per_master_fov entry,
        # but the scanner still lists their INSTRUME on the target (issue #63).
        colour_cams = set(target.get("colour_cameras") or [])
        for cam_name in (target.get("cameras") or []):
            if cam_name and not _looks_truncated(cam_name):
                c = cams.setdefault(cam_name, {"filters": set(), "pixel_um": [], "sensor_px": [], "colour": False})
                if cam_name in colour_cams:
                    c["colour"] = True
                # Seed the real filters the user shot with, not the bands they
                # credit: an OSC camera gets one OSC entry rather than R, G, B,
                # and a dual band filter keeps its own name.
                for f, d in (target.get("filters") or {}).items():
                    if (d or {}).get("total_hours", 0) <= 0:
                        continue
                    sources = (d or {}).get("sources")
                    if isinstance(sources, dict) and sources:
                        c["filters"].update(k for k, v in sources.items() if (v or 0) > 0)
                    else:
                        c["filters"].add(f)
        for m in (target.get("per_master_fov") or []):
            name = m.get("telescope")
            if not name or _looks_truncated(name):
                continue
            t = tels.setdefault(name, {"filters": set(), "pix_arcsecs": [], "fovs": [],
                                       "focal_mm": [], "aperture_mm": [], "cameras_seen": set()})
            if m.get("filter"): t["filters"].add(m["filter"])
            if m.get("pix_arcsec"):
                try: t["pix_arcsecs"].append(float(m["pix_arcsec"]))
                except (TypeError, ValueError): pass
            fov = m.get("fov_arcmin")
            if isinstance(fov, (list, tuple)) and len(fov) == 2:
                try: t["fovs"].append([float(fov[0]), float(fov[1])])
                except (TypeError, ValueError): pass
            for k, bucket in (("focal_length_mm", "focal_mm"), ("aperture_mm", "aperture_mm")):
                v = m.get(k)
                if v is not None:
                    try: t[bucket].append(float(v))
                    except (TypeError, ValueError): pass
            cam_name = m.get("camera") or m.get("instrument")
            if cam_name and not _looks_truncated(cam_name):
                t["cameras_seen"].add(cam_name)
                c = cams.setdefault(cam_name, {"filters": set(), "pixel_um": [], "sensor_px": [], "colour": False})
                if m.get("colour"): c["colour"] = True
                if m.get("filter"): c["filters"].add(m["filter"])
                if m.get("pixel_size_um"):
                    try: c["pixel_um"].append(float(m["pixel_size_um"]))
                    except (TypeError, ValueError): pass
                sensor = m.get("sensor_px")
                if isinstance(sensor, (list, tuple)) and len(sensor) == 2:
                    try: c["sensor_px"].append([int(sensor[0]), int(sensor[1])])
                    except (TypeError, ValueError): pass

    def _median(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None

    telescopes = []
    for name, info in tels.items():
        telescopes.append({
            "name": name,
            "filters": sorted(info["filters"]),
            "focal_length_mm": _median(info["focal_mm"]) or 0,
            "aperture_mm": _median(info["aperture_mm"]) or 0,
            "observed_pix_arcsec": round(_median(info["pix_arcsecs"]), 3) if info["pix_arcsecs"] else None,
            "observed_fov_arcmin": _median([f[0] for f in info["fovs"]]) if info["fovs"] else None,
            "cameras_seen": sorted(info["cameras_seen"]),
        })
    cameras = []
    for name, info in cams.items():
        cameras.append({
            "name": name,
            "filters": sorted(info["filters"]),
            "pixel_size_um": _median(info["pixel_um"]) or 0,
            "sensor_px": info["sensor_px"][-1] if info["sensor_px"] else None,
            "colour": bool(info.get("colour")),
        })
    return {"telescopes": telescopes, "cameras": cameras}


def _seed_gear_from_manifest() -> dict:
    """Idempotent: merge unseen manifest telescopes + cameras into gear.json.
    Uses normalized-name fuzzy matching so existing gear isn't duplicated."""
    extracted = _extract_gear_from_manifest()
    with GEAR_LOCK:
        return _seed_gear_locked(extracted)


def _seed_gear_locked(extracted: dict) -> dict:
    gear = copy.deepcopy(_load_gear_stored())
    migrated = gt.is_migrated(gear)
    gear.setdefault("version", 2)
    telescopes = gear.setdefault("telescopes", [])
    cameras = gear.setdefault("cameras", [])

    existing_tel_norms = {_norm_gear_name(t.get("name", "")) for t in telescopes} - {""}
    existing_tel_ids = {t.get("id") for t in telescopes}
    existing_cam_norms = {_norm_gear_name(c.get("name", "")) for c in cameras} - {""}
    existing_cam_ids = {c.get("id") for c in cameras}

    # Default filter config for a camera seeded from observed filter names.
    def _default_filter_cfg(f):
        # Broadband and colour sensor entries get a short default sub; every
        # named narrowband, dual band or multi band filter gets a long one.
        is_narrow = f not in ("L", "R", "G", "B", "V", "OSC", "NoFilter", "L-Pro", "L-Quad", "CLS", "UHC")
        return {
            "ts_template_id": None, "ts_template_name": None,
            "default_sub_s": 300 if is_narrow else 120,
            "gain": -1, "offset": -1, "bin": 1,
        }

    added_tels = []
    for info in extracted["telescopes"]:
        if _norm_gear_name(info["name"]) in existing_tel_norms:
            continue
        tel_id = _slug_id(info["name"])
        base = tel_id; n = 2
        while tel_id in existing_tel_ids:
            tel_id = f"{base}-{n}"; n += 1
        new_tel = {
            "id": tel_id,
            "name": info["name"],
            "focal_length_mm": info["focal_length_mm"] or 0,
            "aperture_mm": info["aperture_mm"] or 0,
        }
        if info["observed_pix_arcsec"] is not None:
            new_tel["observed_pix_arcsec"] = info["observed_pix_arcsec"]
        if info["observed_fov_arcmin"] is not None:
            new_tel["observed_fov_arcmin"] = round(info["observed_fov_arcmin"], 2)
        telescopes.append(new_tel)
        added_tels.append(info["name"])
        existing_tel_norms.add(_norm_gear_name(info["name"]))
        existing_tel_ids.add(tel_id)

    added_cams = []
    for info in extracted["cameras"]:
        if _norm_gear_name(info["name"]) in existing_cam_norms:
            continue
        cam_id = _slug_id(info["name"])
        base = cam_id; n = 2
        while cam_id in existing_cam_ids:
            cam_id = f"{base}-{n}"; n += 1
        cameras.append(_with_sensor_scalars({
            "id": cam_id,
            "name": info["name"],
            "pixel_size_um": info["pixel_size_um"] or 0,
            "sensor_px": info["sensor_px"] or [0, 0],
            "colour": bool(info.get("colour")),
            "filters": {f: _default_filter_cfg(f) for f in info["filters"]},
        }))
        added_cams.append(info["name"])
        existing_cam_norms.add(_norm_gear_name(info["name"]))
        existing_cam_ids.add(cam_id)

    if migrated:
        # Only the cameras this run added are in the old shape.
        for cam in cameras:
            if any(not (isinstance(v, dict) and isinstance(v.get("templates"), list))
                   for v in (cam.get("filters") or {}).values()):
                gt.camera_to_v3(cam)
    if added_tels or added_cams:
        if migrated:
            commit_gear(gear)
        else:
            save_gear(gear)
    return {
        "ok": True,
        "added_telescopes": added_tels,
        "added_cameras": added_cams,
        "scanned_telescopes": [t["name"] for t in extracted["telescopes"]],
        "scanned_cameras": [c["name"] for c in extracted["cameras"]],
    }


@app.route("/api/gear/seed", methods=["POST"])
def api_gear_seed():
    return jsonify(_seed_gear_from_manifest())


def _iso_mtime(path: Path) -> str | None:
    """UTC ISO 8601 mtime of `path`, or None when it doesn't exist."""
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


@app.route("/api/version")
def api_version():
    """Lets the NINA plugin poll cheaply for plan/manifest changes instead of
    refetching /api/plans on a timer regardless of whether anything moved."""
    return jsonify({
        "version": VERSION,
        "plans_last_modified": _iso_mtime(PLANS_PATH),
        "manifest_last_modified": _iso_mtime(MANIFEST_PATH),
    })


@app.route("/")
def index():
    # Render the page even when no manifest exists yet: a fresh-install
    # user lands here, the frontend fetches /api/manifest, sees an empty
    # targets list, and shows the onboarding banner directing them at the
    # archive setup guide. Erroring out here would just give them a
    # white-screen 500 with no path forward.
    if _wants_html() and request.args.get("setup") != "skip" and _first_run():
        return redirect("/setup")
    m = load_manifest() or {}
    return render_template(
        "index.html",
        total_targets=m.get("total_targets", 0),
        total_hours=m.get("total_integration_hours", 0),
        scan_date=(m.get("scan_date") or "")[:10],
        activity_enabled=activity.enabled(),
        version=VERSION,
    )


_ABS_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/])")


def _shorten_archive_path(s: str, roots: list[str]) -> str:
    """One absolute path, cut down so it still names the file but no longer
    names the machine.

    A path under a scan root becomes root-relative. A path under no known
    root keeps its last two segments. Either way the username, the mount
    point and the folder layout above the archive stop crossing the wire.
    """
    folded = s.replace("\\", "/")
    low = folded.lower()
    for root in roots:
        r = str(root).replace("\\", "/").rstrip("/")
        if not r:
            continue
        rl = r.lower()
        if low == rl:
            return r.rsplit("/", 1)[-1] or folded
        if low.startswith(rl + "/"):
            return folded[len(r) + 1:]
    parts = [seg for seg in folded.split("/") if seg]
    return "/".join(parts[-2:]) if parts else folded


def _without_archive_paths(obj, roots: list[str]):
    """Recursively shorten every absolute path in a manifest payload,
    walking dict keys as well as values.

    Deliberately structural rather than a list of key names. `api_manifest`
    used to strip the one key called `paths`, which read as if it prevented
    this, while `master_files`, `per_master_fov[].path` and three fields
    inside every `folder_sub_buckets` entry carried the real layout through.
    Walking the whole payload means a key added later is covered too.

    Keys are walked as well as values: a rig key can itself be path-shaped,
    from a capture app that wrote a profile path into a camera field, and a
    key is just as much a wire leak as a value.
    """
    if isinstance(obj, str):
        return _shorten_archive_path(obj, roots) if _ABS_PATH_RE.match(obj) else obj
    if isinstance(obj, dict):
        return {
            (_shorten_archive_path(k, roots) if isinstance(k, str) and _ABS_PATH_RE.match(k) else k):
            _without_archive_paths(v, roots)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_without_archive_paths(v, roots) for v in obj]
    return obj


# Fields the page never reads from /api/manifest. They are most of the bytes:
# folder_sub_buckets alone is over half. The target detail panel fetches
# master_files from /api/target/<id> when it opens. app.py and
# scripts/sanitise_manifest.py read per_master_fov and corners_galactic from
# the manifest on disk, not from this response.
_MANIFEST_TARGET_OMIT = ("folder_sub_buckets", "per_master_fov", "corners_galactic", "master_files")
_MANIFEST_FILTER_OMIT = ("paths", "folder_sub_buckets")

# (manifest object, serialised body). Keyed on the loaded manifest object, which
# load_manifest replaces whenever the file's mtime changes, so a rescan
# invalidates it and every other request reuses the bytes. Kept as bytes so a
# conditional-GET or gzip hook can use them without rebuilding.
_manifest_body_cache: tuple[dict, bytes] | None = None
_manifest_body_lock = threading.Lock()


def _build_manifest_payload(m: dict) -> dict:
    roots = [str(r) for r in (m.get("scan_roots") or []) if r]
    slim_targets = []
    for t in m["targets"]:
        ft = {f: {k: v for k, v in d.items() if k not in _MANIFEST_FILTER_OMIT}
              for f, d in t["filters"].items()}
        slim = {k: v for k, v in t.items() if k not in _MANIFEST_TARGET_OMIT}
        slim["filters"] = ft
        slim_targets.append(slim)
    return _without_archive_paths({
        "scan_date": m.get("scan_date"),
        "total_targets": m.get("total_targets"),
        "total_integration_hours": m.get("total_integration_hours"),
        "total_captured_hours": m.get("total_captured_hours", 0),
        "total_integrated_hours": m.get("total_integrated_hours", 0),
        "targets": slim_targets,
        "scan_health": _scan_health(m),
    }, roots)


def manifest_body_bytes(m: dict) -> bytes:
    """The serialised /api/manifest body for a loaded manifest, built once per
    load. Same bytes jsonify would send, trailing newline included."""
    global _manifest_body_cache
    cached = _manifest_body_cache
    if cached is not None and cached[0] is m:
        return cached[1]
    with _manifest_body_lock:
        cached = _manifest_body_cache
        if cached is not None and cached[0] is m:
            return cached[1]
        body = app.json.response(_build_manifest_payload(m)).get_data()
        _manifest_body_cache = (m, body)
        return body


@app.route("/api/manifest")
def api_manifest():
    m = load_manifest()
    if m is None:
        # Empty manifest rather than 404: the frontend's onboarding
        # banner triggers on targets.length === 0, so a clean empty
        # response is exactly what we want here.
        return jsonify({
            "scan_date": None,
            "total_targets": 0,
            "total_integration_hours": 0,
            "total_captured_hours": 0,
            "total_integrated_hours": 0,
            "targets": [],
        })
    return app.response_class(manifest_body_bytes(m), mimetype="application/json")


def _scan_health(m: dict) -> dict | None:
    """Slim view of the scanner's integrity flags for the rail. None for a
    manifest written before the flags existed."""
    flags = m.get("integrity_flags")
    if not isinstance(flags, dict):
        return None

    def _count(key):
        v = flags.get(key)
        return len(v) if isinstance(v, list) else 0

    def _num(key):
        try:
            return float(flags.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _int(key):
        """A whole count, guarded the way _num already guards a fraction.

        The manifest is a file on disk. It can predate a key, or be hand edited,
        and a count that reads "many" used to take the whole panel down with a
        ValueError rather than showing the rest of the scan's health.
        """
        try:
            return int(flags.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    def _examples(key, limit=10):
        """First few offending paths, so the rail can name the file.

        Stephen could see the count but had no way to find which master it
        meant without opening the summary markdown by hand (issue #63).
        """
        v = flags.get(key)
        if not isinstance(v, list):
            return []
        return [str(x)[:400] for x in v[:limit] if isinstance(x, str)]

    unrec = []
    for row in flags.get("unrecognised_filter_names") or []:
        if isinstance(row, dict) and row.get("name"):
            unrec.append({"name": str(row["name"])[:64], "frames": int(row.get("frames") or 0)})

    def _rig_rows(key, limit=10):
        """Slim stale-master / integration-anomaly rows for the rail: which
        target, band and rig, and by how much."""
        v = flags.get(key)
        if not isinstance(v, list):
            return []
        out = []
        for row in v[:limit]:
            if not isinstance(row, dict):
                continue
            out.append({
                "target_id": row.get("target_id"),
                "band": row.get("band"),
                "rig": row.get("rig"),
                "excess_hours": row.get("excess_hours"),
            })
        return out

    def _error_groups(key, limit=5):
        """Rows shaped {error, files, examples}.

        Paths are left whole here. api_manifest passes the entire payload
        through _without_archive_paths, so shortening them again would only
        cut the same path twice.
        """
        v = flags.get(key)
        if not isinstance(v, list):
            return []
        out = []
        for row in v[:limit]:
            if not isinstance(row, dict):
                continue
            try:
                n_files = int(row.get("files") or 0)
            except (TypeError, ValueError):
                # The manifest is a file on disk and may predate this key or
                # have been hand-edited. A junk count must not take the panel
                # down with it, same as _num above.
                n_files = 0
            examples = row.get("examples")
            out.append({
                "error": str(row.get("error") or "")[:200],
                "files": n_files,
                "examples": ([str(x)[:400] for x in examples[:5] if x]
                             if isinstance(examples, list) else []),
            })
        return out

    def _mangled_names(limit=5):
        """Rows shaped {name, example}: a name the server invented, and where.

        Its own shape rather than _error_groups, because a renamed file is not an
        error and the useful part is the invented name itself. A share that does
        this hands every client an 8.3 name in place of one Windows cannot hold,
        so folder names in the manifest are not the ones on disk.
        """
        v = flags.get("mangled_names")
        if not isinstance(v, list):
            return []
        out = []
        for row in v[:limit]:
            if not isinstance(row, dict):
                continue
            out.append({
                "name": str(row.get("name") or "")[:80],
                "example": str(row.get("example") or "")[:400],
            })
        return out

    def _skipped_links(limit=5):
        """Paths of directory links the walk refused to follow.

        Plain strings rather than error rows, because nothing went wrong: not
        following a link is the correct behaviour and what it protects against
        is a link pointing out of the archive. It is still worth saying, since
        a user who moved a capture folder off C: with a junction would
        otherwise see fewer hours and no reason for it.
        """
        v = flags.get("skipped_links")
        if not isinstance(v, list):
            return []
        return [str(x)[:400] for x in v[:limit] if x]

    return {
        "sii_ha_suspects": _count("sii_ha_correlation_suspects"),
        "unreadable_files": _int("unreadable_file_count"),
        "unreadable_files_examples": _error_groups("unreadable_files"),
        "unreadable_dirs": _int("unreadable_dir_count"),
        "unreadable_dirs_examples": _error_groups("unreadable_dirs"),
        "stat_failures": _int("stat_failure_count"),
        "stat_failures_examples": _error_groups("stat_failures"),
        "skipped_links": _int("skipped_link_count"),
        "skipped_links_examples": _skipped_links(),
        "mangled_names": _int("mangled_name_count"),
        "mangled_names_examples": _mangled_names(),
        "masters_missing_wcs": _count("masters_missing_wcs"),
        "masters_ambiguous_filter": _count("masters_ambiguous_filter"),
        "masters_missing_wcs_examples": _examples("masters_missing_wcs"),
        "masters_ambiguous_filter_examples": _examples("masters_ambiguous_filter"),
        "dedup_hours_dropped": round(_num("session_dedup_hours_dropped") + _num("content_dedup_hours_dropped"), 2),
        "unrecognised_filters": unrec,
        "stale_masters": _count("stale_masters"),
        "stale_masters_examples": _rig_rows("stale_masters"),
        "integration_anomalies": _count("integration_anomalies"),
    }


@app.route("/api/target/<int:target_id>")
def api_target(target_id: int):
    m = load_manifest()
    if m is None:
        return jsonify({"error": "manifest not found"}), 404
    roots = [str(r) for r in (m.get("scan_roots") or []) if r]
    for t in m["targets"]:
        if t["target_id"] == target_id:
            return jsonify(_without_archive_paths(t, roots))
    return jsonify({"error": "not found"}), 404


@app.route("/api/catalogs")
def api_catalogs():
    """Marker data for every catalogue surfaced in the rail.

    Two contributors merged at request time:
      - file-loaded entries from `data/catalogs.json` (populated by
        scripts/fetch_catalogs.py); keyed by the registry's `data_key`,
      - `app.catalog_sources` (extension-registered Protocols); keyed by
        the source's `id()`. Each object is a dict with at least
        `name`, `ra_deg`, `dec_deg`, plus optional `category` + extras.
    """
    out = dict(load_catalogs() or {})
    for src in (getattr(app, "catalog_sources", []) or []):
        try:
            src_id = src.id()
        except Exception as exc:
            logging.warning("catalog source missing id(): %s", exc)
            continue
        try:
            objs = list(src.objects() or [])
        except Exception as exc:
            logging.warning("catalog source %r objects() raised: %s", src_id, exc)
            continue
        # Out-of-tree sources win on key collisions: same precedence as the
        # registry merge: so an extension can deliberately override a
        # file-loaded catalogue with a richer per-class build.
        out[src_id] = objs
    return jsonify(out)


def _entry_from_catalog_source(src) -> dict | None:
    """Auto-generate a registry entry for a CategorisedCatalogSource."""
    try:
        sid = src.id()
        meta = src.metadata() or {}
    except Exception as exc:
        logging.warning("catalog source missing id/metadata: %s", exc)
        return None
    if not isinstance(sid, str) or not sid:
        return None
    cats: list = []
    if hasattr(src, "categories"):
        try:
            cats = list(src.categories() or [])
        except Exception as exc:
            logging.warning("catalog source %r categories() raised: %s", sid, exc)
    return {
        "id": sid,
        "data_key": sid,
        "label": meta.get("label") or sid,
        "color": meta.get("color") or "#888",
        "marker": meta.get("marker") or "circle",
        "size": int(meta.get("size") or 10),
        "attribution": meta.get("attribution") or "",
        "enabled_default": bool(meta.get("enabled_default")),
        "categories": cats,
        # Cross-catalogue Object-filter fields (all optional).
        # `default_visible_categories`: categories ticked on first load
        # in the Objects panel; absent ⇒ all visible.
        # `known_tags`: tag values to render chips for up-front,
        # before any objects load.
        # `default_filter`: initial search-box expression.
        "default_visible_categories": list(meta.get("default_visible_categories") or []),
        "known_tags": list(meta.get("known_tags") or []),
        "default_filter": str(meta.get("default_filter") or ""),
    }


# --- Tile sources (Plan 4) -----------------------------------------------
# A "tile source" is anything implementing sources.PrioritisedTilesSource , 
# a curated, ranked list of sky cells with per-band coverage. In-tree code
# does not ship one; out-of-tree extensions register via
# `app.tile_sources.append(...)`. The Inventory rail surfaces whichever
# are present at request time.

def _tile_completion(tile: dict) -> float:
    """Fraction of declared bands marked covered. 0.0 if no per-band data."""
    pb = tile.get("per_band") or {}
    if not pb:
        return 0.0
    n = len(pb)
    n_covered = sum(1 for v in pb.values()
                    if isinstance(v, dict) and v.get("covered"))
    return (n_covered / n) if n else 0.0


def _summarise_tile_source(src) -> dict:
    """Probe a tile source for the metadata the Inventory rail needs.

    Walks the tiles once to derive `n_tiles`, `max_priority_level`, and
    union sets for `categories` + `bands`. Sources with very large tile
    lists may want to override these via dedicated methods later, but the
    walk is fast (a few thousand tiles ≈ 10 ms).
    """
    error: str | None = None
    try:
        sid = src.id()
    except Exception as exc:
        logging.warning("tile source id() raised: %s", exc)
        sid = type(src).__name__
        error = f"id() raised: {_short_error(exc)}"
    try:
        meta = src.metadata() or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception as exc:
        logging.warning("tile source %r metadata() raised: %s", sid, exc)
        meta = {}
        error = error or f"metadata() raised: {_short_error(exc)}"
    loading = meta.get("status") == "loading" and error is None
    n = 0
    max_pri = 0
    cats: set[str] = set()
    bands: set[str] = set()
    try:
        for tile in ([] if (loading or error) else (src.tiles() or [])):
            n += 1
            if isinstance(tile.get("priority_level"), int) and tile["priority_level"] > max_pri:
                max_pri = tile["priority_level"]
            for k in (tile.get("category_counts") or {}).keys():
                if isinstance(k, str):
                    cats.add(k)
            for k in (tile.get("per_band") or {}).keys():
                if isinstance(k, str):
                    bands.add(k)
    except Exception as exc:
        logging.warning("tile source %r tiles() raised during summary: %s", sid, exc)
        error = f"tiles() raised: {_short_error(exc)}"
    status = "error" if error else "loading" if loading else "empty" if n == 0 else "ready"
    return {
        "id": sid,
        "status": status,
        "error": error,
        "extension": _source_extension(src, meta),
        "label": meta.get("label") or sid,
        "color": meta.get("color") or "",
        "attribution": meta.get("attribution") or "",
        "enabled_default": bool(meta.get("enabled_default")),
        "n_tiles": n,
        "max_priority_level": max_pri,
        "categories": sorted(cats),
        "bands": sorted(bands),
        "facets": _coerce_facets(meta.get("facets")),
        "color_facet": meta.get("color_facet") or "",
        "fill_opacity": _coerce_fill_opacity(meta.get("fill_opacity")),
        "tile_panel": meta.get("tile_panel") if meta.get("tile_panel") in ("replace", "append") else None,
    }


def _short_error(exc: Exception) -> str:
    """One line of an exception for the UI: ``Type: message``, capped."""
    text = f"{type(exc).__name__}: {exc}".replace(chr(10), " ").strip()
    return text if len(text) <= 160 else text[:157] + "..."


def _source_extension(src, meta: dict) -> str:
    """Id of the extension a tile source came from, or "".

    A source can name it with ``extension`` in its ``metadata()``. Otherwise
    it is taken from the module the source class was loaded from, since
    ``load_extensions`` names those ``acp_ext_<name>``.
    """
    named = meta.get("extension")
    if isinstance(named, str) and named.strip():
        return named.strip()
    mod = getattr(type(src), "__module__", "") or ""
    if mod.startswith("acp_ext_"):
        return mod[len("acp_ext_"):].split(".", 1)[0]
    return ""


def _coerce_fill_opacity(raw) -> float | None:
    """A source's optional ``fill_opacity`` as a float in 0..1, else None.

    None means "not set": the frontend keeps its default footprint drawing.
    Booleans, NaN and out-of-range numbers count as not set, so a typo can't
    paint a source solid or invisible.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    v = float(raw)
    return v if 0.0 <= v <= 1.0 else None


def _coerce_facets(raw) -> list[dict]:
    """Pass-through validator for extension-declared facets.

    Drops malformed entries silently so a typo in one facet doesn't break
    the whole rail. Each kept facet has id+label+field+values, where
    each value has value+label+color.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        fid = f.get("id"); lbl = f.get("label"); field = f.get("field")
        vals = f.get("values")
        if not (isinstance(fid, str) and isinstance(lbl, str)
                and isinstance(field, str) and isinstance(vals, list)):
            continue
        kept_vals = []
        for v in vals:
            if not isinstance(v, dict):
                continue
            if "value" not in v or not isinstance(v.get("label"), str):
                continue
            kept_vals.append({
                "value": v["value"],
                "label": v["label"],
                "color": v.get("color") or "",
            })
        if kept_vals:
            out.append({"id": fid, "label": lbl, "field": field, "values": kept_vals})
    return out


@app.route("/api/tile-sources")
def api_tile_sources():
    """List metadata for every registered PrioritisedTilesSource.

    Frontend renders the Inventory rail only when this returns at least
    one source. Each entry includes summary stats (n_tiles, max priority,
    available categories + bands) so the rail can build filter chips
    without a second request.
    """
    out = []
    for src in (getattr(app, "tile_sources", []) or []):
        try:
            summary = _summarise_tile_source(src)
        except Exception as exc:
            logging.exception("tile source summary failed")
            summary = {"id": type(src).__name__, "label": type(src).__name__,
                       "status": "error", "error": _short_error(exc),
                       "extension": "", "n_tiles": 0, "max_priority_level": 0,
                       "categories": [], "bands": [], "facets": [],
                       "color": "", "color_facet": "", "attribution": "",
                       "enabled_default": False, "fill_opacity": None,
                       "tile_panel": None}
        if summary:
            out.append(summary)
    return jsonify({"sources": out})


@app.route("/api/tiles/<source_id>")
def api_tiles(source_id: str):
    """Return all tiles for one source.

    No server-side filtering for v1: payloads are small enough (~100KB
    for a few thousand tiles) that the frontend can filter live as the
    user toggles chips. Add `?priority=1,2&missing=Ha,SII` later if a
    payload grows past comfort.
    """
    src = next(
        (s for s in (getattr(app, "tile_sources", []) or [])
         if hasattr(s, "id") and s.id() == source_id),
        None,
    )
    if src is None:
        return jsonify({"error": f"unknown tile source {source_id!r}"}), 404
    tiles_out: list[dict] = []
    try:
        for tile in src.tiles() or []:
            if not isinstance(tile, dict):
                continue
            tiles_out.append(tile)
    except Exception:
        logging.exception("tile source %r tiles() raised", _safe_log(source_id))
        return jsonify({"error": "source raised"}), 500
    return jsonify({"id": source_id, "tiles": tiles_out})


# --- Saved Inventory searches (Plan 6) -----------------------------------
# Each entry: {id, name, source_id, filters: {priorities, missing,
# categories, hidePlanned}, created_at}. Per-source so the user can have
# different named searches against different tile sources.

def _validate_saved_search(s: dict) -> str | None:
    if not isinstance(s, dict):
        return "search must be an object"
    if not isinstance(s.get("name"), str) or not s["name"].strip():
        return "name must be a non-empty string"
    if not isinstance(s.get("source_id"), str) or not s["source_id"].strip():
        return "source_id must be a non-empty string"
    f = s.get("filters") or {}
    if not isinstance(f, dict):
        return "filters must be an object"
    for k in ("priorities", "missing", "categories"):
        if k in f and not isinstance(f[k], list):
            return f"filters.{k} must be a list"
    if "hidePlanned" in f and not isinstance(f["hidePlanned"], bool):
        return "filters.hidePlanned must be a boolean"
    return None


@app.route("/api/saved-searches", methods=["GET", "POST"])
def api_saved_searches():
    if request.method == "GET":
        return jsonify(load_saved_searches())
    payload = request.get_json(silent=True) or {}
    err = _validate_saved_search(payload)
    if err:
        return jsonify({"error": err}), 400
    data = load_saved_searches()
    searches = list(data.get("searches", []))
    sid = payload.get("id") or str(uuid.uuid4())
    f = payload.get("filters") or {}
    entry = {
        "id": sid,
        "name": payload["name"].strip(),
        "source_id": payload["source_id"].strip(),
        "filters": {
            "priorities": list(f.get("priorities") or []),
            "missing":    list(f.get("missing") or []),
            "categories": list(f.get("categories") or []),
            "hidePlanned": bool(f.get("hidePlanned")),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # upsert by id
    searches = [s for s in searches if s.get("id") != sid]
    searches.append(entry)
    save_saved_searches({"version": 1, "searches": searches})
    return jsonify(entry), 201


@app.route("/api/saved-searches/<search_id>", methods=["DELETE"])
def api_saved_search_delete(search_id: str):
    data = load_saved_searches()
    searches = [s for s in data.get("searches", []) if s.get("id") != search_id]
    if len(searches) == len(data.get("searches", [])):
        return jsonify({"error": "not found"}), 404
    save_saved_searches({"version": 1, "searches": searches})
    return ("", 204)


@app.route("/api/catalog-registry")
def api_catalog_registry():
    """Merged catalogue registry: extensions + file-loaded defaults.

    Three contributors, in precedence order:
      1. `app.catalog_sources` Protocols (extension-registered),
      2. `app.extra_catalogues` dicts (extension-registered raw entries),
      3. `data/catalog_registry.json` file entries.

    Frontend uses this to render the Catalogues rail dynamically, so
    adding a new catalogue is an extension append or a JSON edit rather
    than a code change.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _add(entry: dict | None) -> None:
        if not isinstance(entry, dict):
            return
        cid = entry.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            return
        seen.add(cid)
        out.append(entry)

    for src in (getattr(app, "catalog_sources", []) or []):
        _add(_entry_from_catalog_source(src))
    for entry in (app.extra_catalogues or []):
        _add(entry)
    for entry in load_catalog_registry().get("catalogues", []):
        _add(entry)
    return jsonify({"version": 1, "catalogues": out})


@app.route("/api/extensions/manifest")
def api_extensions_manifest():
    """Manifest of extension-registered UI actions for the frontend.

    Each entry is a free-form dict supplied by an extension at register()
    time. Documented fields (used by the frontend renderer):

      ``id``         Stable id, unique within an extension (e.g. "push").
      ``kind``       "button" | "toggle".
      ``label``      User-visible label.
      ``endpoint``   HTTP endpoint to call (extension-defined route).
      ``method``     "GET" | "POST".
      ``needs``      Optional list of required config keys, e.g.
                      ``["profile_id"]``. Frontend prompts before the call.
      ``replaces``   Optional core button id to swap (see REPLACEABLE_BUTTONS
                      registry in static/app.js). When set, the core button
                      is hidden and the extension's button takes its slot.
      ``preview_endpoint``  Optional. When present on a "button" action, the
                      frontend calls this first and shows the response in a
                      modal with [Apply]/[Cancel] before hitting ``endpoint``.
      ``interval_s``  Required on "toggle" actions. Poll interval in seconds.

    An entry may also carry ``rail_tabs``, a list of
    ``{"id", "label", "icon", "page"}`` dicts that add tabs to the right
    rail. Those are validated here (invalid tabs are dropped with a logged
    warning, see ``extensions.sanitise_rail_tabs``); everything else passes
    through verbatim.
    """
    from extensions import sanitise_rail_tabs
    return jsonify([sanitise_rail_tabs(e) for e in app.extensions_manifest])


@app.route("/api/sources")
def api_sources():
    """List of registered coverage sources for the frontend Sources rail."""
    out = []
    for src in app.coverage_sources:
        meta = src.metadata()
        out.append({
            "id": src.id(),
            "label": meta["label"],
            "color": meta["color"],
            "kind": meta["kind"],
            "attribution": meta["attribution"],
            "enabled_default": meta["enabled_default"],
        })
    return jsonify(out)


@app.route("/api/moc/<source_id>")
def api_moc(source_id: str):
    """Serve the cached FITS MOC blob for a registered MOC source.

    Lazy-fetches on first hit. Returns 404 for unknown ids or non-MOC sources,
    503 when mocpy is missing, 502 when the upstream fetch or validation
    fails. Successful responses are application/octet-stream: Aladin Lite's
    FITS MOC loader takes the bytes directly.
    """
    src = next(
        (s for s in app.coverage_sources
         if s.id() == source_id and isinstance(s, MocCoverageSource)),
        None,
    )
    if src is None:
        return jsonify({"error": "MOC source not found"}), 404
    if not _MOCPY_AVAILABLE:
        return jsonify({
            "error": "mocpy not installed; MOC overlays disabled",
        }), 503
    try:
        fits_path = src.ensure_cached()
    except _MocFetchError:
        logging.exception("MOC fetch failed for %r", _safe_log(source_id))
        return jsonify({"error": "upstream fetch failed"}), 502
    return Response(
        fits_path.read_bytes(),
        mimetype="application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{source_id}.fits"'},
    )


def _clamped_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(request.args.get(name, default))
    except (TypeError, ValueError):
        v = default
    if math.isnan(v) or v < lo or v > hi:
        v = default
    return v


@app.route("/api/observability")
def api_observability():
    try:
        from astropy.coordinates import AltAz, EarthLocation, SkyCoord
        from astropy.time import Time
        import astropy.units as u
    except Exception:
        logging.exception("astropy import failed")
        return jsonify({"error": "astropy not available"}), 500

    lat = _clamped_float("lat", 19.82, -90.0, 90.0)
    lon = _clamped_float("lon", -155.47, -180.0, 180.0)
    height = _clamped_float("height", 4205.0, -430.0, 9000.0)
    iso = request.args.get("time") or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    try:
        t = Time(iso)
    except Exception:
        return jsonify({"error": "invalid time"}), 400

    m = load_manifest()
    if m is None:
        return jsonify({"error": "manifest not found"}), 404
    if not m["targets"]:
        return jsonify({"lat": lat, "lon": lon, "time": iso, "targets": []})

    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=height * u.m)
    ras = [tg["center_ra_deg"] for tg in m["targets"]]
    decs = [tg["center_dec_deg"] for tg in m["targets"]]
    sc = SkyCoord(ras * u.deg, decs * u.deg)
    altaz = sc.transform_to(AltAz(obstime=t, location=loc))
    out = [
        {"target_id": tg["target_id"], "alt_deg": round(float(alt), 2), "az_deg": round(float(az), 2)}
        for tg, alt, az in zip(m["targets"], altaz.alt.deg, altaz.az.deg)
    ]
    return jsonify({"lat": lat, "lon": lon, "time": iso, "targets": out})


# --- Visibility (Plan A.3) ----------------------------------------------
# Bin a (peak_alt, hours_above_min) pair into one of five labels. Highest-
# quality gates run first; "fair" is the catch-all for "≥1h above min but
# doesn't meet good/great". See planner-design memory for the locked rules.
_VIS_LABELS = ("not_visible", "partial", "fair", "good", "great")


def _bin_visibility(peak_alt_deg: float, hours_above_min: float, min_alt_deg: float) -> str:
    if peak_alt_deg < min_alt_deg:
        return "not_visible"
    if hours_above_min < 1.0:
        return "partial"
    if peak_alt_deg >= 60.0 and hours_above_min >= 3.0:
        return "great"
    if peak_alt_deg >= 45.0 and hours_above_min >= 2.0:
        return "good"
    return "fair"


def compute_year_visibility(
    targets: list[dict],
    *, lat: float, lon: float, elev_m: float, min_alt_deg: float,
    year: int, sample_step_min: int = 15,
) -> dict[int, list[dict]]:
    """Return {target_id: [12 bins]} of visibility per month for the given site.

    For each month we sample altitudes across a 24h window centred on the
    15th at noon UTC, mask to astronomical-darkness times (sun alt < -18°),
    and reduce per-target to peak alt + hours-above-min. The (peak, hours,
    min) tuple feeds _bin_visibility for the label.

    Vectorised: one AltAz transform per month for the (N×T) (target, time)
    grid, then per-target reductions in numpy. ~1-3s for N≈70 targets.
    """
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_sun
    from astropy.time import Time
    import astropy.units as u
    import numpy as np
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    out: dict[int, list[dict]] = {int(t["target_id"]): [] for t in targets}
    if not targets:
        return out

    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=elev_m * u.m)
    ras = np.array([float(t["center_ra_deg"]) for t in targets])
    decs = np.array([float(t["center_dec_deg"]) for t in targets])
    ids = [int(t["target_id"]) for t in targets]
    samples_per_day = (24 * 60) // sample_step_min  # 96 at 15-min steps

    for month in range(1, 13):
        anchor = _dt(year, month, 15, 12, 0, 0, tzinfo=_tz.utc).replace(tzinfo=None)
        times_dt = [anchor + _td(minutes=sample_step_min * i)
                    for i in range(samples_per_day + 1)]
        t_grid = Time(times_dt)

        # Astronomical darkness mask via sun altitude.
        sun_altaz = get_sun(t_grid).transform_to(
            AltAz(obstime=t_grid, location=loc))
        is_dark = sun_altaz.alt.deg < -18.0  # shape (T,)

        if not bool(is_dark.any()):
            for tid in ids:
                out[tid].append({
                    "month": month, "label": "not_visible",
                    "peak_alt_deg": None, "hours_above_min": 0.0,
                })
            continue

        # One AltAz transformation for all targets x all times, broadcasting
        # a (n_targets, 1) SkyCoord against a (1, n_t) obstime instead of
        # flattening to a single (n_targets * n_t,) array. Broadcasting keeps
        # the per-timestamp precession/nutation/polar-motion matrices (ERFA
        # pnm06a et al.) computed once per timestamp rather than once per
        # target -- flattening previously forced ERFA to redo that work for
        # every target, which dominated runtime at manifest scale.
        sc = SkyCoord(ras[:, None] * u.deg, decs[:, None] * u.deg)
        alt_grid = sc.transform_to(
            AltAz(obstime=t_grid[None, :], location=loc)).alt.deg

        for i, tid in enumerate(ids):
            alts_dark = alt_grid[i, is_dark]
            if alts_dark.size == 0:
                out[tid].append({
                    "month": month, "label": "not_visible",
                    "peak_alt_deg": None, "hours_above_min": 0.0,
                })
                continue
            peak = float(np.max(alts_dark))
            hours = float(np.sum(alts_dark >= min_alt_deg)
                          * sample_step_min / 60.0)
            label = _bin_visibility(peak, hours, min_alt_deg)
            out[tid].append({
                "month": month, "label": label,
                "peak_alt_deg": round(peak, 2),
                "hours_above_min": round(hours, 2),
            })

    return out


def _require_astropy() -> tuple[Response, int] | None:
    """Return a 500 response if astropy can't be imported, else None.

    Centralised so the error message doesn't echo the underlying ImportError
    (which can carry path strings) back to the client.
    """
    try:
        import astropy  # noqa: F401  - presence check only
    except Exception:
        logging.exception("astropy import failed")
        return jsonify({"error": "astropy not available"}), 500
    return None


def _safe_log(value) -> str:
    """Strip CR/LF and truncate so user-supplied values can't forge log lines.

    `%r` already escapes control chars, but CodeQL's taint tracker doesn't
    model that: wrap user input through here at the log-call boundary to
    keep the log-injection rule quiet.
    """
    return str(value).replace("\r", "").replace("\n", "")[:200]


def _resolve_site_from_request() -> tuple[dict, tuple[Response, int] | None]:
    """Resolve the site for a /api/visibility call.

    Either `site_id=<id>` (looks up sites.json) or explicit lat/lon/elev_m/
    min_alt_deg query params. Defaults match the existing /api/observability
    fallback so behaviour stays predictable.
    """
    sid = request.args.get("site_id")
    if sid:
        sdata = next(
            (s for s in load_sites().get("sites", []) if s.get("id") == sid),
            None,
        )
        if not sdata:
            logging.info("visibility: unknown site_id %r", _safe_log(sid))
            return {}, (jsonify({"error": "unknown site_id"}), 404)
        return {
            "id": sid,
            "lat": float(sdata["lat"]),
            "lon": float(sdata["lon"]),
            "elev_m": float(sdata.get("elev_m") or 0.0),
            "min_alt_deg": float(sdata.get("min_alt_deg") or 30.0),
        }, None
    return {
        "id": None,
        "lat": _clamped_float("lat", 19.82, -90.0, 90.0),
        "lon": _clamped_float("lon", -155.47, -180.0, 180.0),
        "elev_m": _clamped_float("elev_m", 0.0, -430.0, 9000.0),
        "min_alt_deg": _clamped_float("min_alt_deg", 30.0, 0.0, 90.0),
    }, None


@app.route("/api/visibility/point")
def api_visibility_point():
    """Visibility bins for one arbitrary (ra_deg, dec_deg) point.

    Same compute as /api/visibility but the "target list" is a single
    synthesised point, so callers (e.g. the Inventory tile-detail panel)
    can show year-curves for cells that aren't in the manifest.
    """
    err = _require_astropy()
    if err is not None:
        return err
    site, err = _resolve_site_from_request()
    if err is not None:
        return err
    try:
        ra = float(request.args.get("ra"))
        dec = float(request.args.get("dec"))
    except (TypeError, ValueError):
        return jsonify({"error": "ra and dec required (decimal degrees)"}), 400
    if not (-360.0 <= ra <= 360.0) or not (-90.0 <= dec <= 90.0):
        return jsonify({"error": "ra/dec out of range"}), 400
    try:
        year = int(request.args.get("year") or datetime.now(timezone.utc).year)
    except ValueError:
        return jsonify({"error": "year must be an integer"}), 400
    if not (1900 <= year <= 2200):
        return jsonify({"error": "year out of range"}), 400

    # Cache by (site, ra, dec, year). Manifest mtime is irrelevant here since
    # the point is supplied directly, not looked up in the manifest.
    cache_key = _site_cache_key(site) + (round(ra, 4), round(dec, 4), year)
    bins = _visibility_cache.get(cache_key)
    if bins is None:
        bins = compute_year_visibility(
            [{"target_id": 0, "center_ra_deg": ra, "center_dec_deg": dec}],
            lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
            min_alt_deg=site["min_alt_deg"], year=year,
        )
        _visibility_cache[cache_key] = bins
    return jsonify({
        "site": site,
        "year": year,
        "labels": list(_VIS_LABELS),
        "ra_deg": ra,
        "dec_deg": dec,
        "months": bins.get(0, []),
    })


@app.route("/api/visibility/panels", methods=["POST"])
def api_visibility_panels():
    """Aggregated visibility for a list of mosaic panel (ra, dec) centres.

    Used by the planner to summarise a mosaic's per-month "fraction of
    panels visible". POST body::

        { "panels": [{"ra_deg": ..., "dec_deg": ...}, ...],
          "year":     optional int,
          "site_id":  optional str  (else lat/lon/min_alt_deg from query) }

    Returns per-panel month-bins identical to /api/visibility/point, plus
    a ``months`` aggregate of {panels_visible, total_panels} per month.
    Single-panel calls degenerate to the same bins as /api/visibility/point.
    """
    err = _require_astropy()
    if err is not None:
        return err
    site, err = _resolve_site_from_request()
    if err is not None:
        return err
    body = request.get_json(silent=True) or {}
    panels_in = body.get("panels") if isinstance(body, dict) else None
    if not isinstance(panels_in, list) or not panels_in:
        return jsonify({"error": "panels: non-empty list required"}), 400
    if len(panels_in) > 400:
        return jsonify({"error": "panels: max 400 per request"}), 400
    try:
        year = int(body.get("year") or datetime.now(timezone.utc).year)
    except (TypeError, ValueError):
        return jsonify({"error": "year must be an integer"}), 400
    if not (1900 <= year <= 2200):
        return jsonify({"error": "year out of range"}), 400

    parsed, err = _parse_visibility_panels(panels_in)
    if err is not None:
        return jsonify({"error": err}), 400
    bins = _panel_bins(parsed, site, year)
    return jsonify(_panels_payload(site, year, parsed, bins))


def _parse_visibility_panels(panels_in: list, label: str = "panel"):
    """Validate a list of {ra_deg, dec_deg} panels.

    Returns ``(parsed, None)`` with coordinates rounded to 4 places, or
    ``(None, message)`` for the first bad panel.
    """
    parsed: list[tuple[float, float]] = []
    for i, p in enumerate(panels_in):
        if not isinstance(p, dict):
            return None, f"{label} {i}: must be object"
        try:
            ra = float(p["ra_deg"])
            dec = float(p["dec_deg"])
        except (KeyError, TypeError, ValueError):
            return None, f"{label} {i}: ra_deg/dec_deg required"
        if not (-360.0 <= ra <= 360.0) or not (-90.0 <= dec <= 90.0):
            return None, f"{label} {i}: ra/dec out of range"
        parsed.append((round(ra, 4), round(dec, 4)))
    return parsed, None


def _panel_bins(parsed: list[tuple[float, float]], site: dict, year: int) -> list[list[dict]]:
    """Month bins for each (ra, dec), one compute for every cache miss.

    A point repeated within ``parsed`` is computed once.
    """
    site_key = _site_cache_key(site)
    per_panel_bins: list[list[dict] | None] = [None] * len(parsed)
    misses: list[tuple[int, float, float]] = []
    for idx, (ra, dec) in enumerate(parsed):
        cached = _visibility_cache.get(site_key + (ra, dec, year))
        if cached is None:
            misses.append((idx, ra, dec))
        else:
            per_panel_bins[idx] = cached.get(0, [])

    if misses:
        unique = list(dict.fromkeys((ra, dec) for _, ra, dec in misses))
        targets = [{"target_id": i, "center_ra_deg": ra, "center_dec_deg": dec}
                   for i, (ra, dec) in enumerate(unique)]
        bins_dict = compute_year_visibility(
            targets,
            lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
            min_alt_deg=site["min_alt_deg"], year=year,
        )
        slot = {pt: i for i, pt in enumerate(unique)}
        for idx, ra, dec in misses:
            single_bins = bins_dict.get(slot[(ra, dec)], [])
            _visibility_cache[site_key + (ra, dec, year)] = {0: single_bins}
            per_panel_bins[idx] = single_bins
    return [b or [] for b in per_panel_bins]


def _panels_payload(site: dict, year: int, parsed: list[tuple[float, float]],
                    per_panel_bins: list[list[dict]]) -> dict:
    """The response body for one mosaic, shared by the single and batch routes."""
    total = len(parsed)
    months: list[dict] = []
    for m in range(1, 13):
        visible = 0
        for bins in per_panel_bins:
            b = next((x for x in (bins or []) if x.get("month") == m), None)
            if b and b.get("label") != "not_visible":
                visible += 1
        months.append({"month": m, "panels_visible": visible, "total_panels": total})

    return {
        "site": site,
        "year": year,
        "labels": list(_VIS_LABELS),
        "panel_count": total,
        "months": months,
        "per_panel": [
            {"ra_deg": parsed[i][0], "dec_deg": parsed[i][1],
             "months": per_panel_bins[i] or []}
            for i in range(total)
        ],
    }


@app.route("/api/visibility/panels/batch", methods=["POST"])
def api_visibility_panels_batch():
    """Aggregated visibility for several plans' panels in one request.

    POST body::

        { "plans": [{"id": "<plan id>", "panels": [{"ra_deg": ..., "dec_deg": ...}, ...]}, ...],
          "year":    optional int,
          "site_id": optional str  (else lat/lon/min_alt_deg from query) }

    Returns ``{"site", "year", "results": {"<plan id>": <the body
    /api/visibility/panels returns for that plan's panels>}}``. The total across
    all plans is capped at the single route's 400 panels. One astropy pass
    covers every panel that is not already cached, which is what makes this
    cheaper than one request per plan.
    """
    err = _require_astropy()
    if err is not None:
        return err
    site, err = _resolve_site_from_request()
    if err is not None:
        return err
    body = request.get_json(silent=True) or {}
    plans_in = body.get("plans") if isinstance(body, dict) else None
    if not isinstance(plans_in, list) or not plans_in:
        return jsonify({"error": "plans: non-empty list required"}), 400
    try:
        year = int(body.get("year") or datetime.now(timezone.utc).year)
    except (TypeError, ValueError):
        return jsonify({"error": "year must be an integer"}), 400
    if not (1900 <= year <= 2200):
        return jsonify({"error": "year out of range"}), 400

    parsed_by_plan: list[tuple[str, list[tuple[float, float]]]] = []
    total = 0
    for i, pl in enumerate(plans_in):
        if not isinstance(pl, dict) or pl.get("id") in (None, ""):
            return jsonify({"error": f"plan {i}: object with id required"}), 400
        panels_in = pl.get("panels")
        if not isinstance(panels_in, list) or not panels_in:
            return jsonify({"error": f"plan {i}: panels: non-empty list required"}), 400
        total += len(panels_in)
        if total > 400:
            return jsonify({"error": "panels: max 400 per request"}), 400
        parsed, err_msg = _parse_visibility_panels(panels_in, label=f"plan {i} panel")
        if err_msg is not None:
            return jsonify({"error": err_msg}), 400
        parsed_by_plan.append((str(pl["id"]), parsed))

    flat = [pt for _, parsed in parsed_by_plan for pt in parsed]
    flat_bins = _panel_bins(flat, site, year)
    results: dict[str, dict] = {}
    pos = 0
    for plan_id, parsed in parsed_by_plan:
        n = len(parsed)
        results[plan_id] = _panels_payload(site, year, parsed, flat_bins[pos:pos + n])
        pos += n
    return jsonify({"site": site, "year": year, "results": results})


@app.route("/api/visibility")
def api_visibility():
    err = _require_astropy()
    if err is not None:
        return err

    site, err = _resolve_site_from_request()
    if err is not None:
        return err

    m = load_manifest()
    if m is None:
        return jsonify({"error": "manifest not found"}), 404

    try:
        year = int(request.args.get("year") or datetime.now(timezone.utc).year)
    except ValueError:
        return jsonify({"error": "year must be an integer"}), 400
    if not (1900 <= year <= 2200):
        return jsonify({"error": "year out of range"}), 400

    # Cache only the bins (the expensive part). The site dict comes from
    # the live request so site_id-based and lat/lon-based calls with the
    # same coords don't pollute each other's metadata.
    cache_key = _site_cache_key(site) + (_manifest_cache_mtime or 0.0, year)
    bins = _visibility_cache.get(cache_key)
    if bins is None:
        targets = m.get("targets") or []
        bins = compute_year_visibility(
            targets,
            lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
            min_alt_deg=site["min_alt_deg"], year=year,
        )
        _visibility_cache[cache_key] = bins
    return jsonify({
        "site": site,
        "year": year,
        "labels": list(_VIS_LABELS),
        "targets": bins,
    })


def _validate_site(s: dict) -> str | None:
    if not isinstance(s, dict):
        return "site must be an object"
    sid = s.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return "id must be a non-empty string"
    if not isinstance(s.get("name"), str) or not s["name"].strip():
        return f"site {sid!r}: name must be a non-empty string"
    try:
        lat = float(s.get("lat"))
        lon = float(s.get("lon"))
    except (TypeError, ValueError):
        return f"site {sid!r}: lat and lon must be numbers"
    if not (-90.0 <= lat <= 90.0):
        return f"site {sid!r}: lat out of range"
    if not (-180.0 <= lon <= 180.0):
        return f"site {sid!r}: lon out of range"
    if "elev_m" in s and s["elev_m"] is not None:
        try:
            elev = float(s["elev_m"])
        except (TypeError, ValueError):
            return f"site {sid!r}: elev_m must be a number"
        if not (-430.0 <= elev <= 9000.0):
            return f"site {sid!r}: elev_m out of range"
    if "min_alt_deg" in s and s["min_alt_deg"] is not None:
        try:
            ma = float(s["min_alt_deg"])
        except (TypeError, ValueError):
            return f"site {sid!r}: min_alt_deg must be a number"
        if not (0.0 <= ma <= 90.0):
            return f"site {sid!r}: min_alt_deg out of range"
    return None


@app.route("/api/sites", methods=["GET", "POST"])
def api_sites():
    if request.method == "GET":
        return jsonify(load_sites())
    payload = request.get_json(silent=True) or {}
    sites = payload.get("sites")
    if not isinstance(sites, list) or not sites:
        return jsonify({"error": "sites array required and must be non-empty"}), 400
    seen_ids: set[str] = set()
    cleaned: list[dict] = []
    for s in sites:
        err = _validate_site(s)
        if err:
            return jsonify({"error": err}), 400
        sid = s["id"].strip()
        if sid in seen_ids:
            return jsonify({"error": f"duplicate site id {sid!r}"}), 400
        seen_ids.add(sid)
        out = {
            "id": sid,
            "name": s["name"].strip(),
            "lat": float(s["lat"]),
            "lon": float(s["lon"]),
        }
        if s.get("elev_m") is not None:
            out["elev_m"] = float(s["elev_m"])
        if s.get("min_alt_deg") is not None:
            out["min_alt_deg"] = float(s["min_alt_deg"])
        cleaned.append(out)
    save_sites({"version": 1, "sites": cleaned})
    return jsonify({"ok": True, "sites": cleaned})


def _ts_db_file_exists(path: str) -> bool:
    """True when *path* (a ts_db_path, ``$VAR``-expanded) points at a file
    that actually exists. Used to decide whether a direct-write NINA sync
    is usable: ACP runs in places (Docker on a homelab box) that have no
    Target Scheduler database at all, only a NINA imaging PC does."""
    return Path(os.path.expandvars(path)).exists()


_DESTINATION_KINDS = frozenset({"local_db", "shared_file"})


def _validate_destination(d: dict) -> str | None:
    """Return an error string if the destination is invalid, else None."""
    if not isinstance(d, dict):
        return "destination must be a JSON object"
    did = d.get("id")
    if not isinstance(did, str) or not did.strip():
        return "destination id required (non-empty string)"
    label = d.get("label")
    if not isinstance(label, str) or not label.strip():
        return f"destination {did!r}: label required"
    kind = d.get("kind")
    if kind not in _DESTINATION_KINDS:
        return (
            f"destination {did!r}: kind must be one of "
            f"{sorted(_DESTINATION_KINDS)}, got {kind!r}"
        )
    if kind == "local_db":
        ts_db = d.get("ts_db_path")
        if not isinstance(ts_db, str) or not ts_db.strip():
            return f"destination {did!r}: kind=local_db requires ts_db_path"
    elif kind == "shared_file":
        ep = d.get("export_path")
        if not isinstance(ep, str) or not ep.strip():
            return f"destination {did!r}: kind=shared_file requires export_path"
    return None


def resolve_destination_paths(destination: dict | None) -> dict:
    """Resolve where a sync's zip should land and which sqlite file to read
    for TS's DatabaseVersion, based on a destination's ``kind``.

    Centralises the kind dispatch that used to be copy-pasted at
    validation, serialisation, and sync-path-resolution sites. Unlike the
    other two sites, ``destination`` here can come straight off disk via
    load_destinations() (which never validates), so a hand-edited
    destinations.json with an unknown kind must not silently fall through
    to the legacy global-path behaviour: it RAISES ValueError instead,
    which the caller turns into a 400 naming the bad kind.

    ``destination=None`` means the legacy, pre-multi-rig call: sync all
    non-draft plans to the global ZIP_OUTPUT_DIR / TS_DB_PATH.

    Returns {"zip_path": Path|None, "download_url": bool, "ts_db_path": str}.
    ``zip_path`` is None when the caller should build the default
    timestamped path in ZIP_OUTPUT_DIR (legacy mode, or kind=local_db);
    ``download_url`` says whether that default download-url scheme applies.
    """
    if destination is None:
        return {"zip_path": None, "download_url": True, "ts_db_path": str(TS_DB_PATH)}
    kind = destination.get("kind")
    if kind == "shared_file":
        return {"zip_path": Path(destination["export_path"]), "download_url": False, "ts_db_path": str(TS_DB_PATH)}
    if kind == "local_db":
        return {"zip_path": None, "download_url": True, "ts_db_path": destination["ts_db_path"]}
    raise ValueError(f"destination {destination.get('id')!r}: unsupported kind {kind!r}")


@app.route("/api/destinations", methods=["GET", "POST"])
def api_destinations():
    """Read/write the full destinations list. GET returns
    ``{version, destinations: [...]}``; the list is empty when no
    destinations.json exists (single-rig users never see the concept).
    POST replaces the full list: partial updates not supported (low
    cardinality, simpler UI).
    """
    if request.method == "GET":
        data = load_destinations()
        out = {**data, "destinations": [
            {**d, "ts_db_available": _ts_db_file_exists(d["ts_db_path"])}
            if d.get("kind") == "local_db" else dict(d)
            for d in data.get("destinations", [])
        ]}
        return jsonify(out)
    payload = request.get_json(silent=True) or {}
    dests = payload.get("destinations")
    if not isinstance(dests, list):
        return jsonify({"error": "destinations array required"}), 400
    seen_ids: set[str] = set()
    cleaned: list[dict] = []
    for d in dests:
        err = _validate_destination(d)
        if err:
            return jsonify({"error": err}), 400
        did = d["id"].strip()
        if did in seen_ids:
            return jsonify({"error": f"duplicate destination id {did!r}"}), 400
        seen_ids.add(did)
        out: dict = {
            "id": did,
            "label": d["label"].strip(),
            "kind": d["kind"],
        }
        if d["kind"] == "local_db":
            out["ts_db_path"] = d["ts_db_path"].strip()
        else:  # shared_file
            out["export_path"] = d["export_path"].strip()
            ap = d.get("acquired_path")
            if isinstance(ap, str) and ap.strip():
                out["acquired_path"] = ap.strip()
        notes = d.get("notes")
        if isinstance(notes, str) and notes.strip():
            out["notes"] = notes.strip()
        cleaned.append(out)
    # Preserve any existing backfilled_at flag: it's metadata about
    # whether the one-shot plan-backfill migration has run, not user
    # data the editor should clobber.
    prev = load_destinations()
    out_doc = {"version": 1, "destinations": cleaned}
    if isinstance(prev, dict) and prev.get("backfilled_at"):
        out_doc["backfilled_at"] = prev["backfilled_at"]
    save_destinations(out_doc)
    return jsonify({"ok": True, "destinations": cleaned})


def _gaps_query_params() -> tuple[dict, tuple[Response, int] | None]:
    """Parse + validate /api/gaps query params. Returns (params, error_response).

    On success error_response is None. On bad input it's a (response, status)
    tuple ready to return from the route. Defaults match the documented
    "Ha covered, SII not yet" recipe.
    """
    have = request.args.get("have", "Ha")
    missing = request.args.get("missing", "SII")
    if not isinstance(have, str) or not have or not isinstance(missing, str) or not missing:
        return {}, (jsonify({"error": "have/missing must be non-empty strings"}), 400)
    sources_raw = request.args.get("sources")
    if sources_raw is not None:
        wanted = [s.strip() for s in sources_raw.split(",") if s.strip()]
    else:
        wanted = None  # None = all enabled-by-default sources
    try:
        min_have = float(request.args.get("min_have_hours", 1.0))
        max_missing = float(request.args.get("max_missing_hours", 0.5))
    except (TypeError, ValueError):
        return {}, (jsonify({"error": "min_have_hours/max_missing_hours must be numeric"}), 400)
    return {
        "have": have, "missing": missing,
        "source_ids": wanted,
        "min_have_hours": min_have,
        "max_missing_hours": max_missing,
    }, None


def _source_passes_threshold(src, filter_name: str, min_hours: float) -> bool:
    """True if any region from src.coverage() has >= min_hours of filter_name.

    Returns True for sources without per-region hours (MOC sources): the
    threshold is meaningless there, so they always qualify if their declared
    filter matches: that match is enforced separately by compute_gap_moc.
    """
    coverage = getattr(src, "coverage", None)
    if coverage is None:
        return True
    saw_any_region = False
    for region in coverage():
        saw_any_region = True
        f = (region.get("filters") or {}).get(filter_name)
        if f and float(f.get("hours", 0.0)) >= min_hours:
            return True
    # No regions at all → MOC source (or empty manifest). Don't filter it out
    # here; compute_gap_moc will skip it if it has no coverage at this filter.
    return not saw_any_region


def _select_gap_sources(have: str, missing: str, source_ids: list[str] | None,
                       min_have: float, max_missing: float) -> tuple[list, list[str], list[tuple[str, str]]]:
    """Filter app.coverage_sources by id + per-side hour thresholds.

    Returns (sources_to_pass_in, restricted_ids, threshold_skipped). Sources
    that satisfy *either* side's threshold are passed through; compute_gap_moc
    sorts out which side they actually contribute to.
    """
    candidates = list(app.coverage_sources)
    if source_ids is not None:
        wanted = set(source_ids)
        candidates = [s for s in candidates if s.id() in wanted]
    else:
        # Default = enabled-by-default sources only.
        candidates = [s for s in candidates if s.metadata().get("enabled_default")]

    selected: list = []
    selected_ids: list[str] = []
    skipped: list[tuple[str, str]] = []
    for src in candidates:
        ok_have = _source_passes_threshold(src, have, min_have)
        ok_missing = _source_passes_threshold(src, missing, max_missing)
        if ok_have or ok_missing:
            selected.append(src)
            selected_ids.append(src.id())
        else:
            skipped.append((src.id(),
                            f"below {min_have}h threshold for {have} and "
                            f"below {max_missing}h threshold for {missing}"))
    return selected, selected_ids, skipped


@app.route("/api/gaps")
def api_gaps():
    """Multi-source gap finder: where `have` is covered but `missing` isn't.

    Returns 503 when mocpy isn't installed (MOC algebra is the whole point of
    this route; CSV consumers fall back via /api/export/priority instead).
    """
    if not _MOCPY_AVAILABLE:
        return jsonify({"error": "mocpy not installed"}), 503
    params, err = _gaps_query_params()
    if err is not None:
        return err

    sources, selected_ids, threshold_skipped = _select_gap_sources(
        params["have"], params["missing"], params["source_ids"],
        params["min_have_hours"], params["max_missing_hours"],
    )
    result = compute_gap_moc(
        sources,
        have_filter=params["have"],
        missing_filter=params["missing"],
        source_ids=selected_ids,
    )
    skipped_payload = [{"source_id": sid, "reason": reason}
                       for sid, reason in (threshold_skipped + result.skipped)]

    if result.gap_moc is None:
        return jsonify({
            "have_filter": params["have"],
            "missing_filter": params["missing"],
            "have_sources": result.have_sources,
            "missing_sources": result.missing_sources,
            "gap_sky_fraction": 0.0,
            "candidates": [],
            "skipped": skipped_payload,
        })

    cands = candidates_in_moc(result.gap_moc, load_catalogs())
    # moc_url echoes the same query string so a frontend can fetch the FITS
    # without re-parsing: keep the param order stable for cache-friendliness.
    qs = urllib.parse.urlencode({
        "have": params["have"],
        "missing": params["missing"],
        "sources": ",".join(selected_ids),
        "min_have_hours": params["min_have_hours"],
        "max_missing_hours": params["max_missing_hours"],
    })
    return jsonify({
        "have_filter": params["have"],
        "missing_filter": params["missing"],
        "have_sources": result.have_sources,
        "missing_sources": result.missing_sources,
        "gap_sky_fraction": result.gap_sky_fraction,
        "candidates": [
            {"catalog": c.catalog, "name": c.name,
             "ra_deg": c.ra_deg, "dec_deg": c.dec_deg}
            for c in cands
        ],
        "skipped": skipped_payload,
        "moc_url": f"/api/gaps/moc.fits?{qs}",
    })


@app.route("/api/gaps/moc.fits")
def api_gaps_moc_fits():
    """Serve the gap MOC as raw FITS bytes."""
    if not _MOCPY_AVAILABLE:
        return jsonify({"error": "mocpy not installed"}), 503
    params, err = _gaps_query_params()
    if err is not None:
        return err

    sources, selected_ids, _ = _select_gap_sources(
        params["have"], params["missing"], params["source_ids"],
        params["min_have_hours"], params["max_missing_hours"],
    )
    result = compute_gap_moc(
        sources,
        have_filter=params["have"],
        missing_filter=params["missing"],
        source_ids=selected_ids,
    )
    if result.gap_moc is None:
        return jsonify({"error": "no gap region for these parameters"}), 404

    buf = BytesIO()
    result.gap_moc.serialize(format="fits").writeto(buf)
    return Response(
        buf.getvalue(),
        mimetype="application/octet-stream",
        headers={"Content-Disposition": 'inline; filename="gap.fits"'},
    )


# Catalogs that contributed to the legacy /api/export/priority CSV. Kept here
# because the gap-finder pulls candidates from the entire load_catalogs() dict
# but the CSV consumer expects only these three.
_PRIORITY_CSV_CATALOGS = ("green_snrs", "smgps_candidates")
_PRIORITY_CSV_HEADER = [
    "catalog", "name", "ra_deg", "dec_deg", "l_deg", "b_deg",
    "overlap_target_id", "overlap_target_objects",
    "ha_hours", "sii_hours", "oiii_hours",
]


def _priority_csv_response(rows: list[list]) -> tuple[str, int, dict]:
    out = StringIO()
    w = csv.writer(out)
    w.writerow(_PRIORITY_CSV_HEADER)
    for row in rows:
        w.writerow(row)
    return out.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=priority_sii_targets.csv",
    }


@app.route("/api/export/priority")
def api_export_priority():
    """CSV of overlay candidates where Ha >= 1h but SII < 0.5h (validation-gap bucket).

    With mocpy installed: thin wrapper over compute_gap_moc against the user's
    own manifest. CSV-shaped per-target overlap fields (galactic l/b, target id,
    filter hours) are re-derived here: they're export-shape, not gap-math, so
    they don't belong inside gaps.py.

    Without mocpy: falls back to the original inline implementation so the CSV
    keeps working on stripped-down installs.
    """
    cats = load_catalogs()
    m = load_manifest()
    if m is None or not cats:
        return ("manifest or catalogs missing", 404)

    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
    except Exception:
        return ("astropy missing", 500)

    if _MOCPY_AVAILABLE:
        manifest_src = next(
            (s for s in app.coverage_sources if s.id() == "manifest"), None,
        )
        if manifest_src is None:
            return ("manifest source not registered", 404)
        result = compute_gap_moc(
            [manifest_src],
            have_filter="Ha", missing_filter="SII",
            source_ids=["manifest"],
        )
        if result.gap_moc is None:
            return _priority_csv_response([])

        # Restrict to the legacy CSV's catalog subset before MOC-filtering.
        scoped = {k: v for k, v in cats.items() if k in _PRIORITY_CSV_CATALOGS}
        candidates = candidates_in_moc(result.gap_moc, scoped)

        target_ras = [t["center_ra_deg"] for t in m["targets"]]
        target_decs = [t["center_dec_deg"] for t in m["targets"]]
        target_coords = (
            SkyCoord(target_ras * u.deg, target_decs * u.deg) if target_ras else None
        )
        cat_lookup = {(name, e.get("name", "")): e
                      for name, entries in scoped.items() for e in entries}

        rows: list[list] = []
        for c in candidates:
            entry = cat_lookup.get((c.catalog, c.name), {})
            match_tid, match_objs = "", ""
            ha = sii = oiii = 0.0
            if target_coords is not None:
                cand_sc = SkyCoord(c.ra_deg * u.deg, c.dec_deg * u.deg)
                idx, sep, _ = cand_sc.match_to_catalog_sky(target_coords)
                if float(sep.arcminute) <= 45:
                    t = m["targets"][int(idx)]
                    match_tid = t["target_id"]
                    match_objs = "|".join(t.get("objects", []))
                    ha = t["filters"].get("Ha", {}).get("total_hours", 0.0)
                    sii = t["filters"].get("SII", {}).get("total_hours", 0.0)
                    oiii = t["filters"].get("OIII", {}).get("total_hours", 0.0)
            # Re-apply the 1h/0.5h gate: the gap MOC is union-of-Ha minus
            # union-of-SII at the cell level, but the legacy CSV gates on the
            # nearest-target's hours. Targets near a candidate but with low Ha
            # would otherwise sneak in.
            if ha < 1.0 or sii >= 0.5:
                continue
            rows.append([
                c.catalog, c.name, c.ra_deg, c.dec_deg,
                entry.get("l_deg", ""), entry.get("b_deg", ""),
                match_tid, match_objs, ha, sii, oiii,
            ])
        return _priority_csv_response(rows)

    # --- mocpy-missing fallback: pre-Phase-4 inline implementation ----------
    target_ras = [t["center_ra_deg"] for t in m["targets"]]
    target_decs = [t["center_dec_deg"] for t in m["targets"]]
    target_coords = (
        SkyCoord(target_ras * u.deg, target_decs * u.deg) if target_ras else None
    )

    out = StringIO()
    w = csv.writer(out)
    w.writerow(_PRIORITY_CSV_HEADER)

    for cat_name, entries in cats.items():
        if cat_name not in _PRIORITY_CSV_CATALOGS:
            continue
        valid = [e for e in entries if e.get("ra_deg") is not None and e.get("dec_deg") is not None]
        if not valid:
            continue
        if target_coords is not None:
            cras = [e["ra_deg"] for e in valid]
            cdecs = [e["dec_deg"] for e in valid]
            cand = SkyCoord(cras * u.deg, cdecs * u.deg)
            idx, sep, _ = cand.match_to_catalog_sky(target_coords)
            sep_arcmin = sep.arcminute
        else:
            idx = [None] * len(valid)
            sep_arcmin = [None] * len(valid)

        for e, best_idx, best_sep in zip(valid, idx, sep_arcmin):
            match_tid, match_objs = "", ""
            ha = sii = oiii = 0.0
            if target_coords is not None and best_sep is not None and best_sep <= 45:
                t = m["targets"][int(best_idx)]
                match_tid = t["target_id"]
                match_objs = "|".join(t.get("objects", []))
                ha = t["filters"].get("Ha", {}).get("total_hours", 0.0)
                sii = t["filters"].get("SII", {}).get("total_hours", 0.0)
                oiii = t["filters"].get("OIII", {}).get("total_hours", 0.0)
            if ha >= 1.0 and sii < 0.5:
                w.writerow([
                    cat_name, e.get("name", ""), e["ra_deg"], e["dec_deg"],
                    e.get("l_deg", ""), e.get("b_deg", ""),
                    match_tid, match_objs, ha, sii, oiii,
                ])

    return out.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=priority_sii_targets.csv",
    }


# Filter names flow into innerHTML in renderPlanEditor and a few other
# places. Frontend escapes them, but defence-in-depth: reject anything
# outside the legitimate astrophoto filter-name shape on the way in,
# so a poisoned gear.json can never enter the system in the first place.
# Real filter names are short tokens (Ha, SII, OIII, L, R, G, B, V, IDAS,
# "Antlia Ha", "IDAS + RED"): < and " have no place here. A space is fine
# inside the name but not at either end (docs/specs/gear-templates.md
# section 4).
_FILTER_NAME_RE = gt.FILTER_NAME_RE


def _validate_gear_payload(payload: dict) -> str | None:
    cameras = payload.get("cameras") or []
    if not isinstance(cameras, list):
        return "cameras must be a list"
    for i, c in enumerate(cameras):
        if not isinstance(c, dict):
            return f"cameras[{i}] must be an object"
        filters = c.get("filters") or {}
        if not isinstance(filters, dict):
            return f"cameras[{i}].filters must be an object"
        for fname in filters.keys():
            if (not isinstance(fname, str) or not _FILTER_NAME_RE.match(fname)
                    or fname != fname.strip()):
                return (f"cameras[{i}].filters has invalid filter name "
                        f"{fname!r}: must match {_FILTER_NAME_RE.pattern} "
                        "with no space at either end")
    return None


def _gear_response(g: dict) -> dict:
    """What GET /api/gear and a 409 serve."""
    out = {
        "version": g.get("version", 2),
        "telescopes": g.get("telescopes", []),
        # Derive sensor_width_px/sensor_height_px on every read so
        # cameras added via the UI editor (which only ever writes
        # sensor_px) still carry the scalar fields the NINA plugin
        # expects, not just ones seeded from a manifest scan.
        "cameras": [_with_sensor_scalars(c) for c in g.get("cameras", [])],
        "telescope_colours": g.get("telescope_colours", {}),
    }
    if gt.is_migrated(g):
        for key in ("gear_schema", "rev", "tidy", "links_capable", "wheel_unlinked",
                    "wheel_ambiguous", "conflict_count"):
            out[key] = g.get(key, {} if key in ("tidy", "links_capable") else
                             [] if key.startswith("wheel_") else 0)
    return out


def _plans_using(removed_ids: set, cameras: list, plans: list) -> dict[str, dict]:
    """plan id -> {template id: rows} for the removed templates still in use."""
    out: dict[str, dict] = {}
    for tid, per_plan in gt.usage_by_template(plans, cameras).items():
        if tid in removed_ids:
            for pid, n in per_plan.items():
                out.setdefault(pid, {})[tid] = n
    return out


def _gear_camera_context(doc: dict) -> dict:
    """Who reports each camera, for the save guard."""
    try:
        profiles = load_fingerprints().get("profiles") or {}
    except (OSError, ValueError):
        profiles = {}
    cams = [c for c in doc.get("cameras") or [] if isinstance(c, dict)]
    return gt.link_cameras(cams, gt._profile_entries(profiles), _norm_gear_name)


def _camera_edit_blocked(doc: dict, ctx: dict, cam: dict) -> bool:
    return gt.edit_blocked(cam, ctx["linked"].get(cam.get("id"), []),
                           ctx["name_reports"].get(cam.get("id"), []), doc.get("links_capable"))


def _ts_name_clash(doc: dict, ctx: dict, cam: dict, tpl: dict, new_name: str):
    """A TS template of a profile linked to the camera that already holds
    *new_name* and is not this template's own link, as (profile, TS id)."""
    lists = _load_ts_templates_store()
    pids = {p["profile_id"] for p in ctx["linked"].get(cam.get("id"), [])}
    pids.update((tpl.get("ts") or {}).keys())
    for pid in sorted(p for p in pids if p):
        own = ((tpl.get("ts") or {}).get(pid) or {}).get("id")
        for row in (lists.get(pid) or {}).get("templates") or []:
            if (isinstance(row, dict) and str(row.get("name") or "").lower() == new_name.lower()
                    and row.get("id") is not None and row.get("id") != own):
                return pid, row.get("id")
    return None


def _carry_into_plans(renames: dict, subs: dict, row_ok=None) -> list[dict]:
    """Carry gear template changes into the plan rows (spec section 4).

    *renames* is template id to new name; *subs* is template id to (old sub,
    new sub). A rename reaches every row on the template. A sub change reaches
    only the rows that follow the template, whose `template_sub_s` equals the
    old sub, and never touches `target_hours` or `target_frames` except where
    a row changes unit. Writes plans.json when anything changed and returns
    the `sub_changes`. The caller holds GEAR_LOCK and PLANS_LOCK.
    """
    if not renames and not subs:
        return []
    data = load_plans()
    plans = copy.deepcopy(data.get("plans") or [])
    sub_changes: list[dict] = []
    changed = False
    for p in plans:
        if not isinstance(p, dict):
            continue
        touched = False
        for row in fr.rows_of(p):
            tid = row.get("template_id")
            if tid in renames and row.get("template") != renames[tid]:
                row["template"] = renames[tid]
                touched = True
            if tid in subs:
                old, new = subs[tid]
                have = fr._num(row.get("template_sub_s"))
                if have is not None and abs(have - old) < 1e-9 and (row_ok is None or row_ok(p, row)):
                    hours = fr._num(row.get("actual_hours"))
                    fr.set_row_sub(row, new)
                    sub_changes.append({
                        "plan_id": p.get("id"), "row_id": row.get("id"), "filter": row.get("filter"),
                        "template": row.get("template"), "old_sub_s": old, "new_sub_s": new,
                        "acquired_frames": round(hours * 3600.0 / old) if hours else 0,
                    })
                    touched = True
        if touched:
            p["updated_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
    if changed:
        if not _backup_before_row_rewrite():
            logging.error("Writing template changes into plan rows without a backup of %s", PLANS_PATH)
        save_plans({**data, "version": data.get("version", 1), "plans": plans})
    return sub_changes


def _gear_post_v3(payload: dict, base: dict):
    """POST /api/gear from a page that sends templates (gear_schema 3)."""
    if payload.get("rev") != base.get("rev"):
        return jsonify({"error": "gear changed elsewhere", "gear": _gear_response(load_gear())}), 409
    if not isinstance(payload.get("telescopes"), list) or not all(
            isinstance(t, dict) for t in payload["telescopes"]):
        return jsonify({"error": "telescopes must be a list of objects"}), 400
    cams_in = payload["cameras"]
    stored_cams = {c.get("id"): c for c in base.get("cameras") or [] if isinstance(c, dict)}
    stored_loc = {t["id"]: (cam.get("id"), f, t) for cam, f, t in gt.all_templates(base)}

    seen_cams: set[str] = set()
    seen_ids: set[str] = set()
    new_cams: list[dict] = []
    for i, c in enumerate(cams_in):
        cid = c.get("id")
        if not isinstance(cid, str) or not cid.strip():
            return jsonify({"error": f"cameras[{i}].id must be a non-empty string"}), 400
        if cid in seen_cams:
            return jsonify({"error": f"two cameras share the id {cid!r}"}), 400
        seen_cams.add(cid)
        cam = {k: copy.deepcopy(v) for k, v in c.items() if k not in ("filters", *gt.DERIVED_CAMERA)}
        old_cam = stored_cams.get(cid) or {}
        if "nina_names" not in c and old_cam.get("nina_names"):
            cam["nina_names"] = list(old_cam["nina_names"])
        names_seen: set[str] = set()
        filters_out: dict = {}
        for fname, cfg in (c.get("filters") or {}).items():
            where = f"cameras[{i}].filters[{fname!r}]"
            tpls = cfg.get("templates") if isinstance(cfg, dict) else None
            if not isinstance(tpls, list) or not tpls:
                return jsonify({"error": f"{where} has no templates. A filter needs at least one."}), 400
            out_tpls = []
            for j, t in enumerate(tpls):
                twhere = f"{where}.templates[{j}]"
                err = gt.valid_template_fields(t, twhere)
                if err:
                    return jsonify({"error": err}), 400
                tid = t.get("id")
                if tid is None or tid == "":
                    tid = str(uuid.uuid4())
                elif not isinstance(tid, str) or len(tid) > 64:
                    return jsonify({"error": f"{twhere}.id must be a string of at most 64 characters"}), 400
                if tid in seen_ids:
                    return jsonify({"error": f"two templates share the id {tid!r}"}), 400
                seen_ids.add(tid)
                key = t["name"].lower()
                if key in names_seen:
                    return jsonify({"error": f"camera {cid!r} has two templates named {t['name']!r}"}), 400
                names_seen.add(key)
                was = stored_loc.get(tid)
                if was is not None and (was[0], was[1]) != (cid, fname):
                    return jsonify({"error": f"{twhere}: a template cannot move to another filter "
                                              "or camera. Remove it and add a new one."}), 400
                out_tpls.append(gt.clean_template({**t, "id": tid}, was[2] if was else None))
            gt.repair_stars(out_tpls)
            filters_out[fname] = {"templates": out_tpls}
        cam["filters"] = filters_out
        new_cams.append(cam)

    new_doc = {**base, "cameras": new_cams, "telescopes": payload["telescopes"]}
    new_loc = {t["id"]: (cam.get("id"), f, t) for cam, f, t in gt.all_templates(new_doc)}

    with PLANS_LOCK:
        plans = load_plans().get("plans") or []
        # Never remove what a plan row uses (a template, its filter or its camera).
        removed = set(stored_loc) - set(new_loc)
        in_use = _plans_using(removed, list(stored_cams.values()), plans)
        gone_cams = set(stored_cams) - seen_cams
        for p in plans:
            if isinstance(p, dict) and p.get("camera_id") in gone_cams and p.get("id") not in in_use:
                in_use[p["id"]] = {"camera": 1}
        if in_use:
            listing = ", ".join(f"{pid} ({sum(v.values())})" for pid, v in sorted(in_use.items()))
            total = sum(sum(v.values()) for v in in_use.values())
            return jsonify({"error": f"{total} plan rows use templates, filters or cameras this save would "
                                     f"remove. Plans (rows): {listing}",
                            "rows": total,
                            "plans": {pid: sum(v.values()) for pid, v in in_use.items()}}), 400

        ctx = _gear_camera_context(base)
        renames, subs = {}, {}
        for tid, (cid, fname, t) in new_loc.items():
            was = stored_loc.get(tid)
            if was is None:
                continue
            old = was[2]
            if old.get("name") != t["name"]:
                renames[tid] = t["name"]
            if not gt._same(old.get("sub_s"), t["sub_s"]):
                subs[tid] = (old["sub_s"], t["sub_s"])
        for tid in set(renames) | set(subs):
            cid = new_loc[tid][0]
            if cid in stored_cams and _camera_edit_blocked(base, ctx, stored_cams[cid]):
                return jsonify({"error": gt.PLUGIN_MESSAGE}), 400
        for tid, new_name in renames.items():
            cid, fname, t = new_loc[tid]
            hit = _ts_name_clash(base, ctx, stored_cams.get(cid) or {}, stored_loc[tid][2], new_name)
            if hit:
                pname = next((p["profile_name"] for p in ctx["linked"].get(cid, [])
                              if p["profile_id"] == hit[0]), hit[0])
                return jsonify({"error": f"TS template id {hit[1]} in profile {pname} ({hit[0]}) "
                                          f"is already named {new_name!r}"}), 400

        sub_changes = _carry_into_plans(renames, subs)
        rev = commit_gear(new_doc)
    return jsonify({"ok": True, "rev": rev, "sub_changes": sub_changes})


def _gear_post_old_page(payload: dict, base: dict):
    """POST /api/gear without `gear_schema`: a page loaded before templates
    shipped. Its filters are old scalars, and applying them would revert
    edits made on a newer page, so they are ignored. Telescopes, and each
    known camera's name and sensor, are applied; a camera it does not know
    is added with one default template per filter; none is deleted."""
    stored = {c.get("id"): c for c in base.get("cameras") or [] if isinstance(c, dict)}
    cameras = list(base.get("cameras") or [])
    ids = []
    for c in payload.get("cameras") or []:
        cid = c.get("id")
        if cid in stored:
            for key in ("name", "pixel_size_um", "sensor_px"):
                if key in c:
                    stored[cid][key] = copy.deepcopy(c[key])
            continue
        cam = {k: copy.deepcopy(v) for k, v in c.items() if k not in ("filters", *gt.DERIVED_CAMERA)}
        cam["filters"] = {}
        taken: set[str] = set()
        for fname, cfg in (c.get("filters") or {}).items():
            cfg = cfg if isinstance(cfg, dict) else {}
            sub = cfg.get("default_sub_s")
            name = gt._clip_name(f"{fname} ({cam.get('name')})")
            n = 2
            while name.lower() in taken:
                name = gt._clip_name(f"{fname} ({cam.get('name')}) {n}")
                n += 1
            taken.add(name.lower())
            gain, offset, bin_ = (gt._whole(cfg.get(k)) for k in ("gain", "offset", "bin"))
            cam["filters"][fname] = {"templates": [gt.new_template(
                name, sub if gt._is_num(sub) and 0 < sub <= gt.SUB_MAX_S else 300,
                gain if gain is not None and gain >= -1 else -1,
                offset if offset is not None and offset >= -1 else -1,
                bin_ if bin_ is not None and 1 <= bin_ <= 4 else 1, starred=True)]}
        cameras.append(cam)
        stored[cid] = cam
        ids.append(cid)
    new_doc = {**base, "telescopes": payload.get("telescopes", []), "cameras": cameras}
    rev = commit_gear(new_doc)
    logging.warning("gear saved by a page without templates; kept every stored filter and template "
                    "(added cameras: %s)", ", ".join(_safe_log(i) for i in ids) or "none")
    return jsonify({"ok": True, "rev": rev, "ignored": ["filters"]})


@app.route("/api/gear", methods=["GET", "POST"])
def api_gear():
    if request.method == "GET":
        return jsonify(_gear_response(load_gear()))
    payload = request.get_json(silent=True) or {}
    if "telescopes" not in payload or "cameras" not in payload:
        return jsonify({"error": "telescopes and cameras arrays required"}), 400
    err = _validate_gear_payload(payload)
    if err:
        return jsonify({"error": err}), 400
    with GEAR_LOCK:
        base = _gear_for_write()
        if not gt.is_migrated(base):
            # The migration could not take its backup, so the file is still
            # in the old shape and is written the old way.
            if "gear_schema" in payload:
                return jsonify({"error": "gear is waiting for its migration backup"}), 503
            out = {"version": 2, "telescopes": payload.get("telescopes", []),
                   "cameras": payload.get("cameras", [])}
            colours = base.get("telescope_colours")
            if colours:
                out["telescope_colours"] = colours
            save_gear(out)
            return jsonify({"ok": True})
        if "gear_schema" not in payload:
            return _gear_post_old_page(payload, base)
        if payload["gear_schema"] != gt.SCHEMA:
            return jsonify({"error": f"gear_schema must be {gt.SCHEMA}"}), 400
        return _gear_post_v3(payload, base)


def _find_camera(doc: dict, camera_id) -> dict | None:
    return next((c for c in doc.get("cameras") or [] if isinstance(c, dict) and c.get("id") == camera_id), None)


def _template_fields_from_body(body: dict, where: str = "body") -> tuple[dict | None, str | None]:
    t = {"name": body.get("name"), "sub_s": body.get("sub_s"), "gain": body.get("gain", -1),
         "offset": body.get("offset", -1), "bin": body.get("bin", 1)}
    err = gt.valid_template_fields(t, where)
    return (None, err) if err else (t, None)


@app.route("/api/gear/templates", methods=["POST"])
def api_gear_template_add():
    """Add one template to a camera's filter. Body: camera_id, filter, name,
    sub_s, gain, offset, bin. It is starred only when the filter has none."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "payload must be a JSON object"}), 400
    fields, err = _template_fields_from_body(body)
    if err:
        return jsonify({"error": err}), 400
    with GEAR_LOCK:
        doc = _gear_for_write()
        if not gt.is_migrated(doc):
            return jsonify({"error": "gear is waiting for its migration backup"}), 503
        cam = _find_camera(doc, body.get("camera_id"))
        if cam is None:
            return jsonify({"error": "no such camera"}), 404
        filt = body.get("filter")
        cfg = (cam.get("filters") or {}).get(filt) if isinstance(filt, str) else None
        if not isinstance(cfg, dict):
            return jsonify({"error": f"the camera has no filter {filt!r}"}), 400
        names = {t["name"].lower() for _c, _f, t in gt.all_templates({"cameras": [cam]})}
        if fields["name"].lower() in names:
            return jsonify({"error": f"camera {cam['id']!r} already has a template named {fields['name']!r}"}), 400
        tpls = cfg.setdefault("templates", [])
        tpl = gt.new_template(fields["name"], fields["sub_s"], gt._whole(fields["gain"]),
                              gt._whole(fields["offset"]), gt._whole(fields["bin"]),
                              starred=not tpls)
        tpls.append(tpl)
        rev = commit_gear(doc)
    return jsonify({"ok": True, "rev": rev, "template": tpl}), 201


@app.route("/api/gear/templates/<template_id>/star", methods=["POST"])
def api_gear_template_star(template_id: str):
    with GEAR_LOCK:
        doc = _gear_for_write()
        hit = gt.find_by_id(doc, template_id) if gt.is_migrated(doc) else None
        if hit is None:
            return jsonify({"error": "no such template"}), 404
        cam, fname, _t = hit
        for t in cam["filters"][fname]["templates"]:
            t["starred"] = t.get("id") == template_id
        rev = commit_gear(doc)
    return jsonify({"ok": True, "rev": rev})


@app.route("/api/gear/templates/<template_id>/resolve", methods=["POST"])
def api_gear_template_resolve(template_id: str):
    """Settle one held conflict. Body: profile_id, field, take ("acp" or "ts").

    "acp" sets the field's base to TS's value, so the next push writes
    ACP's value. "ts" sets the gear field and the base to TS's value, and a
    changed sub reaches the rows that follow the template.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "payload must be a JSON object"}), 400
    pid, field, take = body.get("profile_id"), body.get("field"), body.get("take")
    if field == "defaultexposure":
        field = "sub_s"
    if not isinstance(pid, str) or not pid:
        return jsonify({"error": "profile_id is required"}), 400
    if field not in gt.TEMPLATE_FIELDS:
        return jsonify({"error": f"field must be one of {', '.join(gt.TEMPLATE_FIELDS)}"}), 400
    if take not in ("acp", "ts"):
        return jsonify({"error": 'take must be "acp" or "ts"'}), 400
    with GEAR_LOCK, PLANS_LOCK:
        doc = _gear_for_write()
        hit = gt.find_by_id(doc, template_id) if gt.is_migrated(doc) else None
        if hit is None:
            return jsonify({"error": "no such template"}), 404
        _cam, _f, tpl = hit
        link = (tpl.get("ts") or {}).get(pid)
        if not isinstance(link, dict):
            return jsonify({"error": "the template has no link to that profile"}), 404
        known, row = gt._find_row(_load_ts_templates_store().get(pid), link)
        ts_value = row.get(gt.LIST_KEY[field]) if row else None
        if ts_value is None:
            return jsonify({"error": "TS's value is not known: the last template list has no such row"}), 409
        base = link.setdefault("base", {})
        renames, subs = {}, {}
        if take == "ts":
            if field == "name" and any(
                    t is not tpl and t["name"].lower() == str(ts_value).lower()
                    for _c, _ff, t in gt.all_templates({"cameras": [_cam]})):
                return jsonify({"error": f"the camera already has a template named {ts_value!r}"}), 400
            if field == "sub_s" and not gt._same(tpl.get("sub_s"), ts_value):
                subs[tpl["id"]] = (tpl["sub_s"], ts_value)
            if field == "name" and tpl.get("name") != ts_value:
                tpl["aliases"] = gt.push_alias(tpl.get("aliases") or [], tpl.get("name"), ts_value)
                renames[tpl["id"]] = ts_value
            tpl[field] = ts_value
        base[gt.BASE_KEY[field]] = ts_value
        sub_changes = _carry_into_plans(renames, subs)
        rev = commit_gear(doc)
    return jsonify({"ok": True, "rev": rev, "sub_changes": sub_changes})


@app.route("/api/gear/cameras/<camera_id>/nina-names", methods=["POST"])
def api_gear_camera_nina_names(camera_id: str):
    """Link a NINA camera name to a gear camera. Body: {"name": str}."""
    body = request.get_json(silent=True)
    name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return jsonify({"error": "name must be a non-empty string of at most 200 characters"}), 400
    name = name.strip()
    with GEAR_LOCK:
        doc = _gear_for_write()
        cam = _find_camera(doc, camera_id) if gt.is_migrated(doc) else None
        if cam is None:
            return jsonify({"error": "no such camera"}), 404
        names = cam.setdefault("nina_names", [])
        if name in names:
            return jsonify({"ok": True, "rev": doc.get("rev"), "nina_names": names})
        if len(names) >= 20:
            return jsonify({"error": "too many linked NINA names"}), 400
        names.append(name)
        rev = commit_gear(doc)
    return jsonify({"ok": True, "rev": rev, "nina_names": names})


def _ts_plan_id(plan: dict, profile_id: str, row_id) -> int | None:
    """The exposure plan id ACP pinned for a row on a profile."""
    links = plan.get("ts_links") if isinstance(plan.get("ts_links"), dict) else {}
    refs = (links.get(profile_id) or {}).get("refs")
    if not isinstance(refs, dict) and isinstance((plan.get("ts_refs") or {}).get("profile_id"), str) \
            and plan["ts_refs"]["profile_id"] == profile_id:
        refs = plan["ts_refs"]
    ids = (refs or {}).get("exposure_plan_ids") if isinstance(refs, dict) else None
    for key, ep in (ids or {}).items() if isinstance(ids, dict) else []:
        if str(key).endswith("_" + str(row_id)):
            return ep
    return None


def apply_ts_lists() -> list[str]:
    """Adoption and the pull for every profile that has posted a template
    list (docs/specs/gear-templates.md sections 8 and 11). Runs when a list
    arrives, when a fingerprint arrives and when a plugin becomes capable,
    so the order of those events does not matter. Idempotent: a repeat
    changes nothing and leaves `rev` alone. Returns the sync notes."""
    notes: list[str] = []
    with GEAR_LOCK:
        doc = _gear_for_write()
        if not gt.is_migrated(doc):
            return notes
        lists = _load_ts_templates_store()
        if not lists:
            return notes
        ctx = _gear_camera_context(doc)
        primaries = {ps[0]["profile_id"] for ps in ctx["linked"].values() if ps}
        now = datetime.now(timezone.utc).isoformat()
        changed, subs, renames = False, {}, {}
        exposures: dict[tuple, float] = {}
        for pid in sorted(lists, key=lambda p: (p not in primaries, p)):
            rows = (lists[pid] or {}).get("templates") or []
            for r in rows:
                for pl in r.get("plans") or []:
                    exposures[(pid, pl.get("id"))] = pl.get("exposure")
            if gt._is_capable(doc.get("links_capable", {}).get(pid)):
                ch, n = gt.adopt(doc, pid, ctx["linked"], rows, now)
                changed, notes = changed or ch, notes + n
            ch, n, sb, rn = gt.pull(doc, pid, ctx["linked"], rows)
            changed, notes = changed or ch, notes + n
            subs.update({k: (v, pid) for k, v in sb.items()})
            renames.update(rn)
        if not changed:
            return notes
        with PLANS_LOCK:
            # Only a row whose TS plan follows the template default (-1)
            # takes a sub changed in TS; a plan with its own exposure keeps it.
            by_tid = {tid: pid for tid, (_old_new, pid) in subs.items()}

            def follows_default(plan, row):
                pid = by_tid.get(row.get("template_id"))
                ep = _ts_plan_id(plan, pid, row.get("id")) if pid else None
                return ep is not None and exposures.get((pid, ep)) == -1
            _carry_into_plans(renames, {tid: v[0] for tid, v in subs.items()}, follows_default)
            commit_gear(doc)
            _repair_row_templates()
    return notes


def set_links_capable(profile_id: str, capabilities) -> bool:
    """Record what the plugin on *profile_id* says it can do, from a links
    post (the sync's route calls this). A post that lists both "templates"
    and "repoint" sets the entry. Any other post, including one with no
    `capabilities`, deletes it, so a downgraded plugin loses the capability
    at once. Returns whether gear changed."""
    if not isinstance(profile_id, str) or not profile_id:
        return False
    caps = {c for c in capabilities if isinstance(c, str)} if isinstance(capabilities, (list, tuple)) else set()
    capable = {"templates", "repoint"} <= caps
    with GEAR_LOCK:
        doc = _gear_for_write()
        if not gt.is_migrated(doc):
            return False
        table = doc.setdefault("links_capable", {})
        if capable:
            if gt._is_capable(table.get(profile_id)):
                return False
            table[profile_id] = {"templates": True, "repoint": True,
                                 "at": datetime.now(timezone.utc).isoformat()}
        elif profile_id in table:
            del table[profile_id]
        else:
            return False
        commit_gear(doc)
    if capable:
        _apply_ts_lists_safely()
    return True


def _apply_ts_lists_safely() -> list[str]:
    try:
        return apply_ts_lists()
    except Exception:
        logging.exception("applying the TS template lists to gear failed")
        return []


def record_template_results(profile_id: str, results) -> int:
    """Store the writer's per-template results into `ts[profile_id]` (spec
    section 4; the sync's sync and links routes both call this). Returns the
    number of templates whose link changed. `rev` moves only when one did.

    The actions are inserted, updated, unchanged, held (or conflict),
    skipped and adopted. A skipped template records nothing. An adopted one
    took TS's values, which gear takes only for the camera's primary profile;
    for any other the link is recorded with the gear values as its base.
    """
    if not isinstance(profile_id, str) or not profile_id or not isinstance(results, list):
        return 0
    with GEAR_LOCK:
        doc = _gear_for_write()
        if not gt.is_migrated(doc):
            return 0
        ctx = _gear_camera_context(doc)
        changed = 0
        for res in results:
            if not isinstance(res, dict) or res.get("action") == "skipped":
                continue
            hit = gt.find_by_id(doc, res.get("template_id"))
            if hit is None or res.get("ts_id") is None:
                continue
            cam, _fname, tpl = hit
            linked = ctx["linked"].get(cam.get("id"), [])
            primary = linked[0]["profile_id"] if linked else None
            old = (tpl.get("ts") or {}).get(profile_id) or {}
            ts_base = copy.deepcopy(res.get("base")) if isinstance(res.get("base"), dict) else {}
            if res.get("action") == "adopted":
                if primary in (None, profile_id):
                    for field in gt.TEMPLATE_FIELDS:
                        v = ts_base.get(gt.BASE_KEY[field])
                        if v is not None:
                            tpl[field] = v
                else:
                    for field in gt.TEMPLATE_FIELDS:
                        ts_base[gt.BASE_KEY[field]] = tpl.get(field)
            link = {"id": res["ts_id"], "guid": res.get("guid"), "base": ts_base,
                    "repoint_from": list(old.get("repoint_from") or []),
                    "pushed_iso": old.get("pushed_iso")}
            if {k: v for k, v in link.items() if k != "pushed_iso"} == \
                    {k: v for k, v in old.items() if k != "pushed_iso"}:
                continue
            link["pushed_iso"] = datetime.now(timezone.utc).isoformat()
            tpl.setdefault("ts", {})[profile_id] = link
            changed += 1
        if changed:
            commit_gear(doc)
    return changed


def clear_repoint(profile_id: str) -> bool:
    """A push finished its plan moves: empty every `repoint_from` list of
    *profile_id* (spec section 10, `repoint_done`)."""
    with GEAR_LOCK:
        doc = _gear_for_write()
        if not gt.is_migrated(doc):
            return False
        touched = False
        for _c, _f, tpl in gt.all_templates(doc):
            link = (tpl.get("ts") or {}).get(profile_id)
            if isinstance(link, dict) and link.get("repoint_from"):
                link["repoint_from"] = []
                touched = True
        if touched:
            commit_gear(doc)
    return touched


# A colour the user picked for a telescope, keyed by the telescope name shown next
# to the swatch. Only #rrggbb gets in, because the value is written into a
# style attribute on the page.
_TELESCOPE_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_TELESCOPE_COLOURS_MAX = 500


@app.route("/api/gear/telescope-colour", methods=["POST"])
def api_gear_telescope_colour():
    """Set or clear one telescope's colour. Body: {"name": str, "colour": "#rrggbb" | null}.

    A null colour removes the choice, so the telescope goes back to its
    automatic colour. Returns the full colour map and, once gear is in the
    version 3 shape, the new `rev` (the write bumps it, and a page holding
    the old one would otherwise get a 409 on its next save).
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "payload must be a JSON object"}), 400
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return jsonify({"error": "name must be a non-empty string of at most 200 characters"}), 400
    colour = payload.get("colour")
    if colour is not None and (not isinstance(colour, str) or not _TELESCOPE_COLOUR_RE.match(colour)):
        return jsonify({"error": "colour must be #rrggbb or null"}), 400
    with GEAR_LOCK:
        gear = _gear_for_write() if GEAR_PATH.exists() else {"version": 2, "telescopes": [], "cameras": []}
        colours = dict(gear.get("telescope_colours") or {})
        if colour is None:
            colours.pop(name, None)
        else:
            if name not in colours and len(colours) >= _TELESCOPE_COLOURS_MAX:
                return jsonify({"error": "too many telescope colours"}), 400
            colours[name] = colour.lower()
        gear["telescope_colours"] = colours
        rev = None
        if gt.is_migrated(gear):
            rev = commit_gear(gear)
        else:
            save_gear(gear)
    out = {"ok": True, "telescope_colours": colours}
    if rev is not None:
        out["rev"] = rev
    return jsonify(out)


def _validate_plan_payload(payload: dict) -> str | None:
    """Defence-in-depth check on a plan dict before it lands in plans.json.

    The UI validates per-field on entry but a misbehaving client (or a
    direct curl) can still POST garbage. This catches the cases that
    would silently break downstream sync to NINA TS:
      - centre RA is normalised into [0, 360) rather than rejected (Aladin's
        seam-drag and the manual field can legitimately hand back small
        negatives or exactly 360.0); Dec outside [-90, 90] is still rejected
      - NaN / Infinity anywhere a number is expected: comparisons like
        `x < 0` are silently False for NaN, so every numeric field is
        checked with math.isfinite() rather than relying on range
        comparisons alone. A non-finite value here would otherwise
        write straight to plans.json and 500 every later /api/sync
        call via int(math.ceil(nan)).
      - mosaic rows/cols not positive integers (sync expansion would
        crash or emit zero panels)
      - mosaic overlap_pct outside [0, 99]
      - filter_rows that break the rules in section 3 of
        docs/specs/goal-panel-and-ts-order.md (see filter_rows.validate_rows)

    All sub-checks are optional: a draft plan with no target / filters
    is fine. Returns None on success, an error string otherwise.
    """
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    if not payload.get("id") or not isinstance(payload["id"], str):
        return "id required (non-empty string)"
    target = payload.get("target")
    if target is not None:
        if not isinstance(target, dict):
            return "target must be an object"
        if "center_ra_deg" in target:
            try:
                ra = float(target["center_ra_deg"])
            except (TypeError, ValueError):
                return "target.center_ra_deg must be a number"
            if not math.isfinite(ra):
                return "target.center_ra_deg must be finite"
            # Normalise rather than reject: seam-drag in Aladin and manual
            # entry both legitimately produce -0.5 or exactly 360.0.
            target["center_ra_deg"] = ra % 360.0
        if "center_dec_deg" in target:
            try:
                dec = float(target["center_dec_deg"])
            except (TypeError, ValueError):
                return "target.center_dec_deg must be a number"
            if not math.isfinite(dec) or not (-90.0 <= dec <= 90.0):
                return "target.center_dec_deg out of range [-90, 90]"
        if "rotation_deg" in target:
            try:
                rot = float(target["rotation_deg"])
            except (TypeError, ValueError):
                return "target.rotation_deg must be a number"
            if not math.isfinite(rot):
                return "target.rotation_deg must be finite"
        mosaic = target.get("mosaic")
        if mosaic is not None:
            if not isinstance(mosaic, dict):
                return "target.mosaic must be an object"
            for dim in ("rows", "cols"):
                if dim in mosaic:
                    v = mosaic[dim]
                    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                        return f"target.mosaic.{dim} must be a positive integer"
            if "overlap_pct" in mosaic:
                try:
                    op = float(mosaic["overlap_pct"])
                except (TypeError, ValueError):
                    return "target.mosaic.overlap_pct must be a number"
                if not math.isfinite(op) or not (0.0 <= op <= 99.0):
                    return "target.mosaic.overlap_pct out of range [0, 99]"
    # filter_goals is the old shape. GET /api/plans still serves a derived
    # copy to older plugins, so a client may echo it back; it is never
    # stored (docs/specs/goal-panel-and-ts-order.md section 3).
    payload.pop("filter_goals", None)
    if "filter_rows" in payload:
        gear = load_gear()
        cameras = {c.get("id"): c for c in (gear.get("cameras") or []) if isinstance(c, dict)}
        err = fr.validate_rows(payload["filter_rows"], cameras.get(payload.get("camera_id") or ""),
                               payload.get("id"), gt.is_migrated(gear))
        if err:
            return err
    # What AAC reads (section 6). Absent means survey.
    tclass = payload.get("target_class")
    if tclass is not None and tclass not in fr.TARGET_CLASSES:
        return f"target_class must be one of {', '.join(fr.TARGET_CLASSES)}"
    # TS project state (docs/specs/ts-project-settings.md section 1). Absent
    # means "active". "parked" is a held PR #97 value that never shipped;
    # normalise it to "inactive" (TS's meaning for a plan paused by hand)
    # rather than writing a fifth, unrecognised state.
    state = payload.get("state")
    if state == "parked":
        state = payload["state"] = "inactive"
    if state is not None and state not in PLAN_STATES:
        return f"state must be one of {', '.join(PLAN_STATES)}"
    # Minimum time, in minutes, the same TS-owned rule as min altitude:
    # absent means 0, and a group's largest value wins (_build_ts_export).
    if "minimum_time_min" in payload:
        mt = payload["minimum_time_min"]
        if isinstance(mt, bool) or not isinstance(mt, int):
            return "minimum_time_min must be an integer"
        if mt < 0:
            return "minimum_time_min must be ≥ 0"
    # The TS project's shooting-order settings
    # (docs/specs/goal-panel-and-ts-order.md section 4). Absent means
    # unknown, so neither is required.
    for key in ORDER_SETTING_KEYS:
        if key in payload:
            err = _order_setting_error(key, payload[key])
            if err:
                return err
    # Live-page fields (docs/specs/shooting-page.md). Absent means private,
    # so anything other than the two known values is rejected, never coerced.
    vis = payload.get("visibility")
    if vis is not None and vis not in ("private", "public"):
        return "visibility must be 'private' or 'public'"
    cur = payload.get("is_current")
    if cur is not None and not isinstance(cur, bool):
        return "is_current must be a boolean"
    blurb = payload.get("public_blurb")
    if blurb is not None:
        if not isinstance(blurb, str):
            return "public_blurb must be a string"
        if len(blurb) > 500:
            return "public_blurb must be 500 characters or fewer"
    section = payload.get("public_section")
    if section is not None and section not in ("current", "vote"):
        return "public_section must be 'current' or 'vote'"
    finished = payload.get("finished_at")
    if finished not in (None, ""):
        if not isinstance(finished, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", finished):
            return "finished_at must be a date as YYYY-MM-DD"
        try:
            datetime.strptime(finished, "%Y-%m-%d")
        except ValueError:
            return "finished_at must be a real calendar date"
    astrobin = payload.get("astrobin_url")
    if astrobin not in (None, ""):
        import shooting_publish as sp
        if not sp.valid_astrobin_url(astrobin):
            return "astrobin_url must be an https link on astrobin.com"
    role = payload.get("balance_role")
    if role not in (None, "") and role not in balancer.ROLES:
        return "balance_role must be one of " + ", ".join(balancer.ROLES)
    hold = payload.get("balance_hold")
    if hold is not None and not isinstance(hold, bool):
        return "balance_hold must be true or false"
    for key in ("vote_goals", "vote_baseline"):
        mapping = payload.get(key)
        if mapping is None:
            continue
        if not isinstance(mapping, dict):
            return f"{key} must be an object of filter name to hours"
        for fname, hours in mapping.items():
            if not isinstance(fname, str) or not fname.strip():
                return f"{key} filter names must be non-empty strings"
            if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours):
                return f"{key} hours must be numbers"
            if key == "vote_goals" and hours <= 0:
                return "vote_goals hours must be greater than 0"
            if key == "vote_baseline" and hours < 0:
                return "vote_baseline hours must be 0 or more"
    return None


def _set_vote_baseline(payload: dict, existing: dict | None) -> None:
    """The vote baseline is the server's: the hours the plan already had when
    it entered the vote, so the public page counts only what is added after.
    It is never taken from the client. Set on entering the vote section or on
    first setting goals, topped up for any goal filter without one, cleared
    when the plan goes back to the current section."""
    if payload.get("public_section") != "vote":
        payload.pop("vote_baseline", None)
        return
    import shooting_publish as sp
    was_vote = bool(existing) and existing.get("public_section") == "vote"
    kept = dict(existing.get("vote_baseline") or {}) if was_vote else {}
    if not was_vote or "vote_baseline" not in (existing or {}):
        kept = sp.current_done_hours(payload, load_manifest(), load_gear())
    else:
        have = {sp.canon_filter(k) or k for k in kept}
        missing = [f for f in (payload.get("vote_goals") or {}) if (sp.canon_filter(f) or f) not in have]
        if missing:
            now = sp.current_done_hours(payload, load_manifest(), load_gear())
            for f in missing:
                key = sp.canon_filter(f) or f
                kept[key] = now.get(key, 0.0)
    payload["vote_baseline"] = kept


_PLAN_EXPAND_TOKENS = frozenset({"gear", "site", "panels"})


def _plan_panels(plan: dict, gear_data: dict) -> list[dict]:
    """Mosaic panel centers for one plan, using the same geometry
    /api/sync expands a mosaic with. Collapses to a single "panel" when
    the plan isn't a mosaic, or when gear doesn't resolve to a FOV."""
    tg = plan.get("target") or {}
    ra_deg = float(tg.get("center_ra_deg") or 0) % 360.0
    dec_deg = float(tg.get("center_dec_deg") or 0)
    rot_deg = float(tg.get("rotation_deg") or 0)
    telescopes_by_id = {t["id"]: t for t in gear_data.get("telescopes", [])}
    cameras_by_id = {c["id"]: c for c in gear_data.get("cameras", [])}
    telescope = telescopes_by_id.get(plan.get("telescope_id") or "")
    camera = cameras_by_id.get(plan.get("camera_id") or "")
    fov_w, fov_h = _fov_arcmin(telescope, camera)
    mosaic = tg.get("mosaic") or {"rows": 1, "cols": 1, "overlap_pct": 0}
    rows = max(1, int(mosaic.get("rows") or 1))
    cols = max(1, int(mosaic.get("cols") or 1))
    overlap = float(mosaic.get("overlap_pct") or 0)
    return _mosaic_panel_centers(ra_deg, dec_deg, fov_w, fov_h, rot_deg, rows, cols, overlap)


def _expand_plan(plan: dict, tokens: frozenset[str], gear_data: dict | None, sites_data: dict | None) -> dict:
    """Add inline `gear` (expand=gear), `site` (expand=site) and `panels`
    (expand=panels) to a shallow copy of `plan`.

    The plugin has no way to resolve `telescope_id` / `camera_id` without a
    second round trip to /api/gear, and it needs the mosaic panel centres to
    drive the Framing Wizard, so both are offered inline. `gear` carries the
    resolved telescope and camera plus the pair's field of view and pixel
    scale. Unresolvable ids come back as null inside `gear`; an absent token
    leaves the plan byte for byte as /api/plans always returned it."""
    out = dict(plan)
    if "gear" in tokens and gear_data is not None:
        telescopes_by_id = {t["id"]: t for t in gear_data.get("telescopes", []) if t.get("id")}
        cameras_by_id = {c["id"]: c for c in gear_data.get("cameras", []) if c.get("id")}
        telescope = telescopes_by_id.get(plan.get("telescope_id") or "")
        camera = cameras_by_id.get(plan.get("camera_id") or "")
        fov_w, fov_h = _fov_arcmin(telescope, camera)
        scale = _plan_pixel_scale(telescope, camera)
        out["gear"] = {
            "telescope": telescope,
            "camera": _with_sensor_scalars(camera) if camera else None,
            "fov_arcmin": [fov_w, fov_h],
            "pixel_scale_arcsec": round(scale, 4) if scale else None,
        }
    if "site" in tokens and sites_data is not None:
        sites = sites_data.get("sites") or []
        if sites:
            # Plans do not carry a site today, so the first site is the
            # observing site. A plan that does name one wins if it exists.
            wanted = plan.get("site_id")
            out["site"] = next((s for s in sites if wanted and s.get("id") == wanted), sites[0])
    if "panels" in tokens and gear_data is not None:
        out["panels"] = _plan_panels(plan, gear_data)
    return out


def _commitment_progress(plan: dict) -> list[dict]:
    """Where each of a plan's current vote commitments stands, counted as the
    balancer counts it (`current_done_hours`, canonical filter names). For the
    editor's commitment line; never stored."""
    commitments = plan.get("commitments")
    if not isinstance(commitments, list) or not commitments:
        return []
    import shooting_publish as sp
    night = activity.current_night()
    settings = balancer.load_state(_balancer_path())["settings"]
    day = datetime.fromisoformat(night)
    dim = balancer.calendar.monthrange(day.year, day.month)[1]
    elapsed = (day.day - 1) / dim
    done = sp.current_done_hours(plan, load_manifest(), load_gear())
    out = []
    for c in commitments:
        if not isinstance(c, dict) or not isinstance(c.get("month"), str):
            continue
        j = balancer._judge(plan, c, done, sp.canon_filter, night[:7], elapsed,
                            settings["finish_by"], {})
        if j["skipped"]:
            continue
        entry = {"month": c["month"], "done_h": round(j["done_h"], 3),
                 "goal_h": round(j["goal_h"], 3), "met": bool(j["met"]),
                 "behind": bool(j["behind"])}
        if c.get("lapsed_at"):
            entry.update({"lapsed_at": c["lapsed_at"], "lapsed_reason": c.get("lapsed_reason")})
        out.append(entry)
    return out


def _with_goals_view(plan):
    """A copy of *plan* carrying the derived `filter_goals` older plugins
    read (filter_rows.filter_goals_view). Never stored."""
    if not isinstance(plan, dict):
        return plan
    # A plan with no target_class reads as survey (section 6), for AAC.
    plan = {**plan, "target_class": fr.target_class_of(plan)}
    if plan.get("commitments"):
        plan["commitment_progress"] = _commitment_progress(plan)
    if not isinstance(plan.get("filter_rows"), list):
        # Not migrated yet (the backup failed): its own filter_goals stand.
        return plan
    return {**plan, "filter_goals": fr.filter_goals_view(plan)}


@app.route("/api/plans", methods=["GET", "POST"])
@_holds_plans_lock
def api_plans():
    data = load_plans()
    if request.method == "GET":
        tokens = _PLAN_EXPAND_TOKENS.intersection(
            t.strip() for t in (request.args.get("expand") or "").split(",")
        )
        if tokens:
            gear_data = load_gear() if ("gear" in tokens or "panels" in tokens) else None
            sites_data = load_sites() if "site" in tokens else None
            resp = jsonify({
                "version": data.get("version", 1),
                "plans": [_with_goals_view(_expand_plan(p, tokens, gear_data, sites_data))
                          for p in data.get("plans", [])],
            })
        else:
            resp = jsonify({**data, "plans": [_with_goals_view(p) for p in data.get("plans", [])]})
        if PLANS_PATH.exists():
            resp.last_modified = datetime.fromtimestamp(PLANS_PATH.stat().st_mtime, tz=timezone.utc)
        return resp
    payload = request.get_json(silent=True) or {}
    err = _validate_plan_payload(payload)
    if err:
        return jsonify({"error": err}), 400
    now = datetime.now(timezone.utc).isoformat()
    payload.setdefault("guid", str(uuid.uuid4()))
    payload.setdefault("created_at", now)
    payload.setdefault("last_synced_at", None)
    payload["updated_at"] = now
    previous = next((p for p in data.get("plans", []) if p.get("id") == payload["id"]), None)
    before = copy.deepcopy(previous) if previous is not None and activity.enabled() else None
    err = _keep_balancer_fields(payload, previous)
    if err:
        return jsonify({"error": err}), 400
    fr.keep_aac(payload, previous)
    fr.sync_actuals(payload)
    _set_vote_baseline(payload, previous)
    plans = [p for p in data.get("plans", []) if p.get("id") != payload["id"]]
    plans.append(payload)
    save_plans({**data, "version": data.get("version", 1), "plans": plans})
    if activity.enabled():
        after = copy.deepcopy(payload)
        if previous is None:
            _activity_later(lambda: _record_plan_created(after))
        else:
            _activity_later(lambda: _record_plan_edit(before, after))
    return jsonify(payload), 201


_OLD_PAGE_KEEPS = ("filter_rows", "ts_links", "ts_refs", "ts_base_snapshot")


def _keep_rows_from_old_page(payload: dict, existing: dict) -> None:
    """A save with no `filter_rows` comes from a page loaded before the goal
    panel shipped. It only knows `filter_goals`, which is never stored, and
    it echoes the TS link keys from before the rows migration. Storing it as
    sent wipes every filter on the plan and points the next push at plans
    that no longer exist, so keep what is stored for those fields instead.
    """
    if "filter_rows" in payload or not isinstance(existing.get("filter_rows"), list):
        return
    for key in _OLD_PAGE_KEEPS:
        if key in existing:
            payload[key] = copy.deepcopy(existing[key])
        else:
            payload.pop(key, None)
    logging.warning(
        "plan %s saved without filter rows (a page loaded before the goal panel?); kept the stored rows",
        _safe_log(payload.get("id")),
    )


def _keep_balancer_fields(payload: dict, existing: dict | None) -> str | None:
    """The balancer's fields on a plan saved from the editor
    (docs/specs/imaging-balancer.md section 1, Routes). `commitments` and
    `balancer_note` are never taken from the client. `balance_role` and
    `balance_hold` keep the stored value when the payload leaves them out.
    Returns an error text when the payload would give a plan with a
    commitment a role other than pretty."""
    existing = existing or {}
    payload.pop("commitment_progress", None)  # derived for the editor, never stored
    for key in ("commitments", "balancer_note"):
        if key in existing:
            payload[key] = copy.deepcopy(existing[key])
        else:
            payload.pop(key, None)
    for key in ("balance_role", "balance_hold"):
        if key not in payload and key in existing:
            payload[key] = existing[key]
    if payload.get("commitments") and payload.get("balance_role") not in (None, "pretty"):
        return "a plan with a vote commitment must have balancer role pretty"
    if payload.get("commitments") and not payload.get("balance_role"):
        payload["balance_role"] = "pretty"
    return None


@app.route("/api/plans/<plan_id>", methods=["GET", "PUT", "DELETE"])
@_holds_plans_lock
def api_plan(plan_id: str):
    data = load_plans()
    plans = data.get("plans", [])
    idx = next((i for i, p in enumerate(plans) if p.get("id") == plan_id), None)
    if idx is None:
        return jsonify({"error": "not found"}), 404
    if request.method == "GET":
        return jsonify(_with_goals_view(plans[idx]))
    if request.method == "DELETE":
        gone = plans.pop(idx)
        save_plans({**data, "version": data.get("version", 1), "plans": plans})
        if activity.enabled():
            gone = copy.deepcopy(gone)
            _activity_later(lambda: _record_plan_deleted(gone))
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    payload["id"] = plan_id  # URL is authoritative; validator runs after
    err = _validate_plan_payload(payload)
    if err:
        return jsonify({"error": err}), 400
    existing = plans[idx]
    # What the plan was as loaded (already healed), for the Activity diff.
    before = copy.deepcopy(existing) if activity.enabled() else None
    payload["guid"] = existing.get("guid") or str(uuid.uuid4())
    payload["created_at"] = existing.get("created_at") or datetime.now(timezone.utc).isoformat()
    payload.setdefault("last_synced_at", existing.get("last_synced_at"))
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _keep_rows_from_old_page(payload, existing)
    err = _keep_balancer_fields(payload, existing)
    if err:
        return jsonify({"error": err}), 400
    fr.keep_aac(payload, existing)
    fr.sync_actuals(payload)
    _set_vote_baseline(payload, existing)
    plans[idx] = payload
    save_plans({**data, "version": data.get("version", 1), "plans": plans})
    if before is not None:
        after = copy.deepcopy(payload)
        _activity_later(lambda: _record_plan_edit(before, after))
    return jsonify(payload)


def _record_plan_created(plan: dict) -> None:
    activity.record(
        type="plan_created", key=f"plan_created:{plan.get('id')}:{activity.current_night()}",
        title=f"Plan created: {activity.plan_name(plan)}", refs=activity.plan_refs(plan))


def _record_plan_deleted(plan: dict) -> None:
    activity.record(
        type="plan_deleted", key=f"plan_deleted:{plan.get('id')}:{activity.current_night()}",
        title=f"Plan deleted: {activity.plan_name(plan)}", refs=activity.plan_refs(plan))


def _record_plan_edit(before: dict, after: dict) -> None:
    """One ``plan_edited`` per plan per night. ``before`` is the plan as it was
    loaded and healed, ``after`` the plan as stored, so a heal or a migration
    is never mistaken for an edit."""
    changes = activity.diff_plans(before, after)
    night = activity.current_night()
    activity.record_changes(
        etype="plan_edited", key=f"plan_edited:{after.get('id')}:{night}", night=night,
        changes=changes, refs=activity.plan_refs(after))


# The TS project's filter switch frequency and smart exposure order
# (docs/specs/goal-panel-and-ts-order.md section 4). "Finish each" stores a
# switch frequency of 0, "Rotate every N" stores N from 1 to 99. Absent on a
# plan means unknown: the sync never writes it on update.
ORDER_SETTING_KEYS = ("filter_switch_frequency", "smart_exposure_order")
SWITCH_FREQUENCY_MAX = 99


def _order_setting_error(key: str, value) -> str | None:
    if key == "smart_exposure_order":
        return None if isinstance(value, bool) else "smart_exposure_order must be true or false"
    if isinstance(value, bool) or not isinstance(value, int):
        return "filter_switch_frequency must be an integer"
    if not 0 <= value <= SWITCH_FREQUENCY_MAX:
        return f"filter_switch_frequency must be between 0 and {SWITCH_FREQUENCY_MAX}"
    return None


@app.route("/api/projects/<project_name>/settings", methods=["PUT"])
@_holds_plans_lock
def api_project_settings(project_name: str):
    """Save state, priority, minimum_time_min, filter_switch_frequency and
    smart_exposure_order across every plan that shares this project_name,
    in one write to plans.json. The last two are the TS project's
    shooting-order settings (docs/specs/goal-panel-and-ts-order.md
    section 4).

    ACP has no project entity: a TS project is the group of plans that
    share `project_name`, the same grouping `_build_ts_export` uses. The
    editor calls this route instead of saving each plan separately so a
    change to one of these three fields can never land on some of a
    project's plans and not others. See docs/specs/ts-project-settings.md
    section 1. Any subset of the body keys can be given; a key left out is
    left unchanged on every plan in the group.
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "payload must be a JSON object"}), 400

    state = payload.get("state")
    if state == "parked":
        state = "inactive"
    if state is not None and state not in PLAN_STATES:
        return jsonify({"error": f"state must be one of {', '.join(PLAN_STATES)}"}), 400

    priority = payload.get("priority")
    if priority is not None and priority not in PRIORITY_RANK:
        return jsonify({"error": f"priority must be one of {', '.join(PRIORITY_RANK)}"}), 400

    minimum_time_min = None
    has_minimum_time = "minimum_time_min" in payload
    if has_minimum_time:
        minimum_time_min = payload["minimum_time_min"]
        if isinstance(minimum_time_min, bool) or not isinstance(minimum_time_min, int):
            return jsonify({"error": "minimum_time_min must be an integer"}), 400
        if minimum_time_min < 0:
            return jsonify({"error": "minimum_time_min must be ≥ 0"}), 400

    order_settings = {}
    for key in ORDER_SETTING_KEYS:
        if key in payload:
            err = _order_setting_error(key, payload[key])
            if err:
                return jsonify({"error": err}), 400
            order_settings[key] = payload[key]

    has_role = "balance_role" in payload
    role = payload.get("balance_role") or None
    if has_role and role is not None and role not in balancer.ROLES:
        return jsonify({"error": f"balance_role must be one of {', '.join(balancer.ROLES)}"}), 400
    has_hold = "balance_hold" in payload
    hold = payload.get("balance_hold")
    if has_hold and not isinstance(hold, bool):
        return jsonify({"error": "balance_hold must be true or false"}), 400

    data = load_plans()
    plans = data.get("plans", [])
    group = [p for p in plans if isinstance(p, dict) and balancer.project_key(p) == project_name]
    if not group:
        return jsonify({"error": f"no plans in project {project_name!r}"}), 404

    wanted = dict(order_settings)
    if state is not None:
        wanted["state"] = state
    if priority is not None:
        wanted["priority"] = priority
    if has_minimum_time:
        wanted["minimum_time_min"] = minimum_time_min
    if has_role:
        if role != "pretty" and any(p.get("commitments") for p in group):
            return jsonify({"error": "a project with a vote commitment must have balancer role pretty"}), 400
        wanted["balance_role"] = role
    if has_hold:
        wanted["balance_hold"] = hold
    now = datetime.now(timezone.utc).isoformat()
    changes = apply_project_settings(plans, project_name, wanted, now)
    save_plans({**data, "version": data.get("version", 1), "plans": plans})
    if changes:
        night = activity.current_night()
        _activity_later(lambda: activity.record_changes(
            etype="project_settings", key=f"project_settings:{project_name}:{night}", night=night,
            changes=changes, prefix=f"{project_name}: ", refs={"project": project_name},
            extra_data={"plans": len(group)}))
    return jsonify({
        "ok": True,
        "project_name": project_name,
        "plan_count": len(group),
        "state": state,
        "priority": priority,
        "minimum_time_min": minimum_time_min if has_minimum_time else None,
        **{key: order_settings.get(key) for key in ORDER_SETTING_KEYS},
        "balance_role": role if has_role else None,
        "balance_hold": hold if has_hold else None,
    })


def apply_project_settings(plans: list[dict], project_name: str, wanted: dict, now: str) -> dict:
    """Set every key in `wanted` on every plan in the project (`balancer.project_key`),
    stamp updated_at, and return {key: {"from": ..., "to": ...}} for the
    keys that changed. Mutates `plans` in place. Does not load, save or
    record anything."""
    group = [p for p in plans if isinstance(p, dict) and balancer.project_key(p) == project_name]
    changes: dict = {}
    for p in group:
        for k, v in wanted.items():
            if k not in changes and not activity.same_value(p.get(k), v):
                changes[k] = {"from": p.get(k), "to": v}
    for p in group:
        p.update(copy.deepcopy(wanted))
        p["updated_at"] = now
    return changes


# --- NINA plugin v3: gear fingerprint matching --------------------------
#
# The companion NINA plugin posts a "fingerprint" of whatever gear is
# connected right now (camera, filter wheel, mount, site, solved focal
# length) and asks which of your plans that rig can actually shoot. The
# matching rules live here rather than in the plugin so both sides can't
# drift, and so the same verdicts show up in ACP's own UI.
#
# Three tests, in order of how often they matter:
#   1. pixel scale within PIXEL_SCALE_TOL of the plan's own pixel scale
#   2. field of view at least FOV_FIT of the plan's field in BOTH axes
#      (bigger is always fine; between FOV_WARN and FOV_FIT is a warning;
#      below FOV_WARN is a miss)
#   3. every filter the plan has a goal for is reachable from the
#      connected filter list, using the scanner's own canonicalisation so
#      "Antlia Ha" and "Ha" are the same filter here and in the manifest
#
# All verdicts are returned. The plugin decides whether to show only the
# fits or everything; `mode` is echoed back and stored with the
# fingerprint, it never changes a verdict.

PIXEL_SCALE_TOL = 0.15
FOV_FIT = 0.90
FOV_WARN = 0.80
ARCSEC_PER_RAD_MM = 206.265  # arcsec/px = 206.265 x pixel_um / focal_mm

_scanner_filters = None


def _filter_helpers():
    """Lazily import the scanner's filter vocabulary.

    Same trick shooting_publish.py uses: scripts/ isn't a package, so it
    goes on sys.path first. Imported on first use rather than at module
    scope because the scanner pulls in numpy/astropy and nothing else in
    the request path needs it.
    """
    global _scanner_filters
    if _scanner_filters is None:
        scripts_dir = str(REPO_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from build_archive_manifest import bands_for, canon_filter
        _scanner_filters = (canon_filter, bands_for)
    return _scanner_filters


def _num(value):
    """float(value) when it is a real finite number, else None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _plan_pixel_scale(telescope: dict | None, camera: dict | None) -> float | None:
    """arcsec/px for a plan's telescope + camera pair, or None when either
    is missing or unusable. Unbinned, to match _fov_arcmin."""
    if not telescope or not camera:
        return None
    fl = _num(telescope.get("focal_length_mm"))
    px_um = _num(camera.get("pixel_size_um"))
    if not fl or fl <= 0 or not px_um or px_um <= 0:
        return None
    return ARCSEC_PER_RAD_MM * px_um / fl


def _fingerprint_focal_length(fp: dict) -> float | None:
    """Solved focal length wins over the profile's nominal value: a focal
    reducer, a different back-focus or simply a wrong profile entry all
    show up in the plate solve and nowhere else."""
    fl = fp.get("focal_length_mm")
    if isinstance(fl, dict):
        return _num(fl.get("solved")) or _num(fl.get("profile"))
    return _num(fl)


def _fingerprint_optics(fp: dict) -> dict:
    """Connected pixel scale and field of view derived from the fingerprint.

    Returns {pixel_scale_arcsec, fov_arcmin: [w, h], focal_length_mm} with
    None / zeros where the fingerprint didn't carry enough to compute them.
    The plugin sends `pixel_scale_arcsec` when NINA knows it; we derive it
    from pixel size, bin and focal length otherwise so an older plugin
    build still matches.
    """
    camera = fp.get("camera") or {}
    fl = _fingerprint_focal_length(fp)
    bin_v = _num(camera.get("bin")) or 1.0
    if bin_v < 1:
        bin_v = 1.0
    px_um = _num(camera.get("pixel_size_um"))
    scale = _num(fp.get("pixel_scale_arcsec"))
    if scale is None and px_um and fl and fl > 0:
        scale = ARCSEC_PER_RAD_MM * px_um * bin_v / fl
    fov = [0.0, 0.0]
    sensor = camera.get("sensor_px")
    if scale and isinstance(sensor, (list, tuple)) and len(sensor) == 2:
        w_px, h_px = _num(sensor[0]), _num(sensor[1])
        if w_px and h_px:
            # sensor_px is the unbinned pixel count and `scale` is the
            # binned scale, so divide by the bin factor to keep the
            # product (the physical sensor size) right either way.
            fov = [round(w_px / bin_v * scale / 60.0, 2),
                   round(h_px / bin_v * scale / 60.0, 2)]
    return {"pixel_scale_arcsec": scale, "fov_arcmin": fov, "focal_length_mm": fl}


def _fingerprint_bands(fp: dict) -> dict[str, str]:
    """Which coverage bands the connected filters can deliver.

    Maps band -> credit kind, one of "direct" (a real single-band filter),
    "colour" (an OSC sensor with no filter wheel, which credits R, G and B
    at once) or "dual" (a dual or quad band filter credits Ha and OIII
    together, which is worth a warning because the plan's per-filter hours
    can't actually be shot independently). Canonicalisation and the band
    map both come from the scanner, so a filter named "Antlia Ha" here
    counts the same way it does in the manifest.
    """
    canon_filter, bands_for = _filter_helpers()
    camera = fp.get("camera") or {}
    colour = bool(camera.get("colour"))
    names = [str(f) for f in (fp.get("filters") or []) if str(f).strip()]
    if colour and not names:
        # A one-shot colour camera with no filter wheel still shoots R, G
        # and B every frame. bands_for spells that out for "NoFilter".
        names = ["NoFilter"]
    rank = {"direct": 3, "colour": 2, "dual": 1}
    out: dict[str, str] = {}
    for raw in names:
        canon = canon_filter(raw)
        bands = bands_for(canon, colour)
        if len(bands) == 1:
            kind = "direct"
        elif set(bands) == {"R", "G", "B"}:
            kind = "colour"
        else:
            kind = "dual"
        for band in bands:
            if rank[kind] > rank.get(out.get(band, ""), 0):
                out[band] = kind
    return out


def _plan_goal_filters(plan: dict) -> list[str]:
    """Canonical names of the filters a plan actually asks for. A goal with
    no hours on it isn't something the rig has to be able to shoot."""
    canon_filter, _ = _filter_helpers()
    out = []
    for name, totals in fr.hours_by_filter(plan).items():
        if totals["target_hours"] <= 0:
            continue
        canon = canon_filter(name) or str(name)
        if canon not in out:
            out.append(canon)
    return out


def _match_plan(plan: dict, optics: dict, bands: dict[str, str],
                telescopes_by_id: dict, cameras_by_id: dict) -> dict:
    """Verdict for one plan against one fingerprint. See the module comment
    above for the rules. Every reason is collected, not just the first, so
    the plugin can tell the user everything that's wrong in one go."""
    telescope = telescopes_by_id.get(plan.get("telescope_id") or "")
    camera = cameras_by_id.get(plan.get("camera_id") or "")
    plan_scale = _plan_pixel_scale(telescope, camera)
    plan_fov_w, plan_fov_h = _fov_arcmin(telescope, camera)

    result = {
        "verdict": "unconstrained",
        "pixel_scale_ratio": None,
        "fov_ratio": [None, None],
        "filters_missing": [],
        "reasons": [],
    }

    conn_scale = optics["pixel_scale_arcsec"]
    conn_w, conn_h = optics["fov_arcmin"]
    if not plan_scale or plan_fov_w <= 0 or plan_fov_h <= 0:
        result["reasons"].append(
            "Plan has no telescope or camera set, so there is nothing to match against.")
        return result
    if not conn_scale or conn_w <= 0 or conn_h <= 0:
        result["reasons"].append(
            "Fingerprint did not carry enough camera detail to derive a pixel scale.")
        return result

    scale_ratio = conn_scale / plan_scale
    fov_ratio = [conn_w / plan_fov_w, conn_h / plan_fov_h]
    result["pixel_scale_ratio"] = round(scale_ratio, 4)
    result["fov_ratio"] = [round(fov_ratio[0], 4), round(fov_ratio[1], 4)]

    hard_fail = False
    warnings = []

    if abs(scale_ratio - 1.0) > PIXEL_SCALE_TOL:
        hard_fail = True
        result["reasons"].append(
            f"Pixel scale {conn_scale:.3f}\"/px is {abs(scale_ratio - 1.0) * 100:.0f}% "
            f"off the plan's {plan_scale:.3f}\"/px.")

    worst_fov = min(fov_ratio)
    if worst_fov < FOV_WARN:
        hard_fail = True
        result["reasons"].append(
            f"Field of view {conn_w:.0f}x{conn_h:.0f}' is only {worst_fov * 100:.0f}% "
            f"of the plan's {plan_fov_w:.0f}x{plan_fov_h:.0f}'.")
    elif worst_fov < FOV_FIT:
        warnings.append(
            f"Field of view is {worst_fov * 100:.0f}% of the plan's, so the framing "
            f"will be tighter than planned.")

    for filt in _plan_goal_filters(plan):
        kind = bands.get(filt)
        if kind is None:
            result["filters_missing"].append(filt)
        elif kind == "dual":
            warnings.append(
                f"{filt} is only available through a dual band filter, so its hours "
                f"can't be shot independently.")
    if result["filters_missing"]:
        hard_fail = True
        result["reasons"].append(
            "No filter for " + ", ".join(result["filters_missing"]) + ".")

    result["reasons"].extend(warnings)
    if hard_fail:
        result["verdict"] = "no_fit"
    elif warnings:
        result["verdict"] = "fit_with_warnings"
    else:
        result["verdict"] = "fit"
    return result


MAX_WHEEL_SLOTS = 64
MAX_WHEEL_SLOT_CHARS = 32
MAX_PROFILE_ID_CHARS = 64


def _normalise_fingerprint(fp: dict) -> dict:
    """The subset of a fingerprint that identifies the rig, rounded so the
    same physical setup hashes the same way across sessions.

    Rotation and site are deliberately left out: pointing the same rig at a
    different target, or driving to a different dark site, does not make it
    a different rig.
    """
    camera = fp.get("camera") or {}
    sensor = camera.get("sensor_px")
    canon_filter, _ = _filter_helpers()
    fl = _fingerprint_focal_length(fp)
    px_um = _num(camera.get("pixel_size_um"))
    out = {
        "profile_name": str(fp.get("profile_name") or ""),
        "camera": {
            "name": str(camera.get("name") or ""),
            "sensor_px": [int(_num(sensor[0]) or 0), int(_num(sensor[1]) or 0)]
                         if isinstance(sensor, (list, tuple)) and len(sensor) == 2 else [0, 0],
            "pixel_size_um": round(px_um, 3) if px_um else None,
            "colour": bool(camera.get("colour")),
            "bin": int(_num(camera.get("bin")) or 1),
        },
        "filters": sorted({canon_filter(str(f)) or str(f)
                           for f in (fp.get("filters") or []) if str(f).strip()}),
        "mount": str((fp.get("mount") or {}).get("name") or ""),
        "focal_length_mm": round(fl, 1) if fl else None,
    }
    # The wheel as the NINA profile holds it, raw and in slot order, and the
    # profile's id (docs/specs/gear-templates.md section 5). `filters` above
    # stays canonical and sorted. Left out when the plugin sent none, so an
    # older plugin's stored entry keeps its shape.
    slots = fp.get("wheel_slots")
    if isinstance(slots, list) and slots:
        out["wheel_slots"] = [str(sl) for sl in slots if isinstance(sl, str)]
    pid = fp.get("profile_id")
    if isinstance(pid, str) and pid.strip():
        out["profile_id"] = pid.strip()
    return out


def _fingerprint_id(fp: dict) -> str:
    # Which profile reported a rig, and how its wheel slots are spelt, do not
    # make it a different rig, so neither goes into the id.
    norm = _normalise_fingerprint(fp)
    norm.pop("wheel_slots", None)
    norm.pop("profile_id", None)
    payload = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _validate_fingerprint(fp) -> str | None:
    """Reject a fingerprint that can't produce a meaningful verdict.

    Only `camera` and `focal_length_mm` are required, everything else is
    optional, because a plugin talking to a rig with no filter wheel, no
    mount driver or no site set still deserves an answer.
    """
    if not isinstance(fp, dict):
        return "payload must be a JSON object"
    mode = fp.get("mode")
    if mode is not None and mode not in ("fit", "everything"):
        return "mode must be 'fit' or 'everything'"
    camera = fp.get("camera")
    if not isinstance(camera, dict):
        return "camera object required"
    sensor = camera.get("sensor_px")
    if (not isinstance(sensor, (list, tuple)) or len(sensor) != 2
            or _num(sensor[0]) is None or _num(sensor[1]) is None):
        return "camera.sensor_px must be [width_px, height_px]"
    if _num(camera.get("pixel_size_um")) is None:
        return "camera.pixel_size_um must be a finite number"
    if "bin" in camera and (_num(camera["bin"]) is None or _num(camera["bin"]) < 1):
        return "camera.bin must be a number >= 1"
    if "colour" in camera and not isinstance(camera["colour"], bool):
        return "camera.colour must be a boolean"
    filters = fp.get("filters")
    if filters is not None and (not isinstance(filters, list)
                                or not all(isinstance(f, str) for f in filters)):
        return "filters must be a list of strings"
    slots = fp.get("wheel_slots")
    if slots is not None:
        if (not isinstance(slots, list) or len(slots) > MAX_WHEEL_SLOTS
                or not all(isinstance(sl, str) and len(sl) <= MAX_WHEEL_SLOT_CHARS for sl in slots)):
            return (f"wheel_slots must be a list of at most {MAX_WHEEL_SLOTS} strings "
                    f"of at most {MAX_WHEEL_SLOT_CHARS} characters")
    pid = fp.get("profile_id")
    if pid is not None and (not isinstance(pid, str) or len(pid) > MAX_PROFILE_ID_CHARS):
        return f"profile_id must be a string of at most {MAX_PROFILE_ID_CHARS} characters"
    fl = fp.get("focal_length_mm")
    if isinstance(fl, dict):
        if _num(fl.get("solved")) is None and _num(fl.get("profile")) is None:
            return "focal_length_mm needs a finite 'solved' or 'profile' value"
    elif _num(fl) is None:
        return "focal_length_mm required (number, or {profile, solved})"
    if "pixel_scale_arcsec" in fp and fp["pixel_scale_arcsec"] is not None:
        scale = _num(fp["pixel_scale_arcsec"])
        if scale is None or scale <= 0:
            return "pixel_scale_arcsec must be a positive number"
    return None


@app.route("/api/plans/match", methods=["POST"])
def api_plans_match():
    """Score every plan against the gear the plugin says is connected.

    Returns all verdicts, not just the fits: the caller decides what to
    show. The fingerprint itself is stored under its profile name so ACP's
    own planning rail can say what each NINA install last reported.

    This is what the NINA plugin's "Sync for tonight" calls to decide what
    to load onto the rig. A plugin that sends `supports_state: true` gets
    every plan back, including draft, inactive and closed ones, because
    the plugin still has to write a paused plan's state to its TS row even
    though it won't load it (see docs/specs/ts-project-settings.md section
    6): those plans come back with `match: {"verdict": "held", "state":
    "<state>"}` and don't count in `summary`. Only `"active"` plans (or a
    plan with no `state` at all) get a real verdict. Without the flag, an
    older plugin or ACP's own planning rail sees only active plans, same
    as this endpoint behaved before state existed: an old plugin writes
    state 1 on every push, so it must never be shown a paused plan.
    """
    fp = request.get_json(silent=True) or {}
    err = _validate_fingerprint(fp)
    if err:
        return jsonify({"error": err}), 400

    gear = load_gear()
    telescopes_by_id = {t["id"]: t for t in gear.get("telescopes", []) if t.get("id")}
    cameras_by_id = {c["id"]: c for c in gear.get("cameras", []) if c.get("id")}

    optics = _fingerprint_optics(fp)
    bands = _fingerprint_bands(fp)
    want = frozenset({"gear", "panels"})
    supports_state = bool(fp.get("supports_state"))

    out_plans = []
    summary = {"fit": 0, "fit_with_warnings": 0, "no_fit": 0, "unconstrained": 0}
    for plan in load_plans().get("plans", []):
        if not isinstance(plan, dict):
            continue
        state = plan.get("state") or "active"
        if state != "active":
            if not supports_state:
                continue
            entry = _expand_plan(plan, want, gear, None)
            entry["match"] = {"verdict": "held", "state": state}
            out_plans.append(entry)
            continue
        match = _match_plan(plan, optics, bands, telescopes_by_id, cameras_by_id)
        summary[match["verdict"]] += 1
        entry = _expand_plan(plan, want, gear, None)
        entry["match"] = match
        out_plans.append(entry)

    fp_id = _fingerprint_id(fp)
    mode = fp.get("mode") or "fit"
    _store_fingerprint(fp, fp_id, mode, summary)
    return jsonify({
        "fingerprint_id": fp_id,
        "mode": mode,
        "plans": out_plans,
        "summary": summary,
    })


# Kept alongside the normalised rig because they help diagnose a report and
# say nothing about where the user lives. Deliberately excludes `site` and
# `rotation_deg`.
_FINGERPRINT_EXTRA_KEYS = ("nina_version", "pixel_scale_arcsec")


def _store_fingerprint(fp: dict, fp_id: str, mode: str, summary: dict) -> None:
    """Remember the last fingerprint per profile name.

    Keyed by profile name because that's what the user recognises in
    NINA's own profile switcher; a fingerprint with no profile name falls
    back to its own hash so it still shows up rather than being dropped.
    Write failures are logged and swallowed: failing to record telemetry
    must never cost the caller its match results.
    """
    name = str(fp.get("profile_name") or "").strip()
    profile_id = fp.get("profile_id").strip() if isinstance(fp.get("profile_id"), str) else ""
    key = profile_id or name or fp_id
    doc = load_fingerprints()
    profiles = dict(doc.get("profiles") or {})
    if profile_id and name:
        # A report with an id replaces the entry an older plugin left under
        # the profile's name, which has no id (spec section 5).
        old_entry = profiles.get(name)
        if isinstance(old_entry, dict) and not old_entry.get("profile_id") and name != key:
            del profiles[name]
    profiles[key] = {
        "profile_name": name or key,
        **({"profile_id": profile_id} if profile_id else {}),
        "fingerprint_id": fp_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "summary": dict(summary),
        # The normalised rig plus a short allowlist, not the raw body.
        # Storing the body verbatim let an unknown key a caller invented
        # reach disk, and it also wrote the user's observing site
        # coordinates into a file /api/fingerprints then served to anyone
        # who could reach the port. The store has no use for the site, the
        # rotation, or anything else the caller chose to send.
        "fingerprint": {**_normalise_fingerprint(fp),
                        **{k: fp[k] for k in _FINGERPRINT_EXTRA_KEYS
                           if isinstance(fp.get(k), (str, int, float))}},
    }
    if len(profiles) > MAX_FINGERPRINT_PROFILES:
        # Oldest report out first. An entry with no timestamp sorts first and
        # so goes first, which is the right call for a record we cannot age.
        keep = sorted(profiles.items(),
                      key=lambda kv: str((kv[1] or {}).get("received_at") or ""),
                      reverse=True)[:MAX_FINGERPRINT_PROFILES]
        dropped = len(profiles) - len(keep)
        profiles = dict(keep)
        logging.info("fingerprint store at its ceiling of %d profiles; dropped %d oldest",
                     MAX_FINGERPRINT_PROFILES, dropped)
    try:
        save_fingerprints({"version": doc.get("version", 1), "profiles": profiles})
    except (OSError, ValueError) as exc:
        logging.warning("could not write %s: %s", FINGERPRINTS_PATH, exc)
        return
    if profile_id:
        # A fingerprint can complete the pair a template list was waiting for.
        _apply_ts_lists_safely()


@app.route("/api/fingerprints")
def api_fingerprints():
    """The last fingerprint each NINA profile reported. Read-only; the only
    writer is /api/plans/match."""
    doc = load_fingerprints()
    return jsonify({"version": doc.get("version", 1), "profiles": doc.get("profiles") or {}})


def _validate_progress_payload(payload) -> str | None:
    """Same defence-in-depth shape as _validate_plan_payload: the numbers
    here land straight in plans.json and feed the sync builder's
    acquired-count arithmetic, so NaN and negatives are refused rather
    than written."""
    if not isinstance(payload, dict):
        return "payload must be a JSON object"
    filters = payload.get("filters")
    entries = payload.get("entries")
    if entries is not None:
        # One entry per exposure plan, from a plugin that knows filter rows
        # (docs/specs/goal-panel-and-ts-order.md section 3). It also sends
        # the old `filters` object for an ACP that predates rows.
        if not isinstance(entries, list) or not entries:
            return "entries must be a non-empty list"
        items = []
        for i, cfg in enumerate(entries):
            if not isinstance(cfg, dict):
                return f"entries[{i}] must be an object"
            if not isinstance(cfg.get("filter"), str) or not cfg["filter"].strip():
                return f"entries[{i}].filter must be a non-empty string"
            if cfg.get("template") is not None and not isinstance(cfg["template"], str):
                return f"entries[{i}].template must be a string"
            items.append((f"entries[{i}]", cfg))
    elif isinstance(filters, dict) and filters:
        items = [(f"filters[{name!r}]", cfg) for name, cfg in filters.items()]
    else:
        return "filters object required"
    for name, cfg in items:
        if not isinstance(cfg, dict):
            return f"{name} must be an object"
        # A JSON string is refused rather than coerced. _num would happily
        # turn "1e6" into a million, and this value is written straight into
        # a plan, so the caller has to mean it as a number.
        raw_hours = cfg.get("acquired_hours")
        if isinstance(raw_hours, bool) or not isinstance(raw_hours, (int, float)):
            return f"{name}.acquired_hours must be a number >= 0"
        hours = _num(raw_hours)
        if hours is None or hours < 0:
            return f"{name}.acquired_hours must be a number >= 0"
        # An upper bound, because hours only ever go up without `force`: one
        # absurd value writes a goal that can never be corrected through this
        # endpoint again. The cap is far past any real integration time, so it
        # rejects nonsense without ever rejecting an honest report.
        if hours > MAX_ACQUIRED_HOURS:
            return (f"{name}.acquired_hours must be <= "
                    f"{MAX_ACQUIRED_HOURS} (got {hours!r})")
        if "acquired_count" in cfg and cfg["acquired_count"] is not None:
            count = cfg["acquired_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                return f"{name}.acquired_count must be an integer >= 0"
        if cfg.get("panels") is not None:
            err = fr.validate_panels(cfg["panels"], f"{name}.panels")
            if err:
                return err
            for key, val in cfg["panels"].items():
                if val > MAX_ACQUIRED_HOURS:
                    return (f"{name}.panels[{key!r}] must be <= "
                            f"{MAX_ACQUIRED_HOURS} (got {val!r})")
    if "force" in payload and not isinstance(payload["force"], bool):
        return "force must be a boolean"
    for field in ("source", "at"):
        if field in payload and payload[field] is not None and not isinstance(payload[field], str):
            return f"{field} must be a string"
    return None


def _template_names_for_row(plan: dict):
    """A function from a row to every name its gear template has gone by:
    the current name, `aliases` and `ts_names`."""
    stored = _load_gear_stored()
    cam = next((c for c in stored.get("cameras") or []
                if isinstance(c, dict) and c.get("id") == plan.get("camera_id")), None)

    def names(row: dict) -> set[str]:
        tpls = fr.camera_templates(cam, row.get("filter"))
        if not tpls:
            return set()
        tpl = next((t for t in tpls if t.get("id") == row.get("template_id")), None) \
            or fr.find_template(tpls, row.get("template"))
        return fr.template_names(tpl)

    return names


@app.route("/api/plans/<plan_id>/progress", methods=["POST"])
@_holds_plans_lock
def api_plan_progress(plan_id: str):
    """Record acquired hours against a plan's filter rows.

    Called by the plugin after an imaging session, and by anything else
    that knows what was actually shot. Hours only ever go up: NINA's own
    acquired count can drop when frames are culled or a project is reset,
    and silently rewinding a plan the user has been watching fill up is
    worse than being one session stale. Pass `"force": true` to overwrite
    downward on purpose.

    Filters the plan has no goal for are ignored rather than invented, and
    named in the response so the caller can tell the difference between
    "recorded" and "quietly dropped". An entry naming a template lands on the
    row with that filter and template; one without (an older plugin) lands on
    the filter's only row, and is unknown when the filter has two.
    """
    err = _validate_progress_payload(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    payload = request.get_json(silent=True) or {}

    data = load_plans()
    plans = data.get("plans", [])
    idx = next((i for i, p in enumerate(plans) if p.get("id") == plan_id), None)
    if idx is None:
        return jsonify({"error": "not found"}), 404
    plan = plans[idx]

    canon_filter, _ = _filter_helpers()
    rows = fr.rows_of(plan)
    names_for_row = _template_names_for_row(plan)

    if payload.get("entries") is not None:
        reports = [(e["filter"], e.get("template"), e) for e in payload["entries"]]
    else:
        reports = [(name, None, cfg) for name, cfg in payload["filters"].items()]

    force = bool(payload.get("force"))
    updated, unknown, not_lowered, panel_rows_kept = {}, [], [], []
    tracking = activity.enabled()
    hours_before = copy.deepcopy(fr.hours_by_filter(plan)) if tracking else {}
    reached_before = {r.get("id"): _row_at_goal(r, plan) for r in rows} if tracking else {}
    for name, template, cfg in reports:
        # "Antlia Ha" from the plugin lands on the plan's Ha rows.
        canon = canon_filter(name) or str(name)
        same_filter = [r for r in rows if r.get("filter") in (canon, name)]
        if template is not None:
            # A row renamed in gear since the plugin read the plans still
            # answers to its earlier names (gear templates spec section 7).
            hits = [r for r in same_filter
                    if r.get("template") == template or template in names_for_row(r)]
            label = f"{canon} ({template})"
        else:
            # An older plugin sends no template. It can only mean the one
            # row of that filter; with two, which one is a guess.
            hits = same_filter
            label = name
        if len(hits) != 1:
            unknown.append(label)
            continue
        row = hits[0]
        key = row.get("template") if template is not None else row.get("filter")
        reported = cfg.get("panels")
        if isinstance(reported, dict) and reported:
            # Per-panel report: merge it into the row's map. A panel only goes
            # up unless `force`, which replaces the whole map. actual_hours
            # becomes the sum of the map, whatever acquired_hours said.
            stored = fr.row_panels(row) or {}
            merged = {} if force else dict(stored)
            for pkey, hours in reported.items():
                hours = float(hours)
                if not force and hours < stored.get(pkey, 0.0):
                    not_lowered.append(f"{key}#{pkey}")
                    continue
                merged[pkey] = hours
            if merged != stored or "panels" not in row:
                row["panels"] = {k: round(v, 4) for k, v in sorted(merged.items())}
                fr.sync_actuals({"filter_rows": [row], "target": plan.get("target")})
                updated[key] = row["actual_hours"]
            continue
        new_hours = _num(cfg.get("acquired_hours"))
        current = _num(row.get("actual_hours")) or 0.0
        if isinstance(row.get("panels"), dict) and not force:
            # The row is tracked per panel, so the per-panel figures are the
            # record. A report with one figure for the whole plan cannot say
            # which panel it belongs to; leave the map and the sum alone.
            panel_rows_kept.append(key)
            continue
        if new_hours < current and not force:
            not_lowered.append(key)
            continue
        row["actual_hours"] = new_hours
        row.pop("panels", None)
        updated[key] = new_hours

    if updated:
        plan["updated_at"] = datetime.now(timezone.utc).isoformat()
        plans[idx] = plan
        save_plans({**data, "version": data.get("version", 1), "plans": plans})

    if tracking:
        plan_copy = copy.deepcopy(plan)
        hours_after = fr.hours_by_filter(plan_copy)
        newly = [r for r in fr.rows_of(plan_copy)
                 if _row_at_goal(r, plan_copy) and not reached_before.get(r.get("id"), False)]
        _activity_later(lambda: _record_progress(plan_copy, hours_before, hours_after,
                                                 list(unknown), newly))

    return jsonify({
        "ok": True,
        "plan": plan,
        "updated": updated,
        "unknown_filters": unknown,
        "not_lowered": not_lowered,
        "panel_rows_kept": panel_rows_kept,
    })


@app.route("/api/plans/<plan_id>/accepted", methods=["POST"])
@_holds_plans_lock
def api_plan_accepted(plan_id: str):
    """Store AAC's accepted counts against a plan's filter rows.

    AAC is the only caller (docs/specs/goal-panel-and-ts-order.md section 6,
    and its own spec, incremental-master-builds.md section 7). The access gate
    has already checked the bearer token. A row lands only when its id, echoed
    filter and echoed template match the plan; the rest come back in
    `unknown_rows` with a reason, and AAC does not retry them until its figures
    change. Values may go down, and `actual_hours` is never touched.
    """
    body = request.get_json(silent=True)
    data = load_plans()
    plans = data.get("plans", [])
    idx = next((i for i, p in enumerate(plans) if p.get("id") == plan_id), None)
    if idx is None:
        return jsonify({"error": "not found"}), 404
    err = fr.validate_accepted(body)
    if err:
        return jsonify({"error": err}), 400
    plan = plans[idx]
    canon_filter, _ = _filter_helpers()
    now = datetime.now(timezone.utc).isoformat()
    complete_before = {r.get("id"): bool((r.get("aac") or {}).get("complete"))
                       for r in fr.rows_of(plan)}
    unknown = fr.apply_accepted(plan, body, now, canon_filter, _template_names_for_row(plan))
    plans[idx] = plan
    save_plans({**data, "version": data.get("version", 1), "plans": plans})
    if activity.enabled():
        plan_copy = copy.deepcopy(plan)
        newly = [r for r in fr.rows_of(plan_copy)
                 if (r.get("aac") or {}).get("complete") and not complete_before.get(r.get("id"))]
        _activity_later(lambda: _record_accepted(plan_copy, newly))
    return jsonify({"ok": True, "unknown_rows": unknown,
                    "filed_through_night": body["filed_through_night"]})


def _row_at_goal(row: dict, plan: dict) -> bool:
    # A mosaic row's goal is per panel. With a panels map each panel counts up
    # to its own goal; without one the stored actual covers the plan.
    goal = fr.goal_hours(row) * fr.panel_count(plan)
    actual = fr.credited_hours(row, plan)
    return goal > 0 and actual >= goal


def _record_goal_reached(plan: dict, row: dict) -> None:
    goal = fr.goal_hours(row) * fr.panel_count(plan)
    filt = str(row.get("filter") or "")
    activity.record(
        type="goal_reached", key=f"goal:{plan.get('id')}:{row.get('id')}:{goal:g}",
        title=f"{activity.plan_name(plan)}: {filt} reached its goal of {goal:g} h",
        refs={**activity.plan_refs(plan), "filter": filt}, data={"goal_h": round(goal, 4)})


def _record_progress(plan: dict, before: dict, after: dict, unknown: list, newly: list) -> None:
    """The night's progress report for a plan, with the hours it added, plus an
    open item for filters the plan has no goal for."""
    night = activity.current_night()
    plan_id = plan.get("id")
    name = activity.plan_name(plan)

    def merge(previous):
        prior = ((previous or {}).get("data") or {}).get("filters") or {}
        filters = {}
        for fname, now in after.items():
            was = (before.get(fname) or {}).get("actual_hours", now["actual_hours"])
            carried = (prior.get(fname) or {}).get("delta_h")
            carried = carried if isinstance(carried, (int, float)) else 0.0
            filters[fname] = {"hours": round(now["actual_hours"], 4),
                              "goal": round(now["target_hours"], 4),
                              "delta_h": round(carried + now["actual_hours"] - was, 4)}
        added = sum(f["delta_h"] for f in filters.values())
        title = (f"{name}: +{added:.2f} h reported" if added > 0.005
                 else f"{name}: progress reported, no new hours")
        return {"title": title, "data": {"filters": filters},
                "resolves": [] if unknown else [f"progress:{plan_id}"]}

    activity.record_merged(
        merge, type="progress_report", key=f"progress_report:{plan_id}:{night}", night=night,
        refs=activity.plan_refs(plan), title="progress", data={})
    if unknown:
        # A report count in the content, so a repeat after a clean report
        # changes it and reopens the item.
        def merge_unknown(previous):
            return {"data": {"reports": int(_carried(previous, "reports")) + 1}}

        activity.record_merged(
            merge_unknown, type="progress_unknown", key=f"progress_unknown:{plan_id}:{night}",
            night=night, severity="warn", needs_you=True, resolve_key=f"progress:{plan_id}",
            title=f"{name}: a progress report named filters the plan has no goal for",
            detail="Unknown: " + ", ".join(str(u) for u in unknown),
            refs=activity.plan_refs(plan), data={})
    for row in newly:
        _record_goal_reached(plan, row)


def _record_accepted(plan: dict, newly: list) -> None:
    night = activity.current_night()
    rows = []
    for r in fr.rows_of(plan):
        block = r.get("aac")
        if isinstance(block, dict):
            rows.append({"filter": r.get("filter"), "template": r.get("template"),
                         "accepted_frames": block.get("accepted_frames"),
                         "accepted_h": round((block.get("accepted_seconds") or 0) / 3600.0, 3),
                         "complete": bool(block.get("complete"))})
    activity.record(
        type="accepted_counts", key=f"accepted_counts:{plan.get('id')}:{night}", night=night,
        title=f"{activity.plan_name(plan)}: AAC accepted counts updated ({len(rows)} rows)",
        refs=activity.plan_refs(plan), data={"rows": rows[:40]})
    for row in newly:
        _record_goal_reached(plan, row)


# ---------------------------------------------------------------------------
# Activity tab (docs/specs/activity-tab.md)
#
# The storage and the rules live in activity.py. These routes only check the
# gate-adjacent things (the token for posting, the Origin for browser writes),
# parse the request and hand over. ACP_ACTIVITY=off makes every one a 404.
# ---------------------------------------------------------------------------

def _activity_route(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not activity.enabled():
            return jsonify({"error": "not found"}), 404
        try:
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception:
            logging.exception("activity store failed")
            return jsonify({"error": "the activity store is unavailable"}), 503
    return wrapper


def _origin_refused():
    """A browser write whose Origin is another site is refused. A caller with
    no Origin header (the plugin, curl, a test) is not a browser."""
    origin = request.headers.get("Origin")
    if not origin:
        return None
    seen = urllib.parse.urlsplit(origin).netloc.lower()
    allowed = {request.host.lower()}
    forwarded = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip().lower()
    if forwarded:
        allowed.add(forwarded)
    if seen in allowed:
        return None
    return jsonify({"error": "cross-origin write refused"}), 403


@app.route("/api/activity/events", methods=["POST"])
@_activity_route
def api_activity_events():
    if not _api_auth_token():
        return jsonify({"error": "ACP needs an access token set before it accepts activity events"}), 403
    # Above the 1 MB every other route has; set before the body is read.
    request.max_content_length = activity.MAX_BODY_BYTES
    body = request.get_json(silent=True)
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        return jsonify({"error": "the body must be {\"events\": [...]}"}), 400
    if len(events) > activity.MAX_BATCH:
        return jsonify({"error": f"more than {activity.MAX_BATCH} events in one batch",
                        "max_events": activity.MAX_BATCH}), 413
    return jsonify(activity.ingest(events))


def _int_arg(name: str, default: int):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default, None
    try:
        return int(raw), None
    except ValueError:
        return None, (jsonify({"error": f"{name} must be an integer"}), 400)


@app.route("/api/activity")
@_activity_route
def api_activity():
    nights, bad = _int_arg("nights", activity.DEFAULT_NIGHTS)
    if bad:
        return bad
    if nights < 1:
        return jsonify({"error": "nights must be 1 or more"}), 400
    before = request.args.get("before") or None
    if before is not None and not activity.valid_night(before):
        return jsonify({"error": "before must be a night, YYYY-MM-DD"}), 400
    return jsonify(activity.list_nights(nights=min(nights, activity.MAX_NIGHTS), before=before))


@app.route("/api/activity/night/<night>")
@_activity_route
def api_activity_night(night: str):
    if not activity.valid_night(night):
        return jsonify({"error": "night must be YYYY-MM-DD"}), 400
    offset, bad = _int_arg("offset", 0)
    if bad:
        return bad
    if offset < 0:
        return jsonify({"error": "offset must be 0 or more"}), 400
    return jsonify(activity.night_page(night, offset))


@app.route("/api/activity/summary")
@_activity_route
def api_activity_summary():
    return jsonify(activity.summary())


@app.route("/api/activity/seen", methods=["POST"])
@_activity_route
def api_activity_seen():
    refused = _origin_refused()
    if refused:
        return refused
    # Whatever time the client sends is ignored: last_seen is the server's.
    return jsonify({"last_seen": activity.mark_seen()})


@app.route("/api/activity/events/<int:event_id>/dismiss", methods=["POST"])
@_activity_route
def api_activity_dismiss(event_id: int):
    refused = _origin_refused()
    if refused:
        return refused
    if not activity.set_dismissed(event_id, True):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/activity/events/<int:event_id>/restore", methods=["POST"])
@_activity_route
def api_activity_restore(event_id: int):
    refused = _origin_refused()
    if refused:
        return refused
    if not activity.set_dismissed(event_id, False):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/activity/prefs", methods=["GET", "PUT"])
@_activity_route
def api_activity_prefs():
    if request.method == "GET":
        return jsonify(activity.get_prefs())
    refused = _origin_refused()
    if refused:
        return refused
    body = request.get_json(silent=True)
    chips = body.get("chips") if isinstance(body, dict) else None
    if not isinstance(chips, dict) or any(
            not isinstance(v, bool) for k, v in chips.items() if k in activity.DEFAULT_CHIPS):
        return jsonify({"error": "chips must be an object of chip id to true or false"}), 400
    return jsonify(activity.set_prefs(chips))


def _build_live_document() -> dict:
    import shooting_publish as sp
    plans = load_plans().get("plans", [])
    return sp.build_shooting_document(plans, load_manifest(), load_gear())


@app.route("/api/publish/config")
def api_publish_config():
    """Tells the frontend whether live-page publishing is configured. The
    plan editor only shows the Public page section when it is, so a stock
    install looks the same as before this feature existed."""
    return jsonify({"live_page_enabled": bool((os.environ.get("ACP_PUBLISH_DEST") or "").strip())})


@app.route("/api/public/shooting")
def api_public_shooting():
    """Preview of the document the live-page publisher pushes.
    Only public plans with a live status appear. See docs/specs/shooting-page.md."""
    return jsonify(_build_live_document())


@app.route("/api/publish/shooting", methods=["POST"])
def api_publish_shooting():
    """Write data/live/shooting.json and upload it to ACP_PUBLISH_DEST.
    400 when the destination is unset, 502 when the upload fails."""
    import shooting_publish as sp
    try:
        dest = sp.resolve_dest()
    except sp.PublishConfigError as e:
        # Fixed text on the wire; the detail goes to the log.
        logging.warning("live publish refused: %s", _safe_log(str(e)))
        return jsonify({"error": "ACP_PUBLISH_DEST is not set on the server"}), 400
    doc = _build_live_document()
    sp.attach_thumbs(doc, LIVE_OUT_DIR)
    path = sp.write_document(doc, LIVE_OUT_DIR)
    result = sp.push_document(path, dest, os.environ.get("ACP_PUBLISH_SSH_KEY") or None)
    ok = result.returncode == 0
    if not ok:
        logging.warning("live publish failed: %s", _safe_log(result.stderr))
    body = {
        "ok": ok,
        "dest": dest,
        "projects": len(doc["projects"]),
        "error": None if ok else "upload failed; see the ACP server log for the reason",
    }
    return jsonify(body), (200 if ok else 502)


@app.route("/api/target-overrides", methods=["GET", "POST"])
def api_target_overrides():
    """Per-target state the user sets manually: primarily the finished flag for
    targets that have no plan but should still be treated as done. Keyed by
    target_id (string) to survive JSON round-trips cleanly."""
    data = load_target_overrides()
    if request.method == "GET":
        return jsonify({
            "version": data.get("version", 1),
            "overrides": data.get("overrides", {}),
        })
    payload = request.get_json(silent=True) or {}
    tid = payload.get("target_id")
    if tid is None or (isinstance(tid, str) and not tid.strip()):
        return jsonify({"error": "target_id required"}), 400
    key = str(tid)
    overrides = dict(data.get("overrides", {}))
    # A null/missing "finished" deletes the override so the target falls back to
    # plan-derived status: this is how the frontend clears a manual mark.
    if "finished" not in payload or payload["finished"] is None:
        overrides.pop(key, None)
    else:
        overrides[key] = {
            "finished": bool(payload["finished"]),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    save_target_overrides({"version": data.get("version", 1), "overrides": overrides})
    return jsonify({"ok": True, "overrides": overrides})


@app.route("/api/hidden", methods=["GET", "POST"])
def api_hidden():
    """Hide or unhide a plan, a project or a coverage target.

    POST {"kind": "plans"|"projects"|"targets", "key": <id>, "hidden": bool}.
    Hiding only changes what the web UI lists, draws and counts by default.
    It never changes a plan's state, and it never deletes anything.
    Target keys are the manifest target_id, stored as a string.
    """
    data = load_hidden()
    if request.method == "GET":
        return jsonify(data)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "payload must be a JSON object"}), 400
    kind = payload.get("kind")
    if kind not in HIDDEN_KINDS:
        return jsonify({"error": f"kind must be one of {', '.join(HIDDEN_KINDS)}"}), 400
    key = payload.get("key")
    if kind == "targets":
        if isinstance(key, bool) or not ((isinstance(key, int) and key >= 0)
                                         or (isinstance(key, str) and key.strip().isascii()
                                             and key.strip().isdigit())):
            return jsonify({"error": "key must be a target_id (a whole number)"}), 400
        key = str(int(key))
    else:
        if not isinstance(key, str) or not key.strip() or len(key) > _HIDDEN_KEY_MAX:
            return jsonify({"error": f"key must be a non-empty string of at most {_HIDDEN_KEY_MAX} characters"}), 400
    hidden = payload.get("hidden")
    if not isinstance(hidden, bool):
        return jsonify({"error": "hidden must be true or false"}), 400
    new = {"version": data.get("version", 1), **{k: dict(data.get(k, {})) for k in HIDDEN_KINDS}}
    if hidden:
        new[kind][key] = {"hidden_at": datetime.now(timezone.utc).isoformat()}
    else:
        new[kind].pop(key, None)
    save_hidden(new)
    return jsonify(new)


@app.route("/api/sync/config")
def api_sync_config():
    """Tells the frontend whether the global Target Scheduler database is
    reachable, so it can hide direct-to-NINA sync UI (the "Sync with NINA"
    extension button) on a machine that has no TS database at all, such as
    ACP running in Docker away from the imaging PC. The zip-export Manual
    Sync always stays available: it never touches this file."""
    return jsonify({
        "ts_db_available": _ts_db_file_exists(TS_DB_PATH),
        "ts_db_path": str(Path(os.path.expandvars(TS_DB_PATH))),
    })


_TS_TEMPLATE_NUMBER_FIELDS = ("default_exposure_s", "gain", "offset", "bin")
# Sent by a plugin that knows gear templates (docs/specs/gear-templates.md
# section 3): the TS row id and the columns Tidy compares. A list whose
# templates carry no `id` comes from an older plugin.
_TS_TEMPLATE_EXTRA_NUMBERS = ("id", "moonavoidanceenabled", "moonavoidanceseparation",
                              "moonavoidancewidth", "twilightlevel", "maximumhumidity", "ditherevery")
_TS_TEMPLATE_PLANS_MAX = 5000


_ts_store_cache: tuple | None = None


def _load_ts_templates_store() -> dict:
    """The per-profile template lists the plugin posted, or {} when none.
    The result is shared between callers, so copy before changing it."""
    global _ts_store_cache
    try:
        st = TS_TEMPLATES_PATH.stat()
    except OSError:
        return {}
    key = (str(TS_TEMPLATES_PATH), st.st_mtime_ns, st.st_size)
    if _ts_store_cache is not None and _ts_store_cache[0] == key:
        return _ts_store_cache[1]
    try:
        data = json.loads(TS_TEMPLATES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    profiles = data.get("profiles") if isinstance(data, dict) else None
    profiles = profiles if isinstance(profiles, dict) else {}
    _ts_store_cache = (key, profiles)
    return profiles


def _plugin_templates_response(path: Path, error: str):
    """Answer from the lists the plugin posted, or None when there are none.

    The server case: ACP on homeserver has no TS database file, so the plugin
    sends the list instead (docs/specs/goal-panel-and-ts-order.md section 2).
    """
    profiles = _load_ts_templates_store()
    if not profiles:
        return None
    templates = []
    for pid, entry in profiles.items():
        for t in entry.get("templates", []):
            templates.append({**t, "profile_id": pid, "received_at": entry.get("received_at")})
    return jsonify({
        "available": True,
        "source": "plugin",
        "path": str(path),
        "profiles": {
            pid: {"received_at": e.get("received_at"), "sent_at": e.get("sent_at")}
            for pid, e in profiles.items()
        },
        "templates": templates,
    })


def _validate_ts_templates_payload(payload) -> str | None:
    if not isinstance(payload, dict):
        return "body must be a JSON object"
    pid = payload.get("profile_id")
    if not isinstance(pid, str) or not pid.strip():
        return "profile_id is required"
    if "sent_at" in payload and payload["sent_at"] is not None and not isinstance(payload["sent_at"], str):
        return "sent_at must be a string"
    templates = payload.get("templates")
    if not isinstance(templates, list):
        return "templates must be a list"
    for i, t in enumerate(templates):
        if not isinstance(t, dict):
            return f"templates[{i}] must be an object"
        if not isinstance(t.get("name"), str) or not t["name"].strip():
            return f"templates[{i}].name is required"
        if "filter" in t and t["filter"] is not None and not isinstance(t["filter"], str):
            return f"templates[{i}].filter must be a string"
        for f in _TS_TEMPLATE_NUMBER_FIELDS + _TS_TEMPLATE_EXTRA_NUMBERS:
            v = t.get(f)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                return f"templates[{i}].{f} must be a number"
        if t.get("guid") is not None and not isinstance(t["guid"], str):
            return f"templates[{i}].guid must be a string"
        plans = t.get("plans")
        if plans is not None:
            if not isinstance(plans, list):
                return f"templates[{i}].plans must be a list"
            if len(plans) > _TS_TEMPLATE_PLANS_MAX:
                return f"templates[{i}].plans has more than {_TS_TEMPLATE_PLANS_MAX} entries"
            for j, plan in enumerate(plans):
                if not isinstance(plan, dict):
                    return f"templates[{i}].plans[{j}] must be an object"
                for f in ("id", "exposure"):
                    v = plan.get(f)
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        return f"templates[{i}].plans[{j}].{f} must be a number"
    return None


@app.route("/api/ts-templates", methods=["POST"])
def api_ts_templates_post():
    """Store the plugin's exposure template list for one profile.

    The latest post for a profile replaces that profile's list. The bearer
    token gate that covers every /api route applies, as it does to /progress.
    """
    payload = request.get_json(silent=True)
    err = _validate_ts_templates_payload(payload)
    if err:
        return jsonify({"error": err}), 400
    pid = payload["profile_id"].strip()
    templates = []
    for t in payload["templates"]:
        entry = {
            "name": t["name"],
            "filter": t.get("filter"),
            **{f: t.get(f) for f in _TS_TEMPLATE_NUMBER_FIELDS},
        }
        for f in _TS_TEMPLATE_EXTRA_NUMBERS + ("guid",):
            if t.get(f) is not None:
                entry[f] = t[f]
        if isinstance(t.get("plans"), list):
            entry["plans"] = [{"id": int(p["id"]), "exposure": p["exposure"]} for p in t["plans"]]
        templates.append(entry)
    received_at = datetime.now(timezone.utc).isoformat()
    with _ts_templates_lock:
        profiles = dict(_load_ts_templates_store())
        profiles[pid] = {
            "received_at": received_at,
            "sent_at": payload.get("sent_at"),
            "templates": templates,
        }
        TS_TEMPLATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(TS_TEMPLATES_PATH, {"profiles": profiles}, indent=2)
        global _ts_store_cache
        _ts_store_cache = None
    notes = _apply_ts_lists_safely()
    return jsonify({"profile_id": pid, "count": len(templates), "received_at": received_at,
                    "notes": notes})


_ts_templates_lock = threading.Lock()


@app.route("/api/ts-templates")
def api_ts_templates():
    """Read-only dump of user's Target Scheduler ExposureTemplates.

    Reads the TS database when it is readable ("source": "database"). Where it
    is not, as on a server away from the imaging PC, falls back to the lists
    the plugin posted ("source": "plugin")."""
    path = Path(os.path.expandvars(TS_DB_PATH))
    if not path.exists():
        fallback = _plugin_templates_response(path, "")
        if fallback is not None:
            return fallback
        return jsonify({
            "available": False,
            "path": str(path),
            "error": "TS database not found at configured path",
            "templates": [],
        })
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT Id, profileId, name, filtername, defaultexposure, gain, offset, bin "
            "FROM exposuretemplate ORDER BY filtername, name"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        logging.exception("sqlite read failed for %r", _safe_log(path))
        fallback = _plugin_templates_response(path, "")
        if fallback is not None:
            return fallback
        return jsonify({
            "available": False,
            "path": str(path),
            "error": "sqlite read failed",
            "templates": [],
        })
    templates = [{
        "id": r["Id"],
        "profile_id": r["profileId"],
        "name": r["name"],
        "filter": r["filtername"],
        "default_exposure_s": r["defaultexposure"],
        "gain": r["gain"],
        "offset": r["offset"],
        "bin": r["bin"],
    } for r in rows]
    return jsonify({"available": True, "source": "database", "path": str(path), "templates": templates})


PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2}

# Target Scheduler enums use [JsonConverter(typeof(StringEnumConverter))]
# in NINA.Plugin.TargetScheduler.Database.Schema, so JSON values are
# enum NAMES, not ordinal ints. Must match exactly: "Draft" / "Active" /
# "Inactive" / "Closed" and "Low" / "Normal" / "High".
_TS_PRIORITY_NAME = {0: "Low", 1: "Normal", 2: "High"}

# Group-resolution order for state, per docs/specs/ts-project-settings.md
# section 1: "the most running value wins ... one active plan keeps the
# project imaging." Higher rank wins when plans sharing a project_name
# disagree. TS's own enum name string is what the export writes.
STATE_RANK = {"active": 3, "inactive": 2, "draft": 1, "closed": 0}
_TS_STATE_NAME = {"draft": "Draft", "active": "Active", "inactive": "Inactive", "closed": "Closed"}


def _ts_database_version(ts_db_path: str | None = None) -> str:
    """Read PRAGMA user_version from a TS sqlite DB.

    TS's ImportProfile compares this against the export's `DatabaseVersion`
    and refuses imports newer than its own. Reading the live value at sync
    time guarantees a clean import without the "are you sure?" prompt.

    Defaults to the global TS_DB_PATH; pass a destination's own ts_db_path
    when syncing a single multi-rig destination so the version probe hits
    the right rig's NINA install rather than the operator's local one.
    """
    path = Path(os.path.expandvars(ts_db_path or TS_DB_PATH))
    if not path.exists():
        return "0"
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        return str(int(ver))
    except sqlite3.Error:
        return "0"


def _panel_acquired(spec: dict, panel: dict, multi: bool) -> int:
    """Frames already acquired on one exported target. A mosaic whose row
    tracks panels gives each panel its own count (a panel with no entry has
    none); anything else gets the plan's figure, as before."""
    by_panel = spec.get("acquired_by_panel")
    if multi and by_panel is not None:
        return int(by_panel.get(fr.panel_key(panel["row"], panel["col"]), 0))
    return int(spec["acquired"])


def _mosaic_panel_centers(ra_c: float, dec_c: float, fov_w_arcmin: float, fov_h_arcmin: float,
                          rot_deg: float, rows: int, cols: int, overlap_pct: float) -> list[dict]:
    """Return per-panel centers [{row, col, ra_deg, dec_deg}] for an rows×cols mosaic
    anchored on (ra_c, dec_c). Panel stride = fov × (1 − overlap). Row 0 is north."""
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    overlap = max(0.0, min(0.99, float(overlap_pct) / 100.0))
    # stride in degrees (camera-frame)
    stride_w = (fov_w_arcmin / 60.0) * (1.0 - overlap)
    stride_h = (fov_h_arcmin / 60.0) * (1.0 - overlap)
    R = math.radians(rot_deg or 0)
    cosR = math.cos(R)
    sinR = math.sin(R)
    cosD = max(1e-6, math.cos(math.radians(dec_c)))
    panels = []
    for i in range(rows):
        for j in range(cols):
            # Camera-frame offset: row 0 at top (+camy = north when rot=0), col 0 at left
            cx = (j - (cols - 1) / 2.0) * stride_w
            cy = ((rows - 1) / 2.0 - i) * stride_h
            # Rotate into sky (east, north): camera-Y sits at PA east-of-north
            de =  cx * cosR + cy * sinR
            dn = -cx * sinR + cy * cosR
            panels.append({
                "row": i, "col": j,
                "ra_deg": ra_c + de / cosD,
                "dec_deg": dec_c + dn,
            })
    return panels


def _build_ts_export(plans_list: list, gear_data: dict) -> tuple[dict, list]:
    """Group plans by project_name, apply strictest-wins for constraints, expand
    mosaics into per-panel targets, and emit TS-shape project + exposureTemplate
    records. Returns (payload, warnings).

    JSON shape matches Newtonsoft.Json [JsonProperty(MemberSerialization.OptIn)]
    on the TS schema classes (PascalCase property names; StringEnumConverter for
    State/Priority enums; Epoch as ordinal int 0=J2000). ExposurePlans reference
    templates by Id (synthesised here); the importer rewrites IDs through its
    exposureTemplateIdMap so our IDs only need to be stable WITHIN this payload.
    """
    telescopes_by_id = {t["id"]: t for t in gear_data.get("telescopes", [])}
    cameras_by_id = {c["id"]: c for c in gear_data.get("cameras", [])}

    # Group plans into TS Projects. When the user sets `project_name` we
    # honour that as the grouping key. When it's blank, each plan becomes
    # its own Project named after the plan's target: TS docs explicitly
    # call out that "many projects will have only a single target", so
    # giving every untagged plan its own Project is idiomatic and avoids
    # the previous "Unassigned" catch-all dump.
    projects_by_name: dict[str, list] = {}
    for pl in plans_list:
        explicit = (pl.get("project_name") or "").strip()
        if explicit:
            pname = explicit
        else:
            tg = pl.get("target") or {}
            pname = (tg.get("name") or "").strip() or pl.get("id") or "Untitled"
        projects_by_name.setdefault(pname, []).append(pl)

    ts_projects: list[dict] = []
    ts_templates: list[dict] = []
    template_seen: dict[tuple[str, str], dict] = {}
    # Per-entity ID counters. TS's importer keys its remapping dictionaries
    # (projectsIdMap / targetsIdMap / exposurePlansIdMap / exposureTemplateIdMap)
    # by the exported Id. If two entries of the same kind share an Id (e.g.
    # two ExposurePlans both default to 0), Dictionary.Add throws "An item
    # with the same key has already been added. Key: 0". So every entity
    # we emit needs a unique Id within its kind, even though the importer
    # immediately rewrites them.
    project_id_seq, target_id_seq = [0], [0]
    exposure_plan_id_seq, template_id_seq = [0], [0]
    warnings: list[dict] = []

    def _next_id(seq: list[int]) -> int:
        seq[0] += 1
        return seq[0]
    def _next_template_id() -> int: return _next_id(template_id_seq)
    def _next_project_id() -> int: return _next_id(project_id_seq)
    def _next_target_id() -> int: return _next_id(target_id_seq)
    def _next_exposure_plan_id() -> int: return _next_id(exposure_plan_id_seq)

    def _warn(kind: str, pname: str, resolved, group: list, suggested_suffix: str, msg: str) -> None:
        warnings.append({
            "project_name": pname, "kind": kind, "resolved": resolved,
            "plan_ids": [p["id"] for p in group],
            "plan_name": group[0].get("target", {}).get("name") or group[0]["id"],
            "plan_id": group[0]["id"],
            "suggested_name": f"{pname} ({suggested_suffix})",
            "message": msg,
        })

    for pname, group in projects_by_name.items():
        # Reserve the project id up front so each Target can carry it as
        # ProjectId: TS's importer remaps target.ProjectId via
        # projectsIdMap.GetValueOrDefault(); leaving it at 0 produces an
        # orphan target that TS surfaces under an "Unassigned" node.
        proj_id = _next_project_id()
        min_alt = max(float(p.get("min_altitude_deg") or 0) for p in group)
        merid_vals = [float(p.get("meridian_window_min") or 0) for p in group]
        nonzero = [v for v in merid_vals if v > 0]
        meridian = min(nonzero) if nonzero else 0.0
        pri_name = max(group, key=lambda p: PRIORITY_RANK.get(p.get("priority", "normal"), 1)).get("priority", "normal")
        state_vals = [p.get("state") or "active" for p in group]
        resolved_state = max(state_vals, key=lambda s: STATE_RANK.get(s, STATE_RANK["active"]))
        min_time = max(int(p.get("minimum_time_min") or 0) for p in group)

        if len(set(p.get("min_altitude_deg") or 0 for p in group)) > 1:
            _warn("min_altitude", pname, min_alt, group, "strict",
                  f"Min altitude differed across plans; using strictest ({min_alt}°).")
        if len(set(p.get("meridian_window_min") or 0 for p in group)) > 1:
            _warn("meridian_window", pname, meridian, group, "narrow",
                  f"Meridian window differed; using {meridian} min.")
        if len(set(p.get("priority", "normal") for p in group)) > 1:
            _warn("priority", pname, pri_name, group, pri_name,
                  f"Priority differed; using '{pri_name}'.")
        if len(set(state_vals)) > 1:
            _warn("state", pname, resolved_state, group, resolved_state,
                  f"State differed across plans; the most running value wins: '{resolved_state}'.")
        if len(set(int(p.get("minimum_time_min") or 0) for p in group)) > 1:
            _warn("minimum_time", pname, min_time, group, "strict",
                  f"Minimum time differed; using the largest ({min_time} min).")

        ts_targets: list[dict] = []
        project_is_mosaic = False
        for pl in group:
            tg = pl.get("target") or {}
            # % 360 defends against plans written before RA-range validation
            # existed: normalise rather than reject on export, so a
            # pre-existing bad plan doesn't block the whole sync.
            ra_deg = float(tg.get("center_ra_deg") or 0) % 360.0
            dec_deg = float(tg.get("center_dec_deg") or 0)
            rot_deg = float(tg.get("rotation_deg") or 0)
            telescope = telescopes_by_id.get(pl.get("telescope_id") or "")
            camera = cameras_by_id.get(pl.get("camera_id") or "")
            fov_w, fov_h = _fov_arcmin(telescope, camera)

            mosaic = tg.get("mosaic") or {"rows": 1, "cols": 1, "overlap_pct": 0}
            rows = max(1, int(mosaic.get("rows") or 1))
            cols = max(1, int(mosaic.get("cols") or 1))
            overlap = float(mosaic.get("overlap_pct") or 0)

            # Resolve exposure plans for this plan. Each entry tracks the
            # (sub_s, desired, acquired, template_id) so all panels of the
            # mosaic share the same set.
            exp_plan_specs: list[dict] = []
            for row in fr.rows_of(pl):
                desired = fr.desired_for(row, pl)
                if desired <= 0:
                    continue
                fname = str(row.get("filter") or "")
                sub_s = fr.sub_s(row)
                acquired = int(round(float(row.get("actual_hours") or 0) * 3600.0 / max(1.0, sub_s)))
                # Per-panel hours, as frames, when the row tracks them. Only a
                # mosaic reads them; a single target uses the plan's figure.
                by_panel = fr.row_panels(row, pl)
                if by_panel is not None:
                    by_panel = {k: int(round(v * 3600.0 / max(1.0, sub_s))) for k, v in by_panel.items()}
                # Dedupe ExposureTemplate by (camera_id, template name): the
                # row names its template, and gain/offset/etc live on the camera.
                tpl_name = str(row.get("template") or fname)
                key = (pl.get("camera_id") or "", tpl_name)
                if key not in template_seen:
                    filt_cfg = (camera or {}).get("filters", {}).get(fname, {}) if camera else {}
                    tpl = {
                        # Synthetic ID: TS's importer rewrites these through
                        # exposureTemplateIdMap. Only needs to be unique within
                        # this payload so ExposurePlan.ExposureTemplateId can
                        # reference the right template before remapping.
                        "Id": _next_template_id(),
                        "Guid": str(uuid.uuid4()),
                        "Name": tpl_name,
                        "FilterName": fname,
                        "DefaultExposure": float(sub_s),
                        "Gain": int(filt_cfg.get("gain", -1)),
                        "Offset": int(filt_cfg.get("offset", -1)),
                        "bin": int(filt_cfg.get("bin", 1)),       # lowercase per TS schema
                        "ReadoutMode": 0,
                        "TwilightLevel": 1,                        # Astronomical (TS default)
                        "MinutesOffset": 0,
                        "MoonAvoidanceEnabled": False,
                        "MoonAvoidanceSeparation": 0.0,
                        "MoonAvoidanceWidth": 0,
                        "MoonRelaxScale": 0.0,
                        "MoonRelaxMaxAltitude": 0.0,
                        "MoonRelaxMinAltitude": 0.0,
                        "MoonDownEnabled": False,
                        "DitherEvery": 0,
                        "MaximumHumidity": 100.0,
                    }
                    template_seen[key] = tpl
                    ts_templates.append(tpl)
                tpl = template_seen[key]
                exp_plan_specs.append({
                    "sub_s": sub_s,
                    "desired": desired,
                    "acquired": acquired,
                    "acquired_by_panel": by_panel,
                    "template_id": tpl["Id"],
                })

            base_name = tg.get("name") or pl.get("id")
            if rows > 1 or cols > 1:
                if fov_w <= 0 or fov_h <= 0:
                    warnings.append({
                        "project_name": pname, "kind": "mosaic_no_fov", "resolved": "skipped",
                        "plan_ids": [pl["id"]], "plan_id": pl["id"],
                        "plan_name": base_name, "suggested_name": base_name,
                        "message": f"Mosaic '{base_name}' has no valid FOV (pick a telescope + camera). Emitted as single target.",
                    })
                    rows = cols = 1

            panels = _mosaic_panel_centers(ra_deg, dec_deg, fov_w, fov_h, rot_deg, rows, cols, overlap)
            multi = len(panels) > 1
            if multi:
                project_is_mosaic = True
            for panel in panels:
                if multi:
                    panel_idx = panel["row"] * cols + panel["col"] + 1
                    pname_suffix = f" Panel {panel_idx} (R{panel['row']+1}C{panel['col']+1})"
                else:
                    pname_suffix = ""
                tgt_id = _next_target_id()
                exp_plans = [{
                    "Id": _next_exposure_plan_id(),
                    "Guid": str(uuid.uuid4()),
                    "TargetId": tgt_id,    # EF can auto-link via nesting too,
                                            # but setting explicitly is safer.
                    "Exposure": float(spec["sub_s"]),
                    "Desired": int(spec["desired"]),
                    "Acquired": _panel_acquired(spec, panel, multi),
                    "Accepted": _panel_acquired(spec, panel, multi),
                    "IsEnabled": True,
                    "ExposureTemplateId": int(spec["template_id"]),
                } for spec in exp_plan_specs]
                ts_targets.append({
                    "Id": tgt_id,
                    "ProjectId": proj_id,    # link to parent project
                    "Guid": str(uuid.uuid4()),
                    "Name": f"{base_name}{pname_suffix}",
                    "Enabled": True,
                    # Target Scheduler stores RA in HOURS, Dec in degrees.
                    "RA": panel["ra_deg"] / 15.0,
                    "Dec": panel["dec_deg"],
                    "Epoch": 0,                         # 0 = J2000 (NINA enum)
                    "Rotation": rot_deg,
                    "ROI": 100.0,
                    "ExposurePlans": exp_plans,
                    "OverrideExposureOrders": [],
                })

        ts_projects.append({
            "Id": proj_id,
            "Guid": str(uuid.uuid4()),
            "Name": pname,
            "Description": "",
            "State": _TS_STATE_NAME.get(resolved_state, "Active"),
            "Priority": _TS_PRIORITY_NAME.get(PRIORITY_RANK.get(pri_name, 1), "Normal"),
            "CreateDate": datetime.now(timezone.utc).isoformat(),
            "ActiveDate": None,
            "InactiveDate": None,
            "IsMosaic": project_is_mosaic,
            "FlatsHandling": 0,
            "MinimumTime": min_time,
            "MinimumAltitude": float(min_alt),
            "MaximumAltitude": 90.0,
            "UseCustomHorizon": False,
            "HorizonOffset": 0.0,
            "MeridianWindow": int(meridian),
            "FilterSwitchFrequency": 0,
            "DitherEvery": 0,
            "EnableGrader": False,
            "Targets": ts_targets,
        })

    return {"projects": ts_projects, "exposureTemplates": ts_templates}, warnings


@app.route("/api/sync", methods=["POST"])
@_holds_plans_lock
def api_sync():
    """Build a Target Scheduler Import Profile zip from current plans.

    Writes metadata.json, profilePreference.json, exposureTemplates.json, and
    projects.json into a zip. Returns the zip path and any strictest-wins
    warnings so the UI can offer an inline rename and re-sync.

    Closed plans (plan.state == "closed") never sync: TS imports this zip
    as new projects every time (see section 6 of the spec), so a closed
    plan re-syncing here would recreate a project the user already finished
    and deleted from TS. Active, inactive and draft plans are all
    included, each with the matching TS State name, because draft and
    inactive projects still have to exist in TS for a later push to find
    them. Plans with no `state` field (everything written before this
    field existed) are treated as active. The response reports how many
    were skipped as closed.

    Optional `destination_id` (JSON body or `?destination_id=` query param)
    scopes the sync to one entry from /api/destinations: only plans with a
    matching plan.destination_id are bundled, and that destination's own
    ts_db_path / export_path is used instead of the global TS_DB_PATH /
    ZIP_OUTPUT_DIR. Omitting it preserves the pre-multi-rig behaviour (all
    non-closed plans, global paths) so the existing NINA plugin integration
    keeps working unchanged.
    """
    body = request.get_json(silent=True) or {}
    destination_id = body.get("destination_id") or request.args.get("destination_id")

    destination = None
    if destination_id:
        dests = load_destinations().get("destinations") or []
        destination = next((d for d in dests if d.get("id") == destination_id), None)
        if destination is None:
            return jsonify({"error": f"unknown destination_id {destination_id!r}"}), 400

    data = load_plans()
    all_plans = data.get("plans", [])
    closed = [p for p in all_plans if p.get("state") == "closed"]
    pls = [p for p in all_plans if p.get("state") != "closed"]
    if destination is not None:
        pls = [p for p in pls if p.get("destination_id") == destination_id]
    if not pls:
        return jsonify({"error": "no plans to sync"}), 400

    payload, warnings = _build_ts_export(pls, load_gear())

    # Resolve output location + TS db version source. Destination-scoped
    # syncs use the destination's own configured paths (fix: multi-rig
    # sync ignoring destinations); the legacy no-destination call keeps
    # writing to the global ZIP_OUTPUT_DIR / probing the global TS_DB_PATH.
    # destinations.json is never validated on load, so an unknown kind
    # (hand-edited file) must 400 here rather than silently falling
    # through to the global paths.
    try:
        resolved = resolve_destination_paths(destination)
    except ValueError:
        kind = (destination or {}).get("kind")
        return jsonify(
            {"error": f"destination {destination_id!r}: unsupported kind {kind!r}"}
        ), 400
    ts_db_for_version = resolved["ts_db_path"]
    if resolved["zip_path"] is not None:
        zip_path = resolved["zip_path"]
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        download_url = None
    else:
        ZIP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        zip_path = ZIP_OUTPUT_DIR / f"acp-sync-{stamp}.zip"
        download_url = f"/api/sync/download/{zip_path.name}"

    # Match TS's ExportMetadata schema (PascalCase). DatabaseVersion read
    # from the user's TS sqlite means the import goes through silently
    # instead of triggering the "newer/older than current" prompt.
    metadata = {
        "ExportDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "TargetSchedulerVersion": "5.0.0",
        "DatabaseVersion": _ts_database_version(ts_db_for_version),
        "ExportedProfileName": "Astro Coverage Planner",
        "ExportedProfileId": str(uuid.uuid4()),
    }

    # profilePreference.json intentionally omitted: TS skips the import
    # step when the file is absent, which preserves the user's existing
    # profile preferences and avoids EF validation on a default shell.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))
        zf.writestr("exposureTemplates.json", json.dumps(payload["exposureTemplates"], indent=2))
        zf.writestr("projects.json", json.dumps(payload["projects"], indent=2))

    now = datetime.now(timezone.utc).isoformat()
    for pl in pls:
        pl["last_synced_at"] = now
    save_plans({**data, "version": data.get("version", 1), "plans": all_plans})

    return jsonify({
        "ok": True,
        "plan_count": len(pls),
        "skipped_closed_count": len(closed),
        "destination_id": destination_id or None,
        "project_count": len(payload["projects"]),
        "template_count": len(payload["exposureTemplates"]),
        "zip_path": str(zip_path),
        "zip_filename": zip_path.name,
        "download_url": download_url,
        "warnings": warnings,
        "conflicts": warnings,  # alias: the UI inspects this to offer renames
    })


# Filename whitelist matches the timestamped name produced by /api/sync.
# send_from_directory already blocks path traversal; this is defense in depth
# so a stray request can't probe arbitrary names in ZIP_OUTPUT_DIR.
_SYNC_ZIP_NAME_RE = re.compile(r"^acp-sync-\d{8}T\d{6}Z\.zip$")


@app.route("/api/sync/download/<path:filename>")
def api_sync_download(filename: str):
    if not _SYNC_ZIP_NAME_RE.match(filename):
        return jsonify({"error": "invalid filename"}), 400
    return send_from_directory(
        ZIP_OUTPUT_DIR,
        filename,
        as_attachment=True,
        mimetype="application/zip",
    )


# ---------------------------------------------------------------------------
# Scheduled archive rescan
#
# Set ACP_SCAN_CRON to a five-field cron expression and this process runs the
# manifest builder on that schedule. The point is the Docker image: there is no
# cron daemon in it, and adding one means a second process to supervise. A
# scheduler thread costs nothing when the variable is unset, and the scan itself
# runs as a subprocess so a builder crash, or a MemoryError on a huge archive,
# cannot take the web app down with it. Times are the container's local time,
# so set TZ if you care which midnight you get.
# ---------------------------------------------------------------------------

SCAN_STATE_LOCK = threading.Lock()
_scan_state = {
    "running": False,
    "last_start": None,
    "last_finish": None,
    "last_exit_code": None,
    "last_trigger": None,
    "last_error": None,
    "last_error_kind": None,
}
_scan_started_at: float | None = None
_scan_scheduler_thread: threading.Thread | None = None
_scan_scheduler_stop = threading.Event()
_scan_cron_expr: str | None = None


def scan_status() -> dict:
    """Snapshot of the rescan state, safe to hand to jsonify."""
    with SCAN_STATE_LOCK:
        state = dict(_scan_state)
        started = _scan_started_at
    state["cron"] = _scan_cron_expr
    state["scheduled"] = _scan_scheduler_thread is not None
    state["progress"] = _read_scan_progress()
    state["elapsed_seconds"] = (int(time.monotonic() - started)
                                if state["running"] and started is not None else None)
    return state


_SCAN_STAGES = ("listing", "reading", "matching", "writing")


def _scan_progress_path() -> Path:
    return MANIFEST_PATH.with_name("scan_progress.json")


def _read_scan_progress() -> dict | None:
    """The builder's progress file reduced to a stage and a count. A missing,
    half-written or odd file gives None, because the status route must stay
    valid whatever is on disk. Only the two fields cross the wire, so a path
    in a hand-edited file could not leak."""
    try:
        doc = json.loads(_scan_progress_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("stage") not in _SCAN_STAGES:
        return None
    seen = doc.get("files_seen")
    if isinstance(seen, bool) or not isinstance(seen, int) or seen < 0:
        return None
    return {"stage": doc["stage"], "files_seen": seen}


def _clear_scan_progress() -> None:
    """A new scan starts with no progress, so the last scan's final figures do
    not show while the builder is still starting."""
    try:
        _scan_progress_path().unlink()
    except OSError:
        pass


# What a failed scan is told to the caller as. The scanner's own stderr goes
# to the log and no further: the last line of a Python traceback is the
# exception message, and for the common failures that message is a path.
# /api/scan/status has no authentication in the stock configuration.
_SCAN_ERROR_KINDS = (
    ("no valid roots", "no_archive_roots"),
    ("permission denied", "permission_denied"),
    ("no such file", "path_not_found"),
    ("memoryerror", "out_of_memory"),
    ("no space left", "disk_full"),
)
_SCAN_ERROR_MESSAGES = {
    "no_archive_roots": "the scan found no archive roots to read; check the image folders in Settings",
    "permission_denied": "the scan could not read part of the archive",
    "path_not_found": "the scan could not find part of the archive",
    "out_of_memory": "the scan ran out of memory",
    "disk_full": "the scan ran out of disk space",
    "crashed": "the scan failed; see the server log for the reason",
    "could_not_start": "the scan could not be started; see the server log",
}


def _classify_scan_error(text: str) -> str:
    low = (text or "").lower()
    for needle, kind in _SCAN_ERROR_KINDS:
        if needle in low:
            return kind
    return "crashed"


def _record_scan_error(text: str | None, kind: str | None = None) -> None:
    """Keep the shape of the failure, discard its wording."""
    with SCAN_STATE_LOCK:
        if text is None and kind is None:
            _scan_state["last_error_kind"] = None
            _scan_state["last_error"] = None
            return
        k = kind or _classify_scan_error(text or "")
        _scan_state["last_error_kind"] = k
        _scan_state["last_error"] = _SCAN_ERROR_MESSAGES.get(
            k, _SCAN_ERROR_MESSAGES["crashed"])


def _run_scan_subprocess(args: list[str]) -> int:
    """Run the builder and return its exit code. Overridden in tests.

    The encoding is named rather than left to the locale. text=True alone decodes
    the builder's output with the system encoding, which is cp1252 on an English
    Windows install, so an accented character in an archive path came back as
    mojibake in the error tail this records and shows in the scan health panel.
    The builder writes UTF-8 deliberately (see force_utf8_output there), so this
    is the matching half. errors="replace" because a garbled tail is still worth
    reading and a decode error here would lose the reason a scan failed.
    """
    proc = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=_scan_subprocess_env())
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        logging.error("scheduled scan failed (exit %s):\n%s",
                      proc.returncode, "\n".join(tail))
        _record_scan_error("\n".join(tail))
    else:
        _record_scan_error(None)
    return proc.returncode


def _scan_subprocess_env() -> dict:
    """The builder's environment: ours, plus the effective folders and manifest.

    The builder reads the settings file itself when run by hand, but the app
    hands it the values it resolved so both agree for a scan started here. The
    manifest is the path this process reads, not a fresh resolve, so a file
    edited since startup cannot send the scan somewhere the app will not look.
    FITS_ROOTS is left alone when nothing is effective, so the builder keeps
    its own fallback root for the maintainer's setup.
    """
    env = dict(os.environ)
    roots = acp_settings.effective("fits_roots")
    if roots:
        env["FITS_ROOTS"] = ";".join(roots)
    env["MANIFEST_PATH"] = str(MANIFEST_PATH)
    return env


def _packaged() -> bool:
    return (os.environ.get("ACP_PACKAGED") or "").strip() == "1"


def _interpreter_args() -> list[str]:
    """The interpreter flags a child Python should repeat. The bundled Windows
    Python reads a ._pth file, which makes it ignore PYTHONUTF8 and
    PYTHONDONTWRITEBYTECODE, so a child started with just sys.executable would
    lose -X utf8 and -B. Repeating the parent's flags keeps them. A normal
    install has neither flag, or has them through the environment, and gets
    the same child as before."""
    args = []
    if sys.flags.utf8_mode:
        args += ["-X", "utf8"]
    if sys.flags.dont_write_bytecode:
        args.append("-B")
    return args


def claim_scan(trigger: str = "cron") -> str:
    """Take the one scan slot, under the same lock that guards the state.

    Returns "claimed", "running" (a scan is already in flight) or "no_roots".
    Two builders writing the same manifest and the same scan cache at once
    would race each other's output, and on a big archive the overlap is likely
    rather than theoretical, so a second start is refused rather than queued.

    A scan from the page, and any scan in packaged mode, refuses to start with
    no folders effective: the builder's fallback root belongs to the
    maintainer's machine and must never be scanned by accident. Source and
    Docker scheduled scans behave as before.
    """
    global _scan_started_at
    must_have_roots = trigger == "page" or _packaged()
    with SCAN_STATE_LOCK:
        # Read inside the lock: a PUT that clears the folders takes the same
        # lock, so it cannot slip between this check and the claim.
        has_roots = bool(acp_settings.effective("fits_roots"))
        if _scan_state["running"]:
            logging.warning("scan already running; skipping %s trigger", trigger)
            return "running"
        if must_have_roots and not has_roots:
            logging.warning("no image folders set; skipping %s scan", trigger)
            return "no_roots"
        _scan_started_at = time.monotonic()
        # Before the answer goes out, so a poll never sees the last scan's figures.
        _clear_scan_progress()
        _scan_state["running"] = True
        _scan_state["last_start"] = datetime.now().isoformat(timespec="seconds")
        _scan_state["last_finish"] = None
        _scan_state["last_trigger"] = trigger
    return "claimed"


def run_scan_now(trigger: str = "cron") -> bool:
    """Claim the scan slot and run one scan in this thread.

    Returns False when no scan ran: one was already in flight, or there were
    no folders to read where that is required (see claim_scan).
    """
    if claim_scan(trigger) != "claimed":
        return False
    run_claimed_scan()
    return True


def run_claimed_scan() -> None:
    """Run the scan whose slot claim_scan took. The route claims first so it
    can answer 202 or 409 at once, then hands this to a worker thread."""
    code = None
    before = None
    try:
        before = _manifest_snapshot() if activity.enabled() else None
        scan_args = [sys.executable, *_interpreter_args(), str(SCAN_SCRIPT_PATH)]
        logging.info("starting the archive scan: %s", " ".join(scan_args))
        code = _run_scan_subprocess(scan_args)
    except Exception as e:
        logging.error("scheduled scan could not start: %s: %s", type(e).__name__, e)
        _record_scan_error(f"{type(e).__name__}: {e}", kind="could_not_start")
    finally:
        with SCAN_STATE_LOCK:
            _scan_state["running"] = False
            _scan_state["last_finish"] = datetime.now().isoformat(timespec="seconds")
            _scan_state["last_exit_code"] = code
    _run_activity(lambda: _record_scan_activity(before, code == 0))


def _manifest_snapshot() -> dict | None:
    """Hours per filter for each target, read straight from the manifest file
    (not the cache, which can hold the old file for a second). None when
    there is no readable manifest to compare against."""
    try:
        doc = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out: dict = {}
    for t in (doc.get("targets") if isinstance(doc, dict) else None) or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("target_id"))
        filters = {}
        for fname, d in (t.get("filters") or {}).items():
            hours = d.get("total_hours") if isinstance(d, dict) else None
            if isinstance(hours, (int, float)) and not isinstance(hours, bool):
                filters[str(fname)] = float(hours)
        out[tid] = {"name": ", ".join(str(o) for o in (t.get("objects") or [])) or tid,
                    "filters": filters}
    return out


def _carried(previous, *path) -> float:
    node = (previous or {}).get("data") or {}
    for step in path:
        node = node.get(step) if isinstance(node, dict) else None
    return float(node) if isinstance(node, (int, float)) and not isinstance(node, bool) else 0.0


def _record_scan_activity(before: dict | None, ok: bool) -> None:
    night = activity.current_night()
    if not ok:
        with SCAN_STATE_LOCK:
            kind = _scan_state.get("last_error_kind") or "crashed"
            message = _scan_state.get("last_error") or _SCAN_ERROR_MESSAGES["crashed"]

        def merge_error(previous):
            return {"title": f"Coverage scan failed: {message}"[:200],
                    "data": {"kind": kind, "failures": int(_carried(previous, "failures")) + 1}}

        activity.record_merged(
            merge_error, type="scan_error", key=f"scan_error:{night}", night=night,
            severity="error", needs_you=True, resolve_key="scan", title="scan", data={})
        return

    after = _manifest_snapshot()
    added = removed = 0
    moves = []
    if before is not None and after is not None:
        added = len(set(after) - set(before))
        removed = len(set(before) - set(after))
        for tid in set(before) | set(after):
            old = (before.get(tid) or {}).get("filters", {})
            new = (after.get(tid) or {}).get("filters", {})
            name = (after.get(tid) or before.get(tid) or {}).get("name", tid)
            for fname in set(old) | set(new):
                delta = new.get(fname, 0.0) - old.get(fname, 0.0)
                if abs(delta) > activity.MOVED_H:
                    moves.append((tid, name, fname, old.get(fname, 0.0), new.get(fname, 0.0), delta))

    def merge_finished(previous):
        total_added = int(_carried(previous, "added")) + added
        total_removed = int(_carried(previous, "removed")) + removed
        bits = [f"{total_added} target{'s' if total_added != 1 else ''} added",
                f"{total_removed} removed"] if total_added or total_removed else ["no target changes"]
        return {"title": "Coverage scan finished: " + ", ".join(bits),
                "data": {"scans": int(_carried(previous, "scans")) + 1,
                         "added": total_added, "removed": total_removed},
                "resolves": ["scan"]}

    activity.record_merged(merge_finished, type="scan_finished", key=f"scan_finished:{night}",
                           night=night, title="scan", data={})
    for tid, name, fname, was, now, delta in moves:
        def merge_move(previous, name=name, fname=fname, was=was, now=now, delta=delta):
            prior = (previous or {}).get("data") or {}
            total = _carried(previous, "delta_h") + delta
            if abs(total) <= activity.MOVED_H:
                return None
            first = prior.get("from_h", was)
            return {"title": f"{name}: {fname} {total:+.2f} h",
                    "data": {"filter": fname, "delta_h": round(total, 4),
                             "from_h": first, "to_h": round(now, 4)}}

        activity.record_merged(
            merge_move, type="coverage_changed", key=f"coverage:{tid}:{fname}:{night}",
            night=night, refs={"target": name, "filter": fname}, title="move", data={})


def _next_fire_time(expr: str, now: datetime) -> datetime:
    """The next local time ``expr`` fires after ``now``."""
    from croniter import croniter

    return croniter(expr, now).get_next(datetime)


def _scan_scheduler_loop(expr: str, poll_seconds: float = 30.0) -> None:
    while not _scan_scheduler_stop.is_set():
        now = datetime.now()
        try:
            next_at = _next_fire_time(expr, now)
        except Exception as e:
            logging.error("ACP_SCAN_CRON became unusable (%s: %s); scheduler stopping",
                          type(e).__name__, e)
            return
        while not _scan_scheduler_stop.is_set():
            remaining = (next_at - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            # Wake up regularly rather than sleeping until the fire time: it
            # keeps shutdown responsive, and a laptop that suspends across the
            # scheduled minute re-reads the clock instead of firing hours late.
            _scan_scheduler_stop.wait(min(remaining, poll_seconds))
        if _scan_scheduler_stop.is_set():
            return
        run_scan_now("cron")


def start_scan_scheduler(expr: str | None = None) -> bool:
    """Start the rescan thread if ACP_SCAN_CRON is set and valid.

    Returns True if a thread was started. Calling it again while a scheduler is
    already running is a no-op, so an import that happens twice (or a test that
    calls it twice) cannot end up with two threads racing the same builder.
    """
    global _scan_scheduler_thread, _scan_cron_expr
    expr = (expr if expr is not None else acp_settings.effective("scan_cron") or "").strip()
    if not expr:
        return False
    try:
        from croniter import croniter
    except ImportError:
        logging.error("ACP_SCAN_CRON is set but croniter is not installed; "
                      "no rescan will be scheduled (pip install -r requirements.txt)")
        return False
    if not croniter.is_valid(expr):
        logging.error("ACP_SCAN_CRON=%r is not a valid cron expression; "
                      "no rescan will be scheduled", expr)
        return False
    with SCAN_STATE_LOCK:
        if _scan_scheduler_thread is not None and _scan_scheduler_thread.is_alive():
            logging.warning("scan scheduler already running; ignoring restart")
            return False
        _scan_cron_expr = expr
        _scan_scheduler_stop.clear()
        _scan_scheduler_thread = threading.Thread(
            target=_scan_scheduler_loop, args=(expr,),
            name="acp-scan-scheduler", daemon=True)
        _scan_scheduler_thread.start()
    logging.info("scheduled archive rescan on %r (next run computed from local time)", expr)
    return True


def stop_scan_scheduler() -> None:
    """Stop the scheduler thread. Used by tests and by a clean shutdown."""
    global _scan_scheduler_thread, _scan_cron_expr
    _scan_scheduler_stop.set()
    thread = _scan_scheduler_thread
    if thread is not None:
        thread.join(timeout=5)
    _scan_scheduler_thread = None
    _scan_cron_expr = None
    # Leave the module startable again: a set stop event would make the next
    # start_scan_scheduler() spawn a thread that returns immediately.
    _scan_scheduler_stop.clear()


@app.route("/api/scan/status")
def api_scan_status():
    """Last start, last finish, last exit code, and whether a scan is running."""
    return jsonify(scan_status())


# --- Settings (docs/specs/windows-download-and-setup.md, sections 1 and 2) ----
#
# The settings file holds the image folders and the shortcuts answer. The
# environment still wins, so a Docker deployment sets FITS_ROOTS and sees the
# page lock. Routes that reveal a local path or change state are "restricted":
# see _restricted_refusal.

_LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"})
_SETTINGS_WRITABLE_KEYS = ("fits_roots", "shortcuts")


def _shortcuts_available() -> bool:
    """Only a packaged Windows or Linux build has an app folder to point a
    shortcut at. A source install and Docker have none, and Mac has none."""
    return _packaged() and acp_shortcuts.available()


def _client_is_loopback() -> bool:
    """A request from this machine that no proxy relayed. A proxy on the same
    machine makes every client look like 127.0.0.1, so any X-Forwarded-For
    header means the address says nothing."""
    return (request.remote_addr in _LOOPBACK_ADDRS
            and "X-Forwarded-For" not in request.headers)


def _host_is_loopback() -> bool:
    """The Host header names this machine. A page on another site that
    rebinds its DNS name to 127.0.0.1 still sends its own name here."""
    raw = request.headers.get("Host", "").strip().lower()
    if raw.startswith("["):
        host = raw[1:].split("]", 1)[0]
    elif raw.count(":") == 1:
        host = raw.split(":", 1)[0]
    else:
        host = raw
    return host in _LOOPBACK_HOSTS


def _authorised_client() -> bool:
    """With a token set the gate has already demanded a bearer header or the
    session cookie, so the request is authorised. Without one, only a
    loopback client addressing us by a loopback name is."""
    if _api_auth_token():
        return True
    return _client_is_loopback() and _host_is_loopback()


@app.before_request
def _packaged_host_guard():
    """DNS rebinding: in packaged mode, answer only to a loopback Host name.
    A page on another site that rebinds its name to 127.0.0.1 still sends its
    own name here. A packaged build has no reverse proxy, so the check is safe
    on every route. With a token set, _api_gate has already demanded a bearer
    header or the session cookie by the time this runs, so only the tokenless
    case needs it. A source install checks the restricted routes only (see
    _restricted_refusal), because a proxy with a hostname would lock out its
    own users."""
    if _packaged() and not _api_auth_token() and not _host_is_loopback():
        return jsonify({"error": "bad_host"}), 400
    return None


def _restricted_refusal():
    """Refuse a restricted route that takes a JSON body, or return None.

    Order: who is asking, then which name they used, then the content type,
    then the Origin. A browser form cannot send application/json without a
    preflight ACP never answers, so the type check stops cross-site writes
    even from a page that gets past the Origin test.
    """
    if not _api_auth_token():
        if not _client_is_loopback():
            return jsonify({"error": "forbidden"}), 403
        if not _host_is_loopback():
            return jsonify({"error": "bad_host"}), 400
    if request.mimetype != "application/json":
        return jsonify({"error": "unsupported_media_type"}), 415
    return _origin_refused()


def _first_run(resolved: dict | None = None) -> bool:
    """No image folders anywhere and no manifest file: nothing to show yet.
    A manifest with no folders is a user who built it by hand, not first run."""
    resolved = resolved or acp_settings.resolve_all()
    return not resolved["fits_roots"]["value"] and not MANIFEST_PATH.exists()


def _settings_state():
    """The settings file, its path and any load error, read fresh."""
    path = acp_settings.settings_file()
    data, error = acp_settings.load_file(path)
    return path, data, error


def _settings_view(authorised: bool) -> dict:
    path, data, error = _settings_state()
    resolved = acp_settings.resolve_all(file_data=data)
    keys = {}
    for name, info in resolved.items():
        entry = {"source": info["source"], "locked": info["locked"]}
        hidden = name in ("fits_roots", "manifest_path") and not authorised
        if not hidden:
            entry["value"] = info["value"]
        keys[name] = entry
    if authorised:
        # The manifest the app is reading, which a restart would change.
        keys["manifest_path"]["value"] = str(MANIFEST_PATH)
    view = {
        "version": VERSION,
        "first_run": _first_run(resolved),
        "targets": (load_manifest() or {}).get("total_targets", 0),
        "writable": acp_settings.is_writable(path),
        "error": "settings_file_corrupt" if error == "corrupt" else error,
        "shortcuts": {"available": _shortcuts_available(),
                      "value": acp_settings.shortcuts_value(data)},
        "packaged": _packaged(),
        "keys": keys,
    }
    if authorised:
        view["data_dir"] = str(acp_settings.data_dir())
    return view


def _save_settings(path, updates: dict):
    """Write the file, refusing a change of image folders while a scan runs.
    Holds the lock the scan claim uses, so a scan cannot start between the
    check and the write."""
    try:
        with SCAN_STATE_LOCK:
            if "fits_roots" in updates and _scan_state["running"]:
                return jsonify({"error": "scan_running"}), 409
            acp_settings.save(path, updates)
    except OSError:
        logging.exception("could not write the settings file")
        return jsonify({"error": "write_failed"}), 500
    return None


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(_settings_view(_authorised_client()))


@app.route("/api/settings", methods=["PUT"])
def api_put_settings():
    refused = _restricted_refusal()
    if refused:
        return refused
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return jsonify({"error": "bad_request"}), 400
    if any(key not in _SETTINGS_WRITABLE_KEYS for key in body):
        return jsonify({"error": "unknown_key"}), 400
    path, _, _ = _settings_state()
    if not acp_settings.is_writable(path):
        return jsonify({"error": "not_writable"}), 409

    updates: dict = {}
    if "shortcuts" in body:
        value = body["shortcuts"]
        if value is not None and not isinstance(value, bool):
            return jsonify({"error": "bad_request"}), 400
        if not _shortcuts_available():
            return jsonify({"error": "unavailable"}), 400
        updates["shortcuts"] = value
        if value is not None:
            # The files change first, so a failure leaves the offer open
            # instead of recording an answer that did not happen.
            root = acp_shortcuts.app_root(REPO_ROOT)
            try:
                if value:
                    acp_shortcuts.create(root)
                    updates["shortcuts_app_dir"] = str(root)
                else:
                    acp_shortcuts.remove()
                    updates["shortcuts_app_dir"] = None
            except acp_shortcuts.ShortcutError as exc:
                logging.error("shortcuts: %s", exc)
                return jsonify({"error": "shortcuts_failed"}), 500
    if "fits_roots" in body:
        raw = body["fits_roots"]
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            return jsonify({"error": "bad_request"}), 400
        if acp_settings.resolve_all()["fits_roots"]["locked"]:
            return jsonify({"error": "locked_by_env", "key": "fits_roots"}), 409
        roots, code = acp_settings.validate_roots(raw)
        if code:
            return jsonify({"error": code}), 400
        warnings, code, index = _check_roots_on_disk(roots)
        if code:
            return jsonify({"error": code, "index": index}), 400
        updates["fits_roots"] = roots

    failed = _save_settings(path, updates)
    if failed:
        return failed
    view = _settings_view(True)
    if "fits_roots" in updates:
        view["warnings"] = warnings
    return jsonify(view)


def _check_roots_on_disk(roots: list[str]):
    """The disk checks for a whole list, run side by side so one sleeping NAS
    costs five seconds and not five per folder. Returns the warnings, the first
    blocking code (or None) and the index of the folder it belongs to. Only
    folders new in this request touch the disk. A folder that is already saved
    is kept as it is, so Remove and Add stay quick while a NAS is off."""
    saved = {acp_settings._compare_key(p) for p in acp_settings.effective("fits_roots")}
    results: list = [None] * len(roots)

    def check(i: int, text: str) -> None:
        fresh = acp_settings._compare_key(text) not in saved
        if not fresh:
            # Checked when it was added. An unplugged drive or a sleeping NAS
            # must not stop the user changing the other folders, and a save
            # must not wait five seconds on it either.
            results[i] = {"code": "ok",
                          "warnings": acp_settings.path_warnings(text, roots[:i] + roots[i + 1:])}
            return
        code = acp_settings.check_directory(text)
        out = {"code": code, "warnings": []}
        if code == "ok":
            out["warnings"] = acp_settings.path_warnings(text, roots[:i] + roots[i + 1:])
            if acp_settings.has_images(text) is False:
                out["warnings"].append("no_images_seen")
        results[i] = out

    threads = [threading.Thread(target=check, args=(i, text), daemon=True)
               for i, text in enumerate(roots)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + acp_settings.READ_TIMEOUT + acp_settings.IMAGE_WALK_SECONDS + 1
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    warnings = []
    for i, out in enumerate(results):
        if out is None:
            return warnings, "timed_out", i
        if out["code"] != "ok":
            return warnings, out["code"], i
        warnings.extend({"index": i, "code": w} for w in out["warnings"])
    return warnings, None, None


@app.route("/api/settings/check-path", methods=["POST"])
def api_settings_check_path():
    """Judge one folder against the saved list and write nothing. The
    restricted-route test comes first and the syntax test comes before any
    disk access: on Windows a UNC path makes this machine open an SMB
    connection and offer its login hash."""
    refused = _restricted_refusal()
    if refused:
        return refused
    body = request.get_json(silent=True)
    raw = body.get("path") if isinstance(body, dict) else None
    if not isinstance(raw, str):
        return jsonify({"error": "bad_request"}), 400
    return jsonify(acp_settings.check_path(raw, acp_settings.effective("fits_roots")))


@app.route("/api/settings/adopt-env", methods=["POST"])
def api_settings_adopt_env():
    """Copy the folders FITS_ROOTS names into the settings file. The variable
    still wins until the user removes it, so this changes nothing they can
    see today; it makes the file ready for the day they do."""
    refused = _restricted_refusal()
    if refused:
        return refused
    path, _, _ = _settings_state()
    if not acp_settings.is_writable(path):
        return jsonify({"error": "not_writable"}), 409
    raw = acp_settings.parse_roots(os.environ.get("FITS_ROOTS") or "")
    if not raw:
        return jsonify({"error": "nothing_to_adopt"}), 400
    roots, code = acp_settings.validate_roots(raw)
    if code:
        return jsonify({"error": code}), 400
    failed = _save_settings(path, {"fits_roots": roots})
    if failed:
        return failed
    return jsonify(_settings_view(True))


@app.route("/setup")
def setup_page():
    """The first-run and settings page. It carries no local path: the script
    asks /api/settings, which hides paths from anyone not authorised."""
    return render_template("setup.html", version=VERSION)


@app.route("/api/scan/start", methods=["POST"])
def api_scan_start():
    """Start one scan from the setup page. The slot is claimed before the
    answer, so two quick clicks cannot both get 202, then a worker thread runs
    it. If the thread cannot start the slot is given back, or running would
    stay true for good."""
    refused = _restricted_refusal()
    if refused:
        return refused
    claim = claim_scan("page")
    if claim == "running":
        return jsonify({"error": "scan_running"}), 409
    if claim == "no_roots":
        return jsonify({"error": "no_archive_roots"}), 400
    try:
        threading.Thread(target=run_claimed_scan, name="acp-scan-page", daemon=True).start()
    except Exception:
        logging.exception("could not start the scan thread")
        with SCAN_STATE_LOCK:
            _scan_state["running"] = False
        _record_scan_error(None, kind="could_not_start")
        return jsonify({"error": "could_not_start"}), 500
    return jsonify({"status": "started"}), 202


# --- Catalogue overlays (spec 2.6) ----------------------------------------------
#
# data/catalogs.json is made from VizieR tables by scripts/fetch_catalogs.py and
# is not shipped, because the licence terms were not checked. The button runs
# that script the way a scan runs: one at a time, as a subprocess, with a status
# that carries a kind and never a path or the script's own words.

CATALOGUE_SCRIPT_PATH = REPO_ROOT / "scripts" / "fetch_catalogs.py"
_CATALOGUE_NO_NETWORK = 3
_catalogue_state = {"running": False, "finished": False, "error_kind": None,
                    "last_finish": None}


def catalogue_status() -> dict:
    with SCAN_STATE_LOCK:
        state = dict(_catalogue_state)
    state["present"] = CATALOGS_PATH.exists()
    return state


def _run_catalogue_subprocess(args: list[str]) -> int:
    """Run the fetch script and return its exit code. Overridden in tests."""
    env = dict(os.environ)
    env["CATALOGS_PATH"] = str(CATALOGS_PATH)
    proc = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=1800)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        logging.error("catalogue download failed (exit %s):\n%s",
                      proc.returncode, "\n".join(tail))
    return proc.returncode


def _run_catalogue_fetch() -> None:
    kind = None
    try:
        code = _run_catalogue_subprocess([sys.executable, *_interpreter_args(),
                                          str(CATALOGUE_SCRIPT_PATH)])
        if code == _CATALOGUE_NO_NETWORK:
            kind = "network"
        elif code != 0:
            kind = "crashed"
    except subprocess.TimeoutExpired:
        logging.error("catalogue download ran past its time limit")
        kind = "timed_out"
    except Exception as e:
        logging.error("catalogue download could not run: %s: %s", type(e).__name__, e)
        kind = "could_not_start"
    finally:
        with SCAN_STATE_LOCK:
            _catalogue_state.update(running=False, finished=kind is None, error_kind=kind,
                                    last_finish=datetime.now().isoformat(timespec="seconds"))


@app.route("/api/catalogues/fetch", methods=["POST"])
def api_catalogues_fetch():
    refused = _restricted_refusal()
    if refused:
        return refused
    with SCAN_STATE_LOCK:
        if _catalogue_state["running"]:
            return jsonify({"error": "fetch_running"}), 409
        _catalogue_state.update(running=True, finished=False, error_kind=None)
    try:
        threading.Thread(target=_run_catalogue_fetch, name="acp-catalogue-fetch",
                         daemon=True).start()
    except Exception:
        logging.exception("could not start the catalogue download thread")
        with SCAN_STATE_LOCK:
            _catalogue_state.update(running=False, error_kind="could_not_start")
        return jsonify({"error": "could_not_start"}), 500
    return jsonify({"status": "started"}), 202


@app.route("/api/catalogues/status")
def api_catalogues_status():
    return jsonify(catalogue_status())


# --- Quitting a packaged ACP (spec 3.3) ---------------------------------------
#
# The launcher owns the server, the tray and the lock, so it registers a
# shutdown function here. The tray's Quit item calls the same function. It
# returns False while a scan runs, and the route answers 409 for that.

_quit_hook = None


def set_quit_hook(hook) -> None:
    """Called by launcher.py with its shutdown function."""
    global _quit_hook
    _quit_hook = hook


@app.route("/api/quit", methods=["POST"])
def api_quit():
    """Stop a packaged ACP. The route exists only when ACP_PACKAGED=1; anywhere
    else it answers 404, as an unknown route does."""
    if not _packaged():
        return jsonify({"error": "not_found"}), 404
    refused = _restricted_refusal()
    if refused:
        return refused
    if _quit_hook is None:
        return jsonify({"error": "unavailable"}), 503
    if not _quit_hook():
        return jsonify({"error": "scan_running"}), 409
    return jsonify({"status": "stopping"})



# --- Imaging balancer (docs/specs/imaging-balancer.md) ------------------------
#
# The decision is balancer.decide, a pure function. This section feeds it
# (plans, Activity hours, commitment progress), writes what it decides and
# reports in Activity. Commitments are vote promises on a plan; the routes
# here make and remove them.

BALANCER_LOCK = threading.RLock()
BALANCER_POLL_SECONDS = 30.0
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _balancer_path() -> Path:
    raw = (os.environ.get("BALANCER_PATH") or "").strip()
    return Path(raw) if raw else PLANS_PATH.with_name("balancer.json")


@app.route("/api/plans/<plan_id>/commitments", methods=["POST"])
@_holds_plans_lock
def api_add_commitment(plan_id: str):
    """Mark a plan as the vote winner for a month. Copies `vote_goals` and the
    vote baseline into a new commitment and sets the project's role to
    pretty. `clear_other_votes: true` also moves every other plan out of the
    vote section, which is what ends a round."""
    import shooting_publish as sp
    payload = request.get_json(silent=True) or {}
    month = payload.get("month") if isinstance(payload, dict) else None
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        return jsonify({"error": "month must be YYYY-MM"}), 400
    data = load_plans()
    plans = data.get("plans", [])
    plan = next((p for p in plans if p.get("id") == plan_id), None)
    if plan is None:
        return jsonify({"error": "not found"}), 404
    goals = plan.get("vote_goals")
    if not isinstance(goals, dict) or not goals:
        return jsonify({"error": "the plan has no vote goals; set them in the Public page section first"}), 400
    if any(c.get("month") == month and not c.get("lapsed_at")
           for c in plan.get("commitments") or [] if isinstance(c, dict)):
        return jsonify({"error": f"the plan already has a commitment for {month}"}), 400
    # A lapsed commitment for the month is replaced: adding it again is how
    # the user revives one.
    if plan.get("commitments"):
        plan["commitments"] = [c for c in plan["commitments"]
                               if not (isinstance(c, dict) and c.get("month") == month)]
    baseline = plan.get("vote_baseline")
    if not isinstance(baseline, dict):
        baseline = sp.current_done_hours(plan, load_manifest(), load_gear())
    now = datetime.now(timezone.utc).isoformat()
    plan.setdefault("commitments", []).append({
        "kind": "vote", "month": month,
        "goals": copy.deepcopy(goals), "baseline": copy.deepcopy(baseline),
        "set_at": now[:10],
    })
    apply_project_settings(plans, balancer.project_key(plan), {"balance_role": "pretty"}, now)
    cleared = 0
    if payload.get("clear_other_votes") is True:
        for other in plans:
            if other is not plan and other.get("public_section") == "vote":
                other["public_section"] = "current"
                other.pop("vote_baseline", None)
                other["updated_at"] = now
                cleared += 1
    plan["updated_at"] = now
    save_plans({**data, "version": data.get("version", 1), "plans": plans})
    return jsonify({**_with_goals_view(plan), "_cleared_other_votes": cleared}), 201


@app.route("/api/plans/<plan_id>/commitments/<month>", methods=["DELETE"])
@_holds_plans_lock
def api_remove_commitment(plan_id: str, month: str):
    data = load_plans()
    plans = data.get("plans", [])
    plan = next((p for p in plans if p.get("id") == plan_id), None)
    if plan is None:
        return jsonify({"error": "not found"}), 404
    kept = [c for c in plan.get("commitments") or [] if not (isinstance(c, dict) and c.get("month") == month)]
    if len(kept) == len(plan.get("commitments") or []):
        return jsonify({"error": f"no commitment for {month}"}), 404
    if kept:
        plan["commitments"] = kept
    else:
        plan.pop("commitments", None)
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_plans({**data, "version": data.get("version", 1), "plans": plans})
    return jsonify(_with_goals_view(plan))


SEASON_WINDOW_NIGHTS = 14
SEASON_MIN_HOURS = 1.0
LAPSE_QUIET_NIGHTS = 21


def _season_hours(plan: dict, night: str) -> float | None:
    """Hours a night the plan's target spends above its site's minimum altitude
    in astronomical darkness, averaged over the next SEASON_WINDOW_NIGHTS
    nights. Read from the year visibility the Inventory uses (one value for
    the 15th of each month), interpolated to the middle of the window. None
    when the target has no coordinates, there is no site, or the calculation
    fails (for example astropy cannot fetch its Earth orientation data)."""
    tg = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    try:
        ra = float(tg["center_ra_deg"]) % 360.0
        dec = float(tg["center_dec_deg"])
        sites = load_sites().get("sites") or DEFAULT_SITES["sites"]
        wanted = plan.get("site_id")
        s = next((x for x in sites if wanted and x.get("id") == wanted), sites[0])
        site = {"lat": float(s["lat"]), "lon": float(s["lon"]),
                "elev_m": float(s.get("elev_m") or 0.0),
                "min_alt_deg": float(s.get("min_alt_deg") or 30.0)}
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    mid = balancer.date.fromisoformat(night) + balancer.timedelta(days=SEASON_WINDOW_NIGHTS // 2)

    def mid_month(year: int, month: int) -> float:
        key = _site_cache_key(site) + (round(ra, 4), round(dec, 4), year)
        bins = _visibility_cache.get(key)
        if bins is None:
            bins = compute_year_visibility(
                [{"target_id": 0, "center_ra_deg": ra, "center_dec_deg": dec}],
                lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
                min_alt_deg=site["min_alt_deg"], year=year)
            _visibility_cache[key] = bins
        return float(bins[0][month - 1]["hours_above_min"])

    try:
        if mid.day >= 15:
            a = balancer.date(mid.year, mid.month, 15)
            b = balancer.date(mid.year + (mid.month == 12), mid.month % 12 + 1, 15)
        else:
            a = balancer.date(mid.year - (mid.month == 1), (mid.month - 2) % 12 + 1, 15)
            b = balancer.date(mid.year, mid.month, 15)
        ha, hb = mid_month(a.year, a.month), mid_month(b.year, b.month)
    except Exception:
        logging.exception("balancer: could not work out the season for plan %s", plan.get("id"))
        return None
    return ha + (hb - ha) * (mid - a).days / max(1, (b - a).days)


def _balancer_season(plans: list[dict], night: str) -> dict:
    """Plan id to the reason its unmet vote commitment should lapse. A target
    that spends under SEASON_MIN_HOURS a night above the site's minimum
    altitude in darkness over the next two weeks is "out of season". When that
    cannot be calculated, a plan that is active and not on hold, has had its
    commitment for LAPSE_QUIET_NIGHTS nights and gained no hours in that time
    lapses as "no hours in the last 21 nights"."""
    cur_month = night[:7]
    first = (balancer.date.fromisoformat(night)
             - balancer.timedelta(days=LAPSE_QUIET_NIGHTS)).isoformat()
    recent = None
    out: dict = {}
    for p in plans:
        if not isinstance(p, dict) or not isinstance(p.get("commitments"), list):
            continue
        open_ones = [c for c in p["commitments"]
                     if isinstance(c, dict) and c.get("kind", "vote") == "vote"
                     and isinstance(c.get("month"), str) and c["month"] <= cur_month
                     and not c.get("met_at") and not c.get("lapsed_at")]
        if not open_ones:
            continue
        hours = _season_hours(p, night)
        if hours is not None:
            if hours < SEASON_MIN_HOURS:
                out[p.get("id")] = "out of season"
            continue
        if p.get("balance_hold") is True or p.get("state") != "active":
            continue
        if any(str(c.get("set_at") or "9999") > first for c in open_ones):
            continue
        if recent is None:
            recent = activity.progress_hours(first, night)
        if not recent.get(p.get("id")):
            out[p.get("id")] = f"no hours in the last {LAPSE_QUIET_NIGHTS} nights"
    return out


def _balancer_inputs(plans: list[dict], night: str) -> dict:
    """Everything `decide` needs beyond plans, settings and memory."""
    import shooting_publish as sp
    month = night[:7]
    project_of = {p.get("id"): balancer.project_key(p) for p in plans if isinstance(p, dict)}
    month_hours: dict = {}
    for pid, hours in activity.progress_hours(f"{month}-01", f"{month}-31").items():
        if pid in project_of:
            month_hours[project_of[pid]] = month_hours.get(project_of[pid], 0.0) + hours
    first = (balancer.date.fromisoformat(night) - balancer.timedelta(days=balancer.RECENT_NIGHTS)).isoformat()
    recent_hours: dict = {}
    for pid, hours in activity.progress_hours(first, night).items():
        if pid in project_of:
            recent_hours[project_of[pid]] = recent_hours.get(project_of[pid], 0.0) + hours
    manifest, gear = load_manifest(), load_gear()
    done = {p["id"]: sp.current_done_hours(p, manifest, gear)
            for p in plans if isinstance(p, dict) and p.get("commitments")}
    return {"month_hours": month_hours, "done_hours": done, "canon": sp.canon_filter,
            "newest_report_night": activity.newest_progress_night(),
            "keep_nights": activity.keep_nights(), "recent_hours": recent_hours,
            "season": _balancer_season(plans, night)}


def _balancer_decide(plans: list[dict], state: dict, night: str, late: bool = False) -> dict:
    inp = _balancer_inputs(plans, night)
    return balancer.decide(
        plans, inp["month_hours"], inp["done_hours"], state["settings"], state["memory"], night,
        newest_report_night=inp["newest_report_night"], keep_nights=inp["keep_nights"],
        canon=inp["canon"], late=late, recent_hours=inp["recent_hours"], season=inp["season"])


def _balancer_note(change: dict, night: str) -> dict:
    d = balancer.date.fromisoformat(night)
    when = f"{d.day} {balancer.MONTH_NAMES[d.month - 1][:3]}"
    return {"rule": change["rule"], "at": night,
            "text": f"Balancer set this to {change['to'].capitalize()} on {when}: {change['note']}"}


_balancer_attempted: dict = {}


def _balancer_skipped(path: Path, state: dict, night: str, why: str) -> None:
    """A scheduled run that did not complete counts as skipped. The third in
    a row opens the blind item. Never raises: a failure to save is logged."""
    memory = state["memory"]
    memory["skipped_runs"] = int(memory.get("skipped_runs") or 0) + 1
    memory["last_attempt_night"] = night
    logging.warning("balancer run skipped: %s (%d in a row)", why, memory["skipped_runs"])
    try:
        balancer.save_state(path, state)
    except Exception:
        logging.exception("could not save balancer.json after a skipped run")
    if memory["skipped_runs"] >= 3:
        try:
            activity.record(
                type="balancer_alert", key=f"balancer_blind:{night}", night=night, severity="warn",
                needs_you=True, resolve_key="balancer_blind",
                title="The balancer has not completed a run in 3 nights, so priorities are not being balanced",
                detail=why)
        except Exception:
            logging.exception("could not record the balancer blind item")


def run_balancer(trigger: str = "manual", now: datetime | None = None, preview: bool = False) -> dict:
    """One balancer run. `preview` decides and reports without writing
    anything. Only a scheduled run (`trigger="schedule"`) sets the night's
    attempt marker and counts toward skipped runs, so a manual "Run now"
    never blocks the scheduled run and a failed one does not count against
    the balancer."""
    now = now or datetime.now(timezone.utc)
    night = activity.night_of(now)
    scheduled = trigger == "schedule" and not preview
    late = now.astimezone(activity.tz()).hour >= 15
    with BALANCER_LOCK:
        path = _balancer_path()
        if scheduled:
            _balancer_attempted[str(path)] = night
        state = balancer.load_state(path)
        mode = state["settings"]["mode"]
        if mode == "off" and not preview:
            return {"ran": False, "reason": "off", "message": "The balancer is off."}
        if not activity.enabled():
            if scheduled:
                _balancer_skipped(path, state, night, "Activity is off, so there are no hours to read")
            return {"ran": False, "reason": "activity_off",
                    "message": "Activity is off (ACP_ACTIVITY=off), so the balancer has no hours to read."}
        try:
            with PLANS_LOCK:
                data = load_plans()
                plans = data.get("plans", [])
                if not isinstance(plans, list):
                    raise ValueError("plans.json has no plan list")
                result = _balancer_decide(plans, state, night, late)
                if preview:
                    return {"ran": True, "preview": True, **result}
                if mode == "live" and (result["changes"] or result["met_new"] or result["lapsed_new"]):
                    stamp = now.isoformat()
                    for ch in result["changes"]:
                        apply_project_settings(
                            plans, ch["project"],
                            {"priority": ch["to"], "balancer_note": _balancer_note(ch, night)}, stamp)
                    for m in result["met_new"]:
                        for p in plans:
                            if p.get("id") != m["plan_id"]:
                                continue
                            listed = p.get("commitments")
                            for c in listed if isinstance(listed, list) else []:
                                if isinstance(c, dict) and c.get("month") == m["month"] and not c.get("met_at"):
                                    c["met_at"] = night
                                    p["updated_at"] = stamp
                    for m in result["lapsed_new"]:
                        for p in plans:
                            if p.get("id") != m["plan_id"]:
                                continue
                            listed = p.get("commitments")
                            for c in listed if isinstance(listed, list) else []:
                                if isinstance(c, dict) and c.get("month") == m["month"] \
                                        and not c.get("met_at") and not c.get("lapsed_at"):
                                    c["lapsed_at"] = night
                                    c["lapsed_reason"] = m["reason"]
                                    p["updated_at"] = stamp
                    save_plans({**data, "version": data.get("version", 1), "plans": plans})
        except Exception:
            logging.exception("balancer run failed")
            if scheduled:
                _balancer_skipped(path, state, night, "The run failed; see the server log")
            return {"ran": False, "reason": "error", "message": "The balancer run failed; see the server log."}
        was_blind = int(state["memory"].get("skipped_runs") or 0) >= 3
        memory = result["memory"]
        memory["skipped_runs"] = 0
        if scheduled:
            memory.update({"last_run_night": night, "last_attempt_night": night})
        state["memory"] = memory
        warnings = list(result["warnings"])
        try:
            balancer.save_state(path, state)
        except Exception:
            logging.exception("could not save balancer.json after a run")
            warnings.append("balancer.json could not be saved, so the balancer's memory is out of date.")
        try:
            for item in result["items_open"]:
                activity.record(
                    type="balancer_alert", key=item["key"], night=night, severity="warn",
                    needs_you=True, resolve_key=item["key"], title=item["title"],
                    refs={"plan_id": item["plan_id"]})
            resolves = list(result["items_resolve"]) + (["balancer_blind"] if was_blind else [])
            activity.record(
                type="balancer_run", key=f"balancer:{night}", night=night, title=result["title"],
                detail=result["detail"], severity=result["severity"], data=result["data"],
                resolves=resolves)
        except Exception:
            logging.exception("could not record the balancer run in Activity")
            warnings.append("the run could not be recorded in Activity.")
        return {"ran": True, "preview": False, **result, "warnings": warnings}


def balancer_tick(now: datetime | None = None) -> bool:
    """One scheduler poll: run if the balancer is due. Returns whether it ran."""
    now = now or datetime.now(timezone.utc)
    state = balancer.load_state(_balancer_path())
    night = activity.night_of(now)
    if _balancer_attempted.get(str(_balancer_path())) == night:
        return False
    if not balancer.is_due(now.astimezone(activity.tz()), night, state["settings"], state["memory"]):
        return False
    run_balancer("schedule", now)
    return True


_balancer_thread: threading.Thread | None = None
_balancer_stop = threading.Event()


def _balancer_loop() -> None:
    while not _balancer_stop.is_set():
        try:
            balancer_tick()
        except Exception:
            logging.exception("balancer scheduler tick failed")
        _balancer_stop.wait(BALANCER_POLL_SECONDS)


def start_balancer_scheduler() -> bool:
    """Start the balancer thread. Not started under the test suite
    (ACP_SKIP_STARTUP_MIGRATION) or with ACP_BALANCER=off."""
    global _balancer_thread
    if os.environ.get("ACP_SKIP_STARTUP_MIGRATION"):
        return False
    if (os.environ.get("ACP_BALANCER") or "").strip().lower() == "off":
        return False
    with BALANCER_LOCK:
        if _balancer_thread is not None and _balancer_thread.is_alive():
            return False
        _balancer_stop.clear()
        _balancer_thread = threading.Thread(target=_balancer_loop, name="acp-balancer", daemon=True)
        _balancer_thread.start()
    logging.info("balancer scheduler started")
    return True


def stop_balancer_scheduler() -> None:
    global _balancer_thread
    _balancer_stop.set()
    thread = _balancer_thread
    if thread is not None:
        thread.join(timeout=5)
    _balancer_thread = None
    _balancer_stop.clear()


@app.route("/api/balancer", methods=["GET"])
def api_balancer():
    state = balancer.load_state(_balancer_path())
    notes = []
    keep = activity.keep_nights()
    if keep is not None and keep < 31:
        notes.append(f"Activity keeps only {keep} nights, so this month's hours may be undercounted.")
    if not activity.enabled():
        notes.append("Activity is off (ACP_ACTIVITY=off), so the balancer cannot run.")
    mem = state["memory"]
    return jsonify({
        "settings": state["settings"],
        "memory": {k: mem.get(k) for k in ("last_run_night", "bands", "skipped_runs")},
        "activity_enabled": activity.enabled(), "notes": notes,
    })


@app.route("/api/balancer/settings", methods=["PUT"])
def api_balancer_settings():
    with BALANCER_LOCK:
        path = _balancer_path()
        state = balancer.load_state(path)
        merged, err = balancer.validate_settings(request.get_json(silent=True), state["settings"])
        if err:
            return jsonify({"error": err}), 400
        state["settings"] = merged
        balancer.save_state(path, state)
    return jsonify({"settings": merged})


@app.route("/api/balancer/preview", methods=["POST"])
def api_balancer_preview():
    result = run_balancer("manual", preview=True)
    return jsonify(result), (200 if result.get("ran") else 409)


@app.route("/api/balancer/run", methods=["POST"])
def api_balancer_run():
    result = run_balancer("manual")
    return jsonify(result), (200 if result.get("ran") else 409)


@app.route("/api/balancer/tag-from-class", methods=["POST"])
@_holds_plans_lock
def api_balancer_tag_from_class():
    """One-off: give a role to every project that has none, from its plans'
    target class. Survey becomes survey and pretty becomes pretty. Science is
    always set by hand."""
    data = load_plans()
    plans = data.get("plans", [])
    projects: dict = {}
    for p in plans:
        if isinstance(p, dict):
            projects.setdefault(balancer.project_key(p), []).append(p)
    now = datetime.now(timezone.utc).isoformat()
    tagged = []
    for name, group in projects.items():
        if any(p.get("balance_role") for p in group):
            continue
        classes = {p.get("target_class") or fr.target_class_of(p) for p in group}
        if len(classes) == 1 and classes <= {"survey", "pretty"}:
            apply_project_settings(plans, name, {"balance_role": classes.pop()}, now)
            tagged.append(name)
    if tagged:
        save_plans({**data, "version": data.get("version", 1), "plans": plans})
    return jsonify({"tagged": sorted(tagged)})


def migrate_gear_at_startup() -> None:
    """Run the gear template migration before the first request
    (docs/specs/gear-templates.md section 11). It also runs lazily on the
    first load of an unmigrated file, so this only makes it eager. Skipped
    when ACP_SKIP_STARTUP_MIGRATION is set, which the test suite does so an
    import never touches the developer's own data/gear.json."""
    if os.environ.get("ACP_SKIP_STARTUP_MIGRATION"):
        return
    try:
        _load_gear_stored()
    except Exception:
        logging.exception("gear migration at startup failed; gear is served as stored")


migrate_gear_at_startup()
start_scan_scheduler()
start_balancer_scheduler()


def effective_port(env=None) -> int:
    """PORT, then the settings file, then 5555. A bad value is skipped."""
    return acp_settings.effective("port", env)


def effective_bind_host(env=None) -> str:
    """ACP_BIND_HOST, then HOST, then loopback. Never the settings file: no file
    or page may open ACP to the network. A launcher sets ACP_BIND_HOST to what
    it really binds, since it ignores HOST."""
    env = os.environ if env is None else env
    for name in ("ACP_BIND_HOST", "HOST"):
        value = (env.get(name) or "").strip()
        if value:
            return value
    return "127.0.0.1"


if __name__ == "__main__":
    host = effective_bind_host()
    port = effective_port()
    print(f"Astro Coverage Planner -> http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
