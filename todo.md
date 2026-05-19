# Todo — parked features & open questions

Active work lives in the Claude task list. This file is for parked items: bugs, polish, and open design questions awaiting review.

## Done

### Earlier

- **Feature 1** — Target list in right-hand rail (commit c21ca65).
- **Feature 2** — Smart search bar in right rail (commit eee3b7f).
- **Feature 3** — Planning mode with mosaics, gear management, NINA Target Scheduler sync (commit fd63bb7). Plus follow-ups: gear-seed dedupe + TS resync guard (6f77f79), target-finished override + click-in-FOV selection + rail accordions (8a88ff6).
- **Deselect target on Esc / empty-sky click** (was item 6 below) — done.

### Coverage-source plugin architecture (May 2026, branch `planner`)

Architectural reset that turned ACP into a plugin host so users (and the author privately) can plug in extra coverage sources without touching the core. All commits on `planner`:

- **Foundation — Extensions API** (`17ca796`). `extensions.py` loader for out-of-tree plugins via `ACP_EXTENSIONS_DIR` (default `%APPDATA%\acp\extensions` on Windows, `~/.config/acp/extensions` elsewhere). README "Extensions" section documents the contract.
- **Phase 0 — Catalog overlay bug fix** (`40012e2`). Resolves item 5 below: silent-no-markers was caused by `data/catalogs.json` not existing. Startup logs an `[acp] WARN` when it's absent and the rail summary shows a "(no catalogs loaded — run scripts/fetch_catalogs.py)" hint instead of "(broken — see TODO)". Plus two latent rendering bugs: WISE HII used an invalid Aladin shape ("dot") and a sourceSize below Aladin's safe floor.
- **Phase 1 — `CoverageSource` interface** (`81284ce`, `fe437d8`, `25c818a`). Pure-types `sources.py` with `CoverageSource` Protocol + `PolygonCoverage`/`MocCoverage` tagged union. `ManifestCoverageSource` wraps the existing manifest as the first source. New `/api/sources` endpoint and "Sources" rail accordion. Persistence-on-reload bug fix; scipy added to requirements (`/api/export/priority` was 500ing once catalogs were populated, missing transitive dep for `match_to_catalog_sky`).
- **Phase 2 — Friend-manifest sharing** (`53b662d`, `1eb9d67`, `a8ce9f2`). Resolves item 3 below: sanitiser strips local paths, telescope serials, exact dates; whitelist-based per-target rebuild with a `validate_no_paths` safety net. `--sanitise <out>` flag on `build_archive_manifest.py` plus standalone `scripts/sanitise_manifest.py`. `FriendManifestSource` consumes the sanitised JSON via `ACP_FRIEND_MANIFESTS=path1;path2`. New "Sharing coverage with friends" README section.
- **Phase 3 — Survey MOCs** (`ddc3159`, `bba0286`, `e80442a`, `8c4872f`, `1598226`). `MocCoverageSource` lazy-fetches MOC FITS bytes from CDS (HTTPS-only, hostname allowlist, 10MB cap, 30s timeout, mocpy parse-validation, content-hash cache invalidation, 30-day TTL). `data/surveys.json` declarative registry, ships with IPHAS DR2 Hα. New `/api/moc/<id>` endpoint and frontend Aladin overlay using the `perimeter+fill` recipe (the default `edge:true` mode tanks FPS at order 11+). Default base survey switched to DSS2/red for lighter HiPS load. New "Public surveys" README section.
- **Phase 4 — Multi-source gap-finder** (`4077606`, `2d0c09a`, `d6c5e5e`, `dbd7eb8`). `gaps.py` does pure MOC union/intersection across enabled sources. New `/api/gaps?have=Ha&missing=SII&sources=...&min_have_hours=&max_missing_hours=` and `/api/gaps/moc.fits` endpoints. Catalogues rail's gap-mode button rewritten as a have/missing/threshold/source-multi-select panel with a yellow gap MOC overlay and a stats line. Old `/api/export/priority` becomes a thin wrapper preserving the legacy CSV schema (falls back to the original inline implementation when mocpy isn't installed). Gap candidates filter the existing catalog overlays instead of dumping a single yellow blob — toggle a catalog and only its in-gap entries render.
- **Catalog tooltips** (`6391d32`). Aladin `objectHovered` event drives a small dark tooltip following the cursor; shows name + catalog + up to two extra metadata fields. Flips quadrant near viewport edges so it doesn't clip.
- **More catalogues** (`a0fb3fc`). Messier (110 hardcoded), Sharpless 2 HII (313, VizieR VII/20), Strasbourg-ESO PNe (1143, VizieR V/84 — Abell PNe present as a subset). All slot into the gap-finder filter for free via the existing `gapNamesByCatalog` machinery.
- **Telescope filter polish** (`7d95ce1`). Empty telescope selection now hides every tagged target (was: showed everything).

### Extension UI manifest + nina_ts_sync UI integration (May 2026, branch `planner`)

Generic action-registration mechanism so extensions can surface buttons + toggles in the planning rail (and swap a core button in place) without touching frontend code. First consumer is the (private) `acp-nina-ts-sync` extension which replaces the core zip-export "Sync to NINA" button with a bidirectional "Sync with NINA" modal and adds a "Live progress from NINA" auto-poll toggle. Shipped same day.

- **Core ACP** — `app.extensions_manifest` registry, `/api/extensions/manifest` endpoint, `REPLACEABLE_BUTTONS` swap mechanism, Extensions rail accordion, modal driver with profile-picker + preview-then-apply + bidirectional flows, plan-grouped from→to diff renderer with per-item radios, surgical actual_hours patcher for the open detail panel, retry-with-backoff on `database is locked`, two-line plan-row layout (priority dot blue/green/grey, target name dominant on row 1, project + filter dots + time-aware sparkline on row 2).
- **acp-nina-ts-sync** (private repo) — manifest registration in `__init__.py`, `config.py` for persisted `profile_id`, `/config` + `/profiles` endpoints, three-way bidi modal driver. Fixed two in-session bugs during testing: (a) `_overlap_pct_from_grid` now uses camera-frame stride instead of raw sky-frame so rotated mosaics round-trip cleanly; (b) `ts_base_snapshot` captures the plan's mosaic dims so the diff has a real BASE for mosaic.* fields instead of falling back to "no opinion".
- **Docs** — `docs/extensions.md` gets a UI manifest section; `acp-nina-ts-sync/README.md` rewritten around the UI flow; `FINDINGS-2026-05-11.md` strikes the two fixed bugs and updates the follow-up order.

Live-verified: push/pull with NINA-open-idle; bidi modal with both directions populated; live-progress 60s poll updating the rail + open detail panel without a refresh. Not yet verified: graceful retry during an actual imaging sequence (parked).

### Test suite hardening (May 2026, branch `main`)

CI-grade pin-down of graceful-degradation contracts in `app.py`. Coverage 71% → 83%, 170 tests passing across 11 new modules, plus two CI infrastructure fixes.

- **Critical tier (C1-C5):** `/api/visibility/panels` POST shape + partial-hit + oversize 400; `load_manifest` malformed-JSON / missing-keys / mtime cache; plan POST/PUT `_validate_plan_payload`; `load_catalogs` error paths; sync mosaic edge cases. App.py hardened so `load_manifest` / `load_catalogs` degrade gracefully on parse errors (previously 500'd).
- **High tier (H1-H4, H7, H8):** sync-download path-traversal regex gate; `/api/moc/<source_id>` route coverage; `/api/export/priority` mocpy-missing fallback; gear-seed fuzzy dedupe + slug-id collision chain; observability silent-clamp coords vs 400-on-bad-time divergence; visibility point/panels cache seeding + manifest-mtime invalidation.
- **H5 — Frontend tokenizer harness (2026-05-18).** Extracted `tokenizeSearch` / `targetMatchesSearch` / `catalogObjectMatchesTokens` to `static/search.mjs`; `templates/index.html` loads `app.js` as `type="module"` and app.js re-imports the bare names. New `tests/frontend/` with 50 cases across tokenizer + matchers, run by Node's built-in test runner (`node --test`) — zero npm deps. New `tests-frontend` job in `tests.yml`. (Originally scoped for Vitest + jsdom; switched to `node --test` because Vitest 2.x trips a Node ESM bug on Windows network drives, and the tokenizer is pure-function anyway so jsdom is unnecessary.)
- **CI infrastructure fixes:** renamed `tests/test_smoke.py` → `tests/smoke.py` (procedural script with top-level asserts was being auto-discovered by unittest and breaking against an empty manifest in CI — the C2 hardening turned `/api/manifest` from 500 into 200-empty, blowing past the script's first assert). Added `if: github.actor != 'dependabot[bot]'` to `claude-code-review.yml` (Dependabot PRs run with a restricted secret store that doesn't expose `CLAUDE_CODE_OAUTH_TOKEN`).
- **Plus PR #22 (2026-05-18):** stripped exception text from 9 jsonify error responses + consolidated 3 duplicate astropy presence checks + added `_safe_log()` helper, clearing all 14 CodeQL alerts (5 reflective-xss, 7 stack-trace-exposure, 2 log-injection).
- **Dependabot triage:** merged 8 GitHub-Actions major bumps (checkout 4→6, setup-python 5→6, buildx 3→4, build-push 6→7, login 3→4, fetch-metadata 2→3, metadata-action 5→6, codeql 3→4). 6 Python majors parked: numpy 1→2 (#21), astropy 6→7 (#20), astroquery 0.4.7→0.4.11 (#17), flask 3.0→3.1 (#15), scipy 1.10→1.17 (#13), mocpy 0.13→0.20 (#11).

---

## In progress

- **Prettier README (item 8 below).** Showcase rewrite is mostly there: hero, "Who this is for", feature blocks with screenshot/GIF placeholders, search-grammar table, planning-mode demo, plus the new Extensions / Public surveys / Sharing / Finding coverage gaps sections from the May 2026 work. Captures checklist in `docs/images/CAPTURES.md` — screenshots/GIFs still need to be filled in by hand from a real session.

---

## Open design questions

(Existing list pruned: items 3 and 5 are now resolved by the May 2026 work; remaining items renumbered.)

1. **Workflow:** feature branch + PR per phase, or push direct to `main`? Matters more now that the repo is public and shareable with astro friends.
2. **Feature 3 extras:**
   - Mobile layout? Current panel is fixed 380px — won't work on phones.
   - Plan export format — CSV? JSON? NINA sequence file?
   - NINA integration beyond copy-paste (e.g. NINA's HTTP API if it exposes one)?
3. **Sky-visibility overlay from observer location (parked 2026-04-20):** surface "what's actually reachable from where I am" on the map. Ideas to pick from:
   - Shade the portion of the celestial sphere that is *never* above the user's minimum altitude from their latitude (always-below-horizon band).
   - Let the user override location (stored in localStorage already via `/api/observability`) for travel / remote-site planning.
   - Per-target "best time of year" badge: months during which the target clears the plan's `min_altitude_deg` for more than N hours of astronomical darkness.
   - Stretch: overlay the current night's altitude curve for a selected target on the side panel.
5. **Project-level fields for power-user plan groups (parked 2026-05-11):** today a plan carries `project_name` as free text, and project-level fields like `min_altitude_deg`, `meridian_window_min`, and `priority` live on each plan individually. On `/api/ext/nina-ts-sync/sync` the strictest-wins rule collapses N per-plan values into the single TS Project value (max min-alt, narrowest meridian, highest priority) — correct for safety but surprising when most plans agree and a couple of outliers tighten the whole project. Fine for users with one target per plan; gets hard when a single `project_name` has 5–6 mosaic groups (e.g. galactic-plane survey covering Galactic Bulge 1 + Plane 1/3/4/5 + SMC + LMC all under "PNe Survey"). Exploration ideas:
   - Optional first-class **Project** entity in ACP (sharable across plans) with its own `min_altitude_deg` / `meridian_window_min` / `priority`. Plans without a Project keep the current behaviour (free-text `project_name` + per-plan fields) so single-target users don't feel forced into TS's project-first ceremony.
   - Pre-sync warning UI: when `/sync` is about to collapse mixed per-plan values into one TS project value, surface it as a one-time confirmable dialog ("PNe Survey has 30°/40° min-alts across 8 plans; TS will use 40°. Continue?").
   - Inline rename hint in the same warning, matching the strictest-wins design memo (planner-design.md): offer to rename outliers into a derived project (e.g. "PNe Survey (high)") in one click.
   This is **UX work**, not extension/sync work — the sync semantics are settled (strictest-wins, locked 2026-04-20). The extension can stay as-is; surface helpers (like the sync warning) can live either side.

6. **Bug — page is slightly taller than the viewport (parked 2026-04-21):** the layout is very close to fitting in one screen but overflows by a few pixels, so the page gains a vertical scrollbar when nothing should need to scroll. Likely culprit after the topbar restructure: `.layout` is sized to `calc(100vh - 44px)` in `static/style.css`, which assumes a 44px topbar — but with padding/borders the actual topbar is taller, or `body`/`html` has residual margin pushing total height past 100vh. Fix: measure the real topbar height (e.g. with `getBoundingClientRect`) and set the layout height from that, or switch the layout to `flex: 1` inside a `display: flex; flex-direction: column; height: 100vh` body so it just absorbs whatever space the topbar leaves.

---

## Parked work to pick up next session

### Night-out validation of the TS-sync extension (parked 2026-05-11)

The push/pull/diff flows are validated against an idle NINA + TS plugin. What's *not* yet validated: the graceful retry on `database is locked` while an imaging sequence is actually running. The retry path exists (3 attempts, 2s/4s/8s exponential backoff) but only fires against contention from idle NINA. Needs a real night out — clear sky + a target you'd image anyway — to confirm it holds up under genuine concurrent writes.

- Open NINA. Start a sequence on any project (Fesen SNR is the closest-to-zenith candidate from the current ACP plan list at southern latitudes).
- While imaging, hit `Sync with NINA` in ACP and apply something small (e.g. bump a target_hours by 0.5h). Expect: modal status briefly shows "Pulling… retrying (1/3)" or similar, then succeeds.
- Also try `Live progress from NINA` toggle on. Confirm the 60s poll picks up the rising `acquired` count and the rail's "h left" decreases per-filter.

If anything trips the retry to exhaustion, capture the modal's error and the timing relative to TS plugin's own writes — would point at needing to widen the SQLite `busy_timeout` (currently 10s).

### TS-sync extension: open findings (parked 2026-05-11)

Two parked items in `acp-nina-ts-sync/FINDINGS-2026-05-11.md` worth working through eventually:

- **Per-plan `min_altitude_deg` false positives** (#3). When N ACP plans share a `project_name` with mixed min-alts, strictest-wins collapses them to one TS project value. On re-import, every non-strictest plan shows as a fake "ACP-only change" — eroding diff trust. Likely fix: capture per-plan min_alt in `ts_base_snapshot.plan` (new field) and diff against that.
- **Strictest-wins UX surprise** (#6). User pushed mixed min_alts (40 + 30), got 40 in NINA, expected 30. Not a bug — but `/sync` should return a note flagging when the collapse happened, and the preview modal should surface it as a one-time warning. Workaround today: split outliers into a derived project (`project_name="PNe Survey (high)"`).

Both have fix shapes in FINDINGS; not done because they need a tiny bit more design (per-plan snapshot adds another layer; surprise-note format needs to thread through the existing notes channel).

### NINA plugin initiative (parked 2026-05-19)

Architecture, research, and gap-analysis locked. See `docs/nina-plugin-research.md` (Framing Wizard API, TS schema, TS pub/sub, the existing private `nina_ts_sync` extension's surface) and `docs/nina-plugin-api-audit.md` (/api/plans schema audit + recommended additions). Memory: [[nina-plugin-initiative]].

Decision summary:
- Plugin is a **thin C#/.NET 8 NINA-side UX layer** over the existing private Python `nina_ts_sync` extension — not a port. Plugin POSTs to `localhost:5555/api/ext/nina-ts-sync/*` for TS work; calls `IFramingAssistantVM.SetCoordinates()` directly for Framing.
- ACP and NINA on the same machine for v1.x (matches [[remote-edit-architecture]]).
- v1.0 = Framing push + manual TS sync button. v1.1 = live progress dock (subscribe to TS pub/sub topics). v1.2 = pull/conflict UI inside NINA.
- v3.0 = full C# port of `nina_ts_sync` to enable location-independent ACP (e.g. ACP on NAS / homeserver / remote home server, NINA at a dark site).

Open items parked for the plugin work:

- **Rotation convention verification (10-min test).** `ninaAPI` does `Rectangle.TotalRotation = 360 - rotationDeg` for Framing; our `_build_ts_export` writes `Rotation` to TS un-inverted. Either TS and Framing use different conventions (plausible) or one of these paths is off. Verify empirically before plugin v1.0 ships. Cheapest test: pick a target in the planner with a non-zero rotation, push to TS, open TS's target view, compare displayed rotation against ACP's rail value. Then push to Framing and compare against TS's display.
- **ACP-side prep (small, pre-plugin).** Add `GET /api/version` with a `last_modified` for `/api/plans` (plugin caches against it). Add `?expand=gear,site,panels` enrichment on `/api/plans` so the plugin can do a single fetch instead of a join. Persist `filter_goals.<f>.sub_exposure_s` on save (currently 0/31 plans have it; the plugin's per-filter UI needs it). Add CORS headers on read-only endpoints.
- **Bearer-token plumbing on `/api/*` (defer enforcement to v3.0).** Add `Authorization: Bearer <token>` parsing to every API endpoint now, defaulting to "no token required" when binding to loopback. Don't gate it on host detection — bake the contract in early so v3.0 (cross-machine, where auth MUST be on) doesn't require an API rewrite. HTTPS-ready URL parsing in the plugin's Server URL field from day one.

### v3.0 cross-machine security (parked 2026-05-19)

Substantial design work, not v1.x scope. The v3.0 port to C# is also when ACP becomes safely network-accessible. Today's `HOST=0.0.0.0` warning ("only do this on a trusted network") is fine for LAN exposure but inadequate for the remote-observatory use case where the imaging PC and ACP server may not share a network.

**Scope (locked 2026-05-19): Shape A and Shape B only. ACP does not, and will not, attempt to be an Internet-facing service.**

- **Shape A — same machine (today's state).** Loopback only, no auth needed. Must remain the simplest path. v1.x lives entirely here.
- **Shape B — same LAN (or overlay network).** v3.0's target. App-layer auth required when binding to anything other than 127.0.0.1. Optional TLS for sniff-resistance on untrusted LANs.
- **Shape C — direct Internet exposure: EXPLICITLY OUT OF SCOPE.** Don't build for it, don't recommend it, don't paper over it with half-measures.

**For remote-observatory users:** point at an overlay network (Tailscale especially — zero-config, identity-based, NAT-traversing, already common in remote-obs setups). Once on the overlay, the deployment is Shape B as far as ACP is concerned — ACP sees a trusted "LAN" of overlay nodes, doesn't know or care that one of them is across the Internet. This means **all the hard remote-access problems** (NAT traversal, NAT-PMP, dynamic DNS, public PKI cert provisioning, certbot rotation, rate limiting, public-Internet hardening) **move from ACP to the overlay layer**, where they're solved problems with mature tooling. ACP's job is the app-layer auth; the overlay's job is everything else.

Attack vectors actually in scope (Shape B):
- LAN sniffing — compromised IoT device, guest Wi-Fi → TLS handles this if user enables it
- MITM tampering on plan coordinates → TLS + bearer token
- Unauthenticated LAN access — anyone on the LAN scanning ports → bearer token always required off-loopback
- Credential theft from the plugin → store token in OS credential store, not plain text

Attack vectors explicitly NOT in scope (because they're Shape C):
- Internet replay attacks (nonces/short-lived tokens) — overlay's job
- Public-PKI cert provisioning — overlay's job
- NAT traversal — overlay's job
- DDoS / rate limiting — overlay's job

Pre-v3.0 hooks to land in v1.x so we don't paint ourselves into a corner:
- Bearer-token plumbing on `/api/*` (above). Required when binding off-loopback; optional but supported on loopback.
- Plugin's Server URL accepts `https://` from day one.
- No hardcoded `http://` anywhere in the plugin or extension HTTP clients.
- Token storage on the plugin side uses OS credential store (Windows Credential Manager) from the first version that needs it. Don't ship a plain-text-token fallback.

Docs work (deferred until v3.0 lands):
- One page in `docs/` titled "Running ACP across machines (Tailscale + overlay networks)" that walks through the overlay setup pattern. Explicit "don't port-forward ACP to the Internet" warning at the top with the reasoning.

---

## Follow-ups from May 2026 work

- **Phase 5 — generic `docs/extensions.md` worked example.** The only remaining piece of the original plugin-architecture plan. ~30-60 lines of markdown showing how a third party would author an extension that registers a `CoverageSource`. Tiny — half a day at most. (Note: a `docs/extensions.md` file now exists from the May 2026 README restructure — check whether it satisfies this item before treating it as still-open.)
- **EMU SNR-candidates removed (2026-05-06).** Tried six plausible VizieR IDs, none matched the Ball et al. EMU SNR catalogue — it isn't mirrored on VizieR yet. Removed from `data/catalog_registry.json` (no chip in rail) and from `scripts/fetch_catalogs.py`. To restore once a VizieR mirror appears: re-add the registry entry (id `emu`, data_key `emu_candidates`) and copy the SMGPS-style candidate-loop pattern from the same file using the new VizieR ID.
- ~~**`pix2world` TypeError on cursor hover (pre-existing).**~~ Fixed by wrapping the `aladin.pix2world(...)` call in `hoverRaf` with a try/catch (Aladin throws an internal TypeError on mousemove before WebGL view is ready; treat as no-hit and recover next frame).
- ~~**ESO PNe (V/84) returns 0 entries in practice.**~~ Fixed: V/84's `_RA.icrs`/`_DE.icrs` come back as sexagesimal strings ("18 13 18.03"), not floats — switched to `astropy.coordinates.Angle` parsing. All 1143 rows now resolve.
- **User-supplied custom catalogues.** Right now the catalog list is hardcoded in `setupCatalogOverlays` (cfg array) and `templates/index.html` (checkbox per id). To let any user drop in their own catalogues (custom catalogues, club target lists, personal favourites, etc.) without touching the code, lift the registry into a declarative file similar to `data/surveys.json`:
   - New `data/catalog_registry.json` listing `{id, name, label, color, marker, size, source}` per catalogue. `source` is either a path to a JSON file in the user's data dir or a VizieR id `fetch_catalogs.py` knows how to pull.
   - `setupCatalogOverlays` reads from `/api/catalog-registry` instead of a hardcoded array; the rail accordion renders checkboxes dynamically (mirrors the Sources rail's already-dynamic pattern).
   - Per-OS user config dir override (`ACP_CATALOG_REGISTRY` or similar) so a user can keep private catalogues out of the repo.
   - `fetch_catalogs.py` reads the registry's VizieR-backed entries and pulls each one independently with try/except per entry, keeping the existing built-ins as defaults.
   - Out-of-tree extensions can append to the registry the same way they append to `app.coverage_sources`, which gives the existing extension API a second hook for catalogue-only contributions without needing a full coverage source.
- **`/api/export/priority` mocpy-vs-legacy row-set parity.** The new MOC-based path inside the legacy CSV route filters by gap-MOC cells before the per-target overlap match; the original inline path filters by per-target hours after the catalog match. On the current manifest both produce 15 rows but the row sets aren't strictly guaranteed to match in edge cases (a candidate inside a multi-target gap MOC where the *nearest* target lacks the hours could be excluded by the new path). Acceptable for now; if a discrepancy bites, either thread `min_have_hours` deeper into `gaps.compute_gap_moc` or document the behavioural change in the README.
- **`mocpy 0.20` `MOC.difference` bug workaround.** `gaps.py` works around a buggy `m1.difference(m2)` returning empty for fully-disjoint operands by computing `have.intersection(missing.complement())`. Track upstream; revert the workaround when fixed in mocpy.
- **Friend-manifest hours fingerprintability.** Sanitiser rounds `total_hours` to 0.1h. Even rounded, hours per filter are still a reasonable fingerprint of imaging cadence. Probably acceptable for the share-among-friends model, but worth flagging if anyone wants tighter privacy (e.g. quantise to half-hour buckets, or omit hours entirely and just share the polygon footprint).
- **MOC source `friend_friend_X` cosmetic.** Already fixed in `e80442a` — the `friend_` prefix is no longer doubled when a friend filename starts with `friend_`. Listed here for traceability only.
- **TS exposure-template duplication on re-sync (parked 2026-05-05; Path B prototyped 2026-05-09).** TS's Import Profile flow always creates new `ExposureTemplate` rows and rewrites `ExposurePlan.ExposureTemplateId` via its own remap dictionary — there is no name/GUID dedup. Re-syncing N times yields N copies of every template. Two paths to fix:
   - **Path B — direct SQLite write (experimental prototype, lives outside this repo).** Prototyped as a standalone ACP extension at `sibling-extension-dir/` (sibling of this repo, **untracked / no git remote / no commits** — agent kept it separate while it's experimental). Package `nina_ts_sync/` registers `/api/ext/nina-ts-sync/*` routes (status, preview, sync, import/preview, import/apply, sync-acquired, import/diff, import/resolve), GUID-based upsert with claim-before-insert, `PRAGMA user_version` allowlist (currently `{23}` only — TS plugin 5.9.0+, Feb 2026+), and a fixture builder that rebuilds schedulerdb from the plugin's vendored migration SQL (`tcpalmer/nina.plugin.targetscheduler@2ec0c4d`). 46 offline tests, all passing. Not yet validated against a live `schedulerdb.sqlite` — see `TESTING.md` in that folder for the Windows real-world test plan that still needs to be run. `DESIGN-v0.2.md` drafts a three-way merge (BASE/ACP/TS) with `ts_refs` ID stamping and per-plan conflict resolution; not yet implemented. To turn it on you'd drop `nina_ts_sync/` into `ACP_EXTENSIONS_DIR`; nothing in core ACP references it yet.
   - **Upstream feature request to `tcpalmer/nina.plugin.targetscheduler`.** Ask for one of: (a) Import Profile dedupes ExposureTemplates by `(profileId, name)` — skip insert if match exists, point `ExposurePlan.ExposureTemplateId` at the existing row; (b) Import Profile honours pre-set `ExposureTemplateId` values that resolve to existing rows in TS, instead of always remapping through the export's id-map. Either would let us emit a templates-empty zip on resync. Worth filing once we know which interpretation we'd actually consume on our side.
   Cosmetic mitigation already shippable in core: stamp template names with an `[ACP]` suffix so the user can bulk-delete dupes in TS by name search before re-syncing.

---

## Homeserver deployment cleanup (parked 2026-05-09)

The homeserver container at `acp.homeserver.rohanhinton.com` was switched from a 2-min cron that did `git pull && docker compose up -d --build` locally to GHA → GHCR → Watchtower. Pipeline: push to `main` → `.github/workflows/publish.yml` builds → `ghcr.io/astro-roro/astro-coverage-planner:latest` → Watchtower (label-scoped, 60s poll) recreates the `acp` container. Wait until two or three pushes have landed via this path and visibly rotated the container (check `docker logs acp-watchtower` for `Updated=1`) before doing the cleanup below.

- **Bin the local-build remnants on homeserver** once Watchtower has picked up at least one push:
   - `/mnt/user/appdata/acp/repo/` — old git checkout; no longer pulled from
   - `/mnt/user/appdata/acp/scripts/update.sh` and its `update.log` — old auto-update script; replaced by Watchtower
   - The `acp-update` entry in User Scripts (already disabled, safe to delete the script + schedule.json entry)
- **Bin the safety-net backups on homeserver** once cleanup above is done:
   - `/mnt/user/appdata/acp/docker-compose.yml.bak.*` (pre-GHCR compose)
   - `/boot/config/plugins/user.scripts/customSchedule.cron.bak`
   - `/boot/config/plugins/user.scripts/schedule.json.bak`
   - `/etc/cron.d/root.bak` (this one regenerates anyway, lowest priority)
- **If GHCR package is ever flipped to private** (currently public, inheriting the repo's visibility), Watchtower will start failing with auth errors. Fix: `docker login ghcr.io -u astro-roro -p <PAT>` on homeserver as the user the Watchtower container runs as (writes `~/.docker/config.json`, which Watchtower reads automatically). PAT scope: `read:packages` is enough.
- **Watchtower notification channel** is currently disabled (`WATCHTOWER_NOTIFICATIONS_LEVEL: info` but no channel configured). If you want a ping when an update lands or fails, add e.g. `WATCHTOWER_NOTIFICATION_URL: ntfy://...` pointing at the existing `cal-sync-ntfy` container. Skip until you've decided you actually want the noise.

---

## Notes from the kick-off plan (2026-04-19)

Rollout order agreed: **Feature 1 → Feature 2 → Feature 3**. Feature 3 needs Feature 2's search grammar to express "show only incomplete plans".

Scanner change landing with Feature 2: split `INSTRUME` from `TELESCOP`. Scanner run can happen in background after the code ships.
