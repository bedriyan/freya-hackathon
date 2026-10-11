"""Does a file of test customers cover what the tool API file says can happen? No model, a few milliseconds.

    python3 check_cases.py <cases.json> <tool_api.json>

Counts, over the expected bank records of all cases: every allowed value of the closing call's outcome and reason, of a note's reason and
of a transfer's queue; customers with one, two and three products; call days; ways of talking; tool failures. Prints what is missing.
A value of the tool API file that the brief never leads to needs no case: the writer of the cases decides that, this program only lists it.
Exit code 1 when something is missing.
"""
import collections, json, sys

STYLES = ("pieces", "corrects", "talkative", "asks_back", "in_words")
NEED = {"id", "variables", "identity", "products", "script", "expected", "checks"}
PRACTICE = ("verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call")     # the services the local test bank copies


def enum(api, tool, field):
    for p, v in (api.get("paths") or {}).items():
        if p.rstrip("/").endswith("/" + tool):
            props = ((v.get("post") or {}).get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema") or {}).get("properties") or {}
            return list((props.get(field) or {}).get("enum") or [])
    return []


def service_gaps(cases, api):
    """The same count for a bank whose services are not the practice ones (cases in the shape of CASE-FORMAT-SERVICES.md)."""
    out, need = [], {"id", "variables", "script", "checks"}
    cases = [c for c in cases if isinstance(c, dict) and need <= set(c)]
    names = [next((p for p in reversed(path.strip("/").split("/")) if not p.startswith("{")), path) for path in (api.get("paths") or {})]
    seen = {k.get("tool") for c in cases for k in ((c.get("expected") or {}).get("calls") or [])}
    lost = [n for n in names if n not in seen]
    if lost: out.append(f"services: no case expects a call of {lost}. Write a case that leads to each, unless the documents never lead to it.")
    if len(cases) < 22: out.append(f"number: {len(cases)} cases, at least 22 are needed")
    styles = collections.Counter(c.get("style") for c in cases if c.get("style"))
    lost = [s for s in STYLES if not styles[s]]
    if lost: out.append(f"way of talking: no case has the style {lost}")
    if sum(styles.values()) < 6: out.append(f"way of talking: {sum(styles.values())} cases have a style, at least 6 are needed")
    if sum(bool(c.get("faults")) for c in cases) < 2: out.append("service failures: fewer than 2 cases with `faults`, on different services")
    if len({json.dumps(c["variables"], sort_keys=True) for c in cases}) < 4: out.append("test customers: fewer than 4 different sets of call variables; use more of the bank's listed test customers")
    return out, len(cases)


def gaps(cases, api):
    if not all(n in PRACTICE for n in (p.rstrip("/").split("/")[-1] for p in (api.get("paths") or {}))): return service_gaps(cases, api)
    out = []
    cases = [c for c in cases if isinstance(c, dict) and NEED <= set(c)]
    exp = [c["expected"] for c in cases]
    def missing(label, allowed, seen, hint):
        lost = [x for x in allowed if x not in seen]
        if lost: out.append(f"{label}: no case expects {lost}. {hint}")
    missing("closing outcome", enum(api, "close_call", "outcome"), {e.get("outcome") for e in exp}, "Write a case that ends with each, unless the brief never leads to it.")
    missing("closing reason", enum(api, "close_call", "reason"), {e.get("reason") for e in exp}, "Write a case for each row of the brief's outcome table that uses it.")
    missing("note reason", enum(api, "add_note", "reason"), {n.get("reason") for e in exp for n in e.get("notes") or []}, "Write a case in which a product ends with each.")
    missing("transfer queue", enum(api, "transfer_to_agent", "queue"), {t.get("queue") for e in exp for t in e.get("transfers") or []}, "Write a case that is handed to each.")
    n = collections.Counter(len(c["products"]) for c in cases)
    for k, least in ((1, 6), (2, 5), (3, 5)):
        if n[k] < least: out.append(f"products: {n[k]} cases with {k} product{'s' if k > 1 else ''}, at least {least} are needed (the scored customers have one to three)")
    multi = [c for c in cases if len(c["products"]) > 1]
    mixed = sum(1 for c in multi if c["expected"].get("promises") and c["expected"].get("notes"))
    if multi and mixed < 3: out.append(f"products: {mixed} cases in which one product gets a promise and another ends without one, at least 3 are needed")
    days = {str((c["variables"] or {}).get("now"))[:10] for c in cases}
    if len(days) < 4: out.append(f"call days: {len(days)} different days in `now`, at least 4 are needed (a Friday, a day before a holiday, a day late in the month)")
    styles = collections.Counter(c.get("style") for c in cases if c.get("style"))
    lost = [s for s in STYLES if not styles[s]]
    if lost: out.append(f"way of talking: no case has the style {lost}")
    if sum(styles.values()) < 8: out.append(f"way of talking: {sum(styles.values())} cases have a style, at least 8 are needed")
    if sum(bool(c.get("faults")) for c in cases) < 3: out.append("tool failures: fewer than 3 cases with `faults`, on different tools")
    no_account = sum(1 for e in exp if not e.get("promises") and not e.get("notes"))
    if no_account < 5: out.append(f"early endings: {no_account} cases end before any product is dealt with (wrong person, identity not passed, callback), at least 5 are needed")
    promised = sum(1 for e in exp if e.get("promises"))
    if promised < 8: out.append(f"promises: {promised} cases end with a promise, at least 8 are needed")
    return out, len(cases)


if __name__ == "__main__":
    if len(sys.argv) != 3: sys.exit(__doc__)
    g, n = gaps(json.load(open(sys.argv[1])), json.load(open(sys.argv[2])))
    for x in g: print("MISSING", x)
    print(f"\n{n} cases, {len(g)} gaps")
    sys.exit(1 if g else 0)
