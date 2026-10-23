| [Rules & Info](https://github.com/SimDynamX/SpaceTeamsPRO/discussions/218) | [Competitor Starter Code (Here)](https://github.com/SimDynamX/STU4-Solution) | [Forum](https://github.com/SimDynamX/SpaceTeamsPRO/discussions/categories/2026-escape-from-phobos)
| ----- | ----- | ----- |

# Escape from Phobos - Starter Code
All info is on the wiki here: https://github.com/SimDynamX/STU4-Solution/wiki

---

# Our controller

| File | What it does |
|---|---|
| `Controller.py` | Sim glue: reads state/radar, schedules camera captures, attitude PD, status log. Boilerplate above the markers is untouched. |
| `nav.py` | `ShuttleNav` (state fusion + time history), `CVTrack`/`DebrisTracker` (world-frame constant-velocity Kalman tracks, radar + camera fusion, radar clock-offset estimate), `Guidance` (tube filter, planned-path closest approach, burn/coast/trim law). Pure numpy. |
| `vision.py` | Binary threshold + connected-component blob finder, pixel → bearing, camera yaw/pitch geometry (wiki convention). |
| `sim_offline.py` | Point-mass rehearsal with the competition numbers (0.5 m/s², 500 debris / 200 radar-visible, 120 Hz round-robin radar noise model). `python sim_offline.py 8` |

## Design

* **World frame.** Every radar return is `shuttle_pos(t_meas) + rel` using `ShuttleNav.pos_at(t_meas)`, so our own burns never look like debris velocity changes. The radar stamps with its *own* start time; `DebrisTracker` estimates the offset as the running minimum of `(t_now − newest stamp)`.
* **Tracks.** Constant-velocity KF per rock with a slow-drift prior (σ_v0 = 0.05 m/s, σ_a = 0.01 m/s²) so 2 m radar noise averages down instead of becoming velocity. Association is nearest-neighbour in the world frame with a size-consistency check; duplicates are rejected by (stamp, position).
* **Camera fusion.** Blob bearing + radar range → anisotropic position update (tight across the line of sight, radar σ along it). If the image object exposes depth (`as_Depth*`/`PixelsDepth` — probed and logged at the first capture), camera-only tracks are created for the ~300 rocks the radar never reports.
* **Tube + closest approach.** Our *planned* trajectory (burn to `v_cruise`, then coast) is sampled against every track; only tracks whose closest approach is within `tube_radius` of the path count. A conflict is `miss < rock radius + 3 m shuttle + 4 m margin + 3σ`.
* **One burn, coast, trims.** Along-track: accelerate at 0.5 m/s² to `v_cruise`, then coast with a ±0.15 m/s deadband. Cross-track: when a conflict exists, pick the perpendicular direction needing the least Δv to clear *all* threats (exact forbidden-interval solve), with 2 m hysteresis; drift back to the corridor only if the correction is worth it (> 1 m/s far, > 0.3 m/s near) and provably safe. Final 20 s: pursuit straight at the target.
* **Attitude.** Body +x on the velocity vector so the cameras look where we are going; PD saturated at 0.05 rad/s².

## Fuel is the binding constraint

`JetSelectionActuator.py` accrues fuel **per 60 Hz tick without dt**, so fuel ≈ **0.0765 kg per m/s of commanded Δv** (translation *and* the translational components of rotation jets, in absolute value). The 1.15 kg nominal is therefore a **~15 m/s Δv budget**; the sim's `Scoring.py` charges `max(fuel/nominal − 1, 0)`, i.e. ≈ 6.7 score points per m/s over budget, nothing under it. With `T = V + 1256/V` the optimum is a single burn to ~14–15 m/s (≈100 s, ≈165–170 points) — not the fastest possible flight. First real run (22 m/s, 60 m aim offset, continuous attitude hold): 81.5 s, 0 damage, 2.46 kg → 83.7.

Consequences built in: the coasting search scores candidates in raw-score units (`−120/t + fuel_penalty`, including the terminal-steer Δv of an aim offset), `Guidance.dv_used` tracks commanded Δv, and attitude control is deadbanded (`ATT_MODE`).

## Knobs (top of `Controller.py`)

`v_cruise` / `v_arrive` (14 m/s), `ATT_MODE` / `ATT_DEADBAND_DEG`, `tube_radius`, `margin_m`, `k_sigma`, `plan_offsets_m` / `plan_speed_fracs`, `CAMERA_PERIOD_S`, `BLOB_THRESHOLD`, `CAMERA_ONLY_WHEN_COASTING`.

## Things to verify in the real sim (logged on screen)

1. `Attitude convention: R|RT|frames` — which quaternion convention matched the API.
2. `Image API attrs: [...]` — whether a depth accessor exists.
3. `tracks=` should settle near 200 (radar `NumDebris` in the sim config is 200 of 500 spawned).
4. If blobs appear mirrored (camera trims go the wrong way), flip `right_is_minus_y` in `vision.pixel_to_dir_cam`.

## Testing workflow (real sim)

The sim runs **`%LOCALAPPDATA%\SimDynamX\STPro\Assets\Repos\STU4-Solution\Controller.py`**, not this folder, and every run leaves a log folder under `%LOCALAPPDATA%\SimDynamX\STPro\Logs\`. Physics runs in the Unreal client (`Main_Interactive`), so the headless `run_Server.exe` runner cannot score this sim: starting a run (Sim → User made sim → `COMPETITION_SIM` → Confirm) stays a manual click. Everything around it is scripted:

| Command | What it does |
|---|---|
| `python tools/sync_to_sim.py` | Copies `Controller.py`/`nav.py`/`vision.py`/`run_overrides.json` to the folder the sim runs (backs up what was there, clears `__pycache__`). `--check` only reports; `--junction` replaces that folder with a link to this repo so there is one source of truth. |
| `python tools/collect_runs.py` | Harvests every run: score, time, damage, fuel, plus the controller's `CONFIG`/`RESULT` banners (tag, git hash, cruise speed, Δv used, plans…) → `results/runs.csv` and a table. `--group tag` aggregates; `--watch` prints each new run as it finishes. |
| `python tools/sweep.py auto` | Steps through `tools/sweep.json`: writes `run_overrides.json` for the next config, syncs, waits for the run to finish, repeats. You only click Start each time. `status` / `next` / `clear` also available. |

`run_overrides.json` keys are `GuidanceParams` fields or `Controller.py` globals (`ATT_MODE`, `CAMERA_ENABLED`, …) plus a `tag`; the controller logs the full effective config at start-up so every row in `runs.csv` is reproducible.
