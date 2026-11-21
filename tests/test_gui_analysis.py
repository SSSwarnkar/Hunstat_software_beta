# tests/test_gui_analysis.py
"""Step 3 unit tests: Analysis menu/dock tools in hunstat/gui_analysis.py."""

from __future__ import annotations

from pathlib import Path
import sys

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # type: ignore[import-not-found]
from PySide6.QtWidgets import QApplication

from hunstat.gui import HunStatMainWindow
from hunstat.models import DatasetRun
from hunstat.settings import TECH_CV, make_paper_defaults


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def _make_synthetic_cv_dataset(name: str, scan_rate: float) -> DatasetRun:
    import math

    params = make_paper_defaults("CV")
    params.cv.scan_rate = scan_rate
    ds = DatasetRun(name=name, technique=TECH_CV, params=params)
    cyc = ds.get_or_create_cycle(1)
    sqrt_v = math.sqrt(scan_rate)
    # Forward sweep -0.2 to 0.6 V
    for i in range(41):
        e_v = -0.2 + i * 0.02
        i_ua = 10.0 * e_v + 250.0 * sqrt_v * math.exp(-0.5 * ((e_v - 0.23) / 0.04) ** 2)
        cyc.append_point(e_v, i_ua)
    # Reverse sweep 0.6 to -0.2 V
    for i in range(41):
        e_v = 0.6 - i * 0.02
        i_ua = 10.0 * e_v - 250.0 * sqrt_v * math.exp(-0.5 * ((e_v - 0.17) / 0.04) ** 2)
        cyc.append_point(e_v, i_ua)
    return ds


def test_analysis_dock_tools(qapp: QApplication) -> None:
    win = HunStatMainWindow(fast_sim=True)
    assert hasattr(win, "analysis_dock")
    dock = win.analysis_dock

    # Load 3 synthetic CV datasets at different scan rates
    for sr in (0.025, 0.05, 0.10):
        win.datasets.append(_make_synthetic_cv_dataset(f"CV_{int(sr*1000)}mVs", sr))
    win.active_dataset = win.datasets[-1]
    win._rebuild_selection_tree()
    dock.on_dataset_selection_changed()

    # 1. Baseline + Peak picking updates edt_ipeak and overlay
    dock.spin_base_e1.setValue(-0.18)
    dock.spin_base_e2.setValue(-0.05)
    peak = dock.compute_baseline_and_peak()
    assert peak is not None
    assert abs(peak.Ep - 0.23) < 0.03
    assert peak.Ip > 50.0
    assert win.edt_ipeak.text() == f"{peak.Ip:.3f}"

    # 2. CV Metrics Table
    cv_rows = dock.compute_cv_metrics_table()
    assert len(cv_rows) == 1
    assert dock.tbl_cv.rowCount() == 1
    assert abs(cv_rows[0].E_half - 0.20) < 0.03

    # 3. Randles-Sevcik tool across the 3 loaded datasets
    dock.refresh_randles_dataset_table()
    assert dock.tbl_rs.rowCount() == 3
    rs_res = dock.run_randles_sevcik_from_table()
    assert rs_res is not None
    assert rs_res.r_squared > 0.99

    # 4. Calibration Curve tool (paper Ascorbic Acid preset)
    cal_out = dock.run_calibration_from_ui()
    assert cal_out is not None
    cal_fit, c_unk = cal_out
    assert abs(cal_fit.slope - 5.865) < 1e-4
    assert abs(c_unk - 2.48) < 0.01

    # 5. Standard Addition tool (paper Pb & Cd presets)
    dock._load_paper_pb_std_addition()
    pb_res = dock.run_standard_addition_from_ui()
    assert pb_res is not None
    assert abs(pb_res.original_concentration - 9.72) < 0.02

    dock._load_paper_cd_std_addition()
    cd_res = dock.run_standard_addition_from_ui()
    assert cd_res is not None
    assert abs(cd_res.original_concentration - 9.86) < 0.02

    # 6. Diode Turn-On tool
    dock.spin_diode_thresh.setValue(15.0)
    v_on = dock.compute_diode_turn_on_ui()
    assert v_on is not None
    win.close()