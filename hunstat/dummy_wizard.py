# hunstat/dummy_wizard.py
"""Guided 10 kOhm Dummy-Cell Self-Test Wizard dialog (Step 4).

Displays RE+CE / WE 10 kOhm wiring instructions, executes an LSV sweep from
0.0 V to 1.0 V at 0.1 V/s via SerialRunWorker (on the simulator dummy cell or
real hardware), fits the Ohmic line via dummy_cell_fit(), and reports measured
resistance (kOhm), current at 1 V (uA), PASS/FAIL status, and |I| > 160 uA
saturation warnings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pyqtgraph as pg  # type: ignore[import-untyped]
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .analysis import DummyCellResult, dummy_cell_fit
from .models import DatasetRun
from .settings import (
    CURRENT_SATURATION_WARN_UA,
    SIMULATOR_PORT_DUMMY,
    SIMULATOR_PORT_FIRST,
    TECH_CV,
    CVParams,
    ExperimentParams,
    GeneralParams,
)
from .worker import SerialRunWorker, list_candidate_ports

if TYPE_CHECKING:
    from .gui import HunStatMainWindow


def make_dummy_lsv_params() -> ExperimentParams:
    """Return strict LSV 0.0 V -> 1.0 V @ 0.1 V/s parameters for the 10 kOhm self-test."""
    return ExperimentParams(
        technique=TECH_CV,
        general=GeneralParams(
            v_start=0.0,
            v_stop=1.0,
            v_eq=0.0,
            t_eq=0,
            v_after=0.0,
            e_apply_after=False,
            i_range_ua=120.0,
            no_e_readback=True,
        ),
        cv=CVParams(scan_rate=0.1, cycles=1, half_cycle=True),
    )


class DummyCellSelfTestDialog(QDialog):
    """Guided 10 kOhm dummy-cell self-test wizard dialog."""

    def __init__(
        self,
        main_window: HunStatMainWindow | None = None,
        fast_sim: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.fast_sim = fast_sim or (
            bool(getattr(main_window, "fast_sim", False)) if main_window is not None else False
        )
        self.worker: SerialRunWorker | None = None
        self.last_dataset: DatasetRun | None = None
        self.last_result: DummyCellResult | None = None

        self._e_points: list[float] = []
        self._i_points: list[float] = []

        self.setWindowTitle("10 kΩ Dummy-Cell Self-Test Wizard")
        self.resize(680, 580)
        self._build_ui()
        self._populate_ports()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # 1. Wiring instructions group
        grp_wiring = QGroupBox("Step 1 — Dummy-Cell Wiring Instructions")
        w_lay = QVBoxLayout(grp_wiring)
        self.lbl_instructions = QLabel(
            "<b>Before running the self-test, wire a 10 kΩ (±1%) resistor as follows:</b>"
            "<ol>"
            "<li>Join the <b>Reference Electrode (RE)</b> and <b>Counter Electrode (CE)</b> clips together.</li>"
            "<li>Connect one leg of a <b>10 kΩ resistor</b> to the joined <b>RE + CE</b> clips.</li>"
            "<li>Connect the other leg of the <b>10 kΩ resistor</b> to the <b>Working Electrode (WE)</b> clip.</li>"
            "</ol>"
            "<i>Test protocol:</i> Linear Sweep Voltammetry (LSV) from <b>0.00 V to +1.00 V</b> at "
            "<b>0.10 V/s</b> (expected slope ≈ 100 µA/V, current at 1.0 V ≈ 100 µA, R = 10.0 kΩ ± 10%)."
        )
        self.lbl_instructions.setWordWrap(True)
        w_lay.addWidget(self.lbl_instructions)
        root.addWidget(grp_wiring)

        # 2. Port selection & Run/Abort controls
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Target Port:"))
        self.cmb_port = QComboBox()
        ctrl_row.addWidget(self.cmb_port, stretch=1)

        self.btn_start = QPushButton("Run Self-Test (LSV 0 → 1 V @ 0.1 V/s)")
        self.btn_start.clicked.connect(self.start_self_test)
        ctrl_row.addWidget(self.btn_start)

        self.btn_abort = QPushButton("Abort")
        self.btn_abort.setEnabled(False)
        self.btn_abort.clicked.connect(self.abort_self_test)
        ctrl_row.addWidget(self.btn_abort)
        root.addLayout(ctrl_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)

        # 3. Live I-V plot & linear fit
        self.plot_widget = pg.PlotWidget(title="Dummy-Cell LSV (0.0 V to 1.0 V @ 0.1 V/s)")
        self.plot_widget.setBackground("w")
        self.plot_widget.setLabel("bottom", "Potential E, V")
        self.plot_widget.setLabel("left", "Current I, uA")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.25)
        self.plot_widget.setMinimumHeight(210)
        root.addWidget(self.plot_widget, stretch=1)

        # 4. Results & Saturation warning panel
        grp_res = QGroupBox("Step 2 — Diagnostic Results")
        form = QFormLayout(grp_res)

        self.lbl_status = QLabel("IDLE — Click 'Run Self-Test' to start")
        self.lbl_status.setStyleSheet("font-weight: bold; font-size: 13px;")
        form.addRow("Test Status:", self.lbl_status)

        self.lbl_resistance = QLabel("— kΩ")
        form.addRow("Measured Resistance:", self.lbl_resistance)

        self.lbl_current_1v = QLabel("— µA")
        form.addRow("Current at 1.0 V:", self.lbl_current_1v)

        self.lbl_fit_stats = QLabel("—")
        form.addRow("Linear Fit (Slope / R²):", self.lbl_fit_stats)

        self.lbl_sat_warning = QLabel("")
        self.lbl_sat_warning.setWordWrap(True)
        self.lbl_sat_warning.setVisible(False)
        form.addRow("Hardware Warning:", self.lbl_sat_warning)

        root.addWidget(grp_res)

        btn_close_row = QHBoxLayout()
        btn_close_row.addStretch(1)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        btn_close_row.addWidget(self.btn_close)
        root.addLayout(btn_close_row)

    def _populate_ports(self) -> None:
        self.cmb_port.clear()
        ports = list_candidate_ports(include_simulator=True)
        seen: set[str] = set()
        ordered_devices: list[tuple[str, str]] = []
        for p in ports:
            if not p.is_simulator and p.device not in seen:
                ordered_devices.append((f"{p.device} — {p.description}", p.device))
                seen.add(p.device)
        if SIMULATOR_PORT_DUMMY not in seen:
            ordered_devices.append((SIMULATOR_PORT_DUMMY, SIMULATOR_PORT_DUMMY))
            seen.add(SIMULATOR_PORT_DUMMY)
        if SIMULATOR_PORT_FIRST not in seen:
            ordered_devices.append((SIMULATOR_PORT_FIRST, SIMULATOR_PORT_FIRST))
            seen.add(SIMULATOR_PORT_FIRST)

        for label, dev in ordered_devices:
            self.cmb_port.addItem(label, dev)

        default_port = SIMULATOR_PORT_DUMMY
        if self.main_window is not None:
            mw_port = self.main_window.settings.serial_port
            if mw_port and mw_port != SIMULATOR_PORT_FIRST:
                default_port = mw_port

        idx = self.cmb_port.findData(default_port)
        if idx >= 0:
            self.cmb_port.setCurrentIndex(idx)

    def selected_port(self) -> str:
        data = self.cmb_port.currentData()
        return str(data) if data else SIMULATOR_PORT_DUMMY

    def start_self_test(self) -> bool:
        """Start the background LSV 0->1 V @ 0.1 V/s run on the selected port."""
        if self.worker is not None and self.worker.isRunning():
            return False

        port = self.selected_port()
        params = make_dummy_lsv_params()
        v_park = (
            self.main_window.settings.park_potential_v
            if self.main_window is not None
            else 0.0
        )

        self._e_points.clear()
        self._i_points.clear()
        self.last_result = None
        self.plot_widget.clear()
        self.progress_bar.setValue(0)
        self.lbl_sat_warning.setVisible(False)
        self.lbl_status.setText("RUNNING LSV (0.0 V → 1.0 V @ 0.1 V/s)...")
        self.lbl_status.setStyleSheet("color: #1f77b4; font-weight: bold;")

        self.last_dataset = DatasetRun(
            name="Dummy_10k_SelfTest",
            technique=TECH_CV,
            params=params,
            port=port,
        )

        self.btn_start.setEnabled(False)
        self.btn_abort.setEnabled(True)

        self.worker = SerialRunWorker(
            port=port,
            params=params,
            v_park=v_park,
            fast_sim=self.fast_sim,
            parent=self,
        )
        self.worker.point.connect(self._on_point)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()
        return True

    def abort_self_test(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_abort()

    def _on_point(self, cycle: int, e_v: float, i_ua: float, saturated: bool) -> None:
        self._e_points.append(e_v)
        self._i_points.append(i_ua)
        if self.last_dataset is not None:
            self.last_dataset.get_or_create_cycle(cycle).append_point(e_v, i_ua, saturated)
        self.plot_widget.clear()
        self.plot_widget.plot(
            self._e_points,
            self._i_points,
            pen=pg.mkPen(color="#1f77b4", width=2),
        )

    def _on_progress(self, elapsed_s: float, total_s: float) -> None:
        if total_s > 0:
            pct = int(min(100.0, max(0.0, (elapsed_s / total_s) * 100.0)))
            self.progress_bar.setValue(pct)

    def _on_finished(self, aborted: bool) -> None:
        self.btn_start.setEnabled(True)
        self.btn_abort.setEnabled(False)
        self.progress_bar.setValue(100 if not aborted else 0)

        if aborted:
            self.lbl_status.setText("ABORTED")
            self.lbl_status.setStyleSheet("color: #d62728; font-weight: bold;")
            return

        if len(self._e_points) >= 2:
            self.evaluate_points(self._e_points, self._i_points)
            if self.main_window is not None and self.last_dataset is not None:
                self.main_window.datasets.append(self.last_dataset)
                self.main_window.active_dataset = self.last_dataset
                self.main_window._rebuild_selection_tree()
                self.main_window.redraw_plot()

    def _on_error(self, message: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_abort.setEnabled(False)
        self.lbl_status.setText(f"ERROR: {message}")
        self.lbl_status.setStyleSheet("color: #d62728; font-weight: bold;")

    def evaluate_points(self, e_vals: Any, i_vals: Any) -> DummyCellResult:
        """Fit (E, I) with dummy_cell_fit() and update all dialog readouts and warnings."""
        res = dummy_cell_fit(e_vals, i_vals)
        self.last_result = res

        e_arr = np.asarray(e_vals, dtype=float)
        i_arr = np.asarray(i_vals, dtype=float)
        self.plot_widget.clear()
        self.plot_widget.plot(
            e_arr,
            i_arr,
            pen=None,
            symbol="o",
            symbolSize=5,
            symbolBrush="#1f77b4",
            name="Measured",
        )
        self.plot_widget.plot(
            e_arr,
            res.slope_ua_per_v * e_arr + res.intercept_ua,
            pen=pg.mkPen(color="#d62728", width=2),
            name="Linear Fit",
        )

        self.lbl_resistance.setText(f"{res.resistance_kohm:.3f} kΩ  (target: 10.0 ± 1.0 kΩ)")
        self.lbl_current_1v.setText(f"{res.current_at_1v_ua:.2f} µA  (target: ~100.0 µA)")
        self.lbl_fit_stats.setText(
            f"Slope = {res.slope_ua_per_v:.2f} µA/V | Offset = {res.intercept_ua:.2f} µA | R² = {res.r_squared:.5f}"
        )

        if res.passed:
            self.lbl_status.setText("PASS — Dummy cell within 10% of 10.0 kΩ")
            self.lbl_status.setStyleSheet("color: #2ca02c; font-weight: bold; font-size: 14px;")
        else:
            self.lbl_status.setText("FAIL — Resistance outside 10.0 kΩ ± 10% or poor linearity")
            self.lbl_status.setStyleSheet("color: #d62728; font-weight: bold; font-size: 14px;")

        if res.saturation_warning:
            self.lbl_sat_warning.setText(
                f"WARNING: Peak current |I| = {res.max_abs_current_ua:.1f} µA exceeds "
                f"the {CURRENT_SATURATION_WARN_UA:.0f} µA TIA saturation threshold!"
            )
            self.lbl_sat_warning.setStyleSheet(
                "color: #b30000; background-color: #ffe6e6; padding: 4px; font-weight: bold;"
            )
            self.lbl_sat_warning.setVisible(True)
        else:
            self.lbl_sat_warning.setVisible(False)

        return res

    def closeEvent(self, event: Any) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_abort()
            self.worker.wait(2000)
        super().closeEvent(event)