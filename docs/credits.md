# Credits

ACP is licensed under the GNU General Public License v3 or later (see `LICENSE`). It stands on other people's software, sky surveys and catalogues. This page lists each one, how ACP uses it, its licence, and the credit its authors ask for.

Licences were read from the package metadata and licence files of the Python environment used for development (Python 3.14). The Windows download pins versions for Python 3.12, so a licence could differ in a later release. Where a licence or citation could not be read from a file in this repository or from package metadata, the entry says "to confirm" instead of guessing.

## Sky surveys and CDS services

ACP does not store any survey image. The browser loads tiles straight from CDS (Centre de Données astronomiques de Strasbourg), and the images belong to their authors.

### Aladin Lite and CDS

- What it is: the sky viewer that draws the map. Version 3.8.2, loaded from `aladin.cds.unistra.fr`, or from the npm copy on `cdn.jsdelivr.net` when CDS does not answer (`static/init-error.mjs`, called from `static/app.js`). It is not copied into this repository.
- How ACP uses it: every sky background and the HiPS browser come from it.
- Licence: to confirm. No licence file for it exists in this repository.
- Credit requested: citation to confirm. Check the Aladin Lite documentation at CDS for the paper and wording before publishing.

### Survey backgrounds

The Sky dropdown (`static/surveys.mjs`) offers these HiPS surveys, served by CDS. ACP asks for them by CDS identifier.

| Survey | CDS identifier | Credit |
|---|---|---|
| Mellinger natural colour | `CDS/P/Mellinger/color` | Axel Mellinger. Permission to use it in the README images is being requested. Citation to confirm. |
| DSS2 colour and red | `CDS/P/DSS2/color`, `CDS/P/DSS2/red` | Digitized Sky Survey. Credit wording to confirm. |
| Pan-STARRS DR1 | `CDS/P/PanSTARRS/DR1/color-z-zg-g` | Pan-STARRS1 Surveys. Credit wording to confirm. |
| Dark Energy Survey DR2 | `CDS/P/DES-DR2/ColorIRG` | Dark Energy Survey. Credit wording to confirm. |
| H-alpha full sky | `CDS/P/Finkbeiner` | Finkbeiner. Citation to confirm. |
| WISE infrared | `CDS/P/allWISE/color` | WISE / AllWISE. Credit wording to confirm. |
| 2MASS near infrared | `CDS/P/2MASS/color` | Two Micron All Sky Survey. Credit wording to confirm. |
| GALEX ultraviolet | `CDS/P/GALEXGR6/AIS/color` | GALEX. Credit wording to confirm. |
| Planck microwave | `CDS/P/PLANCK/R2/HFI/color` | Planck Collaboration. Credit wording to confirm. |
| ROSAT X-ray | `CDS/P/RASS` | ROSAT All-Sky Survey. Credit wording to confirm. |

Each survey's licence and acknowledgement text is listed in the HiPS properties file that CDS serves with the tiles. ACP does not read that file, so none of the wording above comes from it.

### Where the README images get their background

- The hero video poster, `docs/images/hero-coverage-map.png`, uses the Mellinger background (`docs/images/CAPTURES.md`).
- The first frame of `survey-swap.gif` and of `smart-search.gif` looks like the Mellinger panorama.
- `survey-swap.gif` then cycles through optical, H-alpha, infrared and radio surveys. I could not tell which radio survey from a still frame.
- The first frames of `feature-catalogues.gif` and `planning-mode.gif` are zoomed in and grainy. They look like DSS2 colour, but I could not confirm that from the file.
- The hero video itself was not checked, because the MP4 is hosted on GitHub and not in this repository.

### Public survey footprints (MOC)

- What it is: coverage footprints (Multi-Order Coverage maps) fetched from `alasky.cds.unistra.fr` and cached locally (`data/surveys.json`, `docs/public-surveys.md`).
- Surveys shipped:
  - IPHAS DR2 Hα. The repo's own attribution string is "INT/IPHAS DR2 (Drew et al. 2014)".
  - VPHAS+ DR4 Hα. The repo's own attribution string is "VST/VPHAS+ DR4 (Drew et al. 2014)".
- Licence: to confirm.
- Check before publishing: both strings cite the same paper, "Drew et al. 2014". I believe that is the VPHAS+ paper and that IPHAS DR2 has a different one, but I could not verify that offline.

### SIMBAD and VizieR

- What it is: CDS name resolver and catalogue services. The browser queries `simbad.cds.unistra.fr` and `vizier.cds.unistra.fr` for sky search (`static/sky-search.mjs`). `scripts/fetch_catalogs.py` downloads catalogues through VizieR.
- Licence: service terms, to confirm.
- Credit requested: wording to confirm. CDS publishes acknowledgement text for each service.

## Catalogues

`scripts/fetch_catalogs.py` builds the catalogue overlays. The Download catalogues button on the Settings page runs the same script. Nothing is committed to the repository: the file is written on the user's machine.

| Catalogue | Source | Credit and licence |
|---|---|---|
| Green SNRs | VizieR `VII/284`. The repo's string is "Green 2019 catalogue of confirmed Galactic SNRs". | Citation to confirm. Terms to confirm. |
| SMGPS candidates | VizieR, tried under four paper identifiers (`J/MNRAS/530/4928` and three others). The repo's string is "SARAO MeerKAT Galactic Plane Survey SNR candidates". | Citation to confirm. The script keeps an empty list if none matches, so this overlay may be empty. |
| WISE HII | VizieR `J/ApJS/212/1`. The repo's string is "Anderson 2014 WISE HII region catalogue". | Citation to confirm. Terms to confirm. |
| Sharpless | VizieR `VII/20`. The repo's string is "Sharpless 2 catalogue of HII regions (VizieR VII/20)". | Citation to confirm. |
| ESO planetary nebulae | VizieR `V/84`. The repo's string is "Strasbourg-ESO catalogue of Galactic PNe (VizieR V/84)". | Citation to confirm. |
| Messier | 110 coordinates typed into `fetch_catalogs.py`. The script does not say where the numbers came from. | Source to confirm. |

## Earth orientation data

- What it is: the `astropy-iers-data` package carries the IERS Earth rotation and leap second tables that Astropy needs for time and altitude calculations.
- How ACP uses it: it comes in through Astropy. ACP does not call it directly.
- Licence: BSD-3-Clause for the package (`astropy_iers_data` licence file). The terms of the IERS tables inside it are to confirm.
- Credit requested: none stated in the package metadata.

## JavaScript, fonts and icons

- JavaScript: ACP has no vendored or npm runtime libraries. `static/` holds ACP's own code, and the only outside script is Aladin Lite from CDS (above). `package.json` exists for the Node test runner and has no dependencies.
- Fonts: none are bundled or fetched. The stylesheets name system fonts only (`style.css`, `setup.css`).
- Icons: `static/favicon.svg` is a plain diamond and star drawn for ACP. The Windows download draws its icons from the same shapes.

## Python packages

Everything below installs with `pip` from `requirements.txt`. The Docker image and the Windows download redistribute these packages. Licence is as read from each package's metadata.

### Direct dependencies

| Package | Use in ACP | Licence | Credit requested |
|---|---|---|---|
| Flask | web framework | BSD-3-Clause | none stated |
| Astropy | coordinates, time, FITS headers | BSD-3-Clause | Cite the Astropy papers (Astropy Collaboration 2013, 2018, 2022). The package's `CITATION` file gives the exact wording and BibTeX. |
| NumPy | arrays and geometry | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | none stated |
| SciPy | sky matching for the priority export | BSD (licence text in the wheel, plus bundled OpenBLAS under BSD) | none stated |
| Astroquery | VizieR downloads in `fetch_catalogs.py` | BSD | Cite Ginsburg et al. 2019, AJ 157, 98 (named in the package README). |
| MOCpy | survey footprints (optional) | BSD License | none stated |
| xisf | reads `.xisf` image headers in the archive scan (optional) | GNU GPL v3 | none stated. ACP is GPL v3 too, so xisf ships inside the Windows download. |
| Waitress | production web server | ZPL 2.1 | none stated |
| croniter | scheduled rescan | MIT | none stated |
| Paramiko | SFTP upload for the live page | LGPL-2.1 | none stated |
| tzdata | time zone data on Windows | Apache-2.0 (PyPI metadata, tzdata 2026.5) | none stated |

### Packages installed alongside

| Package | Licence |
|---|---|
| astropy-iers-data | BSD-3-Clause |
| bcrypt | Apache-2.0 |
| beautifulsoup4 | MIT |
| blinker | MIT (classifier only) |
| certifi | MPL-2.0 |
| cffi | MIT-0 |
| charset-normalizer | MIT |
| click | BSD-3-Clause |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| html5lib | MIT |
| idna | BSD-3-Clause |
| invoke | BSD-2-Clause |
| itsdangerous | BSD (classifier only) |
| jaraco.classes, jaraco.context, jaraco.functools | MIT |
| Jinja2 | BSD (classifier only) |
| keyring | MIT |
| lz4 | BSD (classifier only) |
| MarkupSafe | BSD-3-Clause |
| more-itertools | MIT |
| packaging | Apache-2.0 OR BSD-2-Clause |
| pycparser | BSD-3-Clause |
| pyerfa | BSD-3-Clause |
| PyNaCl | Apache-2.0 (bundles libsodium, its licence file ships in the wheel) |
| python-dateutil | BSD and Apache-2.0 dual licence |
| pyvo | BSD-3-Clause |
| PyYAML | MIT |
| requests | Apache-2.0 (ships a NOTICE file) |
| six | MIT |
| soupsieve | MIT |
| typing-extensions | PSF-2.0 |
| urllib3 | MIT |
| webencodings | BSD (classifier only) |
| Werkzeug | BSD-3-Clause |
| zstandard | BSD-3-Clause |

Astropy's wheel also carries licence files for code it bundles, including WCSLIB (LGPL-3.0), ERFA, jQuery, DataTables, PLY and configobj. Those files sit in `astropy-*.dist-info/licenses/licenses/`.

## Extras in the Windows download

The Windows download (on the `packaging/windows-download` branch, not yet merged) adds the following to the zip. Licences for the first group could not be read, because they were not installed in the development environment.

| Item | Use | Licence |
|---|---|---|
| Python 3.12.10 embeddable package | runs ACP without an install | PSF licence, to confirm against the `LICENSE.txt` inside the python.org zip |
| pystray | the notification-area icon | LGPL-3.0 (PyPI metadata, 0.19.5) |
| Pillow | draws the tray and shortcut icons | MIT-CMU (PyPI metadata, 13.0.0) |
| colorama (Windows only) | pulled in by other packages | BSD-3-Clause (PyPI classifier, 0.4.6) |
| pywin32-ctypes (Windows only) | pulled in by keyring | BSD-3-Clause (PyPI metadata, 0.2.3) |

## Interoperating with other projects

- NINA (Nighttime Imaging 'N' Astronomy) and its Target Scheduler plugin. ACP's bundled sync (`bundled_extensions/nina_ts_sync`) reads and writes the Target Scheduler SQLite database. The plugin source is `tcpalmer/nina.plugin.assistant` (named in `docs/nina-plugin-research.md`). ACP contains none of that code. Licence and credit wording to confirm.
- `docs/nina-plugin-api-audit.md` cites `Josh-Jones-76/AstroPM.NINA.Plugin` (MIT, per that document) and `christian-photo/ninaAPI` as references. No code from either is copied here.

## Hero video

The music in the README video is "Space Ambient" by Monume, under the Pixabay Content License, which needs no attribution (`docs/images/CAPTURES.md`). It is credited here anyway.

## Docker image

The Docker image starts from the official `python:3.12-slim` image (Debian based). Its licences are those of Python and the Debian packages inside it.
