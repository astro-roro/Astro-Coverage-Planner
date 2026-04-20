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

---

## Notes from the kick-off plan (2026-04-19)

Rollout order agreed: **Feature 1 → Feature 2 → Feature 3**. Feature 3 needs Feature 2's search grammar to express "show only incomplete plans".

Scanner change landing with Feature 2: split `INSTRUME` from `TELESCOP`. Scanner run can happen in background after the code ships.
