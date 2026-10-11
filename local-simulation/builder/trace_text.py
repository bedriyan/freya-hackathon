"""The run's trace as plain text, for the submission: start and end time, each model call (model and purpose), what was created
on Freya, and every test the builder ran on its own agent. build.py writes it as out/<slot>/trace.txt at the end of a run.

    python3 trace_text.py traces/<slot>-<time>.jsonl [out/<slot>/trace.txt]      # the same text from a finished run's JSON trace
"""
import json, pathlib, sys, time

PURPOSE = {"build": "read the brief, write the rule list (rules.md), the generator (gen.py), its unit tests (selftest.py) and the workflow",
           "cases": "write test customers from the brief alone, in a separate session that never sees the agent"}


def shape(workflow):
    """Number of steps and the tool names of a workflow file, for the line that ties the agent to the run."""
    try:
        w = json.loads(pathlib.Path(workflow).read_text()); w = w.get("workflow", w); nodes = w.get("nodes") or {}
        tools = set()
        for n in nodes.values():
            for t in (n.get("tools") or []) + (n.get("pre_actions") or []) + (n.get("post_actions") or []):
                name = (t.get("function") or {}).get("name") or t.get("name")
                if name and name != "code_exec": tools.add(name)
        return f"{len(nodes)} steps, {len(tools)} tools ({', '.join(sorted(tools))})"
    except Exception as e: return f"shape not read ({type(e).__name__})"


def text(start, report, inputs, cases_model="claude-opus-5-5", sim=("claude-haiku-5-5", "claude-sonnet-5-5", "claude-sonnet-5-5"), workflow=None):
    t0 = time.mktime(time.strptime(start[:19], "%Y-%m-%dT%H:%M:%S"))
    at = lambda s: time.strftime("%H:%M:%S", time.localtime(t0 + s))
    L = [f"Builder run, slot {report['slot']}", f"start {start}", f"end   {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t0 + report['seconds']))}  ({report['seconds']} s = {report['seconds'] / 60:.1f} min)",
         f"time budget of the run: {report.get('budget_seconds', '?')} s (the repair loop ends when every test customer passes and the model-free test is clean, when two rounds in a row bring nothing, or when the budget is used)", f"command: python3 builder/build.py {' '.join(pathlib.Path(str(i)).name for i in inputs)}", "", "Steps, in the order they ended (seconds are the step's own duration; the test customers are written while the agent is built):"]
    for s in report["steps"]:
        k, sec = s["step"], s.get("seconds")
        if k == "cases": L.append(f"- model call, {cases_model}: {PURPOSE['cases']}. {s.get('cases')} customers, {sec} s.")
        elif k == "build": L.append(f"- model call, {report['model']} (Claude Agent SDK session, {s.get('turns')} turns): {PURPOSE['build']}. {sec} s.")
        elif k.startswith("repair"): L.append(f"- model call, {report['model']} (same session, {s.get('turns', '?')} turns): repair gen.py from the failed test calls and the model-free findings, one fix at a time with a checkpoint after each"
                                               f" ({s.get('checkpoints', 'n/a')} checkpoints{', cut off at the time limit' if s.get('interrupted') else ''}). {sec} s.")
        elif k.startswith("test"): L.append(f"- test on the builder's own agent, round {s.get('round')}: {s.get('conversations')} simulated calls in the local copy of the engine ({s.get('customers')} customers x 3). Model calls: agent {sim[0]} (plays the platform's agent model),"
                                             f" customer {sim[1]}, judge {sim[2]}. Result {s.get('customers_passed')} / {s.get('customers')} customers with all runs passing. {sec} s.")
        elif k.startswith("fuzz"): L.append(f"- model-free test, round {k.split()[-1]}: {s.get('runs')} runs of the workflow without any model (random calls, stuck customers, recorded calls replayed with one disturbance). {s.get('groups')} defect groups. {sec} s.")
        else: L.append(f"- {k}: {json.dumps({a: b for a, b in s.items() if a != 'step'}, default=str)}")
    L += ["", "On Freya:", "- POST /api/v2/workflow?dryRun=true: asked whether the platform accepts the workflow file (nothing saved).",
          f"- workflow {report.get('workflow_id')} (agent {report.get('agent_id')}) saved in slot {report['slot']} and published; exported to exported.workflow.json." + (f" The workflow has {shape(workflow)}." if workflow else "") if report.get("workflow_id") else "- NOT saved (the platform did not answer); workflow.json is the result.",
          "- No simulation was started on Freya by this run.", "",
          f"Delivered: round {report.get('delivered_round')} of " + ", ".join(f"round {v['round']}: {v['customers_passed']}/{v['customers']}" for v in report.get("versions") or []) + ".",
          f"Cost at API prices: ${report.get('usd')} ({json.dumps(report.get('cost'))}).", "The full trace with every message and tool call of the sessions is the JSON-lines file of the same run."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    start = report = inputs = None
    for line in open(sys.argv[1]):
        d = json.loads(line)
        if d["type"] == "start": start, inputs = d["start"], d["inputs"]
        if d["type"] == "done": report = d["report"]
    if not report: sys.exit("the trace has no 'done' record: the run did not finish")
    wf = pathlib.Path(sys.argv[2]).parent / "workflow.json" if len(sys.argv) > 2 else None
    out = text(start, report, inputs, workflow=wf if wf and wf.exists() else None)
    if len(sys.argv) > 2: pathlib.Path(sys.argv[2]).write_text(out)
    print(out)
