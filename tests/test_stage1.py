# tests/test_stage1.py
"""Stage 1 unit tests: protocol builders, validation, parser, simulator, abort, unplug, park_safe."""

from __future__ import annotations

from pathlib import Path
import sys
import time

# Ensure project root is on sys.path even when invoked via bare `pytest`
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # type: ignore[import-not-found]

from hunstat.protocol import (
    build_p_command,
    build_park_p_command,
    clamp_potential,
    compute_real_values,
    mv_to_dpv_code,
    parse_device_line,
    real_dpv_mv,
    real_potential_v,
    v_to_dac_code,
    validate_params,
)
from hunstat.settings import (
    MAX_P_COMMAND_LEN,
    SIMULATOR_PORT_DUMMY,
    SIMULATOR_PORT_FIRST,
    TECH_CA,
    TECH_CV,
    TECH_DPV,
    ExperimentParams,
    make_paper_defaults,
)
from hunstat.simulator import SimulatedSerialPort
from hunstat.worker import SerialRunWorker, park_safe, probe_port


def test_dac_and_dpv_quantisation() -> None:
    assert v_to_dac_code(0.0) == 512
    assert abs(real_potential_v(0.0)) < 1e-9
    # StepE < 1.61 mV gives 0 in firmware: int((1.60/3300*1024)+0.5) == 0
    assert mv_to_dpv_code(1.60) == 0
    assert mv_to_dpv_code(1.61) == 1
    assert mv_to_dpv_code(3.0) == 1
    assert abs(real_dpv_mv(3.0) - (3300.0 / 1024.0)) < 1e-6

    real_vals = compute_real_values(make_paper_defaults("DPV"))
    assert real_vals.i_step_e == 1
    assert real_vals.i_pulse_amplitude == 16


def test_p_command_format_and_length() -> None:
    for preset in ("CV", "LSV", "DPV", "CA", "DUMMY"):
        params = make_paper_defaults(preset)
        cmd = build_p_command(params)
        assert cmd.startswith("P ")
        assert len(cmd) <= MAX_P_COMMAND_LEN
        fields = cmd[2:].split(",")
        assert len(fields) == 18
        # Verify teq (idx 5) and CATime (idx 9) are formatted as integers
        assert "." not in fields[5]
        assert "." not in fields[9]


def test_validation_rules() -> None:
    # Paper defaults must all pass validation with no errors or warnings
    for preset in ("CV", "LSV", "DPV", "CA"):
        res = validate_params(make_paper_defaults(preset))
        assert res.ok, f"{preset} failed validation: {res.errors}"
        assert len(res.warnings) == 0

    # Downward scan must fail
    bad_dir = make_paper_defaults("CV")
    bad_dir.general.v_start = 0.5
    bad_dir.general.v_stop = -0.2
    assert not validate_params(bad_dir).ok

    # Out-of-range voltage (> 1.60 V) must fail
    bad_v = make_paper_defaults("CV")
    bad_v.general.v_stop = 1.63
    assert not validate_params(bad_v).ok

    # DPV Vstop + amplitude > 1.60 V must fail
    bad_dpv = make_paper_defaults("DPV")
    bad_dpv.general.v_start = 1.0
    bad_dpv.general.v_stop = 1.58
    bad_dpv.dpv.pulse_amplitude_mv = 50.0  # 1.58 + 0.05 = 1.63 > 1.60
    assert not validate_params(bad_dpv).ok

    # DPV StepE < 1.61 mV must fail
    bad_step = make_paper_defaults("DPV")
    bad_step.dpv.step_e_mv = 1.5
    assert not validate_params(bad_step).ok

    # DPV timing violation must warn
    warn_dpv = make_paper_defaults("DPV")
    warn_dpv.dpv.pulse_period_ms = 50
    warn_dpv.dpv.pulse_width_ms = 50
    warn_dpv.dpv.sampling_period_ms = 3
    res_warn = validate_params(warn_dpv)
    assert res_warn.ok
    assert len(res_warn.warnings) >= 1

    # CA datapoints > 999 must fail
    bad_ca = make_paper_defaults("CA")
    bad_ca.ca.ca_datapoints = 1000
    assert not validate_params(bad_ca).ok


def test_parse_device_line() -> None:
    p_data = parse_device_line("-0.2000,-11.1182,\r\n")
    assert p_data.kind == "data"
    assert abs(p_data.x - (-0.2)) < 1e-6
    assert abs(p_data.y - (-11.1182)) < 1e-6
    assert not p_data.saturated

    p_sat = parse_device_line("0.5000,162.5000,\r\n")
    assert p_sat.kind == "data"
    assert p_sat.saturated

    assert parse_device_line("*\r\n").kind == "section"
    assert parse_device_line("@\r\n").kind == "end_cv"
    assert parse_device_line("$\r\n").kind == "end_dpv"
    assert parse_device_line("%\r\n").kind == "end_ca"
    assert parse_device_line("#\r\n").kind == "ack"


def _run_worker_sync(
    port: str | SimulatedSerialPort, params: ExperimentParams
) -> tuple[list[tuple[int, float, float, bool]], list[int], list[bool], list[str]]:
    points: list[tuple[int, float, float, bool]] = []
    sections: list[int] = []
    finished_flags: list[bool] = []
    errors: list[str] = []

    worker = SerialRunWorker(port=port, params=params, fast_sim=True)
    worker.point.connect(lambda c, x, y, sat: points.append((c, x, y, sat)))
    worker.section_started.connect(lambda c: sections.append(c))
    worker.finished.connect(lambda aborted: finished_flags.append(aborted))
    worker.error.connect(lambda msg: errors.append(msg))

    worker.start()
    deadline = time.monotonic() + 10.0
    while worker.isRunning() and time.monotonic() < deadline:
        try:
            from PySide6.QtCore import QCoreApplication
            if QCoreApplication.instance() is not None:
                QCoreApplication.processEvents()
        except ImportError:
            pass
        time.sleep(0.01)
    try:
        from PySide6.QtCore import QCoreApplication
        if QCoreApplication.instance() is not None:
            QCoreApplication.processEvents()
    except ImportError:
        pass
    assert not worker.isRunning(), "Worker did not finish within 10 s"
    return points, sections, finished_flags, errors


def test_simulator_probe_and_park() -> None:
    sim = SimulatedSerialPort(fast_mode=True)
    try:
        assert probe_port(sim, timeout_s=1.0)
        assert park_safe(sim, v_park=0.1)
        assert sim.dac_volt == v_to_dac_code(0.1)
    finally:
        sim.close()


def test_worker_cv_lsv_dpv_ca_and_dummy() -> None:
    # 1. CV (2 cycles)
    cv_params = make_paper_defaults("CV")
    cv_params.cv.cycles = 2
    pts, secs, fin, errs = _run_worker_sync(SIMULATOR_PORT_FIRST, cv_params)
    assert not errs
    assert fin == [False]
    assert secs == [1, 2]
    assert len(pts) > 40

    # 2. LSV (HalfCycle = True, even if cycles=3 firmware forces 1 cycle)
    lsv_params = make_paper_defaults("LSV")
    lsv_params.cv.cycles = 3
    pts, secs, fin, errs = _run_worker_sync(SIMULATOR_PORT_FIRST, lsv_params)
    assert not errs
    assert fin == [False]
    assert secs == [1]
    assert pts[-1][1] > pts[0][1]  # Forward sweep only

    # 3. DPV
    dpv_params = make_paper_defaults("DPV")
    pts, secs, fin, errs = _run_worker_sync(SIMULATOR_PORT_FIRST, dpv_params)
    assert not errs
    assert fin == [False]
    assert secs == [1]
    assert len(pts) > 50

    # 4. CA (verify pre-step baseline points before teq and Cottrell decay after teq)
    ca_params = make_paper_defaults("CA")
    ca_params.ca.ca_datapoints = 50
    pts, secs, fin, errs = _run_worker_sync(SIMULATOR_PORT_FIRST, ca_params)
    assert not errs
    assert fin == [False]
    assert len(pts) == 50
    pre_step = [p for p in pts if p[1] <= ca_params.general.t_eq]
    post_step = [p for p in pts if p[1] > ca_params.general.t_eq]
    assert len(pre_step) >= 1
    assert post_step[0][2] > post_step[-1][2]  # Cottrell decay decreases with time

    # 5. Dummy 10 kOhm cell (0 to 1 V -> ~0 to 100 uA)
    dummy_params = make_paper_defaults("DUMMY")
    pts, secs, fin, errs = _run_worker_sync(SIMULATOR_PORT_DUMMY, dummy_params)
    assert not errs
    assert fin == [False]
    max_e, max_i = max((p[1], p[2]) for p in pts)
    assert 0.99 <= max_e <= 1.01
    assert 98.0 <= max_i <= 102.0


def test_worker_abort_and_unplug() -> None:
    # Test abort mid-run and verify park_safe resets DAC to 0 V
    sim = SimulatedSerialPort(cell_mode="fe_cn6", fast_mode=False)
    try:
        params = make_paper_defaults("CV")
        params.general.t_eq = 5  # Long equilibration so we can abort cleanly
        finished_flags: list[bool] = []
        worker = SerialRunWorker(port=sim, params=params, v_park=0.0, fast_sim=False)
        worker.finished.connect(lambda aborted: finished_flags.append(aborted))
        worker.start()
        time.sleep(1.3)  # Wait for P command ack and S start
        worker.request_abort()
        deadline = time.monotonic() + 8.0
        while worker.isRunning() and time.monotonic() < deadline:
            try:
                from PySide6.QtCore import QCoreApplication
                if QCoreApplication.instance() is not None:
                    QCoreApplication.processEvents()
            except ImportError:
                pass
            time.sleep(0.01)
        try:
            from PySide6.QtCore import QCoreApplication
            if QCoreApplication.instance() is not None:
                QCoreApplication.processEvents()
        except ImportError:
            pass
        assert not worker.isRunning(), "Abort did not complete within timeout"
        assert finished_flags == [True]
        assert sim.dac_volt == v_to_dac_code(0.0)
    finally:
        sim.close()

    # Test sudden unplug mid-run emits error cleanly without crashing
    sim_unplug = SimulatedSerialPort(cell_mode="fe_cn6", fast_mode=False)
    try:
        params = make_paper_defaults("CV")
        params.general.t_eq = 5
        errors: list[str] = []
        worker2 = SerialRunWorker(port=sim_unplug, params=params, fast_sim=False)
        worker2.error.connect(lambda msg: errors.append(msg))
        worker2.start()
        time.sleep(0.3)
        sim_unplug.simulate_unplug()
        deadline2 = time.monotonic() + 5.0
        while worker2.isRunning() and time.monotonic() < deadline2:
            try:
                from PySide6.QtCore import QCoreApplication
                if QCoreApplication.instance() is not None:
                    QCoreApplication.processEvents()
            except ImportError:
                pass
            time.sleep(0.01)
        try:
            from PySide6.QtCore import QCoreApplication
            if QCoreApplication.instance() is not None:
                QCoreApplication.processEvents()
        except ImportError:
            pass
        assert not worker2.isRunning()
        assert len(errors) == 1
    finally:
        sim_unplug.close()