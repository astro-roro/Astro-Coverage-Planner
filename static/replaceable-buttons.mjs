// Pure decision logic for REPLACEABLE_BUTTONS (see app.js), split out so it
// can be unit tested without a DOM. An extension can swap a core button's
// label + handler by publishing a manifest action with a matching
// `replaces` id; some swaps (nina_ts_sync's direct-write NINA sync) only
// make sense when a resource the extension needs actually exists on this
// machine, so `meta.requiresTsDb` lets the core button opt out of the swap
// and show a hint explaining why instead.

export function pickButtonState(meta, extensionsManifest, tsDbAvailable) {
  for (const ext of extensionsManifest || []) {
    for (const action of (ext.actions || [])) {
      if (action.replaces !== meta.id) continue;
      if (meta.requiresTsDb && !tsDbAvailable) {
        return { mode: "default", hint: meta.hintWhenUnavailable || null };
      }
      return { mode: "extension", ext, action, hint: null };
    }
  }
  return { mode: "default", hint: null };
}
