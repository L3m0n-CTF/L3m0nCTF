/* The crew's activity page (/admin/l3mon/activity). It talks to the crew API only (/api/v1/l3mon/admin/...).
 *
 * The ledger holds text that PLAYERS control (a studio's name, a browser's identification), so nothing from it is ever put into the page as markup:
 * every cell is built with createElement and textContent. The CSV export opens in a spreadsheet, where a cell that starts with = + - or @ is a
 * formula; such a cell gets a leading apostrophe (csvCell). */
(function () {
  "use strict";

  const app = document.getElementById("activity-app");
  if (!app) return;
  const api = app.dataset.api;
  const el = (id) => document.getElementById(id);
  const statusEl = el("activity-status");
  const verifyEl = el("verify-status");
  const wrap = el("activity-wrap");
  const removalsStatus = el("removals-status");
  const removalsWrap = el("removals-wrap");
  let shown = [];

  function csrf() {
    if (window.init && window.init.csrfNonce) return window.init.csrfNonce;
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function cell(tag, value, cls) {
    const node = document.createElement(tag);
    node.textContent = value === null || value === undefined ? "" : String(value);
    if (cls) node.className = cls;
    return node;
  }

  function table(headers, rows, build) {
    const t = document.createElement("table");
    t.className = "table table-striped table-bordered";
    const head = document.createElement("thead");
    const hr = document.createElement("tr");
    headers.forEach((h) => hr.appendChild(cell("th", h)));
    head.appendChild(hr);
    t.appendChild(head);
    const body = document.createElement("tbody");
    rows.forEach((r) => body.appendChild(build(r)));
    t.appendChild(body);
    return t;
  }

  function params() {
    const q = new URLSearchParams();
    const team = el("filter-team").value.trim();
    const actor = el("filter-actor").value.trim();
    const kind = el("filter-kind").value.trim();
    if (team) q.set("team_id", team);
    if (actor) q.set("actor_id", actor);
    if (kind) q.set("kind", kind);
    q.set("limit", el("filter-limit").value);
    return q;
  }

  async function getJson(path) {
    const resp = await fetch(api + path, { headers: { Accept: "application/json" }, cache: "no-store" });
    if (!resp.ok) throw new Error("the server answered " + resp.status);
    const body = await resp.json();
    if (!body.success) throw new Error("the server said no");
    return body.data;
  }

  async function loadActivity() {
    statusEl.textContent = "Loading…";
    try {
      shown = await getJson("/activity?" + params().toString());
      wrap.textContent = "";
      if (!shown.length) {
        statusEl.textContent = "Nothing matches.";
        return;
      }
      wrap.appendChild(table(["Time (UTC)", "Kind", "Who", "Studio", "Target", "Detail", "Address", "Browser"], shown, (r) => {
        const tr = document.createElement("tr");
        tr.appendChild(cell("td", r.at));
        tr.appendChild(cell("td", r.kind));
        tr.appendChild(cell("td", r.actor_name + (r.actor_id ? " (#" + r.actor_id + ")" : "")));
        tr.appendChild(cell("td", r.team_id || ""));
        tr.appendChild(cell("td", r.target));
        tr.appendChild(cell("td", JSON.stringify(r.detail || {}), "activity-detail"));
        tr.appendChild(cell("td", r.ip));
        tr.appendChild(cell("td", r.user_agent, "activity-agent"));
        return tr;
      }));
      statusEl.textContent = shown.length + " line(s), newest first.";
    } catch (err) {
      statusEl.textContent = "Could not read the log: " + err.message;
      wrap.textContent = "";
    }
  }

  async function verify() {
    verifyEl.textContent = "Checking…";
    try {
      const r = await getJson("/activity/verify");
      const database = r.triggers_installed ? "The database refuses changes (triggers in place)." : "WARNING: the database does NOT refuse changes yet (run tools/ops/activity-triggers.sh apply).";
      verifyEl.textContent = (r.ok ? "Chain OK: " + r.checked + " lines checked, none changed, none missing. " : "CHAIN BROKEN at line #" + r.first_bad_id + " (" + r.reason + "). Do not repair anything: tell the owner. ") + database;
      verifyEl.className = r.ok && r.triggers_installed ? "text-success" : "text-danger";
    } catch (err) {
      verifyEl.textContent = "Could not check: " + err.message;
      verifyEl.className = "text-danger";
    }
  }

  // a cell that a spreadsheet would run as a formula (= + - @ at the start) gets a leading apostrophe
  function csvCell(value) {
    let s = value === null || value === undefined ? "" : String(value);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return '"' + s.replace(/"/g, '""') + '"';
  }

  function exportCsv() {
    if (!shown.length) {
      statusEl.textContent = "Show some lines first.";
      return;
    }
    const cols = ["id", "at", "kind", "actor_id", "actor_name", "team_id", "target", "detail", "ip", "user_agent", "request_id"];
    const lines = [cols.map(csvCell).join(",")];
    shown.forEach((r) => lines.push(cols.map((c) => csvCell(c === "detail" ? JSON.stringify(r.detail || {}) : r[c])).join(",")));
    const blob = new Blob([lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "activity-" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + ".csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
  }

  async function loadRemovals() {
    removalsStatus.textContent = "Loading…";
    try {
      const rows = await getJson("/removals?active=1");
      removalsWrap.textContent = "";
      if (!rows.length) {
        removalsStatus.textContent = "Nobody is removed from a studio right now.";
        return;
      }
      removalsWrap.appendChild(table(["#", "Person", "Studio", "Removed by", "When (UTC)", "Note", ""], rows, (r) => {
        const tr = document.createElement("tr");
        tr.appendChild(cell("td", r.id));
        tr.appendChild(cell("td", (r.user_name || "?") + " (#" + r.user_id + ")"));
        tr.appendChild(cell("td", (r.team_name || "?") + " (#" + r.team_id + ")"));
        tr.appendChild(cell("td", r.removed_by ? "#" + r.removed_by : "the crew"));
        tr.appendChild(cell("td", r.removed_at));
        tr.appendChild(cell("td", r.recent_activity ? "recent solve or hint" : ""));
        const action = document.createElement("td");
        const button = cell("button", "Let them back", "btn btn-sm btn-outline-danger");
        button.type = "button";
        button.addEventListener("click", () => lift(r, button));
        action.appendChild(button);
        tr.appendChild(action);
        return tr;
      }));
      removalsStatus.textContent = rows.length + " removal(s) in force.";
    } catch (err) {
      removalsStatus.textContent = "Could not read the removals: " + err.message;
      removalsWrap.textContent = "";
    }
  }

  async function lift(removal, button) {
    if (!confirm("Let " + (removal.user_name || "this person") + " join " + (removal.team_name || "this studio") + " again?")) return;
    button.disabled = true;
    try {
      const resp = await fetch(api + "/removals/" + removal.id + "/lift", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "CSRF-Token": csrf() },
        body: "{}",
      });
      if (!resp.ok) throw new Error("the server answered " + resp.status);
      await loadRemovals();
      await loadActivity();
    } catch (err) {
      button.disabled = false;
      removalsStatus.textContent = "Could not lift it: " + err.message;
    }
  }

  el("btn-filter").addEventListener("click", loadActivity);
  el("btn-verify").addEventListener("click", verify);
  el("btn-export").addEventListener("click", exportCsv);
  ["filter-team", "filter-actor", "filter-kind"].forEach((id) => el(id).addEventListener("keydown", (e) => { if (e.key === "Enter") loadActivity(); }));
  loadActivity();
  loadRemovals();
})();
