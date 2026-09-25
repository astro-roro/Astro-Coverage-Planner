// TS project state (docs/specs/ts-project-settings.md section 1) as it
// shows up in the plan list: a badge for anything but active, no badge
// for active or an absent state so the ordinary list looks as it always
// has. Pulled out of app.js so the label rule has one place, testable
// without a browser.

const STATE_LABELS = { draft: "Draft", inactive: "Inactive", closed: "Closed" };

export function planStateBadgeLabel(state) {
  return STATE_LABELS[state] || null;
}
