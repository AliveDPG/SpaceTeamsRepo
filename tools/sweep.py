"""
sweep.py - Step a parameter sweep through the real simulator with one click per run.

The sim must still be started from the SpaceCRAFT GUI (Sim -> User made sim -> COMPETITION_SIM
-> Confirm); that part cannot be scripted because the physics runs in the Unreal client.  This
tool automates everything around that click:

    python tools/sweep.py status            # how many runs each config has so far
    python tools/sweep.py next              # write run_overrides.json for the next config + sync to the sim
    python tools/sweep.py auto              # loop: next -> wait for a finished run -> next ...  (you only click Start)
    python tools/sweep.py clear             # remove run_overrides.json (back to the defaults in Controller.py)

tools/sweep.json:
    {
      "runs_per_config": 2,
      "configs": [
        {"tag": "v15",        "v_cruise": 15.0, "v_arrive": 15.0},
        {"tag": "v16",        "v_cruise": 16.0, "v_arrive": 16.0},
        {"tag": "v16-att-db", "v_cruise": 16.0, "v_arrive": 16.0, "ATT_MODE": "deadband"}
      ]
    }
Keys are GuidanceParams fields (v_cruise, margin_m, tube_radius, ...) or Controller.py globals
(ATT_MODE, CAMERA_ENABLED, CAMERA_PERIOD_S, ...).  "tag" is recorded in the run log.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import collect_runs  # noqa: E402

SWEEP = REPO / "tools" / "sweep.json"
OVERRIDES = REPO / "run_overrides.json"


def load_sweep():
    if not SWEEP.exists():
        sys.exit(f"missing {SWEEP}")
    sw = json.loads(SWEEP.read_text())
    for c in sw["configs"]:
        if "tag" not in c:
            sys.exit("every config needs a 'tag'")
    return sw


def runs_per_tag():
    rows = collect_runs.collect(collect_runs.DEFAULT_LOGS)
    counts: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("tag"):
            counts.setdefault(str(r["tag"]), []).append(r)
    return counts


def status(sw, counts):
    print(f"{'tag':14} {'runs':>4}/{sw['runs_per_config']:<3} {'mean':>6} {'best':>6}")
    for c in sw["configs"]:
        rs = counts.get(c["tag"], [])
        sc = [r["score"] for r in rs]
        print(f"{c['tag']:14} {len(rs):>4}/{sw['runs_per_config']:<3} "
              f"{(sum(sc)/len(sc)) if sc else 0:>6.1f} {max(sc) if sc else 0:>6.1f}")


def pick_next(sw, counts):
    for c in sw["configs"]:
        if len(counts.get(c["tag"], [])) < sw["runs_per_config"]:
            return c
    return None


def write_next(cfg):
    OVERRIDES.write_text(json.dumps(cfg, indent=2))
    print(f"next config -> {cfg['tag']}: {cfg}")
    subprocess.run([sys.executable, str(REPO / "tools" / "sync_to_sim.py")], check=False)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "clear":
        OVERRIDES.unlink(missing_ok=True)
        subprocess.run([sys.executable, str(REPO / "tools" / "sync_to_sim.py")], check=False)
        print("overrides cleared")
        return
    sw = load_sweep()
    counts = runs_per_tag()
    if cmd == "status":
        status(sw, counts)
        return
    if cmd == "next":
        cfg = pick_next(sw, counts)
        if cfg is None:
            print("sweep complete")
            status(sw, counts)
            return
        write_next(cfg)
        return
    if cmd == "auto":
        while True:
            counts = runs_per_tag()
            cfg = pick_next(sw, counts)
            if cfg is None:
                print("\nsweep complete")
                status(sw, counts)
                return
            write_next(cfg)
            total_before = sum(len(v) for v in counts.values())
            print(f"-> start COMPETITION_SIM in SpaceCRAFT now (config {cfg['tag']}); waiting for it to finish...")
            while True:
                time.sleep(5)
                counts = runs_per_tag()
                if sum(len(v) for v in counts.values()) > total_before:
                    rs = counts.get(cfg["tag"], [])
                    if rs:
                        r = rs[-1]
                        print(f"   finished: score={r['score']} time={r['elapsed_s']}s fuel={r['fuel_kg']}kg dmg={r['damage_J']}J")
                    break
    sys.exit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
