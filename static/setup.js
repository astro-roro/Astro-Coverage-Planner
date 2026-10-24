// Setup and settings page (docs/specs/windows-download-and-setup.md, section 2.3).
// The page carries no local path in its HTML. Every path arrives from
// /api/settings, which hides them from anyone who is not an authorised client.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const JSON_HEADERS = { "Content-Type": "application/json" };

  const PATH_ERRORS = {
    not_absolute: "That is not a full path. A full path starts with a drive letter such as D:\\ or with a slash.",
    not_found: "ACP cannot find that folder. Check the spelling, and that any external drive or network share is connected.",
    not_a_directory: "That is a file, not a folder. Add the folder that holds the files.",
    not_readable: "ACP is not allowed to read that folder.",
    timed_out: "ACP could not reach that folder. It did not answer within 5 seconds. If it is on a network drive, check that the drive is switched on and awake.",
    invalid: "That path has characters ACP cannot use, or it is too long.",
    duplicate: "That folder is already in the list.",
    too_many: "ACP can scan up to 32 folders.",
    locked_by_env: "These folders are set by an environment variable, so they cannot be changed here.",
    scan_running: "A scan is running. Wait for it to finish, then change the folders.",
    not_writable: "ACP cannot write its settings file on this install.",
    write_failed: "ACP could not save the settings file. Check that its folder is writable.",
    forbidden: "Folders can only be changed from the computer ACP runs on.",
    bad_host: "Open this page as localhost or 127.0.0.1 to change settings.",
    unauthorized: "Sign in again, then retry.",
  };
  const MAC_PRIVACY = " On a Mac, open System Settings, then Privacy & Security, and allow ACP (or your terminal) to use Files and Folders or Full Disk Access.";

  const WARNINGS = {
    overlaps: "This folder sits inside another folder in the list, or contains one. That is allowed, but it is usually not needed.",
    very_large: "This is the top of a whole drive. A scan of it can take a very long time. A folder closer to your images is usually better.",
    no_images_seen: "ACP found no FITS or XISF files in this folder. It was added, but if your images are in a parent folder, add that folder instead.",
  };

  const SCAN_ERRORS = {
    no_archive_roots: "ACP found no image folders to read. Check the folders above.",
    permission_denied: "ACP could not read part of your image folders. Check that it is allowed to open them, then scan again.",
    path_not_found: "ACP could not find part of your image folders. Check that any drive or network share is connected.",
    out_of_memory: "The scan ran out of memory. Try scanning fewer folders at a time.",
    disk_full: "The scan ran out of disk space.",
    could_not_start: "The scan could not be started. The log has the reason.",
    crashed: "The scan failed. The log has the reason.",
  };

  const STAGES = {
    listing: "Looking for image files",
    reading: "Reading file headers",
    matching: "Working out your targets",
    writing: "Saving the results",
  };

  const CATALOGUE_ERRORS = {
    network: "Could not reach the catalogue service. Check the internet connection and try again.",
    crashed: "The download failed. The log has the reason.",
    timed_out: "The download took too long and was stopped. Try again.",
    could_not_start: "The download could not be started. The log has the reason.",
  };

  const os = (() => {
    const ua = navigator.userAgent || "";
    if (/Windows/i.test(ua)) return "windows";
    if (/Mac/i.test(ua)) return "mac";
    return "linux";
  })();

  const state = {
    settings: null,
    mode: "settings",
    folders: [],
    scanWatched: false,
    pollTimer: null,
    redirectTimer: null,
    catalogueTimer: null,
  };

  async function api(method, path, body) {
    const init = { method, headers: {} };
    if (method !== "GET") {
      init.headers = JSON_HEADERS;
      init.body = JSON.stringify(body || {});
    }
    let resp;
    try {
      resp = await fetch(path, init);
    } catch (err) {
      return { status: 0, body: { error: "unreachable" } };
    }
    let data = null;
    try {
      data = await resp.json();
    } catch (err) {
      data = null;
    }
    return { status: resp.status, body: data || {} };
  }

  function show(el, on) { el.hidden = !on; }

  function pathMessage(code) {
    let text = PATH_ERRORS[code] || "ACP could not use that folder.";
    if (code === "not_readable" && os === "mac") text += MAC_PRIVACY;
    return text;
  }

  function setMsg(text, kind) {
    const el = $("pathMsg");
    el.textContent = text || "";
    el.classList.toggle("warn", kind === "warn");
    show(el, !!text);
  }

  function pathHelpText() {
    const win = "On Windows 11, right-click the folder and choose Copy as path. On Windows 10, hold Shift while you right-click the folder, then choose Copy as path.";
    const mac = "On a Mac, right-click the folder in Finder, hold the Option key and choose Copy as Pathname.";
    const lin = "On Linux, type the full path, such as /home/you/astro.";
    if (os === "windows") return win + " Then paste it here.";
    if (os === "mac") return mac + " Then paste it here.";
    return lin + " Network folders work too, once they are mounted.";
  }

  // --- Rendering ---------------------------------------------------------

  function render() {
    const s = state.settings;
    const keys = s.keys || {};
    const roots = keys.fits_roots || {};
    const known = Array.isArray(roots.value);
    const setupMode = state.mode === "setup";

    $("pageTitle").textContent = setupMode ? "Welcome to Astro Coverage Planner" : "Settings";
    $("pageLead").textContent = setupMode
      ? "ACP shows which parts of the sky you have already photographed. To start, tell it where your images are and it will scan them. It only reads your files."
      : "";
    show($("pageLead"), setupMode);

    const readOnly = s.writable === false;
    show($("readonlyCard"), readOnly);
    show($("remoteCard"), !readOnly && !known);

    // A read-only install (Docker) lists its folders and changes nothing: no
    // Add, Remove, Scan or catalogue buttons.
    const canEdit = !readOnly && known;
    show($("foldersCard"), known);
    show($("scanCard"), canEdit);
    show($("catalogCard"), canEdit);
    show($("infoCard"), !setupMode && known);

    if (!known) return;

    state.folders = known ? roots.value.slice() : [];
    renderFolders(roots);
    $("scanBtn").disabled = state.folders.length === 0 || state.scanWatched;
    $("pathHelp").textContent = pathHelpText();
    show($("skipLink"), setupMode);

    if (known) {
      $("infoData").textContent = s.data_dir || "";
      $("infoManifest").textContent = (keys.manifest_path || {}).value || "";
      $("infoPort").textContent = String((keys.port || {}).value || "");
      $("infoVersion").textContent = s.version || "";
      // A container cannot know the port it is published on, and its settings
      // come from the compose file, not a settings file.
      $("infoPortLabel").textContent = readOnly ? "Port inside the container" : "Port";
      show($("infoHintWritable"), !readOnly);
      show($("infoHintReadOnly"), readOnly);
    }
  }

  function renderFolders(rootsKey) {
    const list = $("folderList");
    list.textContent = "";
    const locked = !!rootsKey.locked;
    state.folders.forEach((folder, index) => {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.className = "folder-path";
      span.textContent = folder;
      li.appendChild(span);
      if (!locked && state.settings.writable !== false) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "Remove";
        btn.addEventListener("click", () => removeFolder(index));
        li.appendChild(btn);
      }
      list.appendChild(li);
    });
    show($("folderEmpty"), state.folders.length === 0);
    const readOnly = state.settings.writable === false;
    show($("lockedBox"), locked && !readOnly);
    show($("adoptBox"), locked && !readOnly);
    show($("addForm"), !locked && !readOnly);
    show($("pathHelp"), !locked && !readOnly);
    show($("folderEmpty"), state.folders.length === 0 && !readOnly);
  }

  async function loadSettings() {
    const r = await api("GET", "/api/settings");
    if (r.status !== 200) {
      setMsg("ACP could not load its settings. Reload the page to try again.");
      return null;
    }
    state.settings = r.body;
    return r.body;
  }

  // --- Folders -----------------------------------------------------------

  async function saveFolders(next) {
    const r = await api("PUT", "/api/settings", { fits_roots: next });
    if (r.status !== 200) {
      setMsg(pathMessage(r.body.error), "bad");
      return null;
    }
    state.settings = r.body;
    return r.body;
  }

  async function addFolder(event) {
    event.preventDefault();
    const input = $("pathInput");
    const raw = input.value;
    if (!raw.trim()) return;
    setMsg("", "");
    $("addBtn").disabled = true;
    try {
      const check = await api("POST", "/api/settings/check-path", { path: raw });
      if (check.status !== 200) {
        setMsg(pathMessage(check.body.error), "bad");
        return;
      }
      if (check.body.code !== "ok") {
        setMsg(pathMessage(check.body.code), "bad");
        return;
      }
      const warnings = (check.body.warnings || []).map((w) => WARNINGS[w]).filter(Boolean);
      const saved = await saveFolders(state.folders.concat([raw]));
      if (!saved) return;
      input.value = "";
      resetResult();
      render();
      if (warnings.length) setMsg(warnings.join(" "), "warn");
    } finally {
      $("addBtn").disabled = false;
    }
  }

  async function removeFolder(index) {
    setMsg("", "");
    const next = state.folders.filter((_, i) => i !== index);
    const saved = await saveFolders(next);
    if (saved) {
      resetResult();
      render();
    }
  }

  async function adoptEnv() {
    const r = await api("POST", "/api/settings/adopt-env", {});
    if (r.status !== 200) {
      setMsg(pathMessage(r.body.error), "bad");
      return;
    }
    state.settings = r.body;
    setMsg("Copied. Remove the FITS_ROOTS variable and restart ACP to use the settings file.", "warn");
    render();
  }

  // --- Scan --------------------------------------------------------------

  function resetResult() {
    show($("doneBox"), false);
    show($("failBox"), false);
    clearTimeout(state.redirectTimer);
  }

  function formatElapsed(seconds) {
    if (seconds == null) return "";
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return m > 0 ? `${m} min ${s} s` : `${s} s`;
  }

  async function startScan() {
    resetResult();
    setMsg("", "");
    $("scanBtn").disabled = true;
    const r = await api("POST", "/api/scan/start", {});
    if (r.status === 202 || r.status === 409) {
      watchScan();
      return;
    }
    $("scanBtn").disabled = state.folders.length === 0;
    failScan(r.body.error === "no_archive_roots" ? "no_archive_roots" : (r.body.error || "crashed"));
  }

  function watchScan() {
    state.scanWatched = true;
    show($("progressBox"), true);
    $("scanBtn").disabled = true;
    clearTimeout(state.pollTimer);
    pollScan();
  }

  async function pollScan() {
    const r = await api("GET", "/api/scan/status");
    const status = r.body || {};
    if (r.status === 200 && status.running) {
      const p = status.progress;
      $("progressStage").textContent = p ? (STAGES[p.stage] || "Working") : "Starting";
      const bits = [];
      if (p) bits.push(`${p.files_seen.toLocaleString()} files so far`);
      const elapsed = formatElapsed(status.elapsed_seconds);
      if (elapsed) bits.push(`${elapsed} elapsed`);
      $("progressDetail").textContent = bits.join(", ");
      state.pollTimer = setTimeout(pollScan, 1000);
      return;
    }
    state.scanWatched = false;
    show($("progressBox"), false);
    $("scanBtn").disabled = state.folders.length === 0;
    if (r.status !== 200) {
      failScan("crashed");
      return;
    }
    if (status.last_exit_code === 0) {
      finishScan();
    } else {
      failScan(status.last_error_kind || "crashed");
    }
  }

  async function finishScan() {
    const fresh = await loadSettings();
    const n = fresh ? fresh.targets || 0 : 0;
    $("doneText").textContent = n === 1
      ? "The scan finished. ACP found 1 target."
      : `The scan finished. ACP found ${n.toLocaleString()} targets.`;
    show($("doneBox"), true);
    let left = 3;
    const tick = () => {
      $("doneCountdown").textContent = `Opening the map in ${left} s`;
      if (left <= 0) {
        window.location.href = "/";
        return;
      }
      left -= 1;
      state.redirectTimer = setTimeout(tick, 1000);
    };
    tick();
  }

  function failScan(kind) {
    $("failText").textContent = SCAN_ERRORS[kind] || PATH_ERRORS[kind] || SCAN_ERRORS.crashed;
    show($("failBox"), true);
  }

  function changeFolders() {
    resetResult();
    $("foldersCard").scrollIntoView({ behavior: "smooth", block: "start" });
    $("pathInput").focus();
  }

  // --- Catalogues --------------------------------------------------------

  function renderCatalogues(status) {
    const el = $("catalogStatus");
    $("catalogBtn").disabled = !!status.running;
    if (status.running) {
      el.textContent = "Downloading. This can take a few minutes.";
    } else if (status.error_kind) {
      el.textContent = CATALOGUE_ERRORS[status.error_kind] || CATALOGUE_ERRORS.crashed;
    } else if (status.finished || status.present) {
      el.textContent = "Downloaded.";
    } else {
      el.textContent = "Not downloaded yet.";
    }
  }

  async function pollCatalogues() {
    const r = await api("GET", "/api/catalogues/status");
    if (r.status !== 200) return;
    renderCatalogues(r.body);
    clearTimeout(state.catalogueTimer);
    if (r.body.running) state.catalogueTimer = setTimeout(pollCatalogues, 2000);
  }

  async function fetchCatalogues() {
    $("catalogBtn").disabled = true;
    const r = await api("POST", "/api/catalogues/fetch", {});
    if (r.status !== 202 && r.status !== 409) {
      $("catalogStatus").textContent = CATALOGUE_ERRORS.could_not_start;
      $("catalogBtn").disabled = false;
      return;
    }
    pollCatalogues();
  }

  // --- Start -------------------------------------------------------------

  async function init() {
    $("addForm").addEventListener("submit", addFolder);
    $("scanBtn").addEventListener("click", startScan);
    $("adoptBtn").addEventListener("click", adoptEnv);
    $("changeFoldersBtn").addEventListener("click", changeFolders);
    $("catalogBtn").addEventListener("click", fetchCatalogues);

    const s = await loadSettings();
    if (!s) return;
    state.mode = s.first_run ? "setup" : "settings";
    if (s.error === "settings_file_corrupt") {
      setMsg("ACP could not read its settings file, so it is using defaults. The old file was kept as settings.json.bak. Saving here replaces it.", "warn");
    }
    render();
    pollCatalogues();
    const r = await api("GET", "/api/scan/status");
    if (r.status === 200 && r.body.running) watchScan();
  }

  init();
})();
