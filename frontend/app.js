"use strict";

let job = null;

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const money = (n) =>
  n === null || n === undefined || n === "" ? "—" : "₹" + Number(n).toLocaleString("en-IN");

const CUSTOMER_FIELDS = [
  ["customer_name", "Customer Name"],
  ["pan", "PAN"],
  ["cibil_score", "CIBIL Score"],
  ["dob", "Date of Birth"],
  ["aadhaar", "Aadhaar / UID"],
  ["phone", "Phone Number(s)"],
  ["email", "Email ID(s)"],
  ["report_date", "Report Date"],
];
const ACCOUNT_COLS = [
  ["loan_type", "Loan Type", "text"],
  ["financier", "Financier Name", "text"],
  ["sanction", "Sanction Amount", "num"],
  ["outstanding", "Current Outstanding", "num"],
  ["overdue", "Overdue Amount", "num"],
  ["pd", "PD", "text"],
  ["ad12m", "AD 12 M", "text"],
  ["dpd", "DPD Last 12 Months", "text"],
  ["ownership", "Ownership", "own"],
  ["date_opened", "Date Opened", "text"],
  ["status", "NPA/ SMA/ Suit Filed/ Write off", "text"],
];

// --------------------------------------------------------------------------- //
// API
// --------------------------------------------------------------------------- //
async function api(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

// --------------------------------------------------------------------------- //
// Upload
// --------------------------------------------------------------------------- //
const EXTRACT_STAGES = [
  "Uploading CIBIL report",
  "Reading & recognising the report",
  "Digitising every page",
  "Extracting accounts, enquiries & summary with AI",
  "Applying credit rules & reconciling totals",
  "Preparing maker-checker review",
];

// Rotating micro-updates shown on the final step so a long extraction feels alive.
const WAIT_MESSAGES = [
  "Reading account details\u2026",
  "Checking sanctioned vs outstanding\u2026",
  "Tallying overdue amounts\u2026",
  "Cross-checking the credit summary\u2026",
  "Calculating ownership totals\u2026",
  "Reconciling loan-type summaries\u2026",
  "Validating enquiry history\u2026",
  "Finalising the numbers\u2026",
];

function fmtDur(ms) {
  ms = Math.max(0, Math.round(ms));
  if (ms < 1000) return ms + " ms";
  return (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + " s";
}

// Maps the backend's real phase timings onto the visible steps.
function buildStageTimes(p, clientTotalMs) {
  p = p || {};
  const digit = p.di_ran ? (p.di_ms ?? 0) : (p.read_ms ?? 0);
  const llm = p.llm_ms ?? 0;
  const rules = p.rules_ms ?? 0;
  const total = p.total_ms ?? 0;
  const upload = Math.max(0, clientTotalMs - total); // upload + network + render
  return {
    total: clientTotalMs,
    steps: [upload, null, digit, llm, rules, null],
    note: p.di_ran
      ? "Document Intelligence and AI extraction run in parallel, so the total is less than their sum."
      : (p.chunks > 1 ? "Account batches are extracted in parallel, so the total is less than their sum." : ""),
  };
}

function renderActivity(activeIdx, done, times, activeLabel) {
  const box = document.getElementById("extract-activity");
  if (!box) return;
  box.hidden = false;
  box.innerHTML = "";
  let title = done ? "\u2713 Extraction complete" : "Extracting report\u2026";
  if (done && times && times.total != null) title += "  \u00b7  " + fmtDur(times.total);
  box.append(el("div", "act-title", title));
  const list = el("div", "act-list");
  EXTRACT_STAGES.forEach((s, i) => {
    const state = done || i < activeIdx ? "done" : (i === activeIdx ? "active" : "todo");
    const row = el("div", "act-row " + state);
    row.append(el("span", "act-ico", state === "done" ? "\u2713" : ""));
    row.append(el("span", "act-label", (!done && state === "active" && activeLabel) ? activeLabel : s));
    if (done && times && times.steps && times.steps[i] != null)
      row.append(el("span", "act-time", fmtDur(times.steps[i])));
    list.append(row);
  });
  box.append(list);
  if (done && times && times.note) box.append(el("div", "act-foot", times.note));
}

function initUpload() {
  const dz = $("#dropzone");
  const input = $("#file");
  const text = $("#dz-text");
  const btn = $("#btn-extract");

  const setFile = (f) => {
    if (!f) return;
    input._file = f;
    text.innerHTML = `<span class="dz-file">${f.name}</span>`;
    const hint = document.getElementById("file-hint");
    if (hint) hint.textContent = "Selected file: " + f.name;
    btn.disabled = false;
  };

  input.addEventListener("change", () => setFile(input.files[0]));
  ["dragover", "dragenter"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

  btn.addEventListener("click", async () => {
    const f = input._file;
    if (!f) return;
    $("#upload-error").hidden = true;
    $("#dropzone").hidden = true;
    $("#file-hint").hidden = true;
    btn.hidden = true;
    let idx = 0;
    let wi = 0;
    const t0 = performance.now();
    renderActivity(idx, false);
    const timer = setInterval(() => {
      if (idx < EXTRACT_STAGES.length - 1) renderActivity(++idx, false);
      else renderActivity(idx, false, null, WAIT_MESSAGES[wi++ % WAIT_MESSAGES.length]);
    }, 1600);
    try {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("mode", "ai");
      fd.append("model", "");
      job = await api("/api/extract", { method: "POST", body: fd });
      clearInterval(timer);
      renderActivity(EXTRACT_STAGES.length, true, buildStageTimes(job.processing, performance.now() - t0));
      setTimeout(render, 1200);
    } catch (err) {
      clearInterval(timer);
      $("#extract-activity").hidden = true;
      $("#dropzone").hidden = false;
      $("#file-hint").hidden = false;
      btn.hidden = false; btn.disabled = false;
      const e = $("#upload-error"); e.hidden = false; e.textContent = err.message;
    }
  });
}

async function initCapabilities() {
  const note = $("#mode-ai-note");
  if (!note) return;
  try {
    const cap = await api("/api/capabilities");
    note.textContent = cap.ai_enabled
      ? "keyless · managed identity + RBAC"
      : "not configured (set DI_ENDPOINT / models.json)";
  } catch (e) {
    note.textContent = "unavailable";
  }
}

// --------------------------------------------------------------------------- //
// Rendering
// --------------------------------------------------------------------------- //
function setStepper(status) {
  const map = { maker_review: "maker", rejected: "maker",
                pending_check: "checker", approved: "done" };
  const order = ["upload", "maker", "checker", "done"];
  const active = map[status] || "upload";
  const activeIdx = order.indexOf(active);
  $("#stepper").hidden = false;
  document.querySelectorAll(".step").forEach((s) => {
    const idx = order.indexOf(s.dataset.step);
    s.classList.toggle("active", idx === activeIdx);
    s.classList.toggle("done", idx < activeIdx);
  });
}

function showView(name) {
  ["upload", "maker", "checker", "done"].forEach((v) =>
    ($(`#view-${v}`).hidden = v !== name));
}

function render() {
  if (!job) { showView("upload"); $("#stepper").hidden = true; return; }
  setStepper(job.status);
  if (job.status === "maker_review" || job.status === "rejected") renderMaker();
  else if (job.status === "pending_check") renderChecker();
  else if (job.status === "approved") renderDone();
}

function metricCards(container) {
  const m = job.data.metrics;
  container.innerHTML = "";
  const cards = [
    { num: m.accounts, lbl: "Accounts", cls: "" },
    { num: m.flagged_accounts, lbl: "Flagged for review", cls: m.flagged_accounts ? "bad" : "good" },
    { num: `${m.reconciliation_pass}/${m.reconciliation_total}`, lbl: "Reconciliation passed",
      cls: m.reconciliation_pass === m.reconciliation_total ? "good" : "bad" },
    { num: m.straight_through ? "Yes" : "No", lbl: "Straight-through",
      cls: m.straight_through ? "good" : "" },
  ];
  cards.forEach((c) => {
    const card = el("div", "metric " + c.cls);
    card.append(el("div", "num", String(c.num)), el("div", "lbl", c.lbl));
    container.append(card);
  });
}

function renderProcessing(container, p) {
  if (!container) return;
  if (!p) { container.innerHTML = ""; return; }
  container.innerHTML = "";
  const row = el("div", "proc-row");
  const engine = el("div", "proc-engine");
  engine.append(el("span", "proc-badge" + (p.mode === "ai" ? " ai" : ""),
    p.engine || "Engine"));
  if (job && job.id) engine.append(el("span", "trace-id", "trace: " + job.id));
  row.append(engine);

  const stats = [];
  if (p.mode === "ai") {
    if (p.report_type)
      stats.push(["Report type", p.report_type === "commercial" ? "Commercial CIR" : "Individual CIR"]);
    if (p.di_ran) stats.push([p.ocr_used ? "OCR (DI)" : "DI read", (p.di_ms ?? 0) + " ms"]);
    else stats.push(["Text read", (p.read_ms ?? 0) + " ms"]);
    stats.push(["LLM", (p.llm_ms ?? 0) + " ms"]);
    if (p.chunks) stats.push(["Chunks", p.chunks]);
  } else {
    stats.push(["Read time", (p.read_ms ?? 0) + " ms"]);
  }
  stats.push(["Rules", (p.rules_ms ?? 0) + " ms"]);
  stats.push(["Total time", (p.total_ms ?? 0) + " ms"]);
  stats.push(["Pages", p.pages]);
  const tk = p.token_kind === "real" ? "" : " (est.)";
  stats.push(["Input tokens" + tk, Number(p.input_tokens || 0).toLocaleString("en-IN")]);
  stats.push(["Output tokens" + tk, Number(p.output_tokens || 0).toLocaleString("en-IN")]);
  stats.forEach(([k, v]) => {
    const s = el("div", "proc-stat");
    s.append(el("div", "v", String(v ?? "—")), el("div", "k", k));
    row.append(s);
  });
  container.append(row);

  const note = el("div", "proc-note");
  if (p.mode === "ai") {
    note.innerHTML = `${p.engine_detail || ""} ` +
      `<strong>${Number(p.llm_tokens || 0).toLocaleString("en-IN")} real tokens</strong> ` +
      `billed on ${p.deployment || "the Foundry model"}.` +
      `<span class="obs">Observability — DI &amp; LLM latency, token usage and per-report cost are ` +
      `tracked against a trace id; in production these stream to Azure Monitor / Application Insights.</span>`;
  } else {
    note.innerHTML = `${p.engine_detail || ""} <strong>0 AI tokens billed</strong> — ` +
      `token figures are LLM-equivalent estimates for capacity planning.`;
  }
  container.append(note);
}

function renderReconciliation(container) {
  container.innerHTML = "";
  const recon = job.data.reconciliation;
  const issues = recon.filter((c) => !c.ok);
  if (issues.length) {
    const names = issues.map((c) => c.check).join(", ");
    container.append(el("div", "recon-lead",
      `${recon.length - issues.length} of ${recon.length} checks reconciled \u00b7 ` +
      `${issues.length} need${issues.length > 1 ? "" : "s"} attention: ${names}`));
  }
  recon.forEach((c) => {
    const state = c.unverified ? "warn" : (c.ok ? "ok" : "fail");
    const item = el("div", "recon-item " + state);
    item.append(el("span", "dot"));
    const body = el("div");
    body.append(el("div", "rc-label", c.check));
    const fmt = (v) => c.kind === "money" ? money(v) : (v === null || v === undefined ? "\u2014" : v);
    if (c.unverified) {
      body.append(el("div", "rc-vals",
        "the report\u2019s own total wasn\u2019t found, so this couldn\u2019t be cross-checked \u2014 verify against the source PDF"));
    } else if (c.expected === null || c.expected === undefined) {
      body.append(el("div", "rc-vals",
        `not stated in the report \u00b7 extracted ${fmt(c.actual)} (nothing to reconcile against)`));
    } else {
      body.append(el("div", "rc-vals",
        `report says ${fmt(c.expected)} \u00b7 extracted ${fmt(c.actual)}`));
      if (!c.ok) body.append(el("div", "rc-why", reconReason(c)));
    }
    item.append(body);
    container.append(item);
  });
}
// Plain-language "why did this check fail" line: the size + direction of the gap
// and the likely cause, so the checker knows exactly where to look.
function reconReason(c) {
  const isMoney = c.kind === "money";
  const delta = typeof c.delta === "number" ? c.delta
    : (typeof c.actual === "number" && typeof c.expected === "number"
        ? c.actual - c.expected : null);
  if (delta === null || delta === 0) return "";
  const mag = Math.abs(delta);
  const dir = delta < 0 ? "short by" : "over by";
  const amt = isMoney ? money(mag) : String(mag);
  const pct = isMoney && c.expected
    ? ` (${(mag / Math.abs(c.expected) * 100).toFixed(2)}%)` : "";
  let why;
  if (/account count/i.test(c.check))
    why = delta < 0 ? "fewer accounts extracted than the report lists \u2014 one may be missing"
                    : "more accounts extracted than the report lists \u2014 one may be duplicated";
  else if (/closed/i.test(c.check))
    why = "zero-balance account count differs from the report \u2014 check balances / statuses";
  else if (/enquir/i.test(c.check))
    why = "enquiry count differs from the report\u2019s stated total";
  else
    why = "sum of per-account values differs from the report\u2019s total \u2014 review individual amounts";
  return `${dir} ${amt}${pct} \u2014 ${why}`;
}

function renderMaker() {
  showView("maker");
  metricCards($("#maker-metrics"));
  renderProcessing($("#maker-proc"), job.processing);

  const banner = $("#maker-banner");
  if (job.status === "rejected" && job.reject_reason) {
    banner.hidden = false; banner.className = "banner reject";
    banner.innerHTML = `<strong>Returned by checker:</strong> ${job.reject_reason}`;
  } else banner.hidden = true;

  // Customer form
  const cf = $("#customer-form"); cf.innerHTML = "";
  const flags = job.data.customer_flags || [];
  const details = job.data.customer.details;
  CUSTOMER_FIELDS.forEach(([key, label]) => {
    const f = el("div", "field" + (flags.includes(key) ? " flagged" : ""));
    f.append(el("label", null, label));
    const inp = el("input");
    inp.value = details[key] ?? "";
    inp.dataset.cust = key;
    f.append(inp);
    cf.append(f);
  });

  renderReconciliation($("#reconciliation"));
  renderAccountsTable();
  renderEnquiries($("#enquiries"));
}

function renderAccountsTable() {
  const table = $("#accounts-table");
  table.innerHTML = "";
  const thead = el("thead");
  const htr = el("tr");
  ACCOUNT_COLS.forEach(([, label]) => htr.append(el("th", null, label)));
  thead.append(htr); table.append(thead);

  const tbody = el("tbody");
  job.data.accounts.forEach((a) => {
    const tr = el("tr");
    if (a.peak_dpd > 0) tr.className = "row-dpd";
    ACCOUNT_COLS.forEach(([key, , type]) => {
      const td = el("td");
      if ((a.flags || []).includes(key)) td.className = "flagged";
      let inp;
      if (type === "own") {
        inp = el("select");
        ["Individual", "Joint", "Guarantor"].forEach((o) => {
          const opt = el("option", null, o); opt.value = o;
          if (a[key] === o) opt.selected = true;
          inp.append(opt);
        });
      } else {
        inp = el("input");
        inp.value = a[key] ?? "";
        if (type === "num") { inp.type = "number"; inp.className = "num-input"; }
        if (key === "dpd") {
          inp.classList.add("dpd-input");
          inp.title = inp.value; // full 12-month history on hover (the cell clips the long string)
          if (a.peak_dpd > 0) inp.classList.add("dpd-red");
        }
      }
      inp.dataset.id = a._id;
      inp.dataset.field = key;
      td.append(inp);
      tr.append(td);
    });
    tbody.append(tr);
  });
  table.append(tbody);
}

function renderEnquiries(container) {
  const e = job.data.enquiries;
  const pw = job.data.purpose_wise;
  const sum = e.summary;
  container.innerHTML = "";

  const left = el("div");
  left.append(el("h3", null, "Enquiry Summary"));
  left.append(kvTable([
    ["Last 3 Months", sum.last_3m], ["Last 6 Months", sum.last_6m],
    ["Last 12 Months", sum.last_12m], ["Lifetime", sum.lifetime],
  ], ["Period", "No. of Enquiries"]));
  left.append(el("h3", "spacer", "Purpose-wise Summary"));
  const pwt = el("table");
  pwt.innerHTML = "<tr><th>Purpose</th><th>Count</th><th>Total Amount</th></tr>";
  pw.forEach((r) => {
    const tr = el("tr", r.total ? "total" : "");
    tr.innerHTML = `<td>${r.total ? "<b>Total</b>" : r.purpose}</td>` +
      `<td>${r.count}</td><td>${money(r.amount)}</td>`;
    pwt.append(tr);
  });
  left.append(pwt);

  const right = el("div");
  right.append(el("h3", null, `Enquiries (${e.detail.length})`));
  const dt = el("table");
  dt.innerHTML = "<tr><th>Member</th><th>Date</th><th>Purpose</th><th>Amount</th></tr>";
  e.detail.forEach((d) => {
    const tr = el("tr");
    tr.innerHTML = `<td>${d.member}</td><td>${d.date}</td>` +
      `<td>${d.purpose}</td><td>${money(d.amount)}</td>`;
    dt.append(tr);
  });
  right.append(dt);

  container.append(left, right);
}

function kvTable(rows, headers) {
  const t = el("table");
  t.innerHTML = `<tr><th>${headers[0]}</th><th>${headers[1]}</th></tr>`;
  rows.forEach(([k, v]) => t.append(el("tr", null, `<td>${k}</td><td>${v ?? "—"}</td>`)));
  return t;
}

function collectEdits() {
  const customer = {};
  document.querySelectorAll("[data-cust]").forEach((i) => {
    customer[i.dataset.cust] = i.value;
  });
  const map = {};
  document.querySelectorAll("#accounts-table [data-id]").forEach((i) => {
    const id = Number(i.dataset.id);
    (map[id] = map[id] || { id })[i.dataset.field] = i.value;
  });
  return { customer, accounts: Object.values(map) };
}

// --------------------------------------------------------------------------- //
// Checker + Done
// --------------------------------------------------------------------------- //
function renderChecker() {
  showView("checker");
  metricCards($("#checker-metrics"));
  renderProcessing($("#checker-proc"), job.processing);
  renderReconciliation($("#checker-reconciliation"));

  const d = job.data.customer.details;
  const grand = job.data.ownership_summary.find((r) => r.total) || {};
  const rows = [
    ["Customer", d.customer_name], ["PAN", d.pan], ["CIBIL Score", d.cibil_score],
    ["Accounts", job.data.metrics.accounts],
    ["Total Sanction", money(grand.sanction)],
    ["Total Outstanding", money(grand.outstanding)],
    ["Flagged fields", job.data.metrics.flagged_accounts],
  ];
  const box = $("#checker-summary");
  box.className = "grid-form"; box.innerHTML = "";
  rows.forEach(([k, v]) => {
    const f = el("div", "field");
    f.append(el("label", null, k), el("div", null, `<strong>${v ?? "—"}</strong>`));
    box.append(f);
  });
}

function renderDone() {
  showView("done");
  const tl = $("#history"); tl.innerHTML = "";
  job.history.forEach((h) => {
    const item = el("div", "tl-item");
    item.append(el("span", "tl-ts", h.ts));
    const body = el("div");
    body.append(el("span", "tl-act", h.act || h.action));
    if (h.note) body.append(el("span", null, " — " + h.note));
    item.append(body);
    tl.append(item);
  });
}

// --------------------------------------------------------------------------- //
// Actions
// --------------------------------------------------------------------------- //
async function withBusy(btn, label, fn) {
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = label;
  try { await fn(); }
  catch (err) { alert(err.message); }
  finally { btn.disabled = false; btn.textContent = old; }
}

function initActions() {
  $("#btn-save").addEventListener("click", (e) =>
    withBusy(e.target, "Saving…", async () => {
      job = await api(`/api/job/${job.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectEdits()),
      });
      render();
    }));

  $("#btn-submit").addEventListener("click", (e) =>
    withBusy(e.target, "Submitting…", async () => {
      job = await api(`/api/job/${job.id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectEdits()),
      });
      job = await api(`/api/job/${job.id}/submit`, { method: "POST" });
      render();
    }));

  $("#btn-approve").addEventListener("click", (e) =>
    withBusy(e.target, "Generating…", async () => {
      job = await api(`/api/job/${job.id}/approve`, { method: "POST" });
      render();
    }));
  $("#btn-approve-dl").addEventListener("click", (e) =>
    withBusy(e.target, "Approving\u2026", async () => {
      job = await api(`/api/job/${job.id}/approve`, { method: "POST" });
      if (job.status === "approved") window.location.href = `/api/job/${job.id}/excel`;
      render();
    }));
  $("#btn-reject").addEventListener("click", (e) => {
    const reason = $("#reject-reason").value.trim();
    if (!reason) { alert("Please enter a reason to reject."); return; }
    withBusy(e.target, "Rejecting…", async () => {
      job = await api(`/api/job/${job.id}/reject`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      render();
    });
  });

  $("#btn-download").addEventListener("click", () => {
    window.location.href = `/api/job/${job.id}/excel`;
  });
  $("#btn-restart").addEventListener("click", () => {
    job = null;
    $("#file")._file = null; $("#file").value = "";
    $("#dz-text").innerHTML = 'Drop PDF here or <span class="link">browse</span>';
    $("#dropzone").hidden = false;
    $("#file-hint").hidden = false;
    $("#file-hint").textContent = "Upload a CIBIL report PDF.";
    $("#extract-activity").hidden = true;
    $("#btn-extract").hidden = false;
    $("#btn-extract").disabled = true;
    render();
  });
}

// --------------------------------------------------------------------------- //
// How it works: top nav + Mermaid diagrams
// --------------------------------------------------------------------------- //
const DIAGRAMS = {
  "seq-box": `sequenceDiagram
  actor Maker as Maker (CPA)
  participant UI as Browser UI
  participant API as FastAPI
  participant PDF as pypdfium2 (text)
  participant DI as Document Intelligence
  participant AG as Foundry gpt-5.4-mini (cibil-extractor)
  participant Rules as Rules engine
  participant XL as Excel generator
  actor Checker
  Maker->>UI: Upload CIBIL PDF
  UI->>API: POST /api/extract
  API->>PDF: read text layer
  API->>API: detect report type · chunk per facility
  opt no text layer (scanned PDF)
    API->>DI: Document Intelligence (OCR / layout)
  end
  API->>AG: report text + extraction instructions
  AG-->>API: customer + all accounts + enquiries (JSON)
  API->>Rules: apply_rules()
  Rules-->>API: sequencing · summaries · 6/6 reconciliation
  API-->>UI: data + timings + tokens
  Maker->>UI: Review · edit · Submit
  Checker->>UI: Approve
  UI->>API: POST /approve
  API->>XL: build_workbook()
  XL-->>API: .xlsx (5 sheets)
  UI->>API: GET /excel
  API-->>Maker: Download workbook`,
  "arch-local-box": `flowchart LR
  U["Browser UI<br/>HTML · CSS · JS"]
  A["FastAPI backend<br/>127.0.0.1:8010"]
  P["pypdfium2<br/>text layer"]
  DI["Azure Document Intelligence<br/>prebuilt-layout"]
  L["Microsoft Foundry<br/>gpt-5.4-mini · cibil-extractor"]
  R["rules.py<br/>normalize · summaries · reconcile"]
  X["excelgen.py<br/>openpyxl"]
  M[("In-memory<br/>job store")]
  D[/"Excel .xlsx"/]
  U -->|HTTP| A
  A --> P
  A --> DI
  P -->|report text| L
  DI -->|scanned pages| L
  L -->|structured JSON| R
  R --> X
  A --> M
  X --> D
  D -->|download| U`,
  "arch-azure-box": `flowchart TB
  CPA["CPA (maker / checker)"]
  ID["Microsoft Entra<br/>External ID"]
  GW["App Gateway + WAF"]
  subgraph AKS["AKS + KEDA · Workload Identity"]
    WEB["Portal pods<br/>upload · maker-checker"]
    WK["Worker pods<br/>autoscaled by queue"]
  end
  ACR[("ACR<br/>images")]
  SB[["Service Bus<br/>queue + DLQ"]]
  DI["Document Intelligence"]
  AG["Foundry agent<br/>cibil-extractor · gpt-5.4-mini"]
  RU["Rules + Excel<br/>rules.py · openpyxl"]
  BLOB[("Blob Storage<br/>PDFs · Excel")]
  SQL[("Azure SQL / PostgreSQL<br/>state · audit · RLS")]
  MON["Azure Monitor"]
  CPA -->|sign in| ID
  CPA -->|HTTPS| GW --> WEB
  WEB -->|enqueue| SB
  WEB --> BLOB
  WEB --> SQL
  SB -->|scale + feed| WK
  WK --> DI
  WK --> AG
  DI --> AG
  AG --> RU
  RU --> BLOB
  WK --> SQL
  ACR -. images .-> AKS
  AKS --> MON`,
};

let mermaidReady = false;
function ensureMermaid() {
  if (mermaidReady || !window.mermaid) return;
  window.mermaid.initialize({
    startOnLoad: false, theme: "base", securityLevel: "loose",
    themeVariables: {
      primaryColor: "#eaf0fa", primaryBorderColor: "#003a8c",
      primaryTextColor: "#1f2733", lineColor: "#5b6b86",
      fontFamily: "Segoe UI, Roboto, sans-serif", fontSize: "13px",
    },
  });
  mermaidReady = true;
}

async function renderDiagrams() {
  ensureMermaid();
  if (!window.mermaid) return;
  for (const [id, def] of Object.entries(DIAGRAMS)) {
    const box = document.getElementById(id);
    if (!box || box.dataset.done) continue;
    try {
      const { svg } = await window.mermaid.render(id + "-svg", def);
      box.innerHTML = svg;
      const el2 = box.querySelector("svg");
      if (el2) {
        const bb = el2.getBBox();
        const p = 16;
        el2.setAttribute("viewBox",
          `${bb.x - p} ${bb.y - p} ${bb.width + 2 * p} ${bb.height + 2 * p}`);
        el2.setAttribute("width", String(Math.ceil(bb.width + 2 * p)));
        el2.removeAttribute("height");
        el2.style.maxWidth = "100%";
      }
      box.dataset.done = "1";
    } catch (err) {
      box.innerHTML = '<p class="error">Diagram failed to render.</p>';
      console.error(err);
    }
  }
}

function initTopNav() {
  document.querySelectorAll(".topnav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.view;
      document.querySelectorAll(".topnav-btn").forEach((b) =>
        b.classList.toggle("active", b === btn));
      $("#app-view").hidden = view !== "app";
      $("#howto-view").hidden = view !== "howto";
      if (view === "howto") renderDiagrams();
      window.scrollTo(0, 0);
    });
  });
}

initUpload();
initActions();
initTopNav();
initCapabilities();
render();
