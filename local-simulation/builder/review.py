"""Independent readers of a generated workflow. Each one gets the brief, the tool API file, the platform documentation and the
workflow JSON, looks at it from one angle and lists concrete defects. No call is simulated here.

    findings, cost = await review(slot_dir, brief_files, api_file)

Needs ANTHROPIC_API_KEY. The readers run in parallel on one cached copy of the documents.
"""
import asyncio, json, os, pathlib, re

import anthropic

HERE = pathlib.Path(__file__).resolve().parent
MODEL = os.environ.get("REVIEW_MODEL", "claude-opus-5-5")
PRICE = {"claude-opus-5-5": (4.00, 20.00, 0.20, 5.00), "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50)}     # $ per million tokens: input, output, cache read, cache write

SYSTEM = """You review a phone agent that was generated for a bank. The agent is a workflow JSON that a voice platform's engine runs: steps (nodes), tools, hooks, code steps, transitions on variables.
You are given the bank's brief, the tool API file, the platform's documentation and the workflow. You look at the workflow from ONE angle, named at the end, and list its defects from that angle.

A defect is a place where a call that follows the workflow can break a rule of the brief, or can end without exactly one closing call as its last tool call.
How calls are scored: a program compares the bank's ledger (closing outcome and reason, promises, notes, callbacks, transfers) with the expected one, and a judge model reads the transcript and the tool events against a few literal checks (an order, a count such as "asked exactly once", a required sentence, a forbidden word or offer, honesty after a failed tool). The same customer is run three times and must pass all three, so a rule the workflow leaves to the model's attention instead of the engine is a defect when the rule is counted or ordered.

Rules for your answer:
- Report only what you can point to in the workflow: name the step, tool, hook, transition or sentence. Trace the path a call takes. Do not report style, and do not report a risk you cannot tie to a rule of the brief.
- For every defect give the brief rule it breaks (number or quote), where it is, a short customer scenario that triggers it, and the smallest change of mechanism that fixes it.
- Severity: high = a plausible customer fails; medium = fails only with an unusual customer or sometimes; low = unlikely.
- At most 8 defects, most severe first. If you find none from your angle, return an empty list.

Your whole answer is one JSON object: {"findings": [{"severity": "high|medium|low", "rule": "...", "where": "...", "scenario": "...", "fix": "..."}]}. No text before or after it, no code fence."""

LENSES = {
    "coverage": "ANGLE: rule coverage. Go through the brief rule by rule, exceptions and 'unless' clauses and the closing table included. For each one find the step, variable, hook or sentence that carries it. Report rules that nothing carries, rules carried only partly, and exceptions inside a rule that have no path of their own (for example a branch that closes the call although the brief says a later statement of the customer changes the outcome).",
    "tools": "ANGLE: tools and closing. For every tool: can it be called before the brief allows it; can it be called twice where once is required; does every argument come from a validated or computed value; what happens on each failure (HTTP error, ok false) and does the brief's retry rule hold. Then for every way a call can end: is there exactly one closing call, with the outcome and reason the brief's table gives for that situation, as the last tool call, with the goodbye before it and no customer turn after the goodbye.",
    "counters": "ANGLE: counts and limits. List everything the brief counts or limits (questions asked once or twice, wrong answers, persuasion attempts, offers made once, sentences said once, retries). For each: is it counted by a variable that the engine changes, and does a transition on it stop the behaviour? Check what happens to each count when the customer does something else in between: asks a question back, repeats a request, changes the subject, gives a value in two parts, corrects a value. Report counts that the model has to keep in its head, counters that can be skipped or reset, and steps where the agent can repeat a counted question or offer.",
    "wording": "ANGLE: required and forbidden wording. List every sentence the brief requires (and when), and every word, topic or offer it forbids (and until when or to whom). For each required sentence: is it fixed text said by the engine at the right moment, complete, in one message, on every path that needs it, and never on a path that must not have it (for example after a failed tool)? For each forbidden item: read every prompt and fixed text of every step that can be reached before the event and report any that contains it or invites the model to say it. Also check that words claiming success (recorded, scheduled, transferred, confirmed) cannot be spoken before the tool returned success.",
}


def _usd(model, u):
    p = PRICE.get(model, PRICE["claude-opus-5-5"])
    return ((u.input_tokens or 0) * p[0] + (u.output_tokens or 0) * p[1] + (getattr(u, "cache_read_input_tokens", 0) or 0) * p[2] + (getattr(u, "cache_creation_input_tokens", 0) or 0) * p[3]) / 1e6


def _documents(slot_dir, brief_files, api_file):
    part = lambda title, text: f"<document name=\"{title}\">\n{text}\n</document>"
    docs = [part("platform documentation: " + f, (HERE / "docs" / f).read_text()) for f in ("workflow-format.md", "tools-and-code.md")]
    docs += [part("brief: " + pathlib.Path(f).name + (" (later documents override earlier ones)" if len(brief_files) > 1 else ""), pathlib.Path(f).read_text()) for f in brief_files]
    docs.append(part("tool API file", pathlib.Path(api_file).read_text()))
    docs.append(part("the generated workflow", (slot_dir / "workflow.json").read_text()))
    return "\n\n".join(docs)


async def review(slot_dir, brief_files, api_file, lenses=None, timeout=420):
    """Run the readers in parallel. Returns (findings sorted by severity, {"usd", "calls"})."""
    client = anthropic.AsyncAnthropic(max_retries=3, timeout=float(timeout))
    context = {"type": "text", "text": _documents(pathlib.Path(slot_dir), brief_files, api_file), "cache_control": {"type": "ephemeral"}}
    cost = {"usd": 0.0, "calls": 0, "model": MODEL}

    async def call(question, max_tokens):
        r = await client.messages.create(model=MODEL, max_tokens=max_tokens, system=SYSTEM, output_config={"effort": os.environ.get("REVIEW_EFFORT", "high")},
                                         messages=[{"role": "user", "content": [context, {"type": "text", "text": question}]}])
        cost["usd"] += _usd(MODEL, r.usage); cost["calls"] += 1
        return r

    try: await call("warm-up", 0)                 # writes the documents to the cache once, so the parallel readers read them instead of each writing a copy
    except Exception: pass

    async def one(name):
        try:
            r = await call(LENSES[name], 16000)
            text = "".join(b.text for b in r.content if b.type == "text")
            found = json.loads(re.search(r"\{.*\}", text, re.S).group(0)).get("findings") or []
            return [{"lens": name, **f} for f in found if isinstance(f, dict)]
        except Exception as e:
            return [{"lens": name, "severity": "info", "rule": "", "where": "", "scenario": "", "fix": "", "error": f"reader failed: {type(e).__name__}: {str(e)[:200]}"}]

    results = await asyncio.gather(*(one(n) for n in (lenses or LENSES)))
    order = {"high": 0, "medium": 1, "low": 2}
    findings = sorted((f for fs in results for f in fs), key=lambda f: order.get(f.get("severity"), 3))
    cost["usd"] = round(cost["usd"], 4)
    return findings, cost


def as_markdown(findings):
    lines = ["# Findings of the independent readers", "", "Each reader looked at the workflow from one angle without running it. A finding can be wrong: check it against the brief and the workflow before you change anything.", ""]
    for i, f in enumerate(findings, 1):
        if f.get("error"): lines.append(f"- ({f['lens']}) {f['error']}"); continue
        lines += [f"## R{i} [{f.get('severity')}] ({f['lens']}) {f.get('where', '')}", f"- Rule: {f.get('rule', '')}", f"- Scenario: {f.get('scenario', '')}", f"- Suggested fix: {f.get('fix', '')}", ""]
    return "\n".join(lines)


if __name__ == "__main__":       # python3 review.py <slot> <brief files...> <tool_api.json>
    import sys
    slot, briefs, api = HERE / "out" / sys.argv[1], sys.argv[2:-1], sys.argv[-1]
    found, c = asyncio.run(review(slot, briefs, api))
    (slot / "review.md").write_text(as_markdown(found)); (slot / "review.json").write_text(json.dumps(found, indent=1, ensure_ascii=False))
    print(as_markdown(found)); print(f"\n{len(found)} findings, {c['calls']} calls, ${c['usd']}")
