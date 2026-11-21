# hunstat/settings.py
"""Hardware constants, validated paper defaults, and persistent user settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

APP_NAME = "HunStat"
APP_VERSION = "1.0.0"

# USB Serial parameters (PoteVK-3.2.ino line 54)
BAUD_RATE = 115200
LINE_ENDING = "\r\n"
READSTRING_TIMEOUT_S = 1.0  # Serial.readString() default timeout on Arduino
PROBE_TIMEOUT_S = 2.0
RUN_TIMEOUT_MARGIN_S = 10.0
MAX_P_COMMAND_LEN = 98  # Firmware reads substring(2, 100); keep total len <= 98

# Seeeduino XIAO SAMD21 USB identifiers
SEEED_VID = 0x2886
SIMULATOR_PORT_FIRST = "Simulator (Fe(CN)6 3-/4-)"
SIMULATOR_PORT_DUMMY = "Simulator (10 kOhm Dummy Cell)"
SIMULATOR_PORTS = [SIMULATOR_PORT_FIRST, SIMULATOR_PORT_DUMMY]

# DAC / ADC conversion constants from PoteVK-3.2.ino
DAC_BITS = 10
DAC_MAX_CODE = 1023
DAC_RESOLUTION_DIVISOR = 1024
DAC_ZERO_CODE = 512
DAC_SCALE_PER_V = 312  # V_start = 512 + Vstart * 312 (lines 43-44, 122-123)
V_REF = 3.3

ADC_BITS = 12
ADC_RESOLUTION_DIVISOR = 4096
V_CENTER = 1.65
TIA_GAIN_UA_PER_V = 100.0  # 10 kOhm feedback resistor -> 100 uA/V (line 201)

# Hardware safety limits
HARD_V_MIN = -1.60  # Hard-clamp potentials to avoid 10-bit DAC wraparound near +/-1.64 V
HARD_V_MAX = 1.60
ADC_MAX_ABS_CURRENT_UA = 165.0
CURRENT_SATURATION_WARN_UA = 160.0

SCAN_RATE_MIN_VS = 0.003
SCAN_RATE_MAX_VS = 2.0
SCAN_RATE_WARN_VS = 1.0

DPV_MIN_STEP_MV = 1.61  # Below 1.61 mV, int(StepE/3300*1024 + 0.5) == 0 -> infinite loop
DPV_QUANT_STEP_MV = 3300.0 / 1024.0  # ~3.22265625 mV per DAC LSB

CA_MAX_DATAPOINTS = 999  # Firmware arrays CAIData[1000] / CAtData[1000] index 1..N
FIRMWARE_ARRAY_CAPACITY = 1000

DEFAULT_PARK_V = 0.0

# Technique integer codes (matching legacy TSV Technique header)
TECH_CV = 0
TECH_DPV = 1
TECH_CA = 2


@dataclass
class GeneralParams:
    """Electrochemical parameters shared across techniques."""

    v_start: float = -0.2
    v_stop: float = 0.6
    v_eq: float = -0.2
    t_eq: int = 2
    v_after: float = 0.0
    e_apply_after: bool = False
    i_range_ua: float = 100.0
    no_e_readback: bool = True


@dataclass
class CVParams:
    """Parameters specific to Cyclic / Linear Sweep Voltammetry."""

    scan_rate: float = 0.05
    cycles: int = 1
    half_cycle: bool = False  # True = LSV


@dataclass
class DPVParams:
    """Parameters specific to Differential Pulse Voltammetry."""

    step_e_mv: float = 3.0
    pulse_amplitude_mv: float = 50.0
    pulse_period_ms: int = 100
    pulse_width_ms: int = 50
    sampling_period_ms: int = 3
    half_cycle: bool = True
    cycles: int = 1


@dataclass
class CAParams:
    """Parameters specific to Chronoamperometry."""

    ca_time_s: int = 10
    ca_datapoints: int = 200


@dataclass
class ExperimentParams:
    """Complete parameter bundle for a single run."""

    technique: int = TECH_CV  # 0=CV/LSV, 1=DPV, 2=CA
    general: GeneralParams = field(default_factory=GeneralParams)
    cv: CVParams = field(default_factory=CVParams)
    dpv: DPVParams = field(default_factory=DPVParams)
    ca: CAParams = field(default_factory=CAParams)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentParams:
        gen = GeneralParams(**data.get("general", {}))
        cv = CVParams(**data.get("cv", {}))
        dpv = DPVParams(**data.get("dpv", {}))
        ca = CAParams(**data.get("ca", {}))
        return cls(
            technique=int(data.get("technique", TECH_CV)),
            general=gen,
            cv=cv,
            dpv=dpv,
            ca=ca,
        )


def make_paper_defaults(preset: str = "CV") -> ExperimentParams:
    """Return validated experiment defaults from Vamos & Kertesz (2024)."""
    key = preset.upper()
    if key == "CV":
        return ExperimentParams(
            technique=TECH_CV,
            general=GeneralParams(
                v_start=-0.2, v_stop=0.6, v_eq=-0.2, t_eq=2,
                v_after=0.0, e_apply_after=False, i_range_ua=100.0, no_e_readback=True,
            ),
            cv=CVParams(scan_rate=0.05, cycles=1, half_cycle=False),
        )
    if key == "LSV":
        return ExperimentParams(
            technique=TECH_CV,
            general=GeneralParams(
                v_start=-0.2, v_stop=0.55, v_eq=-0.2, t_eq=1,
                v_after=0.0, e_apply_after=False, i_range_ua=150.0, no_e_readback=True,
            ),
            cv=CVParams(scan_rate=0.1, cycles=1, half_cycle=True),
        )
    if key == "DPV":
        return ExperimentParams(
            technique=TECH_DPV,
            general=GeneralParams(
                v_start=-1.0, v_stop=-0.4, v_eq=-1.1, t_eq=2,
                v_after=0.0, e_apply_after=False, i_range_ua=50.0, no_e_readback=True,
            ),
            dpv=DPVParams(
                step_e_mv=3.0, pulse_amplitude_mv=50.0, pulse_period_ms=100,
                pulse_width_ms=50, sampling_period_ms=3, half_cycle=True, cycles=1,
            ),
        )
    if key == "CA":
        return ExperimentParams(
            technique=TECH_CA,
            general=GeneralParams(
                v_start=0.8, v_stop=0.8, v_eq=0.0, t_eq=1,
                v_after=0.0, e_apply_after=False, i_range_ua=60.0, no_e_readback=True,
            ),
            ca=CAParams(ca_time_s=10, ca_datapoints=200),
        )
    if key == "DUMMY":
        return ExperimentParams(
            technique=TECH_CV,
            general=GeneralParams(
                v_start=0.0, v_stop=1.0, v_eq=0.0, t_eq=0,
                v_after=0.0, e_apply_after=False, i_range_ua=120.0, no_e_readback=True,
            ),
            cv=CVParams(scan_rate=0.1, cycles=1, half_cycle=True),
        )
    raise ValueError(f"Unknown preset: {preset}")


@dataclass
class AppSettings:
    """User-configurable instrument and UI settings persisted to JSON."""

    serial_port: str = SIMULATOR_PORT_FIRST
    v_min: float = HARD_V_MIN
    v_max: float = HARD_V_MAX
    scan_rate_min: float = SCAN_RATE_MIN_VS
    scan_rate_max: float = SCAN_RATE_MAX_VS
    dpv_min_step_mv: float = DPV_MIN_STEP_MV
    max_ca_datapoints: int = CA_MAX_DATAPOINTS
    park_potential_v: float = DEFAULT_PARK_V
    dark_theme: bool = False
    log_visible: bool = True
    last_directory: str = ""
    last_params: dict[str, Any] = field(
        default_factory=lambda: make_paper_defaults("CV").to_dict()
    )

    @staticmethod
    def default_config_path() -> Path:
        return Path.home() / ".hunstat_settings.json"

    def save(self, path: Path | None = None) -> None:
        target = path or self.default_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)

    @classmethod
    def load(cls, path: Path | None = None) -> AppSettings:
        target = path or cls.default_config_path()
        if not target.exists():
            return cls()
        try:
            with open(target, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            valid_keys = {f for f in cls.__dataclass_fields__}
            filtered = {k: v for k, v in raw.items() if k in valid_keys}
            settings = cls(**filtered)
            # Enforce hard safety bounds even if JSON was hand-edited
            settings.v_min = max(HARD_V_MIN, float(settings.v_min))
            settings.v_max = min(HARD_V_MAX, float(settings.v_max))
            settings.dpv_min_step_mv = max(DPV_MIN_STEP_MV, float(settings.dpv_min_step_mv))
            settings.max_ca_datapoints = min(CA_MAX_DATAPOINTS, int(settings.max_ca_datapoints))
            settings.park_potential_v = max(
                HARD_V_MIN, min(HARD_V_MAX, float(settings.park_potential_v))
            )
            return settings
        except Exception:
            return cls()