# tests/test_stage2.py
"""Stage 2 unit tests: DatasetRun/CycleData smoothing and full GUI against simulator."""

from __future__ import annotations

from pathlib import Path
import sys
import time

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # type: ignore[import-not-found]
from PySide6.QtWidgets import QApplication

from hunstat.gui import HunStatMainWindow
from hunstat.models import DatasetRun
from hunstat.settings import AppSettings, SIMULATOR_PORT_FIRST, make_paper_defaults


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def test_models_smoothing_non_destructive() -> None:
    ds = DatasetRun(name="Test CV", params=make_paper_defaults("CV"))
    cyc = ds.get_or_create_cycle(1)
    for i in range(30):
        e_v = -0.2 + i * 0.025
        # Add alternating noise on top of linear ramp
        i_ua = e_v * 50.0 + (2.0 if i % 2 == 0 else -2.0)
        cyc.append_point(e_v, i_ua)

    raw_snapshot = list(cyc.y_raw)
    ds.apply_smoothing("savgol", 9)
    assert cyc.y_raw == raw_snapshot
    assert cyc.y_display != raw_snapshot

    ds.apply_smoothing("moving_avg", 7)
    assert cyc.y_raw == raw_snapshot
    assert cyc.y_display != raw_snapshot

    ds.apply_smoothing("raw", 7)
    assert cyc.y_display == raw_snapshot


def test_gui_presets_validation_and_simulator_runs(
    qapp: QApplication, tmp_path: Path
) -> None:
    cfg = AppSettings(serial_port=SIMULATOR_PORT_FIRST)
    win = HunStatMainWindow(settings=cfg, fast_sim=True)

    # 1. Verify inline validation catches Start E >= End E and disables Run
    win.spin_start_e.setValue(0.5)
    win.spin_end_e.setValue(-0.2)
    assert not win.act_run.isEnabled()
    assert "ERROR" in win.lbl_validation.text()

    # 2. Save and reload JSON preset
    win.set_experiment_params(make_paper_defaults("DPV"))
    assert win.act_run.isEnabled()
    preset_file = tmp_path / "dpv_preset.json"
    assert win.save_preset_json(str(preset_file))
    win.set_experiment_params(make_paper_defaults("CV"))
    assert win.load_preset_json(str(preset_file))
    assert win.rb_dpv.isChecked()

    # 3. Verify "Show real values" formatting
    real_text = win.show_real_values_dialog()
    assert "DAC code" in real_text
    assert "i_StepE" in real_text

    # 4. Run CV against Simulator via GUI and wait for completion
    win.set_experiment_params(make_paper_defaults("CV"))
    assert win.start_run()
    deadline = time.monotonic() + 8.0
    while win.worker is not None and win.worker.isRunning() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()

    assert len(win.datasets) == 1
    assert win.datasets[0].total_points > 40
    assert win.tree_selection.topLevelItemCount() == 1
    assert win.edt_ipeak.text() != ""

    # 5. Apply Savitzky-Golay and Running Average from GUI Signal Processing tab
    win.apply_smoothing_to_selected("savgol")
    assert "SG" in win.datasets[0].smoothing_label
    win.apply_smoothing_to_selected("moving_avg")
    assert "Avg" in win.datasets[0].smoothing_label
    win.apply_smoothing_to_selected("raw")
    assert win.datasets[0].smoothing_label == "Raw"

    # 6. Theme toggle
    win.apply_theme(True)
    assert win.settings.dark_theme is True
    win.apply_theme(False)
    assert win.settings.dark_theme is False
    win.close()