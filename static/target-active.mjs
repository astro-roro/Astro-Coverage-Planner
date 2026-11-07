// A plan's own scheduling switch and its per-panel ones, apart from the
// project's state. Mirrors filter_rows.target_active: `target_active` is
// stored only as false and `panel_active` holds only "row,col" keys set to
// false, so absent means on and a plan from before these fields is unchanged.
// Target Scheduler gets them as each target's `active` column.

export function planScheduled(plan) {
  return plan?.target_active !== false;
}

export function panelScheduled(plan, key) {
  if (!planScheduled(plan)) return false;
  const panels = plan?.panel_active;
  return !(panels && typeof panels === "object" && panels[key] === false);
}

// The panel's own switch, ignoring the plan's, for drawing the panel toggle.
export function panelSwitchOn(plan, key) {
  const panels = plan?.panel_active;
  return !(panels && typeof panels === "object" && panels[key] === false);
}

export function setPlanScheduled(plan, on) {
  if (on) delete plan.target_active;
  else plan.target_active = false;
}

export function setPanelScheduled(plan, key, on) {
  const panels = { ...(plan.panel_active && typeof plan.panel_active === "object" ? plan.panel_active : {}) };
  if (on) delete panels[key];
  else panels[key] = false;
  if (Object.keys(panels).length) plan.panel_active = panels;
  else delete plan.panel_active;
}

// [{key, label}] for every panel of a rows x cols mosaic, in the order the
// sync numbers them: "Panel 1 (R1C1)", then along the row.
export function panelList(rows, cols) {
  const out = [];
  for (let r = 1; r <= rows; r++) {
    for (let c = 1; c <= cols; c++) {
      out.push({ key: `${r},${c}`, label: `Panel ${(r - 1) * cols + c} (R${r}C${c})` });
    }
  }
  return out;
}
