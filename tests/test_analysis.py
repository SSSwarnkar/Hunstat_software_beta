# tests/test_analysis.py
"""Step 2 unit tests for hunstat/analysis.py (paper values + simulator synthetic datasets)."""

from __future__ import annotations

from pathlib import Path
import sys

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pytest  # type: ignore[import-not-found]

from hunstat.analysis import (
    baseline_linear,
    ca_current_at,
    calibration_fit,
    concentration_from_current,
    cv_metrics,
    diode_turn_on,
    dummy_cell_fit,
    find_peak,
    randles_sevcik_fit,
    standard_addition,
    subtract_baseline,
)
from hunstat.models import CycleData, DatasetRun
from hunstat.settings import (
    SIMULATOR_PORT_DUMMY,
    SIMULATOR_PORT_FIRST,
    ExperimentParams,
    make_paper_defaults,
)
from hunstat.worker import SerialRunWorker


def _collect_simulator_cycle(port: str, params: ExperimentParams) -> CycleData:
    ds = DatasetRun(name="SimTest", technique=params.technique, params=params, port=port)
    worker = SerialRunWorker(port=port, params=params, fast_sim=True)
    worker.point.connect(
        lambda c, x, y, sat: ds.get_or_create_cycle(c).append_point(x, y, sat)
    )
    worker.start()
    assert worker.wait(10000), "Simulator worker did not finish in time"
    assert len(ds.cycles) >= 1
    return ds.cycles[-1]


def test_no_qt_imported_in_analysis() -> None:
    import hunstat.analysis as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "PySide6" not in src
    assert "pyqtgraph" not in src


def test_baseline_linear_subtract_and_find_peak() -> None:
    e_v = np.linspace(-0.2, 0.6, 81)
    true_base = 10.0 * e_v + 2.0
    gauss_peak = 35.0 * np.exp(-0.5 * ((e_v - 0.25) / 0.04) ** 2)
    i_ua = true_base + gauss_peak

    b_est = baseline_linear(e_v, i_ua, p1=(-0.2, true_base[0]), p2=(0.6, true_base[-1]))
    corr = subtract_baseline(i_ua, b_est)
    np.testing.assert_allclose(b_est, true_base, atol=1e-6)
    np.testing.assert_allclose(corr, gauss_peak, atol=1e-6)

    ep, ip = find_peak(e_v, i_ua, baseline=b_est, polarity="anodic")
    assert abs(ep - 0.25) < 0.01
    assert abs(ip - 35.0) < 0.2


def test_paper_ascorbic_acid_calibration_and_lod() -> None:
    conc_mm = np.array([0.5, 1.0, 2.0, 3.0, 4.0, 5.0])
    i_ua = 5.865 * conc_mm + 4.06

    fit = calibration_fit(conc_mm, i_ua, sigma_blank=0.1955)
    assert abs(fit.slope - 5.865) < 1e-6
    assert abs(fit.intercept - 4.06) < 1e-6
    assert abs(fit.r_squared - 1.0) < 1e-9
    assert fit.lod is not None
    assert abs(fit.lod - (3.0 * 0.1955 / 5.865)) < 1e-6

    unknown_c = fit.concentration_from_current(18.611)
    assert abs(float(unknown_c) - 2.48) < 0.005
    assert abs(float(concentration_from_current(18.611, fit)) - 2.48) < 0.005


def test_paper_standard_addition_pb_and_cd() -> None:
    c_added_ppm = np.array([0.0, 0.5, 1.0, 1.5, 2.0])

    i_pb = 20.11 * c_added_ppm + 9.77
    res_pb = standard_addition(c_added_ppm, i_pb, dilution_factor=20.0)
    assert abs(res_pb.slope - 20.11) < 1e-6
    assert abs(res_pb.intercept - 9.77) < 1e-6
    assert abs(res_pb.x_intercept - (-0.486)) < 0.001
    assert abs(res_pb.original_concentration - 9.72) < 0.01

    i_cd = 22.17 * c_added_ppm + 10.93
    res_cd = standard_addition(c_added_ppm, i_cd, dilution_factor=20.0)
    assert abs(res_cd.slope - 22.17) < 1e-6
    assert abs(res_cd.intercept - 10.93) < 1e-6
    assert abs(res_cd.x_intercept - (-0.493)) < 0.001
    assert abs(res_cd.original_concentration - 9.86) < 0.01


def test_diode_turn_on_and_ca_current_at() -> None:
    e_v = np.linspace(0.0, 1.0, 101)
    i_ua = np.where(e_v < 0.55, 0.2 * e_v, 0.11 + 400.0 * (e_v - 0.55) ** 2)
    v_on = diode_turn_on(e_v, i_ua, threshold_uA=10.0)
    assert v_on is not None
    assert 0.65 <= v_on <= 0.72
    assert diode_turn_on(e_v, i_ua, threshold_uA=5000.0) is None

    ca_params = make_paper_defaults("CA")
    ca_params.general.t_eq = 2
    ca_params.ca.ca_time_s = 10
    ca_params.ca.ca_datapoints = 120
    ca_cyc = _collect_simulator_cycle(SIMULATOR_PORT_FIRST, ca_params)

    i_1s = ca_current_at(ca_cyc.x, ca_cyc.y_raw, t_query=1.0, t_step=2.0)
    i_8s = ca_current_at(ca_cyc.x, ca_cyc.y_raw, t_query=8.0, t_step=2.0)
    assert i_1s > i_8s > 10.0


def test_cv_metrics_randles_sevcik_and_dummy_cell_on_simulator() -> None:
    scan_rates = [0.02, 0.05, 0.10, 0.20]
    ipa_list: list[float] = []

    for sr in scan_rates:
        p = make_paper_defaults("CV")
        p.general.t_eq = 0
        p.cv.scan_rate = sr
        cyc = _collect_simulator_cycle(SIMULATOR_PORT_FIRST, p)
        m = cv_metrics(cyc)
        assert 0.15 <= m.E_half <= 0.25
        assert 0.03 <= m.delta_Ep <= 0.12
        assert m.Ipa > 0.0 and m.Ipc < 0.0
        assert 0.70 <= m.Ipa_Ipc <= 1.35
        ipa_list.append(m.Ipa)

    rs = randles_sevcik_fit(scan_rates, ipa_list)
    assert rs.slope > 200.0
    assert rs.r_squared > 0.99

    dummy_p = make_paper_defaults("DUMMY")
    dummy_cyc = _collect_simulator_cycle(SIMULATOR_PORT_DUMMY, dummy_p)
    dfit = dummy_cell_fit(dummy_cyc.x, dummy_cyc.y_raw)
    assert dfit.passed
    assert 9.5 <= dfit.resistance_kohm <= 10.5
    assert 95.0 <= dfit.current_at_1v_ua <= 105.0
    assert not dfit.saturation_warning

    e_bad = np.linspace(0.0, 1.0, 50)
    i_bad = 200.0 * e_bad
    dfit_bad = dummy_cell_fit(e_bad, i_bad)
    assert not dfit_bad.passed
    assert abs(dfit_bad.resistance_kohm - 5.0) < 1e-4
    assert dfit_bad.saturation_warning