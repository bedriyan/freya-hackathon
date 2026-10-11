"""Cross-check the Worker against our Python bank (local-simulation/sim/bank.py).

    ../.venv/bin/python test/compare.py [--api openapi.json] [--cases cases.json] [--limit 6] [--data other-data.js]

Starts `node test/serve.mjs` (the Worker with fake Durable Objects) on a free localhost port, plays the same call sequences for
every customer against both, and prints every difference in status code, JSON body and final ledger. Exit code 1 on any difference.
src/data.js must have been written from the same --api/--cases (the defaults are the example bank the repo ships with).
"""
import argparse, json, pathlib, subprocess, sys, urllib.request, urllib.error

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "local-simulation" / "sim"))
from bank import Bank

NODE = "/opt/homebrew/bin/node"


def sequences(case):
    ident = list((case.get("identity") or {}).items())
    right = [("verify_identity", {"field": f, "answer": v}) for f, v in ident]
    f0, v0 = ident[0]; f1, v1 = ident[-1]
    amount = (case["products"][0].get("overdue_amount") or 100)
    promise = {"product_id": "p1", "amount": amount, "date": "2026-12-22", "part_payment": False}
    yield "happy path", right + [("get_account", {}), ("create_promise", promise), ("create_promise", promise), ("create_promise", {**promise, "product_id": "p9"}),
        ("create_promise", {**promise, "product_id": "p2"}), ("add_note", {"product_id": "p1", "reason": "declined", "text": "x"}),
        ("add_note", {"product_id": "p7", "reason": "payment_claimed", "payment_date": "2026-12-01"}),
        ("schedule_callback", {"date": "2026-12-23", "time": "10:30", "arranged_with": "named_contact"}), ("transfer_to_agent", {"queue": "sme_servicing"}),
        ("close_call", {"outcome": "commitment", "identity_verified": True}), ("get_account", {}), ("close_call", {"outcome": "commitment", "identity_verified": True})]
    yield "wrong answers to the limit", [("verify_identity", {"field": f1, "answer": "1900"})] * 4 + right + [("get_account", {}), ("create_promise", promise),
        ("close_call", {"outcome": "identity_failed", "reason": "wrong_answers", "identity_verified": False})]
    yield "one right, then wrong to the limit", right[:1] + [("verify_identity", {"field": f0, "answer": "ZZ999999"})] * 3 + right[1:] + [("get_account", {})]
    yield "hints and normalisation", [("verify_identity", {"field": f0, "answer": v0[:5]}), ("verify_identity", {"field": f0, "answer": " " + v0[:3] + " " + v0[3:6]}),
        ("verify_identity", {"field": f0, "answer": ""}), ("verify_identity", {"field": f0, "answer": " "}), ("verify_identity", {"field": f0, "answer": v0 + "9"}),
        ("verify_identity", {"field": f0, "answer": " " + v0[:2].lower() + " " + v0[2:5] + "-" + v0[5:] + " "}), ("verify_identity", {"field": f1, "answer": int(v1) if v1.isdigit() else v1}),
        ("verify_identity", {"field": f1, "answer": v1[:2]}), ("verify_identity", {"field": f1, "answer": v1}), ("get_account", {})]
    for f, v in ident:
        if len(v) == 10 and v[4] == "-" and v[7] == "-":      # a date question: day/month exchanged, unreadable, wrong, right
            yield "date answers", [("verify_identity", {"field": f, "answer": f"{v[:4]}-{v[8:]}-{v[5:7]}"}), ("verify_identity", {"field": f, "answer": "4 March 1990"}),
                ("verify_identity", {"field": f, "answer": v.replace("-", "/")}), ("verify_identity", {"field": f, "answer": "1901-01-01"}), ("verify_identity", {"field": f, "answer": " " + v + " "})]
    yield "before the identity check", [("get_account", {}), ("create_promise", promise), ("add_note", {"product_id": "p1", "reason": "site_visit"}),
        ("schedule_callback", {"date": "2026-12-23", "time": 930, "arranged_with": "third_party"}), ("transfer_to_agent", {"queue": "fraud_ops"}),
        ("close_call", {"outcome": "no_contact", "reason": "", "identity_verified": False})]
    yield "validation", right + [("transfer_to_agent", {"queue": "the_moon"}), ("transfer_to_agent", {}), ("transfer_to_agent", {"queue": None}), ("transfer_to_agent", {"queue": ""}),
        ("verify_identity", {"field": "shoe_size", "answer": "42"}), ("verify_identity", {"field": f0}), ("verify_identity", {"answer": "x"}),
        ("create_promise", {**promise, "amount": "lots"}), ("create_promise", {**promise, "amount": None}), ("create_promise", {**promise, "date": "22/12/2026"}),
        ("create_promise", {**promise, "date": "2026-02-30"}), ("create_promise", {k: v for k, v in promise.items() if k != "part_payment"}),
        ("add_note", {"product_id": "p1", "reason": "declined", "payment_date": "soon"}), ("add_note", {"product_id": "p1", "reason": "declined", "payment_date": None}),
        ("add_note", {"product_id": "p1", "reason": "nope"}), ("close_call", {"outcome": "commitment"}), ("close_call", {"outcome": "won", "identity_verified": True}),
        ("close_call", {"outcome": "handed_over", "reason": "because", "identity_verified": True}), ("schedule_callback", {"date": "2026-12-23", "time": "", "arranged_with": "named_contact"}),
        ("create_promise", {**promise, "amount": "1840.50", "extra": {"a": 1}}), ("close_call", {"outcome": "handed_over", "reason": "disputed", "identity_verified": "yes"})]
    yield "numbers", right + [("create_promise", {**promise, "amount": 1840.0}), ("add_note", {"product_id": 1, "reason": "disputed", "text": 5}),
        ("close_call", {"outcome": "no_commitment", "reason": "declined", "identity_verified": True})]
    # bank.py (Python 3.11+ date.fromisoformat) accepts a date without hyphens and stores it as sent; the Worker keeps to YYYY-MM-DD.
    yield "KNOWN: compact date", right + [("create_promise", {**promise, "date": "20261222"})]
    yield "unknown tool and customer", [("open_vault", {}), ("get_account", {"customer_id": "nobody"}), ("verify_identity", {"customer_id": None, "field": f0, "answer": v0})]


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.load(r)
    except urllib.error.HTTPError as e: return e.code, json.load(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=str(ROOT / "local-simulation/blind/s02/openapi.json")); ap.add_argument("--cases", default=str(ROOT / "local-simulation/blind/s02/cases.json"))
    ap.add_argument("--limit", type=int, default=6); ap.add_argument("--data", help="data.js to serve instead of src/data.js")
    a = ap.parse_args()
    api, cases = json.load(open(a.api)), json.load(open(a.cases))[:a.limit]
    proc = subprocess.Popen([NODE, str(HERE / "serve.mjs"), "0"] + ([str(pathlib.Path(a.data).resolve())] if a.data else []), stdout=subprocess.PIPE, text=True)
    diffs = known = calls = seqs = 0
    try:
        line = proc.stdout.readline()
        while line and not line.startswith("LISTENING"): line = proc.stdout.readline()
        base = f"http://127.0.0.1:{line.split()[1]}"
        for case in cases:
            cid = case["id"]
            for name, seq in sequences(case):
                seqs += 1
                bank = Bank(case, api, cid); post(base, f"/v1/_reset/{cid}", {})
                for i, (tool, args) in enumerate(seq):
                    calls += 1
                    body = {"customer_id": cid, **args}
                    try: py = bank.http(f"https://x/v1/{tool}", "POST", json.loads(json.dumps(body)))
                    except Exception as e: py = ("EXCEPTION", repr(e))
                    js = post(base, f"/v1/{tool}", body)
                    if tuple(py) != tuple(js) and name.startswith("KNOWN"): known += 1; print(f"known difference {cid} [{name}] {tool}: bank.py {py[0]} {json.dumps(py[1])} / worker {js[0]} {json.dumps(js[1])}")
                    elif tuple(py) != tuple(js):
                        diffs += 1; print(f"DIFF {cid} [{name}] call {i + 1} {tool} {json.dumps(args)}\n   bank.py: {py[0]} {json.dumps(py[1])}\n   worker : {js[0]} {json.dumps(js[1])}")
                want = json.loads(json.dumps(bank.ledger()))
                with urllib.request.urlopen(f"{base}/v1/_ledger/{cid}") as r: got = json.load(r)
                got.pop("_state", None)
                for k in sorted(set(want) | set(got)) if not name.startswith("KNOWN") else []:
                    if want.get(k, "<absent>") != got.get(k, "<absent>"):
                        diffs += 1; print(f"DIFF {cid} [{name}] ledger.{k}\n   bank.py: {json.dumps(want.get(k, '<absent>'))[:600]}\n   worker : {json.dumps(got.get(k, '<absent>'))[:600]}")
    finally:
        proc.terminate()
    print(f"\n{len(cases)} customers, {seqs} sequences, {calls} calls compared, {diffs} difference(s), {known} known/intended")
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
