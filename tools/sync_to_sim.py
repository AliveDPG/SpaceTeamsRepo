"""
sync_to_sim.py - Put the controller the sim actually runs in sync with this repo.

Space Teams PRO runs  %LOCALAPPDATA%\\SimDynamX\\STPro\\Assets\\Repos\\STU4-Solution\\Controller.py
(see "Source": "Local/Repos/STU4-Solution/Controller.py" in COMPETITION_SIM.simconf), NOT the
files in this repo.  Editing here and forgetting to copy is the easiest way to test the wrong code.

    python tools/sync_to_sim.py            # copy Controller.py / nav.py / vision.py / run_overrides.json there
    python tools/sync_to_sim.py --check    # just report which files differ
    python tools/sync_to_sim.py --junction # replace the sim's folder with a junction to this repo (one source of truth)

Before the first overwrite the sim-side files are backed up to <sim folder>/_backup_<stamp>/.
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIM_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "SimDynamX" / "STPro" / "Assets" / "Repos" / "STU4-Solution"
FILES = ["Controller.py", "nav.py", "vision.py", "run_overrides.json"]


def differing(files):
    out = []
    for f in files:
        src, dst = REPO / f, SIM_DIR / f
        if not src.exists():
            continue
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--junction", action="store_true")
    ap.add_argument("--sim-dir", type=Path, default=SIM_DIR)
    args = ap.parse_args()
    sim_dir: Path = args.sim_dir

    if args.junction:
        if sim_dir.is_junction() if hasattr(sim_dir, "is_junction") else False:
            print(f"{sim_dir} is already a junction")
            return
        if sim_dir.exists():
            backup = sim_dir.with_name(sim_dir.name + f"_local_{time.strftime('%y%m%d_%H%M%S')}")
            sim_dir.rename(backup)
            print(f"moved existing folder to {backup}")
        subprocess.run(["cmd", "/c", "mklink", "/J", str(sim_dir), str(REPO)], check=True)
        print(f"junction created: {sim_dir} -> {REPO}")
        return

    if not sim_dir.exists():
        sys.exit(f"sim folder not found: {sim_dir}")
    stale_ovr = (sim_dir / "run_overrides.json").exists() and not (REPO / "run_overrides.json").exists()
    diff = differing(FILES)
    if not diff and not stale_ovr:
        print("sim copy is up to date")
        return
    if stale_ovr and not args.check:
        (sim_dir / "run_overrides.json").unlink()
        print("  removed stale run_overrides.json on the sim side")
    print("differs:", ", ".join(diff))
    if args.check:
        sys.exit(1)
    backup = sim_dir / f"_backup_{time.strftime('%y%m%d_%H%M%S')}"
    backup.mkdir(exist_ok=True)
    for f in diff:
        if (sim_dir / f).exists():
            shutil.copy2(sim_dir / f, backup / f)
        shutil.copy2(REPO / f, sim_dir / f)
        print(f"  copied {f}")
    # stale bytecode from the previous version must not be picked up
    shutil.rmtree(sim_dir / "__pycache__", ignore_errors=True)
    print(f"done (previous versions in {backup})")


if __name__ == "__main__":
    main()
