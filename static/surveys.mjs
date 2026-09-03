// Curated sky surveys for the Sky dropdown in the map toolbar.
//
// Aladin Lite's own layers control lists hundreds of HiPS surveys by id and
// only shows what each one is on hover. Most users have no idea which to pick
// (issue #46). This table is the short, described list ACP offers instead.
// Aladin's control stays available for anyone who wants the full catalogue.
//
// Every entry carries a coverage note. Partial-sky surveys have a hard edge
// where the tiles stop, and without the note a user reads that edge as a bug.
// Ids and sky fractions were checked against the CDS MocServer on 2026-09-03.

export const SURVEY_GROUPS = [
  {
    label: "Pretty",
    surveys: [
      {
        id: "CDS/P/Mellinger/color",
        name: "Mellinger (natural colour)",
        caption: "Wide-field colour photographs stitched into one panorama. Looks like the night sky to the eye. Goes soft when zoomed into a single target.",
        fullSky: true,
        coverage: "Full sky",
      },
      {
        id: "CDS/P/DSS2/color",
        name: "DSS2 colour",
        caption: "Digitized Sky Survey photographic plates, blended into colour. Sharper than Mellinger when zoomed in.",
        fullSky: true,
        coverage: "Full sky",
      },
      {
        id: "CDS/P/DSS2/red",
        name: "DSS2 red (greyscale)",
        caption: "Digitized Sky Survey red plates only, shown in greyscale. Nebulae show well.",
        fullSky: true,
        coverage: "Full sky",
      },
      {
        id: "CDS/P/PanSTARRS/DR1/color-z-zg-g",
        name: "Pan-STARRS (northern detail)",
        caption: "Deep modern colour imaging from Hawaii. The most detailed option for northern targets.",
        fullSky: false,
        coverage: "Partial sky: north of declination -30, about 78% of the sky",
      },
      {
        id: "CDS/P/DES-DR2/ColorIRG",
        name: "Dark Energy Survey (southern detail)",
        caption: "Deep modern colour imaging from Chile. The most detailed option for the southern sky away from the Milky Way.",
        fullSky: false,
        coverage: "Partial sky: about 5,000 square degrees of the southern sky, 13% of the total",
      },
    ],
  },
  {
    label: "Useful",
    surveys: [
      {
        id: "CDS/P/Finkbeiner",
        name: "H-alpha (hydrogen)",
        caption: "Glowing hydrogen gas, shown in red. Emission nebulae stand out, so this is the map to use when planning narrowband targets.",
        fullSky: true,
        coverage: "Full sky",
        // Single-band FITS tiles. Without a colour map Aladin shows them in
        // greyscale; red is what people expect hydrogen to look like. The
        // cuts are Aladin's own preset for its "P/Finkbeiner" alias.
        render: { imgFormat: "fits", colormap: "redtemperature", minCut: -10, maxCut: 800, stretch: "linear" },
      },
      {
        id: "CDS/P/allWISE/color",
        name: "Infrared (WISE)",
        caption: "Warm dust and stars behind the dust. Dark nebulae that hide things in visible light glow here.",
        fullSky: true,
        coverage: "Full sky",
      },
      {
        id: "CDS/P/2MASS/color",
        name: "Near infrared (2MASS)",
        caption: "Just past red light. Sees through dust to the stars behind it, so star clusters and the galactic bulge pop.",
        fullSky: true,
        coverage: "Full sky",
      },
      {
        id: "CDS/P/GALEXGR6/AIS/color",
        name: "Ultraviolet (GALEX)",
        caption: "Hot young stars and star-forming regions. Galaxies with active star formation light up.",
        fullSky: false,
        coverage: "Partial sky: about 80% of the sky, with gaps near the Milky Way and bright stars",
      },
      {
        id: "CDS/P/PLANCK/R2/HFI/color",
        name: "Microwave (Planck)",
        caption: "Cold dust across the whole galaxy. Shows where the dust lanes and molecular clouds are.",
        fullSky: true,
        coverage: "Full sky",
      },
      {
        id: "CDS/P/RASS",
        name: "X-ray (ROSAT)",
        caption: "Very hot gas: supernova remnants, galaxy clusters and active galaxies. Very different from the visible sky.",
        fullSky: true,
        coverage: "Full sky",
      },
    ],
  },
];

export const ALL_SURVEYS = SURVEY_GROUPS.flatMap(g => g.surveys);

/**
 * Options to pass to Aladin's HiPS factory for a survey, or null when the
 * plain id is enough. Returns a fresh object each call so Aladin can own it.
 */
export function surveyRenderOptions(id) {
  const s = ALL_SURVEYS.find(x => x.id === id);
  return s?.render ? { ...s.render } : null;
}

export const DEFAULT_SURVEY_ID = ALL_SURVEYS[0].id;

/** Value the dropdown shows when the map's base layer is not in the table. */
export const CUSTOM_SURVEY_VALUE = "__custom__";

// Aladin reports layer ids in more than one shape depending on how the survey
// was chosen: "P/Mellinger", "CDS/P/Mellinger/color", or a full tile URL.
// Compare a canonical lowercased form with the "CDS/" prefix removed.
function canonical(id) {
  return String(id || "").trim().toLowerCase().replace(/^cds\//, "").replace(/\/+$/, "");
}

/**
 * Find the table entry that matches a layer id or tile URL from Aladin.
 * Returns null when the layer is not one of ours (chosen via Aladin's control).
 */
export function findSurvey(idOrUrl) {
  const probe = canonical(idOrUrl);
  if (!probe) return null;
  for (const s of ALL_SURVEYS) {
    const mine = canonical(s.id);
    if (probe === mine) return s;
    // URL form, e.g. https://alasky.cds.unistra.fr/Pan-STARRS/DR1/color-z-zg-g/
    // does not carry the id, so only match ids that end in the survey path.
    if (probe.endsWith("/" + mine)) return s;
  }
  return null;
}

/** Dropdown value for a layer id: the survey id, or CUSTOM_SURVEY_VALUE. */
export function surveySelectValue(idOrUrl) {
  return findSurvey(idOrUrl)?.id ?? CUSTOM_SURVEY_VALUE;
}

/**
 * Text for the caption under the toolbar. Returns { chip, chipFull, text }.
 * `chip` is the coverage label, `chipFull` says whether to style it as full
 * sky, `text` is the one-line description.
 */
export function surveyCaption(idOrUrl) {
  const s = findSurvey(idOrUrl);
  if (!s) {
    return {
      chip: "Coverage unknown",
      chipFull: false,
      text: "A survey chosen from Aladin's own layers control. Pick one from the Sky list for a description.",
    };
  }
  return { chip: s.coverage, chipFull: s.fullSky, text: s.caption };
}
