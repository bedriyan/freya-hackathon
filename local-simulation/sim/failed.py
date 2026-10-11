"""The failed calls of one local test run as a text file, for the agent that repairs the workflow.

    python3 sim/failed.py results/<run name> <cases file> [> failed.md]

For every customer that failed at least one run: its script, the expected bank records, the judge's checks, the reason of every
failed run, and the transcript and tool calls of the first failed run.
"""
import json, pathlib, sys


def digest(folder, cases_file):
    folder = pathlib.Path(folder); s = json.loads((folder / "summary.json").read_text())
    cases = {c["id"]: c for c in json.load(open(cases_file))}
    reps = max(f["runs"] for f in s["final"])
    out = [f"# Failed test calls: {folder.name}", "", f"{s['customers_passed']} of {s['customers']} test customers passed all {reps} runs ({s['passed']} of {s['conversations']} calls). A customer counts only when every run passes.",
           "'Bank records' lines come from a program and are exact. 'Judge' lines come from a model that read the transcript against the listed checks.",
           "A test customer can be wrong too: check its expected records and checks against the brief before changing the workflow.", ""]
    for f in (f for f in s["final"] if not f["pass"]):
        c, shown = cases[f["id"]], False
        out += [f"## {c['id']}: {c.get('title', '')} (passed {f['passes']} of {f['runs']} runs)", f"- What the customer does: {c['script']}", f"- Call variables: {json.dumps(c['variables'])}",
                f"- Account: {json.dumps(c['products'])}", f"- Expected bank records: {json.dumps(c['expected'])}", "- The judge's checks: " + " | ".join(c["checks"]),
                *([f"- A tool is made to fail: {json.dumps(c['faults'])}"] if c.get("faults") else []), *([f"- Way of talking: {c['style']}"] if c.get("style") else []), ""]
        for r in (r for r in s["rows"] if r["id"] == f["id"] and not r["pass"]):
            out.append(f"### run {r['rep']}: ended with {r.get('ending')}")
            for reason in r["failed"]: out.append(("- Judge: " + reason[len("conversation: "):]) if reason.startswith("conversation: ") else "- Bank records / harness: " + reason)
            detail = folder / f"{r['id']}-r{r['rep']}.json"
            if not shown and detail.exists():
                d = json.loads(detail.read_text()); shown = True
                out += ["", "Transcript:", *d["transcript"], "", "Tool calls, in order:", *[f"{k['n']}. {k['tool']} {json.dumps(k['args'])} -> {k['status']} {json.dumps(k['result'])}" for k in d["ledger"]["calls"]]]
            out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) != 3: sys.exit(__doc__)
    print(digest(sys.argv[1], sys.argv[2]))
