# hunstat/gui_analysis.py
"""Analysis menu and dock widget providing interactive baseline/peak picking,
CV metrics table, Randles-Sevcik plot/fit, calibration curve, standard addition,
and diode turn-on / CA query tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pyqtgraph as pg  # type: ignore[import-untyped]
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from hunstat.analysis import (
    CVMetrics,
    PeakResult,
    baseline_linear,
    ca_current_at,
    calibration_fit,
    cv_metrics,
    diode_turn_on,
    find_peak,
    randles_sevcik_fit,
    standard_addition,
)
from hunstat.models import CycleData, DatasetRun

if TYPE_CHECKING:
    from hunstat.gui import HunStatMainWindow


class AnalysisDockWidget(QDockWidget):
    """Dockable electrochemistry analysis suite attached to HunStatMainWindow."""

    def __init__(self, main_window: HunStatMainWindow) -> None:
        super().__init__("Analysis Tools", main_window)
        self.main_window = main_window
        self.setObjectName("AnalysisDockWidget")
        self.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        # Interactive baseline click state
        self._picking_baseline: bool = False
        self._clicked_points: list[tuple[float, float]] = []
        self._overlay_baseline_x: np.ndarray | None = None
        self._overlay_baseline_y: np.ndarray | None = None
        self._last_peak: PeakResult | None = None
        self._diode_marker_pt: tuple[float, float] | None = None

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        self.tabs = QTabWidget(container)
        self.tabs.addTab(self._build_baseline_peak_tab(), "Baseline && Peak")
        self.tabs.addTab(self._build_cv_metrics_tab(), "CV Metrics")
        self.tabs.addTab(self._build_randles_tab(), "Randles-Ševčík")
        self.tabs.addTab(self._build_calibration_tab(), "Calibration")
        self.tabs.addTab(self._build_std_addition_tab(), "Std Addition")
        self.tabs.addTab(self._build_diode_ca_tab(), "Diode / CA")

        layout.addWidget(self.tabs)
        self.setWidget(container)

        # Connect mouse click on main plot scene for interactive 2-point baseline picking
        scene = self.main_window.plot_widget.scene()
        if scene is not None:
            scene.sigMouseClicked.connect(self._on_main_plot_clicked)

    # ------------------------------------------------------------------
    # Helper: Selected dataset & cycle
    # ------------------------------------------------------------------
    def get_active_dataset_and_cycle(self) -> tuple[DatasetRun | None, CycleData | None]:
        """Return currently selected DatasetRun and CycleData from the main tree."""
        items = self.main_window.tree_selection.selectedItems()
        if items:
            meta = items[0].data(0, Qt.ItemDataRole.UserRole)
            if meta and meta[0] == "cycle":
                _, ds_idx, cyc_idx = meta
                if 0 <= ds_idx < len(self.main_window.datasets):
                    ds = self.main_window.datasets[ds_idx]
                    for cyc in ds.cycles:
                        if cyc.cycle_index == cyc_idx:
                            return ds, cyc
            elif meta and meta[0] == "dataset":
                ds_idx = meta[1]
                if 0 <= ds_idx < len(self.main_window.datasets):
                    ds = self.main_window.datasets[ds_idx]
                    cyc_num = self.spin_peak_cycle.value()
                    for cyc in ds.cycles:
                        if cyc.cycle_index == cyc_num:
                            return ds, cyc
                    return ds, (ds.cycles[-1] if ds.cycles else None)

        ds = self.main_window._get_selected_dataset()
        if ds is None or not ds.cycles:
            return ds, None
        cyc_num = self.spin_peak_cycle.value()
        for cyc in ds.cycles:
            if cyc.cycle_index == cyc_num:
                return ds, cyc
        return ds, ds.cycles[-1]

    def on_dataset_selection_changed(self) -> None:
        """Refresh cycle spinner bounds and Randles-Sevcik table when selection changes."""
        ds = self.main_window._get_selected_dataset()
        if ds is not None and ds.cycles:
            max_cyc = max(c.cycle_index for c in ds.cycles)
            self.spin_peak_cycle.setMaximum(max(1, max_cyc))
        self.refresh_randles_dataset_table()

    # ------------------------------------------------------------------
    # Tab 1: Baseline + Peak Picking
    # ------------------------------------------------------------------
    def _build_baseline_peak_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        form = QFormLayout()
        self.spin_peak_cycle = QSpinBox()
        self.spin_peak_cycle.setRange(1, 99)
        self.spin_peak_cycle.setValue(1)
        form.addRow("Cycle #:", self.spin_peak_cycle)

        self.cmb_polarity = QComboBox()
        self.cmb_polarity.addItems(["anodic", "cathodic"])
        form.addRow("Polarity:", self.cmb_polarity)

        self.spin_base_e1 = QDoubleSpinBox()
        self.spin_base_e1.setRange(-1.60, 1.60)
        self.spin_base_e1.setDecimals(4)
        self.spin_base_e1.setSingleStep(0.02)
        self.spin_base_e1.setValue(-0.15)
        form.addRow("Baseline E1 (V):", self.spin_base_e1)

        self.spin_base_e2 = QDoubleSpinBox()
        self.spin_base_e2.setRange(-1.60, 1.60)
        self.spin_base_e2.setDecimals(4)
        self.spin_base_e2.setSingleStep(0.02)
        self.spin_base_e2.setValue(0.05)
        form.addRow("Baseline E2 (V):", self.spin_base_e2)
        lay.addLayout(form)

        btn_row = QHBoxLayout()
        self.btn_pick_two = QPushButton("Click 2 Baseline Pts on Plot")
        self.btn_pick_two.setCheckable(True)
        self.btn_pick_two.clicked.connect(self._on_toggle_pick_baseline)
        btn_row.addWidget(self.btn_pick_two)

        self.btn_compute_peak = QPushButton("Find Peak")
        self.btn_compute_peak.clicked.connect(self.compute_baseline_and_peak)
        btn_row.addWidget(self.btn_compute_peak)

        self.btn_clear_overlay = QPushButton("Clear")
        self.btn_clear_overlay.clicked.connect(self.clear_overlays)
        btn_row.addWidget(self.btn_clear_overlay)
        lay.addLayout(btn_row)

        self.lbl_peak_result = QLabel("Select a dataset/cycle and click 'Find Peak' or pick 2 points.")
        self.lbl_peak_result.setWordWrap(True)
        lay.addWidget(self.lbl_peak_result)
        lay.addStretch(1)
        return w

    def _on_toggle_pick_baseline(self, checked: bool) -> None:
        self._picking_baseline = checked
        self._clicked_points.clear()
        if checked:
            self.lbl_peak_result.setText("Click point 1 on the main plot...")
        else:
            self.lbl_peak_result.setText("Interactive baseline picking cancelled.")

    def _on_main_plot_clicked(self, event: Any) -> None:
        if not self._picking_baseline:
            return
        vb = self.main_window.plot_widget.getPlotItem().vb
        scene_pos = event.scenePos()
        if not vb.sceneBoundingRect().contains(scene_pos):
            return
        pt = vb.mapSceneToView(scene_pos)
        self._clicked_points.append((float(pt.x()), float(pt.y())))
        if len(self._clicked_points) == 1:
            self.spin_base_e1.setValue(self._clicked_points[0][0])
            self.lbl_peak_result.setText(
                f"Point 1 set at E1 = {pt.x():.4f} V. Now click point 2 on the plot..."
            )
        elif len(self._clicked_points) >= 2:
            self.spin_base_e2.setValue(self._clicked_points[1][0])
            self._picking_baseline = False
            self.btn_pick_two.setChecked(False)
            self.compute_baseline_and_peak(use_clicked_anchors=True)

    def compute_baseline_and_peak(self, use_clicked_anchors: bool = False) -> PeakResult | None:
        """Compute linear baseline and peak on the active cycle and draw plot markers."""
        ds, cyc = self.get_active_dataset_and_cycle()
        if ds is None or cyc is None or len(cyc.x) < 2:
            self.lbl_peak_result.setText("No valid cycle data selected.")
            return None

        e_arr = np.asarray(cyc.x, dtype=float)
        i_arr = np.asarray(cyc.y_display if cyc.y_display else cyc.y_raw, dtype=float)

        # Restrict to forward sweep if anodic, reverse sweep if cathodic (when multi-sweep CV)
        polarity = self.cmb_polarity.currentText()
        turn_idx = int(np.argmax(e_arr))
        if 1 < turn_idx < (e_arr.size - 2):
            if polarity == "anodic":
                e_seg, i_seg = e_arr[: turn_idx + 1], i_arr[: turn_idx + 1]
            else:
                e_seg, i_seg = e_arr[turn_idx:], i_arr[turn_idx:]
        else:
            e_seg, i_seg = e_arr, i_arr

        if use_clicked_anchors and len(self._clicked_points) >= 2:
            p1: Any = self._clicked_points[0]
            p2: Any = self._clicked_points[1]
        else:
            p1 = float(self.spin_base_e1.value())
            p2 = float(self.spin_base_e2.value())

        base = baseline_linear(e_seg, i_seg, p1, p2)
        peak = find_peak(e_seg, i_seg, baseline=base, polarity=polarity)

        self._overlay_baseline_x = e_seg
        self._overlay_baseline_y = base
        self._last_peak = peak

        # Update main window "I peak" readout with height above baseline
        self.main_window.edt_ipeak.setText(f"{peak.Ip:.3f}")
        self.lbl_peak_result.setText(
            f"Ep = {peak.Ep:.4f} V | Ip (above baseline) = {peak.Ip:.3f} uA\n"
            f"I_raw = {peak.i_raw:.3f} uA | I_base = {peak.i_baseline:.3f} uA"
        )
        self.main_window.redraw_plot()
        return peak

    def clear_overlays(self) -> None:
        self._overlay_baseline_x = None
        self._overlay_baseline_y = None
        self._last_peak = None
        self._diode_marker_pt = None
        self.main_window.redraw_plot()

    def draw_plot_overlays(self) -> None:
        """Called at the end of HunStatMainWindow.redraw_plot() to render active markers."""
        pw = self.main_window.plot_widget
        if self._overlay_baseline_x is not None and self._overlay_baseline_y is not None:
            base_pen = pg.mkPen(color="#7f7f7f", width=1.5, style=Qt.PenStyle.DashLine)
            pw.plot(self._overlay_baseline_x, self._overlay_baseline_y, pen=base_pen)

        if self._last_peak is not None:
            pk = self._last_peak
            drop_pen = pg.mkPen(color="#d62728", width=2, style=Qt.PenStyle.DotLine)
            pw.plot([pk.Ep, pk.Ep], [pk.i_baseline, pk.i_raw], pen=drop_pen)
            pw.plot(
                [pk.Ep],
                [pk.i_raw],
                pen=None,
                symbol="o",
                symbolSize=9,
                symbolBrush="#d62728",
            )

        if self._diode_marker_pt is not None:
            vx, iy = self._diode_marker_pt
            pw.plot(
                [vx],
                [iy],
                pen=None,
                symbol="t",
                symbolSize=10,
                symbolBrush="#2ca02c",
            )

    # ------------------------------------------------------------------
    # Tab 2: CV Metrics Table
    # ------------------------------------------------------------------
    def _build_cv_metrics_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        self.btn_calc_cv = QPushButton("Compute CV Metrics for Selected Dataset")
        self.btn_calc_cv.clicked.connect(self.compute_cv_metrics_table)
        lay.addWidget(self.btn_calc_cv)

        self.tbl_cv = QTableWidget(0, 8)
        self.tbl_cv.setHorizontalHeaderLabels(
            ["Cycle", "Epa (V)", "Epc (V)", "ΔEp (mV)", "Ipa (uA)", "Ipc (uA)", "|Ipa/Ipc|", "E1/2 (V)"]
        )
        self.tbl_cv.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tbl_cv)
        return w

    def compute_cv_metrics_table(self) -> list[CVMetrics]:
        ds = self.main_window._get_selected_dataset()
        self.tbl_cv.setRowCount(0)
        results: list[CVMetrics] = []
        if ds is None or not ds.cycles:
            return results

        for cyc in ds.cycles:
            if len(cyc.x) < 4:
                continue
            m = cv_metrics(cyc)
            results.append(m)
            row = self.tbl_cv.rowCount()
            self.tbl_cv.insertRow(row)
            vals = [
                str(cyc.cycle_index),
                f"{m.Epa:.4f}",
                f"{m.Epc:.4f}",
                f"{m.delta_Ep_mv:.1f}",
                f"{m.Ipa:.3f}",
                f"{m.Ipc:.3f}",
                f"{m.Ipa_Ipc:.3f}" if not np.isnan(m.Ipa_Ipc) else "N/A",
                f"{m.E_half:.4f}",
            ]
            for col, txt in enumerate(vals):
                self.tbl_cv.setItem(row, col, QTableWidgetItem(txt))
        return results

    # ------------------------------------------------------------------
    # Tab 3: Randles-Sevcik Tool
    # ------------------------------------------------------------------
    def _build_randles_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        top_btns = QHBoxLayout()
        self.btn_rs_refresh = QPushButton("Refresh Loaded Files")
        self.btn_rs_refresh.clicked.connect(self.refresh_randles_dataset_table)
        top_btns.addWidget(self.btn_rs_refresh)

        self.btn_rs_fit = QPushButton("Fit Ip vs sqrt(v)")
        self.btn_rs_fit.clicked.connect(self.run_randles_sevcik_from_table)
        top_btns.addWidget(self.btn_rs_fit)
        lay.addLayout(top_btns)

        self.tbl_rs = QTableWidget(0, 3)
        self.tbl_rs.setHorizontalHeaderLabels(["Use / Dataset", "Scan Rate v (V/s)", "Ip (uA)"])
        self.tbl_rs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tbl_rs)

        self.lbl_rs_fit = QLabel("Select >= 2 datasets/rows and click 'Fit Ip vs sqrt(v)'.")
        lay.addWidget(self.lbl_rs_fit)

        self.plot_rs = pg.PlotWidget(title="Randles-Ševčík: Ip vs v^(1/2)")
        self.plot_rs.setBackground("w")
        self.plot_rs.setLabel("bottom", "v^(1/2), (V/s)^(1/2)")
        self.plot_rs.setLabel("left", "Ip, uA")
        self.plot_rs.setMinimumHeight(160)
        lay.addWidget(self.plot_rs)
        return w

    def refresh_randles_dataset_table(self) -> None:
        self.tbl_rs.setRowCount(0)
        for ds in self.main_window.datasets:
            if not ds.cycles or not ds.cycles[-1].x:
                continue
            row = self.tbl_rs.rowCount()
            self.tbl_rs.insertRow(row)

            item_name = QTableWidgetItem(ds.name)
            item_name.setFlags(item_name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item_name.setCheckState(Qt.CheckState.Checked)
            self.tbl_rs.setItem(row, 0, item_name)

            sr = float(ds.params.cv.scan_rate)
            self.tbl_rs.setItem(row, 1, QTableWidgetItem(f"{sr:.4f}"))

            try:
                ip_val = cv_metrics(ds.cycles[-1]).Ipa
            except Exception:
                ip_val = ds.max_abs_current_ua
            self.tbl_rs.setItem(row, 2, QTableWidgetItem(f"{ip_val:.3f}"))

    def run_randles_sevcik_from_table(self) -> Any:
        scan_rates: list[float] = []
        peak_currents: list[float] = []
        for r in range(self.tbl_rs.rowCount()):
            it0 = self.tbl_rs.item(r, 0)
            it1 = self.tbl_rs.item(r, 1)
            it2 = self.tbl_rs.item(r, 2)
            if it0 is not None and it0.checkState() != Qt.CheckState.Checked:
                continue
            if it1 is None or it2 is None:
                continue
            try:
                v = float(it1.text())
                ip = float(it2.text())
                if v > 0:
                    scan_rates.append(v)
                    peak_currents.append(ip)
            except ValueError:
                continue

        if len(scan_rates) < 2:
            self.lbl_rs_fit.setText("Need at least 2 checked rows with positive scan rates.")
            return None

        res = randles_sevcik_fit(scan_rates, peak_currents)
        self.lbl_rs_fit.setText(
            f"Ip = {res.slope:.3f} * sqrt(v) + {res.intercept:.3f} uA  (R² = {res.r_squared:.5f})"
        )
        self.plot_rs.clear()
        self.plot_rs.plot(
            res.sqrt_scan_rates,
            res.peak_currents,
            pen=None,
            symbol="o",
            symbolBrush="#1f77b4",
        )
        self.plot_rs.plot(
            res.sqrt_scan_rates,
            res.fitted_currents,
            pen=pg.mkPen(color="#d62728", width=2),
        )
        return res

    # ------------------------------------------------------------------
    # Tab 4: Calibration Curve Tool
    # ------------------------------------------------------------------
    def _build_calibration_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        row_btns = QHBoxLayout()
        btn_add = QPushButton("+ Row")
        btn_add.clicked.connect(lambda: self._append_table_row(self.tbl_cal, "0.0", "0.0"))
        row_btns.addWidget(btn_add)

        btn_paper_aa = QPushButton("Load Paper Ascorbic Acid")
        btn_paper_aa.clicked.connect(self._load_paper_ascorbic_calibration)
        row_btns.addWidget(btn_paper_aa)

        btn_fit = QPushButton("Fit Calibration")
        btn_fit.clicked.connect(self.run_calibration_from_ui)
        row_btns.addWidget(btn_fit)
        lay.addLayout(row_btns)

        self.tbl_cal = QTableWidget(0, 2)
        self.tbl_cal.setHorizontalHeaderLabels(["Concentration (mM/ppm)", "Current I (uA)"])
        self.tbl_cal.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tbl_cal)

        form = QFormLayout()
        self.spin_cal_sigma = QDoubleSpinBox()
        self.spin_cal_sigma.setRange(0.0, 1000.0)
        self.spin_cal_sigma.setDecimals(4)
        self.spin_cal_sigma.setValue(0.0)
        form.addRow("Blank σ (uA, 0=none):", self.spin_cal_sigma)

        self.spin_cal_unknown_i = QDoubleSpinBox()
        self.spin_cal_unknown_i.setRange(-1000.0, 1000.0)
        self.spin_cal_unknown_i.setDecimals(3)
        self.spin_cal_unknown_i.setValue(18.611)
        form.addRow("Unknown Current I (uA):", self.spin_cal_unknown_i)
        lay.addLayout(form)

        self.lbl_cal_result = QLabel("Enter calibration standards and click 'Fit Calibration'.")
        self.lbl_cal_result.setWordWrap(True)
        lay.addWidget(self.lbl_cal_result)

        self.plot_cal = pg.PlotWidget(title="Calibration Curve")
        self.plot_cal.setBackground("w")
        self.plot_cal.setLabel("bottom", "Concentration")
        self.plot_cal.setLabel("left", "Current, uA")
        self.plot_cal.setMinimumHeight(150)
        lay.addWidget(self.plot_cal)

        self._load_paper_ascorbic_calibration()
        return w

    def _append_table_row(self, tbl: QTableWidget, col0: str, col1: str) -> None:
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, 0, QTableWidgetItem(col0))
        tbl.setItem(r, 1, QTableWidgetItem(col1))

    def _load_paper_ascorbic_calibration(self) -> None:
        self.tbl_cal.setRowCount(0)
        for c in (0.5, 1.0, 2.0, 3.0, 4.0, 5.0):
            i_val = 5.865 * c + 4.06
            self._append_table_row(self.tbl_cal, f"{c:.2f}", f"{i_val:.4f}")
        self.spin_cal_unknown_i.setValue(18.611)

    def run_calibration_from_ui(self) -> Any:
        concs, currs = self._read_two_col_table(self.tbl_cal)
        if len(concs) < 2:
            self.lbl_cal_result.setText("Enter at least 2 valid (concentration, current) rows.")
            return None

        sigma = float(self.spin_cal_sigma.value())
        sigma_arg = sigma if sigma > 0.0 else None
        fit = calibration_fit(concs, currs, sigma_blank=sigma_arg)
        c_unk = float(fit.concentration_from_current(float(self.spin_cal_unknown_i.value())))
        lod_str = f"{fit.lod:.4f}" if fit.lod is not None else "N/A"
        self.lbl_cal_result.setText(
            f"I = {fit.slope:.4f}*c + {fit.intercept:.4f} (R² = {fit.r_squared:.5f})\n"
            f"Unknown c = {c_unk:.3f} | LOD = {lod_str}"
        )

        c_arr = np.asarray(concs, dtype=float)
        i_arr = np.asarray(currs, dtype=float)
        self.plot_cal.clear()
        self.plot_cal.plot(c_arr, i_arr, pen=None, symbol="o", symbolBrush="#1f77b4")
        self.plot_cal.plot(
            c_arr,
            fit.slope * c_arr + fit.intercept,
            pen=pg.mkPen(color="#d62728", width=2),
        )
        return fit, c_unk

    # ------------------------------------------------------------------
    # Tab 5: Standard Addition Tool
    # ------------------------------------------------------------------
    def _build_std_addition_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        row_btns = QHBoxLayout()
        btn_add = QPushButton("+ Row")
        btn_add.clicked.connect(lambda: self._append_table_row(self.tbl_std, "0.0", "0.0"))
        row_btns.addWidget(btn_add)

        btn_pb = QPushButton("Paper Pb Preset")
        btn_pb.clicked.connect(self._load_paper_pb_std_addition)
        row_btns.addWidget(btn_pb)

        btn_cd = QPushButton("Paper Cd Preset")
        btn_cd.clicked.connect(self._load_paper_cd_std_addition)
        row_btns.addWidget(btn_cd)

        btn_fit = QPushButton("Fit Std Addition")
        btn_fit.clicked.connect(self.run_standard_addition_from_ui)
        row_btns.addWidget(btn_fit)
        lay.addLayout(row_btns)

        self.tbl_std = QTableWidget(0, 2)
        self.tbl_std.setHorizontalHeaderLabels(["Added Conc", "Peak Current Ip (uA)"])
        self.tbl_std.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tbl_std)

        form = QFormLayout()
        self.spin_std_dilution = QDoubleSpinBox()
        self.spin_std_dilution.setRange(0.001, 10000.0)
        self.spin_std_dilution.setDecimals(2)
        self.spin_std_dilution.setValue(20.0)
        form.addRow("Dilution Factor:", self.spin_std_dilution)
        lay.addLayout(form)

        self.lbl_std_result = QLabel("Enter standard additions and click 'Fit Std Addition'.")
        self.lbl_std_result.setWordWrap(True)
        lay.addWidget(self.lbl_std_result)

        self.plot_std = pg.PlotWidget(title="Standard Addition")
        self.plot_std.setBackground("w")
        self.plot_std.setLabel("bottom", "Added Concentration")
        self.plot_std.setLabel("left", "Peak Current, uA")
        self.plot_std.setMinimumHeight(150)
        lay.addWidget(self.plot_std)

        self._load_paper_pb_std_addition()
        return w

    def _load_paper_pb_std_addition(self) -> None:
        self.tbl_std.setRowCount(0)
        for c in (0.0, 0.5, 1.0, 1.5, 2.0):
            self._append_table_row(self.tbl_std, f"{c:.2f}", f"{20.11 * c + 9.77:.4f}")
        self.spin_std_dilution.setValue(20.0)

    def _load_paper_cd_std_addition(self) -> None:
        self.tbl_std.setRowCount(0)
        for c in (0.0, 0.5, 1.0, 1.5, 2.0):
            self._append_table_row(self.tbl_std, f"{c:.2f}", f"{22.17 * c + 10.93:.4f}")
        self.spin_std_dilution.setValue(20.0)

    def run_standard_addition_from_ui(self) -> Any:
        concs, currs = self._read_two_col_table(self.tbl_std)
        if len(concs) < 2:
            self.lbl_std_result.setText("Enter at least 2 valid standard addition rows.")
            return None

        dil = float(self.spin_std_dilution.value())
        res = standard_addition(concs, currs, dilution_factor=dil)
        self.lbl_std_result.setText(
            f"I = {res.slope:.3f}*c + {res.intercept:.3f} (R² = {res.r_squared:.5f})\n"
            f"x-intercept = {res.x_intercept:.4f} | Original conc = {res.original_concentration:.3f}"
        )

        c_arr = np.asarray(concs, dtype=float)
        i_arr = np.asarray(currs, dtype=float)
        x_line = np.linspace(min(res.x_intercept, float(np.min(c_arr))), float(np.max(c_arr)), 50)
        self.plot_std.clear()
        self.plot_std.plot(c_arr, i_arr, pen=None, symbol="o", symbolBrush="#1f77b4")
        self.plot_std.plot(
            x_line,
            res.slope * x_line + res.intercept,
            pen=pg.mkPen(color="#2ca02c", width=2),
        )
        return res

    @staticmethod
    def _read_two_col_table(tbl: QTableWidget) -> tuple[list[float], list[float]]:
        xs: list[float] = []
        ys: list[float] = []
        for r in range(tbl.rowCount()):
            it0 = tbl.item(r, 0)
            it1 = tbl.item(r, 1)
            if it0 is None or it1 is None:
                continue
            try:
                xs.append(float(it0.text()))
                ys.append(float(it1.text()))
            except ValueError:
                continue
        return xs, ys

    # ------------------------------------------------------------------
    # Tab 6: Diode Turn-On & Chronoamperometry Query Tool
    # ------------------------------------------------------------------
    def _build_diode_ca_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        grp_diode = QGroupBox("Diode Turn-On Voltage")
        d_form = QFormLayout(grp_diode)
        self.spin_diode_thresh = QDoubleSpinBox()
        self.spin_diode_thresh.setRange(0.01, 165.0)
        self.spin_diode_thresh.setDecimals(2)
        self.spin_diode_thresh.setValue(10.0)
        d_form.addRow("Threshold |I| (uA):", self.spin_diode_thresh)

        self.btn_diode_calc = QPushButton("Find Turn-On E")
        self.btn_diode_calc.clicked.connect(self.compute_diode_turn_on_ui)
        d_form.addRow(self.btn_diode_calc)

        self.lbl_diode_res = QLabel("Turn-on E: —")
        d_form.addRow(self.lbl_diode_res)
        lay.addWidget(grp_diode)

        grp_ca = QGroupBox("CA Current at Time After Step (t - teq)")
        c_form = QFormLayout(grp_ca)
        self.spin_ca_tquery = QDoubleSpinBox()
        self.spin_ca_tquery.setRange(0.0, 1000.0)
        self.spin_ca_tquery.setDecimals(2)
        self.spin_ca_tquery.setValue(5.0)
        c_form.addRow("t_query after step (s):", self.spin_ca_tquery)

        self.btn_ca_query = QPushButton("Evaluate I(t_step + t_query)")
        self.btn_ca_query.clicked.connect(self.compute_ca_query_ui)
        c_form.addRow(self.btn_ca_query)

        self.lbl_ca_res = QLabel("CA I: —")
        c_form.addRow(self.lbl_ca_res)
        lay.addWidget(grp_ca)

        lay.addStretch(1)
        return w

    def compute_diode_turn_on_ui(self) -> float | None:
        ds, cyc = self.get_active_dataset_and_cycle()
        if ds is None or cyc is None or not cyc.x:
            self.lbl_diode_res.setText("Turn-on E: No active cycle")
            return None
        thresh = float(self.spin_diode_thresh.value())
        y_vals = cyc.y_display if cyc.y_display else cyc.y_raw
        v_on = diode_turn_on(cyc.x, y_vals, threshold_uA=thresh)
        if v_on is None:
            self._diode_marker_pt = None
            self.lbl_diode_res.setText(f"Turn-on E: Not reached (|I| <= {thresh:.2f} uA)")
        else:
            idx = int(np.argmin(np.abs(np.asarray(cyc.x, dtype=float) - v_on)))
            self._diode_marker_pt = (v_on, float(y_vals[idx]))
            self.lbl_diode_res.setText(f"Turn-on E = {v_on:.4f} V (at |I| > {thresh:.2f} uA)")
        self.main_window.redraw_plot()
        return v_on

    def compute_ca_query_ui(self) -> float | None:
        ds, cyc = self.get_active_dataset_and_cycle()
        if ds is None or cyc is None or not cyc.x:
            self.lbl_ca_res.setText("CA I: No active cycle")
            return None
        t_step = float(ds.params.general.t_eq)
        t_q = float(self.spin_ca_tquery.value())
        y_vals = cyc.y_display if cyc.y_display else cyc.y_raw
        val_ua = ca_current_at(cyc.x, y_vals, t_query=t_q, t_step=t_step)
        self.lbl_ca_res.setText(f"I(t_step={t_step:.1f}s + {t_q:.2f}s) = {val_ua:.3f} uA")
        return val_ua


def install_analysis_ui(main_window: HunStatMainWindow) -> AnalysisDockWidget:
    """Create the Analysis dock widget and '&Analysis' menu on `main_window`."""
    dock = AnalysisDockWidget(main_window)
    main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    analysis_menu = main_window.menuBar().addMenu("&Analysis")
    toggle_act = dock.toggleViewAction()
    toggle_act.setText("Show/Hide &Analysis Dock")
    analysis_menu.addAction(toggle_act)
    analysis_menu.addSeparator()

    tab_names = [
        ("Baseline && Peak Picking", 0),
        ("CV Metrics Table", 1),
        ("Randles-Ševčík Fit", 2),
        ("Calibration Curve", 3),
        ("Standard Addition", 4),
        ("Diode Turn-On && CA Query", 5),
    ]
    for label, tab_idx in tab_names:
        act = QAction(label, main_window)
        act.triggered.connect(
            lambda checked=False, idx=tab_idx: (dock.show(), dock.tabs.setCurrentIndex(idx))
        )
        analysis_menu.addAction(act)

    analysis_menu.addSeparator()

    def _open_dummy_wizard() -> Any:
        from .dummy_wizard import DummyCellSelfTestDialog

        dlg = DummyCellSelfTestDialog(main_window=main_window, fast_sim=main_window.fast_sim)
        dlg.show()
        return dlg

    setattr(main_window, "open_dummy_cell_wizard", _open_dummy_wizard)
    act_dummy = QAction("10 kΩ &Dummy-Cell Self-Test Wizard...", main_window)
    act_dummy.triggered.connect(_open_dummy_wizard)
    analysis_menu.addAction(act_dummy)

    return dock