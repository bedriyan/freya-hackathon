# Build the Builder: a builder for Freya phone agents

One command reads a bank's brief and its tool API file and creates a Freya phone agent (a workflow), with no manual step.

```bash
pip install -r requirements.txt            # Python 3.10+; the Claude Agent SDK also needs the `claude` CLI on PATH
source kit/build-the-builder/env.sh        # FREYA_API_KEY, FREYA_API_URL, FREYA_BANK_TEAM_KEY, ANTHROPIC_API_KEY (copy env.example)
python3 local-simulation/builder/build.py {case_folder} {mock_url} --slot <name>
```

The case can be a folder or a zip file with Markdown, Word, Excel and PDF documents (the last three are also given to the builder as
text), or single brief files. `{mock_url}` is the address the bank's services run at; the agent calls this address and no other.
The older form still works: `build.py {brief_path} [more briefs] {tool_api_path}`. When the case has no tool API file, the first
step writes one from the service contract in the documents.

**Time budget.** A run has a budget of 60 minutes by default (`--budget-minutes N` or `BUILD_BUDGET_SECONDS`). The first version of
the event's rules limited a builder run to 30 minutes and this builder ran with a budget of 28; the rules published for the final
no longer set a limit, so the default is now 60 minutes and the repair loop is no longer cut short by the clock. It ends as soon as
every test customer passes and the model-free test is clean, or when two repair rounds in a row bring no improvement, so a run
that is done after 40 minutes stops after 40 minutes. `--budget-minutes 28` gives the earlier behaviour back.

Output: `local-simulation/builder/out/<name>/` (`workflow.json`, `exported.workflow.json`, `rules.md`, `gen.py`, `selftest.py`,
`cases.json`, test and repair reports, `build-report.json`, `trace.txt`) and the full JSON-lines trace in
`local-simulation/builder/traces/`.

## What one run does

Diagrams of the run, of what runs in parallel, of the repair loop and of the local simulation engine: [ARCHITECTURE.md](ARCHITECTURE.md).

1. **Build** (Claude Opus, Claude Agent SDK, skill `local-simulation/builder/.claude/skills/build-phone-agent/SKILL.md`): reads the
   brief, writes a numbered rule list (`rules.md`, checked against the brief by `check_rules.py`), then a generator (`gen.py`) that
   holds the whole call logic as one code library and writes the workflow, and unit tests of the rules (`selftest.py`).
   `check_workflow.py` checks the file statically.
2. **Test customers**, in parallel: a second session that never sees the agent writes about 28 test customers from the brief alone
   (`cases/CASE-FORMAT.md`, coverage checked by `check_cases.py`), in the language of the call.
3. **Platform dry run**: asks Freya whether it accepts the file. Nothing is saved.
4. **Local test**: every test customer talks to the workflow three times in a local copy of the platform's engine (`sim/`), with a
   simulated customer and a judge.
5. **Model-free test** (`fuzz_workflow.py`): about a thousand runs without any model: random calls, stuck customers, and the recorded
   test calls replayed with one disturbance each (cut-off message, value in two parts, garbage value, side question).
6. **Repair**, in short rounds (each at most 4.5 minutes; as many as the budget allows): the build session fixes the generator one defect at a time.
   After each fix `checkpoint.py` regenerates, re-checks and replays the recorded calls in a few seconds and saves a checkpoint if
   nothing broke. A round is cut off at its time limit; the last checkpoint is tested again (step 4), and the next round starts
   from the calls that still fail.
7. **Delivery**: the best tested version is saved to Freya and exported.

Without `ANTHROPIC_API_KEY` in the environment steps 2 and 4 to 6 are skipped and the first version is delivered.

## Layout

| Path | What |
|---|---|
| `local-simulation/builder/` | the builder: `build.py`, the skill, the checks, `freya.py` |
| `local-simulation/sim/`, `local-simulation/prompts/` | local copy of the engine, the bank and the customer and judge prompts |
| `local-simulation/cases/CASE-FORMAT.md` | how test customers are written |
| `kit/build-the-builder/` | the organisers' kit (scorer and runner are used by the local test) |
| `scripts/run_cases_retry.py` | the kit's runner with network retries (only for `--test-on platform`) |
| `ARCHITECTURE.md` | diagrams of the builder and of the local simulation engine; updated with every structural change |
| `submission/`, `traces/` | the exported phone agents and the traces of the runs that made them; see `SUBMISSION.md` |

Nothing in the builder comes from a hidden brief.
