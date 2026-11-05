// Decides what the "newer release" notice looks like. Pure functions, so
// tests/frontend can run them without a DOM. app.js fetches /api/update, calls
// noticeFor() and draws the result.
//
// The louder the jump, the more visible the notice:
//   patch  a dot on the Help button and a line beside the version text in Help
//   minor  the same, plus a calm one-line banner under the toolbar
//   major  the same, plus a prominent banner

export const DISMISS_KEY = "acp.update.dismissed";

const LEVELS = ["patch", "minor", "major"];

// The version whose notice the user dismissed, or null when storage is
// unavailable (private windows and blocked site data throw).
export function dismissedVersion(storage) {
  try {
    return storage.getItem(DISMISS_KEY) || null;
  } catch {
    return null;
  }
}

export function dismiss(storage, version) {
  try {
    storage.setItem(DISMISS_KEY, String(version));
  } catch {
    // Nothing to do: the notice comes back next visit.
  }
}

// The link only ever points at the project's own releases.
function safeUrl(url) {
  return typeof url === "string" && url.startsWith("https://github.com/") ? url : null;
}

// `info` is the /api/update body. Returns null when nothing should show, else
// { level, version, url, text, linkText, banner, dot }.
export function noticeFor(info, dismissed) {
  if (!info || info.available !== true || !LEVELS.includes(info.jump)) return null;
  const version = typeof info.latest === "string" ? info.latest : null;
  if (!version || version === dismissed) return null;
  const docker = info.docker === true;
  const text = docker
    ? `ACP ${version} is out. Pull the new image to update.`
    : `ACP ${version} is out.`;
  return {
    level: info.jump,
    version,
    url: safeUrl(info.url),
    text,
    linkText: docker ? "Release notes" : "Download",
    banner: info.jump !== "patch",
    dot: true,
  };
}
