"""Fully local simulation: nothing on Freya is touched. Workflow file in, pass/fail per customer out.

    cd local-simulation && unset ANTHROPIC_API_KEY
    ../.venv/bin/python sim/local.py                                   # the 13 practice customers, once
    ../.venv/bin/python sim/local.py --reps 3 --concurrency 8          # a customer counts only when all 3 runs pass
    ../.venv/bin/python sim/local.py --cases cases/my-cases.json --workflow A=builder/out/brief-a/workflow.json

Pieces: sim/engine.py runs the workflow JSON (the phone agent's three model roles are played by --agent-model),
sim/bank.py is the bank, the customer and the judge use the platform's own prompts (prompts/), the ledger is scored
by the kit's tools/score.py. A case file has the shape of the kit's public-test-set.json, so new customers are new
entries in such a file: variables, identity, products, script, expected, checks, optional faults and style.
"""
import argparse, asyncio, json, pathlib, sys, time, uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
KIT = ROOT.parent / "kit" / "build-the-builder"
sys.path[:0] = [str(KIT), str(KIT / "tools"), str(ROOT / "sim")]
from score import score_ledger
from run_cases import persona_for, STYLES, PERSONA
import llm
from bank import Bank, RemoteBank, knows, score_calls
from engine import Engine

CUSTOMER_SYSTEM = (ROOT / "prompts" / "customer_system.txt").read_text().strip()
JUDGE = (ROOT / "prompts" / "judge.txt").read_text().strip()
JSON_ONLY = "Your whole answer is one JSON object with the keys reason, success and relevantMessages. No text before or after it, no code fence."
MAX_TURNS = 45
import os, random
CUT, cut_rng = float(os.environ.get("SIM_CUT", 0) or 0), random.Random(int(os.environ.get("SIM_CUT_SEED", 3)))


def slim(x):
    if isinstance(x, dict): return {k: slim(v) for k, v in x.items() if k not in ("variable_table", "header")}
    if isinstance(x, list): return [slim(v) for v in x[:12]]
    if isinstance(x, str) and len(x) > 500: return x[:500] + "…"
    return x


def judge_input(messages, criteria, ending):
    lines, ev, i = [], [], -1
    for m in messages:
        if m["role"] == "system":
            ev.append((f"[after Message {i}] " if i >= 0 else "[before Message 0] ") + json.dumps(slim(json.loads(m["message"])), ensure_ascii=False))
        else:
            i += 1
            lines.append(f"[Message {i}] {'Assistant' if m['role'] == 'assistant' else 'User'}: {m['message']}")
    return (JUDGE.replace("{success_criteria}", criteria).replace("{call_ending}", f"The call ended with reason: {ending}.")
            .replace("{numbered_transcript}", "\n".join(lines)).replace("{system_context}", "\n".join(ev))), lines


def persona_of(case, style=None):
    """The simulated customer of a case for a bank other than the practice one: who they are is the case's own `who` line."""
    who = case.get("who") or "You are the customer who called the bank."
    text = PERSONA.format(who=who, script=case["script"]) + ("\n\n" + STYLES[style] if style in STYLES else "")
    return {"name": case["id"] + " " + str(case.get("title", "")), "prompt": text, "successCriteria": "All of these are true:\n- " + "\n- ".join(case["checks"]), "tags": []}


async def run_case(case, rep, workflow, api, a, sem, outdir):
    row = {"id": case["id"], "rep": rep, "family": case.get("family", ""), "level": case.get("level", ""), "brief": case["brief"], "pass": False, "failed": [], "invalid": False}
    async with sem:
        t0 = time.time()
        try:
            style = a.style or case.get("style")
            local = knows(api)                                   # the seven services the local bank copies; any other bank is called at its own address
            persona = persona_for(case, {"identity": case["identity"]}, style) if local else persona_of(case, style)
            cid = "c_local_" + uuid.uuid4().hex[:10]
            bank = Bank(case, api, cid) if local else RemoteBank(case, api)
            calls = {"n": 0}
            async def ask(prompt, system):
                calls["n"] += 1
                return await llm.ask(a.agent_model, prompt, system=system, role="agent")
            eng = Engine(workflow, {**case["variables"], "customer_id": cid} if local else dict(case["variables"]), bank, ask)
            ending, said_bye, silent = None, False, 0
            async with llm.Chat(a.customer_model, CUSTOMER_SYSTEM + "\n\n" + persona["prompt"]) as customer:
                await eng.start()
                reply = eng.take_reply()
                for _ in range(MAX_TURNS):
                    if eng.ended: break
                    if said_bye: ending = "customer-ended-call"; break       # the customer said goodbye and the agent did not end the call
                    silent = silent + 1 if not reply.strip() else 0
                    if silent >= 2: ending = "inactivity-ended-call"; break
                    line = await customer.say(reply.strip() or "...")
                    said_bye = "goodbye" in line.lower()
                    # SIM_CUT=0.15: that share of the customer's longer messages reaches the agent cut off in the middle, as on the platform (the customer does not know; it repeats when asked)
                    if CUT and not said_bye and len(line.split()) >= 5 and cut_rng.random() < CUT: line = " ".join(line.split()[: cut_rng.randint(2, len(line.split()) - 1)])
                    await eng.user(line)
                    reply = eng.take_reply()
                else:
                    ending = "exceeded-max-duration"
            if not eng.ended: await eng.finish(ending or "exceeded-max-duration")
            ending = eng.end_reason
            prompt, transcript = judge_input(eng.messages, persona["successCriteria"], ending)
            raw = await llm.ask(a.judge_model, prompt, system=JSON_ONLY, role="judge")
            try: verdict = llm.parse_json(raw)
            except Exception: verdict = llm.parse_json(await llm.ask(a.judge_model, prompt + "\n\nAnswer with the JSON object only.", system=JSON_ONLY, role="judge"))
            led = bank.ledger()
            s = score_ledger(case, led) if local else score_calls(case, led)
            errors = [json.loads(m["message"]).get("message") for m in eng.messages if m["role"] == "system" and '"event": "error"' in m["message"]]
            row.update(ending=ending, turns=len(transcript), tools=[c["tool"] for c in led["calls"]], ledger_failed=s["failed"], agent_model_calls=calls["n"], engine_errors=errors,
                       judge_success=verdict.get("success"), judge_reason=verdict.get("reason"))
            row["failed"] = list(s["failed"]) + ([] if verdict.get("success") is True else ["conversation: " + " ".join(str(verdict.get("reason")).split())])
            row["pass"] = not row["failed"]
            (outdir / f"{case['id']}-r{rep}.json").write_text(json.dumps({"row": row, "persona": persona, "transcript": transcript, "verdict": verdict, "ledger": led,
                                                                          "judge_prompt": prompt, "messages": eng.messages, "variables": {k: v for k, v in eng.vars.items() if "." not in k}}, indent=1, ensure_ascii=False, default=str))
        except Exception as e:
            import traceback
            row.update(invalid=True, failed=[f"harness error: {type(e).__name__}: {str(e)[:300]}"], trace=traceback.format_exc()[-1500:])
        row["seconds"] = round(time.time() - t0)
    tag = "PASS" if row["pass"] else ("VOID" if row["invalid"] else "FAIL")
    chain = " > ".join(t.replace("verify_identity", "verify").replace("create_promise", "promise") for t in row.get("tools", [])) or "(no tool calls)"
    print(tag, f"{case['id']:<8}", f"r{rep}", f"{row['level']:<6}", f"{row['family']:<14}", f"{row['seconds']:>3}s", f"{row.get('ending') or '':<22}", "|", chain, flush=True)
    for f in row["failed"]: print("       -", f[:420], flush=True)
    return row


async def main(a):
    cases = [c for c in json.load(open(a.cases)) if (not a.brief or c["brief"] == a.brief) and (not a.only or c["id"] in a.only.split(",")) and (not a.level or c.get("level") in a.level.split(","))]
    paths = {"A": ROOT / "builder/out/brief-a/workflow.json", "B": ROOT / "builder/out/brief-b/workflow.json"}
    paths.update({k: pathlib.Path(v) for k, v in (x.split("=", 1) for x in a.workflow or [])})
    wfs = {b: json.loads(paths[b].read_text()) for b in sorted({c["brief"] for c in cases})}
    api = json.loads(pathlib.Path(a.api).read_text())
    outdir = ROOT / "results" / (a.name or "local-" + time.strftime("%Y%m%d-%H%M%S")); outdir.mkdir(parents=True, exist_ok=True)
    print(f"{len(cases)} customers x {a.reps}, backend {llm.BACKEND}, agent model {a.agent_model}, customer {a.customer_model}, judge {a.judge_model}\n", flush=True)
    sem, t0 = asyncio.Semaphore(a.concurrency), time.time()
    rows = await asyncio.gather(*(run_case(c, r + 1, wfs[c["brief"]], api, a, sem, outdir) for r in range(a.reps) for c in cases))
    by = {}
    for r in rows: by.setdefault(r["id"], []).append(r)
    final = [{"id": i, "passes": sum(x["pass"] for x in rs), "void": sum(x["invalid"] for x in rs), "runs": len(rs), "pass": all(x["pass"] for x in rs)} for i, rs in by.items()]
    if a.reps > 1:
        print()
        for f in final: print("PASS" if f["pass"] else "FAIL", f["id"], f"{f['passes']}/{f['runs']}" + (f" ({f['void']} void)" if f["void"] else ""))
    ok, void = sum(r["pass"] for r in rows), sum(r["invalid"] for r in rows)
    summary = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "conversations": len(rows), "passed": ok, "failed": len(rows) - ok - void, "void": void, "customers_passed": sum(f["pass"] for f in final), "customers": len(final),
               "seconds": round(time.time() - t0), "agent_model": a.agent_model, "customer_model": a.customer_model, "judge_model": a.judge_model, "style": a.style,
               "workflows": {k: str(v) for k, v in paths.items()}, "cost": llm.cost_report(), "final": final, "rows": rows}
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"\nconversations: {ok} / {len(rows)} passed, {len(rows) - ok - void} failed, {void} void (harness error). customers: {summary['customers_passed']} / {len(final)}"
          + (f" with all {a.reps} runs passing" if a.reps > 1 else "") + f". {summary['seconds']} s. Details: {outdir}")
    llm.print_cost(summary["cost"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default=str(KIT / "public-test-set.json")); ap.add_argument("--brief"); ap.add_argument("--only"); ap.add_argument("--level", help="only cases of these levels, comma separated (easy, medium, hard)"); ap.add_argument("--style", choices=sorted(STYLES))
    ap.add_argument("--workflow", action="append", help="BRIEF=path to a workflow JSON file. Default: builder/out/brief-a|b/workflow.json")
    ap.add_argument("--api", default=str(KIT / "tools" / "openapi.json"), help="the tool API file the local bank is built from")
    ap.add_argument("--agent-model", default="claude-haiku-5-5"); ap.add_argument("--customer-model", default="claude-sonnet-5-5"); ap.add_argument("--judge-model", default="claude-sonnet-5-5")
    ap.add_argument("--reps", type=int, default=1); ap.add_argument("--concurrency", type=int, default=6); ap.add_argument("--name")
    asyncio.run(main(ap.parse_args()))
