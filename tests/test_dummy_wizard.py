# tests/test_dummy_wizard.py
"""Step 4 unit tests: 10 kOhm Dummy-Cell Self-Test Wizard dialog."""

from __future__ import annotations

from pathlib import Path
import sys
import time

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pytest  # type: ignore[import-not-found]
from PySide6.QtWidgets import QApplication

from hunstat.dummy_wizard import DummyCellSelfTestDialog, make_dummy_lsv_params
from hunstat.gui import HunStatMainWindow
from hunstat.settings import SIMULATOR_PORT_DUMMY


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def test_dummy_lsv_params_specification() -> None:
    params = make_dummy_lsv_params()
    assert params.general.v_start == 0.0
    assert params.general.v_stop == 1.0
    assert params.cv.scan_rate == 0.1
    assert params.cv.half_cycle is True


def test_dummy_wizard_simulator_run_and_saturation_warning(qapp: QApplication) -> None:
    win = HunStatMainWindow(fast_sim=True)
    dlg: DummyCellSelfTestDialog = win.open_dummy_cell_wizard()

    # Check wiring instructions text mentions RE, CE, WE, and 10 kOhm
    instr = dlg.lbl_instructions.text()
    assert "RE" in instr and "CE" in instr and "WE" in instr and "10 k" in instr

    # Select Simulator (10 kOhm Dummy Cell) and run LSV 0->1 V @ 0.1 V/s
    idx = dlg.cmb_port.findData(SIMULATOR_PORT_DUMMY)
    assert idx >= 0
    dlg.cmb_port.setCurrentIndex(idx)

    assert dlg.start_self_test()
    deadline = time.monotonic() + 8.0
    while dlg.worker is not None and dlg.worker.isRunning() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()

    assert dlg.last_result is not None
    assert dlg.last_result.passed is True
    assert 9.5 <= dlg.last_result.resistance_kohm <= 10.5
    assert 95.0 <= dlg.last_result.current_at_1v_ua <= 105.0
    assert "PASS" in dlg.lbl_status.text()
    assert not dlg.lbl_sat_warning.isVisible()

    # Verify saturation warning when |I| > 160 uA (e.g., 5 kOhm resistor -> 200 uA at 1 V)
    e_sat = np.linspace(0.0, 1.0, 41)
    i_sat = 200.0 * e_sat
    res_bad = dlg.evaluate_points(e_sat, i_sat)
    assert res_bad.passed is False
    assert res_bad.saturation_warning is True
    assert "FAIL" in dlg.lbl_status.text()
    assert dlg.lbl_sat_warning.isVisible()
    assert "160" in dlg.lbl_sat_warning.text()

    dlg.close()
    win.close()