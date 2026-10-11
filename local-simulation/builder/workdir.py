"""A clean, isolated folder for one hand-driven build (an agent follows the skill there), and the copy back afterwards.

    python3 builder/workdir.py prepare <slot> <brief file ...> <tool_api.json>     # prints the folder; the agent works only inside it
    python3 builder/workdir.py publish <slot> <folder>                             # copies <folder>/out/<slot> to builder/out/<slot>

The folder holds the skill, the platform documentation, the reference shapes, the static check and the given documents. Nothing of an
earlier run or another brief is in it. build.py does the same on its own; this is for builds driven by an agent instead of build.py.
"""
import pathlib, shutil, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent


def workdir(slot):
    work = pathlib.Path(tempfile.mkdtemp(prefix=f"build-{slot}-"))
    for name in (".claude", "docs", "reference"): shutil.copytree(HERE / name, work / name)
    shutil.copy(HERE / "check_workflow.py", work / "check_workflow.py")
    return work


def stage(paths, slot, work):
    dst = work / "out" / slot / "input"; dst.mkdir(parents=True)
    files = []
    for p in map(pathlib.Path, paths):
        for f in (sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]):
            shutil.copy(f, dst / f.name); files.append(f"out/{slot}/input/{f.name}")
    return files

if __name__ == "__main__":
    if len(sys.argv) < 4: sys.exit(__doc__)
    cmd, slot = sys.argv[1], sys.argv[2]
    if cmd == "prepare":
        inputs = [str(pathlib.Path(p).resolve()) for p in sys.argv[3:]]
        work = workdir(slot)
        briefs = stage(inputs[:-1], slot, work)
        shutil.copy(inputs[-1], work / "out" / slot / "input" / "tool_api.json"); api = f"out/{slot}/input/tool_api.json"
        print(f"folder: {work}\nslot: {slot}\nbrief documents, in order: {', '.join(briefs)}\ntool API file: {api}\nskill: {work}/.claude/skills/build-phone-agent/SKILL.md")
    elif cmd == "publish":
        src, dst = pathlib.Path(sys.argv[3]) / "out" / slot, HERE / "out" / slot
        if not (src / "workflow.json").exists(): sys.exit(f"no workflow.json in {src}")
        if dst.exists(): shutil.rmtree(dst)
        shutil.copytree(src, dst); print(f"published: {dst}")
    else: sys.exit(__doc__)
