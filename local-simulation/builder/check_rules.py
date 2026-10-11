"""Is everything the brief pins down also in the rule list, and in the workflow? No model, a few milliseconds.

    python3 check_rules.py <brief file> [more brief files ...] --rules out/<slot>/rules.md [--workflow out/<slot>/workflow.json]

A brief fixes two kinds of things literally: whole sentences in quotation marks (what the agent must say) and quantities (a time
of day, a percentage, an amount, "two wrong answers", "the 8th working day"). Each of them is looked for in rules.md; a quoted sentence is also looked for in the texts of the workflow. What is not found is listed.

It reads words, not meaning: it finds a rule that was left out, not a rule that was misunderstood. A listed item can be
harmless (an example in the brief, a number that is not a rule, a code the brief says is not used). Go through the list once:
add what is a rule, leave the rest.

Exit code 1 when something is listed.
"""
import json, re, sys

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
         "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "once": 1, "twice": 2}
UNITS = (r"(?:working |business |calendar )?days?|weeks?|months?|hours?|minutes?|attempts?|tries|times?|(?:wrong |incorrect |security )?answers?|questions?|digits?|characters?|calls?|offers?|"
         r"per ?cent|percent|products?|facilit(?:y|ies)|items?|instal?ments?")
NUM = r"(?:\d+(?:[.,]\d+)?|" + "|".join(WORDS) + r")"
QUANTITY = re.compile(rf"\b({NUM})(?:st|nd|rd|th)?[ -]({UNITS})\b", re.I)
TIME = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b(?!\.\d)")
PERCENT = re.compile(r"\b(\d+(?:\.\d+)?) ?(?:%|per ?cent|percent)", re.I)
MONEY = re.compile(r"[£$€] ?(\d[\d,]*(?:\.\d+)?)|\b(\d[\d,]*(?:\.\d+)?) ?(?:euros?|pounds?|dollars?|EUR|GBP|USD)\b")
QUOTE = re.compile(r'"([^"\n]{12,})"|“([^”\n]{12,})”')
CODE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")


def norm(s):
    s = s.lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"\b(" + "|".join(WORDS) + r")\b", lambda m: str(WORDS[m.group(1)]), s)
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"per ?cent|percent", "%", s)
    return re.sub(r"[^a-z0-9%:.]+", " ", s).strip()


def unit(u):
    u = re.sub(r"(wrong|incorrect|security|working|business|calendar) ", lambda m: m.group(1)[0] + " ", u.lower())
    return re.sub(r"(ies|s)$", "", u).replace("per cent", "%").replace("percent", "%")


def facts(text):
    """(kind, what to show, how to look for it) for everything the text fixes literally."""
    out = []
    for line in text.split("\n"):
        if line.count('"') % 2: continue                               # an unpaired mark: the pairs on this line cannot be trusted
        for m in QUOTE.finditer(line):
            q = (m.group(1) or m.group(2)).strip()
            # a whole sentence of some length: what an agent is told to say. Short quoted phrases are mostly examples of what a customer says
            if len(q.split()) >= 7 and q[0].isupper() and q[-1] in ".?!" and not re.search(r"[{<`]", q): out.append(("sentence", q, norm(q)))
    plain = QUOTE.sub(" ", text)
    for m in TIME.finditer(plain): out.append(("time of day", m.group(0), f"{int(m.group(1))}:{m.group(2)}"))
    for m in PERCENT.finditer(plain): out.append(("percentage", m.group(0), f"{m.group(1)} %"))
    for m in MONEY.finditer(plain): out.append(("amount", m.group(0), (m.group(1) or m.group(2)).replace(",", "")))
    for m in QUANTITY.finditer(plain):
        n = WORDS.get(m.group(1).lower(), m.group(1))
        if "." in str(n) or "," in str(n): continue                    # a section number in front of a heading
        if unit(m.group(2)) != "%": out.append(("quantity", m.group(0), f"{n} {unit(m.group(2))}"))
    seen, uniq = set(), []
    for k, show, key in out:
        if (k, key) not in seen: seen.add((k, key)); uniq.append((k, show, key))
    return uniq


def has(kind, key, hay, hay_raw):
    if kind == "sentence":
        words = key.split()
        return key in hay or (len(words) >= 8 and " ".join(words[:8]) in hay and " ".join(words[-4:]) in hay)      # a placeholder in the middle may be written differently
    if kind == "code": return key in hay_raw
    if kind == "time of day":
        h, m = key.split(":")
        return bool(re.search(rf"\b0?{h}[:.]{m}\b", hay_raw))
    if kind in ("percentage", "amount"): return bool(re.search(rf"(?<![\d.]){re.escape(key.split()[0])}(?![\d])", hay_raw.replace(",", "")))
    n, u = key.split(" ", 1)
    return bool(re.search(rf"(?<![\d.]){re.escape(n)}(?![\d.])", hay)) and u.split()[-1][:5] in hay         # the number and the thing counted both appear somewhere


def check(briefs, rules, workflow=None):
    hay, hay_raw = norm(rules), rules.lower()
    spoken = ""
    if workflow is not None:
        def walk(x):
            if isinstance(x, str): yield x
            elif isinstance(x, dict):
                for v in x.values(): yield from walk(v)
            elif isinstance(x, list):
                for v in x: yield from walk(v)
        spoken = norm(" \n ".join(walk(workflow)).replace("\\'", "'"))
    missing = []
    for k, show, key in facts("\n".join(briefs)):
        if not has(k, key, hay, hay_raw): missing.append((k, show, "rules.md"))
        elif k == "sentence" and workflow is not None and not has(k, key, spoken, spoken): missing.append((k, show, "the workflow (it is in rules.md, but no step can say it in these words)"))
    return missing


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--rules" not in a: sys.exit(__doc__)
    rules = open(a[a.index("--rules") + 1]).read()
    wf = json.load(open(a[a.index("--workflow") + 1])) if "--workflow" in a else None
    skip = {a.index("--rules"), a.index("--rules") + 1} | ({a.index("--workflow"), a.index("--workflow") + 1} if "--workflow" in a else set())
    briefs = [open(x).read() for i, x in enumerate(a) if i not in skip]
    miss = check(briefs, rules, wf)
    for k, show, where in miss: print(f"NOT FOUND in {where}: {k}: {show}")
    print(f"\n{len(miss)} of the brief's quoted sentences and quantities were not found")
    sys.exit(1 if miss else 0)
