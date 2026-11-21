"""Clearing Desk local app: python app.py, then open http://127.0.0.1:8765"""
import json
import queue
import threading
from functools import lru_cache

import pymupdf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pipeline import MODEL, ROOT, STAGES, connect, process, saved_read, scenario_master, words

# Only invoices with finished rules and a saved AI reading can run, so the UI cannot spend API calls.
READY = ("H01", "B01", "B02", "B06", "B07")
SCENARIOS = {s["id"]: s for s in json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))}
MASTER = json.loads((ROOT / "data" / "master.json").read_text(encoding="utf-8"))
app = FastAPI(title="Clearing Desk", docs_url=None, redoc_url=None)


def ready_case(invoice_id):
    if invoice_id not in READY:
        raise HTTPException(404, "This invoice is not ready yet.")
    return SCENARIOS[invoice_id]["cases"][0]


def ink_bottom(page):
    """Scans have no word positions, so find the last row with ink, ignoring the scanner's edge shadows."""
    pix = page.get_pixmap(dpi=50, colorspace=pymupdf.csGRAY)
    left, right = int(pix.width * .06), int(pix.width * .94)
    rows = [y for y in range(int(pix.height * .97)) if sum(b < 110 for b in pix.samples[y * pix.stride + left:y * pix.stride + right]) >= 2]
    return (rows[-1] + 1) / pix.height * page.rect.height if rows else page.rect.height


@lru_cache(maxsize=8)
def page_image(invoice_id):
    step = ready_case(invoice_id)["steps"][0]
    with pymupdf.open(ROOT / "data" / "invoices" / step["file"]) as doc:
        page = doc[0]
        bottom = max((w[3] for w in page.get_text("words")), default=None) or ink_bottom(page)
        return page.get_pixmap(dpi=170).tobytes("png"), min(1.0, (bottom + 28) / page.rect.height), page.rect.width / page.rect.height


@app.get("/api/desk")
def desk():
    tray = []
    for invoice_id in READY:
        truth = ready_case(invoice_id)["steps"][0]["truth"]
        _, crop, ratio = page_image(invoice_id)
        tray.append({"id": invoice_id, "title": SCENARIOS[invoice_id]["title"], "vendor": truth["vendor_name"], "invoice_number": truth["invoice_number"], "total": truth["total"],
                     "po": truth["po_numbers"], "crop": crop, "ratio": ratio})
    return {"as_of": MASTER["as_of"], "buyer": MASTER["buyer_entities"][MASTER["our_entity"]]["name"], "model": "Amazon Nova 2 Lite" if "nova-2-lite" in MODEL else MODEL,
            "stages": [name for name, _ in STAGES], "tray": tray, "locked": len(SCENARIOS) - len(READY)}


@app.get("/api/invoice/{invoice_id}.png")
def invoice_png(invoice_id: str):
    return Response(page_image(invoice_id)[0], media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/run/{invoice_id}")
def run(invoice_id: str):
    case = ready_case(invoice_id)
    step = case["steps"][0]
    events = queue.Queue()

    def work():
        try:
            result = process(ROOT / "data" / "invoices" / step["file"], step["sender"], connect(":memory:"), saved_read, scenario_master(MASTER, case["setup"]), lambda e: events.put({"type": "stage", **e}))
            events.put({"type": "done", "result": {**result, "amount_words": f"Rupees {words(int(result['payable']))} Only" if result["payable"] else "Nothing to pay"}})
        except Exception as exc:
            events.put({"type": "error", "message": f"The run stopped safely ({type(exc).__name__}). No payment was approved."})

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            event = events.get(timeout=30)
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] != "stage":
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
