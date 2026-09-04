# Filter catalogue for the archive scanner

Branch: `feat/filter-catalogue` (based on `feat/osc-band-model`, commit `d517d96`).

## What changed

1. `data/filter_catalogue.json`: a cached snapshot of AstroBin's public filter
   equipment database, with every MULTIBAND row resolved to specific coverage
   bands.
2. `scripts/build_archive_manifest.py`: a loader that reads the catalogue
   once and consults it from `canon_filter()` and `bands_for()`, after the
   existing hand tables (`FILTER_CANON`, `OSC_BAND_FILTERS`,
   `_BROADBAND_LIKE_NOFILTER`, `_MULTI_BAND`) so nothing already known
   changes behaviour. Also tracks filter names that resolved to nothing known
   and reports them in a new "Unrecognised filter names" section of
   `archive_manifest_summary.md` and the console summary.
3. `tests/test_filter_catalogue.py`: loader and precedence tests.

The scanner never calls AstroBin at scan time; the catalogue ships as a
committed file and is read from disk.

## Fetch

Fetched all 2,506 filter rows from
`https://www.astrobin.com/api/v2/equipment/filter/`, anonymously with a
`Mozilla/5.0` user agent, one page at a time with a one second pause between
pages and one retry on failure.

The task brief estimated ~100 rows per page over 26 pages. The endpoint
actually returns 50 rows per page, so this was 51 pages, not 26. All 51 pages
returned HTTP 200 on the first try, no retries were needed, and there was no
403/429 trouble to report.

## Resolution

| Resolution | Count |
|---|---:|
| `type` (astrobin type field settled it, including LP/OTHER/UV_IR_CUT etc. rows that keep their own product name as the band) | 2,359 |
| `name` (product name settled it) | 99 |
| `maker_page` (read the manufacturer's page) | 45 |
| `default` (MULTIBAND, unresolved after one search, defaulted to Ha+OIII) | 3 |

147 rows carried AstroBin's `MULTIBAND` type. Beyond the six simple
single-band types the brief listed (L, R, G, B, H_ALPHA, OIII, SII), the real
data also has `H_BETA`, `NII`, `UV`, `IR`, and `SOLAR` rows; these map to
their own canonical band (`Hb`, `NII`, `UV`, `IR`, `Solar`) the same way the
brief's six do. `LP`, `UV_IR_CUT`, `OTHER`, `UHC`, `PHOTOMETRIC_*`, `ND`,
`SKY_GLOW`, `LUNAR`, `COMETARY`, and a few blank/`Narrowband` type values
round out the rest; none of those are single emission lines, so they keep
their own product name as the band, the same "not planned against, still
shown" fallback the existing hand tables already use for things like `IR`
and `Sodium`.

### MULTIBAND resolution order

For the 147 MULTIBAND rows, 31 resolved straight off explicit band letters in
the product name (e.g. "Ha+OIII", "SII/OIII"), and the rest grouped into 56
distinct product families once physical-size variants ("1.25\"", "2\"",
"52 mm", "EOS APS-C") were stripped out. Most of those 56 were confidently
known from the task's well-known list or from general knowledge of the
product lines (Dual-Band/Duo-Band/Duo-Narrowband generically means Ha+OIII;
Quad-Band means Ha+OIII+SII, per the band model's own rule). 18 families were
genuinely ambiguous and got one web search each, reading the manufacturer's
or a major retailer's product page. Three stayed unclear after that search
and are flagged `default` (Ha+OIII):

- IDAS HE2-N3 Nikon Clip APS-C: no passband spec found for this model number.
- Sense-Tech (STC) Astrophotography Interchangeable Clip Filter for Nikon Z: generic product name, no passband spec found.
- Sightron Comet BP Filter: spec pages describe Sightron's Quad BP in detail but not the Comet BP.

### Maker pages read

- https://www.altairastro.help/info-instructions/faq/what-altair-filter-should-i-choose-for-deepsky-imaging/ (Altair TriBand = Ha+OIII+Hb, QuadBand = Ha+SII+OIII+Hb)
- https://starizona.com/products/antlia-quadband-anti-light-pollution-filter-2-mounted (Antlia Quad Band = Ha+OIII+SII+Hb)
- https://www.cloudynights.com/articles/astro-gear-today/reviews/a-hybrid-light-pollution-filter-for-color-imaging-antlia-triband-rgb-ultra-review-r4632/ (Antlia Tri Band RGB Ultra/Pro: a hybrid RGB/LP filter, kept as its own name rather than forced into Ha/OIII/SII)
- https://www.cloudynights.com/articles/cn-reports/accessories-reports/cn-report-the-dgm-optics-npb-nebula-filter-r1529/ (DGM NBP = Ha+OIII+Hb)
- https://idas.uno/space/en/IDAS/gnb.htm (IDAS GNB = Ha+OIII plus a NIR pass for galaxies)
- https://idas.uno/space/en/IDAS/nbx-pm.htm (IDAS NB2 = Ha+OIII+NII)
- https://agenaastro.com/idas-nb1-dual-band-h-a-h-b-oiii-nii-nebula-booster-filter-2-mounted-nb1-m48.html (IDAS NB1 = Ha+OIII+Hb)
- https://agenaastro.com/idas-nb3-dual-band-sii-oiii-nebula-booster-filter-2-mounted-nb3-pm-m48.html (IDAS NB3 / Astro Hutech IDAS NB3 = OIII+SII)
- https://agenaastro.com/idas-nbzex-12nm-h-a-oiii-dual-band-nebula-booster-narrowband-high-speed-imaging-filter-2-mounted-nbzex-m48.html (confirms IDAS NBX = Ha+OIII, same as its NBZex successor)
- https://www.koheisha.co.jp/vr3xfilter/vr3xfilter01.html (Koheisha VR3X boosts Ha relative to RGB for unmodified cameras, not a clean band split, so it was kept as its own name)
- https://agenaastro.com/optolong-l-para-dual-band-oiii-10nm-h-a-10nm-filter-2-mounted.html (Optolong L-Para = Ha+OIII, confirmed narrowband not broadband)
- https://www.cloudynights.com/forums/topic/647156-which-company-makes-the-radian-triad-ultra-hahbsii-and-oiii-quad-band-filter/ and https://optcorp.com/blogs/astronomy-gear/about-the-triad-filter-guide (Radian Triad Tri-band = Ha+OIII+Hb, Triad Ultra = Ha+OIII+SII+Hb)
- https://telescopes.net/accessories/imaging-accessories/filters-for-imaging/sightron-quad-band-hb-oiii-ha-sii-filter-48mm-2-sgt-qbp-48.html (Sightron Quad BP = Ha+OIII+SII+Hb)
- https://www.svbony.com/blog/difference-sv220-sv260-sv240 (SVBony SV240 = Ha+OIII+Hb; SV260 kept as its own name, described as broader/less selective than SV240)
- https://www.celestron.com/products/nebula-filter-for-the-celestron-origin-intelligent-home-observatory (Celestron Origin Nebula Filter = Ha+OIII+Hb)

### A discrepancy worth flagging

The task brief's sanity-check list says "Askar ColorMagic D1 and D2 are Ha
plus OIII" but also separately flags the 6nm D1/D2 pair as needing a page
read. The actual AstroBin rows state the passbands directly in the product
name, "ColourMagic D1 (Ha+Oiii)" and "ColourMagic D2 (Sii+Oiii)", and the
same pattern holds for the C1/C2 and E1/E2 pairs. I trusted the literal text
in the product name over the brief's paraphrase: D1/C1/E1 are Ha+OIII, and
D2/C2/E2 are SII+OIII, each an individual duo-band filter. Read as a pair
(D1 and D2 shot together) they do cover more total ground (Ha, OIII and SII)
than a single duo filter, which is probably what "are more" meant, but each
individual filter is still a two-band product.

## Loader design notes

- Normalisation for matching: casefold, collapse whitespace/punctuation to
  single spaces, strip a leading brand (from `_FILTER_BRANDS` or any brand
  seen in the catalogue).
- Catalogue keys are also built with AstroBin's own size/mount noise
  stripped out first (`2"`, `1.25"`, `52 mm`, `EOS APS-C`, `f/1.4-f/2`,
  `Mounted`, ...), since a real filter-wheel slot name never carries that.
  Otherwise a product AstroBin only ever lists as `L-Para 2''` would never
  match someone typing plain "L-Para".
- A bare product name (no brand) is only indexed when every catalogue row
  sharing that bare name agrees on the bands, so e.g. a generic "Duo-Band"
  typed without a brand only resolves if every brand's "Duo-Band" filter
  in the catalogue happens to agree (which, for Ha+OIII duo-band filters,
  they do).
- `bands_for()` checks `_BROADBAND_LIKE_NOFILTER`, `_MULTI_BAND`, and
  `canon_filter`'s own single-band vocabulary (Ha, OIII, SII, L, R, G, B, ...)
  before the catalogue, so a coincidental catalogue product literally named
  e.g. "Ha" can never relabel the plain single-band filter.
- `UNRECOGNISED_FILTER_COUNTS` is a module-level counter incremented inside
  `canon_filter()`'s final fallback. Because `canon_filter()` runs more than
  once over the same physical frame in a couple of code paths (header read,
  then folder-sub block grouping), the frame counts in the summary are
  approximate: good enough to paste into a GitHub issue, not an exact
  per-frame audit.

## Tests

`tests/test_filter_catalogue.py` (7 tests, all passing): brand/punctuation
normalisation, a MULTIBAND product resolved via the catalogue, an unknown
name returning itself unchanged, and two precedence tests proving the hand
tables win even against a deliberately conflicting fabricated catalogue
entry. Full suite: 385 tests passing (`pytest -q`), no regressions.

## Not done / follow-ups

- Could not `git push` the branch from this sandbox: no GitHub credentials
  are configured here (`fatal: could not read Username for 'https://github.com'`).
  Commits are on `feat/filter-catalogue` locally; someone with push access
  needs to push it.
- `data/filter_catalogue.json` is a point-in-time snapshot (fetched
  2026-09-04). It isn't refreshed automatically; re-running the fetch script
  logic periodically and re-resolving any new MULTIBAND rows would keep it
  current.
