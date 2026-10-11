"""Save the repair so far, if it is sound. Run it after EVERY single fix of a repair turn.

    python3 checkpoint.py out/<slot> <tool_api_file> [--recordings <folder of the last test round>]

It runs out/<slot>/gen.py, check_workflow.py, out/<slot>/selftest.py (if there is one), a short model-free run, and replays the recorded
test calls that passed. No model takes part; it takes some seconds. When nothing is broken it copies gen.py and workflow.json to
out/<slot>/ckpt/ and prints "CHECKPOINT n SAVED". Otherwise it prints what is broken and saves nothing: fix it, or go back to the last
checkpoint with    cp out/<slot>/ckpt/gen.py out/<slot>/gen.py

The program that started you cuts the repair turn off at a fixed time. What it then tests and delivers is the last checkpoint, not the
files as they are at that moment. A fix without a checkpoint is lost.
"""
import argparse, asyncio, collections, json, pathlib, shutil, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def run(cmd, limit):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=limit)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired: return 1, f"took longer than {limit} s"


async def replay(wf, api, folder, cases):
    """Recorded calls that passed and no longer give their bank records; engine defects in a short random run."""
    import fuzz_workflow as fz
    lost, n = [], 0
    for rec in fz.recordings(folder, cases) if folder else []:
        if not rec["passed"]: continue
        n += 1
        eng, bank, found = await fz.scripted(wf, api, rec["case"], rec["turns"])
        wrong = fz.records_wrong(rec["case"], bank) if eng.ended else ["the call did not end"]
        if wrong: lost.append(f"{rec['name']}: {'; '.join(wrong)[:160]}")
    hard, rows = ("NO_END", "NO_CLOSE", "REPEAT", "STUCK", "CODE_ERROR", "ENUM_WRITE", "NO_URL", "BAD_CALL"), []
    plain = [{**c, "faults": None} for c in cases] or fz.synthetic(api, wf)
    for i in range(60): rows.append(await fz.random_call(wf, api, plain[i % len(plain)], 700000 + i, collections.defaultdict(list)))
    kinds = sorted({f[0] for r in rows for f in r["found"] if f[0] in hard})
    return n, lost, kinds


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("api"); ap.add_argument("--recordings")
    a = ap.parse_args(); out, t, bad = pathlib.Path(a.out), time.time(), []
    code, text = run([sys.executable, str(out / "gen.py")], 60)
    if code: bad.append("gen.py failed:\n" + text[-600:])
    else:
        code, text = run([sys.executable, str(HERE / "check_workflow.py"), str(out / "workflow.json"), a.api], 60)
        if code: bad.append("check_workflow.py:\n" + "\n".join(l for l in text.splitlines() if l.startswith("ERROR"))[:900])
        if (out / "selftest.py").exists():
            code, text = run([sys.executable, str(out / "selftest.py")], 60)
            if code: bad.append("selftest.py failed:\n" + text[-600:])
    if not bad:
        cases = json.loads((out / "cases.json").read_text()) if (out / "cases.json").exists() else []
        from bank import knows
        api = json.loads(pathlib.Path(a.api).read_text())
        # the replay and the random calls need the local copy of the bank; for a bank with other services only the three checks above apply
        n, lost, kinds = asyncio.run(replay(json.loads((out / "workflow.json").read_text()), api, a.recordings, cases)) if knows(api) else (0, [], [])
        ck = out / "ckpt"; before = json.loads((ck / "state.json").read_text()) if (ck / "state.json").exists() else {"n": 0, "lost": None, "kinds": None}
        # the first checkpoint sets the level; a later one must not lose a recorded call or add a kind of defect that the one before did not have
        if before["lost"] is not None and len(lost) > len(before["lost"]): bad.append(f"{len(lost)} recorded calls that passed no longer give their bank records (last checkpoint: {len(before['lost'])}):\n  " + "\n  ".join(x for x in lost if x not in before["lost"])[:900])
        if before["kinds"] is not None and set(kinds) - set(before["kinds"]): bad.append(f"new defects in 60 random calls: {sorted(set(kinds) - set(before['kinds']))}. Run fuzz_workflow.py to see them.")
    try: left = float((out / "deadline").read_text()) - time.time(); left = f" TIME LEFT in this turn: {int(left) // 60} min {int(left) % 60} s." if left > 0 else " TIME IS UP: stop now."
    except Exception: left = ""
    if bad:
        print(f"NOT SAVED ({time.time() - t:.0f} s).{left} " + "\n".join(bad)); sys.exit(1)
    ck.mkdir(exist_ok=True)
    shutil.copy(out / "gen.py", ck / "gen.py"); shutil.copy(out / "workflow.json", ck / "workflow.json")
    (ck / "state.json").write_text(json.dumps({"n": before["n"] + 1, "lost": lost, "kinds": kinds, "t": time.strftime("%H:%M:%S")}))
    print(f"CHECKPOINT {before['n'] + 1} SAVED ({time.time() - t:.0f} s): {n - len(lost)} of {n} recorded passed calls still give their records; defects in 60 random calls: {kinds or 'none'}.{left}")
