# hunstat/models.py
"""In-memory data structures for electrochemical runs, cycles, and signal smoothing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import numpy as np
from scipy.signal import savgol_filter

from hunstat.settings import TECH_CA, TECH_CV, TECH_DPV, ExperimentParams, make_paper_defaults


# Distinct, high-contrast color palette for multi-file/multi-cycle overlays
PLOT_COLORS = [
    "#1f77b4",  # Blue
    "#d62728",  # Red
    "#2ca02c",  # Green
    "#ff7f0e",  # Orange
    "#9467bd",  # Purple
    "#17becf",  # Cyan
    "#e377c2",  # Pink
    "#8c564b",  # Brown
    "#bcbd22",  # Olive
]


@dataclass
class CycleData:
    """Data points for a single cycle/section within a dataset."""

    cycle_index: int
    x: list[float] = field(default_factory=list)  # E_V for CV/DPV, t_s for CA
    y_raw: list[float] = field(default_factory=list)  # Raw I_uA or dI_uA
    y_display: list[float] = field(default_factory=list)  # Smoothed or raw I_uA
    visible: bool = True
    saturated_count: int = 0

    def append_point(self, x_val: float, y_val: float, saturated: bool = False) -> None:
        self.x.append(float(x_val))
        self.y_raw.append(float(y_val))
        self.y_display.append(float(y_val))
        if saturated:
            self.saturated_count += 1

    def reset_smoothing(self) -> None:
        self.y_display = list(self.y_raw)

    def apply_savgol(self, window: int, polyorder: int = 2) -> None:
        """Apply non-destructive Savitzky-Golay filter from y_raw into y_display."""
        n = len(self.y_raw)
        if n < 5:
            self.y_display = list(self.y_raw)
            return
        w = max(5, int(window))
        if w % 2 == 0:
            w += 1
        if w > n:
            w = n if (n % 2 == 1) else (n - 1)
        if w < 5:
            self.y_display = list(self.y_raw)
            return
        poly = min(polyorder, w - 1)
        arr = np.asarray(self.y_raw, dtype=float)
        self.y_display = savgol_filter(arr, window_length=w, polyorder=poly).tolist()

    def apply_running_average(self, window: int) -> None:
        """Apply non-destructive edge-padded running average from y_raw into y_display."""
        n = len(self.y_raw)
        w = max(1, int(window))
        if n == 0 or w <= 1:
            self.y_display = list(self.y_raw)
            return
        w = min(w, n)
        arr = np.asarray(self.y_raw, dtype=float)
        pad_left = w // 2
        pad_right = w - 1 - pad_left
        padded = np.pad(arr, (pad_left, pad_right), mode="edge")
        kernel = np.ones(w, dtype=float) / float(w)
        smoothed = np.convolve(padded, kernel, mode="valid")
        self.y_display = smoothed.tolist()


@dataclass
class DatasetRun:
    """A complete multi-cycle measurement or loaded file in the selection tree."""

    name: str
    technique: int = TECH_CV
    params: ExperimentParams = field(default_factory=lambda: make_paper_defaults("CV"))
    cycles: list[CycleData] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    port: str = ""
    source_path: str = ""
    visible: bool = True
    smoothing_label: str = "Raw"

    def get_or_create_cycle(self, cycle_index: int) -> CycleData:
        for cyc in self.cycles:
            if cyc.cycle_index == cycle_index:
                return cyc
        new_cyc = CycleData(cycle_index=cycle_index)
        self.cycles.append(new_cyc)
        return new_cyc

    @property
    def total_points(self) -> int:
        return sum(len(c.x) for c in self.cycles)

    @property
    def max_abs_current_ua(self) -> float:
        peak = 0.0
        for c in self.cycles:
            if not c.visible or not c.y_display:
                continue
            m = max(abs(val) for val in c.y_display)
            if m > peak:
                peak = m
        return peak

    @property
    def signed_peak_current_ua(self) -> float:
        """Signed current with the largest magnitude across visible cycles."""
        best_val = 0.0
        best_abs = -1.0
        for c in self.cycles:
            if not c.visible or not c.y_display:
                continue
            for val in c.y_display:
                if abs(val) > best_abs:
                    best_abs = abs(val)
                    best_val = val
        return best_val if best_abs >= 0.0 else 0.0

    @property
    def x_axis_label(self) -> str:
        return "Time, s" if self.technique == TECH_CA else "Voltage, V"

    @property
    def technique_name(self) -> str:
        if self.technique == TECH_CV:
            return "LSV" if self.params.cv.half_cycle else "CV"
        if self.technique == TECH_DPV:
            return "DPV"
        if self.technique == TECH_CA:
            return "CA"
        return "CV"

    def apply_smoothing(self, method: str, window: int) -> None:
        """Apply 'savgol', 'moving_avg', or 'raw' across all cycles non-destructively."""
        key = method.lower()
        for cyc in self.cycles:
            if key == "savgol":
                cyc.apply_savgol(window)
                self.smoothing_label = f"SG({window})"
            elif key in ("moving_avg", "running_average"):
                cyc.apply_running_average(window)
                self.smoothing_label = f"Avg({window})"
            else:
                cyc.reset_smoothing()
                self.smoothing_label = "Raw"