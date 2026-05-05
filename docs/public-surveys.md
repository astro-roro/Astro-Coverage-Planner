# Public survey overlays

ACP can pull public-survey footprints (IPHAS, VPHAS+, etc.) from CDS as MOCs and overlay them on the map alongside your own coverage. Useful for spotting where a survey already has Hα coverage so you can prioritise the gaps. Ships with one survey wired up; adding more is a one-line PR to `data/surveys.json`.

## What ships out of the box

**IPHAS DR2 Hα** (northern Galactic plane). Toggleable in the **Sources** rail, off by default.

## Adding a survey

Append an entry to `data/surveys.json`:

```json
{
  "id": "vphas_ha",
  "label": "VPHAS+ Hα",
  "color": "#5b9bc2",
  "filter": "Ha",
  "moc_url": "https://alasky.cds.unistra.fr/...",
  "attribution": "VPHAS+ DR4 (Drew et al. 2014)",
  "enabled_default": false
}
```

The loader enforces an HTTPS-only hostname allowlist (currently `alasky.cds.unistra.fr` and `alasky.u-strasbg.fr`). Adding other CDS mirrors or other survey hosts means editing the allowlist constant in `app.py` — flag this as the right place to push back in PR review.

## How fetching works

Lazy on first `/api/moc/<id>` hit. Cached at `data/moc_cache/<id>.fits` with a 30-day TTL and content-hash invalidation. Subsequent toggles re-use the cache, so the second view of a survey is instant.

## Hard limits enforced

- **10 MB per MOC** — bigger responses are rejected
- **30 second fetch timeout**
- **Response must parse as a FITS MOC via `mocpy`** before being cached, otherwise `502`

These exist to keep a misbehaving survey host from wedging the app.

## Without `mocpy`

ACP runs fine. Sources still appear in the rail but `/api/moc/<id>` returns `503`. Run `pip install mocpy` to enable.

## `ACP_SURVEYS_PATH`

Point at a custom JSON file to override the bundled registry — handy for testing new survey wirings or per-machine survey curation:

```bash
ACP_SURVEYS_PATH=./my_surveys.json python app.py
```

## Workflow tips

- Public-survey MOCs participate in the [gap-finder](api.md#gap-finder) the same way friend manifests do — their coverage counts toward the "have" side, so you can find regions where neither you nor a public survey has Hα.
- The `attribution` field is rendered in the Sources rail tooltip — keep it accurate; it's how readers know whose data they're looking at.
- `enabled_default: true` will turn a survey on at app startup. Use sparingly — every default-on survey is one more network fetch the user pays for on first launch.
