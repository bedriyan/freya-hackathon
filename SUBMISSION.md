# Submission

Participant: Bedirhan Keskin (individual entry). Repository: https://github.com/bedriyan/freya-hackathon

## How the two hidden-brief agents are made

```bash
source kit/build-the-builder/env.sh
python3 local-simulation/builder/build.py briefs/hidden-1.md briefs/hidden-1.openapi.json --slot hidden-1 2>&1 | tee traces/hidden-1.log
python3 local-simulation/builder/build.py briefs/hidden-2.md briefs/hidden-2.openapi.json --slot hidden-2 2>&1 | tee traces/hidden-2.log
cp local-simulation/builder/out/hidden-1/exported.workflow.json submission/hidden-1.workflow.json
cp local-simulation/builder/out/hidden-2/exported.workflow.json submission/hidden-2.workflow.json
```

`traces/hidden-N.log` is everything the run printed. `traces/hidden-N.trace.txt` is the short trace the builder writes itself
(start and end time, every model call with its model and purpose, what was created on Freya with the number of steps and tools,
every test it ran on its own agent with the result). `traces/hidden-N.jsonl` is the full record of the sessions.

The agents are not edited after the run. Nothing from a hidden brief is written into the builder.

## Form

```json
{
  "team": "Bedirhan Keskin",
  "repo": "https://github.com/bedriyan/freya-hackathon",
  "commit": "<commit>",
  "run_command": "python3 local-simulation/builder/build.py {brief_path} {tool_api_path}",
  "workflows": {
    "hidden-1": "submission/hidden-1.workflow.json",
    "hidden-2": "submission/hidden-2.workflow.json"
  },
  "traces": {
    "hidden-1": "traces/hidden-1.log",
    "hidden-2": "traces/hidden-2.log"
  }
}
```
