// Which list the main side panel returns to from a detail view (a tile, for
// now). The panel has two modes, set by the Coverage/Planning toggle:
// Coverage lists targets, Planning lists plans. A detail view opened from
// the sky map must go back to the mode it came from, not always to targets,
// otherwise the toggle says Planning while the panel body shows Targets and
// stays that way when the panel is closed and reopened.

export function listModeFor(planningMode) {
  return planningMode ? "plan-list" : "list";
}
