"""Runs every test scenario.

python tests/test_scenarios.py         offline with known-correct readings, no AI calls
python tests/test_scenarios.py --real  with the configured AI models, saved readings are reused and it stops at the first access error
"""
import copy
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # The app modules live in the repo root.
import pipeline
from pipeline import ROOT, connect, process_file, record_receipts, scenario_master

# These scans stop before any AI call, so their amount is deliberately unknown.
UNREADABLE = {"T08.pdf", "T09.pdf"}
EXTRA = ("supersedes", "combined_total", "route_to", "suggested_po")
SIMULATED_FAULTS = ("ai_timeout", "ai_malformed", "ai_invented_value")
scenarios = json.loads((ROOT / "data/scenarios.json").read_text(encoding="utf-8"))
master = json.loads((ROOT / "data/master.json").read_text(encoding="utf-8"))
access_errors = []


def live(model, slot):
    """Calls one model with no backup, so a blocked key cannot quietly spend calls on another model."""
    def read(run):
        try:
            return pipeline.read_with(run, model, True, slot)
        except (RuntimeError, OSError) as exc:
            access_errors.append(f"{model}: {exc}")
            raise
    return read


def reading(truth, fault=None):
    clean = {**copy.deepcopy(truth), "place_of_supply": "Maharashtra", "suspicious_instructions": False}
    if fault == "ai_invented_value":
        clean["bank_account"] = "502000999999"
    return {"broken": True} if fault == "ai_malformed" else clean


def reader(truths, fault):
    pieces = iter(truths)

    def read(_run):
        if fault == "ai_timeout":
            raise TimeoutError("timed out")
        return reading(next(pieces), fault)
    return read


def compare(got, want, file):
    if file in UNREADABLE:
        keys = ("outcome", "reason", "payable")
        unknown = [] if got.get("amount_known") is False else ["amount should be marked unknown"]
    else:
        keys, unknown = ("outcome", "reason", "payable", "held", "deducted") + tuple(k for k in EXTRA if k in want), []
    return unknown + [f"{k}: got {got.get(k)}, expected {want[k]}" for k in keys if got.get(k) != want[k]]


def run_step(step, state, ledger, real=False):
    truths = step["truth"] if isinstance(step["truth"], list) else [step["truth"]]
    path, fault = ROOT / "data/invoices" / step["file"], step.get("fault")
    records = {"unavailable": True} if fault == "data_unavailable" else state
    first = lambda f: live(pipeline.MODEL, "ai") if real and f not in SIMULATED_FAULTS else reader(truths, f)
    # The second reader agrees even on T09, because real AI models filled in the total hidden by its stamp.
    second = live(pipeline.SECOND_MODEL, "second_ai") if real else lambda _run: reading(truths[0])
    if fault == "crash_after_stage_6":
        def crash(event):
            if event["stage"] == 6:
                raise KeyboardInterrupt("simulated crash")
        try:
            process_file(path, step["sender"], ledger, first(None), records, crash, second)
        except KeyboardInterrupt:
            pass
        fault = None
    return process_file(path, step["sender"], ledger, first(fault), records, None, second)


def run_concurrent(case, state, file, real=False):
    results, errors = {}, []

    def work(index, step):
        db = connect(file)
        try:
            results[index] = run_step(step, state, db, real)[0]
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            db.close()
    threads = [threading.Thread(target=work, args=(i, s)) for i, s in enumerate(case["steps"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        return errors
    outcomes = sorted(r["outcome"] for r in results.values())
    return [] if outcomes == ["APPROVED", "HOLD"] and all(r["outcome"] == "APPROVED" or r["reason"] == "po_overrun" for r in results.values()) else [f"outcomes {outcomes}"]


def run_case(case, real=False):
    state = scenario_master(master, case["setup"])
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        file = Path(folder) / "ledger.db"
        connect(file).close()
        if case["concurrent"]:
            return run_concurrent(case, state, file, real)
        ledger, problems = connect(file), []
        try:
            for step in case["steps"]:
                record_receipts(state, step.get("receipts", {}))
                got = run_step(step, state, ledger, real)
                wants = step["expect"] if isinstance(step["expect"], list) else [step["expect"]]
                if len(got) != len(wants):
                    problems.append(f"{len(got)} results, expected {len(wants)}")
                problems += [p for g, w in zip(got, wants) for p in compare(g, w, step["file"])]
                if isinstance(step["expect"], dict) and "ledger_entries" in step["expect"]:
                    entries = ledger.execute("select count(*) from runs").fetchone()[0]
                    if entries != step["expect"]["ledger_entries"]:
                        problems.append(f"ledger has {entries} entries")
                for g in got:
                    if g["outcome"] != "RETURNED" and abs(g["payable"] + g["held"] + g["deducted"] - g["total"]) > 0.01:
                        problems.append(f"money does not reconcile: {g['payable']} + {g['held']} + {g['deducted']} != {g['total']}")
        finally:
            ledger.close()
        return problems


def main(real=False):
    passed, failed, report = 0, [], {}
    for scenario in scenarios:
        for case in scenario["cases"]:
            label = scenario["id"] + (f" ({case['name']})" if case["name"] else "")
            try:
                problems = run_case(case, real)
            except Exception as exc:
                problems = [f"crashed: {type(exc).__name__}: {exc}"]
            if access_errors:
                print(f"Stopped at {label} with no retry: {access_errors[0]}")
                return 2
            report[label] = problems
            if problems:
                failed.append(f"{label} {scenario['title']}: " + "; ".join(problems))
            else:
                passed += 1
            if real:
                print(f"{'ok  ' if not problems else 'DIFF'} {label}", flush=True)
    print(f"{passed} of {passed + len(failed)} test cases passed, {len(failed)} failed" + (f", read by {pipeline.MODEL}" if real else ", 0 AI calls"))
    for line in failed:
        print("  " + line)
    if real:
        (ROOT / "output").mkdir(exist_ok=True)
        (ROOT / "output" / f"real_ai_{pipeline.MODEL.replace(':', '_')}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main("--real" in sys.argv))
