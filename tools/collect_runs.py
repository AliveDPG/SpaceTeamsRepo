"""
collect_runs.py - Harvest every Space Teams PRO run of COMPETITION_SIM into a CSV.

Each sim run writes a folder under %LOCALAPPDATA%\\SimDynamX\\STPro\\Logs\\<stamp>_COMPETITION_SIM__xxx\\
containing COMPETITION_SIM.log (the Scoring system's final alerts) and
COMPETITION_SIM_PySys_Controller.log (our on-screen log, including the CONFIG and RESULT
banners Controller.py prints).  This script turns all of them into results/runs.csv.

    python tools/collect_runs.py                 # table of all runs, newest last
    python tools/collect_runs.py --last 5
    python tools/collect_runs.py --group tag     # mean score per config tag (or v_cruise, ATT_MODE, ...)
    python tools/collect_runs.py --watch         # keep polling; print each new run as it finishes
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

DEFAULT_LOGS = Path(os.environ.get("LOCALAPPDATA", "")) / "SimDynamX" / "STPro" / "Logs"
REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "results" / "runs.csv"

RE_SCORE = re.compile(r"(Successfully escaped Phobos with a score of|Crashed into some debris and finished with a score of)\s*(-?[\d.]+)")
RE_TIME = re.compile(r"Sim elapsed time\s*=\s*([\d.]+)")
RE_DIST = re.compile(r"Distance to ship\s*=\s*([\d.]+)")
RE_DMG = re.compile(r"Total damage taken\s*=\s*([\d.]+)")
RE_FUEL = re.compile(r"Total fuel used\s*=\s*([\d.]+)")
RE_CONFIG = re.compile(r"CONFIG (\{.*\})")
RE_RESULT = re.compile(r"RESULT (\{.*\})")
RE_STATUS = re.compile(r"t=\s*([\d.]+)s (\S+)\s+plan#(\d+) off=(\d+)m")

BASE_COLS = ["stamp", "run_id", "outcome", "score", "elapsed_s", "distance_m", "damage_J", "fuel_kg"]
CONFIG_COLS = ["tag", "git", "v_cruise", "v_arrive", "ATT_MODE", "CAMERA_ENABLED", "tube_radius", "margin_m",
               "plan_wait_s", "goal_margin_m", "replan_dv"]
RESULT_COLS = ["dv_used", "n_plans", "final_offset_m", "max_tracks", "trim_s", "replans", "controller_ok"]


def parse_run(folder: Path) -> dict | None:
    main_log = folder / "COMPETITION_SIM.log"
    if not main_log.exists():
        return None
    text = main_log.read_text(errors="ignore")
    m = RE_SCORE.search(text)
    if not m:
        return None                                   # run not finished (or aborted)
    row = {c: "" for c in BASE_COLS + CONFIG_COLS + RESULT_COLS}
    row["stamp"] = folder.name.split("_COMPETITION_SIM")[0]
    row["run_id"] = (folder / "run_uuid.txt").read_text().strip()[:8] if (folder / "run_uuid.txt").exists() else ""
    row["outcome"] = "win" if m.group(1).startswith("Successfully") else "loss"
    row["score"] = float(m.group(2))
    for key, rx in (("elapsed_s", RE_TIME), ("distance_m", RE_DIST), ("damage_J", RE_DMG), ("fuel_kg", RE_FUEL)):
        mm = rx.search(text)
        row[key] = float(mm.group(1)) if mm else ""

    ctrl_log = folder / "COMPETITION_SIM_PySys_Controller.log"
    if ctrl_log.exists():
        ctext = ctrl_log.read_text(errors="ignore")
        row["controller_ok"] = "fatal" not in ctext.lower() and "traceback" not in ctext.lower()
        mc = RE_CONFIG.search(ctext)
        if mc:
            try:
                cfg = json.loads(mc.group(1))
                for k in CONFIG_COLS:
                    if k in cfg:
                        row[k] = cfg[k]
            except json.JSONDecodeError:
                pass
        mr = RE_RESULT.search(ctext)
        if mr:
            try:
                res = json.loads(mr.group(1))
                for k in RESULT_COLS:
                    if k in res:
                        row[k] = res[k]
            except json.JSONDecodeError:
                pass
        # fall back to the status lines for plan info / trim time
        statuses = RE_STATUS.findall(ctext)
        if statuses:
            modes = [s[1] for s in statuses]
            row.setdefault("n_plans", "")
            if row["n_plans"] == "":
                row["n_plans"] = max(int(s[2]) for s in statuses)
            if row["final_offset_m"] == "":
                row["final_offset_m"] = int(statuses[-1][3])
            if row["trim_s"] == "":
                row["trim_s"] = sum(1 for md in modes if md.startswith("trim"))
            if row["replans"] == "":
                row["replans"] = sum(1 for md in modes if md == "replan")
    return row


def collect(logs_dir: Path) -> list[dict]:
    rows = []
    for folder in sorted(logs_dir.glob("*_COMPETITION_SIM__*")):
        r = parse_run(folder)
        if r:
            rows.append(r)
    return rows


def write_csv(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = BASE_COLS + CONFIG_COLS + RESULT_COLS
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def fmt(v, nd=1):
    if v == "" or v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def print_table(rows: list[dict]) -> None:
    hdr = f"{'stamp':14} {'tag':10} {'out':4} {'score':>6} {'time':>6} {'fuel':>5} {'dmg':>7} {'v':>4} {'att':>8} {'plans':>5} {'off':>4} {'dv':>5} {'git':>7}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['stamp']:14} {fmt(r['tag'])[:10]:10} {r['outcome']:4} {fmt(r['score']):>6} {fmt(r['elapsed_s']):>6} "
              f"{fmt(r['fuel_kg'], 2):>5} {fmt(r['damage_J'], 0):>7} {fmt(r['v_cruise']):>4} {fmt(r['ATT_MODE'])[:8]:>8} "
              f"{fmt(r['n_plans']):>5} {fmt(r['final_offset_m']):>4} {fmt(r['dv_used']):>5} {fmt(r['git'])[:7]:>7}")


def print_groups(rows: list[dict], keys: list[str]) -> None:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(str(r.get(k, "")) for k in keys), []).append(r)
    print(f"{' / '.join(keys):24} {'n':>3} {'mean':>6} {'best':>6} {'worst':>6} {'time':>6} {'fuel':>5} {'wins':>4}")
    for g, rs in sorted(groups.items(), key=lambda kv: -sum(r['score'] for r in kv[1]) / len(kv[1])):
        sc = [r["score"] for r in rs]
        ts = [r["elapsed_s"] for r in rs if r["elapsed_s"] != ""]
        fu = [r["fuel_kg"] for r in rs if r["fuel_kg"] != ""]
        print(f"{' / '.join(g):24} {len(rs):>3} {sum(sc)/len(sc):>6.1f} {max(sc):>6.1f} {min(sc):>6.1f} "
              f"{(sum(ts)/len(ts)) if ts else 0:>6.1f} {(sum(fu)/len(fu)) if fu else 0:>5.2f} "
              f"{sum(1 for r in rs if r['outcome']=='win'):>4}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--last", type=int, default=0, help="only show the last N runs")
    ap.add_argument("--group", type=str, default="", help="comma-separated columns to aggregate by, e.g. tag or v_cruise,ATT_MODE")
    ap.add_argument("--watch", action="store_true", help="poll for new finished runs and print them")
    args = ap.parse_args()

    if not args.logs.exists():
        sys.exit(f"log dir not found: {args.logs}")

    rows = collect(args.logs)
    write_csv(rows, args.out)
    shown = rows[-args.last:] if args.last else rows
    print_table(shown)
    if args.group:
        print()
        print_groups(rows, [k.strip() for k in args.group.split(",")])
    print(f"\n{len(rows)} runs -> {args.out}")

    if args.watch:
        seen = {r["stamp"] for r in rows}
        print("watching for new runs (Ctrl+C to stop)...")
        while True:
            time.sleep(5)
            rows = collect(args.logs)
            new = [r for r in rows if r["stamp"] not in seen]
            if new:
                write_csv(rows, args.out)
                print_table(new)
                seen |= {r["stamp"] for r in new}


if __name__ == "__main__":
    main()
