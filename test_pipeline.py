"""Offline check of the happy path and its safety rails. Makes no AI calls: python test_pipeline.py"""
import copy
import json

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

print("OK: happy path, 4 Day 4 scenarios, repeat order, same-day twin, two-reader checks, and 13 failure cases passed, 0 AI calls")
