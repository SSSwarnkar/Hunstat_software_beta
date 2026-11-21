# cli_test.py
"""Command-line smoke test exercising CV, LSV, DPV, CA, and Dummy Cell on the simulator."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from hunstat.protocol import build_p_command, compute_real_values, validate_params
from hunstat.settings import SIMULATOR_PORT_DUMMY, SIMULATOR_PORT_FIRST, make_paper_defaults
from hunstat.worker import SerialRunWorker, probe_port


def run_single_preset(preset: str, port: str, fast: bool, verbose: bool) -> bool:
    params = make_paper_defaults(preset)
    val = validate_params(params)
    if not val.ok:
        print(f"[FAIL] {preset}: validation errors: {val.errors}")
        return False

    p_cmd = build_p_command(params)
    real = compute_real_values(params)
    points: list[tuple[int, float, float, bool]] = []
    errors: list[str] = []

    worker = SerialRunWorker(port=port, params=params, fast_sim=fast)
    worker.point.connect(lambda c, x, y, sat: points.append((c, x, y, sat)))
    worker.error.connect(lambda msg: errors.append(msg))
    if verbose:
        worker.log_line.connect(lambda line: print(f"  {line}"))

    worker.start()
    worker.wait(30000)

    if errors:
        print(f"[FAIL] {preset}: worker error: {errors[0]}")
        return False
    if not points:
        print(f"[FAIL] {preset}: 0 points collected")
        return False

    y_vals = [p[2] for p in points]
    min_y = min(y_vals)
    max_y = max(y_vals)
    print(
        f"[PASS] {preset:<5} | cmd='{p_cmd}' (len={len(p_cmd)}) | "
        f"real V=[{real.v_start_real:+.4f}, {real.v_stop_real:+.4f}] V | "
        f"points={len(points):4d} | I=[{min_y:+.2f}, {max_y:+.2f}] uA"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="HunStat Stage 1 CLI Verification")
    parser.add_argument("--port", default=SIMULATOR_PORT_FIRST, help="Serial or Simulator port")
    parser.add_argument("--real-time", action="store_true", help="Disable fast simulator mode")
    parser.add_argument("--verbose", action="store_true", help="Print raw TX/RX serial log")
    args = parser.parse_args()

    fast = not args.real_time
    print(f"Probing port '{args.port}' (fast_sim={fast})...")
    if not probe_port(args.port, fast_sim=fast):
        print(f"[FAIL] Probe '!' did not return '#' on {args.port}")
        return 1
    print("[PASS] Probe '!' -> '#' OK")

    ok = True
    for preset in ("CV", "LSV", "DPV", "CA"):
        ok = run_single_preset(preset, args.port, fast=fast, verbose=args.verbose) and ok
    ok = run_single_preset("DUMMY", SIMULATOR_PORT_DUMMY, fast=fast, verbose=args.verbose) and ok

    if ok:
        print("All Stage 1 CLI checks PASSED.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())