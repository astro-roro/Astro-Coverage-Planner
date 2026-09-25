// The plan list's pure parts: which plans show under the status chips and
// the search box, how they sort, and how they roll up into projects. Kept
// out of app.js so tests/frontend/ can check them without a browser.

export const PLAN_STATES = ["active", "draft", "inactive", "closed"];
export const DEFAULT_PLAN_STATES = ["active", "draft"];
export const STATE_NAMES = { active: "Active", draft: "Draft", inactive: "Inactive", closed: "Closed" };
export const NO_PROJECT = "(no project)";

const PRIORITY_RANK = { high: 3, normal: 2, low: 1 };

// A plan with no state predates the field and has always been treated as
// active (plan-state.mjs follows the same rule).
export function planState(plan) {
  return plan?.state || "active";
}

export function planProject(plan) {
  return plan?.project_name || NO_PROJECT;
}

export function planPanelCount(plan) {
  const m = plan?.target?.mosaic || {};
  const rows = Math.max(1, parseInt(m.rows) || 1);
  const cols = Math.max(1, parseInt(m.cols) || 1);
  return rows * cols;
}

// Hours still to shoot, summed over every filter goal and multiplied by the
// mosaic panel count. Same sum the plan row has always shown.
export function planHoursLeft(plan) {
  let left = 0;
  for (const g of Object.values(plan?.filter_goals || {})) {
    left += Math.max(0, (g?.target_hours || 0) - (g?.actual_hours || 0));
  }
  return left * planPanelCount(plan);
}

// Hours done and hours wanted, for a progress bar. Hours shot past a goal
// don't count, so one overshot filter can't hide another that is empty.
export function planProgress(plan) {
  let done = 0, total = 0;
  for (const g of Object.values(plan?.filter_goals || {})) {
    const t = Math.max(0, g?.target_hours || 0);
    total += t;
    done += Math.min(Math.max(0, g?.actual_hours || 0), t);
  }
  const k = planPanelCount(plan);
  return { done: done * k, total: total * k };
}

export function planMatchesQuery(plan, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return true;
  const hay = [plan?.target?.name, plan?.project_name, plan?.id];
  return hay.some(s => typeof s === "string" && s.toLowerCase().includes(q));
}

export function stateCounts(plans) {
  const c = { active: 0, draft: 0, inactive: 0, closed: 0 };
  for (const p of plans) {
    const s = planState(p);
    c[s] = (c[s] || 0) + 1;
  }
  return c;
}

// Plans that pass the search box and the ticked status chips.
export function filterPlans(plans, { states, query } = {}) {
  const want = states instanceof Set ? states : new Set(states || PLAN_STATES);
  return plans.filter(p => want.has(planState(p)) && planMatchesQuery(p, query));
}

function planName(p) {
  return p?.target?.name || p?.id || "";
}

const byName = (a, b) => planName(a).localeCompare(planName(b), "en-AU", { numeric: true });

// Sort a copy. "priority" is high, normal, low, then name. "name" is by
// target name. "hours_left" puts the most work left first. Anything else
// falls back to priority, so the time-aware sorts in app.js can layer on
// top without this function knowing about them.
export function sortPlans(plans, by) {
  const arr = plans.slice();
  if (by === "name") {
    arr.sort(byName);
  } else if (by === "hours_left") {
    arr.sort((a, b) => planHoursLeft(b) - planHoursLeft(a) || byName(a, b));
  } else {
    arr.sort((a, b) =>
      (PRIORITY_RANK[b.priority] || 2) - (PRIORITY_RANK[a.priority] || 2) || byName(a, b));
  }
  return arr;
}

// One row per project that still has a plan showing after the filters.
// `plans` counts toward the state mix and the "n of m" total; `shown` is
// the filtered list the row sums its hours and progress over.
export function projectRollups(allPlans, shown, by) {
  const shownIds = new Set(shown.map(p => p.id));
  const groups = new Map();
  for (const p of allPlans) {
    const key = planProject(p);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  const out = [];
  for (const [name, all] of groups) {
    const visible = all.filter(p => shownIds.has(p.id));
    if (!visible.length) continue;
    let hoursLeft = 0, done = 0, total = 0, topPriority = 0;
    for (const p of visible) {
      hoursLeft += planHoursLeft(p);
      const g = planProgress(p);
      done += g.done;
      total += g.total;
      topPriority = Math.max(topPriority, PRIORITY_RANK[p.priority] || 2);
    }
    out.push({
      name,
      plans: visible,
      totalPlans: all.length,
      stateMix: stateCounts(all),
      hoursLeft,
      done,
      total,
      topPriority,
    });
  }
  const nm = (a, b) => a.name.localeCompare(b.name, "en-AU", { numeric: true });
  if (by === "name") out.sort(nm);
  else if (by === "hours_left") out.sort((a, b) => b.hoursLeft - a.hoursLeft || nm(a, b));
  else out.sort((a, b) => b.topPriority - a.topPriority || b.plans.length - a.plans.length || nm(a, b));
  return out;
}

// Plain-words summary of a state mix, e.g. "3 active, 1 draft".
export function stateMixText(mix) {
  return PLAN_STATES.filter(s => mix[s]).map(s => `${mix[s]} ${STATE_NAMES[s].toLowerCase()}`).join(", ");
}

// Saved chip selection, read back defensively: unknown names are dropped,
// and a missing or empty save falls back to Active and Draft.
export function parseSavedStates(saved) {
  if (!Array.isArray(saved)) return new Set(DEFAULT_PLAN_STATES);
  const ok = saved.filter(s => PLAN_STATES.includes(s));
  return new Set(ok.length ? ok : DEFAULT_PLAN_STATES);
}
