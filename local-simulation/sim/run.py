"""Local simulation: our own customer and judge, the phone agent on Freya, the bank's own ledger.

    cd local-simulation && source ../kit/build-the-builder/env.sh && unset ANTHROPIC_API_KEY
    ../.venv/bin/python sim/run.py [--brief A|B] [--only PUB-01,PUB-04] [--style talkative] [--workflow A=<workflow_id>] [--name run1]

What is the same as on the platform: the customer's two-layer prompt (prompts/customer_system.txt + the kit's persona
template), the judge's prompt (prompts/judge.txt), the ledger scorer (kit tools/score.py), the phone agent itself
(the published workflow, talked to turn by turn through /api/v2/chat/message), the practice bank.
What differs: the customer model (Haiku 5.5) and the judge model (Sonnet 5.5) instead of gemini-3-flash-preview;
the chat route needs a first customer line ("Hello?") before the agent's opening and skips an initial code step.
"""
import argparse, asyncio, json, os, pathlib, sys, time, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = ROOT.parent / "kit" / "build-the-builder"
sys.path[:0] = [str(KIT), str(KIT / "tools"), str(ROOT / "sim")]
import freya
from score import http, score_ledger
from run_cases import persona_for, STYLES
import llm

CUSTOMER_SYSTEM = (ROOT / "prompts" / "customer_system.txt").read_text().strip()
JUDGE = (ROOT / "prompts" / "judge.txt").read_text().strip()
CHAT_ONLY_EVENTS = {"turn_completed", "conversation_resumed"}      # bookkeeping of the chat route, absent in a simulation
MAX_TURNS = 45
# The platform asks the judge for structured output through the API. Here the same is asked in words; the judge's prompt itself is untouched.
JSON_ONLY = "Your whole answer is one JSON object with the keys reason, success and relevantMessages. No text before or after it, no code fence."


class PlatformError(RuntimeError):
    """The platform or the bank failed. The conversation says nothing about the phone agent."""


def post(path, body, timeout=150):
    req = urllib.request.Request(freya.URL + path, data=json.dumps(body).encode(), headers=freya.HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    return out.get("data", {}).get("result", out)


def get_call(call_id, tries=4):
    for i in range(tries):
        try: return freya.get_call(call_id)
        except Exception:
            if i == tries - 1: raise
            time.sleep(5)


def events(call):
    out = []
    for m in call.get("messages") or []:
        if m.get("role") == "system":
            try: out.append(json.loads(m["message"]))
            except Exception: pass
    return out


def agent_ended(call):
    return any(e.get("event") == "call_ended" or (e.get("event") == "function_call_start" and e.get("function") == "end_call") for e in events(call))


class PhoneAgent:
    """The published workflow on Freya, one conversation."""
    def __init__(self, workflow_id, variables):
        self.workflow_id, self.variables, self.call_id = workflow_id, variables, None

    def turn(self, text):
        body = {"workflowId": self.workflow_id, "message": text, "customerVariables": self.variables}
        if self.call_id:
            body["callId"] = self.call_id
            # The chat route continues from the last assistant message IT recorded as the end of a turn. When a turn ended
            # in engine-spoken lines (a retry chain, a closing chain) it has none, and silently rewinds to an older message
            # and an older step. Naming the real last assistant message keeps the call where it actually is.
            last = [m for m in (get_call(self.call_id).get("messages") or []) if m.get("role") == "assistant"]
            if last: body["fromMessageId"] = last[-1]["messageId"]
        for attempt in range(4):
            try:
                r = post("/api/v2/chat/message", body)
                self.call_id = r.get("callId") or self.call_id
                return r.get("response") or ""
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                code = getattr(e, "code", 0)
                if not self.call_id:                       # the call never started: starting again changes nothing
                    if code == 500: raise PlatformError("the workflow cannot start a call (HTTP 500)")
                    if attempt == 3: raise PlatformError(f"chat route unavailable: {code or e}")
                    time.sleep(20); continue
                for _ in range(8):                         # mid-call: never resend, wait for the turn to finish on the platform
                    time.sleep(8)
                    call = get_call(self.call_id)
                    msgs = [m for m in (call.get("messages") or []) if m.get("role") != "system"]
                    users = [i for i, m in enumerate(msgs) if m["role"] == "user" and m["message"] == text]
                    after = [m["message"] for m in msgs[users[-1] + 1:] if m["role"] == "assistant"] if users else []
                    if after: return " ".join(after)
                    if agent_ended(call): return ""
                raise PlatformError(f"chat turn failed mid-call: {code or e}")

    def end(self):
        if not self.call_id: return
        try: post("/api/v2/chat/end", {"callId": self.call_id}, timeout=60)
        except Exception: pass


def slim(x, depth=0):
    """Events as the judge needs them: no variable tables, long strings cut."""
    if isinstance(x, dict): return {k: slim(v, depth + 1) for k, v in x.items() if k not in ("variable_table", "workflowSnapshot", "header")}
    if isinstance(x, list): return [slim(v, depth + 1) for v in x[:12]]
    if isinstance(x, str) and len(x) > 500: return x[:500] + "…"
    return x


def judge_input(call, criteria, ending):
    lines, ev, i = [], [], -1
    for m in call.get("messages") or []:
        if m.get("role") == "system":
            try: e = json.loads(m["message"])
            except Exception: continue
            if e.get("event") in CHAT_ONLY_EVENTS: continue
            ev.append(f"[after Message {i}] " if i >= 0 else "[before Message 0] ")
            ev[-1] += json.dumps(slim(e), ensure_ascii=False)
        else:
            i += 1
            lines.append(f"[Message {i}] {'Assistant' if m['role'] == 'assistant' else 'User'}: {m['message']}")
    return (JUDGE.replace("{success_criteria}", criteria).replace("{call_ending}", f"The call ended with reason: {ending}.")
            .replace("{numbered_transcript}", "\n".join(lines)).replace("{system_context}", "\n".join(ev))), lines


async def run_case(case, workflow_id, a, sem, outdir):
    row = {"id": case["id"], "family": case["family"], "level": case["level"], "brief": case["brief"], "pass": False, "failed": [], "invalid": False}
    async with sem:
        t0 = time.time()
        try:
            persona = persona_for(case, {"identity": case["identity"]}, a.style)
            session = await asyncio.to_thread(http, "/v1/practice/sessions", {"case": case["id"]})
            if not session.get("variables"): raise PlatformError(f"the bank did not open a session: {str(session)[:200]}")
            variables = session["variables"]; row["customer_id"] = variables["customer_id"]
            agent = PhoneAgent(workflow_id, variables)
            ending, said_bye, silent = None, False, 0
            async with llm.Chat(a.customer_model, CUSTOMER_SYSTEM + "\n\n" + persona["prompt"]) as customer:
                reply = await asyncio.to_thread(agent.turn, "Hello?")
                for _ in range(MAX_TURNS):
                    row["call_id"] = agent.call_id
                    if agent_ended(await asyncio.to_thread(get_call, agent.call_id)): ending = "assistant-ended-call"; break
                    if said_bye: ending = "customer-ended-call"; break     # the customer said goodbye and the agent did not end the call
                    silent = silent + 1 if not reply.strip() else 0
                    if silent >= 2: ending = "inactivity-ended-call"; break
                    line = await customer.say(reply.strip() or "...")
                    said_bye = "goodbye" in line.lower()
                    reply = await asyncio.to_thread(agent.turn, line)
                else:
                    ending = "exceeded-max-duration"
                if ending is None: ending = "assistant-ended-call" if agent_ended(await asyncio.to_thread(get_call, agent.call_id)) else "exceeded-max-duration"
            await asyncio.to_thread(agent.end)
            await asyncio.sleep(3)
            call = await asyncio.to_thread(get_call, agent.call_id)
            blob = json.dumps(call).lower()
            row["sandbox_trouble"] = "snekbox" in blob
            prompt, transcript = judge_input(call, persona["successCriteria"], ending)
            raw = await llm.ask(a.judge_model, prompt, system=JSON_ONLY)
            try: verdict = llm.parse_json(raw)
            except Exception: verdict = llm.parse_json(await llm.ask(a.judge_model, prompt + "\n\nAnswer with the JSON object only.", system=JSON_ONLY))
            led = await asyncio.to_thread(http, "/v1/practice/ledger/" + variables["customer_id"])
            if "calls" not in led: raise PlatformError(f"the bank did not return the ledger: {str(led)[:200]}")
            s = score_ledger(case, led)
            row.update(ending=ending, turns=len(transcript), tools=[c["tool"] for c in led["calls"]], ledger_failed=s["failed"],
                       judge_success=verdict.get("success"), judge_reason=verdict.get("reason"))
            row["failed"] = list(s["failed"]) + ([] if verdict.get("success") is True else ["conversation: " + " ".join(str(verdict.get("reason")).split())])
            row["pass"] = not row["failed"]
            (outdir / f"{case['id']}.json").write_text(json.dumps({"row": row, "persona": persona, "transcript": transcript, "verdict": verdict, "ledger": led, "judge_prompt": prompt, "call": call}, indent=1, ensure_ascii=False, default=str))
        except PlatformError as e:
            row.update(invalid=True, failed=[f"platform: {e}"])
        except Exception as e:
            row.update(invalid=True, failed=[f"harness error: {type(e).__name__}: {str(e)[:300]}"])
        row["seconds"] = round(time.time() - t0)
    tag = "PASS" if row["pass"] else ("VOID" if row["invalid"] else "FAIL")
    chain = " > ".join(t.replace("verify_identity", "verify").replace("create_promise", "promise") for t in row.get("tools", [])) or "(no tool calls)"
    print(tag, case["id"], f"{case['level']:<6}", f"{case['family']:<14}", f"{row['seconds']:>3}s", f"{row.get('ending', ''):<22}", "|", chain, flush=True)
    for f in row["failed"]: print("       -", f[:420], flush=True)
    return row


async def main(a):
    cases = [c for c in json.load(open(a.cases)) if (not a.brief or c["brief"] == a.brief) and (not a.only or c["id"] in a.only.split(","))]
    state = json.loads(pathlib.Path(a.agents).read_text()) if pathlib.Path(a.agents).exists() else {}
    wf = {b: s["workflow_id"] for b, slot in (("A", "brief-a"), ("B", "brief-b")) if (s := state.get(slot))}
    wf.update(dict(x.split("=", 1) for x in a.workflow or []))
    missing = sorted({c["brief"] for c in cases} - set(wf))
    if missing: sys.exit(f"no workflow for brief {missing}: build it first or pass --workflow {missing[0]}=<workflow_id>")
    outdir = ROOT / "results" / (a.name or time.strftime("%Y%m%d-%H%M%S")); outdir.mkdir(parents=True, exist_ok=True)
    print(f"{len(cases)} customers, customer model {a.customer_model}, judge model {a.judge_model}, workflows {wf}\n", flush=True)
    sem, t0 = asyncio.Semaphore(a.concurrency), time.time()
    rows = await asyncio.gather(*(run_case(c, wf[c["brief"]], a, sem, outdir) for c in cases))
    ok, void = sum(r["pass"] for r in rows), sum(r["invalid"] for r in rows)
    summary = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "passed": ok, "failed": len(rows) - ok - void, "void": void, "total": len(rows), "seconds": round(time.time() - t0),
               "customer_model": a.customer_model, "judge_model": a.judge_model, "style": a.style, "workflows": wf, "rows": rows}
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"\n{ok} / {len(rows)} passed, {len(rows) - ok - void} failed, {void} void (platform trouble, not counted as a result). {summary['seconds']} s. Details: {outdir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(KIT / "public-test-set.json")); ap.add_argument("--brief"); ap.add_argument("--only"); ap.add_argument("--style", choices=sorted(STYLES))
    ap.add_argument("--agents", default=str(ROOT / "builder" / ".freya-agents.json"), help="state file written by the builder (slot -> workflow_id)")
    ap.add_argument("--workflow", action="append", help="BRIEF=workflow_id, overrides the state file, e.g. A=9cb72f80-…")
    ap.add_argument("--customer-model", default="claude-sonnet-5-5"); ap.add_argument("--judge-model", default="claude-sonnet-5-5")
    ap.add_argument("--concurrency", type=int, default=3); ap.add_argument("--name")
    asyncio.run(main(ap.parse_args()))
