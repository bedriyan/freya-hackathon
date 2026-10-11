"""Draws the diagrams of ARCHITECTURE.md as SVG files next to this script. Standard library only.

    python3 diagrams/make.py

Each diagram is a function below: nodes on fixed coordinates, edges between them. To change a diagram, change its function and
run the script again; the .svg files are committed.
"""
import pathlib
from xml.sax.saxutils import escape

HERE = pathlib.Path(__file__).resolve().parent
BG, CARD, LINE, TEXT, MUTED, EDGE = "#0d1117", "#161b22", "#30363d", "#e6edf3", "#9da7b3", "#7d8590"
KIND = {"model": "#a371f7", "code": "#3fb950", "sim": "#58a6ff", "freya": "#f0883e", "io": "#8b949e", "gate": "#d29922", "bad": "#f85149"}
LEGEND = [("model", "Claude session or model role"), ("code", "plain Python, no model"), ("sim", "simulation / test round"),
          ("freya", "Freya platform"), ("gate", "decision"), ("io", "files")]
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


class Diagram:
    def __init__(self, name, w, h, title, sub):
        self.name, self.w, self.h, self.title, self.sub = name, w, h, title, sub
        self.box, self.back, self.mid, self.front, self.top = {}, [], [], [], []

    def node(self, id, x, y, w, title, lines=(), kind="io", h=None, mono=False, pill=False):
        h = h or (26 + 18 + 17 * len(lines) + (4 if lines else 0))
        c = KIND[kind]; self.box[id] = (x, y, w, h)
        self.front.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2 if pill else 10}" fill="{CARD}"/>'
                          f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2 if pill else 10}" fill="{c}" fill-opacity="0.13" stroke="{c}" stroke-width="1.5"/>')
        ty = y + (h - 17 * len(lines)) / 2 + 5 if lines else y + h / 2 + 5
        self.front.append(f'<text x="{x + w / 2}" y="{ty:.1f}" text-anchor="middle" font-size="14" font-weight="600" fill="{TEXT}">{escape(title)}</text>')
        for i, line in enumerate(lines):
            fam = f' font-family="{MONO}" font-size="11"' if mono else ' font-size="12"'
            self.front.append(f'<text x="{x + w / 2}" y="{ty + 19 + 17 * i:.1f}" text-anchor="middle"{fam} fill="{MUTED}">{escape(line)}</text>')

    def group(self, id, x, y, w, h, label, color=EDGE):
        self.box[id] = (x, y, w, h)
        lw = 7.9 * len(label) + 22
        self.back.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#ffffff" fill-opacity="0.025" stroke="{color}" stroke-width="1.2" stroke-dasharray="5 5"/>'
                         f'<rect x="{x + 18}" y="{y - 11}" width="{lw}" height="22" rx="11" fill="{BG}" stroke="{color}" stroke-width="1.2"/>'
                         f'<text x="{x + 18 + lw / 2}" y="{y + 4}" text-anchor="middle" font-size="11" font-weight="600" letter-spacing="0.8" fill="{TEXT}">{escape(label)}</text>')

    def anchor(self, id, side, off=0):
        x, y, w, h = self.box[id]
        return {"t": (x + w / 2 + off, y), "b": (x + w / 2 + off, y + h), "l": (x, y + h / 2 + off), "r": (x + w, y + h / 2 + off)}[side]

    def edge(self, a, b, label=None, sa="b", sb="t", oa=0, ob=0, via=None, dash=False, lpos=None, color=EDGE):
        p0, p1 = self.anchor(a, sa, oa), self.anchor(b, sb, ob)
        if via is not None: pts = [p0, *via, p1]
        elif sa in "tb" and sb in "tb":
            pts = [p0, p1] if abs(p0[0] - p1[0]) < 1 else [p0, (p0[0], (p0[1] + p1[1]) / 2), (p1[0], (p0[1] + p1[1]) / 2), p1]
        elif sa in "lr" and sb in "lr":
            pts = [p0, p1] if abs(p0[1] - p1[1]) < 1 else [p0, ((p0[0] + p1[0]) / 2, p0[1]), ((p0[0] + p1[0]) / 2, p1[1]), p1]
        elif sa in "tb": pts = [p0, (p0[0], p1[1]), p1]
        else: pts = [p0, (p1[0], p0[1]), p1]
        # stop a little before the node so the arrow head touches the border
        (ax, ay), (bx, by) = pts[-2], pts[-1]
        n = max(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5, 1); pts[-1] = (bx - (bx - ax) / n * 3, by - (by - ay) / n * 3)
        d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
        for i in range(1, len(pts) - 1):
            (ax, ay), (cx, cy), (bx, by) = pts[i - 1], pts[i], pts[i + 1]
            la, lb = ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5, ((bx - cx) ** 2 + (by - cy) ** 2) ** 0.5
            r = min(10, la / 2, lb / 2)
            d += f" L{cx - (cx - ax) / la * r:.1f},{cy - (cy - ay) / la * r:.1f} Q{cx},{cy} {cx + (bx - cx) / lb * r:.1f},{cy + (by - cy) / lb * r:.1f}"
        d += f" L{pts[-1][0]:.1f},{pts[-1][1]:.1f}"
        dashed = ' stroke-dasharray="5 4"' if dash else ""
        mk = "arrow" if color == EDGE else "arrow-" + color.strip("#")
        self.mid.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5"{dashed} marker-end="url(#{mk})"/>')
        if label:
            if lpos is None:
                segs = sorted(zip(pts, pts[1:]), key=lambda s: -((s[1][0] - s[0][0]) ** 2 + (s[1][1] - s[0][1]) ** 2))
                lpos = ((segs[0][0][0] + segs[0][1][0]) / 2, (segs[0][0][1] + segs[0][1][1]) / 2)
            lw = 6.1 * len(label) + 14
            self.top.append(f'<rect x="{lpos[0] - lw / 2:.1f}" y="{lpos[1] - 10:.1f}" width="{lw:.1f}" height="20" rx="10" fill="{BG}" stroke="{LINE}"/>'
                            f'<text x="{lpos[0]:.1f}" y="{lpos[1] + 4:.1f}" text-anchor="middle" font-size="11" fill="{TEXT}">{escape(label)}</text>')

    def note(self, x, y, text, anchor="middle", size=12, color=MUTED):
        self.top.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" fill="{color}">{escape(text)}</text>')

    def save(self, legend=("model", "code", "sim", "freya", "gate", "io")):
        colors = sorted(set(KIND.values()))
        defs = f'<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{EDGE}"/></marker>' + "".join(
            f'<marker id="arrow-{c.strip("#")}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>' for c in colors)
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" font-family="{FONT}">', f"<defs>{defs}</defs>",
               f'<rect width="{self.w}" height="{self.h}" rx="16" fill="{BG}"/><rect x="0.5" y="0.5" width="{self.w - 1}" height="{self.h - 1}" rx="16" fill="none" stroke="{LINE}"/>',
               f'<text x="32" y="42" font-size="20" font-weight="700" fill="{TEXT}">{escape(self.title)}</text>',
               f'<text x="32" y="64" font-size="13" fill="{MUTED}">{escape(self.sub)}</text>']
        out += self.back + self.mid + self.front + self.top
        x, y = 32, self.h - 24
        for k in legend:
            name = dict(LEGEND)[k]
            out.append(f'<rect x="{x}" y="{y - 10}" width="12" height="12" rx="3" fill="{KIND[k]}" fill-opacity="0.25" stroke="{KIND[k]}" stroke-width="1.5"/>'
                       f'<text x="{x + 18}" y="{y + 1}" font-size="11" fill="{MUTED}">{escape(name)}</text>')
            x += 18 + 6.0 * len(name) + 22
        out.append("</svg>")
        (HERE / f"{self.name}.svg").write_text("\n".join(out) + "\n")


def run():
    d = Diagram("1-run", 1000, 1010, "One run of the builder", "build.py, one process, no manual step. Dashed frames run in parallel.")
    d.node("cmd", 250, 88, 500, "python3 build.py {case_folder} {mock_url}", ["case: folder or zip with Markdown, Word, Excel, PDF"], "io")
    d.node("stage", 90, 178, 400, "Stage the inputs", ["fresh temp work folder, .txt copies, BRIEF first"], "code")
    d.node("contract", 570, 178, 370, "Contract session", ["only when the case has no tool API file:", "Claude Opus writes it from the service contract"], "model", h=71)
    d.group("p1", 60, 290, 880, 150, "IN PARALLEL · TWO SESSIONS THAT NEVER SEE EACH OTHER'S WORK")
    d.node("build", 90, 318, 400, "Build session", ["Claude Opus + skill build-phone-agent", "rules.md → gen.py → workflow.json, selftest.py", "static check and a model-free run inside the turn"], "model")
    d.node("cases", 510, 318, 400, "Case writer", ["Claude Opus, own empty folder", "about 28 test customers from the brief alone", "coverage counted, one more turn for the gaps"], "model")
    d.group("p2", 60, 490, 880, 116, "IN PARALLEL")
    d.node("test0", 90, 518, 400, "Local test, round 0", ["every customer × 3 runs, up to 30 calls at once", "local engine and bank, customer and judge models"], "sim", h=68)
    d.node("dry", 510, 518, 400, "Platform dry run", ["Freya is asked whether it accepts the file", "nothing is saved"], "freya", h=68)
    d.node("fuzz", 200, 650, 500, "Model-free test", ["about 1,000 calls without any model:", "random, stuck customers, recorded calls replayed and disturbed"], "code", h=74)
    d.node("gate", 300, 768, 300, "Failures, and budget left?", kind="gate", h=44, pill=True)
    d.node("repair", 720, 742, 230, "Repair loop", ["turns of at most 270 s", "checkpoint after each fix", "then the tests again"], "model", h=96)
    d.node("deliver", 250, 852, 400, "Deliver the best tested version", ["saved to Freya and exported"], "freya")
    d.node("out", 730, 852, 220, "out/<slot>/", ["workflow.json, trace.txt,", "build-report.json"], "io", h=65)
    d.edge("cmd", "stage", via=[(500, 160), (290, 160)])
    d.edge("stage", "contract", sa="r", sb="l", dash=True)
    d.edge("stage", "p1", ob=-210)
    d.edge("contract", "p1", ob=255, dash=True)
    d.edge("p1", "p2", "workflow.json + cases.json")
    d.edge("p2", "fuzz", "recordings of every call", ob=0, oa=-50)
    d.edge("fuzz", "gate")
    d.edge("gate", "repair", "yes", sa="r", sb="l")
    d.edge("repair", "fuzz", "re-tested", sa="t", sb="r", color=KIND["model"])
    d.edge("gate", "deliver", "no")
    d.edge("deliver", "out", sa="r", sb="l")
    d.save()


def repair():
    d = Diagram("2-repair", 1000, 900, "The repair loop and its checkpoints", "A repair turn is short because a re-test tells more than a longer turn. No fixed number of rounds.")
    d.node("score", 110, 96, 340, "Test round N is scored", ["failed customers + model-free defect groups"], "sim")
    d.node("exit", 110, 194, 340, "Stop here?", ["every customer passes × 3, model-free clean", "2 rounds in a row without gain", "no budget for a round (320 s first, 290 s later)"], "gate")
    d.node("base", 110, 340, 340, "Baseline checkpoint", ["the level a fix must not fall below"], "code")
    d.node("turn", 110, 436, 340, "Repair turn, at most 270 s", ["the same build session", "reads tests-N.md and fuzz-N.md", "one fix in gen.py → checkpoint → next fix"], "model")
    d.node("take", 110, 580, 340, "Take the LAST CHECKPOINT", ["not the files as the turn left them:", "a turn cut off by the clock loses nothing"], "code", h=74)
    d.node("retest", 110, 692, 340, "Test round N+1", ["local simulator × 3, then the model-free test"], "sim")
    d.node("deliver", 110, 790, 340, "Deliver the best round", ["most customers, then fewest model-free groups"], "freya")
    d.edge("score", "exit"); d.edge("exit", "base", "no"); d.edge("base", "turn"); d.edge("turn", "take"); d.edge("take", "retest", "workflow changed")
    d.edge("retest", "score", "next round", sa="l", sb="l", via=[(62, 721), (62, 125)], lpos=(62, 420))
    d.edge("exit", "deliver", "yes", sa="r", sb="r", via=[(488, 247), (488, 819)], lpos=(488, 380), color=KIND["gate"])
    d.edge("take", "deliver", "nothing new to test", sa="r", sb="r", ob=-12, oa=14, via=[(476, 631), (476, 807)], lpos=(486, 673), dash=True)
    d.group("ck", 560, 110, 392, 600, "checkpoint.py · AFTER EVERY FIX · NO MODEL", KIND["code"])
    d.node("c1", 590, 146, 332, "Run gen.py", ["writes workflow.json"], "code")
    d.node("c2", 590, 240, 332, "check_workflow.py", ["static check of the file"], "code")
    d.node("c3", 590, 334, 332, "selftest.py", ["unit tests of the brief's rules"], "code")
    d.node("c4", 590, 428, 332, "60 random calls + replay", ["the recorded calls that passed must still pass"], "code")
    d.node("c5", 610, 528, 292, "Worse than the last checkpoint?", kind="gate", h=44, pill=True)
    d.node("ok", 590, 618, 156, "SAVED", ["copied to ckpt/"], "code")
    d.node("no", 766, 618, 156, "NOT SAVED", ["fix it, or undo"], "bad")
    d.edge("c1", "c2"); d.edge("c2", "c3"); d.edge("c3", "c4"); d.edge("c4", "c5")
    d.edge("c5", "ok", "no", oa=-70, lpos=(668, 594)); d.edge("c5", "no", "yes", oa=70, lpos=(844, 594))
    d.edge("turn", "ck", "after each fix", sa="r", sb="l", ob=75, dash=True, color=KIND["code"])
    d.note(756, 760, "What is tested and delivered after a turn is the last SAVED checkpoint.")
    d.note(756, 780, "Every tested round is kept as workflow.rN.json: a repair that made")
    d.note(756, 797, "things worse costs nothing, the earlier round is delivered.")
    d.save(("model", "code", "sim", "freya", "gate"))


def call():
    d = Diagram("3-test-call", 1000, 730, "The local simulation: one test call", "sim/local.py. Nothing on Freya is touched. A test round runs every customer 3 times, all calls at once.")
    d.node("case", 60, 100, 250, "One test customer", ["script, identity, products,", "expected records, checks, faults"], "io", h=70)
    d.node("cust", 60, 250, 250, "Simulated customer", ["Claude Sonnet", "platform's customer prompt", "+ persona from the case"], "model", h=100)
    d.group("eng", 375, 196, 250, 250, "sim/engine.py", KIND["sim"])
    d.note(500, 232, "runs workflow.json turn by turn", color=TEXT)
    d.node("r1", 400, 250, 200, "Extraction", kind="model", h=40)
    d.node("r2", 400, 302, 200, "Routing", kind="model", h=40)
    d.node("r3", 400, 354, 200, "Dialogue", kind="model", h=40)
    d.note(500, 422, "three model roles: Claude Haiku")
    d.node("bank", 690, 250, 250, "Local bank", ["sim/bank.py, one ledger", "the case's tool failures", "happen here"], "code", h=100)
    d.node("judge", 190, 520, 270, "Judge", ["Claude Sonnet, platform's judge prompt", "reads against the customer's checks"], "model", h=74)
    d.node("score", 540, 520, 270, "score.py from the organisers' kit", ["ledger against the expected records"], "code", h=74)
    d.node("v", 320, 630, 360, "PASS only when both say yes, in all 3 runs", kind="gate", h=40, pill=True)
    d.edge("case", "cust")
    d.edge("cust", "eng", "customer line", sa="r", sb="l", oa=-16, ob=-37)
    d.edge("eng", "cust", "agent line", sa="l", sb="r", oa=-5, ob=16)
    d.edge("eng", "bank", "tool calls", sa="r", sb="l", oa=-37, ob=-16)
    d.edge("bank", "eng", "results", sa="l", sb="r", oa=16, ob=-5)
    d.edge("eng", "judge", "transcript + engine events", sa="b", sb="t", oa=-60, ob=60, via=[(440, 482), (385, 482)], lpos=(412, 482))
    d.edge("bank", "score", "ledger", sa="b", sb="t", ob=60, via=[(815, 482), (735, 482)], lpos=(775, 482))
    d.edge("judge", "v", sb="t", ob=-100, via=[(325, 612), (400, 612)])
    d.edge("score", "v", sb="t", ob=100, via=[(675, 612), (600, 612)])
    d.note(704, 640, "Failed calls go to the repair turn with", anchor="start")
    d.note(704, 657, "the judge's reason, the transcript", anchor="start")
    d.note(704, 674, "and every tool call.", anchor="start")
    d.save(("model", "code", "sim", "gate", "io"))


def turn():
    d = Diagram("4-engine-turn", 1000, 850, "The local engine: one customer turn", "sim/engine.py, an imitation of Freya's engine. A model is called only in the three violet boxes.")
    d.node("msg", 380, 90, 240, "Customer message", kind="io", h=40, pill=True)
    d.node("ext", 310, 166, 380, "Extraction", ["model call; a value outside its enum or pattern is dropped"], "model")
    d.node("route", 290, 264, 420, "Routing, first match wins", ["1  max_node_turns reached", "2  deterministic conditions on variables, no model", "3  intent conditions, one model call for all of them", "4  global steps"], "gate")
    d.node("dia", 70, 468, 340, "Dialogue", ["model call: a line for the customer,", "or a line and a tool call"], "model", h=74)
    d.node("enter", 590, 468, 340, "Enter the step", ["fixed opening line, if the step has one,", "then by step type"], "sim", h=74)
    d.node("tool", 70, 612, 340, "Bank tool", ["pre hook → bank → post hook", "→ response mapping into variables"], "code", h=74)
    d.node("auto", 590, 612, 340, "api_request or code step", ["the engine runs the call or the code itself,", "then routes on, deterministic conditions only"], "code", h=74)
    d.node("say", 70, 740, 340, "Line goes to the customer", ["wait for the next message"], "io")
    d.node("end", 590, 740, 340, "end_call step", ["closing line, call_ended, end-of-call tools"], "freya")
    d.edge("msg", "ext"); d.edge("ext", "route")
    d.edge("route", "dia", "no transition matched: stay", sa="b", sb="t", oa=-110, via=[(390, 430), (240, 430)], lpos=(300, 430))
    d.edge("route", "enter", "target step", sa="b", sb="t", oa=110, via=[(610, 430), (760, 430)], lpos=(700, 430))
    d.edge("enter", "dia", "conversation step", sa="l", sb="r", oa=-14, ob=-14)
    d.edge("dia", "tool", "tool call", oa=-70, ob=-70)
    d.edge("tool", "dia", "stay", sa="t", sb="b", oa=70, ob=70)
    d.edge("tool", "enter", "new step", sa="r", sb="l", oa=0, ob=16, via=[(500, 649), (500, 521)], lpos=(500, 585))
    d.edge("enter", "auto", oa=-70, ob=-70)
    d.edge("auto", "enter", "next step, at most 40 per turn", sa="t", sb="b", oa=70, ob=70, lpos=(832, 579))
    d.edge("dia", "say", "no tool call", sa="l", sb="l", via=[(40, 505), (40, 769)], lpos=(58, 713))
    d.edge("enter", "end", sa="r", sb="r", via=[(964, 505), (964, 769)])
    d.save(("model", "code", "sim", "freya", "gate", "io"))


def fuzz():
    d = Diagram("5-model-free", 1000, 800, "The model-free test", "fuzz_workflow.py. The same engine and bank; the three model roles are replaced by a random generator or a recording.")
    d.node("rand", 70, 100, 380, "Random calls", ["400 per run: random enum members and pool values,", "random routing, placeholder lines"], "code", h=74)
    d.node("snap", 70, 214, 380, "Snapshot of every step a call waits in", kind="code", h=44)
    d.node("stuck", 70, 298, 380, "Continue with a stuck customer", ["repeat · silent · none · broken · bye"], "code")
    d.node("rec", 550, 100, 380, "Recordings of the last local test round", ["what each customer said, what was extracted"], "sim", h=74)
    d.node("replay", 550, 214, 380, "replay: the same turns again", kind="code", h=44)
    d.node("mut", 550, 298, 380, "mutate and deep", ["a passed call + one disturbance · up to turn k, then stuck"], "code")
    d.node("checks", 70, 420, 860, "Checks that hold for every brief", ["NO_END  NO_CLOSE  REPEAT  STUCK  CODE_ERROR  ENUM_WRITE  NO_URL  BAD_CALL", "BAD_PROMISE  HONESTY  GOODBYE  TEMPLATE  SILENT_TURN  BYE_IGNORED  MUTATION  REGRESSION"], "gate", mono=True, h=88)
    d.node("md", 70, 556, 500, "fuzz-N.md", ["one group per defect, with the shortest run that shows it", "+ selftest.py result + brief items not found (check_rules.py)"], "io", h=74)
    d.node("rep", 120, 676, 400, "Repair turn", ["a defect group counts like a failed customer"], "model")
    d.edge("rand", "snap"); d.edge("snap", "stuck"); d.edge("rec", "replay"); d.edge("replay", "mut")
    d.edge("stuck", "checks", ob=-240); d.edge("mut", "checks", ob=240)
    d.edge("rand", "checks", sa="l", sb="l", via=[(40, 137), (40, 464)])
    d.edge("checks", "md"); d.edge("md", "rep")
    d.note(610, 586, "Disturbances: a cut-off message, nothing", anchor="start")
    d.note(610, 603, "extracted, a garbage value, a side question.", anchor="start")
    d.note(610, 629, "Exact findings, not a model's opinion.", anchor="start", color=TEXT)
    d.save(("model", "code", "sim", "gate", "io"))


if __name__ == "__main__":
    for f in (run, repair, call, turn, fuzz): f()
    print("written:", ", ".join(sorted(p.name for p in HERE.glob("*.svg"))))
