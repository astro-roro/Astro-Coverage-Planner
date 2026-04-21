# Todo — parked features & open questions

Active work lives in the Claude task list. This file is for features queued after Features 1 & 2 ship, and open design questions the user hasn't reviewed yet.

## Feature 3 — Session planner (DRAFT, awaiting review)

**Concept:** a `plan` per target (or per proposed framing) that captures:
- Framing: center RA/Dec, camera, telescope, **rotation angle** in NINA "Rotator PA" convention (0° = +Y axis points North, increasing east-of-north — matches NINA's framing-assistant field directly).
- Per-filter goals: `{ target_hours, sub_exposure_s }` rows.
- Derived status per filter: `actual_hours / target_hours` → complete / in-progress / not-started.
- Plans attach to existing targets OR stand alone (a future target you haven't shot yet).

**Storage:** `data/plans.json` + `data/gear.json` (user-editable list of camera/telescope presets with pix scale & sensor dimensions so the FOV box auto-derives). New REST endpoints:
- `GET/PUT /api/plans`
- `GET/PUT/DELETE /api/plans/<id>`
- `GET /api/gear`

**UI (recommended — option 1 of three considered):**
1. Topbar "Mode" toggle: **Coverage ↔ Planning**. In planning mode the rail becomes the planner (plan list + editor); the map additionally renders planned FOVs as dashed outlines. Coverage mode stays exactly as it is today.
2. (rejected) Planning as a section inside the existing per-target panel — no way to manage plans for unshot targets, no global "what's incomplete" view.
3. (rejected) Separate `/plan` page — too much context switching.

Plus: a small **"remaining hours"** block appears inside the existing per-target detail panel whenever a plan exists for that target — gives both the global planning view and an at-a-glance per-target view without duplicating controls.

**NINA niceties:**
- Rotation shown in degrees with a **"copy to NINA"** button that emits the framing JSON payload NINA's framing assistant accepts.
- Coordinates formatted as `HH:MM:SS` / `±DD:MM:SS` for plate-solve paste-in.

**Depends on Feature 2** for `status:complete|incomplete` search tokens.

---

## Open design questions (after Features 1 & 2)

1. **Workflow:** feature branch + PR per phase, or push direct to `main`? Matters more now that the repo is public and shareable with astro friends.
2. **Feature 3 extras:**
   - Mobile layout? Current panel is fixed 380px — won't work on phones.
   - Plan export format — CSV? JSON? NINA sequence file?
   - NINA integration beyond copy-paste (e.g. NINA's HTTP API if it exposes one)?
3. **Catalog sharing (parked 2026-04-20):** add a "sanitise for sharing" mode that strips `master_files` local paths (and anything else machine-specific) from the manifest before export. Use cases: swap coverage maps with astro friends to see who has hours on what, collaborate on top-up imaging, show off an archive publicly without leaking drive letters / usernames / folder structure. Implementation sketch: `--sanitise` flag on the scanner, or a button in the UI that exports a cleaned copy. Park until after Target Scheduler integration lands.
4. **Sky-visibility overlay from observer location (parked 2026-04-20):** surface "what's actually reachable from where I am" on the map. Ideas to pick from:
   - Shade the portion of the celestial sphere that is *never* above the user's minimum altitude from their latitude (always-below-horizon band).
   - Let the user override location (stored in localStorage already via `/api/observability`) for travel / remote-site planning.
   - Per-target "best time of year" badge: months during which the target clears the plan's `min_altitude_deg` for more than N hours of astronomical darkness.
   - Stretch: overlay the current night's altitude curve for a selected target on the side panel.
5. **Bug — catalog overlays not rendering (parked 2026-04-20):** toggling the SNR/SMGPS/EMU/HII checkboxes in the catalogs section produces no visible markers on the map. Either the catalog data was never ingested, the ingestion step silently failed, or the overlay is being built but the sources aren't attaching to Aladin. Needs a full trace: (a) does `data/catalogs.json` exist and contain rows? (b) is the `/api/catalogs` response non-empty? (c) do the catalog catalogs (e.g. `snrCat`, `hiiCat`) get populated in `redrawCatalogs` / wherever the toggle handler lives? Fix before demoing the catalogs accordion to anyone.
6. **Deselect target on Esc / empty-sky click (parked 2026-04-20):** when a target is selected, pressing `Escape` OR clicking on empty sky (no FOV polygon under cursor) should return to the target list, same as clicking the "← Back to list" link. Keep the back-link for discoverability, but the mouse/keyboard shortcut is what feels natural. Same behaviour should apply in Planning mode (Esc / empty-sky click → back to plan list). Implementation hint: the map-click handler already knows when there are zero hits (`hits.length === 0` path in `onMapPolyClick`) — wire that plus a document-level `keydown` Esc listener into `renderTargetList()` / `renderPlanList()`.
7. **Bug — page is slightly taller than the viewport (parked 2026-04-21):** the layout is very close to fitting in one screen but overflows by a few pixels, so the page gains a vertical scrollbar when nothing should need to scroll. Likely culprit after the topbar restructure: `.layout` is sized to `calc(100vh - 44px)` in `static/style.css`, which assumes a 44px topbar — but with padding/borders the actual topbar is taller, or `body`/`html` has residual margin pushing total height past 100vh. Fix: measure the real topbar height (e.g. with `getBoundingClientRect`) and set the layout height from that, or switch the layout to `flex: 1` inside a `display: flex; flex-direction: column; height: 100vh` body so it just absorbs whatever space the topbar leaves.
8. **Prettier README (parked 2026-04-20):** replace the current plain-text README with a showcase-style landing page so astro friends / strangers landing on the repo can see at a glance what this thing does and why they'd want it. Should include:
   - A hero screenshot of the coverage map with telescope-coloured FOVs and filter badges.
   - Short GIF / video of the core loops: scan → open app → click a FOV → see filter coverage; planning-mode demo (drag a plan, mosaic handle, sync to NINA Target Scheduler).
   - Feature bullets with thumbnail screenshots (search grammar, catalog overlays, gear editor, TS sync zip).
   - "Who this is for" + quickstart (clone → pip install → run scanner → `flask run`).
   - Screenshot of the panel UI (after the top/right restructure lands, otherwise it'll go stale immediately).
   - Consider hosting captures in `docs/images/` so they're versioned with the repo.

---

## Notes from the kick-off plan (2026-04-19)

Rollout order agreed: **Feature 1 → Feature 2 → Feature 3**. Feature 3 needs Feature 2's search grammar to express "show only incomplete plans".

Scanner change landing with Feature 2: split `INSTRUME` from `TELESCOP`. Scanner run can happen in background after the code ships.
