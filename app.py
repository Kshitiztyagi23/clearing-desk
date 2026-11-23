"""Clearing Desk app: python app.py, then open http://127.0.0.1:8765. Set DESK_PASSCODE so only the owner can clear history."""
import hmac
import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import pymupdf
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from pipeline import MAX_PAGE_SIDE, MODEL, PDF_LOCK, ROOT, STAGES, bedrock_read, connect, ink_free, process, record_receipts, saved_read, scenario_master, split_invoices, upright, words
from tests.test_scenarios import SIMULATED_FAULTS, run_case

# Test invoices use saved AI readings, so replaying them never spends an API call. Only a new upload can.
READY = ("H01", "B01", "B02", "B06", "B07", "B17", "B18", "B19", "B20", "T07", "T08", "T09")
MAX_UPLOAD = 10 * 1024 * 1024
MAX_UPLOAD_PAGES = 20
MAX_KEPT_UPLOADS = 30
SCENARIOS = {s["id"]: s for s in json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))}
MASTER = json.loads((ROOT / "data" / "master.json").read_text(encoding="utf-8"))
UPLOADS = {}
OWNER_CODE = os.environ.get("DESK_PASSCODE", "")
app = FastAPI(title="Clearing Desk", docs_url=None, redoc_url=None)


def open_ledger():
    db = sqlite3.connect(ROOT / "data" / "ledger.db")
    db.row_factory = sqlite3.Row
    db.execute("""create table if not exists ledger(id integer primary key, at text, source text, ref text, title text, invoice_number text, vendor text, outcome text,
                  reason text, message text, total real, payable real, held real, deducted real, amount_known int, result text)""")
    return db


def record(source, ref, title, result):
    with closing(open_ledger()) as db, db:
        # A replay replaces its last entry so totals never count it twice. An approved upload stays on record.
        db.execute("delete from ledger where ref=? and (source='Test invoice' or outcome not in ('APPROVED', 'PART_PAY'))", (ref,))
        db.execute("insert into ledger(at, source, ref, title, invoice_number, vendor, outcome, reason, message, total, payable, held, deducted, amount_known, result) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (datetime.now(timezone.utc).isoformat(), source, ref, title, result.get("invoice_number"), result.get("vendor_name"), result["outcome"], result["reason"], result["message"],
                    result.get("total") or 0, result["payable"], result["held"], result["deducted"], int(result.get("amount_known", True)), json.dumps(result)))


def ready_case(invoice_id):
    if invoice_id not in READY:
        raise HTTPException(404, "This invoice is not ready yet.")
    return SCENARIOS[invoice_id]["cases"][0]


def file_of(invoice_id):
    if invoice_id in UPLOADS:
        return UPLOADS[invoice_id]["path"]
    return ROOT / "data" / "invoices" / ready_case(invoice_id)["steps"][0]["file"]


@lru_cache(maxsize=64)
def page_image(invoice_id):
    path = file_of(invoice_id)
    try:
        with PDF_LOCK, pymupdf.open(path) as doc:
            if doc.needs_pass or max(doc[0].rect.width, doc[0].rect.height) > MAX_PAGE_SIDE:
                return None, 1.0, 1 / 1.414, False
            page = doc[0]
            has_text = bool(page.get_text("words"))
            if not has_text:
                upright(page)
            bottom = max((w[3] for w in page.get_text("words")), default=None) or ink_free(page)
            return page.get_pixmap(dpi=170).tobytes("png"), min(1.0, (bottom + 28) / page.rect.height), page.rect.width / page.rect.height, has_text
    except Exception:
        return None, 1.0, 1 / 1.414, False


def tray_item(invoice_id):
    png, crop, ratio, has_text = page_image(invoice_id)
    base = {"id": invoice_id, "crop": crop, "ratio": ratio, "preview": png is not None}
    if invoice_id in UPLOADS:
        return {**base, "title": UPLOADS[invoice_id]["title"], "vendor": "Not read yet", "invoice_number": "Not read yet", "total": None, "upload": True}
    steps = ready_case(invoice_id)["steps"]
    truth = steps[0]["truth"]
    return {**base, "title": SCENARIOS[invoice_id]["title"], "vendor": truth["vendor_name"], "invoice_number": truth["invoice_number"], "total": truth["total"] if has_text else None,
            "recheck": len(steps) > 1 and steps[1].get("action") == "add_receipt_and_recheck"}


@app.get("/api/health")
def health():
    with closing(sqlite3.connect(ROOT / "cache" / "readings.db")) as db:
        db.execute("select count(*) from readings").fetchone()
    return {"ok": True}


@app.get("/api/desk")
def desk():
    return {"as_of": MASTER["as_of"], "buyer": MASTER["buyer_entities"][MASTER["our_entity"]]["name"], "model": ("Opus 5.5" if "opus-5-5" in MODEL else MODEL) + ", Grok 4.6 for scans",
            "stages": [name for name, _ in STAGES], "tray": [tray_item(i) for i in READY], "owner_locked": bool(OWNER_CODE)}  # Uploads stay with the visitor who added them.


@app.get("/api/invoice/{invoice_id}.png")
def invoice_png(invoice_id: str):
    png = page_image(invoice_id)[0]
    if png is None:
        raise HTTPException(404, "This file has no preview.")
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/upload")
async def upload(request: Request):
    data = bytearray()
    async for chunk in request.stream():  # Counted as it arrives, so a huge upload cannot fill memory first.
        data += chunk
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "The file is larger than 10 MB.")
    if not data:
        raise HTTPException(400, "The file is empty.")
    name = " ".join(Path(unquote(request.headers.get("x-filename") or "invoice.pdf").replace("\\", "/")).name.split())[:90] or "invoice.pdf"
    sender = (request.headers.get("x-sender") or "").strip()[:200] or None
    # PDF work runs off the event loop, so a large file cannot freeze the app for everyone else.
    return {"items": await run_in_threadpool(add_upload, data, name, sender)}


def add_upload(data, name, sender):
    folder = ROOT / "uploads"
    folder.mkdir(exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.pdf"
    path.write_bytes(data)
    try:
        with PDF_LOCK, pymupdf.open(path) as doc:
            pages = 1 if doc.needs_pass else doc.page_count
    except Exception:
        pages = 1
    if pages > MAX_UPLOAD_PAGES:
        path.unlink(missing_ok=True)
        raise HTTPException(413, f"This PDF has {pages} pages. The limit is {MAX_UPLOAD_PAGES}.")
    files = split_invoices(path, folder)
    if files != [path]:
        path.unlink(missing_ok=True)
    items = []
    for index, file in enumerate(files):
        invoice_id = "U" + uuid.uuid4().hex[:10]
        UPLOADS[invoice_id] = {"path": file, "sender": sender if index == 0 else None, "title": name if len(files) == 1 else f"{name}, invoice {index + 1} of {len(files)}"}
        items.append(tray_item(invoice_id))
    while len(UPLOADS) > MAX_KEPT_UPLOADS:
        UPLOADS.pop(next(iter(UPLOADS)))["path"].unlink(missing_ok=True)
    return items


@app.get("/api/run/{invoice_id}")
def run(invoice_id: str, step: int = 0):
    uploaded = UPLOADS.get(invoice_id)
    if uploaded:
        path, sender, reader, master, source, title = uploaded["path"], uploaded["sender"], bedrock_read, MASTER, "Upload", uploaded["title"]
    else:
        case = ready_case(invoice_id)
        if not 0 <= step < len(case["steps"]):
            raise HTTPException(404, "This step does not exist.")
        current = case["steps"][step]
        master = scenario_master(MASTER, case["setup"])
        for earlier in case["steps"][:step + 1]:
            record_receipts(master, earlier.get("receipts", {}))
        path, sender, reader, source, title = ROOT / "data" / "invoices" / current["file"], current["sender"], saved_read, "Test invoice", SCENARIOS[invoice_id]["title"]
    events = queue.Queue()

    def work():
        # Uploads share one ledger, so sending the same file twice is caught. Test invoices start clean each time.
        db = connect() if uploaded else connect(":memory:")
        try:
            result = process(path, sender, db, reader, master, lambda e: events.put({"type": "stage", **e}))
            result = {**result, "amount_words": f"Rupees {words(int(result['payable']))} Only" if result["payable"] >= 1 else "Nothing to pay"}
            record(source, invoice_id, title, result)
            events.put({"type": "done", "result": result})
        except Exception as exc:
            events.put({"type": "error", "message": f"The run stopped safely ({type(exc).__name__}). No payment was approved."})
        finally:
            db.close()

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            try:
                event = events.get(timeout=280)
            except queue.Empty:
                event = {"type": "error", "message": "The run timed out. No payment was approved."}
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] != "stage":
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.get("/api/ledger")
def ledger():
    with closing(open_ledger()) as db:
        rows = [dict(row) for row in db.execute("select id, at, source, ref, title, invoice_number, vendor, outcome, reason, message, total, payable, held, deducted, amount_known from ledger order by id desc")]
    money = lambda key, *outcomes: round(sum(r[key] for r in rows if not outcomes or r["outcome"] in outcomes), 2)
    return {"rows": rows, "counts": {o: sum(r["outcome"] == o for r in rows) for o in ("APPROVED", "PART_PAY", "HOLD", "BLOCKED", "RETURNED")},
            "money": {"cleared": money("payable"), "deducted": money("deducted"), "held": money("held", "HOLD", "PART_PAY"), "stopped": money("held", "BLOCKED")},
            "unknown": sum(not r["amount_known"] and r["outcome"] != "RETURNED" for r in rows)}


@app.get("/api/ledger/{entry}")
def ledger_entry(entry: int):
    if not 0 < entry < 2 ** 63:
        raise HTTPException(404, "This entry no longer exists.")
    with closing(open_ledger()) as db:
        row = db.execute("select * from ledger where id=?", (entry,)).fetchone()
    if not row:
        raise HTTPException(404, "This entry no longer exists.")
    return {**dict(row), "result": json.loads(row["result"])}


@app.post("/api/ledger/reset")
def reset_ledger(request: Request):
    if OWNER_CODE and not hmac.compare_digest(request.headers.get("x-owner-code", "").encode(), OWNER_CODE.encode()):
        time.sleep(1)  # Slows down guessing.
        raise HTTPException(403, "Only the desk owner can clear history.")
    with closing(open_ledger()) as db, db:
        db.execute("delete from ledger")
    with closing(connect()) as db, db:
        db.execute("delete from runs")
    return {"ok": True}


def real_run(scenario, case, report):
    """The last real check for the current AI model, so changing the model resets this to Not run."""
    label = scenario["id"] + (f" ({case['name']})" if case["name"] else "")
    if any(step.get("fault") in SIMULATED_FAULTS for step in case["steps"]):
        return "Simulated"
    return "Not run" if label not in report else "Failed" if report[label] else "Passed"


@app.get("/api/lab")
def lab():
    saved = ROOT / "output" / f"real_ai_{MODEL.replace(':', '_')}.json"
    report = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}

    def stream():
        passed = total = 0
        for scenario in SCENARIOS.values():
            for case in scenario["cases"]:
                try:
                    problems = run_case(case)
                except Exception as exc:
                    problems = [f"The test crashed ({type(exc).__name__})."]
                total += 1
                passed += not problems
                expected = [e for step in case["steps"] for e in (step["expect"] if isinstance(step["expect"], list) else [step["expect"]])]
                yield "data: " + json.dumps({"type": "case", "id": scenario["id"], "title": scenario["title"], "name": case["name"], "group": scenario["group"], "passed": not problems, "problems": problems,
                                             "expected": [{"outcome": e["outcome"], "reason": e["reason"]} for e in expected], "real_run": real_run(scenario, case, report)}) + "\n\n"
        yield "data: " + json.dumps({"type": "done", "passed": passed, "total": total}) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
