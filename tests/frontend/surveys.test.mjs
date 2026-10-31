import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  ALL_SURVEYS,
  CUSTOM_SURVEY_VALUE,
  DEFAULT_SURVEY_ID,
  OFFLINE_SURVEY,
  OFFLINE_SURVEY_VALUE,
  SURVEY_GROUPS,
  findSurvey,
  skyControlFor,
  surveyCaption,
  surveySelectValue,
} from "../../static/surveys.mjs";

describe("survey table", () => {
  it("has unique ids and a coverage note on every entry", () => {
    const ids = ALL_SURVEYS.map(s => s.id);
    assert.equal(new Set(ids).size, ids.length);
    for (const s of ALL_SURVEYS) {
      assert.ok(s.name && s.caption && s.coverage, `${s.id} is missing text`);
      assert.equal(typeof s.fullSky, "boolean", `${s.id} needs fullSky`);
      if (s.fullSky) assert.match(s.coverage, /^Full sky/);
      else assert.match(s.coverage, /^Partial sky/);
    }
  });

  it("defaults to Mellinger, the first Pretty entry", () => {
    assert.equal(SURVEY_GROUPS[0].label, "Pretty");
    assert.match(DEFAULT_SURVEY_ID, /Mellinger/);
  });
});

describe("findSurvey: matching what Aladin reports", () => {
  it("matches the exact id", () => {
    assert.equal(findSurvey("CDS/P/DSS2/red")?.id, "CDS/P/DSS2/red");
  });

  it("matches the short id without the CDS prefix, case-insensitively", () => {
    assert.equal(findSurvey("p/dss2/RED")?.id, "CDS/P/DSS2/red");
  });

  it("matches an id that ends with the survey path", () => {
    assert.equal(findSurvey("mirror/CDS/P/allWISE/color")?.id, "CDS/P/allWISE/color");
  });

  it("does not match a different survey that shares a prefix", () => {
    assert.equal(findSurvey("CDS/P/DSS2/blue"), null);
    assert.equal(findSurvey("CDS/P/DES-DR2/g"), null);
  });

  it("returns null for empty input", () => {
    assert.equal(findSurvey(""), null);
    assert.equal(findSurvey(undefined), null);
  });
});

describe("surveySelectValue", () => {
  it("maps a known layer to its option value", () => {
    assert.equal(surveySelectValue("P/Finkbeiner"), "CDS/P/Finkbeiner");
  });

  it("falls back to Custom when the layer was picked in Aladin's control", () => {
    assert.equal(surveySelectValue("CDS/P/SDSS9/color"), CUSTOM_SURVEY_VALUE);
  });
});

describe("surveyCaption", () => {
  it("labels a full-sky survey as full sky", () => {
    const c = surveyCaption("CDS/P/Finkbeiner");
    assert.equal(c.chipFull, true);
    assert.equal(c.chip, "Full sky");
    assert.match(c.text, /hydrogen/i);
  });

  it("labels a partial survey with its extent", () => {
    const c = surveyCaption("CDS/P/PanSTARRS/DR1/color-z-zg-g");
    assert.equal(c.chipFull, false);
    assert.match(c.chip, /^Partial sky/);
    assert.match(c.chip, /-30/);
  });

  it("explains an unknown survey instead of guessing", () => {
    const c = surveyCaption("CDS/P/SDSS9/color");
    assert.equal(c.chipFull, false);
    assert.match(c.chip, /unknown/i);
    assert.match(c.text, /Aladin/);
  });
});

describe("surveyRenderOptions", () => {
  it("gives H-alpha a red colour map so the single band is not greyscale", async () => {
    const { surveyRenderOptions } = await import("../../static/surveys.mjs");
    const r = surveyRenderOptions("CDS/P/Finkbeiner");
    assert.equal(r.colormap, "redtemperature");
    assert.equal(r.imgFormat, "fits");
  });

  it("returns null for surveys that render fine from the plain id", async () => {
    const { surveyRenderOptions } = await import("../../static/surveys.mjs");
    assert.equal(surveyRenderOptions("CDS/P/Mellinger/color"), null);
    assert.equal(surveyRenderOptions("CDS/P/DSS2/red"), null);
  });

  it("returns a fresh object each time", async () => {
    const { surveyRenderOptions } = await import("../../static/surveys.mjs");
    assert.notEqual(surveyRenderOptions("CDS/P/Finkbeiner"), surveyRenderOptions("CDS/P/Finkbeiner"));
  });
});

describe("the Sky control while the bundled Gaia sky shows", () => {
  const GAIA_URL = "http://localhost:5000/static/offline-sky";

  it("names the Gaia sky instead of reading Custom", () => {
    const s = skyControlFor(GAIA_URL, { offline: true });
    assert.equal(s.value, OFFLINE_SURVEY_VALUE);
    assert.notEqual(s.value, CUSTOM_SURVEY_VALUE);
    assert.equal(OFFLINE_SURVEY.name, "Built-in Gaia sky (offline)");
    assert.equal(s.text, "A low-resolution Gaia map bundled with ACP, shown while the survey servers are down. ESA/Gaia/DPAC, CC BY-SA 3.0 IGO.");
    assert.doesNotMatch(s.text, /Coverage unknown|Aladin's own layers control/);
  });

  it("carries the credit in the line the map shows", () => {
    assert.match(skyControlFor("x", { offline: true }).text, /ESA\/Gaia\/DPAC, CC BY-SA 3\.0 IGO/);
  });

  it("is not one of the surveys a user can pick or save", () => {
    assert.equal(findSurvey(GAIA_URL), null);
    assert.ok(!ALL_SURVEYS.some(s => s.id === OFFLINE_SURVEY_VALUE));
    assert.ok(!SURVEY_GROUPS.some(g => g.surveys.some(s => s.name === OFFLINE_SURVEY.name)));
  });

  it("leaves a normal layer alone when the Gaia sky is not showing", () => {
    const s = skyControlFor("CDS/P/DSS2/color");
    assert.equal(s.value, "CDS/P/DSS2/color");
    assert.equal(s.text, surveyCaption("CDS/P/DSS2/color").text);
    assert.equal(skyControlFor(GAIA_URL).value, CUSTOM_SURVEY_VALUE);
  });
});
