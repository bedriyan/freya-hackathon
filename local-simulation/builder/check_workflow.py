"""Static checks on a workflow file before it is saved. Catches what the platform drops or accepts silently.

    python3 check_workflow.py out/<slot>/workflow.json <tool_api.json> [--no-side-questions]

This reads the file. fuzz_workflow.py runs it (without a model) and finds what only shows when a call is played: a question
asked again without end, a call that never closes, a record the bank refuses.

--no-side-questions: only for a brief that gives no answer for any question a customer may ask in between.

Exit code 0 when there are no errors. Warnings do not fail the check.
"""
import json, re, sys

DETERMINISTIC = {"equals", "not_equals", "greater_than", "less_than", "contains", "is_set", "is_not_set", "always"}
NODE_TYPES = {"conversation", "api_request", "code", "end_call"}


def walk(x, path=""):
    yield path, x
    if isinstance(x, dict):
        for k, v in x.items(): yield from walk(v, f"{path}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x): yield from walk(v, f"{path}[{i}]")


def tool_name(t): return (t.get("function") or {}).get("name") or t.get("name")


BLOCKS_URL = re.compile(r"""config\s*\[\s*['"]url['"]\s*\]\s*=\s*(''|""|None\b)|del\s+config\s*\[\s*['"]url['"]\s*\]|config\.pop\(\s*['"]url['"]""")
SETS_LITERAL = re.compile(r"""vars\s*\[\s*['"]([\w.]+)['"]\s*\]\s*=\s*(['"])(.*?)\2\s*(?:$|[#;\n)])""", re.M)
BROKEN_OFF, SIDE_PREFIX = "broken_off", "side_q"


def check(wf, api, side_questions=True):
    err, warn = [], []
    wf = wf.get("workflow", wf)
    nodes = wf.get("nodes") or {}
    api_tools = {p.rstrip("/").split("/")[-1]: v for p, v in (api.get("paths") or {}).items()}
    for p, v in walk(wf):
        if v is None: err.append(f"null value at {p}: leave the key out or use an empty string")
    if "agent_config" in wf: err.append("agent_config must be left out")
    init = wf.get("initial_node")
    if init not in nodes: err.append(f"initial_node {init!r} is not a node")
    elif nodes[init].get("node_type") == "code": err.append("initial_node is a code node: the chat test route skips it, so its variables are empty there")
    used_tools = set()
    # every variable name that something in the workflow can set: extraction variables, names written in code and hooks, call variables
    blob_all = json.dumps(wf)
    set_vars = {e.get("name") for n in nodes.values() for e in (n.get("extraction_schema") or [])} | set(re.findall(r"vars\[\\?['\"]([\w.]+)\\?['\"]\]\s*=[^=]", blob_all)) | set((wf.get("test_variables") or {}))
    # code and hooks: two things the platform does not do what the code expects
    enums = {}
    for n in nodes.values():
        for e in n.get("extraction_schema") or []:
            if e.get("enum_values"): enums.setdefault(e.get("name"), set()).update(e["enum_values"])
    for p, v in walk(wf):
        if not isinstance(v, str) or "vars" not in v and "config" not in v: continue
        if BLOCKS_URL.search(v):
            err.append(f"code at {p} empties config['url'] to stop a call. On the platform that call fails with 'Missing required field: url', the post hook does not run, the sentence it prepared is never said and the model "
                       "invents one ('I have a technical issue'). A hook cannot stop a call. Decide before the call: extract the value, check it in a code step, and let the engine make the call only when the value is complete and allowed")
        for m in SETS_LITERAL.finditer(v):
            name, lit = m.group(1), m.group(3)
            if name in enums and lit not in enums[name]:
                err.append(f"code at {p} sets the extraction variable {name!r} to {lit!r}, which is not one of its values {sorted(enums[name])}. The platform rejects that write and the old value stays, "
                           "so a transition can fire again on a stale value. Reset it to a neutral member of the enum (add one, e.g. 'none')")
    for key, n in nodes.items():
        t = n.get("node_type")
        if t == "conversation" and not n.get("passthrough"):
            node_enums = [e for e in n.get("extraction_schema") or [] if e.get("enum_values")]
            if node_enums and not any(BROKEN_OFF in e["enum_values"] for e in node_enums):
                err.append(f"{key}: no reply-kind value has the member {BROKEN_OFF!r}. A customer message can stop in the middle of a sentence; without this member it is read as a refusal, a wrong answer or a request and uses up an attempt")
            if node_enums and side_questions and not any(str(e.get("name", "")).startswith(SIDE_PREFIX) for e in node_enums):
                err.append(f"{key}: no extraction variable named {SIDE_PREFIX}* here. A question the brief gives an answer for (a robot, a scam, ...) can come in the same message as the answer in any step; "
                           "extract it apart from the reply-kind value and say the brief's answer in front of the next sentence")
        if n.get("name") != key: err.append(f"{key}: name {n.get('name')!r} differs from its key")
        if t not in NODE_TYPES: err.append(f"{key}: node_type {t!r} is not one of {sorted(NODE_TYPES)}")
        if n.get("tools") and t != "conversation": err.append(f"{key}: tools are kept only on conversation nodes")
        if n.get("pre_actions") and t not in ("api_request", "code"): err.append(f"{key}: pre_actions are kept only on api_request and code nodes")
        trs = n.get("transitions") or []
        if t == "end_call":
            if not n.get("terminal"): err.append(f"{key}: end_call node without terminal")
            continue
        if not trs and t != "conversation": err.append(f"{key}: no transitions, the call can never leave this node")
        for i, tr in enumerate(trs):
            tgt = tr.get("target_node")
            if tgt not in nodes and tgt != "__previous__": err.append(f"{key}: transition {i} goes to unknown node {tgt!r}")
            c = tr.get("condition") or {}
            if "logic" in c and c["logic"] != "or": err.append(f"{key}: transition {i} has top-level logic {c['logic']!r}; the top level must be 'or'")
            for p, v in walk(c):
                if isinstance(v, dict) and v.get("operator") == "equals" and str(v.get("operand")).lower() in ("true", "false") and not isinstance(v.get("operand"), str):
                    err.append(f"{key}: transition {i} compares with a boolean; compare with the strings 'true' and 'True' (or a string flag)")
        if n.get("passthrough"):
            last = (trs[-1].get("condition") or {}).get("operator") if trs else None
            if last != "always": err.append(f"{key}: passthrough node must end with an 'always' transition or it can lock")
            if any((tr.get("condition") or {}).get("operator") == "matches_intent" for tr in trs): err.append(f"{key}: passthrough node with matches_intent (there is no customer message to match)")
        for i, tr in enumerate(trs):                                   # a comparison with a value the variable can never hold never fires
            for p, v in walk(tr.get("condition") or {}):
                if isinstance(v, dict) and v.get("operator") in ("equals", "not_equals") and v.get("variable") in enums and isinstance(v.get("operand"), str) and v["operand"] not in enums[v["variable"]]:
                    err.append(f"{key}: transition {i} compares the extraction variable {v['variable']!r} with {v['operand']!r}, which is not one of its values {sorted(enums[v['variable']])}: it can never match")
        if t == "conversation" and not n.get("passthrough") and trs and all((tr.get("condition") or {}).get("operator") == "always" for tr in trs if not tr.get("is_tool_call")):
            warn.append(f"{key}: every customer message leaves this step (an 'always' transition), so max_node_turns never counts here: the step's turn counter starts again on each entry. "
                        "Nothing in the engine then stops a customer who keeps giving an answer the code cannot use. Count in code: every sentence that asks again has its own counter and an exit to one of the brief's closings, "
                        "and one counter of all customer turns ends the call through a closing when it passes a limit. fuzz_workflow.py tests this")
        if n.get("max_node_turns") and not n.get("max_turn_transition") and t == "conversation": warn.append(f"{key}: max_node_turns without max_turn_transition changes nothing")
        if t == "conversation" and not n.get("max_turn_transition"): warn.append(f"{key}: no max_turn_transition, a stuck customer keeps the call here until the time limit")
        for tl in n.get("tools") or []:
            nm = tool_name(tl); used_tools.add(nm)
            if (tl.get("function") or {}).get("handler") == "__function__:api_call" and nm not in api_tools: err.append(f"{key}: tool {nm!r} is not in the tool API file")
            if (tl.get("function") or {}).get("handler") == "__function__:api_call":
                body = ((((tl.get("function") or {}).get("parameters") or {}).get("properties") or {}).get("body") or {}).get("properties") or {}
                filled = [f for f, sp in body.items() if isinstance(sp, dict) and isinstance(sp.get("default"), str) and (mm := re.fullmatch(r"\{\{\s*([\w.]+)\s*\}\}", sp["default"].strip())) and mm.group(1) not in set_vars]
                if filled: warn.append(f"{key}: the model itself sends {filled} of tool {nm!r}. It will also send half a value, a guessed value or a value in the wrong order, and a hook cannot stop the call. "
                                       "Where the brief has any rule about such a value, extract it, check it in a code step and let the engine make the call (an api_request step)")
                for field, spec in body.items():
                    d = spec.get("default") if isinstance(spec, dict) else None
                    if d is None: err.append(f"{key}: tool {nm!r} body field {field!r} has no default. On the platform such a field never reaches the request and the call fails. Give it a placeholder default such as \"{{{{{nm}_{field}_in}}}}\"")
                    elif isinstance(d, str) and (m := re.fullmatch(r"\{\{\s*([\w.]+)\s*\}\}", d.strip())) and m.group(1) in set_vars and field not in ("customer_id",):
                        warn.append(f"{key}: tool {nm!r} body field {field!r} takes its value from the variable {m.group(1)!r}, which an extraction, code or a call variable also sets: the model will not fill it. Fine if intended, otherwise use a placeholder name nothing else sets")
        for e in n.get("extraction_schema") or []:
            if "min_confidence" not in e: warn.append(f"{key}: extraction variable {e.get('name')!r} has no min_confidence (default 0.95 drops many values)")
            if e.get("extraction_prompt") and "{{extractor.speech}}" not in e["extraction_prompt"]: err.append(f"{key}: extraction_prompt of {e.get('name')!r} lacks {{{{extractor.speech}}}}, the extractor would see nothing")
    # an enum member that no code and no transition ever names falls into whatever the default branch does
    consts = set(re.findall(r"[A-Za-z_][\w-]*", "\n".join(v for p, v in walk(wf) if isinstance(v, str) and ("code" in p or "operand" in p))))
    for name, members in sorted(enums.items()):
        lost = sorted(m for m in members if m not in consts)
        if lost: warn.append(f"extraction variable {name!r}: the values {lost} appear in no code and no transition, so such a reply is handled by a default branch. Handle each value, or take it out of the enum")
    # the platform runs code inside a function: `global` then names another object than the top-level one (seen: the saved state lost its keys, 27 of 30 calls failed)
    import ast as _ast
    for path, v in walk(wf):
        if isinstance(v, str) and "code" in path.lower() and "global " in v:
            try: bad = sorted({n2 for n in _ast.walk(_ast.parse(v)) if isinstance(n, _ast.Global) for n2 in n.names})
            except SyntaxError: bad = []
            if bad: err.append(f"{path}: the code uses `global` for {bad}. The platform runs this code inside a function, so a top-level `x = ...` and a `global x` in a function are two different objects there. "
                               f"Keep state in one dict made at the top and only change it in place (d['k'] = v, d.clear(), d.update(...)); never rebind a top-level name inside a function")
    # the platform's sandbox drops single code runs: the step then leaves by its default with nothing written (seen: two of thirty calls closed after the greeting)
    for k, n in nodes.items():
        if n.get("node_type") != "code": continue
        chain, cur = [k], n.get("passthrough_default_transition")
        while cur in nodes and nodes[cur].get("node_type") == "code" and cur not in chain: chain.append(cur); cur = nodes[cur].get("passthrough_default_transition")
        first = not any(k == m.get("passthrough_default_transition") for m in nodes.values() if m.get("node_type") == "code")      # not itself a retry copy
        if first and len(chain) < 3:
            err.append(f"code step {k!r}: when the sandbox fails its code does not run and the step goes to {cur!r} after {len(chain) - 1} retries. Give it two retry copies "
                       f"(same code, same transitions): {k} -> default -> copy 1 -> default -> copy 2 -> default -> {cur!r}")
    # every node must be reachable and must be able to reach an end_call node
    out = {k: {tr.get("target_node") for tr in (n.get("transitions") or [])} | ({n["max_turn_transition"]} if n.get("max_turn_transition") else set())
           | ({n["passthrough_default_transition"]} if n.get("passthrough_default_transition") else set()) for k, n in nodes.items()}
    glob = wf.get("global_nodes") or {}
    glob_keys = set(glob) if isinstance(glob, dict) else {g.get("name") for g in glob}
    seen, todo = set(), [init] if init in nodes else []
    while todo:
        k = todo.pop()
        if k in seen or k not in nodes: continue
        seen.add(k); todo += list(out[k])
    for k in nodes:
        if k not in seen and k not in glob_keys: err.append(f"{k}: not reachable from the initial node")
    ends = {k for k, n in nodes.items() if n.get("node_type") == "end_call"
            or any((tl.get("function") or {}).get("handler") == "__function__:end_call" for tl in n.get("tools") or [])}
    if not ends: err.append("no end_call node and no end_call tool")
    ok = set(ends); changed = True
    while changed:
        changed = False
        for k in nodes:
            if k not in ok and out[k] & ok: ok.add(k); changed = True
    for k in nodes:
        if k not in ok: err.append(f"{k}: no path from here to an end_call node")
    # the closing tool of the bank must be reachable as an engine-run call or a tool
    blob = json.dumps(wf)
    for must in ("close_call",):
        if must in api_tools and f"/{must}" not in blob: err.append(f"{must} is never called")
    for nm in api_tools:
        if f"/{nm}" not in blob: warn.append(f"tool {nm} from the tool API file is not used anywhere")
    base = ((api.get("servers") or [{}])[0].get("url") or "").rstrip("/")
    for url in set(re.findall(r'https?://[^"\\\s]+', blob)):
        if base and not url.startswith(base): err.append(f"call to {url}: only the bank's tool API ({base}) may be called")
    return err, warn


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(args) != 2: sys.exit(__doc__)
    e, w = check(json.load(open(args[0])), json.load(open(args[1])), side_questions="--no-side-questions" not in sys.argv)
    for x in w: print("warning:", x)
    for x in e: print("ERROR:", x)
    print(f"\n{len(e)} errors, {len(w)} warnings")
    sys.exit(1 if e else 0)
