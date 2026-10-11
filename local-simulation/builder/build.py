"""The builder: reads a bank's brief and its tool API file, builds the phone agent, checks and tests it, repairs it, saves it to Freya.

    python3 build.py <brief file or folder> [more brief files ...] <tool_api.json> [--slot NAME] [--cases FILE --case-brief A --case-level hard]

One run, no hands:
  1. build     Claude (Opus 5.5, Claude Agent SDK, skill .claude/skills/build-phone-agent) writes rules.md and gen.py and generates workflow.json.
  2. check     in parallel: four independent readers go through the workflow against the brief (review.py), and the hard
               test customers of --cases talk to it in the local simulator (../sim/local.py), three runs each.
               Then fuzz_workflow.py runs the workflow without a model: random calls, stuck customers, and the recorded test
               calls replayed with one disturbance each. Its defects go to the repair turn with the failed calls.
  3. repair    the same Claude session gets the findings and the failed calls, fixes gen.py, regenerates.
  4. re-test   the test customers run again. One more repair round if something still fails and time is left.
  5. deliver   the best tested version is saved to Freya and exported.

Needs FREYA_API_KEY and ANTHROPIC_API_KEY (readers and simulator; without it steps 2-4 are skipped). Writes out/<slot>/ and
the trace of the run to traces/<slot>-<start time>.jsonl. The trace is part of the submission.
"""
import argparse, asyncio, dataclasses, json, os, pathlib, shutil, sys, tempfile, time

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock, ToolUseBlock

HERE = pathlib.Path(__file__).resolve().parent
SIM = HERE.parent / "sim" / "local.py"
KIT = HERE.parent.parent / "kit" / "build-the-builder"
BUDGET = int(os.environ.get("BUILD_BUDGET_SECONDS", 60 * 60))         # the whole run stays under this (default 60 minutes; the event's rules no longer set a limit, an earlier version of them said 30). --budget-minutes or BUILD_BUDGET_SECONDS=1680 gives the 28-minute run back
STALE_ROUNDS = 4                                                      # the repair loop also ends after this many rounds in a row that did not beat the best version so far
SAFE_PART = 0.9                                                       # after this part of the budget is used, no new repair round starts; what is running is finished, tested and delivered
REPAIR_NEEDS = 170 + 150                                              # the first repair round is started with this much left (a short turn with checkpoints is worth it)
AFTER_REPAIR = 170        # re-test (63-112 s measured) + delivery; the model-free test after a repair runs only when there is time for it
REPAIR_TURN = 270         # one repair turn is at most this long, so that two or three rounds of repair and re-test fit: measured, a re-test tells more than a longer turn (10 -> 20 of 28 after a 98 s turn)
REPAIR_NEEDS_LATER = AFTER_REPAIR + 120                                         # a second round is shorter: measured 22-77 s of repair + ~70 s test + ~60 s model-free test + ~110 s delivery
REPAIR_NEEDS_PLATFORM = 16 * 60                                       # a test round on the platform has taken up to 16 minutes when simulations queue
CASE_FORMAT = HERE.parent / "cases" / "CASE-FORMAT.md"

WRITE_CASES = """Read CASE-FORMAT.md in this folder and follow it. Brief documents, in the order they were given: {briefs}. Tool API file: {api}.
Write the test customers to cases.json in this folder: a JSON list. Use "brief": "{letter}" and the ids T-01, T-02, ... in every case.
Work only in this folder. When the file is written, load it once with python3 to make sure it is valid JSON, then finish with the line: CASES n=<number of cases>"""

CASE_LETTER = "T"
CASES_SECOND_PASS_BEFORE = 7 * 60                                     # the writer gets a second turn only this early: the cases must be ready when the first version is
MORE_CASES = """A program counted what cases.json covers against the tool API file. Missing:
{gaps}

Add cases to cases.json for what is missing, ids from T-{next:02d} on, by the same rules of CASE-FORMAT.md. Keep every case that is there. At most 30 cases in the file.
A value of the tool API file that the brief never leads to needs no case: leave it and say so in one line.
Load the file once with python3 to make sure it is valid JSON, then finish with the line: CASES n=<number of cases>"""

REPAIR = """The first version is built. Now repair it. Two independent checks ran on out/{slot}/workflow.json:

1. out/{slot}/review.md: readers went through the workflow against the brief without running it.
2. out/{slot}/{tests}: simulated customers talked to the workflow (on the real platform or in a local copy of its engine); this file has every failed call with its script, the expected bank records, the checks the judge used, the judge's reason, the transcript and the tool calls.{no_tests}

3. out/{slot}/{fuzz}: a program ran the workflow without any model: random extraction results, customers who are stuck (the same answer again and again, nothing that can be extracted, cut-off messages, only "goodbye"), and the recorded test calls played again with one disturbance put in. Each group is one defect with the shortest run that shows it. These are exact, not a model's opinion: a question that is asked again without end, a call that does not close, a record the bank refuses, or bank records that change when one cut-off message is put into a call that passed. On the platform each of them fails a customer. You can run it yourself after a change: python3 fuzz_workflow.py out/{slot}/workflow.json {api} --cases out/{slot}/cases.json{recordings} (with --recordings it also replays the recorded test calls against your changed workflow; add --regression to see a passed call that no longer gives its records)

Read these files. For each item decide whether it is a real defect of the workflow: check it against the brief and against gen.py. A reader can be wrong, and a simulated customer or judge can be wrong too. The test customers and their expected bank records were written from the brief by another reader who did not see the workflow, and an expectation can be wrong as well: where the brief supports the workflow and not the expectation, leave the workflow alone and say why.
Fix every real defect in out/{slot}/gen.py at the level of the mechanism (a counter, a gate, a transition, a computed fact, fixed text), following the skill's table. Do not write anything that only fits one test customer: a name, amount or date that comes from a test customer and not from the brief must not appear in gen.py. A fix must not break a rule that already works.
Whenever you change how a rule is computed or counted, add or correct its assertion in out/{slot}/selftest.py first, then change gen.py, and run selftest.py: every assertion passes before you go on. Never change an expected value to fit the code; check it against the brief.
Work in small steps, most costly defect first: a defect that fails several test customers, then a call that never ends or never closes, then single customers, then the smaller disturbances. One fix at a time. After EACH fix run
    python3 checkpoint.py out/{slot} {api}{recordings}
It runs gen.py, check_workflow.py, selftest.py and a short model-free run and replays the recorded calls that passed, in some seconds. "CHECKPOINT n SAVED" means this fix is kept. "NOT SAVED" names what broke: repair that, or undo the fix with  cp out/{slot}/ckpt/gen.py out/{slot}/gen.py  and go to the next item. This turn is cut off at a fixed time, and what is then tested and delivered is the LAST CHECKPOINT, not the files as they are: a fix without a checkpoint is lost, and five saved fixes are worth more than eight unfinished ones. Run the full fuzz_workflow.py at most once in this turn; the program that started you runs it after the turn anyway. Do not save to the platform.
After each checkpoint add one line to out/{slot}/repair-{round}.md (R-number or test customer id): fixed (how) or not changed (why). When nothing costly is left, finish with the line: REPAIRED round={round}"""


def workdir(slot):
    """A fresh, empty folder for one run, holding only what the build needs: the skill, the platform documentation, the reference
    shapes and the static check. Nothing of an earlier run or another brief is in it, so the agent cannot start from an old generator."""
    work = pathlib.Path(tempfile.mkdtemp(prefix=f"build-{slot}-"))
    for name in (".claude", "docs", "reference"): shutil.copytree(HERE / name, work / name)
    shutil.copy(HERE / "check_workflow.py", work / "check_workflow.py"); shutil.copy(HERE / "checkpoint.py", work / "checkpoint.py")
    for f in (HERE / "check_rules.py", HERE / "fuzz_workflow.py", SIM.parent / "engine.py", SIM.parent / "bank.py", KIT / "tools" / "score.py"): shutil.copy(f, work / f.name)      # the model-free test and what it runs on
    return work


def to_text(f):
    """The text of a Word, Excel or PDF file, or None for any other kind. The session reads the .txt copy; the original stays next to it."""
    ext = f.suffix.lower()
    try:
        if ext == ".docx":
            import docx
            d = docx.Document(str(f)); out = [p.text for p in d.paragraphs]
            for t in d.tables: out += ["", *(" | ".join(c.text.strip() for c in r.cells) for r in t.rows)]
            return "\n".join(out)
        if ext in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(str(f), data_only=True); out = []
            for ws in wb.worksheets:
                out.append(f"## sheet: {ws.title}")
                out += [" | ".join("" if c is None else str(c) for c in row) for row in ws.iter_rows(values_only=True) if any(c is not None for c in row)]
            return "\n".join(out)
        if ext == ".pdf":
            import pypdf
            return "\n\n".join(f"## page {i + 1}\n{pg.extract_text() or ''}" for i, pg in enumerate(pypdf.PdfReader(str(f)).pages))
        if ext == ".csv": return None
    except Exception as e: return f"(this file could not be converted to text: {type(e).__name__}: {e}. Read the original with a tool.)"
    return None


def stage(paths, slot, work):
    """Copy the given documents (files, folders, zip files) into out/<slot>/input of the work folder so the run reads nothing outside it.
    Word, Excel and PDF files also get a .txt copy. BRIEF and README files come first in the list."""
    import zipfile
    dst = work / "out" / slot / "input"
    dst.mkdir(parents=True, exist_ok=True)
    files = []
    for p in map(pathlib.Path, paths):
        if p.is_file() and p.suffix.lower() == ".zip":
            box = pathlib.Path(tempfile.mkdtemp(prefix="case-")); zipfile.ZipFile(p).extractall(box); p = box
        for f in (sorted(x for x in p.rglob("*") if x.is_file() and not x.name.startswith(".") and "__MACOSX" not in x.parts) if p.is_dir() else [p]):
            name = f.name if not (dst / f.name).exists() else f"{f.parent.name}-{f.name}"
            shutil.copy(f, dst / name); files.append(f"out/{slot}/input/{name}")
            text = to_text(f)
            if text is not None: (dst / (name + ".txt")).write_text(text); files.append(f"out/{slot}/input/{name}.txt")
    first = lambda x: (0 if pathlib.Path(x).stem.upper() == "BRIEF" else 1 if pathlib.Path(x).stem.upper() == "README" else 2)
    return sorted(files, key=first)


def is_tool_api(f):
    try: return f.suffix.lower() == ".json" and "paths" in json.loads(f.read_text())
    except Exception: return False


def stage_api(path, slot, work, mock=None):
    """The tool API file of the run: the given one, or the one among the documents. Returns None when there is none: the run then
    writes it from the service contract in the documents. With a mock address the file's server is that address."""
    dst = work / "out" / slot / "input" / "tool_api.json"
    found = pathlib.Path(path) if path else next((f for f in sorted((work / "out" / slot / "input").iterdir()) if is_tool_api(f)), None)
    if not found: return None
    if found != dst: shutil.copy(found, dst)
    if mock:
        api = json.loads(dst.read_text()); api["servers"] = [{"url": mock}]; dst.write_text(json.dumps(api, indent=1))
    return f"out/{slot}/input/tool_api.json"


CONTRACT = """The documents of a bank's phone-agent case are in out/{slot}/input ({files}). Word, Excel and PDF files have a .txt copy next to them. Among them is a description of the bank's services (a service contract, an API description, example requests and answers).
Write out/{slot}/input/tool_api.json: one OpenAPI 3 file with exactly the services the documents describe that a phone agent calls during a call. For each: path, method, every request field with its type, whether it is required and its allowed values, and the answer with every field, the optional ones included, and the error answers. Use the names and values exactly as the documents give them; add nothing they do not say. Put {server} as the only entry of "servers". Check that the file loads with json.load. Then stop."""


class Run:
    def __init__(self, a):
        self.a, self.t0 = a, time.time()
        self.slot = a.slot or pathlib.Path(a.inputs[0]).stem
        self.work = workdir(self.slot)
        self.out = self.work / "out" / self.slot
        self.briefs = stage(a.docs, self.slot, self.work)
        self.api = stage_api(a.api_file, self.slot, self.work, a.mock)
        self.briefs = [b for b in self.briefs if b != self.api and not is_tool_api(self.work / b)]      # the tool API file is not a brief document, whatever its name in the folder
        self.trace_path = HERE / "traces" / f"{self.slot}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self.trace_path.parent.mkdir(exist_ok=True)
        self.trace = self.trace_path.open("w")
        self.cost = {"builder_usd": 0.0, "cases_usd": 0.0, "review_usd": 0.0, "tests_usd": 0.0}
        self.steps, self.versions = [], []
        self.can_check = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self.dead = False                                                    # the build session no longer answers: no further repair turns

    def left(self): return BUDGET - (time.time() - self.t0)

    def log(self, kind, **data):
        self.trace.write(json.dumps({"type": kind, "t": round(time.time() - self.t0, 1), **data}, default=str) + "\n"); self.trace.flush()

    def say(self, text): print(f"[{time.time() - self.t0:5.0f}s] {text}", flush=True)

    # ---- the Claude session that writes and repairs the generator
    async def turn(self, client, prompt, label, reserve=None):
        """One turn of the session. With `reserve` the turn is interrupted when only that many seconds of the budget are left;
        returns True when that happened (the files are then whatever the session had written so far)."""
        t, cut = time.time(), False
        async def guard():
            nonlocal cut
            await asyncio.sleep(max(self.left() - reserve, 1))
            cut = True; self.say(f"{label}: time limit, the session is interrupted"); self.log("interrupt", step=label)
            await client.interrupt()
        watch = asyncio.create_task(guard()) if reserve is not None else None
        try:
            await client.query(prompt)
            if reserve is None: await self.listen(client, label, t)
            else: await asyncio.wait_for(self.listen(client, label, t), max(self.left() - reserve, 1) + 40)     # 40 s for the session to answer the interrupt
        except asyncio.TimeoutError:
            cut = self.dead = True; self.say(f"{label}: the session did not stop after the interrupt; no further turns")
        finally:
            if watch: watch.cancel()
        if cut and not any(x["step"] == label for x in self.steps): self.steps.append({"step": label, "seconds": round(time.time() - t), "interrupted": True})
        elif cut: self.steps[-1]["interrupted"] = True
        return cut

    async def listen(self, client, label, t):
        async for message in client.receive_response():
            self.log(type(message).__name__, step=label, **dataclasses.asdict(message))
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock): print(block.text, flush=True)
                    elif isinstance(block, ToolUseBlock): print(f"  [{block.name}] {str(block.input)[:140]}", flush=True)
            elif isinstance(message, ResultMessage):
                self.cost["builder_usd"] = max(self.cost["builder_usd"], message.total_cost_usd or 0.0)        # the session reports its running total
                self.steps.append({"step": label, "seconds": round(time.time() - t), "turns": message.num_turns, "session_usd_so_far": message.total_cost_usd})
                self.say(f"{label}: {message.num_turns} turns, {time.time() - t:.0f} s")

    # ---- the tool API file, when the case comes with a service contract in prose instead of one
    async def contract(self):
        t = time.time()
        options = ClaudeAgentOptions(cwd=self.work, model=os.environ.get("BUILDER_MODEL", "claude-opus-5-5"), setting_sources=[], allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
                                     permission_mode="dontAsk", max_turns=40, env={"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"})
        async with ClaudeSDKClient(options=options) as client:
            await client.query(CONTRACT.format(slot=self.slot, files=", ".join(pathlib.Path(b).name for b in self.briefs), server=json.dumps({"url": self.a.mock or "https://bank.invalid"})))
            async for message in client.receive_response():
                self.log(type(message).__name__, step="contract", **dataclasses.asdict(message))
                if isinstance(message, ResultMessage): self.cost["cases_usd"] += message.total_cost_usd or 0.0
        dst = self.out / "input" / "tool_api.json"
        if not is_tool_api(dst): sys.exit("no tool API file was given and none could be written from the documents")
        self.api = f"out/{self.slot}/input/tool_api.json"
        self.steps.append({"step": "contract", "seconds": round(time.time() - t), "paths": len(json.loads(dst.read_text())["paths"])})
        self.say(f"tool API file written from the documents: {self.steps[-1]['paths']} services, {time.time() - t:.0f} s")

    # ---- test customers written from the brief alone, while the agent is being built
    async def write_cases(self):
        """A second Claude session, in its own empty folder, writes test customers for the brief. It never sees the agent, and the
        session that builds the agent never sees the cases before the first version is finished. Returns the path of the file, or None."""
        t, work = time.time(), pathlib.Path(tempfile.mkdtemp(prefix=f"cases-{self.slot}-"))
        names = []
        for b in self.briefs: shutil.copy(self.work / b, work / pathlib.Path(b).name); names.append(pathlib.Path(b).name)
        shutil.copy(self.work / self.api, work / "tool_api.json"); shutil.copy(CASE_FORMAT, work / "CASE-FORMAT.md")
        options = ClaudeAgentOptions(cwd=work, model=os.environ.get("CASES_MODEL", "claude-opus-5-5"), setting_sources=[], allowed_tools=["Read", "Write", "Edit", "Bash"],
                                     permission_mode="dontAsk", max_turns=40, env={"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"})
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(WRITE_CASES.format(briefs=", ".join(names), api="tool_api.json", letter=CASE_LETTER))
                async for message in client.receive_response():
                    self.log(type(message).__name__, step="cases", **dataclasses.asdict(message))
                    if isinstance(message, ResultMessage): self.cost["cases_usd"] = message.total_cost_usd or 0.0
                # what the tool API file allows and no case expects: one more turn for the writer, while the agent is still being built
                import check_cases
                missing, n = check_cases.gaps(json.loads((work / "cases.json").read_text()), json.loads((work / "tool_api.json").read_text()))
                self.log("case_gaps", round=1, cases=n, gaps=missing)
                if missing and time.time() - t < CASES_SECOND_PASS_BEFORE:
                    self.say(f"test customers: {n} written, {len(missing)} gaps, asking for more")
                    await client.query(MORE_CASES.format(gaps="\n".join("- " + g for g in missing), next=n + 1))
                    async for message in client.receive_response():
                        self.log(type(message).__name__, step="cases 2", **dataclasses.asdict(message))
                        if isinstance(message, ResultMessage): self.cost["cases_usd"] = message.total_cost_usd or 0.0
                    missing, n = check_cases.gaps(json.loads((work / "cases.json").read_text()), json.loads((work / "tool_api.json").read_text()))
                    self.log("case_gaps", round=2, cases=n, gaps=missing)
                self.case_gaps = missing
            cases = json.loads((work / "cases.json").read_text())
            need = {"id", "variables", "identity", "products", "script", "expected", "checks"}
            cases = [c for c in cases if isinstance(c, dict) and need <= set(c)]
            for c in cases: c["brief"] = CASE_LETTER
            if not cases: raise ValueError("no usable case in cases.json")
        except Exception as e:                                                   # without test customers the run still delivers its first version
            self.say(f"test customers: none ({type(e).__name__}: {str(e)[:200]})"); self.steps.append({"step": "cases", "seconds": round(time.time() - t), "error": str(e)[:200]})
            return None
        (self.out / "cases.json").write_text(json.dumps(cases, indent=1, ensure_ascii=False))
        self.steps.append({"step": "cases", "seconds": round(time.time() - t), "cases": len(cases), "usd": self.cost["cases_usd"], "gaps_left": getattr(self, "case_gaps", None)})
        self.say(f"test customers: {len(cases)} written from the brief, {len(getattr(self, 'case_gaps', []) or [])} coverage gaps left, {time.time() - t:.0f} s")
        return str(self.out / "cases.json")

    # ---- the two checks
    async def review(self):
        import review
        t = time.time()
        try: findings, cost = await review.review(self.out, [self.work / b for b in self.briefs], self.work / self.api)
        except Exception as e:                                                   # the readers are a help, not a condition: the run goes on without them
            findings, cost = [{"lens": "all", "severity": "info", "error": f"readers failed: {type(e).__name__}: {str(e)[:200]}"}], {"usd": 0.0}
        keep = [f for f in findings if f.get("severity") in self.a.review_severity.split(",") or f.get("error")]     # measured: repairing every finding at once broke working rules
        (self.out / "review.md").write_text(review.as_markdown(keep)); (self.out / "review.json").write_text(json.dumps(keep, indent=1, ensure_ascii=False))
        (self.out / "review-all.json").write_text(json.dumps(findings, indent=1, ensure_ascii=False))
        self.cost["review_usd"] += cost["usd"]
        by = {s: sum(f.get("severity") == s for f in findings) for s in ("high", "medium", "low")}
        self.steps.append({"step": "review", "seconds": round(time.time() - t), "findings": by, "usd": cost["usd"]})
        self.log("review", findings=findings, cost=cost)
        self.say(f"review: {by['high']} high, {by['medium']} medium, {by['low']} low findings, {time.time() - t:.0f} s, ${cost['usd']:.2f}")
        return findings

    async def validate(self):
        """Ask the platform whether it accepts the file (a dry run, nothing is saved). A rejection becomes a finding for the repair turn."""
        import freya
        try:
            await asyncio.wait_for(asyncio.to_thread(freya.validate_workflow, self.slot, json.loads((self.out / "workflow.json").read_text())), 120)
            self.say("platform dry run: accepted"); return None
        except asyncio.TimeoutError:
            self.say("platform dry run: no answer in 120 s, skipped"); return None
        except Exception as e:
            if any(f"-> {c}" in str(e) for c in (500, 502, 503, 504, 429)):          # the platform is down or busy: that says nothing about the file
                self.say(f"platform dry run: no usable answer ({str(e)[:80]}), skipped"); return None
            self.say(f"platform dry run: REJECTED {str(e)[:200]}"); return f"{type(e).__name__}: {str(e)[:1500]}"

    async def test_platform(self, rnd):
        """Run the test customers on the Freya platform itself: its engine, its agent model, its customer and judge model, the practice bank.
        The workflow is saved to the platform first. Returns a summary of the same shape as the local simulator's."""
        import freya
        a, name, t = self.a, f"build-{self.slot}-p{rnd}", time.time()
        ids = await asyncio.to_thread(freya.save_workflow, self.slot, json.loads((self.out / "workflow.json").read_text()))
        folder = SIM.parent.parent / "results" / name; folder.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(HERE.parent.parent / "scripts" / "run_cases_retry.py"), ids["agent_id"], "--cases", a.cases, "--reps", str(a.reps), "--out", str(folder / "run.json")]
        if a.case_brief: cmd += ["--brief", a.case_brief]
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(KIT), env={**os.environ, "PYTHONUNBUFFERED": "1"}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        log = (await proc.communicate())[0].decode()
        (self.out / f"tests-{rnd}.log").write_text(log)
        run = json.loads((folder / "run.json").read_text())
        rows = []
        for rep, rep_rows in enumerate(run["all_rows"], 1):
            for r in rep_rows:
                row = {"id": r["id"], "rep": rep, "pass": r["pass"], "failed": r["failed"], "ending": r.get("ended"), "call_id": r.get("call_id")}
                rows.append(row)
                if r["pass"] or not r.get("call_id"): continue
                try: call = await asyncio.to_thread(freya.api, "GET", f"/api/v2/call/{r['call_id']}")
                except Exception: continue
                lines, calls, i = [], [], -1
                for m in (call.get("call") or call).get("messages", []):
                    if m.get("role") in ("assistant", "user"):
                        i += 1; lines.append(f"[Message {i}] {'Assistant' if m['role'] == 'assistant' else 'User'}: {m.get('message')}")
                        continue
                    try: e = json.loads(m.get("message") or "")
                    except Exception: continue
                    if e.get("event") == "function_call_start" and e.get("function") != "end_call":
                        args = e.get("args") or {}
                        calls.append({"n": len(calls) + 1, "tool": str(args.get("url") or e.get("function")).rstrip("/").split("/")[-1], "args": args.get("body") if "body" in args else {}, "status": "", "result": "(no result event)"})
                    elif e.get("event") == "function_call_complete" and calls and e.get("function") != "end_call":
                        res = e.get("result") or {}; calls[-1]["status"], calls[-1]["result"] = res.get("status_code", ""), res.get("data") if "data" in res else res
                    elif e.get("event") == "function_call_error":
                        calls.append({"n": len(calls) + 1, "tool": e.get("function"), "args": {}, "status": "ERROR", "result": e.get("error")})
                    elif e.get("event") in ("node_enter", "max_turns_exceeded"):
                        lines.append(f"      (engine: {e['event']} {e.get('node')}{' <- ' + str(e.get('reason')) if e.get('reason') else ''})")
                (folder / f"{r['id']}-r{rep}.json").write_text(json.dumps({"transcript": lines, "ledger": {"calls": calls}}, indent=1, default=str))
        final = [{"id": f["id"], "passes": f["passes"], "runs": f["runs"], "pass": f["pass"]} for f in run["final"]]
        s = {"customers_passed": sum(f["pass"] for f in final), "customers": len(final), "passed": sum(r["pass"] for r in rows), "conversations": len(rows), "final": final, "rows": rows, "folder": str(folder), "cost": {"usd": 0}}
        shutil.copy(self.out / "workflow.json", self.out / f"workflow.r{rnd}.json")
        self.versions.append({"round": rnd, "customers_passed": s["customers_passed"], "customers": s["customers"], "conversations_passed": s["passed"], "conversations": s["conversations"], "failed": [f["id"] for f in final if not f["pass"]]})
        self.steps.append({"step": f"platform test {rnd}", "seconds": round(time.time() - t), **self.versions[-1]})
        self.log("test", round=rnd, backend="platform", summary={k: v for k, v in s.items() if k != "rows"}, rows=rows)
        self.say(f"platform test {rnd}: {s['customers_passed']} / {s['customers']} customers with all {a.reps} runs passing ({s['passed']} / {s['conversations']} calls), {time.time() - t:.0f} s")
        return s

    async def test(self, rnd):
        """Run the test customers against the current workflow.json. Returns the simulator's summary, with its folder."""
        if self.a.test_on == "platform": return await self.test_platform(rnd)
        a, name, t = self.a, f"build-{self.slot}-r{rnd}", time.time()
        letters = [a.case_brief] if a.case_brief else sorted({c["brief"] for c in json.load(open(a.cases))})
        cmd = [sys.executable, str(SIM), "--cases", a.cases, "--api", str(self.work / self.api), "--reps", str(a.reps), "--concurrency", str(a.concurrency), "--name", name]
        for letter in letters: cmd += ["--workflow", f"{letter}={self.out / 'workflow.json'}"]
        if a.case_brief: cmd += ["--brief", a.case_brief]
        if a.case_level: cmd += ["--level", a.case_level]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        log = (await proc.communicate())[0].decode()
        folder = SIM.parent.parent / "results" / name
        (self.out / f"tests-{rnd}.log").write_text(log)
        s = json.loads((folder / "summary.json").read_text()); s["folder"] = str(folder)
        self.cost["tests_usd"] += (s.get("cost") or {}).get("usd") or 0
        shutil.copy(self.out / "workflow.json", self.out / f"workflow.r{rnd}.json")
        self.versions.append({"round": rnd, "customers_passed": s["customers_passed"], "customers": s["customers"], "conversations_passed": s["passed"], "conversations": s["conversations"],
                              "failed": [f["id"] for f in s["final"] if not f["pass"]]})
        self.steps.append({"step": f"test {rnd}", "seconds": round(time.time() - t), **self.versions[-1], "usd": (s.get("cost") or {}).get("usd")})
        self.log("test", round=rnd, summary={k: v for k, v in s.items() if k != "rows"}, rows=s["rows"])
        self.say(f"test {rnd}: {s['customers_passed']} / {s['customers']} customers with all {a.reps} runs passing ({s['passed']} / {s['conversations']} calls), {time.time() - t:.0f} s, ${(s.get('cost') or {}).get('usd', 0):.2f}")
        return s

    async def fuzz(self, rnd, folder=None):
        """The model-free test on the current workflow.json: random calls, stuck customers and, with the folder of a local test round, its calls replayed and disturbed.
        Writes fuzz-<round>.md for the repair turn. Returns (file name, number of defect groups); (name, 0) when it could not run."""
        t, name = time.time(), f"fuzz-{rnd}.md"
        cases = self.out / "cases.json"
        if self.a.cases and not cases.exists(): shutil.copy(self.a.cases, cases)
        cmd = [sys.executable, str(self.work / "fuzz_workflow.py"), str(self.out / "workflow.json"), str(self.work / self.api), "--runs", "400", "--md", str(self.out / name), "--json", str(self.out / f"fuzz-{rnd}.json")]
        if cases.exists(): cmd += ["--cases", str(cases)]
        if folder and self.a.test_on == "local": cmd += ["--recordings", folder]
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            await asyncio.wait_for(proc.communicate(), 300)
            r = json.loads((self.out / f"fuzz-{rnd}.json").read_text())
        except Exception as e:
            self.say(f"model-free test {rnd}: did not run ({type(e).__name__}: {str(e)[:160]})"); (self.out / name).write_text("The model-free test did not run.\n")
            return name, 0
        groups, kinds = len(r["groups"]), dict(r["kinds"])
        # two more model-free checks on the same version: the agent's own rule tests, and the brief's literal items against rules.md and the workflow
        extra = []
        st = self.out / "selftest.py"
        if st.exists():
            try:
                p2 = await asyncio.create_subprocess_exec(sys.executable, str(st), cwd=str(self.work), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                out2 = (await asyncio.wait_for(p2.communicate(), 60))[0].decode()
                if p2.returncode: groups += 1; kinds["SELFTEST"] = 1; extra += ["", "## SELFTEST: out/" + self.slot + "/selftest.py fails (the code does not do what rules.md says, or the test's own expected value is wrong: check against the brief)", out2[-3000:]]
            except Exception as e: extra += ["", f"## selftest.py did not run: {type(e).__name__}: {str(e)[:200]}"]
        else: kinds["NO_SELFTEST"] = 1
        try:
            import check_rules
            wfj = json.loads((self.out / "workflow.json").read_text())
            miss = check_rules.check([(self.work / b).read_text() for b in self.briefs], (self.out / "rules.md").read_text(), wfj)
            if miss: extra += ["", "## Brief items not found (words, not meaning: an item can be an example, or replaced by a later document; add what is a rule)"] + [f"- not in {where}: {k}: {show}" for k, show, where in miss]
            kinds["BRIEF_ITEMS_NOT_FOUND"] = len(miss)
        except Exception as e: extra += ["", f"## check_rules did not run: {type(e).__name__}: {str(e)[:200]}"]
        if extra:
            with (self.out / name).open("a") as f: f.write("\n".join(extra) + "\n")
        if self.versions and self.versions[-1]["round"] == rnd: self.versions[-1].update(fuzz_groups=groups, fuzz_kinds=kinds)
        self.steps.append({"step": f"fuzz {rnd}", "seconds": round(time.time() - t), "runs": r["runs"], "defect_runs": r["defect_runs"], "groups": groups, "kinds": kinds})
        self.log("fuzz", round=rnd, report=r)
        self.say(f"model-free test {rnd}: {r['runs']} runs, {groups} defect groups {kinds or ''}, {time.time() - t:.0f} s")
        return name, groups

    def failed_calls(self, s, rnd):
        """Write the failed calls of a test round as a file the repair turn reads."""
        cases = {c["id"]: c for c in json.load(open(self.a.cases))}
        lines = [f"# Failed test calls, round {rnd}", "", f"{s['customers_passed']} of {s['customers']} test customers passed all {self.a.reps} runs. A customer counts only when every run passes.",
                 "Each customer below failed at least one run. 'Bank records' lines come from a program and are exact. 'Judge' lines come from a model that read the transcript against the listed checks.", ""]
        bad = [f for f in s["final"] if not f["pass"]]
        for f in bad:
            c, shown = cases[f["id"]], False
            lines += [f"## {c['id']}: {c['title']} (passed {f['passes']} of {f['runs']} runs)", f"- What the customer does: {c['script']}", f"- Call variables: {json.dumps(c['variables'])}",
                      f"- Account: {json.dumps(c['products'])}", f"- Expected bank records: {json.dumps(c['expected'])}", "- The judge's checks: " + " | ".join(c["checks"]),
                      *([f"- A tool is made to fail: {json.dumps(c['faults'])}"] if c.get("faults") else []), *([f"- Way of talking: {c['style']}"] if c.get("style") else []), ""]
            for r in (r for r in s["rows"] if r["id"] == f["id"] and not r["pass"]):
                lines.append(f"### run {r['rep']}: ended with {r.get('ending')}")
                for reason in r["failed"]: lines.append(("- Judge: " + reason[len("conversation: "):]) if reason.startswith("conversation: ") else "- Bank records: " + reason)
                detail = pathlib.Path(s["folder"]) / f"{r['id']}-r{r['rep']}.json"
                if not shown and detail.exists():
                    d = json.loads(detail.read_text()); shown = True
                    lines += ["", "Transcript:", *d["transcript"], "", "Tool calls, in order:", *[f"{k['n']}. {k['tool']} {json.dumps(k['args'])} -> {k['status']} {json.dumps(k['result'])}" for k in d["ledger"]["calls"]]]
                lines.append("")
        path = self.out / f"tests-{rnd}.md"
        path.write_text("\n".join(lines))
        return path.name, len(bad)

    # ---- checkpoints of a repair turn (checkpoint.py in the work folder)
    async def checkpoint(self, s, reset=False):
        ck = self.out / "ckpt"
        if reset and ck.exists(): shutil.rmtree(ck)
        cmd = [sys.executable, "checkpoint.py", f"out/{self.slot}", self.api] + (["--recordings", s["folder"]] if s and self.a.test_on == "local" else [])
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(self.work), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            text = (await asyncio.wait_for(proc.communicate(), 120))[0].decode()
            self.say("checkpoint before the repair: " + text.strip().splitlines()[0][:160] if text.strip() else "checkpoint: no output")
        except Exception as e: self.say(f"checkpoint before the repair failed ({type(e).__name__}: {str(e)[:100]})")
        self.ck_base = self.ck_n()                                             # 0 when the version before the repair does not pass checkpoint.py itself

    def ck_n(self):
        try: return json.loads((self.out / "ckpt" / "state.json").read_text())["n"]
        except Exception: return 0

    def take_checkpoint(self):
        """Put the last checkpoint of the repair turn in place of the files as the turn left them. Returns how many checkpoints the turn saved."""
        ck, n = self.out / "ckpt", self.ck_n() - self.ck_base
        if n <= 0: return 0
        try: json.loads((ck / "workflow.json").read_text())
        except Exception: return 0
        shutil.copy(ck / "workflow.json", self.out / "workflow.json"); shutil.copy(ck / "gen.py", self.out / "gen.py")
        return n

    # ---- delivery
    def deliver(self):
        """Save the best tested version (the first one when nothing was tested) to Freya and export it."""
        import freya
        best = max(self.versions, key=lambda v: (v["customers_passed"], -v.get("fuzz_groups", 0), v["conversations_passed"], -v["round"])) if self.versions else None
        if best and best["round"] != self.versions[-1]["round"]:
            shutil.copy(self.out / f"workflow.r{best['round']}.json", self.out / "workflow.json")
            self.say(f"round {best['round']} scored best: it is delivered instead of the last round")
        for attempt in range(4):                                                 # the platform is sometimes busy for a while
            try:
                ids = freya.save_workflow(self.slot, json.loads((self.out / "workflow.json").read_text()))
                freya.export_workflow(ids["workflow_id"], str(self.out / "exported.workflow.json"))
                return ids, best
            except Exception as e:
                self.say(f"saving to Freya failed ({type(e).__name__}: {str(e)[:200]}), attempt {attempt + 1} of 4")
                if attempt == 3 or self.left() < 45:                             # the workflow is built and tested: it is delivered as a file even when the platform does not answer
                    self.say("NOT SAVED to Freya: out/<slot>/workflow.json is the result; save and export it when the platform answers")
                    return {"workflow_id": None, "agent_id": None, "saved": False}, best
                time.sleep(20)

    def publish(self):
        """Copy the run's files from the work folder to out/<slot> of the builder."""
        final = HERE / "out" / self.slot
        if final.exists(): shutil.rmtree(final)
        shutil.copytree(self.out, final)

    async def main(self):
        a, slot = self.a, self.slot
        options = ClaudeAgentOptions(
            cwd=self.work, model=os.environ.get("BUILDER_MODEL", "claude-opus-5-5"), skills=["build-phone-agent"], setting_sources=["project"],      # only this folder's settings
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"], permission_mode="dontAsk", max_turns=600,
            env={"BASH_DEFAULT_TIMEOUT_MS": "300000", "BASH_MAX_TIMEOUT_MS": "600000", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
        )
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if not self.api: await self.contract()
        self.log("start", start=self.started, slot=slot, inputs=a.inputs, model=options.model, cases=a.cases)
        writing = asyncio.create_task(self.write_cases()) if (not a.cases and a.write_cases and self.can_check) else None
        async with ClaudeSDKClient(options=options) as client:
            await self.turn(client, f"Use the build-phone-agent skill to build a phone agent. Slot: {slot}. "
                                    f"Brief documents, in reading order: {', '.join(self.briefs)}. Word, Excel and PDF files have a .txt copy; read that. Tool API file: {self.api}."
                                    + (f" The bank's services run at {a.mock}: the workflow calls this address and no other." if a.mock else ""), "build")
            if not (self.out / "workflow.json").exists(): sys.exit("the build step produced no workflow.json")
            if writing:
                a.cases = await writing
                if a.cases: a.case_brief, a.case_level = CASE_LETTER, None
            if self.can_check:
                readers = a.review and not a.cases                                      # measured: reader findings help only when there is no test customer
                if not readers: (self.out / "review.md").write_text("No reader findings.\n"); (self.out / "review.json").write_text("[]")
                rnd, (_, s, rejected) = 0, await asyncio.gather(self.review() if readers else asyncio.sleep(0), self.test(0) if a.cases else asyncio.sleep(0), self.validate())
                if rejected:
                    with (self.out / "review.md").open("a") as f: f.write(f"\n## R0 [high] (platform) The platform rejected workflow.json in a dry run\n- Error: {rejected}\n- Suggested fix: change gen.py so the file is accepted.\n")
                needs = REPAIR_NEEDS_PLATFORM if (a.cases and a.test_on == "platform") else REPAIR_NEEDS
                fuzz, n_fuzz = await self.fuzz(0, s["folder"] if s else None) if a.fuzz else ("(not run)", 0)
                best_score, stale = None, 0
                last_part = max(REPAIR_NEEDS_LATER, (1 - SAFE_PART) * BUDGET)          # no repair round starts in the last tenth of the budget: that time belongs to the final test and the delivery
                while not self.dead and self.left() > last_part and self.left() > (needs if rnd == 0 or a.test_on == "platform" else REPAIR_NEEDS_LATER) and rnd < a.max_repairs:
                    tests, n_bad = self.failed_calls(s, rnd) if s else ("", 0)
                    n_bad += n_fuzz                                                  # a defect of the model-free test is repaired like a failed customer
                    findings = json.loads((self.out / "review.json").read_text())
                    if rnd > 0 and not n_bad: self.say("every test customer passes and the model-free test is clean: done"); break
                    score = (self.versions[-1]["customers_passed"] if self.versions else 0, -n_fuzz)
                    if best_score is None or score > best_score: best_score, stale = score, 0
                    else: stale += 1
                    if stale >= STALE_ROUNDS: self.say(f"{STALE_ROUNDS} repair rounds in a row did not beat the best version ({best_score[0]} customers, {-best_score[1]} model-free groups): done"); break
                    # measured twice: a version whose test customers all pass got worse when it was repaired for reader findings alone (10/10 -> 8/10 on the platform)
                    if rnd == 0 and not n_bad and not rejected and (s or not any(f.get("severity") for f in findings if not f.get("error"))): break
                    rnd += 1
                    secs = max(min(int(self.left() - AFTER_REPAIR), REPAIR_TURN), 60)
                    (self.out / "deadline").write_text(str(time.time() + secs))      # checkpoint.py prints the time left from it
                    # measured with a bare "cut off after N minutes": the session stopped after 62 s of 4 minutes and after 23 s of 2 minutes, with failed customers left unread
                    limit = (f"\nTime: you have {secs // 60} min {secs % 60} s for this turn, and checkpoint.py prints what is left each time you run it. USE ALL OF IT. Being cut off costs nothing: the last checkpoint is what gets tested and delivered, "
                             f"so there is no reason to stop early or to skip an item because time might run out. Keep going, one fix and one checkpoint after the other, until every failed test customer in the file has been looked at or the time is up. "
                             f"Do not write a summary of what you did not get to.")
                    await self.checkpoint(s, reset=True)                             # the level a fix must not fall below
                    self.repair_secs = secs
                    cut = await self.turn(client, limit.strip() + "\n\n" + REPAIR.format(slot=slot, tests=tests or "(no test file)", round=rnd, fuzz=fuzz, api=self.api, recordings=f" --recordings {s['folder']}" if s and a.test_on == "local" else "",
                                                          no_tests="" if s else "\n   No test customers were given for this run, so only the readers' findings exist.") if rnd == 1 else
                                    f"The repaired workflow was tested again. Failed calls: out/{slot}/{tests}. Model-free test: out/{slot}/{fuzz} ({n_fuzz} defect groups; its recordings are now in {s['folder'] if s else '(none)'}). Same rules as before: decide per item whether it is a real defect, fix the mechanism in gen.py, "
                                    f"A customer that passed before your last repair and fails now is a regression: your last change broke it, so look at that change first. Before you finish, replay the recorded calls with --regression and lose none. "
                                    f"nothing that fits only one test customer, do not break what works, do not save. As before: one fix at a time, most costly first, and after each one  python3 checkpoint.py out/{slot} {self.api}{' --recordings ' + s['folder'] if s and a.test_on == 'local' else ''}  (only the last checkpoint is tested and delivered). One line per item in out/{slot}/repair-{rnd}.md. Finish with the line: REPAIRED round={rnd}" + limit, f"repair {rnd}", reserve=self.left() - self.repair_secs)
                    saved = self.take_checkpoint()                                   # the last state of this turn that checkpoint.py found sound
                    self.say(f"repair {rnd}: {saved} checkpoints" + (", cut off by the time limit" if cut else ""))
                    self.steps[-1]["checkpoints"] = saved
                    if cut and not saved:                                            # nothing sound was finished in time: the tested version before it stays, and no time goes into testing it again
                        if (self.out / f"workflow.r{rnd - 1}.json").exists(): shutil.copy(self.out / f"workflow.r{rnd - 1}.json", self.out / "workflow.json")
                        self.say(f"repair {rnd}: no checkpoint; round {rnd - 1} is kept"); break
                    prev = self.out / f"workflow.r{rnd - 1}.json"
                    if prev.exists() and prev.read_text() == (self.out / "workflow.json").read_text():      # measured: the same file tested again gave 20/28, then 18/28; a round that changed nothing is not tested
                        self.say(f"repair {rnd}: the workflow did not change; no new test"); break
                    if not a.cases:
                        if a.fuzz: await self.fuzz(rnd)
                        break
                    s = await self.test(rnd)                                         # every repair round is followed by the full test with simulated customers, then the model-free test
                    if a.fuzz and self.left() > REPAIR_NEEDS_LATER + 150: fuzz, n_fuzz = await self.fuzz(rnd, s["folder"])      # only when a further repair round can still use its findings
                    elif a.fuzz: self.say(f"model-free test {rnd}: skipped, {self.left():.0f} s left"); n_fuzz = 0
                else:
                    if rnd == 0: self.say(f"no repair round: {self.left():.0f} s of the budget are left")
                    elif rnd >= a.max_repairs: self.say(f"{rnd} repair rounds: the most that --max-repairs allows")
                    else: self.say(f"no further repair round: {self.left():.0f} s of the budget are left (no round starts in its last tenth)")
            else:
                self.say("no ANTHROPIC_API_KEY: review and tests skipped")
        ids, best = self.deliver()
        self.publish()
        total = round(sum(self.cost.values()), 2)
        report = {"slot": slot, "model": options.model, "seconds": round(time.time() - self.t0), "budget_seconds": BUDGET, "usd": total, "cost": {k: round(v, 2) for k, v in self.cost.items()},
                  "billed_to": "api" if os.environ.get("ANTHROPIC_API_KEY") else "subscription", "delivered_round": best["round"] if best else None, "versions": self.versions, "steps": self.steps, **ids}
        (self.out / "build-report.json").write_text(json.dumps(report, indent=1, default=str))
        self.log("done", report=report)
        try:
            import trace_text
            (self.out / "trace.txt").write_text(trace_text.text(self.started, report, a.inputs + ([a.mock] if a.mock else []) + ["--slot", slot], workflow=self.out / "workflow.json"))
        except Exception as e: self.say(f"trace.txt not written ({type(e).__name__}: {e})")
        self.publish()
        print(f"\ndone: {report['seconds']} s, ${total:.2f} at API prices (builder {self.cost['builder_usd']:.2f}, readers {self.cost['review_usd']:.2f}, tests {self.cost['tests_usd']:.2f})."
              + "".join(f"\n  round {v['round']}: {v['customers_passed']} / {v['customers']} test customers, {v['conversations_passed']} / {v['conversations']} calls" for v in self.versions)
              + f"\nTrace: {self.trace_path}\nRESULT slot={slot} workflow_id={ids['workflow_id']} agent_id={ids['agent_id']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="brief documents (files or folders), then the tool API file last")
    ap.add_argument("--slot", help="label of the agent; the same slot updates the same workflow. Default: first brief's file name")
    ap.add_argument("--cases", help="test customers in the kit's case format; without it only the readers check the workflow")
    ap.add_argument("--case-brief", help="use only the cases of this brief letter"); ap.add_argument("--case-level", help="use only cases of these levels, e.g. hard")
    ap.add_argument("--reps", type=int, default=3); ap.add_argument("--concurrency", type=int, default=30); ap.add_argument("--max-repairs", type=int, default=12, help="most repair rounds; the loop ends earlier when everything passes, when two rounds in a row bring nothing, or when the budget is used")
    ap.add_argument("--budget-minutes", type=float, help="time budget of the whole run in minutes (default 60, or BUILD_BUDGET_SECONDS)")
    ap.add_argument("--test-on", choices=["local", "platform"], default="local", help="where the test customers run: the local simulator, or the Freya platform itself (its engine, models and practice bank)")
    ap.add_argument("--no-write-cases", dest="write_cases", action="store_false", help="without --cases the builder writes its own test customers from the brief; this turns that off")
    ap.add_argument("--no-review", dest="review", action="store_false", help="do not start the four readers")
    ap.add_argument("--no-fuzz", dest="fuzz", action="store_false", help="do not run the model-free test (fuzz_workflow.py)")
    ap.add_argument("--review-severity", default="high", help="which reader findings reach the repair turn, comma separated (high, medium, low)")
    a = ap.parse_args()
    if a.budget_minutes: BUDGET = int(a.budget_minutes * 60)
    import re as _re
    a.mock = a.inputs.pop().rstrip("/") if a.inputs and _re.match(r"https?://", a.inputs[-1]) else None      # build.py {case_folder} {mock_url}
    if not a.inputs or (len(a.inputs) < 2 and not a.mock): sys.exit(__doc__)
    a.inputs = [str(pathlib.Path(p).resolve()) for p in a.inputs]      # the paths are relative to where the command was
    a.api_file = a.inputs[-1] if len(a.inputs) > 1 and is_tool_api(pathlib.Path(a.inputs[-1])) else None      # build.py {brief} ... {tool_api.json}
    a.docs = a.inputs[:-1] if a.api_file else a.inputs
    if a.cases: a.cases = str(pathlib.Path(a.cases).resolve())
    os.chdir(HERE); sys.path.insert(0, str(HERE))
    asyncio.run(Run(a).main())
