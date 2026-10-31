// The notice under the Sky dropdown that says the survey servers are not
// answering. Pure logic over a few elements, so tests/frontend/ can drive it
// with stand-ins. The banner is shown only when a check has finished and every
// source failed; callers never show it while a check is still running.

export const MESSAGES = {
  // Every remote source for the default sky failed; the built-in Gaia sky is showing.
  cds: "The sky survey servers (CDS) are not responding, so the map is showing a built-in Gaia sky. Your data and plans are fine. ACP switches back to the full surveys by itself when CDS is back.",
  // Only the survey the user picked failed; their choice is kept.
  survey: "That survey's server is not responding. Your choice is kept, and the map will draw it when the server comes back. Try again later.",
};

// `els` is { root, text, retry }. `onRetry` runs when Retry is pressed.
export function createSkyBanner(els, onRetry = () => {}) {
  let kind = null;
  els.retry?.addEventListener("click", () => onRetry());
  return {
    show(next) {
      if (!MESSAGES[next]) return;
      kind = next;
      if (els.text) els.text.textContent = MESSAGES[next];
      if (els.root) els.root.hidden = false;
    },
    hide() {
      kind = null;
      if (els.root) els.root.hidden = true;
    },
    // Retry greys out while the check runs, so a second press cannot stack.
    setBusy(busy) {
      if (els.retry) els.retry.disabled = !!busy;
    },
    get kind() { return kind; },
  };
}
