# Submission

Participant: Bedirhan Keskin (individual entry). Repository: https://github.com/bedriyan/freya-hackathon

## How the final agent is made

```bash
source env.sh                              # FREYA_API_KEY, FREYA_API_URL, ANTHROPIC_API_KEY: see env.example and the README
python3 local-simulation/builder/build.py {case_folder} {mock_url} --slot final 2>&1 | tee traces/final.log
cp local-simulation/builder/out/final/exported.workflow.json submission/final.workflow.json
cp local-simulation/builder/out/final/trace.txt traces/final.trace.txt
cp local-simulation/builder/traces/final-*.jsonl traces/final.session.jsonl
```

The builder runs Claude Code sessions through the Claude Agent SDK, so the trace is the full session log of the run:
`traces/final.session.jsonl` has every message and every tool call of every session, one JSON object per line.
`traces/final.log` is everything the run printed. `traces/final.trace.txt` is the short trace the builder writes itself: start and
end time, every model call with its model and purpose, what was created on Freya (workflow id, agent id, number of steps and
tools), and every test it ran on its own agent with the result.

The agent is not edited after the run. Nothing from the final case is written into the builder.

The bank's mock services (Cloudflare Workers) are a separate step from the builder; their source is in `mock/`.

## Changes to the builder after the case opened

The case message reached us on Sunday 11 October at 20:08 Istanbul time (17:08 UTC). Every commit after that is listed here.

Until the day of the final, the event page and the kit said about the final: "The seven tool names stay the same"
(`kit/build-the-builder/README.md`, section "The final"), and each brief was to come as one Markdown file with its tool API file.
The builder's own test side was written on that: a local copy of the bank with those seven services, a format for test customers
with identity answers, products and promises, and skill sentences that require the closing service. The final case has seven other
services and comes as a folder, so that test side could not run. The commits below take those assumptions out. None of them holds a
name, a value or a rule of the final case.

| Commit | Time | What |
|---|---|---|
| `1213cc0` | 20:09 | README and `env.example` only: which keys a run needs. No code. |
| `2c2dd92` | 20:59 | The readers and the brief-item check skip a file that is not text and cut a very large one, instead of failing on it. |
| the commit that adds this section | 21:05 | Test side only. Test calls go to the bank's own services at the mock address when the local test bank does not copy them; a format for test customers of such a bank; the local engine fills a request body that has objects inside; expected calls are found inside such objects; the random model-free calls are skipped for such a bank. In the skill, the sentences that required a closing service and promise records now hold "when the tool API file has such a service". |

## submission.json

```json
{
  "team": "Bedirhan Keskin",
  "members": [{"name": "Bedirhan Keskin", "email": "bedriyan@gmail.com"}],
  "repo": "https://github.com/bedriyan/freya-hackathon",
  "commit": "<commit>",
  "run_command": "python3 local-simulation/builder/build.py {case_folder} {mock_url}",
  "workflow": "submission/final.workflow.json",
  "trace": "traces/final.session.jsonl",
  "mock_url": "<mock_url>"
}
```
