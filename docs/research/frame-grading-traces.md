# Frame grading traces: how rejected and accepted lights show up on disk

Researched 2026-09-04/05 to ground the imaged-vs-accepted split the coverage scanner needs to add. The scanner currently counts every light frame it finds as "imaged" with no idea whether a grader later rejected it (see `scripts/build_archive_manifest.py`), so anyone who culls subs gets over-reported coverage. This document is the evidence base for detection rules, not the rules themselves.

Sources: NINA Target Scheduler plugin source (cloned from `https://github.com/tcpalmer/nina.plugin.targetscheduler.git`, read directly), NINA's own official FITS header docs, PixInsight's official docs and forum, Siril's GitLab source and readthedocs docs, and forum/vendor research for the remaining tools. Every claim below is tagged with where it came from and how sure we are.

## 1. NINA Target Scheduler grader

Read directly from source: `NINA.Plugin.TargetScheduler/Grading/ImageGrader.cs`, `GraderExpert.cs`, `ImageGradingController.cs`, `Database/Schema/AcquiredImage.cs`, `Utils/Utils.cs`, `NINA.Plugin.TargetScheduler.Shared/Utility/ImageMetadata.cs`.

**Rejected frames.** When `EnableMoveRejected` is on in the grader preferences, a rejected file is moved into a subfolder literally named `rejected`, created next to the original file if it doesn't exist (`ImageGrader.cs:27` defines `REJECTED_SUBDIR = "rejected"`; `MoveRejected()` at line 145 does `Path.Combine(Path.GetDirectoryName(fileLocalPath), REJECTED_SUBDIR)`, then `Utils.MoveFile` at `Utils.cs:206` does a plain `File.Move`). If `EnableMoveRejected` is off (it defaults off in NINA's UI), the file is never touched at all; only the database record changes.

**Nothing is written into the FITS header.** Grepping the whole plugin source for header-writing calls (`SetHeaderKeyword`, `Header[`, `FITSHeader`, etc.) found nothing. The plugin never opens the FITS file again after capture to add a verdict.

**The verdict lives only in the plugin's own sqlite database.** `AcquiredImage.cs:22-23` stores `gradingStatus` (an int enum: Pending/Accepted/Rejected) and `rejectreason` (a string, one of "Guiding RMS", "Star Count", "HFR", "FWHM", "Eccentricity", "Manual", see `ImageGrader.cs:29-34` and `GradingResultToReason` at line 179). This is the plugin's own database file (`schedulerdb.sqlite`), separate from any FITS file. A scanner with no access to that database cannot see grading status directly; it can only infer it from the file move.

**Accepted frames** are left in place. Nothing moves, nothing is renamed, nothing is written to them. The only accepted-side effect is `exposurePlan.Accepted++` in the plugin's database (`ImageGrader.cs:156`).

**What the grader reads to decide.** Not FITS header fields at all, it reads `ImageMetadata` fields populated in the plugin's database at capture time, sourced from NINA's in-memory `ImageSavedEventArgs` (`ImageMetadata.cs:60-107`): `DetectedStars`, `HFR`, `HFRStDev`, `FWHM`, `Eccentricity` (the latter two only present if the separate Hocus Focus plugin is installed and configured for star detection, `GraderExpert.cs:94,121` explicitly warn "Is Hocus Focus installed..." when these are NaN), and guiding RMS (`GuidingRMS`/`GuidingRMSScale`/`GuidingRMSArcSec`). These numbers exist in the plugin's database, not necessarily in the FITS file itself, see section 2.

Confidence: high. All claims are direct source reads with file:line citations.

## 2. NINA's own FITS header writes

Checked against NINA's official docs at `https://nighttime-imaging.eu/docs/master/site/advanced/file_formats/fits/`.

NINA core writes a substantial FITS header on every captured light: `SIMPLE, BITPIX, NAXIS, NAXIS1/2, BZERO, EXTEND, SWCREATE, IMAGETYP, EXPOSURE, EXPTIME, DATE-LOC, DATE-UTC, ROWORDER, SITEELEV/LAT/LONG, OBJECT, OBJCTRA, OBJCTDEC, INSTRUME, XBINNING, YBINNING, GAIN, OFFSET, EGAIN, XPIXSZ, YPIXSZ, SET-TEMP, CCD-TEMP, READOUTM, BAYERPAT, XBAYROFF, YBAYROFF, TELESCOP, FOCALLEN, FOCRATIO, RA, DEC, FWHEEL, FILTER, FOCNAME, FOCPOS, FOCUSPOS, FOCUSSZ, FOCTEMP, FOCUSTEM, ROTNAME, ROTATOR, ROTATANG, ROTSTPSZ, CLOUDCVR, DEWPOINT, HUMIDITY, PRESSURE, SKYBRGHT, MPSAS, SKYTEMP, STARFWHM, AMBTEMP, WINDDIR, WINDGUST, WINDSPD`.

**None of these are grading-relevant.** HFR, detected star count, guiding RMS and eccentricity are not written to the FITS header by NINA core at all. Two independent sources corroborate this: a forum answer stating there are no standard FITS keywords for star count/HFR so "one can easily fashion a plugin to insert them under whatever keyword you desire," and the README for a different plugin, `tcpalmer/nina.plugin.sessionmetadata`, which exists specifically to capture "HFR, detected stars, guiding RMS and more" into a sidecar because NINA's own header writing doesn't cover it. This is strong, independent confirmation that the Target Scheduler grading inputs genuinely only exist in-memory/in-plugin-database at capture time, never in the FITS header, unless a user has separately installed a plugin like sessionmetadata for that purpose.

One near-miss: `STARFWHM` exists in the documented list, but it is an ambient seeing-monitor reading (weather-station category, requires connected hardware), not the per-frame image-analysis FWHM that Hocus Focus computes. Do not confuse the two.

Confidence: high for "the documented core header list contains no grading metrics." Not settled: whether Hocus Focus itself ever writes FWHM/Eccentricity to the header independent of any FITS metadata plugin, no documentation found either way. Settling this would need a real Hocus-Focus-enabled capture's FITS header inspected directly.

## 3. PixInsight

### SubframeSelector

Source: official doc, archived (`web.archive.org/web/20210507093033/https://pixinsight.com/doc/scripts/SubframeSelector/SubframeSelector.html`; the live URL currently 404s).

The weight keyword is **user-configured, not a fixed name PixInsight enforces**. The doc's "Output Subframes" section: "This is the custom FITS keyword used to record subframe weights in copied subframes... If this field is left blank, subframe weights will not be recorded." `SSWEIGHT` is a community convention people type into that field (it's what ImageIntegration's own "FITS keyword" weighting mode expects by default), not something the doc mandates or defaults to. Weights are written only into **copied** subframes, never into moved ones, and only if that field was filled in.

There is no evidence individual metrics (FWHM, Eccentricity, SNR weight, star count) get written into the header automatically; the doc only describes the single custom weight keyword plus a CSV export of the full metrics table.

**No default approved/rejected folder names exist.** The doc is explicit: "Output directories for approved and rejected subframes may be specified... If this field is left blank, approved subframes will be copied or moved to the same directories as their corresponding target files." Same wording for rejected. SubframeSelector does not create or name folders itself; it can be pointed at whatever the user typed in.

Confidence: high that SSWEIGHT is not a spec'd default (direct quote), medium that most real archives will still contain SSWEIGHT literally, because it's the near-universal tutorial convention.

### WBPP (Weighted Batch Preprocessing)

No standalone official WBPP reference doc could be located (WBPP is a PJSR script versioned outside PixInsight's normal doc tree; no archive.org snapshot exists either). Findings below come from the official PixInsight forum, a lower confidence tier than a doc page.

**Frame exclusion is mostly a weighting decision, not a whole-file move.** Forum consensus (`pixinsight.com/forum/index.php?threads/wbpp-subframe-weighting.22141/`): a poor frame typically gets near-zero weight in ImageIntegration rather than being dropped outright as a separate file operation; WBPP itself does not move, rename, or relocate excluded files. Users in that thread describe writing their own external scripts to shuffle rejects into a manually named folder (e.g. `LIGHT\saved`) as a workaround, which itself confirms this isn't a built-in WBPP behaviour.

**The combined-frame-count keyword is probably a HISTORY line, not NCOMBINE.** Two independent web searches turned up consistent secondary-source claims that PixInsight's ImageIntegration writes `HISTORY ImageIntegration.numberOfImages: <N>` into the output header, format string `"ImageIntegration.numberOfImages: %d"`. This could not be independently verified: the `PixInsight/PCL` GitHub repository that both search results cited does not actually resolve (`gh api repos/PixInsight/PCL/...` returns 404; `gh repo view PixInsight/PCL` cannot resolve it either), so the repo is either private, renamed, or the search index is stale. Treat this as medium confidence, not settled; do not hard-code `NCOMBINE` as PixInsight's keyword on the strength of this alone. Settling it would need either a working PCL source checkout or, more reliably, a real WBPP-produced FITS/XISF header inspected directly.

**No structured exclusion log file.** No doc or forum thread found describing WBPP writing a JSON/text report of which frames were excluded and why. Process Console output (visible during the run) is not persisted to disk by default.

Confidence: medium overall for the whole WBPP section; this is the weakest-sourced tool in this document.

## 4. Siril

Source: direct read of Siril's GitLab source (`gitlab.com/free-astro/siril`, branch master), files `src/io/seqfile.c`, `src/io/fits_keywords.c`, `src/io/image_format_fits.c`, `src/stacking/stacking.c`.

**The `.seq` sequence file is a plain-text sidecar, and the selection flag is literally called `incl`.** Each per-frame line is written as `I filenum incl\n` (variable-size sequences add width/height), from `seqfile.c:778-780`: `fprintf(seqfile,"I %d %d\n", seq->imgparam[i].filenum, seq->imgparam[i].incl);`, read back at lines 203-206/229-231. It's a plain int: 1 = included/selected, 0 = excluded/deselected. There is no separate rejection-reason field. The sequence header line (`#S ...`) also carries a `nb_selected` count.

**The underlying FITS files are never touched by deselection.** No rename/move call anywhere near the exclusion-writing path in `seqfile.c` or `sequence.c`. Deselecting a frame in Siril's UI only flips the `incl` flag in the `.seq` text file; the FITS file itself stays exactly where it was, under its original name. So a scanner needs the `.seq` file alongside the FITS files to see rejection at all, the FITS files carry no trace of their own.

**The stacked output's combined-frame-count keyword is `STACKCNT`, with `NCOMBINE` also read/written for cross-tool compatibility.** `fits_keywords.c:409-410`: `KEYWORD_PRIMARY("stack","STACKCNT",...)` / `KEYWORD_SECONDA("stack","NCOMBINE",...)`; also `image_format_fits.c:62`: `static char *NB_STACKED[] = { "STACKCNT", "NCOMBINE", NULL };`. So Siril's own convention is `STACKCNT`, but it treats `NCOMBINE` as an alias, meaning a Siril-stacked master may carry either or both.

**Sigma-clip and similar stacking-time rejection is pixel-level, not per-frame**, and leaves no per-frame trace. `stacking.c:307-337` shows all the rejection algorithms (sigma, percentile, Winsorized, GESDT) reject individual pixel values during the combine, not whole source frames. The only optional on-disk artefact is a rejection map (a per-pixel count image, `create_rejmaps` in `stacking.c:189-212`), which does not identify which source frames a rejected pixel came from. So there is no way to recover "frame X was rejected by sigma clipping" from anything Siril writes; that information genuinely doesn't exist on disk.

Confidence: high across the board; all claims are direct source reads with file:line citations.

## 5. Manual conventions

The scanner already recognises pipeline-stage folders and aliases in `scripts/build_archive_manifest.py`: `STAGE_FOLDER_ALIASES` (`cal` maps to calibrated, `reg`/`aligned` map to registered, `masters`/`integration` map to master, `original`/`originals`/`original_fits` map to og) and `_CANON_STAGE_FOLDERS` (`calibrated, registered, master, og, starless, stars`), plus `prefilter_calibration()`'s explicit calibration-folder patterns (`/flats/`, `/darks/`, `/bias/`, `/biases/`, `/flat/`, `/dark/`, `/.calibration/`, `/master darks/`, `/master flats/`). None of these existing patterns cover a rejected-frames folder, so a reject alias is new territory, not a duplicate.

Forum research (Cloudy Nights, AstroBin forum, astrobackyard tutorials) found no thread that debates reject-folder naming as its own topic; the evidence is all incidental mentions inside broader "how do I cull subs" threads, so treat this section as lower confidence than the source-code sections above.

**"reject" / "rejects" / "rejected"** is the only name that recurs verbatim across independent sources as an actual folder name. A Cloudy Nights thread on culling subs describes moving "the offenders to a folder" with survivors going to a "good" folder; an astrobackyard piece on PixInsight's Blink tool frames the workflow the same way ("move to a reject folder"). "Bad"/"bad frames" shows up almost as often, but purely as descriptive language inside these threads, not as a folder name anyone actually named their folder. "Trash", "junk", "discard", "culled" appear only as verbs, never as a quoted folder name. One AstroBin thread names the survivors' folder "SFS" (SubFrameSelector output) and just deletes the raw-lights folder, meaning the rejects leave no folder at all, only a name change on the accepted side. One mention of a Siril-specific "Dump Folder" also showed up, again on the accepted/moved-out side rather than a reject convention.

Bottom line: `reject`, `rejects`, `rejected` (case-insensitive) is the manual-convention pattern worth encoding; `bad` is a weaker second-tier candidate. Everything else is too thin to encode with any confidence.

## 6. Other mainstream tools

**AstroPixelProcessor (APP).** No evidence found that APP marks per-frame accept/reject status anywhere a filesystem scan could see. Its own forum confirms the opposite problem: APP currently strips original FITS header content on integration rather than adding to it (a moderator says future versions may restore more per-frame info, implying it doesn't today). No public documentation of the `.apf` project file's internal structure was found, so whether per-frame weighting survives there is unknown. Skip APP for now; there is nothing solid to key a detection rule on.

**DeepSkyStacker (DSS).** This is a genuine, well-documented signal. DSS's file-list format (`.dssfilelist`, plain text, per DSS's own user guide and command-line manual) has a header row `CHECKED<TAB>TYPE<TAB>FILE` followed by one line per frame with a 1/0 checked flag, the frame type, and the full path. Unchecking a frame in the DSS UI just flips that flag to 0; only checked frames feed registration/stacking. If a user saves this list file alongside their FITS files (not automatic, but common practice), it is a plain-text, fully external, per-frame accept/reject record readable with zero DSS involvement. Worth encoding as a signal if that sidecar file is present.

**Sequence Generator Pro (SGP).** No evidence of any built-in frame-grading or rejection feature; SGP's documented feature set is capture/sequencing/plate-solving/guiding/calibration, not quality-based file management. No official statement found that says "SGP has no grading feature" (so this is an absence-of-evidence call, not a documented negative), but nothing suggests it leaves any trace. Skip it: no signal to detect.

**ASIAIR.** Acquisition-only. Its autofocus routine computes HFR purely to find best focus during the autofocus run, logged in autofocus graphs, not attached to individual captured subs afterward. No accept/reject or culling feature is documented anywhere in ZWO's manual or forum. Skip it: it has no rejection concept at all, so there is nothing for a scanner to find.

## Detection order for the scanner

Ranked strongest to weakest evidence, since these signals can and will coexist in one archive and the strongest available one should win:

1. **A `rejected/` subfolder sitting next to light frames in an otherwise-NINA-shaped folder** (Target Scheduler's own convention, confirmed from source). A frame that used to live in the parent folder and now lives in `rejected/` next to it is rejected; everything else in that same parent is accepted, but only if the archive shows other evidence of Target Scheduler's presence (its own database file, `schedulerdb.sqlite`, or the plugin's characteristic session/target folder shape), since a bare `rejected` folder alone could be a manual convention instead (see rule 5).
2. **A Siril `.seq` file's `incl` flags**, cross-referenced by `filenum` against FITS filenames in the same folder. This is the single most granular, most reliable per-frame signal found in this whole research pass: an explicit 1/0 per frame, in a plain-text file, with no ambiguity about what it means. Requires the `.seq` file to be present alongside the FITS files; if it's missing, this signal is unavailable even for a Siril-processed archive.
3. **A DeepSkyStacker `.dssfilelist` sidecar's CHECKED column**, same idea as the Siril `.seq` file: explicit, per-frame, plain text. Weaker only in that saving the list file next to the data isn't automatic, so its absence tells you nothing.
4. **`SSWEIGHT` (or whatever custom keyword name was configured) present in a frame's header**, from PixInsight SubframeSelector. This tells you the frame passed through SubframeSelector and was copied out with a weight recorded; it does not by itself prove there's a sibling "rejected" copy anywhere, since output directories are user-typed and not standardised. Treat this as "this frame was graded and is presumably a survivor," not as proof of what happened to any missing sibling.
5. **A manually named folder matching `reject`/`rejects`/`rejected` (case-insensitive)**, extending the scanner's existing `STAGE_FOLDER_ALIASES` pattern the same way `cal` maps to calibrated already works. Weakest of the strong signals because it is a human convention with no enforcement behind it: a folder named `reject` could just as easily be an old export a user is unrelated to grading. Only apply this when the folder sits alongside frames that are otherwise recognised as lights for the same target/session, the same way `prefilter_calibration()` already scopes calibration folder matches to specific path segments rather than matching anywhere in the tree.
6. **PixInsight WBPP's near-zero-weight-instead-of-exclusion behaviour, and its HISTORY-line frame count** (medium confidence, unverified keyword spelling): not usable as a hard detection signal, because WBPP does not reliably move or mark excluded files at all; a human normally has to build their own workaround for that. At most, a `HISTORY` line resembling `ImageIntegration.numberOfImages: <N>` in a PixInsight-produced master could be compared against the count of raw lights found for that target to flag a gap, but treat this as advisory, not authoritative, given the keyword itself is unverified from source.

**Fallback rule, stated explicitly: no evidence of grading anywhere in an archive means accepted equals imaged.** The scanner should never assume rejection happened just because a tool that *can* grade was used (e.g. NINA headers present, but no Target Scheduler `rejected/` folder and no `schedulerdb.sqlite`); absence of a positive signal is absence of rejection, not evidence of grading with 100% acceptance.

### Where signals contradict, and where false rejections can happen

- **PixInsight SubframeSelector's rejected-folder name is not standardised**, so a scanner cannot assume any particular folder name means "rejected by SubframeSelector" the way it safely can for Target Scheduler's `rejected/`. If a user typed `bad` or `culls` into that field, it will look identical to a manual convention and there is no way to disambiguate them from folder name alone.
- **Siril's `STACKCNT`/`NCOMBINE` aliasing means a scanner reading `NCOMBINE` off a stacked master cannot assume PixInsight produced it**; Siril writes the same keyword. If both tools are in play in one archive, the keyword alone doesn't identify which tool did the stacking; other headers (e.g. PixInsight's own version/HISTORY strings, or Siril's own signature keywords) would be needed to disambiguate.
- **A frame sitting in a folder literally named `rejected` is not proof it failed quality grading.** Target Scheduler's own `MoveRejected` only fires when `EnableMoveRejected` is turned on and only for images that failed its enabled metrics; a user could just as easily have hand-created a `rejected` folder for an unrelated reason (an old session's leftovers, a folder renamed during reorganisation). This is the generic manual-convention risk already noted in rule 5, worth restating here because it's the same false-positive shape as Target Scheduler's own folder name.
- **A false rejection risk inherent to Target Scheduler's own grader**: `ImageGrader.cs:111-114` explicitly accepts any frame when fewer than 3 comparison images exist yet ("not enough matching images => accepted"), and `GraderExpert.cs:90-92` accepts everything when no metrics are enabled at all. Neither of these is a scanner problem, but it means a `rejected/` folder's absence in an otherwise Target-Scheduler-shaped archive genuinely can mean "every frame passed," not "grading never ran."
- **NINA's own FITS header carries nothing that would let a scanner reconstruct a rejection decision independently of any plugin.** If a Target Scheduler `rejected/` folder or database is missing (e.g. it was archived without the sqlite file, or the plugin was uninstalled after capture), there is no fallback source of grading truth in the FITS headers themselves. This is the single biggest hole this research surfaced: NINA-captured light frames are, by themselves, indistinguishable from ungraded frames no matter how thoroughly they were graded at the time, unless a sidecar (the plugin database, a `.seq` file, a `.dssfilelist`) survived alongside them.
