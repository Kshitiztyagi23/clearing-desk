const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
const rupees = (value) => value === null || value === undefined ? "-" : `Rs ${Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const qty = (value) => Number(value).toLocaleString("en-IN");
const STAMP = { APPROVED: "APPROVED", PART_PAY: "PART-PAY", HOLD: "HOLD", BLOCKED: "BLOCKED", RETURNED: "RETURNED" };
const LABEL = { PASS: "Cleared", HOLD: "Hold", BLOCKED: "Blocked", RETURNED: "Returned", PART_PAY: "Part-pay", SKIPPED: "Skipped", APPROVED: "Approved" };
const FOCUS = { bank_mismatch: ["Bank account", "IFSC"] };
const FLAG = { "Over PO": [3, 4], "Short receipt": [2], "No receipt": [2], "Rate differs": [5] };
const PACE = matchMedia("(prefers-reduced-motion: reduce)").matches ? 40 : 430;
const params = new URLSearchParams(location.search);
// Palette preview. Remove the two unused looks once one is chosen.
if (["aubergine", "graphite"].includes(params.get("look"))) document.documentElement.dataset.look = params.get("look");
let desk, current, stageNodes = [], reached = {}, running = false;

function deskDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
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
}

function drawMatch(rows) {
  if (!rows?.length) return;
  $("matchRows").replaceChildren(...rows.map((row) => {
    const tr = el("tr");
    const flagged = FLAG[row.status] || [];
    [row.description, `${qty(row.ordered)} ${row.unit}`, qty(row.accepted), qty(row.billed_before), qty(row.invoiced), row.invoice_rate.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })].forEach((value, index) => tr.append(el("td", flagged.includes(index) ? "flag" : "", value)));
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

function finish(result) {
  const stamp = $("stamp");
  stamp.replaceChildren(el("strong", "", STAMP[result.outcome]), el("small", "", `${deskDate(desk.as_of)}, AP desk`));
  stamp.className = `stamp show ${result.outcome}`;
  $("amount").textContent = rupees(result.payable);
  $("amountWords").textContent = result.amount_words;
  $("why").replaceChildren(...result.findings.filter((f) => f.outcome !== "APPROVED").map((f) => el("li", f.outcome, f.message)));
  $("invoiceTotal").textContent = rupees(result.total);
  $("deducted").textContent = rupees(result.deducted);
  $("held").textContent = rupees(result.held);
  $("nextStep").textContent = result.next_step || result.message;
  if (reached[7]?.status === "SKIPPED") $("matchRows").querySelector(".empty").textContent = "Not matched, because an earlier check stopped this invoice.";
  const decided = stageNodes[8];
  decided.className = `stage ${result.outcome === "APPROVED" ? "PASS" : result.outcome}`;
  decided.querySelector("em").textContent = LABEL[result.outcome];
  stageNodes[9].querySelector("em").textContent = "Done";
  const cause = Object.values(reached).find((stage) => stage.status === result.outcome);
  showMarks(cause || null, cause && FOCUS[result.reason]);
  if (!cause) $("evidenceNote").textContent = "Every check passed. Select any stage to see what it checked.";
  current.outcome = result.outcome;
  drawTray();
}

function stopRunning(label) {
  running = false;
  $("runButton").disabled = false;
  $("runButton").textContent = label;
  drawTray();
}

function drawTray() {
  $("tray").replaceChildren(...desk.tray.map((item) => {
    const doc = el("button", `tray-doc ${item.outcome || ""}`);
    doc.type = "button";
    doc.disabled = running;
    doc.setAttribute("aria-pressed", String(item === current));
    doc.append(el("strong", "", item.title), el("span", "", item.invoice_number), el("b", "", rupees(item.total)));
    if (item.outcome) doc.append(el("i", "", LABEL[item.outcome]));
    doc.addEventListener("click", () => selectInvoice(item));
    return doc;
  }));
}

function selectInvoice(item) {
  if (running) return;
  current = item;
  $("paper").style.aspectRatio = `${current.ratio} / ${current.crop}`;
  $("invoice").src = `/api/invoice/${current.id}.png`;
  $("runButton").textContent = "Clear this invoice";
  resetRun();
  drawTray();
}

function runInvoice() {
  if (running) return;
  running = true;
  resetRun();
  drawTray();
  $("runButton").disabled = true;
  $("runButton").textContent = "Clearing...";
  stageNodes[0].classList.replace("pending", "running");
  const queue = [];
  let streamDone = false;
  const source = new EventSource(`/api/run/${current.id}`);
  source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    queue.push(event);
    if (event.type !== "stage") { streamDone = true; source.close(); }
  };
  source.onerror = () => {
    if (streamDone) return;
    source.close();
    streamDone = true;
    queue.push({ type: "error", message: "The connection stopped. No payment was approved." });
  };
  const tick = () => {
    const event = queue.shift();
    if (!event) return setTimeout(tick, 60);
    if (event.type === "stage") { showStage(event); return setTimeout(tick, PACE); }
    if (event.type === "done") { finish(event.result); return stopRunning("Clear again"); }
    $("nextStep").textContent = event.message;
    stopRunning("Try again");
  };
  tick();
}

async function start() {
  desk = await (await fetch("/api/desk")).json();
  $("buyer").textContent = `Accounts payable, ${desk.buyer}`;
  $("deskDate").textContent = deskDate(desk.as_of);
  $("model").textContent = desk.model;
  $("locked").textContent = `${desk.locked} more test cases unlock as their rules are finished.`;
  $("runButton").addEventListener("click", runInvoice);
  const wanted = params.get("run");
  selectInvoice(desk.tray.find((item) => item.id === wanted) || desk.tray[0]);
  if (wanted) runInvoice();
}

start().catch(() => { $("nextStep").textContent = "The desk could not load. Check that the local app is running."; });
