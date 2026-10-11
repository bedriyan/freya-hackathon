# How the builder works

Diagrams of one builder run, of what runs in parallel inside it, of the repair loop, and of the local simulation engine the
builder tests its own agent in. The text of each step is in [README.md](README.md); this page shows how the pieces connect.

Each diagram names the source file it describes. When that file's structure changes, the diagram changes in the same commit
(see [Keeping this page current](#keeping-this-page-current)).

- [1. One run, end to end](#1-one-run-end-to-end)
- [2. What runs in parallel](#2-what-runs-in-parallel)
- [3. Who sees what](#3-who-sees-what)
- [4. The repair loop](#4-the-repair-loop)
- [5. One checkpoint](#5-one-checkpoint)
- [6. The local simulation: one test call](#6-the-local-simulation-one-test-call)
- [7. The local engine: one customer turn](#7-the-local-engine-one-customer-turn)
- [8. The model-free test](#8-the-model-free-test)
- [9. What comes out](#9-what-comes-out)

## 1. One run, end to end

`python3 local-simulation/builder/build.py {case_folder} {mock_url}` is the only command. Everything below happens inside that
one process (`build.py`, class `Run`, method `main`).

```mermaid
flowchart TD
    IN["Case folder or zip<br/>Markdown, Word, Excel, PDF"] --> STAGE
    MOCK["Mock URL<br/>where the bank's services run"] --> STAGE
    STAGE["Stage the inputs<br/>fresh temp work folder, .txt copies of Word, Excel, PDF<br/>BRIEF and README first"] --> HASAPI{"Tool API file<br/>among the documents?"}
    HASAPI -- "no" --> CONTRACT["Contract session, Claude Opus<br/>writes tool_api.json from the service contract"]
    HASAPI -- "yes" --> SPLIT
    CONTRACT --> SPLIT(("start"))

    subgraph PAR1["In parallel, two Claude sessions that never see each other's work"]
        direction LR
        BUILD["Build session, Claude Opus + skill<br/>rules.md, gen.py, selftest.py<br/>gen.py writes workflow.json<br/>check_workflow.py and fuzz_workflow.py run inside the turn"]
        CASES["Case writer session, Claude Opus<br/>about 28 test customers from the brief alone<br/>check_cases.py counts coverage, one more turn for the gaps"]
    end
    SPLIT --> BUILD
    SPLIT --> CASES

    subgraph PAR2["In parallel"]
        direction LR
        TEST0["Local test, round 0<br/>every test customer x 3 runs<br/>in the local engine"]
        DRY["Platform dry run<br/>does Freya accept the file?<br/>nothing is saved"]
    end
    BUILD --> TEST0
    CASES --> TEST0
    BUILD --> DRY

    TEST0 --> FUZZ0["Model-free test, round 0<br/>random calls, stuck customers,<br/>recorded calls replayed and disturbed<br/>+ selftest.py + check_rules.py"]
    DRY --> FUZZ0
    FUZZ0 --> LOOP{"Something fails<br/>and budget is left?"}
    LOOP -- "yes" --> REPAIR["Repair loop<br/>see diagram 4"]
    REPAIR --> LOOP
    LOOP -- "no" --> DELIVER["Deliver the best tested version<br/>save to Freya, export"]
    DELIVER --> OUT["out/&lt;slot&gt;/ and traces/<br/>see diagram 9"]
```

Without `ANTHROPIC_API_KEY` the case writer, the tests and the repair loop are skipped and the first version is delivered.

## 2. What runs in parallel

Three levels of parallelism, all inside the one process (`asyncio`): two Claude sessions at the start, the test round next to
the platform dry run, and inside a test round every call of every test customer at once.

```mermaid
sequenceDiagram
    autonumber
    participant B as build.py
    participant S as Build session (Opus)
    participant W as Case writer (Opus)
    participant L as Local simulator (sim/local.py)
    participant Z as Model-free test (fuzz_workflow.py)
    participant F as Freya platform

    par first version
        B->>S: build the agent from the brief (skill build-phone-agent)
        S-->>B: rules.md, gen.py, selftest.py, workflow.json
    and test customers, written blind
        B->>W: write test customers from the brief (CASE-FORMAT.md)
        W-->>B: cases.json
        B->>W: coverage gaps found by check_cases.py
        W-->>B: cases.json, at most 30 customers
    end

    par test round 0
        B->>L: cases.json, workflow.json, 3 runs per customer
        Note over L: about 28 customers x 3 runs, up to 30 calls at the same time
        L-->>B: summary.json, one recording per call
    and platform dry run
        B->>F: validate workflow.json
        F-->>B: accepted or rejected
    end

    B->>Z: workflow.json, cases.json, recordings of round 0
    Z-->>B: fuzz-0.md, defect groups

    loop repair rounds, until all pass, 2 rounds without gain, or the budget is used
        B->>S: failed calls, model-free defects, time for this turn
        Note over S: one fix, then checkpoint.py, again and again
        S-->>B: last checkpoint, or cut off at the time limit
        B->>L: test the checkpoint, 3 runs per customer
        L-->>B: summary.json
        B->>Z: only when one more repair round still fits
        Z-->>B: fuzz-N.md
    end

    B->>F: save the best tested version, export it
    F-->>B: workflow id, agent id, exported.workflow.json
```

The build session is one long conversation: the repair turns go to the same session that wrote the generator, so it still
knows why each rule is built the way it is.

## 3. Who sees what

The test customers are only worth something when they are written without the agent in view, and the agent without the test
customers in view. Each session works in its own empty temp folder.

```mermaid
flowchart LR
    IN["Brief documents<br/>tool_api.json"]
    BS["Build session<br/>own empty folder: skill, platform docs,<br/>reference shapes, the checks<br/>writes rules.md, gen.py, selftest.py"]
    CW["Case writer<br/>own empty folder: CASE-FORMAT.md<br/>never sees the agent"]
    SIM["Local simulator<br/>3 runs per test customer"]
    IN --> BS
    IN --> CW
    BS -- "workflow.json" --> SIM
    CW -- "cases.json: script, expected<br/>bank records, judge's checks" --> SIM
    SIM -- "only after the first version is finished:<br/>failed calls with script, expected records,<br/>judge's reason, transcript, tool calls" --> BS
```

Nothing of an earlier run or of another brief is in either folder, so a session cannot start from an old generator.

## 4. The repair loop

`build.py`, the `while` loop in `main`. A repair turn is short (at most 270 s) because a re-test tells more than a longer
turn. The loop has no fixed number of rounds; it ends on one of the exits below.

```mermaid
flowchart TD
    START["Test round N is scored<br/>failed customers + model-free defect groups"] --> BUDGET{"Budget left for a round?<br/>320 s for the first, 290 s later<br/>and fewer than 12 rounds"}
    BUDGET -- "no" --> DELIVER
    BUDGET -- "yes" --> CLEAN{"Every customer passes all 3 runs<br/>and the model-free test is clean?"}
    CLEAN -- "yes" --> DELIVER
    CLEAN -- "no" --> STALE{"2 rounds in a row<br/>did not beat the best version?"}
    STALE -- "yes" --> DELIVER
    STALE -- "no" --> BASE["Baseline checkpoint<br/>the level a fix must not fall below"]
    BASE --> TURN["Repair turn, same build session, at most 270 s<br/>reads tests-N.md and fuzz-N.md<br/>one fix in gen.py, then checkpoint.py, repeat"]
    TURN --> CUT{"Time limit reached?"}
    CUT -- "yes: interrupt the session" --> TAKE
    CUT -- "no: session says REPAIRED" --> TAKE
    TAKE["Take the LAST CHECKPOINT<br/>not the files as the turn left them"] --> ANY{"A checkpoint was saved?"}
    ANY -- "no, and the turn was cut" --> KEEP["Keep round N as it was"] --> DELIVER
    ANY -- "yes" --> SAME{"workflow.json changed?"}
    SAME -- "no" --> DELIVER
    SAME -- "yes" --> RETEST["Test round N+1<br/>local simulator, 3 runs per customer"]
    RETEST --> FZ["Model-free test N+1<br/>only when one more round still fits"]
    FZ --> START
    DELIVER["Deliver the best round<br/>most customers passed, then fewest model-free groups,<br/>then most calls passed, then the earlier round"]
```

Every tested version is kept as `workflow.r<N>.json`, so a repair that made things worse costs nothing: the earlier round is
delivered.

## 5. One checkpoint

`checkpoint.py`. The build session runs it after every single fix. No model takes part; it takes some seconds. What is tested
and delivered after a repair turn is the last saved checkpoint.

```mermaid
flowchart LR
    FIX["One fix in gen.py"] --> G["Run gen.py<br/>writes workflow.json"]
    G --> C["check_workflow.py<br/>static check"]
    C --> T["selftest.py<br/>unit tests of the rules"]
    T --> R["60 random model-free calls<br/>+ replay of the recorded calls that passed"]
    R --> CMP{"More passed calls lost,<br/>or a new kind of defect,<br/>than at the last checkpoint?"}
    CMP -- "no" --> SAVE["CHECKPOINT n SAVED<br/>gen.py and workflow.json copied to ckpt/"]
    CMP -- "yes" --> NOT["NOT SAVED<br/>names what broke"]
    G -- "fails" --> NOT
    C -- "ERROR" --> NOT
    T -- "fails" --> NOT
    NOT --> UNDO["Fix it, or copy ckpt/gen.py back"]
    SAVE --> NEXT["Next fix"]
    UNDO --> NEXT
```

## 6. The local simulation: one test call

`sim/local.py`. Nothing on Freya is touched: the workflow file runs in a local copy of the platform's engine against a local
bank. The customer and the judge use the platform's own prompts (`prompts/`), and the bank's records are scored by the
organisers' scorer (`kit/build-the-builder/tools/score.py`).

```mermaid
flowchart LR
    CASE["One test customer<br/>variables, identity, products,<br/>script, expected records, checks, faults"] --> PERSONA["Persona prompt<br/>kit's persona template"]
    PERSONA --> CUST["Customer<br/>Claude Sonnet, one chat"]

    subgraph ENGINE["sim/engine.py"]
        direction TB
        EX["Extraction role"]
        RT["Routing role"]
        DG["Dialogue role"]
    end
    AM["Agent model, Claude Haiku<br/>plays the three roles"] --- ENGINE

    CUST -- "customer line" --> ENGINE
    ENGINE -- "agent line" --> CUST
    ENGINE -- "tool calls" --> BANK["sim/bank.py<br/>local bank, one ledger,<br/>fails a tool when the case says so"]
    BANK -- "results" --> ENGINE

    ENGINE -- "numbered transcript + engine events" --> JUDGE["Judge, Claude Sonnet<br/>platform's judge prompt,<br/>the customer's checks"]
    BANK -- "ledger" --> SCORE["score.py<br/>ledger against expected records"]
    JUDGE --> VERDICT{"Records right<br/>AND judge says success?"}
    SCORE --> VERDICT
    VERDICT -- "yes" --> PASS["PASS"]
    VERDICT -- "no" --> FAIL["FAIL<br/>reasons, transcript, tool calls<br/>go to the repair turn"]
```

A test round runs every customer three times, all calls at once (`--concurrency 30`). A customer counts only when all three
runs pass, the same rule the platform scores by.

```mermaid
sequenceDiagram
    participant C as Customer (Sonnet)
    participant E as Engine (engine.py)
    participant M as Agent model (Haiku)
    participant K as Bank (bank.py)
    participant J as Judge (Sonnet)

    E->>E: start: start-of-call tools, enter the first step
    E->>C: opening line
    loop at most 45 customer turns, until the call ends
        C->>E: customer line
        E->>M: extraction: values in this message
        M-->>E: variables
        E->>M: routing: which intent conditions hold (only when no deterministic condition matched)
        M-->>E: next step or stay
        E->>M: dialogue: say something or call a tool
        M-->>E: line and optional tool call
        E->>K: tool call, through pre and post hooks
        K-->>E: result, mapped into variables
        E->>C: agent line
    end
    E->>K: closing call, end-of-call tools
    E->>J: transcript, events, how the call ended, the checks
    J-->>E: success or not, with the reason
    K-->>E: ledger, scored by score.py
```

The call also ends when the customer has said goodbye and the agent leaves the call open, after two silent agent turns, or at
the turn limit; each of these is a failed ending, as on the platform.

## 7. The local engine: one customer turn

`sim/engine.py`, methods `user`, `route`, `enter`, `dialogue`, `run_tool`. This is the imitation of Freya's engine, written
from the kit's `docs/workflow-format.md` and `docs/tools-and-code.md` and corrected against what was measured on the platform
(code steps run inside a function; a response mapping creates only leaf values, as text, under the step's name).

```mermaid
flowchart TD
    MSG["Customer message"] --> HASX{"Step has an<br/>extraction schema?"}
    HASX -- "yes" --> EXT["Extraction model call<br/>a value outside its enum or pattern is dropped"]
    HASX -- "no" --> ROUTE
    EXT --> ROUTE

    subgraph ROUTE["Routing, in this order"]
        direction TB
        R1["1: max_node_turns reached"]
        R2["2: deterministic conditions on variables<br/>no model call"]
        R3["3: intent conditions<br/>one routing-model call for all of them"]
        R4["4: global steps"]
        R1 --> R2 --> R3 --> R4
    end

    ROUTE -- "no transition matched" --> DIA
    ROUTE -- "target step" --> ENTER["Enter the step<br/>fixed opening line, if the step has one"]

    ENTER --> KIND{"Step type"}
    KIND -- "conversation" --> DIA["Dialogue model call<br/>answer: a line, or a line and a tool call"]
    KIND -- "api_request or code" --> PRE["Engine runs the step's own call or code<br/>no model"]
    KIND -- "end_call" --> END["Closing line, call_ended,<br/>end-of-call tools"]

    PRE --> PT["Route again, deterministic conditions only<br/>else the passthrough default"]
    PT -- "next step, at most 40 step changes per turn" --> ENTER
    PT -- "nothing matches" --> STUCKN["Engine error: the call is stuck"]

    DIA --> TOOLQ{"Tool call?"}
    TOOLQ -- "no" --> WAIT["Line goes to the customer<br/>wait for the next message"]
    TOOLQ -- "transition tool" --> ENTER
    TOOLQ -- "bank tool" --> TOOL["pre hook, call the bank, post hook,<br/>response mapping into variables"]
    TOOL --> AFTER["Route again, deterministic conditions only"]
    AFTER -- "target step" --> ENTER
    AFTER -- "stay" --> DIA
```

The three model roles (extraction, routing, dialogue) are the only places a model is called. Everything else is plain Python,
which is what makes the model-free test possible: replace those three calls and a whole call runs in milliseconds.

## 8. The model-free test

`fuzz_workflow.py`. The same engine and bank as in the local simulation, with the three model roles replaced by a random
number generator or by a recording. About a thousand calls in one to two minutes, no model, nothing leaves the machine.

```mermaid
flowchart TD
    WF["workflow.json + tool_api.json + cases.json"] --> RAND["Random calls, 400 in a builder run<br/>extraction: random enum members and pool values<br/>routing: random, dialogue: placeholder line"]
    RAND --> SNAP["Snapshot of every step<br/>a call was seen waiting in"]
    SNAP --> STK["Continue from each snapshot with a stuck customer<br/>repeat, silent, none, broken, bye"]

    REC["Recordings of the last local test round<br/>what each customer said, what was extracted"] --> REPLAY["replay<br/>the same turns again"]
    REPLAY --> MUT["mutate<br/>a passed call + one disturbance:<br/>cut-off message, nothing extracted,<br/>garbage value, side question"]
    REPLAY --> DEEP["deep<br/>the recorded call up to each turn,<br/>then a stuck customer"]

    RAND --> CHECKS
    STK --> CHECKS
    MUT --> CHECKS
    DEEP --> CHECKS
    CHECKS["Checks that hold for every brief<br/>NO_END, NO_CLOSE, REPEAT, STUCK, CODE_ERROR, ENUM_WRITE,<br/>NO_URL, BAD_CALL, BAD_PROMISE, HONESTY, GOODBYE, TEMPLATE,<br/>SILENT_TURN, BYE_IGNORED, MUTATION, REGRESSION"]
    CHECKS --> GROUP["One group per defect<br/>with the shortest run that shows it"]
    GROUP --> MD["fuzz-N.md"]
    ST["selftest.py fails"] --> MD
    CR["check_rules.py<br/>brief items not found in rules.md or the workflow"] --> MD
    MD --> REP["Repair turn<br/>a defect group counts like a failed customer"]
```

These findings are exact, not a model's opinion: a question asked again without end, a call that never closes, a record the
bank refuses, or bank records that change when one cut-off message is put into a call that passed.

## 9. What comes out

```mermaid
flowchart LR
    RUN["One run of build.py"] --> O["local-simulation/builder/out/&lt;slot&gt;/"]
    RUN --> TR["local-simulation/builder/traces/<br/>&lt;slot&gt;-&lt;start time&gt;.jsonl<br/>every model message of every session"]
    O --> W["workflow.json<br/>exported.workflow.json"]
    O --> SRC["rules.md, gen.py, selftest.py"]
    O --> TC["cases.json"]
    O --> REPS["tests-N.md, fuzz-N.md, repair-N.md<br/>workflow.rN.json, ckpt/"]
    O --> BRP["build-report.json<br/>steps, seconds, cost, versions, delivered round"]
    O --> TT["trace.txt<br/>short trace: model calls, what was created on Freya, tests"]
    W --> SUB["submission/"]
    TT --> SUBT["traces/"]
```

## Keeping this page current

| Diagram | Source |
|---|---|
| 1, 2, 4, 9 | `local-simulation/builder/build.py` |
| 3 | `build.py` (`workdir`, `stage`, `write_cases`) |
| 5 | `local-simulation/builder/checkpoint.py` |
| 6 | `local-simulation/sim/local.py`, `sim/bank.py`, `sim/llm.py`, `prompts/` |
| 7 | `local-simulation/sim/engine.py` |
| 8 | `local-simulation/builder/fuzz_workflow.py`, `check_rules.py` |

A commit that changes the order of the steps, what runs in parallel, an exit of the repair loop, a model, a limit or a number
shown above also changes the diagram.
