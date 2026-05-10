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
4. **Bug — page is slightly taller than the viewport (parked 2026-04-21):** the layout is very close to fitting in one screen but overflows by a few pixels, so the page gains a vertical scrollbar when nothing should need to scroll. Likely culprit after the topbar restructure: `.layout` is sized to `calc(100vh - 44px)` in `static/style.css`, which assumes a 44px topbar — but with padding/borders the actual topbar is taller, or `body`/`html` has residual margin pushing total height past 100vh. Fix: measure the real topbar height (e.g. with `getBoundingClientRect`) and set the layout height from that, or switch the layout to `flex: 1` inside a `display: flex; flex-direction: column; height: 100vh` body so it just absorbs whatever space the topbar leaves.

---

## Follow-ups from May 2026 work

- **Phase 5 — generic `docs/extensions.md` worked example.** The only remaining piece of the original plugin-architecture plan. ~30-60 lines of markdown showing how a third party would author an extension that registers a `CoverageSource`. Tiny — half a day at most. (Note: a `docs/extensions.md` file now exists from the May 2026 README restructure — check whether it satisfies this item before treating it as still-open.)
- **EMU SNR-candidates removed (2026-05-06).** Tried six plausible VizieR IDs, none matched the Ball et al. EMU SNR catalogue — it isn't mirrored on VizieR yet. Removed from `data/catalog_registry.json` (no chip in rail) and from `scripts/fetch_catalogs.py`. To restore once a VizieR mirror appears: re-add the registry entry (id `emu`, data_key `emu_candidates`) and copy the SMGPS-style candidate-loop pattern from the same file using the new VizieR ID.
- ~~**`pix2world` TypeError on cursor hover (pre-existing).**~~ Fixed by wrapping the `aladin.pix2world(...)` call in `hoverRaf` with a try/catch (Aladin throws an internal TypeError on mousemove before WebGL view is ready; treat as no-hit and recover next frame).
- ~~**ESO PNe (V/84) returns 0 entries in practice.**~~ Fixed: V/84's `_RA.icrs`/`_DE.icrs` come back as sexagesimal strings ("18 13 18.03"), not floats — switched to `astropy.coordinates.Angle` parsing. All 1143 rows now resolve.
- **User-supplied custom catalogues.** Right now the catalog list is hardcoded in `setupCatalogOverlays` (cfg array) and `templates/index.html` (checkbox per id). To let any user drop in their own catalogues (HASH-style PN catalogues, club target lists, personal favourites, etc.) without touching the code, lift the registry into a declarative file similar to `data/surveys.json`:
   - New `data/catalog_registry.json` listing `{id, name, label, color, marker, size, source}` per catalogue. `source` is either a path to a JSON file in the user's data dir or a VizieR id `fetch_catalogs.py` knows how to pull.
   - `setupCatalogOverlays` reads from `/api/catalog-registry` instead of a hardcoded array; the rail accordion renders checkboxes dynamically (mirrors the Sources rail's already-dynamic pattern).
   - Per-OS user config dir override (`ACP_CATALOG_REGISTRY` or similar) so a user can keep private catalogues out of the repo.
   - `fetch_catalogs.py` reads the registry's VizieR-backed entries and pulls each one independently with try/except per entry, keeping the existing built-ins as defaults.
   - Out-of-tree extensions can append to the registry the same way they append to `app.coverage_sources`, which gives the existing extension API a second hook for catalogue-only contributions without needing a full coverage source.
- **`/api/export/priority` mocpy-vs-legacy row-set parity.** The new MOC-based path inside the legacy CSV route filters by gap-MOC cells before the per-target overlap match; the original inline path filters by per-target hours after the catalog match. On the current manifest both produce 15 rows but the row sets aren't strictly guaranteed to match in edge cases (a candidate inside a multi-target gap MOC where the *nearest* target lacks the hours could be excluded by the new path). Acceptable for now; if a discrepancy bites, either thread `min_have_hours` deeper into `gaps.compute_gap_moc` or document the behavioural change in the README.
- **`mocpy 0.20` `MOC.difference` bug workaround.** `gaps.py` works around a buggy `m1.difference(m2)` returning empty for fully-disjoint operands by computing `have.intersection(missing.complement())`. Track upstream; revert the workaround when fixed in mocpy.
- **Friend-manifest hours fingerprintability.** Sanitiser rounds `total_hours` to 0.1h. Even rounded, hours per filter are still a reasonable fingerprint of imaging cadence. Probably acceptable for the share-among-friends model, but worth flagging if anyone wants tighter privacy (e.g. quantise to half-hour buckets, or omit hours entirely and just share the polygon footprint).
- **MOC source `friend_friend_X` cosmetic.** Already fixed in `e80442a` — the `friend_` prefix is no longer doubled when a friend filename starts with `friend_`. Listed here for traceability only.
- **TS exposure-template duplication on re-sync (parked 2026-05-05).** TS's Import Profile flow always creates new `ExposureTemplate` rows and rewrites `ExposurePlan.ExposureTemplateId` via its own remap dictionary — there is no name/GUID dedup. Re-syncing N times yields N copies of every template. Two paths to fix, neither implemented:
   - **Path B — direct SQLite write** (the original "deferred" path). Open `schedulerdb.sqlite` in write mode, look up existing templates by `(profileId, name)` (or by stamped GUID once we start setting one consistently), reuse their `Id`s, insert/update Project + Target + ExposurePlan rows directly. Bypasses Import Profile entirely. Risks: schema drift across TS versions (mitigate via `PRAGMA user_version` gating against a tested-versions allowlist; refuse to write outside that window). Concurrency: TS docs say live writes are supported but recommend "with NINA closed" — surface that as the default behaviour with an override. Stamp every ACP-managed row with a recognisable marker (`description LIKE '[acp]%'` or a sidecar mapping table in our own data dir) so future re-syncs and cleanup are unambiguous.
   - **Upstream feature request to `tcpalmer/nina.plugin.targetscheduler`.** Ask for one of: (a) Import Profile dedupes ExposureTemplates by `(profileId, name)` — skip insert if match exists, point `ExposurePlan.ExposureTemplateId` at the existing row; (b) Import Profile honours pre-set `ExposureTemplateId` values that resolve to existing rows in TS, instead of always remapping through the export's id-map. Either would let us emit a templates-empty zip on resync. Worth filing once we know which interpretation we'd actually consume on our side.
   Cosmetic mitigation already shippable: stamp template names with an `[ACP]` suffix so the user can bulk-delete dupes in TS by name search before re-syncing.

---

## Brutix deployment cleanup (parked 2026-05-09)

The brutix container at `acp.brutix.rohanhinton.com` was switched from a 2-min cron that did `git pull && docker compose up -d --build` locally to GHA → GHCR → Watchtower. Pipeline: push to `main` → `.github/workflows/publish.yml` builds → `ghcr.io/astro-roro/astro-coverage-planner:latest` → Watchtower (label-scoped, 60s poll) recreates the `acp` container. Wait until two or three pushes have landed via this path and visibly rotated the container (check `docker logs acp-watchtower` for `Updated=1`) before doing the cleanup below.

- **Bin the local-build remnants on brutix** once Watchtower has picked up at least one push:
   - `/mnt/user/appdata/acp/repo/` — old git checkout; no longer pulled from
   - `/mnt/user/appdata/acp/scripts/update.sh` and its `update.log` — old auto-update script; replaced by Watchtower
   - The `acp-update` entry in User Scripts (already disabled, safe to delete the script + schedule.json entry)
- **Bin the safety-net backups on brutix** once cleanup above is done:
   - `/mnt/user/appdata/acp/docker-compose.yml.bak.*` (pre-GHCR compose)
   - `/boot/config/plugins/user.scripts/customSchedule.cron.bak`
   - `/boot/config/plugins/user.scripts/schedule.json.bak`
   - `/etc/cron.d/root.bak` (this one regenerates anyway, lowest priority)
- **If GHCR package is ever flipped to private** (currently public, inheriting the repo's visibility), Watchtower will start failing with auth errors. Fix: `docker login ghcr.io -u astro-roro -p <PAT>` on brutix as the user the Watchtower container runs as (writes `~/.docker/config.json`, which Watchtower reads automatically). PAT scope: `read:packages` is enough.
- **Watchtower notification channel** is currently disabled (`WATCHTOWER_NOTIFICATIONS_LEVEL: info` but no channel configured). If you want a ping when an update lands or fails, add e.g. `WATCHTOWER_NOTIFICATION_URL: ntfy://...` pointing at the existing `cal-sync-ntfy` container. Skip until you've decided you actually want the noise.

---

## Notes from the kick-off plan (2026-04-19)

Rollout order agreed: **Feature 1 → Feature 2 → Feature 3**. Feature 3 needs Feature 2's search grammar to express "show only incomplete plans".

Scanner change landing with Feature 2: split `INSTRUME` from `TELESCOP`. Scanner run can happen in background after the code ships.
