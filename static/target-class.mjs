// A plan's target class, the one setting AAC reads to decide how it handles
// stars when it builds masters (docs/specs/goal-panel-and-ts-order.md
// section 6). Absent, or any value outside the list, means survey. Pure, so
// `node --test` can check the field.

export const TARGET_CLASSES = ["survey", "deep", "pretty"];
export const DEFAULT_TARGET_CLASS = "survey";

export const TARGET_CLASS_LABELS = {
  survey: "Survey",
  deep: "Deep",
  pretty: "Pretty",
};

export const TARGET_CLASS_HINT = "Read by Astro Auto Calibration when it builds masters.";

export function targetClassOf(plan) {
  const v = plan?.target_class;
  return TARGET_CLASSES.includes(v) ? v : DEFAULT_TARGET_CLASS;
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// The Target fieldset's select. Its id is what the editor wires.
export function targetClassFieldHtml(plan) {
  const current = targetClassOf(plan);
  const options = TARGET_CLASSES.map(c =>
    `<option value="${c}"${c === current ? " selected" : ""}>${esc(TARGET_CLASS_LABELS[c])}</option>`).join("");
  return `<label title="${esc(TARGET_CLASS_HINT)}"><span class="lab">Class</span>
          <select class="wide" id="f_target_class">${options}</select>
        </label>`;
}

// What a change of the select stores on the plan. An unknown value is
// ignored, so the plan keeps what it had.
export function setTargetClass(plan, value) {
  if (!TARGET_CLASSES.includes(value)) return plan;
  plan.target_class = value;
  return plan;
}
