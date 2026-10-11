# Submission

Participant: Bedirhan Keskin (individual entry). Repository: https://github.com/bedriyan/freya-hackathon

## How the final agent is made

```bash
source kit/build-the-builder/env.sh
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
