"""A local copy of the practice bank: the seven tools, one customer, one ledger. No network.

Built from what the real mock returned in our recorded calls and from its tool API file. Like the real one it does not
enforce the brief: it records a promise on a holiday or below the minimum without complaint.
The ledger has the shape the kit's scorer (tools/score.py) reads.
"""
import datetime, re, urllib.parse


class Bank:
    def __init__(self, case, api, customer_id):
        self.case, self.customer_id = case, customer_id
        self.schemas = {p.strip("/").split("/")[-1]: (v.get("post") or {}).get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
                        for p, v in (api.get("paths") or {}).items()}
        self.identity = dict(case.get("identity") or {})
        self.products = [{"product_id": f"p{i + 1}", **p} for i, p in enumerate(case.get("products") or [])]
        self.faults = {f["tool"]: int(f.get("fail_first_n", 0)) for f in case.get("faults") or []}
        # optional per-case settings for briefs whose identity check counts differently (defaults = the kit's bank):
        # "bank": {"max_wrong": 3, "pass_at": <all fields>, "swap_hint": "day_month_swapped" | null, "hints": {field: {"pattern": regex, "hint": name}}}
        cfg = case.get("bank") or {}
        self.max_wrong, self.pass_at = int(cfg.get("max_wrong", 3)), int(cfg.get("pass_at") or len(self.identity))
        self.swap_hint, self.hints = cfg.get("swap_hint", "day_month_swapped"), cfg.get("hints") or {}
        self.verified, self.wrong, self.hinted, self.closed = {}, 0, False, False
        self.promises, self.notes, self.callbacks, self.transfers, self.calls = [], [], [], [], []
        self.outcome = self.reason = None
        self.failures = {}

    # ---- transport: what an api_call of the workflow reaches
    def http(self, url, method="POST", body=None):
        tool = urllib.parse.urlparse(str(url)).path.rstrip("/").split("/")[-1]
        body = dict(body) if isinstance(body, dict) else {}
        if tool not in self.schemas: return 404, {"ok": False, "error": f"unknown tool {tool}"}
        if body.get("customer_id") != self.customer_id: return 404, {"ok": False, "error": "unknown customer_id"}
        args = {k: v for k, v in body.items() if k != "customer_id"}
        status, result = self._run(tool, args)
        self.calls.append({"n": len(self.calls) + 1, "tool": tool, "args": args, "status": status, "result": result})
        return status, result

    def _run(self, tool, a):
        if self.closed: return 200, {"ok": False, "error": "the call is closed, nothing is accepted after close_call"}
        if self.faults.get(tool, 0) > 0:
            self.faults[tool] -= 1; self.failures[tool] = self.failures.get(tool, 0) + 1
            return 500, {"ok": False, "error": "service unavailable, nothing was recorded"}
        sch = self.schemas[tool]
        for f in sch.get("required", []):
            if f != "customer_id" and a.get(f) in (None, ""): return 400, {"ok": False, "error": f"missing field {f}"}
        for f, spec in (sch.get("properties") or {}).items():
            if f in a and spec.get("enum") and a[f] not in spec["enum"]: return 400, {"ok": False, "error": f"{f} must be one of {spec['enum']}"}
            if f in a and spec.get("type") in ("number", "integer"):
                try: a[f] = float(a[f]) if spec["type"] == "number" else int(float(a[f]))
                except Exception: return 400, {"ok": False, "error": f"{f} must be a number"}
                if isinstance(a[f], float) and a[f].is_integer(): a[f] = int(a[f])
            if f in a and spec.get("format") == "date":
                try: datetime.date.fromisoformat(str(a[f]))
                except Exception: return 400, {"ok": False, "error": f"{f} must be a date as YYYY-MM-DD"}
        return getattr(self, "_" + tool)(a)

    def _all_verified(self): return bool(self.identity) and sum(bool(self.verified.get(k)) for k in self.identity) >= self.pass_at

    def _verify_identity(self, a):
        field, answer = a["field"], str(a["answer"]).strip()
        if field not in self.identity: return 400, {"ok": False, "error": f"unknown field {field}"}
        if self.wrong >= self.max_wrong: return 200, {"ok": False, "error": "the identity check has ended after too many wrong answers"}
        want = str(self.identity[field]).strip()
        h = self.hints.get(field)
        if h and re.fullmatch(h["pattern"], answer):
            self.hinted = True
            return 200, {"ok": True, "verified": False, "wrong_answers_left": self.max_wrong - self.wrong, "all_verified": self._all_verified(), "hint": h["hint"]}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", want):                      # a date field
            try: got = datetime.date.fromisoformat(answer)
            except Exception: return 200, {"ok": False, "error": "unreadable date, send it as YYYY-MM-DD"}
            exp = datetime.date.fromisoformat(want)
            if self.swap_hint and got != exp and (got.year, got.month, got.day) == (exp.year, exp.day, exp.month):
                self.hinted = True
                return 200, {"ok": True, "verified": False, "wrong_answers_left": self.max_wrong - self.wrong, "all_verified": self._all_verified(), "hint": self.swap_hint}
            right = got == exp
        else:
            norm = lambda s: re.sub(r"[\s-]", "", s).upper()
            right = norm(answer) == norm(want)
        if right: self.verified[field] = True
        else: self.wrong += 1
        return 200, {"ok": True, "verified": right, "wrong_answers_left": self.max_wrong - self.wrong, "all_verified": self._all_verified()}

    def _get_account(self, a):
        if not self._all_verified(): return 200, {"ok": False, "error": "identity not verified"}
        return 200, {"ok": True, "products": self.products}

    def _create_promise(self, a):
        if not self._all_verified(): return 200, {"ok": False, "error": "identity not verified"}
        prod = next((p for p in self.products if p["product_id"] == a["product_id"]), None)
        if not prod: return 200, {"ok": False, "error": "unknown product_id"}
        if any(p["product_id"] == a["product_id"] for p in self.promises): return 200, {"ok": False, "error": "this product already has a promise"}
        pid = f"pr{len(self.promises) + 1}"
        self.promises.append({"promise_id": pid, "product_id": a["product_id"], "type": prod.get("type"), "amount": a["amount"], "date": str(a["date"]),
                              **{k: v for k, v in a.items() if k not in ("product_id", "amount", "date")}})
        return 200, {"ok": True, "promise_id": pid}

    def _add_note(self, a):
        self.notes.append({k: a[k] for k in a}); return 200, {"ok": True}

    def _schedule_callback(self, a):
        self.callbacks.append({**a, "date": str(a["date"]), "time": str(a["time"])}); return 200, {"ok": True}

    def _transfer_to_agent(self, a):
        self.transfers.append({k: a[k] for k in a}); return 200, {"ok": True}

    def _close_call(self, a):
        self.outcome, self.reason, self.closed = a["outcome"], a.get("reason") or None, True
        return 200, {"ok": True}

    def ledger(self):
        return {"ok": True, "case": self.case.get("id"), "now": (self.case.get("variables") or {}).get("now"), "verified": self.verified, "wrong": self.wrong, "hinted": self.hinted,
                "promises": self.promises, "notes": self.notes, "callbacks": self.callbacks, "transfers": self.transfers,
                "outcome": self.outcome, "reason": self.reason, "failures": self.failures, "calls": self.calls}
