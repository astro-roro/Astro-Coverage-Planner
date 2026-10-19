// Which list the main side panel returns to from a detail view (a tile, for
// now). The panel has two modes, set by the Coverage/Planning toggle:
// Coverage lists targets, Planning lists plans. A detail view opened from
// the sky map must go back to the mode it came from, not always to targets,
// otherwise the toggle says Planning while the panel body shows Targets and
// stays that way when the panel is closed and reopened.

export function listModeFor(planningMode) {
  return planningMode ? "plan-list" : "list";
}

// Where a detail screen should send Back when it was opened from somewhere
// other than a list. An entry such as {kind: "tile", sourceId, tileId, title,
// scrollTop} is pushed when a tile panel action navigates away, along with
// `dest`, a key naming the screen it opens (for example "plan:<id>"). That
// screen asks bind(key) when it renders: it gets the entry only if the entry
// was pushed for it. Any other screen, or a list, clears the stack, so a
// Back control never outlives the visit it belongs to.
export function createNavStack() {
  let stack = [];
  return {
    push(entry) { stack.push({ ...entry }); },
    pop() { return stack.pop() || null; },
    clear() { stack = []; },
    peek() { return stack.length ? stack[stack.length - 1] : null; },
    bind(key) {
      const top = stack.length ? stack[stack.length - 1] : null;
      if (top && top.dest === key) return top;
      stack = [];
      return null;
    },
    get size() { return stack.length; },
  };
}
