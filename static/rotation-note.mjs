// A framing with a position but no recorded angle is drawn north up. Seestar
// frames are the common case (alt-az and equatorial wedge alike write no
// rotation), but any frame without a solved WCS lands here.

export const ROTATION_UNKNOWN_TIP = "Rotation unknown: this framing is drawn north up.";
export const ROTATION_UNKNOWN_NOTE =
  "Rotation not known. The framing is drawn north up. Plate-solve these frames to record the real angle.";

// Manifests built before rotation was recorded carry no rotation_source at all.
// The icon shows when the source is "none", or missing on a target that has a
// position but no angle.
export function rotationUnknown(t) {
  if (!t) return false;
  const hasPosition = Number.isFinite(t.center_ra_deg) && Number.isFinite(t.center_dec_deg);
  if (!hasPosition) return false;
  const src = t.rotation_source;
  if (src === "none") return true;
  return !src && !Number.isFinite(t.rotation_deg);
}

// A rotation arrow with a line through it. Stroke follows the text colour.
const ICON = `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.6-3.7"/><path d="M13 2.5v3h-3"/><path d="M2.5 13.5l11-11"/></svg>`;

export function rotationUnknownButtonHtml() {
  return `<button type="button" class="rot-unknown" id="rotUnknownBtn" title="${ROTATION_UNKNOWN_TIP}" aria-label="${ROTATION_UNKNOWN_TIP}" aria-expanded="false" aria-controls="rotUnknownNote">${ICON}</button>`
    + `<p class="rot-unknown-note" id="rotUnknownNote" role="status" hidden>${ROTATION_UNKNOWN_NOTE}</p>`;
}

// Enter and Space already click a button; this only flips the note.
export function wireRotationUnknown(root) {
  const btn = root.querySelector("#rotUnknownBtn");
  const note = root.querySelector("#rotUnknownNote");
  if (!btn || !note) return;
  btn.addEventListener("click", () => {
    const open = note.hidden;
    note.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  });
}
