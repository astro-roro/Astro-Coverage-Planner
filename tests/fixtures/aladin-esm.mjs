// Packaged the way npm's aladin-lite dist/aladin.js is: plain code, then a
// default export, and no global `A`. Loaded as a classic <script> it fails
// to parse on the export. Used by tests/frontend/init-error.test.mjs.
const v = { init: Promise.resolve(), aladin() {} };
const P = v;
export { P as default };
