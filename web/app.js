const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
const money = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const rupees = (value) => value === null || value === undefined ? "-" : `Rs ${money.format(value)}`;
const qty = (value) => Number(value).toLocaleString("en-IN");
const STAMP = { APPROVED: "APPROVED", PART_PAY: "PART-PAY", HOLD: "HOLD", BLOCKED: "BLOCKED", RETURNED: "RETURNED" };
const LABEL = { PASS: "Cleared", HOLD: "Hold", BLOCKED: "Blocked", RETURNED: "Returned", PART_PAY: "Part-pay", SKIPPED: "Skipped", APPROVED: "Approved" };
const OUTCOMES = ["APPROVED", "PART_PAY", "HOLD", "BLOCKED", "RETURNED"];
const FOCUS = { bank_mismatch: ["Bank account", "IFSC"] };
const FLAG = { "Over PO": [3, 4], "Short receipt": [2], "Failed QC": [2], "No receipt": [2], "Rate differs": [5] };
const PACE = matchMedia("(prefers-reduced-motion: reduce)").matches ? 40 : 430;
const params = new URLSearchParams(location.search);
// Palette preview. Remove the two unused looks once one is chosen.
if (["aubergine", "graphite"].includes(params.get("look"))) document.documentElement.dataset.look = params.get("look");
let desk, current, stageNodes = [], reached = {}, running = false, ledgerData = null, ledgerFilter = "ALL", labRows = [], labFilter = "ALL", labRunning = false;

function deskDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

function setUrl(changes) {
  const url = new URL(location.href);
  for (const [key, value] of Object.entries(changes)) value === null ? url.searchParams.delete(key) : url.searchParams.set(key, value);
  history.replaceState(null, "", url);
}

function showView(name) {
  for (const [view, id] of [["desk", "deskView"], ["ledger", "ledgerView"], ["lab", "labView"]]) $(id).hidden = view !== name;
  document.querySelectorAll("[data-view]").forEach((link) => link.dataset.view === name ? link.setAttribute("aria-current", "page") : link.removeAttribute("aria-current"));
  setUrl({ view: name === "desk" ? null : name });
  if (name === "ledger") loadLedger();
  if (name === "lab" && !labRows.length) drawLab();
}

function showMarks(stage, only) {
  const layer = $("marks");
  layer.replaceChildren();
  const crop = current.crop;
  const marks = (stage?.evidence || []).filter((mark) => !only || only.includes(mark.label));
  for (const mark of marks) {
    const [x0, y0, x1, y1] = mark.box;
    const box = el("div", "mark");
    Object.assign(box.style, { left: `${x0 * 100}%`, width: `${(x1 - x0) * 100}%`, top: `${(y0 / crop) * 100}%`, height: `${((y1 - y0) / crop) * 100}%` });
    layer.append(box);
  }
  stageNodes.forEach((node, index) => node.classList.toggle("selected", stage?.stage === index + 1));
  const note = $("evidenceNote");
  note.replaceChildren();
  if (stage) {
    const names = [...new Set(marks.map((mark) => mark.label))];
    note.append(el("b", "", `${stage.name}: `), names.length ? `highlighted ${names.join(", ")}` : stage.detail);
  } else {
    note.textContent = "Clear the invoice, then select any stage to see what it checked.";
  }
}

function buildStages() {
  $("stages").replaceChildren();
  stageNodes = desk.stages.map((name, index) => {
    const row = el("li");
    const button = el("button", "stage pending");
    button.type = "button";
    button.disabled = true;
    button.append(el("span", "hole"));
    const title = el("b");
    title.append(el("i", "", String(index + 1)), name);
    button.append(title, el("em", "", "Waiting"), el("small", "", " "));
    button.addEventListener("click", () => reached[index + 1] && showMarks(reached[index + 1]));
    row.append(button);
    $("stages").append(row);
    return button;
  });
}

function resetRun() {
  reached = {};
  buildStages();
  showMarks(null);
  $("stamp").className = "stamp";
  $("stamp").replaceChildren();
  const empty = el("tr");
  const cell = el("td", "empty", "The match appears when the Match stage runs.");
  cell.colSpan = 7;
  empty.append(cell);
  $("matchRows").replaceChildren(empty);
  $("slipRef").textContent = current.invoice_number;
  $("payee").textContent = current.vendor;
  $("amount").textContent = "Rs -";
  $("amountWords").textContent = "Awaiting decision";
  $("why").replaceChildren();
  $("invoiceTotal").textContent = rupees(current.total);
  $("deducted").textContent = "-";
  $("held").textContent = "-";
  $("nextStep").textContent = "A person releases every payment.";
  $("recheckButton").hidden = true;
  $("draftBox").hidden = true;
  $("draftBox").open = false;
}

function drawMatch(rows) {
  if (!rows?.length) return;
  $("matchRows").replaceChildren(...rows.map((row) => {
    const tr = el("tr");
    const flagged = FLAG[row.status] || [];
    [row.description, `${qty(row.ordered)} ${row.unit}`, qty(row.accepted), qty(row.billed_before), qty(row.invoiced), money.format(row.invoice_rate)].forEach((value, index) => tr.append(el("td", flagged.includes(index) ? "flag" : "", value)));
    const result = el("td");
    result.append(el("span", row.status === "Matched" ? "good" : "bad", row.status));
    tr.append(result);
    return tr;
  }));
}

function showStage(event) {
  reached[event.stage] = event;
  const node = stageNodes[event.stage - 1];
  node.className = `stage ${event.status}`;
  node.disabled = false;
  node.querySelector("em").textContent = event.status === "SKIPPED" ? LABEL.SKIPPED : `${LABEL[event.status]}, ${event.ms} ms`;
  const detail = node.querySelector("small");
  detail.textContent = event.detail;
  detail.title = event.detail;
  stageNodes[event.stage]?.classList.replace("pending", "running");
  drawMatch(event.match);
  if (event.evidence?.length) showMarks(event);
}

function showDraft(draft) {
  $("draftBox").hidden = !draft;
  if (!draft) return;
  $("draftLabel").textContent = `Draft for ${draft.audience}`;
  $("draftTo").textContent = draft.to;
  $("draftSubject").textContent = draft.subject;
  $("draftBody").textContent = draft.body;
  $("copyDraft").textContent = "Copy draft";
}

function finish(result, step) {
  const stamp = $("stamp");
  stamp.replaceChildren(el("strong", "", STAMP[result.outcome]), el("small", "", `${deskDate(desk.as_of)}, AP desk`));
  stamp.className = `stamp show ${result.outcome}`;
  if (result.invoice_number) $("slipRef").textContent = result.invoice_number;
  if (result.vendor_name) $("payee").textContent = result.vendor_name;
  $("amount").textContent = rupees(result.payable);
  $("amountWords").textContent = result.amount_words;
  $("why").replaceChildren(...result.findings.filter((f) => f.outcome !== "APPROVED").map((f) => el("li", f.outcome, f.message)));
  $("invoiceTotal").textContent = result.amount_known === false ? "Not readable" : rupees(result.total);
  $("deducted").textContent = rupees(result.deducted);
  $("held").textContent = result.amount_known === false ? "Whole invoice" : rupees(result.held);
  $("nextStep").textContent = result.next_step || result.message;
  $("recheckButton").hidden = !(current.recheck && step === 0 && result.reason === "awaiting_receipt");
  showDraft(result.draft);
  if (reached[7]?.status === "SKIPPED") $("matchRows").querySelector(".empty").textContent = "Not matched, because an earlier check stopped this invoice.";
  const decided = stageNodes[8];
  decided.className = `stage ${result.outcome === "APPROVED" ? "PASS" : result.outcome}`;
  decided.querySelector("em").textContent = LABEL[result.outcome];
  stageNodes[9].querySelector("em").textContent = "Done";
  const cause = Object.values(reached).find((stage) => stage.status === result.outcome);
  showMarks(cause || null, cause && FOCUS[result.reason]);
  if (!cause) $("evidenceNote").textContent = "Every check passed. Select any stage to see what it checked.";
  current.outcome = result.outcome;
  if (current.upload) {
    current.invoice_number = result.invoice_number || "Unread";
    current.vendor = result.vendor_name || "Unknown vendor";
    if (result.amount_known !== false) current.total = result.total;
  }
  drawTray();
}

function stopRunning(label) {
  running = false;
  $("runButton").disabled = false;
  $("runButton").textContent = label;
  drawTray();
}

function trayAmount(item) {
  if (item.total !== null) return rupees(item.total);
  return item.upload ? "Uploaded" : "Scanned";
}

function drawTray() {
  $("tray").replaceChildren(...desk.tray.map((item) => {
    const doc = el("button", `tray-doc ${item.outcome || ""}`);
    doc.type = "button";
    doc.disabled = running;
    doc.title = item.title;
    doc.setAttribute("aria-pressed", String(item === current));
    doc.append(el("strong", "", item.title), el("span", "", item.invoice_number), el("b", "", trayAmount(item)));
    if (item.outcome) doc.append(el("i", "", LABEL[item.outcome]));
    doc.addEventListener("click", () => selectInvoice(item));
    return doc;
  }));
}

function selectInvoice(item) {
  if (running) return;
  current = item;
  setUrl({ run: null, invoice: item.upload ? null : item.id });
  $("paper").style.aspectRatio = `${current.ratio} / ${current.crop}`;
  $("invoice").hidden = !item.preview;
  $("noPreview").hidden = item.preview;
  if (item.preview) $("invoice").src = `/api/invoice/${current.id}.png`;
  $("runButton").textContent = item.upload ? "Clear with live AI" : "Clear this invoice";
  resetRun();
  drawTray();
}

function runInvoice(step = 0) {
  if (running) return;
  running = true;
  resetRun();
  drawTray();
  $("runButton").disabled = true;
  $("runButton").textContent = "Clearing…";
  stageNodes[0].classList.replace("pending", "running");
  const queue = [];
  let streamDone = false;
  const source = new EventSource(`/api/run/${current.id}?step=${step}`);
  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    queue.push(event);
    if (event.type !== "stage") { streamDone = true; source.close(); }
  };
  source.onerror = () => {
    if (streamDone) return;
    source.close();
    streamDone = true;
    queue.push({ type: "error", message: "The connection stopped. No payment was approved. Check that the app is running, then try again." });
  };
  const tick = () => {
    const event = queue.shift();
    if (!event) return setTimeout(tick, 60);
    if (event.type === "stage") { showStage(event); return setTimeout(tick, PACE); }
    if (event.type === "done") { finish(event.result, step); return stopRunning("Clear again"); }
    $("nextStep").textContent = event.message;
    stopRunning("Try again");
  };
  tick();
}

async function uploadFile(file) {
  if (!file || running) return;
  const note = $("uploadNote");
  if (file.size > 10 * 1024 * 1024) {
    note.textContent = "This file is larger than 10 MB. Choose a smaller PDF.";
    $("fileInput").value = "";
    return;
  }
  note.textContent = `Adding ${file.name}…`;
  try {
    const response = await fetch("/api/upload", { method: "POST", headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name), "X-Sender": $("senderInput").value.trim() }, body: file });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "The upload failed.");
    desk.tray.unshift(...data.items);
    selectInvoice(data.items[0]);
    note.textContent = data.items.length > 1 ? `Found ${data.items.length} invoices. Clear each one.` : "Added. Select Clear with live AI to read it.";
  } catch (error) {
    note.textContent = `${error.message} Choose another PDF.`;
  }
  $("fileInput").value = "";
}

async function loadLedger() {
  try {
    ledgerData = await (await fetch("/api/ledger")).json();
    drawLedger();
  } catch {
    $("ledgerRows").replaceChildren(emptyRow(8, "The ledger could not load. Check that the app is running."));
  }
}

function emptyRow(span, text) {
  const tr = el("tr");
  const td = el("td", "empty", text);
  td.colSpan = span;
  tr.append(td);
  return tr;
}

function drawFlow() {
  const { cleared, deducted, held, stopped } = ledgerData.money;
  const parts = [["ok", "Cleared to pay", cleared], ["part", "Deducted", deducted], ["hold", "On hold", held], ["block", "Stopped", stopped]];
  const total = parts.reduce((sum, part) => sum + part[2], 0);
  $("flowBar").replaceChildren(...parts.filter((part) => part[2] > 0).map(([tone, label, value]) => {
    const segment = el("span", `seg ${tone}`);
    segment.style.flexGrow = value;
    segment.title = `${label}: ${rupees(value)} (${Math.round((value / total) * 100)}%)`;
    return segment;
  }));
  $("flowBar").hidden = total === 0;
  $("flowKey").replaceChildren(...parts.map(([tone, label, value]) => {
    const item = el("li");
    item.append(el("span", `swatch ${tone}`), el("span", "key-label", label), el("strong", "", rupees(value)));
    return item;
  }));
  $("flowNote").hidden = !ledgerData.unknown;
  $("flowNote").textContent = `${ledgerData.unknown} unreadable ${ledgerData.unknown === 1 ? "invoice is" : "invoices are"} on hold with no amount counted.`;
}

function drawFilters(host, options, active, choose) {
  host.replaceChildren(...options.map(([value, label, count]) => {
    const button = el("button", "filter", `${label} ${count}`);
    button.type = "button";
    button.setAttribute("aria-pressed", String(value === active));
    button.addEventListener("click", () => choose(value));
    return button;
  }));
}

function drawLedger() {
  drawFlow();
  const { rows, counts } = ledgerData;
  drawFilters($("filters"), [["ALL", "All", rows.length], ...OUTCOMES.map((o) => [o, LABEL[o], counts[o]])], ledgerFilter, (value) => { ledgerFilter = value; drawLedger(); });
  const shown = rows.filter((row) => ledgerFilter === "ALL" || row.outcome === ledgerFilter);
  if (!rows.length) {
    const tr = emptyRow(8, "No invoices cleared yet. ");
    const link = el("a", "", "Go to the Live desk and clear one.");
    link.href = "?view=desk";
    link.addEventListener("click", (event) => { event.preventDefault(); showView("desk"); });
    tr.firstChild.append(link);
    $("ledgerRows").replaceChildren(tr);
    $("entryPanel").hidden = true;
    return;
  }
  $("ledgerRows").replaceChildren(...(shown.length ? shown.map(ledgerRow) : [emptyRow(8, "No invoices have this decision yet.")]));
}

function ledgerRow(row) {
  const tr = el("tr", row.outcome);
  const first = el("td");
  const open = el("button", "row-open");
  open.type = "button";
  open.append(el("strong", "", row.invoice_number || "Unread"), el("small", "", row.title));
  open.addEventListener("click", () => showEntry(row.id));
  first.append(open);
  const decision = el("td");
  decision.append(el("span", "pill", LABEL[row.outcome]));
  const why = el("td", "why-cell", row.reason === "clean" ? "Every check passed." : row.message);
  why.title = row.message;
  const when = new Date(row.at).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  tr.append(first, el("td", "", row.vendor || "Unknown"), decision, why, el("td", "num", row.amount_known ? rupees(row.total) : "Not readable"), el("td", "num", rupees(row.payable)), el("td", "num", row.amount_known ? rupees(row.held) : "Whole invoice"), el("td", "num", when));
  return tr;
}

async function showEntry(id) {
  const panel = $("entryPanel");
  try {
    const response = await fetch(`/api/ledger/${id}`);
    if (!response.ok) throw new Error();
    const { result, title, source } = await response.json();
    const head = el("header", "slip-head");
    head.append(el("h2", "", result.invoice_number || "Unread"));
    const close = el("button", "entry-close", "Close");
    close.type = "button";
    close.addEventListener("click", () => { panel.hidden = true; });
    head.append(close);
    const body = el("div", "entry-body");
    body.append(el("p", `entry-outcome ${result.outcome}`, LABEL[result.outcome]), el("p", "entry-title", `${title}, ${source.toLowerCase()}`));
    const reasons = el("ul", "entry-why");
    const found = result.findings.filter((f) => f.outcome !== "APPROVED");
    reasons.append(...(found.length ? found : [{ outcome: "PASS", message: "Every check passed." }]).map((f) => el("li", f.outcome, f.message)));
    const figures = el("dl", "bridge");
    for (const [label, value] of [["Invoice total", result.amount_known === false ? "Not readable" : rupees(result.total)], ["To pay", rupees(result.payable)], ["Held", result.amount_known === false ? "Whole invoice" : rupees(result.held)]]) {
      const cell = el("div");
      cell.append(el("dt", "", label), el("dd", "", value));
      figures.append(cell);
    }
    const trail = el("ol", "entry-trail");
    trail.append(...(result.stages || []).map((stage) => {
      const status = stage.stage === 9 ? (result.outcome === "APPROVED" ? "PASS" : result.outcome) : stage.status;
      const label = stage.stage === 10 ? "Done" : stage.stage === 9 ? LABEL[result.outcome] : LABEL[status];
      const item = el("li", status);
      item.append(el("b", "", `${stage.stage} ${stage.name}`), el("em", "", label), el("small", "", stage.detail));
      return item;
    }));
    body.append(reasons, figures, el("p", "next", result.next_step || ""));
    if (result.draft) {
      const details = el("details", "draft");
      details.append(el("summary", "", `Draft for ${result.draft.audience}`), el("p", "draft-line", `To: ${result.draft.to}`), el("p", "draft-line", `Subject: ${result.draft.subject}`), el("pre", "", result.draft.body));
      body.append(details);
    }
    body.append(el("h3", "", "Audit trail"), trail);
    panel.replaceChildren(head, body);
    panel.hidden = false;
    close.focus({ preventScroll: true });
  } catch {
    loadLedger();
  }
}

function labOptions() {
  const count = (test) => labRows.filter(test).length;
  return [["ALL", "All", labRows.length], ["business", "Business", count((r) => r.group === "business")], ["technical", "Technical", count((r) => r.group === "technical")], ["FAILED", "Failed", count((r) => !r.passed)]];
}

function drawLab() {
  drawFilters($("labFilters"), labOptions(), labFilter, (value) => { labFilter = value; drawLab(); });
  const shown = labRows.filter((row) => labFilter === "ALL" || (labFilter === "FAILED" ? !row.passed : row.group === labFilter));
  if (!shown.length) {
    $("labRows").replaceChildren(emptyRow(6, labRows.length ? "No test cases match this filter." : "Select Run all test cases to start."));
    return;
  }
  $("labRows").replaceChildren(...shown.map((row) => {
    const tr = el("tr", row.passed ? "APPROVED" : "BLOCKED");
    const expected = el("td", "expected");
    row.expected.forEach((item) => expected.append(el("span", `pill ${item.outcome}`, LABEL[item.outcome])));
    const result = el("td");
    result.append(el("span", "pill", row.passed ? "Passed" : "Failed"));
    if (!row.passed) result.append(el("small", "lab-problem", row.problems.join("; ")));
    tr.append(el("td", "", row.id), el("td", "", row.name ? `${row.title}: ${row.name}` : row.title), el("td", "", row.group === "technical" ? "Technical" : row.group === "business" ? "Business" : "Happy path"), expected, el("td", "", row.real_ai ? "Yes" : "No"), result);
    return tr;
  }));
}

function runLab() {
  if (labRunning) return;
  labRunning = true;
  labRows = [];
  labFilter = "ALL";
  $("runLab").disabled = true;
  $("runLab").textContent = "Running…";
  $("labScore").textContent = "Running…";
  drawLab();
  const done = (text) => { labRunning = false; $("runLab").disabled = false; $("runLab").textContent = "Run again"; $("labScore").textContent = text; drawLab(); };
  const source = new EventSource("/api/lab");
  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.type === "case") {
      labRows.push(event);
      $("labScore").textContent = `Running… ${labRows.length} checked`;
      drawLab();
    } else {
      source.close();
      done(`${event.passed} of ${event.total} test cases passed.`);
    }
  };
  source.onerror = () => { source.close(); done("The test run stopped. Check that the app is running, then try again."); };
}

function wire() {
  $("runButton").addEventListener("click", () => runInvoice());
  $("recheckButton").addEventListener("click", () => runInvoice(1));
  $("fileInput").addEventListener("change", (event) => uploadFile(event.target.files[0]));
  const zone = $("dropZone");
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (event) => { event.preventDefault(); zone.classList.remove("over"); uploadFile(event.dataTransfer.files[0]); });
  $("copyDraft").addEventListener("click", async () => {
    const text = `To: ${$("draftTo").textContent}\nSubject: ${$("draftSubject").textContent}\n\n${$("draftBody").textContent}`;
    try {
      await navigator.clipboard.writeText(text);
      $("copyDraft").textContent = "Copied";
    } catch {
      $("copyDraft").textContent = "Select the text to copy";
    }
  });
  $("resetLedger").addEventListener("click", async () => {
    if (!confirm("Clear all ledger history? Saved AI readings stay, so this costs no API calls.")) return;
    await fetch("/api/ledger/reset", { method: "POST" });
    desk.tray.forEach((item) => { delete item.outcome; });
    drawTray();
    $("entryPanel").hidden = true;
    ledgerFilter = "ALL";
    loadLedger();
  });
  $("runLab").addEventListener("click", runLab);
  document.querySelectorAll("[data-view]").forEach((link) => link.addEventListener("click", (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    showView(link.dataset.view);
  }));
}

async function start() {
  desk = await (await fetch("/api/desk")).json();
  $("buyer").textContent = desk.buyer;
  $("deskDate").textContent = deskDate(desk.as_of);
  $("model").textContent = desk.model;
  wire();
  const autoRun = params.get("run");
  const wanted = autoRun || params.get("invoice");
  selectInvoice(desk.tray.find((item) => item.id === wanted) || desk.tray[0]);
  showView(["ledger", "lab"].includes(params.get("view")) ? params.get("view") : "desk");
  if (autoRun) runInvoice();
  if (params.get("view") === "lab" && params.get("auto")) runLab();
}

start().catch(() => { $("nextStep").textContent = "The desk could not load. Check that the local app is running."; });
