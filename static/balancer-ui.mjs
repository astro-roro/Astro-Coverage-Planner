// The Balancer dialog on the Activity tab (docs/specs/imaging-balancer.md,
// section 1, UI): settings, a preview of what a run would change, Run now,
// and the one-off "tag from target class" button.

import { esc } from "./activity.mjs";

const MODES = [
  ["dry_run", "Dry run (report only, writes no priorities)"],
  ["live", "Live (writes priorities)"],
  ["off", "Off"],
];

async function call(url, method = "GET", body) {
  const init = { method };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const r = await fetch(url, init);
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, body: j };
}

function numberField(id, label, value, step = "1") {
  return `<label class="bal-field"><span>${esc(label)}</span>` +
    `<input type="number" id="${id}" step="${step}" value="${esc(value)}"></label>`;
}

function formHtml(s) {
  const modes = MODES.map(([v, l]) =>
    `<option value="${v}"${s.mode === v ? " selected" : ""}>${esc(l)}</option>`).join("");
  return `<label class="bal-field"><span>Mode</span><select id="bal_mode">${modes}</select></label>` +
    `<label class="bal-field"><span>Run at (local time)</span>` +
    `<input type="time" id="bal_run_at" value="${esc(s.run_at)}"></label>` +
    numberField("bal_share_science", "Science share (%)", s.shares.science) +
    numberField("bal_share_survey", "Survey share (%)", s.shares.survey) +
    numberField("bal_share_pretty", "Pretty share (%)", s.shares.pretty) +
    numberField("bal_tolerance", "Tolerance (points)", s.tolerance) +
    numberField("bal_min_hours", "Judge shares after (hours in month)", s.min_month_hours, "0.5") +
    numberField("bal_finish_by", "Finish votes by (share of month)", s.finish_by, "0.05");
}

function readForm(dlg) {
  const v = id => dlg.querySelector(id).value;
  return {
    mode: v("#bal_mode"),
    run_at: v("#bal_run_at"),
    shares: {
      science: Number(v("#bal_share_science")),
      survey: Number(v("#bal_share_survey")),
      pretty: Number(v("#bal_share_pretty")),
    },
    tolerance: Number(v("#bal_tolerance")),
    min_month_hours: Number(v("#bal_min_hours")),
    finish_by: Number(v("#bal_finish_by")),
  };
}

export async function openBalancerDialog() {
  const first = await call("/api/balancer");
  if (!first.ok) { alert("The balancer is not available on this server."); return; }
  const dlg = document.createElement("dialog");
  dlg.className = "balancer-dialog";
  dlg.innerHTML =
    `<h3>Balancer</h3>` +
    `<div class="bal-notes">${first.body.notes.map(n => `<p class="panel-error">${esc(n)}</p>`).join("")}</div>` +
    `<form id="balForm" onsubmit="return false">${formHtml(first.body.settings)}</form>` +
    `<div class="bal-actions">` +
    `<button type="button" id="balSave">Save settings</button> ` +
    `<button type="button" id="balPreview">Preview</button> ` +
    `<button type="button" id="balRun">Run now</button> ` +
    `<button type="button" id="balTag">Tag projects from target class</button> ` +
    `<button type="button" id="balClose">Close</button></div>` +
    `<p id="balStatus" style="font-size:12px"></p>` +
    `<pre id="balResult" style="white-space:pre-wrap;font-size:12px"></pre>`;
  document.body.appendChild(dlg);
  const status = dlg.querySelector("#balStatus");
  const result = dlg.querySelector("#balResult");
  const show = (res, label) => {
    if (res.body && res.body.title) {
      result.textContent = `${res.body.title}\n\n${res.body.detail || ""}`;
      status.textContent = label;
    } else {
      result.textContent = "";
      status.textContent = (res.body && (res.body.message || res.body.error)) || `Failed (${res.status}).`;
    }
  };
  dlg.querySelector("#balSave").addEventListener("click", async () => {
    const res = await call("/api/balancer/settings", "PUT", readForm(dlg));
    status.textContent = res.ok ? "Settings saved." : (res.body.error || `Failed (${res.status}).`);
  });
  dlg.querySelector("#balPreview").addEventListener("click", async () => {
    status.textContent = "Working out what a run would change…";
    show(await call("/api/balancer/preview", "POST"), "Preview only. Nothing was written.");
  });
  dlg.querySelector("#balRun").addEventListener("click", async () => {
    const live = dlg.querySelector("#bal_mode").value === "live";
    if (live && !confirm("Run the balancer now? It will change project priorities.")) return;
    status.textContent = "Running…";
    show(await call("/api/balancer/run", "POST"), live ? "Run finished." : "Run finished (dry run, no priorities written).");
  });
  dlg.querySelector("#balTag").addEventListener("click", async () => {
    const res = await call("/api/balancer/tag-from-class", "POST");
    const tagged = (res.body && res.body.tagged) || [];
    status.textContent = res.ok
      ? (tagged.length ? `Tagged ${tagged.length}: ${tagged.join(", ")}.` : "Every project already has a role, or has no survey or pretty class.")
      : (res.body.error || `Failed (${res.status}).`);
  });
  dlg.querySelector("#balClose").addEventListener("click", () => { dlg.close(); dlg.remove(); });
  dlg.addEventListener("cancel", () => dlg.remove());
  dlg.showModal();
}
