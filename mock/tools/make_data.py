"""Write src/data.js for the bank mock from a service contract and a customer list.

Usage (from mock/):
    ../.venv/bin/python tools/make_data.py --api <openapi.json> --customers <customers.json | customers.xlsx> [--out src/data.js]
                                           [--limit N] [--max-wrong N] [--version TEXT] [--sheet NAME]

Example (the bank the repo ships with):
    ../.venv/bin/python tools/make_data.py --api ../local-simulation/blind/s02/openapi.json \
        --customers ../local-simulation/blind/s02/cases.json --limit 6

What it does mechanically
  * services: one per POST path of the tool API file; request fields, required flags, types and enums from its JSON schema.
    The seven known names get their behaviour ("kind"); any other path becomes a plain recorder with a TODO.
  * identity: the fields are the enum of the verify service's field parameter; a right answer shaped YYYY-MM-DD makes the field a date.
    The wrong-answer limit comes from --max-wrong, else the customers' "bank.max_wrong", else the words of the summary
    ("Two wrong answers ..."), else 3 with a TODO. Hint rules come from the customers' "bank.hints"/"bank.swap_hint".
  * customers:
      JSON  a list (or {"cases": [...]} / {"customers": [...]}) of records in our case format:
            {"id" or "customer_id", "variables": {...}, "identity": {field: answer}, "products": [{...}], "faults": [{"tool", "fail_first_n"}]}
      XLSX  first sheet (or --sheet), first row = headers, one row per PRODUCT (rows with the same customer id are one customer).
            A column named customer_id / id / customer is the key; columns named like an identity field are the right answers;
            columns named like a product property of get_account's answer are the product; the rest go to "variables".
            Excel drops leading zeros of numbers: check the identity answers by eye.
Everything a human must decide is marked TODO in the written file and repeated on stderr. After writing: node test/run.mjs
"""
import argparse, datetime, json, pathlib, re, sys

KINDS = {"verify_identity": "verify", "get_account": "account", "create_promise": "promise", "add_note": "record",
         "schedule_callback": "record", "transfer_to_agent": "record", "close_call": "close"}
LISTS = {"create_promise": "promises", "add_note": "notes", "schedule_callback": "callbacks", "transfer_to_agent": "transfers"}
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
TODOS = []


def todo(text):
    TODOS.append(text); return "TODO: " + text


def schema_of(op, kind="request"):
    if kind == "request": return op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {}) or {}
    return (op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})) or {}


def load_customers(path, sheet, identity_fields, product_fields):
    p = pathlib.Path(path)
    if p.suffix.lower() == ".json":
        d = json.load(open(p))
        if isinstance(d, dict): d = d.get("cases") or d.get("customers") or list(d.values())
        return d
    import openpyxl
    wb = openpyxl.load_workbook(p, data_only=True); ws = wb[sheet] if sheet else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h).strip() if h is not None else "" for h in rows[0]]
    key = next((h for h in head if h.lower() in ("customer_id", "id", "customer", "customerid")), None)
    if not key: sys.exit(f"no customer id column among {head}")
    def clean(v):
        if isinstance(v, (datetime.datetime, datetime.date)): return v.strftime("%Y-%m-%d")
        if isinstance(v, float) and v.is_integer(): return int(v)
        return v.strip() if isinstance(v, str) else v
    out = {}
    for r in rows[1:]:
        row = {h: clean(v) for h, v in zip(head, r) if h and v not in (None, "")}
        if key not in row: continue
        c = out.setdefault(str(row[key]), {"customer_id": str(row[key]), "variables": {}, "identity": {}, "products": []})
        prod = {}
        for h, v in row.items():
            if h == key: continue
            if h in identity_fields: c["identity"][h] = str(v)
            elif h in product_fields and h != "product_id": prod[h] = v
            else: c["variables"][h] = v
        if prod: c["products"].append(prod)
    todo("customers came from a spreadsheet: check identity answers (leading zeros, date format) and that every product column was recognised")
    return list(out.values())


def build(api, customers, limit=None, max_wrong=None, version=None):
    base = "/" + (api.get("servers") or [{}])[0].get("url", "/v1").rstrip("/").split("/")[-1]
    if not re.fullmatch(r"/v\d+", base): base = "/v1"; todo("base path guessed as /v1, check the contract's server URL")
    services, verify_name = {}, None
    for path, item in (api.get("paths") or {}).items():
        op = item.get("post")
        if not op: todo(f"path {path} has no POST and was skipped"); continue
        name = path.strip("/").split("/")[-1]; sch = schema_of(op); req = sch.get("required", [])
        fields = {}
        for f, spec in (sch.get("properties") or {}).items():
            if f == "customer_id": continue
            e = {"required": f in req, "type": "date" if spec.get("format") == "date" else spec.get("type", "string")}
            if spec.get("enum"): e["enum"] = spec["enum"]
            fields[f] = e
        kind = KINDS.get(name)
        svc = {"path": path, "kind": kind or "record", "summary": op.get("summary", ""), "fields": fields}
        dates = [f for f, e in fields.items() if e["type"] == "date"]
        if kind is None:
            svc["list"] = name; svc["response"] = {"ok": True}
            svc["TODO"] = todo(f"service {name} is not one of the seven known ones: it only records its calls in the ledger list '{name}'. Decide kind/response.")
        elif kind == "verify":
            verify_name = name
            svc["response"] = {"ok": True, "verified": "$verified", "wrong_answers_left": "$wrong_left", "all_verified": "$all_verified"}
            svc["hintResponse"] = {**svc["response"], "hint": "$hint"}
        elif kind == "account":
            svc.update(requiresVerified=True, response={"ok": True, "products": "$products"})
        elif kind == "promise":
            svc.update(requiresVerified=True, list=LISTS[name], productRef="product_id", idKey="promise_id", idPrefix="pr", copyFromProduct=["type"],
                       onePerProduct=True, stringify=dates, response={"ok": True, "promise_id": "$id"})
        elif kind == "record":
            svc.update(requiresVerified=False, list=LISTS[name], stringify=dates + [f for f in ("time",) if f in fields], response={"ok": True})
        elif kind == "close":
            svc.update(store=[f for f in ("outcome", "reason") if f in fields], response={"ok": True})
            if "outcome" not in fields: todo("close service has no 'outcome' field: set services.<close>.store to the fields the ledger must keep")
        services[name] = svc
    for missing in [n for n in KINDS if n not in services]: todo(f"the contract has no {missing} service")

    # identity
    vf = services.get(verify_name, {}).get("fields", {})
    field_param = next((f for f, e in vf.items() if e.get("enum")), "field")
    answer_param = next((f for f in vf if f != field_param), "answer")
    id_fields = list(vf.get(field_param, {}).get("enum") or [])
    customers = customers[:limit] if limit else customers
    banks = [c.get("bank") or {} for c in customers]
    if max_wrong is None:
        seen = {b["max_wrong"] for b in banks if "max_wrong" in b}
        m = re.search(r"\b(one|two|three|four|five|six|\d+) wrong answers?", services.get(verify_name, {}).get("summary", ""), re.I)
        if len(seen) == 1: max_wrong = seen.pop()
        elif m: max_wrong = WORDS.get(m.group(1).lower()) or int(m.group(1))
        else: max_wrong = 3; todo("wrong-answer limit not found, set identity.maxWrong (3 assumed)")
        if len(seen) > 1: todo(f"customers disagree on max_wrong {sorted(seen)}: the engine has one limit for all")
    pass_at = {b.get("pass_at") for b in banks if b.get("pass_at")}
    hint_enum = (schema_of((api["paths"].get(services[verify_name]["path"]) or {}).get("post", {}), "response").get("properties", {}).get("hint", {}).get("enum") or []) if verify_name else []
    rules = {}
    for f in id_fields or sorted({k for c in customers for k in (c.get("identity") or {})}):
        answers = [str((c.get("identity") or {}).get(f, "")) for c in customers if f in (c.get("identity") or {})]
        rule = {}
        if answers and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", a) for a in answers):
            swaps = {b.get("swap_hint", "day_month_swapped") for b in banks} or {"day_month_swapped"}
            rule = {"type": "date", "swapHint": sorted(swaps, key=str)[0] if len(swaps) == 1 else None}
            if len(swaps) > 1: todo(f"customers disagree on swap_hint for {f}")
        else:
            rule = {"type": "text", "normalise": {"remove": "[\\s-]", "case": "upper"}}
        hs = {json.dumps(b["hints"][f], sort_keys=True) for b in banks if f in (b.get("hints") or {})}
        rule["hints"] = [json.loads(h) for h in sorted(hs)]
        rules[f] = rule
    used = {h["hint"] for r in rules.values() for h in r["hints"]} | {r.get("swapHint") for r in rules.values()}
    for h in hint_enum:
        if h not in used: todo(f"the contract names hint '{h}' but no rule produces it: add {{pattern, hint}} under identity.fields.<field>.hints (the answer text explains when)")
    identity = {"fieldParam": field_param, "answerParam": answer_param, "maxWrong": int(max_wrong),
                "passAt": (pass_at.pop() if len(pass_at) == 1 else None), "fields": rules}

    # customers
    out = {}
    for c in customers:
        cid = str(c.get("customer_id") or (c.get("variables") or {}).get("customer_id") or c.get("id"))
        ident = {k: str(v) for k, v in (c.get("identity") or {}).items()}
        for k in ident:
            if id_fields and k not in id_fields: todo(f"customer {cid}: identity field {k} is not in the contract's enum {id_fields}")
        out[cid] = {"identity": ident,
                    "products": [{"product_id": f"p{i + 1}", **p} for i, p in enumerate(c.get("products") or [])],
                    "faults": {f["tool"]: int(f.get("fail_first_n", 0)) for f in c.get("faults") or []},
                    "ledgerExtra": {"case": c.get("id"), "now": (c.get("variables") or {}).get("now")},
                    "variables": c.get("variables") or {}}
        if not ident: todo(f"customer {cid} has no identity answers")
    if not out: todo("no customers")
    return {"version": version or f"{api.get('info', {}).get('title', 'bank')[:40]} / {datetime.date.today()}", "basePath": base,
            "services": services, "identity": identity, "customers": out}


ERRORS = """{
    notFound:             { status: 404, body: { ok: false, error: "not found" } },
    badBody:              { status: 400, body: { ok: false, error: "the body must be a JSON object" } },
    unknownCustomer:      { status: 404, body: { ok: false, error: "unknown customer_id" } },
    unknownService:       { status: 404, body: { ok: false, error: "unknown tool {service}" } },
    closed:               { status: 200, body: { ok: false, error: "the call is closed, nothing is accepted after close_call" } },
    fault:                { status: 500, body: { ok: false, error: "service unavailable, nothing was recorded" } },
    missingField:         { status: 400, body: { ok: false, error: "missing field {field}" } },
    badEnum:              { status: 400, body: { ok: false, error: "{field} must be one of {enum}" } },
    badNumber:            { status: 400, body: { ok: false, error: "{field} must be a number" } },
    badDate:              { status: 400, body: { ok: false, error: "{field} must be a date as YYYY-MM-DD" } },
    notVerified:          { status: 200, body: { ok: false, error: "identity not verified" } },
    unknownIdentityField: { status: 400, body: { ok: false, error: "unknown field {field}" } },
    identityEnded:        { status: 200, body: { ok: false, error: "the identity check has ended after too many wrong answers" } },
    unreadableDate:       { status: 200, body: { ok: false, error: "unreadable date, send it as YYYY-MM-DD" } },
    unknownProduct:       { status: 200, body: { ok: false, error: "unknown product_id" } },
    duplicatePromise:     { status: 200, body: { ok: false, error: "this product already has a promise" } },
  }"""


def js(v, indent=2, level=1):
    """JSON with one line per leaf-ish object, so a human can read and edit it."""
    pad = " " * (indent * level)
    if isinstance(v, dict) and v and any(isinstance(x, (dict, list)) and x and (isinstance(x, dict) or any(isinstance(y, dict) for y in x)) for x in v.values()):
        return "{\n" + "".join(f"{pad}{json.dumps(k, ensure_ascii=False)}: {js(x, indent, level + 1)},\n" for k, x in v.items()) + " " * (indent * (level - 1)) + "}"
    if isinstance(v, list) and v and any(isinstance(x, dict) for x in v):
        return "[\n" + "".join(f"{pad}{js(x, indent, level + 1)},\n" for x in v) + " " * (indent * (level - 1)) + "]"
    return json.dumps(v, ensure_ascii=False)


def render(d, source):
    todos = "".join(f"//   TODO: {t}\n" for t in TODOS) or "//   (none)\n"
    return f"""// THE ONE FILE ABOUT THE BANK. The engine (engine.js, worker.js) reads everything bank-specific from here.
// Written by tools/make_data.py from: {source}
// To refill for another bank: run make_data.py on its contract + customer list, then go through the TODOs and the comments below.
// Open decisions left by the generator:
{todos}
export default {{
  // Shown by GET /health. Change it on every deploy so you can see which data is live.
  version: {json.dumps(d['version'], ensure_ascii=False)},

  // Prefix of every service path (the last part of the contract's server URL). Service URL = <worker url> + basePath + service.path
  basePath: {json.dumps(d['basePath'])},

  // The body field that carries the customer's key. Every call is routed to the Durable Object of this value.
  customerIdField: "customer_id",

  // Name of the Durable Object binding in wrangler.toml.
  binding: "CUSTOMER",

  // If set, GET <basePath>/_ledger/<id> and POST <basePath>/_reset/<id> need the header x-admin-key with this value. "" = open.
  adminKey: "",

  // When does a customer's state start again by itself? (POST <basePath>/_reset/<customer_id> always clears it.)
  //   idleResetSeconds        a service call that arrives this long after the previous one starts a fresh call (0 = never)
  //   resetAfterCloseSeconds  a service call that arrives this long after close_call starts a fresh call (0 = never: stays closed)
  // The finished call is kept under _state.earlier_calls of the ledger (last 3). The Python bank never resets: set both to 0 to copy it.
  session: {{ idleResetSeconds: 600, resetAfterCloseSeconds: 120 }},

  // The key inside a product record that create_promise / add_note refer to.
  productIdKey: "product_id",

  // ---- Services. One entry per POST path of the contract.
  //   path              appended to basePath
  //   kind              what the engine does:
  //                       "verify"  checks one identity answer (rules under `identity` below)
  //                       "account" returns the customer's products
  //                       "promise" records one entry per product in `list`, answers with a new id (idPrefix + running number)
  //                       "record"  appends the call's fields to the ledger list `list` (notes, callbacks, transfers, anything else)
  //                       "close"   keeps the fields named in `store` in the ledger and refuses every later call (errors.closed)
  //   fields            request fields besides the customer id: required, type (string | number | integer | boolean | date), enum.
  //                     Checked in this order: missing required field, then per field enum / number / date. Errors: see `errors`.
  //   requiresVerified  true = refused with errors.notVerified until the identity check is passed
  //   response          the success body. "$name" is replaced by a value of the engine:
  //                       verify: $verified $wrong_left $all_verified (+ $hint in hintResponse) / account: $products / promise: $id
  //   promise only      productRef (request field holding the product id), idKey, idPrefix, copyFromProduct (product keys copied
  //                     into the ledger entry), onePerProduct (a second promise for the same product -> errors.duplicatePromise)
  //   record only       productRef (optional: if set, an unknown product id is refused)
  //   stringify         fields stored as text in the ledger
  services: {js(d['services'], level=2)},

  // ---- Identity check (the "verify" service).
  //   fieldParam / answerParam  request fields: which question, and the answer given
  //   maxWrong   wrong answers in total (over all questions) that end the check. After that every verify call gets errors.identityEnded
  //              and the customer can never be verified in this call. Hints and unreadable dates are not counted.
  //   passAt     how many questions must be passed. null = all of the customer's identity fields.
  //   fields.<question>:
  //     type       "text": compared after `normalise` ({{remove: regex of characters to drop, case: "upper" | "lower" | "fold" | null}};
  //                        "fold" also ignores accents and the Turkish dotted/dotless i),
  //                        so "sc 418-802" equals "SC418802".
  //                "date": answer must be YYYY-MM-DD (else errors.unreadableDate, not counted); swapHint = hint name returned (not
  //                        counted) when day and month are exchanged, or null.
  //     hints      [{{pattern, hint}}]: an answer that matches the whole regex gets `hint` back and is NOT counted as wrong.
  //                Tested before the answer is compared, so the pattern must not match a complete right answer.
  identity: {js(d['identity'], level=2)},

  // ---- Error answers: status code and exact body. {{field}}, {{service}}, {{enum}} are filled in by the engine.
  // Copy the contract's texts here if it gives any.
  errors: {ERRORS},

  // ---- Customers, keyed by the customer id the agent sends.
  //   identity     right answer per question (text; dates as YYYY-MM-DD)
  //   products     what the "account" service returns, in order; each needs the productIdKey
  //   faults       {{service: N}} = the first N calls of that service answer errors.fault (500) and record nothing. {{}} = none.
  //   ledgerExtra  copied into the ledger as is (for our scorer)
  //   variables    not used by the engine; the call variables the agent gets for this customer, kept here for reference
  customers: {js(d['customers'], level=2)},
}};
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True); ap.add_argument("--customers", required=True)
    ap.add_argument("--out", default=str(pathlib.Path(__file__).resolve().parent.parent / "src" / "data.js"))
    ap.add_argument("--limit", type=int); ap.add_argument("--max-wrong", type=int); ap.add_argument("--version"); ap.add_argument("--sheet")
    a = ap.parse_args()
    api = json.load(open(a.api))
    verify = next((p for p in api.get("paths", {}) if p.strip("/").split("/")[-1] == "verify_identity"), None)
    id_fields = next((s["enum"] for s in schema_of(api["paths"][verify]["post"]).get("properties", {}).values() if s.get("enum")), []) if verify else []
    account = next((p for p in api.get("paths", {}) if p.strip("/").split("/")[-1] == "get_account"), None)
    prod_fields = list(schema_of(api["paths"][account]["post"], "response").get("properties", {}).get("products", {}).get("items", {}).get("properties", {})) if account else []
    customers = load_customers(a.customers, a.sheet, id_fields, prod_fields)
    data = build(api, customers, a.limit, a.max_wrong, a.version)
    pathlib.Path(a.out).write_text(render(data, f"{a.api} + {a.customers}"), encoding="utf-8")
    print(f"wrote {a.out}: {len(data['services'])} services, {len(data['customers'])} customers, maxWrong {data['identity']['maxWrong']}, identity fields {list(data['identity']['fields'])}")
    for t in TODOS: print("TODO:", t, file=sys.stderr)
