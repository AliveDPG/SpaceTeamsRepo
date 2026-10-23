"""
autorun.py - Run COMPETITION_SIM hands-off, N times or through a sweep.

SpaceCRAFT must be open at the Sim menu with COMPETITION_SIM chosen (the state the client
returns to after a sim ends).  For each run this script:
  1. syncs the controller to the folder the sim runs (tools/sync_to_sim.py)
  2. writes run_overrides.json with AUTO_STOP_S so the controller ends the sim itself
  3. brings the Space Teams window to the front, verifies the green Confirm button by pixel
     colour, clicks it (re-selecting the COMPETITION_SIM row first if needed)
  4. waits for a new run log folder, then for the score line
  5. waits for the menu to come back, prints the result, repeats
If the screen is not what it expects it saves a screenshot next to this script and stops
instead of clicking blindly.  Move the mouse to a screen corner to abort.

    python tools/autorun.py --runs 3
    python tools/autorun.py --sweep            # configs from tools/sweep.json
    python tools/autorun.py --dry              # only check the window / button, no click

Pure ctypes; no extra packages.  Coordinates are fractions of the window so a different
resolution still works, but verify once with --dry.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import collect_runs  # noqa: E402

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.SetProcessDPIAware()

WINDOW_TITLE = "Space Teams Platform"
# fractions of the client area (measured on the 1920x1080 menu screenshot)
CONFIRM_XY = (1785 / 1920, 918 / 1080)
COMPETITION_ROW_XY = (1300 / 1920, 242 / 1080)
SIM_TAB_XY = (140 / 1920, 178 / 1080)
EXIT_SIM_XY = (1855 / 1920, 18 / 1080)        # 'Exit Sim' at the top right of the in-sim HUD
OVERRIDES = REPO / "run_overrides.json"


# ---------------------------------------------------------------- win32 helpers
def find_window():
    """Top-level window whose title contains WINDOW_TITLE (exact FindWindow can miss Unreal's title)."""
    found = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if WINDOW_TITLE.lower() in buf.value.lower():
                    found.append((hwnd, buf.value))
        return True
    user32.EnumWindows(cb, 0)
    if not found:
        return None
    found.sort(key=lambda t: -user32.GetWindowTextLengthW(t[0]))
    print(f"window title: {found[0][1]!r}")
    return found[0][0]


def client_rect(h):
    r = wt.RECT()
    user32.GetClientRect(h, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(h, ctypes.byref(pt))
    return pt.x, pt.y, r.right, r.bottom          # origin + size in screen px


def to_screen(h, frac):
    x0, y0, w, hgt = client_rect(h)
    return int(x0 + frac[0] * w), int(y0 + frac[1] * hgt)


def pixel(x, y):
    dc = user32.GetDC(0)
    c = gdi32.GetPixel(dc, x, y)
    user32.ReleaseDC(0, dc)
    return c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF


def is_green(rgb):
    r, g, b = rgb
    return g > 110 and g > r + 40 and g > b + 40


def focus(h):
    user32.ShowWindow(h, 9)                        # SW_RESTORE
    user32.SetForegroundWindow(h)
    time.sleep(0.4)


def click(x, y):
    user32.SetCursorPos(x, y)
    time.sleep(0.15)
    user32.mouse_event(2, 0, 0, 0, 0)               # left down
    time.sleep(0.08)
    user32.mouse_event(4, 0, 0, 0, 0)               # left up
    time.sleep(0.3)


def mouse_in_corner():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    w, hgt = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return (pt.x <= 1 or pt.x >= w - 2) and (pt.y <= 1 or pt.y >= hgt - 2)


def screenshot(path: Path):
    ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
          "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
          "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
          "$g=[System.Drawing.Graphics]::FromImage($bmp);$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
          f"$bmp.Save('{path}',[System.Drawing.Imaging.ImageFormat]::Png)")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)


def fail(msg):
    shot = REPO / "tools" / "autorun_fail.png"
    screenshot(shot)
    sys.exit(f"ABORT: {msg}\n(screenshot saved to {shot})")


# ---------------------------------------------------------------- sim interaction
def at_menu(h) -> bool:
    return is_green(pixel(*to_screen(h, CONFIRM_XY)))


def run_in_progress() -> bool:
    """Newest run folder is being written and has no score yet."""
    folders = sorted(collect_runs.DEFAULT_LOGS.glob("*_COMPETITION_SIM__*"))
    if not folders:
        return False
    log = folders[-1] / "COMPETITION_SIM.log"
    if not log.exists():
        return False
    recent = time.time() - log.stat().st_mtime < 15.0
    return recent and collect_runs.parse_run(folders[-1]) is None


def start_run(h, dry=False):
    if run_in_progress():
        fail("a run is already in progress; wait for it to finish (or stop it) before starting autorun")
    focus(h)
    if dry:
        print("dry run: menu detected" if at_menu(h) else "dry run: NOT at the Sim menu (Confirm button not green)",
              "- Confirm would be clicked at", to_screen(h, CONFIRM_XY))
        return
    if not at_menu(h):
        # a finished run is still on screen: leave it via the HUD's Exit Sim button
        print("  not at the menu; clicking Exit Sim")
        click(*to_screen(h, EXIT_SIM_XY))
        wait_for(lambda: at_menu(h), 45, "the menu after Exit Sim", poll=1.5)
        time.sleep(1.5)
        focus(h)
    if not at_menu(h):
        # only ever try to recover when we are certain no sim is running
        click(*to_screen(h, SIM_TAB_XY))
        time.sleep(0.8)
        click(*to_screen(h, COMPETITION_ROW_XY))
        time.sleep(0.8)
        if not at_menu(h):
            fail("Confirm button not found (not at the Sim menu?)")
    click(*to_screen(h, CONFIRM_XY))


def wait_for(predicate, timeout, what, poll=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if mouse_in_corner():
            sys.exit("aborted: mouse in screen corner")
        if predicate():
            return True
        time.sleep(poll)
    fail(f"timeout waiting for {what}")


def run_folders():
    return {p.name for p in collect_runs.DEFAULT_LOGS.glob("*_COMPETITION_SIM__*")}


def one_run(h, overrides: dict, auto_stop_s: float, dry=False):
    ov = dict(overrides)
    ov["AUTO_STOP_S"] = auto_stop_s
    OVERRIDES.write_text(json.dumps(ov, indent=2))
    subprocess.run([sys.executable, str(REPO / "tools" / "sync_to_sim.py")], check=False, capture_output=True)

    before = run_folders()
    start_run(h, dry)
    if dry:
        return None
    new = {}
    wait_for(lambda: bool(new.update({"f": run_folders() - before}) or new["f"]), 120, "the sim to start (new log folder)")
    folder = collect_runs.DEFAULT_LOGS / sorted(new["f"])[-1]
    print(f"  run started: {folder.name}")
    result = {}
    wait_for(lambda: bool(result.update({"r": collect_runs.parse_run(folder)}) or result["r"]), 420, "the score", poll=3.0)
    r = result["r"]
    print(f"  score={r['score']} time={r['elapsed_s']}s fuel={r['fuel_kg']}kg dmg={r['damage_J']}J "
          f"plans={r['n_plans']} off={r['final_offset_m']} dv={r['dv_used']}")
    # the controller stops the sim AUTO_STOP_S after the result; wait for the menu
    wait_for(lambda: at_menu(h), 120 + auto_stop_s, "the menu to come back", poll=2.0)
    time.sleep(2.0)
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--sweep", action="store_true", help="take configs from tools/sweep.json instead of --runs")
    ap.add_argument("--auto-stop", type=float, default=4.0, help="seconds after the result before the controller stops the sim")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    h = find_window()
    if not h:
        sys.exit(f"window '{WINDOW_TITLE}' not found - is SpaceCRAFT open?")
    print(f"window {h:#x} client rect {client_rect(h)}")

    try:
        if args.sweep:
            import sweep
            sw = sweep.load_sweep()
            while True:
                cfg = sweep.pick_next(sw, sweep.runs_per_tag())
                if cfg is None:
                    print("sweep complete")
                    sweep.status(sw, sweep.runs_per_tag())
                    break
                print(f"\n=== config {cfg['tag']} ===")
                if one_run(h, cfg, args.auto_stop, args.dry) is None:
                    break
        else:
            for i in range(args.runs):
                print(f"\n=== run {i + 1}/{args.runs} ===")
                if one_run(h, {"tag": "autorun"}, args.auto_stop, args.dry) is None:
                    break
    finally:
        # leave the controller in its normal (no auto-stop) state for manual runs
        if OVERRIDES.exists() and not args.sweep:
            OVERRIDES.unlink()
            subprocess.run([sys.executable, str(REPO / "tools" / "sync_to_sim.py")], check=False, capture_output=True)
    rows = collect_runs.collect(collect_runs.DEFAULT_LOGS)
    collect_runs.write_csv(rows, collect_runs.DEFAULT_OUT)
    print(f"\nresults -> {collect_runs.DEFAULT_OUT}")


if __name__ == "__main__":
    main()
