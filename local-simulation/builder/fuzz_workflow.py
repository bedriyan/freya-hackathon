"""Model-free tests of a workflow: no model is called, nothing is sent to the platform, a run takes seconds.

    python3 fuzz_workflow.py <workflow.json> <tool_api.json> [--cases <cases.json>] [--recordings <folder of a local test round>]
                             [--runs 600] [--seed 1] [--md report.md] [--json report.json]

Without --cases the customers are made up from the tool API file (identity fields, one to three products, several call days, each
tool failing once), which is enough for everything except the replayed calls.

The workflow runs in the local engine (engine.py) against the local bank (bank.py). The three model roles are replaced:

1. Random calls. The extraction returns random members of each enum and values from a small pool, the routing model answers at
   random, the dialogue model says a placeholder line. Then, from every step a call was seen waiting in, the call goes on with
   a customer who is stuck in one way: gives the same answer every time (repeat), gives nothing that can be extracted (silent),
   gets the neutral member of every enum (none), is cut off every time (broken), only says goodbye (bye).
2. Replayed calls (--recordings). A local test round stores, for every call, what the customer said and what was extracted
   from it. Those turns are fed back in, without a model.
     replay    the same turns again. The bank calls must be the same as in the recording; a call for which they are not
               (the dialogue model held a tool, or the workflow changed) is left out of the next two.
     mutate    a recorded call that passed, with one disturbance put in front of one of its turns: a message that is cut off,
               a message from which nothing is extracted, a message whose extracted values are garbage (the platform's
               extraction does that with dates and amounts), or a side question in the same message as the answer. The bank
               records must still be the expected ones.
     deep      the recorded call up to each turn, then a stuck customer as in 1. This reaches the late steps.

The checks hold for every brief (the brief is not read here, so whether a date or an amount is the right one is not checked):

    NO_END      the call is still open after MAX_TURNS customer turns
    NO_CLOSE    the call ended, or was cut, without the closing call as the last bank call
    REPEAT      the agent said the same fixed sentence REPEAT_LIMIT times in a row
    STUCK       the engine could not go on (no transition matched in a passthrough step, a step loop)
    CODE_ERROR  a code step or hook raised an exception
    ENUM_WRITE  code wrote a value outside an extraction variable's enum (the platform rejects it, the old value stays)
    NO_URL      a call was made with an empty url (a hook tried to stop it)
    BAD_CALL    the bank refused a call: a missing field, a value outside its enum, account data or a record asked for before
                the identity check has passed, a second promise for one product, a check after the bank ended it, a call after the closing call
    BAD_PROMISE a promise with an amount of zero or less, above anything the product owes, or dated before the day of the call
    HONESTY     the agent said something was recorded, booked, scheduled ... when no record had been made, or the last one had failed
    GOODBYE     the agent said goodbye and the call stayed open (the customer is silent after a goodbye: the call dies on a timeout)
    TEMPLATE    a spoken sentence with an unfilled {{variable}}, 'None' or 'null' in it
    SILENT_TURN the agent said nothing after a customer message
    BYE_IGNORED the customer said goodbye three times and the call is still open (the agent keeps asking)
    MUTATION    a recorded call that passed gives other bank records when one disturbance is put into it
    REGRESSION  a recorded call that passed no longer gives its bank records (after a repair; the call may also just have changed its order of questions)

Exit code 1 when a run has a defect. The same seed gives the same runs.
"""
import argparse, asyncio, collections, copy, datetime, json, pathlib, random, re, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "sim"), str(HERE.parent.parent / "kit" / "build-the-builder" / "tools")]
from engine import Engine      # noqa: E402
from bank import Bank          # noqa: E402
try: from score import score_ledger      # the kit's scorer of the bank records
except Exception: score_ledger = None

MAX_TURNS, REPEAT_LIMIT, SNAPS_PER_STEP = 40, 4, 4
NEUTRAL = ("none", "other", "unclear", "no", "unknown")
STUCK = ("repeat", "silent", "none", "broken", "bye")
POOL = ["2", "28", "the 2nd of January", "January", "December", "next Friday", "tomorrow", "today", "81", "1984", "19", "6602", "FA", "66", "52", "120", "25", "0", "450.50",
        "ten thirty", "in twenty minutes", "morning", "p1", "p2", "p3", "all", "yes", "no", "the", "2026-13-45", "-5", "abc"]
TEXTS = {"bye": "Goodbye.", "broken": "Yes, but the", "silent": "Mm.", "none": "Okay.", "repeat": "The 2nd of January."}
BYE = re.compile(r"bye|hang|end_call|ends?_|leav|stop", re.I)
CLAIM = re.compile(r"\b(i have|i've|it is|that is|that's|has been|have been|is now|are now)\b[^.?!]{0,40}\b(recorded|booked|scheduled|saved|noted|registered|logged|set up|arranged|transferred)\b", re.I)
TEMPLATE = re.compile(r"\{\{|\}\}|\bNone\b|\bnull\b|\bundefined\b")
READS = ("verify", "get_", "lookup", "search")           # tools that write nothing: a claim of a record needs one of the others
REFUSED = ("identity not verified", "already has a promise", "unknown product_id", "identity check has ended", "the call is closed", "unknown field")


def own_values(case):
    """What this customer could truthfully say: the security answers (whole and in two parts), the amounts, days and months."""
    out = []
    for v in (case.get("identity") or {}).values():
        v = str(v); out += [v, v.replace(" ", ""), v[: len(v) // 2].strip(), v[len(v) // 2:].strip()]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v): out += [v[:4], v[5:7], v[8:], str(int(v[8:])), str(int(v[5:7]))]
    for p in case.get("products") or []:
        out += [str(x) for x in p.values() if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return out + [str(d) for d in (1, 2, 5, 14, 15, 23, 28, 29, 30, 31)] + ["Monday", "Friday", "Saturday", "Sunday", "October", "November", "December", "January"]


def synthetic(api, wf, n=12):
    """Customers made from the tool API file alone, for a run before any test customer exists: the identity fields the bank asks for,
    one to three products with the fields the account call returns, a few call days. Their values mean nothing; the checks do not need them to."""
    rng, paths = random.Random(11), api.get("paths") or {}
    def schema(tool, where):
        op = next((v.get("post") or {} for k, v in paths.items() if k.rstrip("/").endswith(tool)), {})
        node = op.get("requestBody", {}) if where == "request" else (op.get("responses", {}).get("200") or {})
        return (node.get("content", {}).get("application/json", {}).get("schema") or {}).get("properties") or {}
    fields = (schema("verify_identity", "request").get("field") or {}).get("enum") or ["answer"]
    item = ((schema("get_account", "response").get("products") or {}).get("items") or {}).get("properties") or {"overdue_amount": {"type": "number"}}
    base = dict((wf.get("workflow", wf)).get("test_variables") or {})
    try: day0 = datetime.datetime.fromisoformat(str(base.get("now"))[:16])
    except Exception: day0 = datetime.datetime(2026, 11, 16, 10, 0)
    hint = str((schema("verify_identity", "request").get("answer") or {}).get("description") or "")
    def answer(f):
        # the tool API file usually shows the shape: "For company_number: eight characters, like 08412277."
        m = re.search(re.escape(f) + r"[^.]*?\b(?:like|for example|e\.g\.|such as)\s+([A-Za-z0-9][A-Za-z0-9 /-]*[A-Za-z0-9])", hint) or re.search(re.escape(f.replace("_", " ")) + r"[^.]*?\b(?:like|for example|e\.g\.|such as)\s+([A-Za-z0-9][A-Za-z0-9 /-]*[A-Za-z0-9])", hint)
        if m: return m.group(1)
        return "1984-03-14" if ("date" in f or "dob" in f or "birth" in f and "year" not in f) else "1984" if "year" in f else "4821KR" if "post" in f else "4821"
    def product(i):
        out = {}
        for k, sp in item.items():
            if k == "product_id": continue
            t = sp.get("type")
            if sp.get("enum"): out[k] = sp["enum"][(i + rng.randrange(len(sp["enum"]))) % len(sp["enum"])]
            elif t == "integer": out[k] = rng.choice([2, 6, 9, 20, 33, 52, 75])
            elif t == "number": out[k] = rng.choice([38.0, 76.0, 120.0, 305.75, 412.6, 640.0])
            elif "date" in k or sp.get("format") == "date": out[k] = (day0 + datetime.timedelta(days=rng.randint(1, 20))).date().isoformat()
            else: out[k] = str(rng.randint(1000, 9999))
        return out
    cases = []
    for i in range(n):
        now = (day0 + datetime.timedelta(days=i % 7, hours=(i * 3) % 8)).isoformat(timespec="minutes")
        cases.append({"id": f"SYN-{i + 1:02d}", "variables": {**{k: v for k, v in base.items() if k != "customer_id"}, "now": now}, "identity": {f: answer(f) for f in fields},
                      "products": [product(j) for j in range(1 + i % 3)], "expected": {}, "faults": None})
    tools = [t for t in (k.rstrip("/").split("/")[-1] for k in paths) if t != "close_call"]
    return cases + [{**cases[i], "id": f"SYN-F{i + 1}", "faults": [{"tool": t, "fail_first_n": 1}]} for i, t in enumerate(tools[: len(cases)])]


class LenientBank(Bank):
    """The identity check passes with a set chance whatever is sent, so that random runs get past it and reach the later stages."""
    def __init__(self, case, api, cid, rng, p_verify):
        super().__init__(case, api, cid); self.rng, self.p = rng, p_verify
        self.hint_first, self.hinted_once, self.hint_left = rng.random() < 0.34, set(), {}
        # every hint name the tool API file knows: the bank can answer with one of them where it does not count the answer, and verifies on the next try
        self.api_hints = [h for p, v in (api.get("paths") or {}).items() if p.rstrip("/").endswith("verify_identity")
                          for h in ((((v.get("post") or {}).get("responses", {}).get("200") or {}).get("content", {}).get("application/json", {}).get("schema") or {}).get("properties") or {}).get("hint", {}).get("enum") or []]

    def _verify_identity(self, a):
        field = a.get("field")
        if self.hint_first and field in self.identity and self.hint_left.setdefault(field, self.rng.choice([1, 2])) > 0 and self.wrong < self.max_wrong and (field in self.hints or self.api_hints):      # the bank's hint answer once or twice, then the same field verified
            self.hint_left[field] -= 1; self.hinted_once.add(field); self.hinted = True
            hint = self.hints[field]["hint"] if field in self.hints else self.rng.choice(self.api_hints)
            return 200, {"ok": True, "verified": False, "wrong_answers_left": self.max_wrong - self.wrong, "all_verified": self._all_verified(), "hint": hint}
        if self.hint_first and field in self.hinted_once and field in self.identity and self.wrong < self.max_wrong:          # the try after a hint is right
            self.verified[field] = True
            return 200, {"ok": True, "verified": True, "wrong_answers_left": self.max_wrong - self.wrong, "all_verified": self._all_verified()}
        if field in self.identity and self.wrong < self.max_wrong and self.rng.random() < self.p:
            self.verified[field] = True
            return 200, {"ok": True, "verified": True, "wrong_answers_left": self.max_wrong - self.wrong, "all_verified": self._all_verified()}
        return super()._verify_identity(a)


class FuzzEngine(Engine):
    """mode: random, one of STUCK, or script. A script is a list of turns {"text", "extract": {name: value}, "kind"}; when it
    runs out the engine goes on in the behaviour `after` (or the call must be over)."""
    def __init__(self, workflow, variables, bank, rng, mode, k=0, script=None, after=None):
        super().__init__(workflow, variables, bank, self._model)
        self.rng, self.mode, self.k, self.n, self.frozen, self.script, self.after = rng, mode, k, 0, {}, script or [], after
        self.own, self.truthful = own_values(bank.case), False

    def __deepcopy__(self, memo):                # a snapshot shares the workflow, copies the state
        for x in (self.wf, self.nodes, self.globals): memo[id(x)] = x
        new = object.__new__(type(self)); memo[id(self)] = new
        new.__dict__.update({k: copy.deepcopy(v, memo) for k, v in self.__dict__.items()})
        return new

    def behaviour(self):
        if self.mode == "script": return "script" if self.n <= len(self.script) else self.after
        return self.mode if (self.mode == "random" or self.n > self.k) else "random"

    def value(self, e):
        r = self.rng
        if e.get("enum_values"):
            if self.truthful and self.n <= self.k + 6 and r.random() < 0.6:      # a customer who simply answers: the member that says so, where the enum has one
                plain = [m for m in e["enum_values"] if re.fullmatch(r"answers?|answered|confirm(ed|s)?|yes|accept(s|ed)?|agree[sd]?|gives?_(answer|value)|value|none|new", m)]
                if plain: return plain[0]
            return r.choice(e["enum_values"])
        t = e.get("type", "string")
        if t == "boolean": return r.choice([True, False])
        if t in ("number", "integer"): return r.choice([0, 1, 2, 25, 28, 31, 81, 120, 1984, -5, 450.5])
        if self.truthful and self.n <= self.k + 6: return self.truth(e)
        return r.choice(self.own if self.own and r.random() < 0.5 else POOL)

    def truth(self, e):
        """A security answer as this customer would give it: on each turn another one of them, cut to the part the variable's name asks for."""
        answers = [str(v) for v in (self.bank.case.get("identity") or {}).values()] or ["1984"]
        v, name = answers[self.n % len(answers)], str(e.get("name", "")).lower()
        date = next((a for a in answers if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)), None)
        if date and "year" in name: return date[:4]
        if date and "month" in name: return str(int(date[5:7]))
        if date and "day" in name: return str(int(date[8:]))
        return v

    def draw(self, schema): return {e["name"]: self.value(e) for e in schema if self.rng.random() < 0.8}

    async def extract(self, n, text):
        schema, b = n["extraction_schema"], self.behaviour()
        if b == "script":
            turn = self.script[self.n - 1]; got = dict(turn["extract"])
            if turn.get("kind") == "broken": got = {e["name"]: "broken_off" for e in schema if "broken_off" in (e.get("enum_values") or [])}
            elif turn.get("kind") == "garbled": got = {e["name"]: (got[e["name"]] if e.get("enum_values") else "abc") for e in schema if e["name"] in got}
            elif turn.get("kind") == "side":
                for e in schema:
                    if str(e["name"]).startswith("side_q") and e.get("enum_values"):
                        got[e["name"]] = next((m for m in e["enum_values"] if m not in NEUTRAL), None)
        elif b == "random": got = self.draw(schema)
        elif b == "repeat": got = self.frozen.setdefault(self.node, self.draw(schema))
        elif b == "silent": got = {}
        elif b == "none": got = {e["name"]: next((m for m in NEUTRAL if m in e["enum_values"]), None) for e in schema if e.get("enum_values")}
        elif b == "broken": got = {e["name"]: "broken_off" for e in schema if "broken_off" in (e.get("enum_values") or [])}
        else: got = {e["name"]: next((m for m in e["enum_values"] if BYE.search(m)), None) for e in schema if e.get("enum_values")}
        for e in schema:
            v = got.get(e["name"])
            if v in (None, ""): continue
            if e.get("enum_values") and v not in e["enum_values"]: continue
            self.vars[e["name"]] = v
            self.event(event="variable_extracted", name=e["name"], value=v, confidence=1.0, source="extraction", source_node=self.node, input_text=text)

    async def judge_intents(self, descs, node):
        b = self.behaviour()
        return {self.sub(d): (self.rng.random() < 0.15 if b in ("random", "bye") else False) for d in descs}

    async def _model(self, prompt, system):
        """The dialogue model: a placeholder line, and in random calls now and then a tool the step offers, with random arguments."""
        n = self.node_def(self.node)
        tools = [t for t in n.get("tools") or []] + [{"function": {"name": "transition_to_" + t["target_node"]}} for t in n.get("transitions") or [] if t.get("is_tool_call")]
        if tools and self.behaviour() == "random" and self.rng.random() < 0.3 and "[result of" not in prompt.split("Customer:")[-1]:
            f = self.rng.choice(tools)["function"]
            shown = self.model_fields.get(self.node, {}).get(f["name"], {})
            return json.dumps({"say": "", "tool": {"name": f["name"], "arguments": {s: self.rng.choice(POOL) for s in shown}}})
        return json.dumps({"say": f"<model line in {self.node}>", "tool": None})


def norm(s): return re.sub(r"\s+", " ", s.lower()).strip()


def tool_of(e): return str((e.get("args") or {}).get("url") or e.get("function") or "").rstrip("/").split("/")[-1]


def inspect(eng, bank, first=0):
    """Everything that can be read off a finished run: engine events, the bank's calls, what was said and when."""
    found, wrote_ok, last_ok, pending = [], False, True, None
    for m in eng.messages[:first]:                                       # what the part of the call before `first` already recorded
        if m["role"] == "system" and '"function_call_complete"' in m["message"] and '"ok": true' in m["message"] and "promise_id" in m["message"]: wrote_ok = True
    for m in eng.messages[first:]:
        if m["role"] == "assistant":
            t = m["message"]
            if "<model line" in t: continue
            if TEMPLATE.search(t): found.append(("TEMPLATE", m.get("nodeId"), t[:110]))
            if CLAIM.search(t) and (not wrote_ok or not last_ok): found.append(("HONESTY", m.get("nodeId"), ("no record was made before: " if not wrote_ok else "the last record failed: ") + t[:100]))
            continue
        if m["role"] != "system": continue
        e = json.loads(m["message"]); msg = str(e.get("message") or e.get("error") or ""); ev = e.get("event")
        if ev == "error":
            kind = ("ENUM_WRITE" if "rejected by schema validation" in msg else "CODE_ERROR" if " raised " in msg else "SILENT_TURN" if "no speakable content" in msg else "STUCK")
            found.append((kind, m.get("nodeId"), re.sub(r"\d+", "N", msg)[:140]))
        elif ev == "function_call_start" and e.get("function") not in ("code_exec", "end_call"): pending = tool_of(e)
        elif ev == "function_call_error":
            if "Missing required field: url" in msg: found.append(("NO_URL", m.get("nodeId"), str(e.get("function"))))
            if pending and not pending.startswith(READS): last_ok = False
            pending = None
        elif ev == "function_call_complete" and e.get("function") not in ("code_exec", "end_call"):
            data = (e.get("result") or {}).get("data")
            if pending and not pending.startswith(READS) and pending != "close_call":
                last_ok = bool(isinstance(data, dict) and data.get("ok")); wrote_ok = wrote_ok or last_ok
            pending = None
    for c in bank.calls:
        err = str((c["result"] or {}).get("error") or "")
        if c["status"] == 400 or any(x in err for x in REFUSED): found.append(("BAD_CALL", c["tool"], re.sub(r"\d+", "N", err)[:120]))
    try: today = datetime.date.fromisoformat(str((bank.case.get("variables") or {}).get("now"))[:10])
    except Exception: today = None
    for p in bank.promises:
        prod = next((x for x in bank.products if x["product_id"] == p["product_id"]), {})
        top = max([float(v) for v in prod.values() if isinstance(v, (int, float)) and not isinstance(v, bool)] or [0])
        try: amount = float(p["amount"])
        except Exception: amount = None
        if amount is None or amount <= 0: found.append(("BAD_PROMISE", "create_promise", f"amount {p['amount']!r}"))
        elif top and amount > top + 0.005: found.append(("BAD_PROMISE", "create_promise", "amount above every amount of the product"))
        try:
            if today and datetime.date.fromisoformat(str(p["date"])[:10]) < today: found.append(("BAD_PROMISE", "create_promise", "date before the day of the call"))
        except Exception: pass
    closed = bool(bank.calls) and bank.calls[-1]["tool"] == "close_call" and bank.closed
    if not eng.ended: found.append(("NO_END", eng.node, "still open" + (" (the closing call was made, the call was not ended)" if closed else "")))
    if not closed: found.append(("NO_CLOSE", eng.node, "ended " + str(eng.end_reason) if eng.ended else "open"))
    return found


async def drive(eng, bank, rng, snaps=None, case=None, limit=MAX_TURNS):
    """Customer turns until the call ends, a script without a follow-up runs out, or the limit. Returns what was noticed on the way."""
    found, streak, last, t0, byes = [], 0, None, eng.n, 0
    while not eng.ended and eng.n - t0 < limit:
        if eng.mode == "script" and eng.n >= len(eng.script) and not eng.after: break
        eng.n += 1
        b, before = eng.behaviour(), len(eng.messages)
        text = eng.script[eng.n - 1]["text"] if b == "script" else TEXTS.get(b) or rng.choice(POOL)
        await eng.user(text)
        eng.max_hops = max(getattr(eng, "max_hops", 0), eng.hops)
        byes = byes + 1 if b == "bye" else 0
        if byes == 3 and not eng.ended: found.append(("BYE_IGNORED", eng.node, "the customer said goodbye three times"))
        said = [m["message"] for m in eng.messages[before:] if m["role"] == "assistant"]
        if not said and not eng.ended: found.append(("SILENT_TURN", eng.node, ""))
        if not eng.ended and any(re.search(r"\bgood ?bye\b", s, re.I) for s in said if "<model line" not in s): found.append(("GOODBYE", eng.node, " ".join(said)[:110]))
        line = norm(" ".join(said))
        if line and "<model line" not in line:
            streak = streak + 1 if line == last else 1
            last = line
            if streak == REPEAT_LIMIT: found.append(("REPEAT", eng.node, " ".join(said)[:110]))
        elif line: streak, last = 0, None
        if streak >= REPEAT_LIMIT + 2: break
        if snaps is not None and not eng.ended and len(snaps[eng.node]) < SNAPS_PER_STEP: snaps[eng.node].append((copy.deepcopy(eng), case))
    return found


def row(eng, bank, case, found, label, first=0):
    seen, out = set(), []
    for f in found + inspect(eng, bank, first):
        if f not in seen: seen.add(f); out.append(f)
    return {"what": label, "case": case["id"], "turns": eng.n, "ended": eng.ended, "tools": [c["tool"] for c in bank.calls], "found": out, "nodes": sorted(eng.entered), "hops": getattr(eng, "max_hops", 0),
            "tail": [f"{'AGENT' if m['role'] == 'assistant' else 'CUSTOMER'}: {m['message'][:150]}" for m in eng.messages if m["role"] != "system"][-8:]}


async def random_call(wf, api, case, seed, snaps):
    rng = random.Random(seed)
    mode, k = rng.choice(["random", "random", "random", *STUCK]), rng.randint(0, 10)
    bank = LenientBank(case, api, "c_fuzz", rng, rng.choice([0.5, 0.9, 1.0, 1.0]))
    eng = FuzzEngine(wf, {**case["variables"], "customer_id": "c_fuzz"}, bank, rng, mode, k)
    eng.truthful = seed % 2 == 0                 # half of the calls answer the security questions with the customer's own values, to get past them
    try:
        await eng.start(); found = await drive(eng, bank, rng, snaps, case)
    except Exception as e: found = [("HARNESS", eng.node, f"{type(e).__name__}: {e}"[:160])]
    return row(eng, bank, case, found, f"random call, customer behaviour {mode}" + (f" from turn {k + 1}" if mode != "random" else ""))


async def stuck_from(snap, case, mode, seed, origin):
    rng = random.Random(seed)
    eng = copy.deepcopy(snap); bank = eng.bank
    eng.rng = rng; eng.mode, eng.k, eng.frozen, eng.script, eng.after = mode, 0, {}, [], None
    if hasattr(bank, "rng"): bank.rng = rng
    first = len(eng.messages)
    try: found = await drive(eng, bank, rng)
    except Exception as e: found = [("HARNESS", eng.node, f"{type(e).__name__}: {e}"[:160])]
    return row(eng, bank, case, found, f"{origin}, then a customer who is stuck ({mode})", first)


# ---------------------------------------------------------------------------------------------------- recordings
def recordings(folder, cases):
    """The calls of one local test round: per call the customer's turns with what was extracted, the bank calls, and whether it passed."""
    by, out = {c["id"]: c for c in cases}, []
    for f in sorted(pathlib.Path(folder).glob("*-r*.json")):
        try: d = json.loads(f.read_text())
        except Exception: continue
        r = d.get("row") or {}
        if r.get("id") not in by or "messages" not in d: continue
        turns = []
        for m in d["messages"]:
            if m["role"] == "user": turns.append({"text": m["message"], "extract": {}})
            elif m["role"] == "system" and turns and '"variable_extracted"' in m["message"]:
                e = json.loads(m["message"]); turns[-1]["extract"][e["name"]] = e["value"]
        out.append({"name": f.stem, "case": by[r["id"]], "turns": turns, "calls": [(c["tool"], json.dumps(c["args"], sort_keys=True)) for c in d["ledger"]["calls"]], "passed": bool(r.get("pass"))})
    return out


async def scripted(wf, api, case, script, after=None, snaps=None):
    rng = random.Random(7)
    bank = Bank(case, api, "c_fuzz")
    eng = FuzzEngine(wf, {**case["variables"], "customer_id": "c_fuzz"}, bank, rng, "script", script=script, after=after)
    try:
        await eng.start(); found = await drive(eng, bank, rng, snaps, case)
    except Exception as e: found = [("HARNESS", eng.node, f"{type(e).__name__}: {e}"[:160])]
    return eng, bank, found


def records_wrong(case, bank):
    if not score_ledger: return []
    try: return list(score_ledger(case, bank.ledger())["failed"])
    except Exception as e: return [f"scorer: {type(e).__name__}: {e}"]


KEEP = ("MUTATION", "BYE_IGNORED", "REPEAT", "CODE_ERROR", "STUCK", "ENUM_WRITE", "NO_URL", "BAD_CALL", "BAD_PROMISE", "TEMPLATE", "HONESTY", "GOODBYE")


async def replay_all(wf, api, recs, a):
    """Returns the rows of the mutation and deep runs, and a line of counts."""
    rows, same, usable, deep_seeds = [], 0, [], collections.defaultdict(list)
    for rec in recs:
        snaps = collections.defaultdict(list)
        eng, bank, found = await scripted(wf, api, rec["case"], rec["turns"], snaps=snaps)
        calls = [(c["tool"], json.dumps(c["args"], sort_keys=True)) for c in bank.calls]
        if calls == rec["calls"] and eng.ended:
            same += 1
            for node, items in snaps.items():
                for s in items:
                    if len(deep_seeds[node]) < a.deep_per_step: deep_seeds[node].append((s[0], rec))
            if rec["passed"] and not records_wrong(rec["case"], bank): usable.append(rec)
        elif rec["passed"] and a.regression:
            wrong = records_wrong(rec["case"], bank) or ["the call did not end"]
            r = row(eng, bank, rec["case"], [("REGRESSION", eng.node, "; ".join(wrong)[:200])], f"replay of the passed call {rec['name']}")
            r["found"] = [f for f in r["found"] if f[0] == "REGRESSION"]; rows.append(r)
    n_mut, n_open, seen = 0, 0, set()
    enum_names = {e["name"] for n in (wf.get("workflow", wf))["nodes"].values() for e in n.get("extraction_schema") or [] if e.get("enum_values")}
    for rec in usable:
        t = rec["turns"]
        for i in range(len(t)):
            for kind in ("broken", "empty", "garbled", "split", "side"):
                key = (rec["case"]["id"], i, kind, t[i]["text"])                # the same customer's three runs mostly give the same turns
                if key in seen: continue
                seen.add(key)
                if kind == "garbled" and not any(n not in enum_names for n in t[i]["extract"]): continue          # nothing but labels in this turn
                if kind == "split":                                  # the same answer given in two parts over two turns, as customers do with numbers
                    vals = {n: str(v) for n, v in t[i]["extract"].items() if n not in enum_names and re.fullmatch(r"[A-Za-z0-9 ]{4,}", str(v)) and sum(c.isdigit() for c in str(v)) >= 3}
                    if not vals: continue
                    cut = lambda v: (v.replace(" ", "")[: len(v.replace(" ", "")) // 2], v.replace(" ", "")[len(v.replace(" ", "")) // 2:])
                    first = {**t[i], "text": " ".join(cut(v)[0] for v in vals.values()), "extract": {**t[i]["extract"], **{n: cut(v)[0] for n, v in vals.items()}}}
                    second = {**t[i], "text": " ".join(cut(v)[1] for v in vals.values()), "extract": {**t[i]["extract"], **{n: cut(v)[1] for n, v in vals.items()}}}
                    script = t[:i] + [first, second] + t[i + 1:]
                elif kind == "side": script = t[:i] + [{**t[i], "kind": "side"}] + t[i + 1:]
                elif kind == "garbled": script = t[:i] + [{**t[i], "kind": "garbled"}] + t[i:]
                else: script = t[:i] + [{"text": (t[i]["text"].split(" ")[0] + " I") if kind == "broken" else "Mm.", "extract": {}, "kind": kind}] + t[i:]
                eng, bank, found = await scripted(wf, api, rec["case"], script)
                n_mut += 1
                wrong = records_wrong(rec["case"], bank)
                what = {"broken": "a message that is cut off", "empty": "a message from which nothing is extracted", "garbled": "the same message with its extracted values turned into garbage ('abc')", "split": "its number given in two parts over two turns", "side": "a side question in the same message"}[kind]
                label = f"the passed call {rec['name']} with {what} {'in' if kind in ('side', 'split') else 'before'} customer turn {i + 1} ({t[i]['text'][:50]!r})"
                if not eng.ended and not bank.closed:                    # the recorded turns ran out with the call still open: the agent asked for something once more, which the recording cannot answer
                    n_open += 1
                    r = row(eng, bank, rec["case"], found, label); r["found"] = [f for f in r["found"] if f[0] in KEEP and f[0] != "MUTATION"]; rows.append(r)
                    continue
                got = wrong[0].split(", expected")[0] if wrong else "the call did not end"
                r = row(eng, bank, rec["case"], found + ([("MUTATION", kind, f"{what}: {got}")] if (wrong or not eng.ended) else []), label + (". Bank records: " + "; ".join(wrong)[:300] if wrong else ""))
                r["found"] = [f for f in r["found"] if f[0] in KEEP]; rows.append(r)
    n_deep, i = 0, 0
    for node in sorted(deep_seeds):
        for snap, rec in deep_seeds[node]:
            for mode in STUCK:
                i += 1; n_deep += 1
                rows.append(await stuck_from(snap, rec["case"], mode, a.seed * 100000 + 70000 + i, f"the recorded call {rec['name']} up to customer turn {snap.n}, waiting in {node}"))
    return rows, f"{len(recs)} recorded calls, {same} replayed with the same bank calls, {len(usable)} of them passed and were disturbed: {n_mut} mutation runs ({n_open} of them left open when the recorded turns ran out: not counted), {n_deep} deep runs from {len(deep_seeds)} steps."


# ---------------------------------------------------------------------------------------------------- report
def report(rows, nodes, header, show):
    bad = [r for r in rows if r["found"]]
    groups = collections.defaultdict(list)
    for r in bad:
        for kind, where, what in r["found"]: groups[(kind, where, what if kind not in ("REPEAT", "GOODBYE", "TEMPLATE", "HONESTY") else "")].append((r, what))
    groups = {k: [x[0] for x in v] if k[2] else v for k, v in groups.items()}
    kinds = collections.Counter(k for r in bad for k in {f[0] for f in r["found"]})
    reached = set().union(*(r["nodes"] for r in rows)) if rows else set()
    never = sorted(set(nodes) - reached)
    lines = [*header, f"{len(rows)} runs, {len(rows) - len(bad)} clean, {len(bad)} with a defect. Steps reached: {len(reached & set(nodes))} of {len(nodes)}."
             + (f" Never reached (nothing here tests them): {', '.join(never)}." if never else "")]
    lines += [f"  {k:<11} {v} runs" for k, v in kinds.most_common()]
    lines.append(f"Most step changes after one customer message: {max((r.get('hops', 0) for r in rows), default=0)} (each code step and bank call adds to the time the customer waits).")
    order = sorted(groups.items(), key=lambda x: (-len(x[1]), x[0]))
    for (kind, where, what), rs in order[:show]:
        if not what and rs and isinstance(rs[0], tuple):
            said = collections.Counter(x[1] for x in rs); rs = [x[0] for x in rs]
            what = f"{len(said)} different sentences, e.g. " + " | ".join(t for t, _ in said.most_common(3))
        r = min(rs, key=lambda r: r["turns"])
        lines += ["", f"## {kind} at {where} ({len(rs)} runs): {what}", f"Shortest run: {r['what']}; customer {r['case']}, {r['turns']} customer turns, bank calls: {' > '.join(r['tools']) or 'none'}", "Last lines:"]
        lines += ["    " + t for t in r["tail"]]
    if len(order) > show: lines += ["", f"({len(order) - show} more groups not shown)"]
    return "\n".join(lines), bad, kinds, order


async def main(a):
    wf = json.loads(pathlib.Path(a.workflow).read_text()); api = json.loads(pathlib.Path(a.api).read_text())
    if a.cases:
        all_cases = json.loads(pathlib.Path(a.cases).read_text())
        if a.brief: all_cases = [c for c in all_cases if c.get("brief") == a.brief]
        cases = [{**c, "faults": None} for c in all_cases] + [c for c in all_cases if c.get("faults")]     # every customer without its tool failure, and those with one as written
    else: all_cases = cases = synthetic(api, wf)
    snaps, rows, header = collections.defaultdict(list), [], []
    for i in range(a.runs): rows.append(await random_call(wf, api, cases[i % len(cases)], a.seed * 100000 + i, snaps))
    n_random, i = len(rows), 0
    for node in sorted(snaps):
        for snap, case in snaps[node]:
            for mode in STUCK:
                i += 1; rows.append(await stuck_from(snap, case, mode, a.seed * 100000 + 50000 + i, f"a random call waiting in {node}"))
    header.append(f"{n_random} random calls, then {len(rows) - n_random} calls continued from {len(snaps)} waiting steps with a stuck customer.")
    if a.recordings:
        more, line = await replay_all(wf, api, recordings(a.recordings, all_cases), a)
        rows += more; header.append(line)
    text, bad, kinds, order = report(rows, (wf.get("workflow", wf))["nodes"], header, a.show)
    print(text)
    if a.md: pathlib.Path(a.md).write_text("# Model-free test of the workflow\n\nNo model took part: extraction results were random or replayed from recorded calls. Each group below is one defect, with the shortest run that shows it.\n\n" + text + "\n")
    if a.json: pathlib.Path(a.json).write_text(json.dumps({"runs": len(rows), "defect_runs": len(bad), "kinds": kinds, "header": header,
                                                           "groups": [{"kind": k[0], "where": k[1], "what": k[2], "runs": len(v)} for k, v in order]}, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workflow"); ap.add_argument("api"); ap.add_argument("--cases", help="test customers; without it customers are made up from the tool API file"); ap.add_argument("--brief")
    ap.add_argument("--recordings", help="folder of a local test round (results/<name>): its calls are replayed, disturbed, and used as starting points")
    ap.add_argument("--regression", action="store_true", help="also report a passed recorded call whose replay no longer gives its bank records (use after a repair)")
    ap.add_argument("--deep-per-step", type=int, default=6)
    ap.add_argument("--runs", type=int, default=600); ap.add_argument("--seed", type=int, default=1); ap.add_argument("--show", type=int, default=15); ap.add_argument("--md"); ap.add_argument("--json")
    sys.exit(asyncio.run(main(ap.parse_args())))
