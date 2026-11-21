# hunstat/gui.py
"""PySide6 main window, dialogs, plot canvas, file I/O, and live controller for HunStat."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

import numpy as np

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStyle,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg
import pyqtgraph.exporters as pg_exporters

from hunstat.io_files import (
    convert_legacy_folder,
    convert_legacy_tsv_to_csv,
    load_datafile,
    save_dataset_csv_json,
)
from hunstat.models import PLOT_COLORS, CycleData, DatasetRun
from hunstat.protocol import (
    ValidationResult,
    compute_real_values,
    expected_run_duration_s,
    validate_params,
)
from hunstat.settings import (
    APP_NAME,
    APP_VERSION,
    CA_MAX_DATAPOINTS,
    CURRENT_SATURATION_WARN_UA,
    DPV_MIN_STEP_MV,
    HARD_V_MAX,
    HARD_V_MIN,
    SCAN_RATE_MAX_VS,
    SCAN_RATE_MIN_VS,
    TECH_CA,
    TECH_CV,
    TECH_DPV,
    AppSettings,
    CAParams,
    CVParams,
    DPVParams,
    ExperimentParams,
    GeneralParams,
    make_paper_defaults,
)
from hunstat.worker import (
    SerialRunWorker,
    auto_detect_port,
    list_candidate_ports,
    probe_port,
)


DARK_STYLESHEET = """
QMainWindow, QDialog, QWidget {
    background-color: #1e1e24;
    color: #e8e8ec;
    font-size: 10pt;
}
QGroupBox {
    border: 1px solid #3a3a46;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QPlainTextEdit, QTreeWidget {
    background-color: #282832;
    color: #f0f0f5;
    border: 1px solid #454554;
    border-radius: 3px;
    padding: 2px 4px;
}
QPushButton {
    background-color: #323240;
    color: #f0f0f5;
    border: 1px solid #505064;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton:hover {
    background-color: #404052;
}
QPushButton:disabled {
    color: #777785;
    background-color: #24242c;
}
QTabWidget::pane {
    border: 1px solid #3a3a46;
}
QTabBar::tab {
    background-color: #262630;
    color: #ccccd4;
    padding: 5px 10px;
    border: 1px solid #3a3a46;
}
QTabBar::tab:selected {
    background-color: #383848;
    color: #ffffff;
    font-weight: bold;
}
"""


class SettingsDialog(QDialog):
    """Instrument & software configuration dialog (ports, hardware ranges, park E, theme)."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self.settings = settings

        layout = QVBoxLayout(self)
        form = QFormLayout()

        port_row = QHBoxLayout()
        self.combo_port = QComboBox()
        self.combo_port.setEditable(True)
        port_row.addWidget(self.combo_port, stretch=1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self._populate_ports)
        port_row.addWidget(self.btn_refresh)

        self.btn_probe = QPushButton("Auto-Detect / Probe '!'")
        self.btn_probe.clicked.connect(self._on_probe_clicked)
        port_row.addWidget(self.btn_probe)
        form.addRow("Serial port:", port_row)

        self.lbl_probe_status = QLabel("")
        form.addRow("", self.lbl_probe_status)

        v_row = QHBoxLayout()
        self.spin_v_min = QDoubleSpinBox()
        self.spin_v_min.setRange(HARD_V_MIN, 0.0)
        self.spin_v_min.setDecimals(2)
        self.spin_v_min.setSingleStep(0.05)
        self.spin_v_min.setValue(self.settings.v_min)
        v_row.addWidget(self.spin_v_min)
        v_row.addWidget(QLabel("to"))
        self.spin_v_max = QDoubleSpinBox()
        self.spin_v_max.setRange(0.0, HARD_V_MAX)
        self.spin_v_max.setDecimals(2)
        self.spin_v_max.setSingleStep(0.05)
        self.spin_v_max.setValue(self.settings.v_max)
        v_row.addWidget(self.spin_v_max)
        v_row.addWidget(QLabel("V (hard limit ±1.60 V)"))
        form.addRow("Voltages range from:", v_row)

        sr_row = QHBoxLayout()
        self.spin_sr_min = QDoubleSpinBox()
        self.spin_sr_min.setRange(SCAN_RATE_MIN_VS, 1.0)
        self.spin_sr_min.setDecimals(3)
        self.spin_sr_min.setSingleStep(0.01)
        self.spin_sr_min.setValue(self.settings.scan_rate_min)
        sr_row.addWidget(self.spin_sr_min)
        sr_row.addWidget(QLabel("to"))
        self.spin_sr_max = QDoubleSpinBox()
        self.spin_sr_max.setRange(0.01, SCAN_RATE_MAX_VS)
        self.spin_sr_max.setDecimals(3)
        self.spin_sr_max.setSingleStep(0.1)
        self.spin_sr_max.setValue(self.settings.scan_rate_max)
        sr_row.addWidget(self.spin_sr_max)
        sr_row.addWidget(QLabel("V/s"))
        form.addRow("Scan rate ranges from:", sr_row)

        self.spin_dpv_min = QDoubleSpinBox()
        self.spin_dpv_min.setRange(DPV_MIN_STEP_MV, 50.0)
        self.spin_dpv_min.setDecimals(2)
        self.spin_dpv_min.setSuffix(" mV")
        self.spin_dpv_min.setValue(self.settings.dpv_min_step_mv)
        form.addRow("DPV minimum step/amplitude:", self.spin_dpv_min)

        self.spin_ca_max = QSpinBox()
        self.spin_ca_max.setRange(10, CA_MAX_DATAPOINTS)
        self.spin_ca_max.setValue(self.settings.max_ca_datapoints)
        form.addRow("CA max datapoints (firmware cap 999):", self.spin_ca_max)

        self.spin_park_v = QDoubleSpinBox()
        self.spin_park_v.setRange(HARD_V_MIN, HARD_V_MAX)
        self.spin_park_v.setDecimals(3)
        self.spin_park_v.setSingleStep(0.05)
        self.spin_park_v.setSuffix(" V")
        self.spin_park_v.setValue(self.settings.park_potential_v)
        form.addRow("Safe park potential (Vpark):", self.spin_park_v)

        self.chk_dark = QCheckBox("Enable dark theme")
        self.chk_dark.setChecked(self.settings.dark_theme)
        form.addRow("Appearance:", self.chk_dark)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_ports()

    def _populate_ports(self) -> None:
        current = self.combo_port.currentText() or self.settings.serial_port
        self.combo_port.clear()
        for info in list_candidate_ports(include_simulator=True):
            tag = " [Seeed XIAO]" if info.is_seeed else ""
            self.combo_port.addItem(f"{info.device}{tag}", userData=info.device)
        idx = self.combo_port.findData(current)
        if idx >= 0:
            self.combo_port.setCurrentIndex(idx)
        else:
            self.combo_port.setEditText(current)

    def _selected_port_device(self) -> str:
        idx = self.combo_port.currentIndex()
        if idx >= 0 and self.combo_port.itemData(idx):
            data = str(self.combo_port.itemData(idx))
            if self.combo_port.currentText().startswith(data):
                return data
        return self.combo_port.currentText().split(" [")[0].strip()

    def _on_probe_clicked(self) -> None:
        port = self._selected_port_device()
        self.lbl_probe_status.setText(f"Probing {port} with '!'...")
        QApplication.processEvents()
        if probe_port(port):
            self.lbl_probe_status.setStyleSheet("color: #2ca02c; font-weight: bold;")
            self.lbl_probe_status.setText(f"Connected! '{port}' replied '#' to '!'.")
            return
        detected = auto_detect_port(include_simulator_fallback=False)
        if detected:
            idx = self.combo_port.findData(detected)
            if idx >= 0:
                self.combo_port.setCurrentIndex(idx)
            else:
                self.combo_port.setEditText(detected)
            self.lbl_probe_status.setStyleSheet("color: #2ca02c; font-weight: bold;")
            self.lbl_probe_status.setText(f"Auto-detected HunStat on '{detected}'!")
        else:
            self.lbl_probe_status.setStyleSheet("color: #d62728; font-weight: bold;")
            self.lbl_probe_status.setText(f"No '#' response on '{port}' (check cable or use Simulator).")

    def _on_accept(self) -> None:
        self.settings.serial_port = self._selected_port_device()
        self.settings.v_min = max(HARD_V_MIN, float(self.spin_v_min.value()))
        self.settings.v_max = min(HARD_V_MAX, float(self.spin_v_max.value()))
        self.settings.scan_rate_min = max(SCAN_RATE_MIN_VS, float(self.spin_sr_min.value()))
        self.settings.scan_rate_max = min(SCAN_RATE_MAX_VS, float(self.spin_sr_max.value()))
        self.settings.dpv_min_step_mv = max(DPV_MIN_STEP_MV, float(self.spin_dpv_min.value()))
        self.settings.max_ca_datapoints = min(CA_MAX_DATAPOINTS, int(self.spin_ca_max.value()))
        self.settings.park_potential_v = float(self.spin_park_v.value())
        self.settings.dark_theme = bool(self.chk_dark.isChecked())
        self.accept()


class HunStatMainWindow(QMainWindow):
    """Main desktop GUI replacing HunStat.exe."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        fast_sim: bool = False,
    ) -> None:
        super().__init__()
        self.settings = settings or AppSettings.load()
        self.fast_sim = fast_sim
        self.datasets: list[DatasetRun] = []
        self.active_dataset: DatasetRun | None = None
        self.worker: SerialRunWorker | None = None
        self._plot_dirty: bool = False
        self._updating_tree: bool = False

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} — DIY Potentiostat Controller")
        self.resize(1180, 740)

        self._build_menus_and_toolbar()
        self._build_central_ui()
        self._build_statusbar()

        # 30 fps plot redraw throttle timer (~33 ms)
        self._plot_timer = QTimer(self)
        self._plot_timer.setInterval(33)
        self._plot_timer.timeout.connect(self._on_plot_timer)
        self._plot_timer.start()

        try:
            restored = ExperimentParams.from_dict(self.settings.last_params)
            self.set_experiment_params(restored)
        except Exception:
            self.set_experiment_params(make_paper_defaults("CV"))

        from hunstat.gui_analysis import install_analysis_ui
        self.analysis_dock = install_analysis_ui(self)

        self.apply_theme(self.settings.dark_theme)
        self._run_live_validation()

    # ------------------------------------------------------------------
    # Menus & Toolbar
    # ------------------------------------------------------------------
    def _build_menus_and_toolbar(self) -> None:
        tb = QToolBar("Main Toolbar", self)
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        style = self.style()

        self.act_open = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open", self
        )
        self.act_open.setToolTip("Open .csv, legacy .tsv, or generic text datafile(s)")
        self.act_open.triggered.connect(self._on_open_clicked)
        tb.addAction(self.act_open)

        self.act_save = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Save", self
        )
        self.act_save.setToolTip("Save selected dataset as .csv + .json metadata")
        self.act_save.triggered.connect(self._on_save_clicked)
        tb.addAction(self.act_save)

        tb.addSeparator()

        self.act_run = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay), "Run", self
        )
        self.act_run.setToolTip("Start electrochemical measurement")
        self.act_run.triggered.connect(self.start_run)
        tb.addAction(self.act_run)

        self.act_abort = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_BrowserStop), "Abort", self
        )
        self.act_abort.setToolTip("Abort active measurement ('!') and park WE")
        self.act_abort.setEnabled(False)
        self.act_abort.triggered.connect(self.abort_run)
        tb.addAction(self.act_abort)

        tb.addSeparator()

        self.act_autoscale = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "Zoom Out / Autoscale",
            self,
        )
        self.act_autoscale.setToolTip("Reset plot axes to fit data or Current range")
        self.act_autoscale.triggered.connect(self.autoscale_plot)
        tb.addAction(self.act_autoscale)

        self.act_screenshot = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_DesktopIcon), "Screenshot", self
        )
        self.act_screenshot.setToolTip("Export plot as PNG or SVG")
        self.act_screenshot.triggered.connect(self._on_screenshot_clicked)
        tb.addAction(self.act_screenshot)

        self.act_redraw = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload), "Redraw", self
        )
        self.act_redraw.setToolTip("Force plot redraw of all checked files and cycles")
        self.act_redraw.triggered.connect(self.redraw_plot)
        tb.addAction(self.act_redraw)

        tb.addSeparator()

        self.act_convert_file = QAction("Convert Legacy TSV File...", self)
        self.act_convert_file.setToolTip("Convert a legacy .tsv file to .csv + .json")
        self.act_convert_file.triggered.connect(self._on_convert_tsv_file_clicked)

        self.act_convert_folder = QAction("Convert Legacy TSV Folder...", self)
        self.act_convert_folder.setToolTip("Batch-convert all legacy .tsv files in a folder to .csv + .json")
        self.act_convert_folder.triggered.connect(self._on_convert_tsv_folder_clicked)

        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self.act_convert_file)
        tools_menu.addAction(self.act_convert_folder)
        tools_menu.addSeparator()
        tools_menu.addAction(self.act_screenshot)

        self.act_settings = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "Settings",
            self,
        )
        self.act_settings.setToolTip("Configure serial port, safety limits, park voltage, theme")
        self.act_settings.triggered.connect(self.open_settings_dialog)
        tb.addAction(self.act_settings)

        self.act_about = QAction(
            style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation), "About", self
        )
        self.act_about.triggered.connect(self.show_about_dialog)
        tb.addAction(self.act_about)

    def _build_central_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.setCentralWidget(splitter)

        left_widget = QWidget()
        left_widget.setMinimumWidth(370)
        left_widget.setMaximumWidth(450)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(6, 6, 6, 6)

        # 1. Technique Radio Group
        tech_group = QGroupBox("Technique")
        tech_layout = QHBoxLayout(tech_group)
        self.rb_cv = QRadioButton("CV")
        self.rb_dpv = QRadioButton("DPV")
        self.rb_ca = QRadioButton("Chronoamperometry")
        self.rb_cv.setChecked(True)

        self.tech_btn_group = QButtonGroup(self)
        self.tech_btn_group.addButton(self.rb_cv, TECH_CV)
        self.tech_btn_group.addButton(self.rb_dpv, TECH_DPV)
        self.tech_btn_group.addButton(self.rb_ca, TECH_CA)
        self.tech_btn_group.idClicked.connect(self._on_technique_changed)

        tech_layout.addWidget(self.rb_cv)
        tech_layout.addWidget(self.rb_dpv)
        tech_layout.addWidget(self.rb_ca)
        left_layout.addWidget(tech_group)

        # 2. Presets bar
        preset_group = QGroupBox("Presets")
        preset_layout = QHBoxLayout(preset_group)
        self.combo_preset = QComboBox()
        self.combo_preset.addItems([
            "Paper CV (Fe(CN)6)",
            "Paper LSV (Diode)",
            "Paper DPV (Cd/Pb ASV)",
            "Paper CA (Ascorbic Acid)",
            "10 kOhm Dummy Cell",
        ])
        self.combo_preset.activated.connect(self._on_builtin_preset_selected)
        preset_layout.addWidget(self.combo_preset, stretch=1)

        self.btn_load_preset = QPushButton("Load JSON")
        self.btn_load_preset.clicked.connect(self.load_preset_json)
        preset_layout.addWidget(self.btn_load_preset)

        self.btn_save_preset = QPushButton("Save JSON")
        self.btn_save_preset.clicked.connect(self.save_preset_json)
        preset_layout.addWidget(self.btn_save_preset)
        left_layout.addWidget(preset_group)

        # 3. Parameter Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_general_tab(), "General")
        self.tabs.addTab(self._create_cv_tab(), "CV")
        self.tabs.addTab(self._create_dpv_tab(), "DPV")
        self.tabs.addTab(self._create_ca_tab(), "Chronoamperometry")
        self.tabs.addTab(self._create_signal_tab(), "Signal processing")
        self.tabs.addTab(self._create_log_tab(), "Log")
        left_layout.addWidget(self.tabs)

        # 4. Inline validation banner
        self.lbl_validation = QLabel("")
        self.lbl_validation.setWordWrap(True)
        self.lbl_validation.setMinimumHeight(36)
        left_layout.addWidget(self.lbl_validation)

        # 5. Selection Tree + TSV Converter shortcut buttons
        sel_header = QHBoxLayout()
        sel_header.addWidget(QLabel("Selection (Files & Cycles):"))
        sel_header.addStretch(1)
        self.btn_convert_tsv = QPushButton("TSV → CSV")
        self.btn_convert_tsv.setToolTip("Convert a legacy .tsv file or folder to .csv + .json")
        self.btn_convert_tsv.clicked.connect(self._on_convert_tsv_button)
        sel_header.addWidget(self.btn_convert_tsv)

        self.btn_clear_files = QPushButton("Clear All")
        self.btn_clear_files.clicked.connect(self.clear_all_datasets)
        sel_header.addWidget(self.btn_clear_files)
        left_layout.addLayout(sel_header)

        self.tree_selection = QTreeWidget()
        self.tree_selection.setHeaderHidden(True)
        self.tree_selection.itemChanged.connect(self._on_tree_item_changed)
        self.tree_selection.itemSelectionChanged.connect(self._on_tree_selection_changed)
        left_layout.addWidget(self.tree_selection, stretch=1)

        splitter.addWidget(left_widget)

        # === RIGHT PANEL ===
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(6, 6, 6, 6)

        readout_bar = QHBoxLayout()
        self.lbl_ei_title = QLabel("E/V, I/uA =")
        readout_bar.addWidget(self.lbl_ei_title)
        self.edt_ei = QLineEdit("0.0000, 0.0000")
        self.edt_ei.setReadOnly(True)
        self.edt_ei.setFixedWidth(180)
        readout_bar.addWidget(self.edt_ei)

        readout_bar.addSpacing(16)
        readout_bar.addWidget(QLabel("I peak (uA) ="))
        self.edt_ipeak = QLineEdit("")
        self.edt_ipeak.setReadOnly(True)
        self.edt_ipeak.setFixedWidth(120)
        readout_bar.addWidget(self.edt_ipeak)

        readout_bar.addSpacing(16)
        self.lbl_port_badge = QLabel("")
        readout_bar.addWidget(self.lbl_port_badge)
        readout_bar.addStretch(1)
        right_layout.addLayout(readout_bar)

        pg.setConfigOptions(antialias=True)
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setLabel("bottom", "Voltage, V")
        self.plot_widget.setLabel("left", "Current, uA")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.25)
        self.plot_legend = self.plot_widget.addLegend(offset=(10, 10))
        self.plot_widget.scene().sigMouseMoved.connect(self._on_plot_mouse_moved)
        right_layout.addWidget(self.plot_widget, stretch=1)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def _create_general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.spin_start_e = QDoubleSpinBox()
        self.spin_start_e.setRange(-5.0, 5.0)
        self.spin_start_e.setDecimals(4)
        self.spin_start_e.setSingleStep(0.05)
        self.spin_start_e.valueChanged.connect(self._on_params_edited)
        form.addRow("Start E (V):", self.spin_start_e)

        self.spin_end_e = QDoubleSpinBox()
        self.spin_end_e.setRange(-5.0, 5.0)
        self.spin_end_e.setDecimals(4)
        self.spin_end_e.setSingleStep(0.05)
        self.spin_end_e.valueChanged.connect(self._on_params_edited)
        form.addRow("End E (V):", self.spin_end_e)

        self.spin_eq_e = QDoubleSpinBox()
        self.spin_eq_e.setRange(-5.0, 5.0)
        self.spin_eq_e.setDecimals(4)
        self.spin_eq_e.setSingleStep(0.05)
        self.spin_eq_e.valueChanged.connect(self._on_params_edited)
        form.addRow("Equilibration E (V):", self.spin_eq_e)

        self.spin_eq_time = QSpinBox()
        self.spin_eq_time.setRange(0, 3600)
        self.spin_eq_time.valueChanged.connect(self._on_params_edited)
        form.addRow("Equilibration time (s):", self.spin_eq_time)

        after_row = QHBoxLayout()
        self.chk_after_e = QCheckBox("After E (V):")
        self.chk_after_e.toggled.connect(self._on_params_edited)
        self.spin_after_e = QDoubleSpinBox()
        self.spin_after_e.setRange(-5.0, 5.0)
        self.spin_after_e.setDecimals(4)
        self.spin_after_e.setSingleStep(0.05)
        self.spin_after_e.valueChanged.connect(self._on_params_edited)
        after_row.addWidget(self.chk_after_e)
        after_row.addWidget(self.spin_after_e, stretch=1)
        form.addRow(after_row)

        self.spin_i_range = QDoubleSpinBox()
        self.spin_i_range.setRange(1.0, 1000.0)
        self.spin_i_range.setDecimals(1)
        self.spin_i_range.setSingleStep(10.0)
        self.spin_i_range.valueChanged.connect(self._on_i_range_changed)
        form.addRow("Current range (uA):", self.spin_i_range)

        self.chk_no_readback = QCheckBox("No potential readback")
        self.chk_no_readback.setChecked(True)
        self.chk_no_readback.toggled.connect(self._on_params_edited)
        form.addRow(self.chk_no_readback)

        self.btn_general_real = QPushButton("Show real values")
        self.btn_general_real.clicked.connect(self.show_real_values_dialog)
        form.addRow(self.btn_general_real)
        return w

    def _create_cv_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.spin_scan_rate = QDoubleSpinBox()
        self.spin_scan_rate.setRange(0.001, 10.0)
        self.spin_scan_rate.setDecimals(4)
        self.spin_scan_rate.setSingleStep(0.01)
        self.spin_scan_rate.valueChanged.connect(self._on_params_edited)
        form.addRow("Scan rate (V/s):", self.spin_scan_rate)

        self.spin_cv_cycles = QSpinBox()
        self.spin_cv_cycles.setRange(1, 100)
        self.spin_cv_cycles.valueChanged.connect(self._on_params_edited)
        form.addRow("Cycles:", self.spin_cv_cycles)

        self.chk_cv_half = QCheckBox("Half cycle (LSV)")
        self.chk_cv_half.toggled.connect(self._on_params_edited)
        form.addRow(self.chk_cv_half)
        return w

    def _create_dpv_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.spin_step_e = QDoubleSpinBox()
        self.spin_step_e.setRange(0.1, 500.0)
        self.spin_step_e.setDecimals(2)
        self.spin_step_e.setSingleStep(1.0)
        self.spin_step_e.valueChanged.connect(self._on_params_edited)
        form.addRow("Step E (mV):", self.spin_step_e)

        self.spin_pulse_amp = QDoubleSpinBox()
        self.spin_pulse_amp.setRange(0.1, 500.0)
        self.spin_pulse_amp.setDecimals(2)
        self.spin_pulse_amp.setSingleStep(5.0)
        self.spin_pulse_amp.valueChanged.connect(self._on_params_edited)
        form.addRow("Pulse amplitude (mV):", self.spin_pulse_amp)

        self.spin_pulse_period = QSpinBox()
        self.spin_pulse_period.setRange(1, 10000)
        self.spin_pulse_period.setSingleStep(10)
        self.spin_pulse_period.valueChanged.connect(self._on_params_edited)
        form.addRow("Pulse period (ms):", self.spin_pulse_period)

        self.spin_pulse_width = QSpinBox()
        self.spin_pulse_width.setRange(1, 10000)
        self.spin_pulse_width.setSingleStep(5)
        self.spin_pulse_width.valueChanged.connect(self._on_params_edited)
        form.addRow("Pulse width (ms):", self.spin_pulse_width)

        self.spin_sampling_period = QSpinBox()
        self.spin_sampling_period.setRange(1, 1000)
        self.spin_sampling_period.valueChanged.connect(self._on_params_edited)
        form.addRow("Sampling period (ms):", self.spin_sampling_period)

        self.chk_dpv_half = QCheckBox("Half cycle")
        self.chk_dpv_half.setChecked(True)
        self.chk_dpv_half.toggled.connect(self._on_params_edited)
        form.addRow(self.chk_dpv_half)

        self.btn_dpv_real = QPushButton("Show real values")
        self.btn_dpv_real.clicked.connect(self.show_real_values_dialog)
        form.addRow(self.btn_dpv_real)
        return w

    def _create_ca_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.spin_ca_time = QSpinBox()
        self.spin_ca_time.setRange(1, 3600)
        self.spin_ca_time.valueChanged.connect(self._on_params_edited)
        form.addRow("Time (s):", self.spin_ca_time)

        self.spin_ca_points = QSpinBox()
        self.spin_ca_points.setRange(1, 2000)
        self.spin_ca_points.setSingleStep(10)
        self.spin_ca_points.valueChanged.connect(self._on_params_edited)
        form.addRow("Datapoints:", self.spin_ca_points)
        return w

    def _create_signal_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        row = QHBoxLayout()
        self.btn_savgol = QPushButton("Savitzky-Golay")
        self.btn_savgol.clicked.connect(lambda: self.apply_smoothing_to_selected("savgol"))
        row.addWidget(self.btn_savgol)

        self.btn_moving_avg = QPushButton("Running average")
        self.btn_moving_avg.clicked.connect(lambda: self.apply_smoothing_to_selected("moving_avg"))
        row.addWidget(self.btn_moving_avg)

        self.spin_smooth_window = QSpinBox()
        self.spin_smooth_window.setRange(5, 99)
        self.spin_smooth_window.setSingleStep(2)
        self.spin_smooth_window.setValue(15)
        row.addWidget(self.spin_smooth_window)
        layout.addLayout(row)

        self.btn_reset_raw = QPushButton("Restore Unsmoothed (Raw)")
        self.btn_reset_raw.clicked.connect(lambda: self.apply_smoothing_to_selected("raw"))
        layout.addWidget(self.btn_reset_raw)

        note = QLabel(
            "Smoothing is non-destructive: raw readings are always preserved and can be "
            "restored at any time."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return w

    def _create_log_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        top = QHBoxLayout()
        self.chk_log_visible = QCheckBox("Visible")
        self.chk_log_visible.setChecked(self.settings.log_visible)
        self.chk_log_visible.toggled.connect(self._on_log_visible_toggled)
        top.addWidget(self.chk_log_visible)
        top.addStretch(1)
        btn_clear_log = QPushButton("Clear Log")
        btn_clear_log.clicked.connect(lambda: self.txt_log.clear())
        top.addWidget(btn_clear_log)
        layout.addLayout(top)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        font = QFont("Consolas", 9)
        self.txt_log.setFont(font)
        self.txt_log.setVisible(self.settings.log_visible)
        layout.addWidget(self.txt_log, stretch=1)
        return w

    def _build_statusbar(self) -> None:
        sb = QStatusBar(self)
        self.setStatusBar(sb)

        self.lbl_status = QLabel("Idle.")
        self.lbl_status.setMinimumWidth(140)
        sb.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setTextVisible(True)
        sb.addPermanentWidget(self.progress_bar)

        self.lbl_time_remaining = QLabel("Remaining: 0.0 s")
        self.lbl_time_remaining.setMinimumWidth(130)
        sb.addPermanentWidget(self.lbl_time_remaining)

        self.lbl_sat_warning = QLabel("")
        self.lbl_sat_warning.setStyleSheet("color: #d62728; font-weight: bold;")
        sb.addPermanentWidget(self.lbl_sat_warning)

        self._update_port_badge()

    # ------------------------------------------------------------------
    # Parameter Synchronisation & Live Validation
    # ------------------------------------------------------------------
    def get_experiment_params(self) -> ExperimentParams:
        tech = self.tech_btn_group.checkedId()
        if tech not in (TECH_CV, TECH_DPV, TECH_CA):
            tech = TECH_CV

        gen = GeneralParams(
            v_start=float(self.spin_start_e.value()),
            v_stop=float(self.spin_end_e.value()),
            v_eq=float(self.spin_eq_e.value()),
            t_eq=int(self.spin_eq_time.value()),
            v_after=float(self.spin_after_e.value()),
            e_apply_after=bool(self.chk_after_e.isChecked()),
            i_range_ua=float(self.spin_i_range.value()),
            no_e_readback=bool(self.chk_no_readback.isChecked()),
        )
        cv = CVParams(
            scan_rate=float(self.spin_scan_rate.value()),
            cycles=int(self.spin_cv_cycles.value()),
            half_cycle=bool(self.chk_cv_half.isChecked()),
        )
        dpv = DPVParams(
            step_e_mv=float(self.spin_step_e.value()),
            pulse_amplitude_mv=float(self.spin_pulse_amp.value()),
            pulse_period_ms=int(self.spin_pulse_period.value()),
            pulse_width_ms=int(self.spin_pulse_width.value()),
            sampling_period_ms=int(self.spin_sampling_period.value()),
            half_cycle=bool(self.chk_dpv_half.isChecked()),
            cycles=int(self.spin_cv_cycles.value()),
        )
        ca = CAParams(
            ca_time_s=int(self.spin_ca_time.value()),
            ca_datapoints=int(self.spin_ca_points.value()),
        )
        return ExperimentParams(technique=tech, general=gen, cv=cv, dpv=dpv, ca=ca)

    def set_experiment_params(self, params: ExperimentParams) -> None:
        widgets = [
            self.spin_start_e, self.spin_end_e, self.spin_eq_e, self.spin_eq_time,
            self.chk_after_e, self.spin_after_e, self.spin_i_range, self.chk_no_readback,
            self.spin_scan_rate, self.spin_cv_cycles, self.chk_cv_half,
            self.spin_step_e, self.spin_pulse_amp, self.spin_pulse_period,
            self.spin_pulse_width, self.spin_sampling_period, self.chk_dpv_half,
            self.spin_ca_time, self.spin_ca_points,
        ]
        for w in widgets:
            w.blockSignals(True)
        try:
            if params.technique == TECH_DPV:
                self.rb_dpv.setChecked(True)
            elif params.technique == TECH_CA:
                self.rb_ca.setChecked(True)
            else:
                self.rb_cv.setChecked(True)

            g = params.general
            self.spin_start_e.setValue(g.v_start)
            self.spin_end_e.setValue(g.v_stop)
            self.spin_eq_e.setValue(g.v_eq)
            self.spin_eq_time.setValue(g.t_eq)
            self.chk_after_e.setChecked(g.e_apply_after)
            self.spin_after_e.setValue(g.v_after)
            self.spin_i_range.setValue(g.i_range_ua)
            self.chk_no_readback.setChecked(g.no_e_readback)

            self.spin_scan_rate.setValue(params.cv.scan_rate)
            self.spin_cv_cycles.setValue(params.cv.cycles)
            self.chk_cv_half.setChecked(params.cv.half_cycle)

            self.spin_step_e.setValue(params.dpv.step_e_mv)
            self.spin_pulse_amp.setValue(params.dpv.pulse_amplitude_mv)
            self.spin_pulse_period.setValue(params.dpv.pulse_period_ms)
            self.spin_pulse_width.setValue(params.dpv.pulse_width_ms)
            self.spin_sampling_period.setValue(params.dpv.sampling_period_ms)
            self.chk_dpv_half.setChecked(params.dpv.half_cycle)

            self.spin_ca_time.setValue(params.ca.ca_time_s)
            self.spin_ca_points.setValue(params.ca.ca_datapoints)
        finally:
            for w in widgets:
                w.blockSignals(False)

        self._update_axes_for_technique(params.technique)
        self._run_live_validation()

    def _on_technique_changed(self, tech_id: int) -> None:
        if tech_id == TECH_CV:
            self.tabs.setCurrentIndex(1)
        elif tech_id == TECH_DPV:
            self.tabs.setCurrentIndex(2)
        elif tech_id == TECH_CA:
            self.tabs.setCurrentIndex(3)
        self._update_axes_for_technique(tech_id)
        self._run_live_validation()

    def _update_axes_for_technique(self, tech_id: int) -> None:
        self.spin_end_e.setEnabled(tech_id != TECH_CA)
        if tech_id == TECH_CA:
            self.lbl_ei_title.setText("t/s, I/uA =")
            self.plot_widget.setLabel("bottom", "Time, s")
        else:
            self.lbl_ei_title.setText("E/V, I/uA =")
            self.plot_widget.setLabel("bottom", "Voltage, V")

    def _on_params_edited(self) -> None:
        self._run_live_validation()

    def _on_i_range_changed(self) -> None:
        i_lim = max(1.0, float(self.spin_i_range.value()))
        self.plot_widget.setYRange(-i_lim, i_lim, padding=0.05)
        self._run_live_validation()

    def _run_live_validation(self) -> ValidationResult:
        params = self.get_experiment_params()
        res = validate_params(params, self.settings)
        is_running = self.worker is not None and self.worker.isRunning()

        if not res.ok:
            self.lbl_validation.setStyleSheet(
                "background-color: #4a1c1c; color: #ff9999; padding: 4px; border-radius: 3px;"
            )
            self.lbl_validation.setText("ERROR: " + " | ".join(res.errors))
            self.act_run.setEnabled(False)
        elif res.warnings:
            self.lbl_validation.setStyleSheet(
                "background-color: #4a3c14; color: #ffdd88; padding: 4px; border-radius: 3px;"
            )
            self.lbl_validation.setText("WARNING: " + " | ".join(res.warnings))
            self.act_run.setEnabled(not is_running)
        else:
            real = compute_real_values(params)
            est = expected_run_duration_s(params)
            self.lbl_validation.setStyleSheet(
                "background-color: #1c3b27; color: #99ffbb; padding: 4px; border-radius: 3px;"
            )
            self.lbl_validation.setText(
                f"Ready | Real Start={real.v_start_real:+.4f} V, End={real.v_stop_real:+.4f} V "
                f"| Est. duration: {est:.1f} s"
            )
            self.act_run.setEnabled(not is_running)
        return res

    # ------------------------------------------------------------------
    # Presets & Real Values
    # ------------------------------------------------------------------
    def _on_builtin_preset_selected(self, index: int) -> None:
        mapping = {0: "CV", 1: "LSV", 2: "DPV", 3: "CA", 4: "DUMMY"}
        key = mapping.get(index, "CV")
        self.set_experiment_params(make_paper_defaults(key))
        self.autoscale_plot()

    def save_preset_json(self, file_path: str | None = None) -> bool:
        path = file_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Parameter Preset", self.settings.last_directory, "JSON Files (*.json)"
            )
        if not path:
            return False
        params = self.get_experiment_params()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(params.to_dict(), fh, indent=2)
        self.settings.last_directory = str(Path(path).parent)
        return True

    def load_preset_json(self, file_path: str | None = None) -> bool:
        path = file_path
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "Load Parameter Preset", self.settings.last_directory, "JSON Files (*.json)"
            )
        if not path:
            return False
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        params = ExperimentParams.from_dict(data)
        self.set_experiment_params(params)
        self.settings.last_directory = str(Path(path).parent)
        return True

    def show_real_values_dialog(self) -> str:
        params = self.get_experiment_params()
        real = compute_real_values(params)
        text = (
            f"10-bit SAMD21 DAC Quantised Values (3.3 V reference):\n\n"
            f"• Start E:   requested {params.general.v_start:+.4f} V  →  "
            f"real {real.v_start_real:+.4f} V  (DAC code {real.dac_start})\n"
            f"• End E:     requested {params.general.v_stop:+.4f} V  →  "
            f"real {real.v_stop_real:+.4f} V  (DAC code {real.dac_stop})\n"
            f"• Eq. E:     requested {params.general.v_eq:+.4f} V  →  "
            f"real {real.v_eq_real:+.4f} V  (DAC code {real.dac_eq})\n"
            f"• After E:   requested {params.general.v_after:+.4f} V  →  "
            f"real {real.v_after_real:+.4f} V  (DAC code {real.dac_after})\n\n"
            f"DPV Step & Amplitude (resolution 3.2227 mV / LSB):\n"
            f"• Step E:    requested {params.dpv.step_e_mv:.2f} mV  →  "
            f"real {real.step_e_real_mv:.2f} mV  (i_StepE = {real.i_step_e})\n"
            f"• Amplitude: requested {params.dpv.pulse_amplitude_mv:.2f} mV  →  "
            f"real {real.pulse_amplitude_real_mv:.2f} mV  (i_PulseAmplitude = {real.i_pulse_amplitude})"
        )
        if self.isVisible():
            QMessageBox.information(self, "Real Hardware Values", text)
        return text

    # ------------------------------------------------------------------
    # Running & Aborting Experiments
    # ------------------------------------------------------------------
    def start_run(self) -> bool:
        if self.worker is not None and self.worker.isRunning():
            return False
        val = self._run_live_validation()
        if not val.ok:
            return False

        params = self.get_experiment_params()
        self.settings.last_params = params.to_dict()
        self.settings.save()

        run_index = len(self.datasets) + 1
        ds = DatasetRun(
            name=f"File {run_index} ({self._tech_short_name(params)})",
            technique=params.technique,
            params=params,
            port=self.settings.serial_port,
        )
        self.datasets.append(ds)
        self.active_dataset = ds
        self._rebuild_selection_tree()
        self.autoscale_plot()

        self.lbl_sat_warning.setText("")
        self.progress_bar.setValue(0)
        self.act_run.setEnabled(False)
        self.act_abort.setEnabled(True)

        self.worker = SerialRunWorker(
            port=self.settings.serial_port,
            params=params,
            v_park=self.settings.park_potential_v,
            fast_sim=self.fast_sim,
            parent=self,
        )
        self.worker.section_started.connect(self._on_worker_section_started)
        self.worker.point.connect(self._on_worker_point)
        self.worker.status_changed.connect(self._on_worker_status_changed)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.log_line.connect(self._append_log)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.error.connect(self._on_worker_error)
        self.worker.start()
        return True

    def abort_run(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.lbl_status.setText("Aborting...")
            self.worker.request_abort()

    def _tech_short_name(self, params: ExperimentParams) -> str:
        if params.technique == TECH_CV:
            return "LSV" if params.cv.half_cycle else "CV"
        if params.technique == TECH_DPV:
            return "DPV"
        return "CA"

    def _on_worker_section_started(self, cycle_index: int) -> None:
        if self.active_dataset is None:
            return
        self.active_dataset.get_or_create_cycle(cycle_index)
        self._rebuild_selection_tree()

    def _on_worker_point(
        self, cycle_index: int, x_val: float, y_val: float, saturated: bool
    ) -> None:
        if self.active_dataset is None:
            return
        cyc = self.active_dataset.get_or_create_cycle(cycle_index)
        first_point = len(cyc.x) == 0
        cyc.append_point(x_val, y_val, saturated=saturated)
        if first_point:
            self._rebuild_selection_tree()

        self.edt_ei.setText(f"{x_val:.4f}, {y_val:.4f}")
        if saturated:
            self.lbl_sat_warning.setText(
                f"SATURATION WARNING: |I| > {CURRENT_SATURATION_WARN_UA:.0f} uA!"
            )
        self._plot_dirty = True

    def _on_worker_status_changed(self, status_text: str) -> None:
        self.lbl_status.setText(f"{status_text}.")

    def _on_worker_progress(self, elapsed_s: float, total_s: float) -> None:
        if total_s > 0:
            frac = max(0.0, min(1.0, elapsed_s / total_s))
            self.progress_bar.setValue(int(frac * 1000))
            rem = max(0.0, total_s - elapsed_s)
            self.lbl_time_remaining.setText(f"Remaining: {rem:.1f} s")
        else:
            self.progress_bar.setValue(1000)
            self.lbl_time_remaining.setText("Remaining: 0.0 s")

    def _on_worker_finished(self, aborted: bool) -> None:
        self.act_abort.setEnabled(False)
        self._run_live_validation()
        self.lbl_status.setText("Idle (Aborted)." if aborted else "Done.")
        self.lbl_time_remaining.setText("Remaining: 0.0 s")
        if self.active_dataset is not None:
            peak = self.active_dataset.signed_peak_current_ua
            self.edt_ipeak.setText(f"{peak:.3f}")
        self.redraw_plot()

    def _on_worker_error(self, message: str) -> None:
        self.act_abort.setEnabled(False)
        self._run_live_validation()
        self.lbl_status.setText("Error.")
        self._append_log(f"ERROR: {message}")
        if self.isVisible():
            QMessageBox.warning(self, "Measurement Error", message)

    # ------------------------------------------------------------------
    # File Open, Save, TSV Converter & Screenshot/SVG Export (Stage 3)
    # ------------------------------------------------------------------
    def open_files(self, file_paths: list[str | Path]) -> list[DatasetRun]:
        """Load one or more .tsv/.csv/.txt datafiles into the selection tree and plot."""
        loaded: list[DatasetRun] = []
        for fp in file_paths:
            ds = load_datafile(fp)
            self.datasets.append(ds)
            self.active_dataset = ds
            loaded.append(ds)
            self.settings.last_directory = str(Path(fp).parent)

        if loaded:
            last_ds = loaded[-1]
            self._update_axes_for_technique(last_ds.technique)
            self.edt_ipeak.setText(f"{last_ds.signed_peak_current_ua:.3f}")
            self._rebuild_selection_tree()
            self.redraw_plot()
            self.autoscale_plot()
        return loaded

    def save_selected_dataset(self, csv_path: str | Path) -> tuple[Path, Path] | None:
        """Save the currently selected (or most recent) dataset to .csv + .json."""
        ds = self._get_selected_dataset()
        if ds is None:
            return None
        saved_csv, saved_json = save_dataset_csv_json(ds, csv_path)
        self.settings.last_directory = str(saved_csv.parent)
        self._rebuild_selection_tree()
        return saved_csv, saved_json

    def _export_svg_qt(self, target: Path) -> None:
        """Write the plot as a vector SVG using Qt's own generator.

        pyqtgraph's SVGExporter crashes on the ViewBox clip path (it cannot parse
        the closing 'Z' command), so we render the widget through QSvgGenerator.
        """
        from PySide6.QtCore import QRectF, QSize
        from PySide6.QtGui import QPainter
        from PySide6.QtSvg import QSvgGenerator

        widget = self.plot_widget
        size = widget.size()
        if size.width() < 50 or size.height() < 50:  # widget not shown yet (tests, headless)
            size = QSize(900, 600)
            widget.resize(size)
        gen = QSvgGenerator()
        gen.setFileName(str(target))
        gen.setSize(size)
        gen.setViewBox(QRectF(0, 0, size.width(), size.height()))
        gen.setTitle("HunStat plot")
        painter = QPainter()
        if not painter.begin(gen):
            raise RuntimeError(f"Could not open SVG file for writing: {target}")
        try:
            widget.render(painter)
        finally:
            painter.end()

    def export_plot_image(self, file_path: str | Path) -> Path:
        """Export the plot canvas to PNG or SVG."""
        target = Path(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == ".svg":
            self._export_svg_qt(target)
        else:
            if target.suffix.lower() not in (".png", ".jpg", ".bmp"):
                target = target.with_suffix(".png")
            pixmap = self.plot_widget.grab()
            if not pixmap.save(str(target)):
                exporter = pg_exporters.ImageExporter(self.plot_widget.plotItem)
                exporter.export(str(target))
        return target

    def _on_open_clicked(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Datafile(s)",
            self.settings.last_directory,
            "Electrochemistry Data (*.csv *.tsv *.txt);;Legacy HunStat (*.tsv);;CSV Files (*.csv);;All Files (*.*)",
        )
        if not paths:
            return
        try:
            self.open_files(paths)
        except Exception as exc:
            QMessageBox.warning(self, "Open Error", f"Failed to load file:\n{exc}")

    def _on_save_clicked(self) -> None:
        ds = self._get_selected_dataset()
        if ds is None or not ds.cycles:
            QMessageBox.information(self, "Save", "No dataset available to save.")
            return
        default_name = str(Path(self.settings.last_directory) / f"{ds.name}.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Dataset as CSV + JSON", default_name, "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            res = self.save_selected_dataset(path)
            if res is not None:
                csv_p, json_p = res
                self.lbl_status.setText(f"Saved {csv_p.name} + {json_p.name}.")
        except Exception as exc:
            QMessageBox.warning(self, "Save Error", f"Failed to save dataset:\n{exc}")

    def _on_convert_tsv_button(self) -> None:
        menu = QMenu(self)
        menu.addAction(self.act_convert_file)
        menu.addAction(self.act_convert_folder)
        menu.exec(self.btn_convert_tsv.mapToGlobal(self.btn_convert_tsv.rect().bottomLeft()))

    def _on_convert_tsv_file_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Legacy .tsv File to Convert",
            self.settings.last_directory,
            "Legacy TSV Files (*.tsv);;All Files (*.*)",
        )
        if not path:
            return
        try:
            out_csv, out_json = convert_legacy_tsv_to_csv(path)
            self.settings.last_directory = str(out_csv.parent)
            QMessageBox.information(
                self,
                "Conversion Complete",
                f"Converted legacy TSV to:\n• {out_csv}\n• {out_json}",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Conversion Error", str(exc))

    def _on_convert_tsv_folder_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder Containing Legacy .tsv Files", self.settings.last_directory
        )
        if not folder:
            return
        try:
            pairs = convert_legacy_folder(folder)
            self.settings.last_directory = folder
            QMessageBox.information(
                self,
                "Batch Conversion Complete",
                f"Converted {len(pairs)} legacy .tsv file(s) in:\n{folder}",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Batch Conversion Error", str(exc))

    def _on_screenshot_clicked(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Plot Image",
            self.settings.last_directory,
            "PNG Image (*.png);;SVG Vector Graphic (*.svg)",
        )
        if not path:
            return
        try:
            out = self.export_plot_image(path)
            self.settings.last_directory = str(out.parent)
            self.lbl_status.setText(f"Exported plot to {out.name}.")
        except Exception as exc:
            QMessageBox.warning(self, "Export Error", f"Failed to export plot:\n{exc}")

    # ------------------------------------------------------------------
    # Selection Tree, Smoothing & Plot Redraw
    # ------------------------------------------------------------------
    def _rebuild_selection_tree(self) -> None:
        self._updating_tree = True
        try:
            selected_ds = self._get_selected_dataset()
            self.tree_selection.clear()
            for ds_idx, ds in enumerate(self.datasets):
                parent_item = QTreeWidgetItem(self.tree_selection)
                label = ds.name if ds.smoothing_label == "Raw" else f"{ds.name} [{ds.smoothing_label}]"
                parent_item.setText(0, label)
                parent_item.setData(0, Qt.ItemDataRole.UserRole, ("dataset", ds_idx))
                parent_item.setCheckState(
                    0, Qt.CheckState.Checked if ds.visible else Qt.CheckState.Unchecked
                )
                parent_item.setExpanded(True)

                for cyc in ds.cycles:
                    child = QTreeWidgetItem(parent_item)
                    child.setText(0, f"Cycle {cyc.cycle_index} ({len(cyc.x)} pts)")
                    child.setData(0, Qt.ItemDataRole.UserRole, ("cycle", ds_idx, cyc.cycle_index))
                    child.setCheckState(
                        0, Qt.CheckState.Checked if cyc.visible else Qt.CheckState.Unchecked
                    )
                if ds is selected_ds:
                    parent_item.setSelected(True)
        finally:
            self._updating_tree = False

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating_tree:
            return
        meta = item.data(0, Qt.ItemDataRole.UserRole)
        if not meta:
            return
        checked = item.checkState(0) == Qt.CheckState.Checked
        if meta[0] == "dataset":
            ds_idx = meta[1]
            if 0 <= ds_idx < len(self.datasets):
                ds = self.datasets[ds_idx]
                ds.visible = checked
                for cyc in ds.cycles:
                    cyc.visible = checked
                self._rebuild_selection_tree()
        elif meta[0] == "cycle":
            _, ds_idx, cyc_idx = meta
            if 0 <= ds_idx < len(self.datasets):
                ds = self.datasets[ds_idx]
                for cyc in ds.cycles:
                    if cyc.cycle_index == cyc_idx:
                        cyc.visible = checked
        self.redraw_plot()

    def _on_tree_selection_changed(self) -> None:
        ds = self._get_selected_dataset()
        if ds is not None and ds.cycles:
            self.edt_ipeak.setText(f"{ds.signed_peak_current_ua:.3f}")
        if getattr(self, "analysis_dock", None) is not None:
            self.analysis_dock.on_dataset_selection_changed()

    def _get_selected_dataset(self) -> DatasetRun | None:
        items = self.tree_selection.selectedItems()
        if not items:
            return self.active_dataset or (self.datasets[-1] if self.datasets else None)
        meta = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not meta:
            return self.active_dataset
        ds_idx = meta[1]
        if 0 <= ds_idx < len(self.datasets):
            return self.datasets[ds_idx]
        return self.active_dataset

    def clear_all_datasets(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.datasets.clear()
        self.active_dataset = None
        self.tree_selection.clear()
        self.edt_ipeak.clear()
        self.redraw_plot()

    def apply_smoothing_to_selected(self, method: str) -> None:
        ds = self._get_selected_dataset()
        if ds is None:
            return
        window = int(self.spin_smooth_window.value())
        ds.apply_smoothing(method, window)
        self.edt_ipeak.setText(f"{ds.signed_peak_current_ua:.3f}")
        self._rebuild_selection_tree()
        self.redraw_plot()

    def _on_plot_timer(self) -> None:
        if self._plot_dirty:
            self._plot_dirty = False
            self.redraw_plot()

    def redraw_plot(self) -> None:
        self.plot_widget.clear()
        if self.plot_legend is not None:
            self.plot_legend.clear()

        color_cursor = 0
        for ds in self.datasets:
            if not ds.visible:
                continue
            for cyc in ds.cycles:
                if not cyc.visible or not cyc.x:
                    continue
                color = PLOT_COLORS[color_cursor % len(PLOT_COLORS)]
                color_cursor += 1
                pen = pg.mkPen(color=color, width=2)
                curve_name = f"{ds.name} - C{cyc.cycle_index}"
                self.plot_widget.plot(cyc.x, cyc.y_display, pen=pen, name=curve_name)

        if getattr(self, "analysis_dock", None) is not None:
            self.analysis_dock.draw_plot_overlays()

    def autoscale_plot(self) -> None:
        params = self.get_experiment_params()
        i_lim = max(1.0, params.general.i_range_ua)

        all_x: list[float] = []
        all_y: list[float] = []
        for ds in self.datasets:
            if not ds.visible:
                continue
            for cyc in ds.cycles:
                if cyc.visible and cyc.x:
                    all_x.extend(cyc.x)
                    all_y.extend(cyc.y_display)

        if all_x and all_y:
            xmin, xmax = min(all_x), max(all_x)
            ymin, ymax = min(all_y), max(all_y)
            if abs(xmax - xmin) < 1e-6:
                xmin -= 0.1
                xmax += 0.1
            y_bound = max(i_lim, abs(ymin) * 1.1, abs(ymax) * 1.1)
            self.plot_widget.setXRange(xmin, xmax, padding=0.03)
            self.plot_widget.setYRange(-y_bound, y_bound, padding=0.05)
        else:
            if params.technique == TECH_CA:
                self.plot_widget.setXRange(
                    0.0, float(params.general.t_eq + params.ca.ca_time_s), padding=0.03
                )
            else:
                self.plot_widget.setXRange(
                    params.general.v_start, params.general.v_stop, padding=0.03
                )
            self.plot_widget.setYRange(-i_lim, i_lim, padding=0.05)

    def _on_plot_mouse_moved(self, pos: Any) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        vb = self.plot_widget.getPlotItem().vb
        if vb.sceneBoundingRect().contains(pos):
            mouse_pt = vb.mapSceneToView(pos)
            self.edt_ei.setText(f"{mouse_pt.x():.4f}, {mouse_pt.y():.4f}")

    # ------------------------------------------------------------------
    # Log, Settings, Theme & About
    # ------------------------------------------------------------------
    def _on_log_visible_toggled(self, checked: bool) -> None:
        self.settings.log_visible = checked
        self.txt_log.setVisible(checked)

    def _append_log(self, line: str) -> None:
        self.txt_log.appendPlainText(line)

    def _update_port_badge(self) -> None:
        self.lbl_port_badge.setText(f"Port: {self.settings.serial_port}")

    def apply_theme(self, dark: bool) -> None:
        self.settings.dark_theme = dark
        if dark:
            self.setStyleSheet(DARK_STYLESHEET)
            self.plot_widget.setBackground("#181820")
        else:
            self.setStyleSheet("")
            self.plot_widget.setBackground("w")

    def open_settings_dialog(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.settings.save()
            self.apply_theme(self.settings.dark_theme)
            self._update_port_badge()
            self._run_live_validation()

    def show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME} v{APP_VERSION}</b><br><br>"
            "Modern Python/PySide6 desktop controller for the HunStat DIY Potentiostat "
            "(Seeeduino XIAO SAMD21, firmware PoteVK-3.2.ino).<br><br>"
            "Supports Cyclic Voltammetry (CV), Linear Sweep Voltammetry (LSV), "
            "Differential Pulse Voltammetry (DPV), and Chronoamperometry (CA).",
        )

    def closeEvent(self, event: Any) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_abort()
            self.worker.wait(3000)
        self.settings.last_params = self.get_experiment_params().to_dict()
        self.settings.save()
        super().closeEvent(event)