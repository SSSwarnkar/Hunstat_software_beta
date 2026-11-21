# hunstat/protocol.py
"""Device protocol command builders, hardware quantisation, validation, and parser."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from hunstat.settings import (
    AppSettings,
    CA_MAX_DATAPOINTS,
    CURRENT_SATURATION_WARN_UA,
    DAC_MAX_CODE,
    DAC_RESOLUTION_DIVISOR,
    DAC_SCALE_PER_V,
    DAC_ZERO_CODE,
    DPV_MIN_STEP_MV,
    HARD_V_MAX,
    HARD_V_MIN,
    MAX_P_COMMAND_LEN,
    RUN_TIMEOUT_MARGIN_S,
    SCAN_RATE_MAX_VS,
    SCAN_RATE_MIN_VS,
    SCAN_RATE_WARN_VS,
    TECH_CA,
    TECH_CV,
    TECH_DPV,
    V_REF,
    ExperimentParams,
    GeneralParams,
    CVParams,
    DPVParams,
    CAParams,
)


def clamp_potential(v: float, v_min: float = HARD_V_MIN, v_max: float = HARD_V_MAX) -> float:
    """Hard-clamp a potential setpoint into the safe non-wrapping DAC range."""
    low = max(HARD_V_MIN, v_min)
    high = min(HARD_V_MAX, v_max)
    return max(low, min(high, float(v)))


def v_to_dac_code(v: float) -> int:
    """Replicate firmware integer truncation: int V_x = 512 + Vx * 312 (lines 122-133)."""
    code = int(DAC_ZERO_CODE + float(v) * DAC_SCALE_PER_V)
    return max(0, min(DAC_MAX_CODE, code))


def dac_code_to_real_v(code: int) -> float:
    """Replicate firmware voltage readback when NoEReadBack==1: (dacVolt-512)/1024*3.3."""
    return (int(code) - DAC_ZERO_CODE) * V_REF / DAC_RESOLUTION_DIVISOR


def real_potential_v(v: float) -> float:
    """Actual DAC output voltage for a requested setpoint V."""
    return dac_code_to_real_v(v_to_dac_code(v))


def mv_to_dpv_code(mv: float) -> int:
    """Replicate firmware DPV step/amplitude rounding: int((mv/3300*1024)+0.5) (lines 141, 144).

    Note: 0.5 DAC LSB is 1650/1024 = 1.611328125 mV, which the spec rounds to 1.61 mV.
    Adding a tiny 1e-3 epsilon ensures 1.61 mV maps to 1 while <= 1.60 mV maps to 0.
    """
    return int((float(mv) / (V_REF * 1000.0) * DAC_RESOLUTION_DIVISOR) + 0.5 + 1e-3)


def dpv_code_to_real_mv(code: int) -> float:
    """Actual step or pulse amplitude in mV corresponding to integer DAC steps."""
    return int(code) * (V_REF * 1000.0) / DAC_RESOLUTION_DIVISOR


def real_dpv_mv(mv: float) -> float:
    """Actual quantised DPV step or amplitude in mV."""
    return dpv_code_to_real_mv(mv_to_dpv_code(mv))


@dataclass
class RealHardwareValues:
    """Quantised values actually output by the 10-bit SAMD21 DAC."""

    v_start_real: float
    v_stop_real: float
    v_eq_real: float
    v_after_real: float
    step_e_real_mv: float
    pulse_amplitude_real_mv: float
    dac_start: int
    dac_stop: int
    dac_eq: int
    dac_after: int
    i_step_e: int
    i_pulse_amplitude: int


def compute_real_values(params: ExperimentParams) -> RealHardwareValues:
    """Compute exact DAC codes and resulting real voltages/steps for UI inspection."""
    g = params.general
    d = params.dpv
    return RealHardwareValues(
        v_start_real=real_potential_v(g.v_start),
        v_stop_real=real_potential_v(g.v_stop),
        v_eq_real=real_potential_v(g.v_eq),
        v_after_real=real_potential_v(g.v_after),
        step_e_real_mv=real_dpv_mv(d.step_e_mv),
        pulse_amplitude_real_mv=real_dpv_mv(d.pulse_amplitude_mv),
        dac_start=v_to_dac_code(g.v_start),
        dac_stop=v_to_dac_code(g.v_stop),
        dac_eq=v_to_dac_code(g.v_eq),
        dac_after=v_to_dac_code(g.v_after),
        i_step_e=mv_to_dpv_code(d.step_e_mv),
        i_pulse_amplitude=mv_to_dpv_code(d.pulse_amplitude_mv),
    )


@dataclass
class ValidationResult:
    """Result of validating an ExperimentParams bundle before sending to hardware."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def validate_params(
    params: ExperimentParams,
    settings: AppSettings | None = None,
) -> ValidationResult:
    """Validate parameters against firmware safety rules without raising exceptions."""
    cfg = settings or AppSettings()
    v_min = max(HARD_V_MIN, cfg.v_min)
    v_max = min(HARD_V_MAX, cfg.v_max)
    res = ValidationResult()

    g = params.general
    tech = params.technique

    floats_to_check = [
        ("Start E", g.v_start),
        ("End E", g.v_stop),
        ("Equilibration E", g.v_eq),
        ("After E", g.v_after),
        ("Current range", g.i_range_ua),
        ("Scan rate", params.cv.scan_rate),
        ("Step E", params.dpv.step_e_mv),
        ("Pulse amplitude", params.dpv.pulse_amplitude_mv),
    ]
    for label, val in floats_to_check:
        if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
            res.errors.append(f"{label} must be a finite number.")
            return res

    if not (v_min <= g.v_start <= v_max):
        res.errors.append(f"Start E ({g.v_start:.3f} V) must be within [{v_min:.2f}, {v_max:.2f}] V.")
    if tech in (TECH_CV, TECH_DPV):
        if not (v_min <= g.v_stop <= v_max):
            res.errors.append(f"End E ({g.v_stop:.3f} V) must be within [{v_min:.2f}, {v_max:.2f}] V.")
        if g.v_start >= g.v_stop:
            res.errors.append("Start E must be strictly less than End E (firmware only scans upward).")
        elif v_to_dac_code(g.v_start) >= v_to_dac_code(g.v_stop):
            res.errors.append("Start E and End E map to the same DAC step; increase span (>= 3.3 mV).")

    if not (v_min <= g.v_eq <= v_max):
        res.errors.append(f"Equilibration E ({g.v_eq:.3f} V) must be within [{v_min:.2f}, {v_max:.2f}] V.")
    if g.t_eq < 0:
        res.errors.append("Equilibration time must be an integer >= 0 s.")
    if g.e_apply_after and not (v_min <= g.v_after <= v_max):
        res.errors.append(f"After E ({g.v_after:.3f} V) must be within [{v_min:.2f}, {v_max:.2f}] V.")
    if g.i_range_ua <= 0:
        res.errors.append("Current range must be > 0 uA.")

    if tech == TECH_CV:
        sr_min = max(SCAN_RATE_MIN_VS, cfg.scan_rate_min)
        sr_max = min(SCAN_RATE_MAX_VS, cfg.scan_rate_max)
        if not (sr_min <= params.cv.scan_rate <= sr_max):
            res.errors.append(
                f"CV scan rate ({params.cv.scan_rate} V/s) must be between {sr_min} and {sr_max} V/s."
            )
        elif params.cv.scan_rate > SCAN_RATE_WARN_VS:
            res.warnings.append(
                f"Scan rate {params.cv.scan_rate} V/s exceeds {SCAN_RATE_WARN_VS} V/s; "
                "firmware 1 ms polling loop and serial output may not keep up."
            )
        if params.cv.cycles < 1:
            res.errors.append("CV cycles must be >= 1.")

    elif tech == TECH_DPV:
        d = params.dpv
        min_step = max(DPV_MIN_STEP_MV, cfg.dpv_min_step_mv)
        if d.step_e_mv < min_step or mv_to_dpv_code(d.step_e_mv) < 1:
            res.errors.append(
                f"DPV Step E ({d.step_e_mv} mV) must be >= {min_step:.2f} mV "
                "(smaller steps quantise to 0 and hang the firmware)."
            )
        if d.pulse_amplitude_mv <= 0 or mv_to_dpv_code(d.pulse_amplitude_mv) < 1:
            res.errors.append(
                f"DPV Pulse amplitude ({d.pulse_amplitude_mv} mV) must be >= {DPV_MIN_STEP_MV:.2f} mV."
            )
        amp_v = d.pulse_amplitude_mv / 1000.0
        if g.v_stop + amp_v > v_max + 1e-9:
            res.errors.append(
                f"DPV End E + Pulse amplitude ({g.v_stop + amp_v:.3f} V) exceeds +{v_max:.2f} V limit."
            )
        if g.v_start - amp_v < v_min - 1e-9:
            res.errors.append(
                f"DPV Start E - Pulse amplitude ({g.v_start - amp_v:.3f} V) is below {v_min:.2f} V limit."
            )
        if d.pulse_period_ms <= 0:
            res.errors.append("DPV Pulse period must be > 0 ms.")
        if d.pulse_width_ms <= 0:
            res.errors.append("DPV Pulse width must be > 0 ms.")
        if d.sampling_period_ms <= 0:
            res.errors.append("DPV Sampling period must be > 0 ms.")
        if d.cycles < 1:
            res.errors.append("DPV cycles must be >= 1.")

        if d.pulse_period_ms < d.pulse_width_ms + d.sampling_period_ms:
            res.warnings.append(
                "DPV Pulse period is less than Pulse width + Sampling period "
                "(causes unsigned timing underflow in firmware)."
            )
        if d.pulse_width_ms < d.sampling_period_ms:
            res.warnings.append(
                "DPV Pulse width is less than Sampling period "
                "(causes unsigned timing underflow in firmware)."
            )

    elif tech == TECH_CA:
        c = params.ca
        max_pts = min(CA_MAX_DATAPOINTS, cfg.max_ca_datapoints)
        if c.ca_time_s < 1:
            res.errors.append("CA time must be an integer >= 1 s.")
        if not (1 <= c.ca_datapoints <= max_pts):
            res.errors.append(f"CA datapoints ({c.ca_datapoints}) must be between 1 and {max_pts}.")
        if g.e_apply_after:
            res.warnings.append(
                "Firmware CA handler ignores After E; park_safe() will be used after completion."
            )
    else:
        res.errors.append(f"Unknown technique code: {tech}")

    return res


def format_compact_float(value: float, max_decimals: int = 4) -> str:
    """Format float compactly with at most max_decimals and no trailing zeros."""
    rounded = round(float(value), max_decimals)
    if abs(rounded) < 1e-12:
        return "0"
    text = f"{rounded:.{max_decimals}f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def build_p_command(params: ExperimentParams) -> str:
    """Build the ASCII 'P v1,v2,...,v18' command string (without trailing CRLF)."""
    g = params.general
    cv = params.cv
    dpv = params.dpv
    ca = params.ca

    v_start = clamp_potential(g.v_start)
    v_stop = v_start if params.technique == TECH_CA else clamp_potential(g.v_stop)
    v_eq = clamp_potential(g.v_eq)
    v_after = clamp_potential(g.v_after)

    cycles = dpv.cycles if params.technique == TECH_DPV else cv.cycles
    cycles = max(1, int(cycles))
    t_eq = max(0, int(round(g.t_eq)))
    ca_time = max(1, int(round(ca.ca_time_s)))
    ca_pts = max(1, min(CA_MAX_DATAPOINTS, int(round(ca.ca_datapoints))))

    fields = [
        format_compact_float(v_start, 4),
        format_compact_float(v_stop, 4),
        str(cycles),
        format_compact_float(cv.scan_rate, 4),
        format_compact_float(v_eq, 4),
        str(t_eq),
        format_compact_float(v_after, 4),
        "1" if g.e_apply_after else "0",
        "1" if cv.half_cycle else "0",
        str(ca_time),
        str(ca_pts),
        format_compact_float(dpv.step_e_mv, 4),
        format_compact_float(dpv.pulse_amplitude_mv, 4),
        str(max(1, int(round(dpv.pulse_period_ms)))),
        str(max(1, int(round(dpv.pulse_width_ms)))),
        str(max(1, int(round(dpv.sampling_period_ms)))),
        "1" if dpv.half_cycle else "0",
        "1" if g.no_e_readback else "0",
    ]
    cmd = "P " + ",".join(fields)
    assert len(cmd) <= MAX_P_COMMAND_LEN, f"P command too long ({len(cmd)} > {MAX_P_COMMAND_LEN}): {cmd}"
    return cmd


def build_park_p_command(v_park: float = 0.0) -> str:
    """Build the 'P ...' command used by park_safe() to set the DAC to v_park.

    Intentionally sets v_start == v_stop == v_park with half_cycle=True and e_apply_after=True
    (bypassing validate_params()) so the firmware 'S' handler executes a single 0-span DAC write
    at v_park without stepping away from the park potential.
    """
    vp = clamp_potential(v_park)
    park_params = ExperimentParams(
        technique=TECH_CV,
        general=GeneralParams(
            v_start=vp,
            v_stop=vp,
            v_eq=vp,
            t_eq=0,
            v_after=vp,
            e_apply_after=True,
            i_range_ua=100.0,
            no_e_readback=True,
        ),
        cv=CVParams(scan_rate=1.0, cycles=1, half_cycle=True),
    )
    return build_p_command(park_params)


def technique_start_command(technique: int) -> str:
    """Return 'S', 'D', or 'C' for the selected technique."""
    if technique == TECH_CV:
        return "S"
    if technique == TECH_DPV:
        return "D"
    if technique == TECH_CA:
        return "C"
    raise ValueError(f"Invalid technique code: {technique}")


def technique_end_marker(technique: int) -> str:
    """Return the technique-specific end marker ('@' for CV, '$' for DPV, '%' for CA)."""
    if technique == TECH_CV:
        return "@"
    if technique == TECH_DPV:
        return "$"
    if technique == TECH_CA:
        return "%"
    raise ValueError(f"Invalid technique code: {technique}")


def expected_run_duration_s(params: ExperimentParams) -> float:
    """Estimate active measurement duration (excluding 1 s readString delays)."""
    g = params.general
    t_eq = max(0, int(g.t_eq))
    if params.technique == TECH_CV:
        span_v = abs(real_potential_v(g.v_stop) - real_potential_v(g.v_start))
        srate = max(1e-4, float(params.cv.scan_rate))
        sweeps_per_cycle = 1 if params.cv.half_cycle else 2
        cycles = 1 if params.cv.half_cycle else max(1, int(params.cv.cycles))
        return t_eq + cycles * sweeps_per_cycle * (span_v / srate)
    if params.technique == TECH_DPV:
        d = params.dpv
        dac_span = max(1, abs(v_to_dac_code(g.v_stop) - v_to_dac_code(g.v_start)))
        i_step = max(1, mv_to_dpv_code(d.step_e_mv))
        steps_per_sweep = (dac_span // i_step) + 1
        sweeps_per_cycle = 1 if d.half_cycle else 2
        cycles = 1 if d.half_cycle else max(1, int(d.cycles))
        return t_eq + cycles * sweeps_per_cycle * steps_per_sweep * (d.pulse_period_ms / 1000.0)
    if params.technique == TECH_CA:
        return float(t_eq + max(1, int(params.ca.ca_time_s)))
    return float(t_eq)


def compute_read_timeout_s(params: ExperimentParams) -> float:
    """Compute serial read timeout: expected duration + 10 s safety margin."""
    return expected_run_duration_s(params) + RUN_TIMEOUT_MARGIN_S


@dataclass
class ParsedLine:
    """Parsed representation of a single line received from the device."""

    kind: str  # "data", "section", "end_cv", "end_dpv", "end_ca", "ack", "empty", "unknown"
    x: float = 0.0  # E_V for CV/DPV, t_s for CA
    y: float = 0.0  # I_uA or dI_uA
    saturated: bool = False
    raw: str = ""


def parse_device_line(line: str) -> ParsedLine:
    """Parse one line from the potentiostat, stripping CRLF and trailing comma."""
    cleaned = line.strip()
    if not cleaned:
        return ParsedLine(kind="empty", raw=line)
    if cleaned == "*":
        return ParsedLine(kind="section", raw=cleaned)
    if cleaned == "@":
        return ParsedLine(kind="end_cv", raw=cleaned)
    if cleaned == "$":
        return ParsedLine(kind="end_dpv", raw=cleaned)
    if cleaned == "%":
        return ParsedLine(kind="end_ca", raw=cleaned)
    if cleaned == "#":
        return ParsedLine(kind="ack", raw=cleaned)

    stripped_comma = cleaned.rstrip(",")
    parts = stripped_comma.split(",")
    if len(parts) == 2:
        try:
            x_val = float(parts[0].strip())
            y_val = float(parts[1].strip())
            saturated = abs(y_val) > CURRENT_SATURATION_WARN_UA
            return ParsedLine(
                kind="data",
                x=x_val,
                y=y_val,
                saturated=saturated,
                raw=cleaned,
            )
        except ValueError:
            pass

    return ParsedLine(kind="unknown", raw=cleaned)