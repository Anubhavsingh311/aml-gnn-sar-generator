"use strict";
const API_URL = (new URLSearchParams(location.search).get("api") || "__API_URL__").replace(/\/$/, "");
const BASE = API_URL.startsWith("__") ? "https://anti-money-laundering-scorer-backend.onrender.com" : API_URL;
const MAX_ROWS = 150000, TIMEOUT_MS = 120000;
const DEMO = new URLSearchParams(location.search).has("demo");
const $ = (id) => document.getElementById(id);
const h = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
const SVG = { flag: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M6 1 11 11H1z" fill="currentColor"/></svg>',
  hi: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M6 1.8 10.2 10.2H1.8z" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
  no: '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4" fill="none" stroke="currentColor"/></svg>' };

/* theme */
const root = document.documentElement;
let saved = null; try { saved = localStorage.getItem("theme"); } catch {}
root.dataset.theme = saved || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
$("theme").onclick = () => { const t = root.dataset.theme === "dark" ? "light" : "dark"; root.dataset.theme = t; try { localStorage.setItem("theme", t); } catch {} };
try { if (localStorage.getItem("seen")) $("help").open = false; else $("help").open = true; } catch { $("help").open = true; }
$("help").addEventListener("toggle", () => { try { localStorage.setItem("seen", "1"); } catch {} });

/* server status: poll /health with backoff */
let health = null, healthP = null;
function setStatus(s) {
  $("status").textContent = s === "ready" ? "Ready: " + health.model : s === "off" ? "Offline" : "Waking";
  $("retry").hidden = s !== "off"; $("wake").hidden = s === "ready";
  if (s !== "wake") $("elapsed").textContent = "";
}
function startHealth() {
  setStatus("wake");
  const t0 = Date.now(); let delay = 2000;
  const tick = setInterval(() => { $("elapsed").textContent = Math.round((Date.now() - t0) / 1000) + " s"; }, 500);
  healthP = (async () => {
    while (Date.now() - t0 < 90000) {
      try { const r = await fetch(BASE + "/health", { signal: AbortSignal.timeout(15000) });
        if (r.ok) { health = await r.json(); clearInterval(tick); setStatus("ready"); return health; } } catch {}
      await new Promise((r) => setTimeout(r, delay)); delay = Math.min(delay * 2, 16000);
    }
    clearInterval(tick); setStatus("off"); throw new Error("offline");
  })();
  healthP.catch(() => {});
}
$("retry").onclick = startHealth;

/* validation (streamed, in chunks) */
let FILE = null, VALID = false;
const TS = /^(\d{4})\/(\d{2})\/(\d{2}) (\d{2}):(\d{2})/, HEX = /^[0-9A-Fa-f]{1,9}$/;
async function validate(file) {
  const v = { rows: 0, cols: 0, short: 0, badAmt: 0, badLines: [], badTs: 0, badHex: 0, min: Infinity, max: -Infinity, prev: [], header: true };
  const rd = file.stream().getReader(), dec = new TextDecoder(); let buf = "", line = 0;
  const eat = (l) => {
    l = l.replace(/\r$/, ""); if (!l.trim()) return; line++;
    const c = l.split(",");
    if (line === 1) { v.cols = c.length; v.prev.push(c); if (TS.test(c[0].trim())) v.header = false; return; }
    v.rows++; if (v.prev.length < 6) v.prev.push(c);
    if (c.length < 10) { v.short++; return; }
    let bad = false;
    for (const i of [5, 7]) { const x = (c[i] || "").trim(); if (!x || !isFinite(Number(x))) bad = true; }
    if (bad) { v.badAmt++; if (v.badLines.length < 5) v.badLines.push(line); }
    const m = TS.exec(c[0].trim());
    if (!m) v.badTs++; else { const t = Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5]); if (t < v.min) v.min = t; if (t > v.max) v.max = t; }
    if (!HEX.test(c[2].trim()) || !HEX.test(c[4].trim())) v.badHex++;
  };
  for (;;) { const { done, value } = await rd.read(); if (done) break;
    buf += dec.decode(value, { stream: true }); const p = buf.split("\n"); buf = p.pop(); p.forEach(eat); }
  eat(buf + dec.decode());
  return v;
}
function renderValidation(file, v) {
  const span = v.max > v.min ? (v.max - v.min) / 864e5 : 0;
  const chk = [
    [v.cols >= 10 && !v.short, `At least 10 columns (found ${v.cols})`, "fail"],
    [v.header, "Header row present", "fail"],
    [v.rows > 0 && v.rows <= MAX_ROWS, `Row count ${v.rows.toLocaleString()} (max ${MAX_ROWS.toLocaleString()})`, "fail"],
    [!v.badAmt, v.badAmt ? `${v.badAmt} rows with blank or non-numeric amounts (file lines ${v.badLines.join(", ")}${v.badAmt > 5 ? ", ..." : ""}). Fix or remove them: the server would score them silently wrong.` : "Amounts are numeric", "fail"],
    [!v.badTs, v.badTs ? `${v.badTs} timestamps not in YYYY/MM/DD HH:MM` : "Timestamps parse", "fail"],
    [!v.badHex, v.badHex ? `${v.badHex} rows with non-hex account ids` : "Account ids are hex", "fail"],
    [span >= 1, span >= 1 ? `Time span ${span.toFixed(1)} days` : "Under 24 hours of history: scores will be unreliable", "warn"],
  ];
  VALID = chk.every(([ok, , lvl]) => ok || lvl === "warn");
  const box = $("vcard"); box.replaceChildren();
  box.append(h("b", "", `${file.name} (${(file.size / 1e6).toFixed(2)} MB, ${v.rows.toLocaleString()} rows, ${v.cols} columns)`));
  const ul = h("ul");
  chk.forEach(([ok, t, lvl]) => { const li = h("li"); li.append(h("span", "pf " + (ok ? "pass" : lvl), ok ? "Pass" : lvl === "fail" ? "Fail" : "Warn"), document.createTextNode(t)); ul.append(li); });
  box.append(ul);
  const pv = h("div", "prev"), tb = h("table");
  v.prev.forEach((r) => { const tr = h("tr"); r.slice(0, 10).forEach((x) => tr.append(h("td", "", x))); tb.append(tr); });
  pv.append(tb); box.append(pv); box.hidden = false;
  $("score").disabled = !VALID;
}
async function pick(file) {
  if (!file) return;
  FILE = file; $("err").hidden = true; $("score").disabled = true;
  $("vcard").hidden = false; $("vcard").textContent = "Checking file...";
  try { renderValidation(file, await validate(file)); } catch (e) { $("vcard").textContent = "Could not read this file."; }
}
const drop = $("drop");
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
drop.addEventListener("click", (e) => { if (e.target !== $("file")) { e.preventDefault(); $("file").click(); } });
$("file").onchange = (e) => pick(e.target.files[0]);
["dragover", "dragenter"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((n) => drop.addEventListener(n, () => drop.classList.remove("over")));
drop.addEventListener("drop", (e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f && /\.csv$/i.test(f.name)) pick(f); });

/* run */
let ctrl = null, secT = null;
function showErr(msg, detail) {
  const e = $("err"); e.replaceChildren(h("b", "", "Error: "), document.createTextNode(msg));
  if (detail) { const d = h("details"); d.append(h("summary", "", "Technical details"), document.createTextNode(detail)); e.append(d); }
  e.hidden = false;
}
function busy(on) {
  $("prog").hidden = !on; $("cancel").hidden = !on; $("score").disabled = on || !VALID;
  clearInterval(secT);
  if (on) { const t0 = Date.now(); secT = setInterval(() => { $("sec").textContent = Math.round((Date.now() - t0) / 1000) + " s"; }, 500); }
}
async function run() {
  $("err").hidden = true; ctrl = new AbortController(); let timedOut = false;
  const to = setTimeout(() => { timedOut = true; ctrl.abort(); }, TIMEOUT_MS);
  busy(true); $("stage").textContent = "Waiting for the server...";
  try {
    try { await healthP; } catch { showErr("The server is offline or still starting. Press Retry in the header, then score again."); return; }
    $("stage").textContent = "Uploading and scoring. Large files can take 10 to 60 s.";
    const fd = new FormData(); fd.append("file", FILE);
    const r = await fetch(`${BASE}/score_csv?only_flagged=false&limit=${$("limit").value}`, { method: "POST", body: fd, signal: ctrl.signal });
    if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch {} throw { status: r.status, detail: String(d || "") }; }
    const data = await r.json(); show(data, health);
  } catch (e) {
    if (e && e.status === 413) showErr(`This file has too many rows. The limit is ${MAX_ROWS.toLocaleString()} rows.`, e.detail);
    else if (e && e.status === 422) showErr("The server could not read this file. Check the column order, timestamps, account ids and amounts.", e.detail);
    else if (e && [502, 503, 504].includes(e.status)) { showErr("The server is waking up. Wait a minute and score again."); startHealth(); }
    else if (timedOut) showErr("Scoring took longer than 120 seconds and was stopped. Try a smaller file.");
    else if (e && e.name === "AbortError") showErr("Cancelled.");
    else if (e && e.name !== undefined) { showErr("Could not reach the server. It may be waking up: wait a minute and score again."); startHealth(); }
  } finally { clearTimeout(to); busy(false); }
}
$("score").onclick = run; $("cancel").onclick = () => ctrl && ctrl.abort();

/* results */
let S = { rows: [], tab: "flag", key: "risk_score", dir: -1, page: 0, q: "", th: null };
const COLS = [["Rank", null], ["Line", "line"], ["Time", "timestamp"], ["From", null], ["To", null], ["Amount", "amt"], ["Format", null], ["Risk", "risk_score"], ["Tier", null]];
function show(d, hl) {
  S.rows = d.results.map((r) => ({ ...r, line: r.row + 2, amt: Number(r.amt_paid) }));
  S.th = { f: hl.thr_f1, r: hl.thr_recall80 }; S.sum = d; S.tab = d.n_flagged ? "flag" : "hi"; S.page = 0;
  const pct = (n) => (100 * n / d.n_rows).toFixed(2) + "%";
  const k = [["Transactions scored", d.n_rows.toLocaleString()], ["Time span", (d.span_hours / 24).toFixed(1) + " days"],
    ["Flagged", `${d.n_flagged.toLocaleString()} (${pct(d.n_flagged)})`], ["High-recall alerts", `${d.n_flagged_high_recall.toLocaleString()} (${pct(d.n_flagged_high_recall)})`], ["Rows returned", d.returned.toLocaleString()]];
  $("kpis").replaceChildren(...k.map(([l, v]) => { const c = h("div", "kpi"); c.append(h("b", "", v), h("span", "", l)); return c; }));
  const b = $("banner"); b.hidden = d.returned >= d.n_flagged; if (!b.hidden) b.textContent = `Showing top ${d.returned.toLocaleString()} of ${d.n_flagged.toLocaleString()} flagged. Raise "Rows to return" to see more.`;
  $("note").textContent = d.note || "";
  $("empty").hidden = true; $("results").hidden = false; renderTabs(); render();
}
function renderTabs() {
  const t = $("tabs"); t.replaceChildren();
  [["flag", "Flagged"], ["hi", "High-recall"], ["all", "All returned"]].forEach(([k, l]) => {
    const b = h("button", "", l); b.setAttribute("role", "tab"); b.setAttribute("aria-selected", S.tab === k);
    b.onclick = () => { S.tab = k; S.page = 0; renderTabs(); render(); }; t.append(b);
  });
}
function view() {
  const q = S.q.toLowerCase();
  let r = S.rows.filter((x) => (S.tab === "flag" ? x.flag : S.tab === "hi" ? x.flag_high_recall : true) &&
    (!q || `${x.from_bank} ${x.from_acct} ${x.to_bank} ${x.to_acct}`.toLowerCase().includes(q)));
  return r.sort((a, b) => (a[S.key] > b[S.key] ? 1 : a[S.key] < b[S.key] ? -1 : 0) * S.dir);
}
function pill(r) {
  const [c, t] = r.flag ? ["flag", "Flagged"] : r.flag_high_recall ? ["hi", "High-recall"] : ["no", "Below threshold"];
  const p = h("span", "pill " + c); p.innerHTML = SVG[c]; p.append(document.createTextNode(t)); return p;  // static icon markup only
}
function render() {
  const head = $("tbl").tHead; head.replaceChildren(); const tr = h("tr");
  COLS.forEach(([l, k]) => { const th = h("th"); th.scope = "col";
    if (k) { const b = h("button", "", l + (S.key === k ? (S.dir < 0 ? " \u2193" : " \u2191") : "")); b.onclick = () => { S.dir = S.key === k ? -S.dir : -1; S.key = k; render(); }; th.append(b); th.setAttribute("aria-sort", S.key === k ? (S.dir < 0 ? "descending" : "ascending") : "none"); }
    else th.append(h("button", "", l)); tr.append(th); });
  head.append(tr);
  const rows = view(), per = +$("per").value, pages = Math.max(1, Math.ceil(rows.length / per));
  S.page = Math.min(S.page, pages - 1);
  const body = $("tbl").tBodies[0]; body.replaceChildren();
  rows.slice(S.page * per, (S.page + 1) * per).forEach((r, i) => {
    const t = h("tr"); const add = (x, c) => t.append(h("td", c || "", x));
    add(S.page * per + i + 1, "m"); add(r.line, "m"); add(r.timestamp, "m"); add(`${r.from_bank}_${r.from_acct}`, "m"); add(`${r.to_bank}_${r.to_acct}`, "m");
    add(`${r.amt.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${r.cur_paid}`, "m"); add(r.fmt);
    const rk = h("td"), w = h("div", "risk"), bar = h("div", "rb"), f = h("i"); f.style.width = (r.risk_score * 100).toFixed(1) + "%"; bar.append(f);
    [S.th.r, S.th.f].forEach((x) => { const s = h("s"); s.style.left = (x * 100) + "%"; bar.append(s); });
    w.append(h("span", "mono", r.risk_score.toFixed(3)), bar); rk.append(w); t.append(rk);
    const tc = h("td"); tc.append(pill(r)); t.append(tc); body.append(t);
  });
  const n = $("none"); n.hidden = rows.length > 0;
  if (!rows.length) n.textContent = S.tab === "flag" && !S.sum.n_flagged ? "No transactions crossed the Flagged threshold. This can happen. Open the High-recall tab to see the wider net." : "No rows match this view.";
  $("pinfo").textContent = `Page ${S.page + 1} of ${pages} (${rows.length.toLocaleString()} rows)`;
  $("prev").disabled = S.page === 0; $("next").disabled = S.page >= pages - 1;
}
$("prev").onclick = () => { S.page--; render(); }; $("next").onclick = () => { S.page++; render(); };
$("per").onchange = () => { S.page = 0; render(); }; $("q").oninput = (e) => { S.q = e.target.value; S.page = 0; render(); };

/* CSV export: neutralise spreadsheet formulas */
const cell = (v) => { let s = String(v); if (/^[=+\-@]/.test(s)) s = "'" + s; return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
$("export").onclick = () => {
  const head = ["rank", "line", "timestamp", "from_bank", "from_acct", "to_bank", "to_acct", "amt_paid", "cur_paid", "fmt", "risk_score", "stage1_score", "flag", "flag_high_recall"];
  const rows = view().map((r, i) => [i + 1, r.line, r.timestamp, r.from_bank, r.from_acct, r.to_bank, r.to_acct, r.amt_paid, r.cur_paid, r.fmt, r.risk_score, r.stage1_score, r.flag, r.flag_high_recall]);
  const a = h("a"); a.href = URL.createObjectURL(new Blob([[head, ...rows].map((r) => r.map(cell).join(",")).join("\n")], { type: "text/csv" }));
  a.download = "aml_scores.csv"; a.click(); URL.revokeObjectURL(a.href);
};

/* start */
if (DEMO) {
  $("demoBanner").hidden = false; $("wake").hidden = true; $("status").textContent = "Demo";
  const fm = ["ACH", "Wire", "Cheque", "Credit Card", "Cash", "Bitcoin", "Reinvestment"], cu = ["Euro", "US Dollar", "Yen", "Saudi Riyal"];
  const res = Array.from({ length: 120 }, (_, i) => { const risk = 0.99 * Math.pow(0.95, i);
    return { row: i * 17, stage1_score: risk * 0.6, risk_score: risk, flag: risk >= 0.776, flag_high_recall: risk >= 0.031,
      timestamp: `2022/09/0${1 + i % 5} ${String(8 + i % 12).padStart(2, "0")}:${String((i * 7) % 60).padStart(2, "0")}`,
      from_bank: String(1 + i % 30), from_acct: (0xF8829A450 + i * 977).toString(16).toUpperCase().slice(-9), to_bank: String(2 + i % 40),
      to_acct: (0xD0DBC74D0 + i * 1319).toString(16).toUpperCase().slice(-9), amt_paid: (1000 + i * 337.13).toFixed(2), cur_paid: cu[i % 4], fmt: fm[i % 7] }; });
  show({ n_rows: 3000, n_flagged: 5, n_flagged_high_recall: 68, span_hours: 120, returned: 120, note: "Demo sample, generated in the browser.", results: res }, { thr_f1: 0.776, thr_recall80: 0.031 });
} else startHealth();
