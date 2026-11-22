"""Offline check of the happy path and its safety rails. Makes no AI calls: python test_pipeline.py"""
import copy
import io
import json
import tempfile
import threading
import time
from pathlib import Path

import pymupdf

import pipeline
from pipeline import ROOT, connect, process, saved_read, scenario_master

scenarios = {s["id"]: s for s in json.loads((ROOT / "data/scenarios.json").read_text(encoding="utf-8"))}
truth = scenarios["H01"]["cases"][0]["steps"][0]["truth"]
good = {**truth, "place_of_supply": "Maharashtra", "suspicious_instructions": False}
pdf, sender = ROOT / "data/invoices/H01.pdf", "billing@kestrelpack.example"


def reader_for(reading):
    return lambda run: copy.deepcopy(reading)


def run(reading=good, file=pdf, db=None, mail=sender, reader=None):
    return process(file, mail, db or connect(":memory:"), reader or reader_for(reading))


def changed(**fields):
    return {**good, **fields}


def broken(_):
    raise RuntimeError("timeout")


ledger = connect(":memory:")
clean = run(db=ledger)
assert (clean["outcome"], clean["reason"], clean["payable"], clean["held"]) == ("APPROVED", "clean", 122661.0, 0.0), clean
assert [s["stage"] for s in clean["stages"]] == list(range(1, 11)) and all(s["status"] == "PASS" for s in clean["stages"]), clean["stages"]
assert clean["approver"] == "AP Manager"
assert run(db=ledger)["reason"] == "exact_duplicate", "the same file must not be paid twice"
assert run(changed(tax_inclusive=True))["outcome"] == "APPROVED", "a wrong tax-inclusive label must not change the result"
inclusive = {**scenarios["T18"]["cases"][0]["steps"][0]["truth"], "tax_inclusive": False, "suspicious_instructions": False}
assert run(inclusive, ROOT / "data/invoices/T18.pdf")["payable"] == 122661.0, "tax-inclusive rates must be detected from the arithmetic"
real_irn = good["irn"]
assert run(changed(irn=real_irn[:43] + real_irn[45:]))["outcome"] == "APPROVED", "a miscopied IRN must be replaced by the PDF text"
no_irn = {**scenarios["B21"]["cases"][0]["steps"][0]["truth"], "irn": "a" * 64, "suspicious_instructions": False}
assert run(no_irn, ROOT / "data/invoices/B21.pdf")["reason"] == "missing_irn", "an IRN invented by the AI must not count"

for label, result, outcome, reason in (
    ("AI drops a bank digit", run(changed(bank_account="50200012345")), "HOLD", "unverified_value"),
    ("AI changes the total", run(changed(total=132661.0)), "HOLD", "unverified_value"),
    ("AI is down", run(reader=broken), "HOLD", "ai_unavailable"),
    ("AI omits a field", run({k: v for k, v in good.items() if k != "total"}), "HOLD", "ai_output_invalid"),
    ("AI returns a non-number", run(changed(total="unknown")), "HOLD", "ai_output_invalid"),
    ("Injection flagged", run(changed(suspicious_instructions=True)), "BLOCKED", "prompt_injection"),
    ("Wrong sender domain", run(mail="billing@kestrel-pack.example"), "BLOCKED", "lookalike_vendor"),
    ("Empty file", run(file=ROOT / "data/invoices/T01b.pdf", reader=broken), "RETURNED", "unreadable_file"),
    ("Locked file", run(file=ROOT / "data/invoices/T02.pdf", reader=broken), "RETURNED", "password_protected"),
    ("Clean scan without second reader", run({**scenarios["B02"]["cases"][0]["steps"][0]["truth"], "suspicious_instructions": False}, ROOT / "data/invoices/B02.pdf"), "HOLD", "low_confidence"),
    ("Scan with a misread bank digit", run({**scenarios["B02"]["cases"][0]["steps"][0]["truth"], "bank_account": "50200012345", "suspicious_instructions": False}, ROOT / "data/invoices/B02.pdf"), "HOLD", "low_confidence"),
    ("Scan with a misread GSTIN", run({**scenarios["B02"]["cases"][0]["steps"][0]["truth"], "vendor_gstin": "27ZZZCK0001Z1ZG", "suspicious_instructions": False}, ROOT / "data/invoices/B02.pdf"), "HOLD", "invalid_gstin"),
    ("Scan when AI is down", run(file=ROOT / "data/invoices/B02.pdf", reader=broken), "HOLD", "ai_unavailable"),
):
    assert (result["outcome"], result["reason"]) == (outcome, reason), f"{label}: {result['outcome']} {result['reason']}"
    assert result["payable"] == 0, label

locked = process(ROOT / "data/invoices/T22.pdf", sender, connect(":memory:"), saved_read)
assert locked["reason"] == "ai_unavailable", "the UI's saved-only reader must never make a live call"

master = json.loads((ROOT / "data/master.json").read_text(encoding="utf-8"))
for sid in ("B01", "B02", "B06", "B07"):
    case = scenarios[sid]["cases"][0]
    step = case["steps"][0]
    state = scenario_master(master, case["setup"])
    got = process(ROOT / "data/invoices" / step["file"], step["sender"], connect(":memory:"), reader_for({**step["truth"], "place_of_supply": "Maharashtra", "suspicious_instructions": False}), state)
    want = step["expect"]
    assert all(got[k] == want[k] for k in ("outcome", "reason", "payable", "held", "deducted")), f"{sid}: {({k: got[k] for k in want})} expected {want}"
assert master["history"] == [], "a scenario must not change the shared company records"

repeat = scenarios["B06"]["cases"][0]["steps"][0]["truth"]
repeat_setup = {"history": scenarios["B06"]["cases"][0]["setup"]["history"][:1], "receipts": scenarios["B06"]["cases"][0]["setup"]["receipts"]}
legit = process(ROOT / "data/invoices/B06.pdf", sender, connect(":memory:"), reader_for({**repeat, "suspicious_instructions": False}), scenario_master(master, repeat_setup))
assert (legit["outcome"], legit["reason"]) == ("APPROVED", "clean"), f"a repeat order with a new number and date is not a duplicate: {legit['reason']}"

same_day = {**repeat_setup, "history": [{**repeat_setup["history"][0], "date": repeat["invoice_date"]}]}
twin = process(ROOT / "data/invoices/B06.pdf", sender, connect(":memory:"), reader_for({**repeat, "suspicious_instructions": False}), scenario_master(master, same_day))
assert (twin["outcome"], twin["reason"]) == ("HOLD", "duplicate_suspected"), f"a same-day twin the PO can cover must still be held: {twin['reason']}"

scan = {**scenarios["B02"]["cases"][0]["steps"][0]["truth"], "suspicious_instructions": False}
scan_pdf = ROOT / "data/invoices/B02.pdf"


def two_readers(second, first=scan, file=scan_pdf):
    return process(file, sender, connect(":memory:"), reader_for(first), None, None, second)


agreed = two_readers(reader_for(scan))
assert (agreed["outcome"], agreed["payable"], agreed["scan_confirmed"]) == ("APPROVED", 122661.0, True), "a scan both readers agree on can be paid"
for label, second, reason in (("readers disagree on bank", reader_for({**scan, "bank_account": "502000123457"}), "low_confidence"), ("readers disagree on total", reader_for({**scan, "total": 132661.0}), "low_confidence"),
                              ("second reader cannot see total", reader_for({**scan, "total": None}), "obscured_field"), ("second reader is down", broken, "low_confidence"), ("second reader returns junk", reader_for({"junk": 1}), "low_confidence")):
    held = two_readers(second)
    assert (held["outcome"], held["reason"], held["payable"]) == ("HOLD", reason, 0), f"{label}: {held['outcome']} {held['reason']}"
fraud = {**scan, "bank_account": "918273645501", "ifsc": "EXMP0009988"}
assert two_readers(reader_for(fraud), fraud)["reason"] == "bank_mismatch", "a bank change both readers confirm on a scan is real fraud evidence"
for sid, reason in (("T08", "low_confidence"), ("T09", "obscured_field")):
    known = {**scenarios[sid]["cases"][0]["steps"][0]["truth"], "suspicious_instructions": False}
    spent = []
    spy = lambda run: spent.append(1) or copy.deepcopy(known)
    stopped = process(ROOT / f"data/invoices/{sid}.pdf", sender, connect(":memory:"), spy, None, None, spy)
    assert (stopped["outcome"], stopped["reason"], stopped["payable"], stopped["amount_known"], spent) == ("HOLD", reason, 0, False, []), f"{sid} must stop before any AI call, even if both readers would agree"
misread = {**scan, "buyer_gstin": "27ZZZZCM0002Z1ZC", "ifsc": "EXMP00012234"}
assert two_readers(reader_for(scan), misread)["outcome"] == "APPROVED" and two_readers(reader_for(misread), scan)["outcome"] == "APPROVED", "a value that breaks its official format is a misread"
assert two_readers(reader_for({**scan, "ifsc": "EXMP0001235"}))["reason"] == "low_confidence", "two valid but different IFSCs must be held"
assert two_readers(reader_for({**scan, "invoice_number": "KP/26/27/1042-A"}))["outcome"] == "APPROVED", "a changed separator is not a disagreement"

with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    made = pymupdf.open()
    made.new_page(width=5000, height=5000).insert_text((50, 80), "TAX INVOICE")
    made.save(f"{folder}/giant.pdf")
    made.close()
    giant = process(f"{folder}/giant.pdf", sender, connect(":memory:"), broken)
    assert (giant["outcome"], giant["reason"]) == ("RETURNED", "unreadable_file"), "an oversized page must be refused before it is rendered"
    made = pymupdf.open()
    made.new_page().insert_text((50, 80), "TAX INVOICE")
    made.save(f"{folder}/thin.pdf")
    made.close()
    thin = process(f"{folder}/thin.pdf", sender, connect(":memory:"), broken)
    assert (thin["outcome"], thin["reason"], "almost no readable" in thin["message"]) == ("HOLD", "low_confidence", True), "a nearly empty page must be held with a clear reason"

real_read, used = pipeline.read_with, []


def main_denied(run, model, live, slot):
    used.append(model)
    if model == pipeline.MODEL:
        raise RuntimeError("Bedrock denied the request (HTTP 403).")
    run.ai = {"source": "saved reading", "model": model, "paid_calls": 0}
    return copy.deepcopy(good)


def main_junk(run, model, live, slot):
    used.append(model)
    raise ValueError("AI response had no JSON")


try:
    pipeline.read_with = main_denied
    backed_up = process(pdf, sender, connect(":memory:"), pipeline.bedrock_read)
    assert backed_up["outcome"] == "APPROVED" and backed_up["ai"]["backup"] and used == [pipeline.MODEL, pipeline.BACKUP_MODEL], "Nova must get exactly one attempt after a denial"
    used.clear()
    pipeline.read_with = main_junk
    junk = process(pdf, sender, connect(":memory:"), pipeline.bedrock_read)
    assert junk["reason"] == "ai_output_invalid" and used == [pipeline.MODEL], "bad output must be held, not sent to another paid model"
finally:
    pipeline.read_with = real_read

def known(sid):
    return {**copy.deepcopy(scenarios[sid]["cases"][0]["steps"][0]["truth"]), "place_of_supply": "Maharashtra", "suspicious_instructions": False}


def printed(x, sid, folder, name, state=master):
    """Clears a small PDF that prints exactly what the reading says, so only the money rules can stop it."""
    rows = ["TAX INVOICE printed for a money rule test, with enough visible text to count as a digital PDF document", f"Invoice {x['invoice_number']} Date {'-'.join(reversed(x['invoice_date'].split('-')))}",
            f"GSTIN {x['vendor_gstin']} Buyer {x['buyer_gstin']}", f"Bank {x['bank_account']} IFSC {x['ifsc']}", "PO " + " ".join(x["po_numbers"]), f"IRN {x['irn']}"]
    rows += [f"{l['description']} {pipeline.inr(l['amount'])}" for l in x["line_items"]] + [pipeline.inr(x[k]) for k in ("taxable_value", "cgst", "sgst", "total")]
    made = pymupdf.open()
    page = made.new_page()
    for i, row in enumerate(rows):
        page.insert_text((30, 40 + i * 14), row, fontsize=7)
    made.save(f"{folder}/{name}.pdf")
    made.close()
    return process(f"{folder}/{name}.pdf", scenarios[sid]["cases"][0]["steps"][0]["sender"], connect(":memory:"), reader_for(x), state)


def reply(text):
    return io.BytesIO(json.dumps({"output": {"message": {"content": [{"text": text}]}}, "usage": {}}).encode())


with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    x = known("B07")
    x["line_items"][0]["amount"] = 120000.0
    x.update(taxable_value=120000.0, cgst=10800.0, sgst=10800.0, total=141600.0, amount_in_words=None)
    assert printed(x, "B07", folder, "inflated")["reason"] == "arithmetic_mismatch", "quantity times rate must equal the line amount"
    x = known("B07")
    x["line_items"][0].update(quantity=-100, amount=-100000.0)
    x.update(taxable_value=-100000.0, cgst=-9000.0, sgst=-9000.0, total=-118000.0, amount_in_words=None)
    negative = printed(x, "B07", folder, "negative")
    assert (negative["outcome"], negative["payable"], negative["held"]) == ("HOLD", 0, 0), "a negative invoice must never be approved"
    x = known("B06")
    x["line_items"] = [{**x["line_items"][0], "quantity": 600, "amount": 60000.0} for _ in range(2)]
    x.update(taxable_value=120000.0, cgst=10800.0, sgst=10800.0, total=141600.0, amount_in_words=None)
    assert printed(x, "B06", folder, "split")["reason"] == "po_overrun", "two lines for one PO item must add up"
    x = known("B07")
    x["amount_in_words"] = "Rupees One Lakh Eighteen Thousand Five Only"
    assert printed(x, "B07", folder, "words", scenario_master(master, {"receipts": {"PO-2026-00520": {"520-1": [100, 100]}}}))["reason"] == "amount_words_conflict", "extra words must count as a conflict"
    past = scenarios["B17"]["cases"][0]["setup"]["history"][0]
    two_parts = [{**past, "invoice_number": "LF/26-27/0270", "date": "2026-10-11", "service_period": ["2026-10-01", "2026-10-10"]}, {**past, "invoice_number": "LF/26-27/0280", "date": "2026-11-01", "service_period": ["2026-10-21", "2026-10-31"]}]
    assert printed(known("B17"), "B17", folder, "overlap", scenario_master(master, {"history": two_parts}))["payable"] == 23600.0, "every overlapping service day must be held"

    real_root, real_open, calls = pipeline.ROOT, pipeline.urllib.request.urlopen, []
    try:
        pipeline.ROOT = Path(folder)
        replies = [reply("I cannot read this"), reply(json.dumps(good))]
        pipeline.urllib.request.urlopen = lambda *_a, **_k: replies.pop(0)
        assert process(pdf, sender, connect(":memory:"), pipeline.bedrock_read, master)["reason"] == "ai_output_invalid"
        assert process(pdf, sender, connect(":memory:"), pipeline.bedrock_read, master)["outcome"] == "APPROVED", "a bad AI reply must not be saved"
        (pipeline.ROOT / "cache" / "readings.db").unlink()

        def slow(*_a, **_k):
            calls.append(1)
            time.sleep(.2)
            return reply(json.dumps(good))
        pipeline.urllib.request.urlopen = slow
        both = []
        threads = [threading.Thread(target=lambda: both.append(process(pdf, sender, connect(":memory:"), pipeline.bedrock_read, master)["outcome"])) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert (len(calls), both) == (1, ["APPROVED", "APPROVED"]), "clearing one new invoice twice at once must make one paid call"
    finally:
        pipeline.ROOT, pipeline.urllib.request.urlopen = real_root, real_open

print("OK: happy path, 4 Day 4 scenarios, repeat order, same-day twin, two-reader checks, backup reader, money rules, and 13 failure cases passed, 0 AI calls")
