# hunstat/analysis.py
"""Pure-NumPy electrochemistry analysis engine (no Qt dependencies).

All potentials are in volts (V), currents in microamperes (uA), scan rates in V/s,
times in seconds (s), resistances in kiloohms (kOhm), and concentrations in user
units (e.g., mM or ppm).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray

from hunstat.models import CycleData, DatasetRun
from hunstat.settings import CURRENT_SATURATION_WARN_UA


def _as_1d_float(arr: ArrayLike, name: str = "array") -> NDArray[np.float64]:
    out = np.asarray(arr, dtype=float).ravel()
    if out.size == 0:
        raise ValueError(f"{name} must not be empty.")
    return out


def _resolve_anchor_point(
    e_arr: NDArray[np.float64],
    i_arr: NDArray[np.float64],
    pt: float | int | Sequence[float],
) -> tuple[float, float]:
    """Resolve an anchor specification into (E_V, I_uA)."""
    if isinstance(pt, (tuple, list, np.ndarray)):
        if len(pt) < 2:
            raise ValueError("Tuple/list baseline anchor must have 2 elements: (E_V, I_uA).")
        return float(pt[0]), float(pt[1])

    if isinstance(pt, (int, np.integer)) and not isinstance(pt, bool):
        idx = int(pt)
        if idx < 0:
            idx += len(e_arr)
        idx = max(0, min(len(e_arr) - 1, idx))
        return float(e_arr[idx]), float(i_arr[idx])

    target_e = float(pt)
    idx_near = int(np.argmin(np.abs(e_arr - target_e)))
    return float(e_arr[idx_near]), float(i_arr[idx_near])


def baseline_linear(
    E: ArrayLike,
    I: ArrayLike,
    p1: float | int | Sequence[float],
    p2: float | int | Sequence[float],
) -> NDArray[np.float64]:
    """Compute a straight-line baseline (uA) across potential array `E` (V)."""
    e_arr = _as_1d_float(E, "E")
    i_arr = _as_1d_float(I, "I")
    if e_arr.size != i_arr.size:
        raise ValueError(f"E and I must have the same length ({e_arr.size} != {i_arr.size}).")

    e1, i1 = _resolve_anchor_point(e_arr, i_arr, p1)
    e2, i2 = _resolve_anchor_point(e_arr, i_arr, p2)

    de = e2 - e1
    if abs(de) < 1e-12:
        return np.full_like(e_arr, 0.5 * (i1 + i2), dtype=float)

    slope = (i2 - i1) / de
    intercept = i1 - slope * e1
    return slope * e_arr + intercept


def subtract_baseline(
    I: ArrayLike,
    baseline: ArrayLike,
) -> NDArray[np.float64]:
    """Subtract a baseline current array (uA) from a measured current array (uA)."""
    i_arr = _as_1d_float(I, "I")
    b_arr = _as_1d_float(baseline, "baseline")
    if i_arr.size != b_arr.size:
        raise ValueError(
            f"I and baseline must have the same length ({i_arr.size} != {b_arr.size})."
        )
    return i_arr - b_arr


@dataclass(frozen=True)
class PeakResult:
    """Peak detection result supporting both `(Ep, Ip)` tuple unpacking and attributes."""

    Ep: float
    Ip: float
    index: int
    i_raw: float
    i_baseline: float

    @property
    def ep_v(self) -> float:
        return self.Ep

    @property
    def ip_ua(self) -> float:
        return self.Ip

    def __iter__(self) -> Iterator[float]:
        yield self.Ep
        yield self.Ip

    def __getitem__(self, idx: int) -> float:
        return (self.Ep, self.Ip)[idx]


def find_peak(
    E: ArrayLike,
    I: ArrayLike,
    baseline: ArrayLike | None = None,
    polarity: str = "anodic",
    e_min: float | None = None,
    e_max: float | None = None,
) -> PeakResult:
    """Find the anodic or cathodic peak (Ep in V, Ip height above baseline in uA)."""
    e_arr = _as_1d_float(E, "E")
    i_arr = _as_1d_float(I, "I")
    if e_arr.size != i_arr.size:
        raise ValueError(f"E and I must have the same length ({e_arr.size} != {i_arr.size}).")

    if baseline is None:
        b_arr = np.zeros_like(i_arr, dtype=float)
    else:
        b_arr = _as_1d_float(baseline, "baseline")
        if b_arr.size != i_arr.size:
            raise ValueError("baseline must have the same length as E and I.")

    corr = i_arr - b_arr

    mask = np.ones(e_arr.size, dtype=bool)
    if e_min is not None:
        mask &= e_arr >= float(e_min)
    if e_max is not None:
        mask &= e_arr <= float(e_max)
    valid_indices = np.flatnonzero(mask)
    if valid_indices.size == 0:
        valid_indices = np.arange(e_arr.size)

    pol = str(polarity).strip().lower()
    if pol in ("anodic", "positive", "+", "max", "oxidation"):
        rel_idx = int(np.argmax(corr[valid_indices]))
    elif pol in ("cathodic", "negative", "-", "min", "reduction"):
        rel_idx = int(np.argmin(corr[valid_indices]))
    elif pol in ("abs", "both", "auto"):
        rel_idx = int(np.argmax(np.abs(corr[valid_indices])))
    else:
        raise ValueError(f"Unknown peak polarity: {polarity!r}")

    peak_idx = int(valid_indices[rel_idx])
    return PeakResult(
        Ep=float(e_arr[peak_idx]),
        Ip=float(corr[peak_idx]),
        index=peak_idx,
        i_raw=float(i_arr[peak_idx]),
        i_baseline=float(b_arr[peak_idx]),
    )


@dataclass(frozen=True)
class CVMetrics:
    """Cyclic voltammetry metrics (Epa, Epc, delta_Ep, E_half in V; Ipa, Ipc in uA)."""

    Epa: float
    Epc: float
    delta_Ep: float
    Ipa: float
    Ipc: float
    Ipa_Ipc: float
    E_half: float

    @property
    def delta_Ep_mv(self) -> float:
        return self.delta_Ep * 1000.0

    def __getitem__(self, key: str) -> float:
        if key == "Ipa/Ipc":
            return self.Ipa_Ipc
        return float(getattr(self, key))


def _fit_initial_tangent_baseline(
    e_seg: NDArray[np.float64],
    i_seg: NDArray[np.float64],
    fraction: float = 0.20,
) -> NDArray[np.float64]:
    n = e_seg.size
    n_fit = max(2, min(n // 3, int(round(n * fraction))))
    if n_fit < 2 or abs(float(e_seg[n_fit - 1] - e_seg[0])) < 1e-9:
        return np.full_like(i_seg, float(i_seg[0]))
    slope, intercept = np.polyfit(e_seg[:n_fit], i_seg[:n_fit], 1)
    return slope * e_seg + intercept


def cv_metrics(
    cycle: CycleData | DatasetRun | tuple[ArrayLike, ArrayLike] | ArrayLike,
    I: ArrayLike | None = None,
    use_baseline: bool = True,
) -> CVMetrics:
    """Compute Epa (V), Epc (V), delta_Ep (V), Ipa (uA), Ipc (uA), |Ipa/Ipc|, and E_half (V)."""
    if isinstance(cycle, DatasetRun):
        if not cycle.cycles:
            raise ValueError("DatasetRun contains no cycles.")
        cyc_obj = cycle.cycles[-1]
        e_arr = _as_1d_float(cyc_obj.x, "E")
        i_arr = _as_1d_float(cyc_obj.y_display, "I")
    elif isinstance(cycle, CycleData):
        e_arr = _as_1d_float(cycle.x, "E")
        i_arr = _as_1d_float(cycle.y_display if cycle.y_display else cycle.y_raw, "I")
    elif I is not None:
        e_arr = _as_1d_float(cycle, "E")
        i_arr = _as_1d_float(I, "I")
    elif isinstance(cycle, (tuple, list)) and len(cycle) == 2:
        e_arr = _as_1d_float(cycle[0], "E")
        i_arr = _as_1d_float(cycle[1], "I")
    else:
        raise ValueError("Provide a CycleData, DatasetRun, or (E, I) arrays to cv_metrics().")

    if e_arr.size < 4 or e_arr.size != i_arr.size:
        raise ValueError("cv_metrics requires matching E and I arrays with at least 4 points.")

    turn_idx = int(np.argmax(e_arr))
    if 1 < turn_idx < (e_arr.size - 2):
        e_fwd, i_fwd = e_arr[: turn_idx + 1], i_arr[: turn_idx + 1]
        e_rev, i_rev = e_arr[turn_idx:], i_arr[turn_idx:]
    else:
        e_fwd, i_fwd = e_arr, i_arr
        e_rev, i_rev = e_arr, i_arr

    base_fwd = _fit_initial_tangent_baseline(e_fwd, i_fwd) if use_baseline else None
    base_rev = _fit_initial_tangent_baseline(e_rev, i_rev) if use_baseline else None

    anodic = find_peak(e_fwd, i_fwd, baseline=base_fwd, polarity="anodic")
    cathodic = find_peak(e_rev, i_rev, baseline=base_rev, polarity="cathodic")

    epa = anodic.Ep
    epc = cathodic.Ep
    ipa = anodic.Ip
    ipc = cathodic.Ip
    delta_ep = epa - epc
    e_half = 0.5 * (epa + epc)
    ratio = abs(ipa / ipc) if abs(ipc) > 1e-12 else float("nan")

    return CVMetrics(
        Epa=float(epa),
        Epc=float(epc),
        delta_Ep=float(delta_ep),
        Ipa=float(ipa),
        Ipc=float(ipc),
        Ipa_Ipc=float(ratio),
        E_half=float(e_half),
    )


def _linear_least_squares(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
) -> tuple[float, float, float]:
    if x.size != y.size or x.size < 2:
        raise ValueError("Linear fit requires matching x and y arrays with at least 2 points.")
    dx = x - np.mean(x)
    dy = y - np.mean(y)
    ss_xx = float(np.dot(dx, dx))
    if ss_xx < 1e-15:
        raise ValueError("x values in linear fit must not all be identical.")
    slope = float(np.dot(dx, dy) / ss_xx)
    intercept = float(np.mean(y) - slope * np.mean(x))
    y_hat = slope * x + intercept
    residuals = y - y_hat
    ss_res = float(np.dot(residuals, residuals))
    ss_tot = float(np.dot(dy, dy))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-15 else 1.0
    return slope, intercept, max(0.0, min(1.0, r_squared))


@dataclass(frozen=True)
class RandlesSevcikResult:
    """Linear regression of peak current `Ip` (uA) vs. `sqrt(scan_rate)` ((V/s)^0.5)."""

    slope: float
    intercept: float
    r_squared: float
    sqrt_scan_rates: NDArray[np.float64]
    peak_currents: NDArray[np.float64]
    fitted_currents: NDArray[np.float64]

    @property
    def R2(self) -> float:
        return self.r_squared


def randles_sevcik_fit(
    scan_rates: ArrayLike,
    peak_currents: ArrayLike,
) -> RandlesSevcikResult:
    """Fit peak current `Ip` (uA) against `sqrt(v)` where `v` is scan rate in V/s."""
    v_arr = _as_1d_float(scan_rates, "scan_rates")
    ip_arr = _as_1d_float(peak_currents, "peak_currents")
    if np.any(v_arr <= 0):
        raise ValueError("All scan rates must be strictly positive (> 0 V/s).")

    sqrt_v = np.sqrt(v_arr)
    slope, intercept, r2 = _linear_least_squares(sqrt_v, ip_arr)
    fitted = slope * sqrt_v + intercept
    return RandlesSevcikResult(
        slope=slope,
        intercept=intercept,
        r_squared=r2,
        sqrt_scan_rates=sqrt_v,
        peak_currents=ip_arr,
        fitted_currents=fitted,
    )


@dataclass(frozen=True)
class CalibrationResult:
    """Linear calibration fit `I (uA) = slope * c + intercept`."""

    slope: float
    intercept: float
    r_squared: float
    lod: float | None = None

    @property
    def R2(self) -> float:
        return self.r_squared

    @property
    def LOD(self) -> float | None:
        return self.lod

    def concentration_from_current(self, current_ua: float | ArrayLike) -> float | NDArray[np.float64]:
        if abs(self.slope) < 1e-15:
            raise ZeroDivisionError("Calibration slope is zero; cannot solve for concentration.")
        if isinstance(current_ua, (int, float, np.floating, np.integer)):
            return (float(current_ua) - self.intercept) / self.slope
        arr = np.asarray(current_ua, dtype=float)
        return (arr - self.intercept) / self.slope


def concentration_from_current(
    I: float | ArrayLike,
    slope_or_fit: float | CalibrationResult,
    intercept: float = 0.0,
) -> float | NDArray[np.float64]:
    """Compute unknown concentration `c = (I - intercept) / slope` from current `I` (uA)."""
    if isinstance(slope_or_fit, CalibrationResult):
        return slope_or_fit.concentration_from_current(I)
    slope = float(slope_or_fit)
    if abs(slope) < 1e-15:
        raise ZeroDivisionError("Calibration slope is zero; cannot solve for concentration.")
    if isinstance(I, (int, float, np.floating, np.integer)):
        return (float(I) - float(intercept)) / slope
    return (np.asarray(I, dtype=float) - float(intercept)) / slope


def calibration_fit(
    conc: ArrayLike,
    current: ArrayLike,
    sigma_blank: float | None = None,
) -> CalibrationResult:
    """Fit a linear calibration curve `I (uA) = slope * conc + intercept`."""
    c_arr = _as_1d_float(conc, "conc")
    i_arr = _as_1d_float(current, "current")
    slope, intercept, r2 = _linear_least_squares(c_arr, i_arr)
    lod: float | None = None
    if sigma_blank is not None and abs(slope) > 1e-15:
        lod = 3.0 * abs(float(sigma_blank)) / abs(slope)
    return CalibrationResult(slope=slope, intercept=intercept, r_squared=r2, lod=lod)


@dataclass(frozen=True)
class StandardAdditionResult:
    """Standard-addition linear regression result."""

    slope: float
    intercept: float
    r_squared: float
    x_intercept: float
    original_concentration: float
    dilution_factor: float

    @property
    def R2(self) -> float:
        return self.r_squared

    @property
    def original_conc(self) -> float:
        return self.original_concentration


def standard_addition(
    added_conc: ArrayLike,
    peak_current: ArrayLike,
    dilution_factor: float = 1.0,
) -> StandardAdditionResult:
    """Fit a standard-addition line and determine `original_concentration = |x_intercept| * dilution_factor`."""
    c_arr = _as_1d_float(added_conc, "added_conc")
    i_arr = _as_1d_float(peak_current, "peak_current")
    if float(dilution_factor) <= 0:
        raise ValueError("dilution_factor must be > 0.")

    slope, intercept, r2 = _linear_least_squares(c_arr, i_arr)
    if abs(slope) < 1e-15:
        raise ZeroDivisionError("Standard addition slope is zero; cannot compute x-intercept.")

    x_int = -intercept / slope
    orig_conc = abs(x_int) * float(dilution_factor)
    return StandardAdditionResult(
        slope=slope,
        intercept=intercept,
        r_squared=r2,
        x_intercept=x_int,
        original_concentration=orig_conc,
        dilution_factor=float(dilution_factor),
    )


def diode_turn_on(
    E: ArrayLike,
    I: ArrayLike,
    threshold_uA: float = 1.0,
) -> float | None:
    """Return the first potential `E` (V) where `|I|` (uA) exceeds `threshold_uA`."""
    e_arr = _as_1d_float(E, "E")
    i_arr = _as_1d_float(I, "I")
    if e_arr.size != i_arr.size:
        raise ValueError("E and I must have the same length.")
    thresh = abs(float(threshold_uA))

    hits = np.flatnonzero(np.abs(i_arr) > thresh)
    if hits.size == 0:
        return None
    return float(e_arr[int(hits[0])])


def ca_current_at(
    t: ArrayLike,
    I: ArrayLike,
    t_query: float,
    t_step: float = 0.0,
) -> float:
    """Evaluate chronoamperometry current (uA) at `t_query` seconds after `t_step` (s)."""
    t_arr = _as_1d_float(t, "t")
    i_arr = _as_1d_float(I, "I")
    if t_arr.size != i_arr.size:
        raise ValueError("t and I must have the same length.")

    target_t = float(t_step) + float(t_query)
    order = np.argsort(t_arr)
    t_sorted = t_arr[order]
    i_sorted = i_arr[order]
    return float(np.interp(target_t, t_sorted, i_sorted))


@dataclass(frozen=True)
class DummyCellResult:
    """10 kOhm dummy-cell self-test fit result."""

    resistance_kohm: float
    slope_ua_per_v: float
    intercept_ua: float
    r_squared: float
    current_at_1v_ua: float
    max_abs_current_ua: float
    passed: bool
    saturation_warning: bool

    @property
    def status_label(self) -> str:
        return "PASS" if self.passed else "FAIL"


def dummy_cell_fit(
    E: ArrayLike,
    I: ArrayLike,
    expected_kohm: float = 10.0,
    tolerance_rel: float = 0.10,
) -> DummyCellResult:
    """Fit an Ohmic I-V curve (`E` in V, `I` in uA) and check against a 10 kOhm dummy cell."""
    e_arr = _as_1d_float(E, "E")
    i_arr = _as_1d_float(I, "I")
    slope, intercept, r2 = _linear_least_squares(e_arr, i_arr)

    resistance_kohm = (1000.0 / slope) if abs(slope) > 1e-12 else float("inf")
    i_at_1v = slope * 1.0 + intercept
    max_abs_i = float(np.max(np.abs(i_arr)))
    sat_warn = max_abs_i > CURRENT_SATURATION_WARN_UA

    within_tol = abs(resistance_kohm - expected_kohm) <= (tolerance_rel * expected_kohm)
    passed = bool(within_tol and r2 >= 0.99 and slope > 0)

    return DummyCellResult(
        resistance_kohm=float(resistance_kohm),
        slope_ua_per_v=float(slope),
        intercept_ua=float(intercept),
        r_squared=float(r2),
        current_at_1v_ua=float(i_at_1v),
        max_abs_current_ua=max_abs_i,
        passed=passed,
        saturation_warning=sat_warn,
    )