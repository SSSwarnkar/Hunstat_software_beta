# hunstat/simulator.py
"""In-memory fake serial port implementing PoteVK-3.2.ino behaviour and cell physics."""

from __future__ import annotations

from collections import deque
import math
import random
import threading
import time

from hunstat.settings import (
    DAC_RESOLUTION_DIVISOR,
    DAC_ZERO_CODE,
    READSTRING_TIMEOUT_S,
    V_REF,
)


class SimulatedSerialPort:
    """Thread-safe pyserial-compatible fake serial port replicating PoteVK-3.2.ino.

    Supports two cell models:
    - 'fe_cn6': Reversible Fe(CN)6 3-/4- couple (E0 = 0.20 V) for CV, DPV peaks (Cd/Pb or redox),
                and CA Cottrell decay + baseline noise.
    - 'dummy_10k': Pure 10 kOhm resistor (I_uA = E_V * 100.0) for the self-test wizard.
    """

    def __init__(
        self,
        cell_mode: str = "fe_cn6",
        fast_mode: bool = False,
        noise_ua: float = 0.15,
        seed: int | None = 42,
    ) -> None:
        self.cell_mode = cell_mode
        self.fast_mode = fast_mode
        self.noise_ua = noise_ua
        self._rng = random.Random(seed)

        self.is_open: bool = True
        self.timeout: float = 0.1  # pyserial read timeout in seconds

        # Firmware state variables matching PoteVK-3.2.ino lines 1-47
        self.v_start: float = -1.20
        self.v_stop: float = 1.00
        self.V_start: int = 512 + int(-1.20 * 312)
        self.V_stop: int = 512 + int(1.00 * 312)
        self.cycle_number: int = 1
        self.srate: float = 0.100
        self.v_eq: float = 0.0
        self.teq: int = 0
        self.V_eq: int = 512
        self.v_after: float = 0.0
        self.V_after: int = 512
        self.e_apply_after: int = 0
        self.half_cycle: int = 0
        self.ca_time: int = 10
        self.ca_datapoints: int = 10
        self.step_e: float = 2.0
        self.i_step_e: int = 1
        self.pulse_amplitude: float = 10.0
        self.i_pulse_amplitude: int = 3
        self.i_pulse_period: int = 200
        self.i_pulse_width: int = 50
        self.i_sampling_period: int = 2
        self.half_cycle_dpv: int = 0
        self.no_e_readback: int = 0
        self.dac_volt: int = 512

        # Communication buffers & synchronisation
        self._rx_lines: deque[bytes] = deque()
        self._rx_partial: bytearray = bytearray()
        self._rx_cond = threading.Condition()
        self._cmd_queue: deque[str] = deque()
        self._in_active_run = threading.Event()
        self._abort_flag = threading.Event()
        self._unplugged = threading.Event()
        self._closed = threading.Event()

        self._worker_thread = threading.Thread(
            target=self._firmware_loop, name="HunStatSimFirmware", daemon=True
        )
        self._worker_thread.start()

    # --- Fault injection helpers for testing ---
    def simulate_unplug(self) -> None:
        """Simulate sudden USB cable disconnect during or between runs."""
        self._unplugged.set()
        self._abort_flag.set()
        with self._rx_cond:
            self._rx_cond.notify_all()

    # --- pyserial-compatible interface ---
    def write(self, data: bytes | bytearray) -> int:
        if not self.is_open or self._unplugged.is_set():
            raise OSError("Simulated serial port is disconnected or closed.")
        text = bytes(data).decode("ascii", errors="ignore").strip()
        if not text:
            return len(data)
        if text.startswith("!") and self._in_active_run.is_set():
            self._abort_flag.set()
            return len(data)
        self._cmd_queue.append(text)
        return len(data)

    def flush(self) -> None:
        if not self.is_open or self._unplugged.is_set():
            raise OSError("Simulated serial port is disconnected or closed.")

    def reset_input_buffer(self) -> None:
        with self._rx_cond:
            self._rx_lines.clear()
            self._rx_partial.clear()

    def reset_output_buffer(self) -> None:
        pass

    @property
    def in_waiting(self) -> int:
        if not self.is_open or self._unplugged.is_set():
            raise OSError("Simulated serial port is disconnected or closed.")
        with self._rx_cond:
            return sum(len(line) for line in self._rx_lines) + len(self._rx_partial)

    def readline(self) -> bytes:
        """Read one '\\r\\n'-terminated line or return b'' on timeout."""
        if not self.is_open or self._unplugged.is_set():
            raise OSError("Simulated serial port is disconnected or closed.")
        deadline = time.monotonic() + (self.timeout if self.timeout is not None else 1.0)
        with self._rx_cond:
            while not self._rx_lines:
                if not self.is_open or self._unplugged.is_set():
                    raise OSError("Simulated serial port disconnected during read.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                self._rx_cond.wait(timeout=min(remaining, 0.05))
            return self._rx_lines.popleft()

    def close(self) -> None:
        self.is_open = False
        self._closed.set()
        self._abort_flag.set()
        with self._rx_cond:
            self._rx_cond.notify_all()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)

    # --- Internal firmware emulation ---
    def _emit_line(self, text: str) -> None:
        if self._closed.is_set() or self._unplugged.is_set():
            return
        with self._rx_cond:
            self._rx_lines.append((text + "\r\n").encode("ascii"))
            self._rx_cond.notify_all()

    def _read_string_delay(self) -> None:
        """Emulate Serial.readString() 1 s timeout unless fast_mode is active."""
        delay_s = 0.002 if self.fast_mode else READSTRING_TIMEOUT_S
        end = time.monotonic() + delay_s
        while time.monotonic() < end and not self._closed.is_set():
            time.sleep(min(0.01, max(0.0, end - time.monotonic())))

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleep for `seconds` (scaled down in fast_mode), checking for '!' abort."""
        if seconds <= 0:
            return not self._abort_flag.is_set()
        actual_s = seconds * (0.02 if self.fast_mode else 1.0)
        end = time.monotonic() + actual_s
        while time.monotonic() < end:
            if self._closed.is_set() or self._unplugged.is_set():
                return False
            if self._cmd_queue and self._cmd_queue[0].startswith("!"):
                self._cmd_queue.popleft()
                self._abort_flag.set()
            if self._abort_flag.is_set():
                self._read_string_delay()
                return False
            time.sleep(min(0.005, max(0.0005, end - time.monotonic())))
        return not self._abort_flag.is_set()

    def _dac_to_v(self, dac_code: int) -> float:
        return (dac_code - DAC_ZERO_CODE) / DAC_RESOLUTION_DIVISOR * V_REF

    def _noise(self) -> float:
        return self._rng.gauss(0.0, self.noise_ua) if self.noise_ua > 0 else 0.0

    def _cv_current_ua(self, e_v: float, forward: bool) -> float:
        if self.cell_mode == "dummy_10k":
            return e_v * 100.0 + self._noise() * 0.2
        e_half = 0.20
        ep_anodic = e_half + 0.030
        ep_cathodic = e_half - 0.030
        sqrt_v = math.sqrt(max(0.003, self.srate))
        ip_scale = 260.0 * sqrt_v
        charging = 3.0 * self.srate * (1.0 if forward else -1.0) + 12.0 * (e_v - 0.1)

        if forward:
            dx = (e_v - ep_anodic) / 0.045
            if dx < 0:
                faradaic = ip_scale * math.exp(-0.5 * dx * dx)
            else:
                faradaic = ip_scale / (1.0 + 0.65 * dx + 0.15 * dx * dx)
            return charging + faradaic + self._noise()
        else:
            dx = (ep_cathodic - e_v) / 0.045
            if dx < 0:
                faradaic = -ip_scale * math.exp(-0.5 * dx * dx)
            else:
                faradaic = -ip_scale / (1.0 + 0.65 * dx + 0.15 * dx * dx)
            return charging + faradaic + self._noise()

    def _dpv_diff_current_ua(self, e_v: float, forward: bool) -> float:
        if self.cell_mode == "dummy_10k":
            amp_v = self._dac_to_v(DAC_ZERO_CODE + self.i_pulse_amplitude)
            sign = 1.0 if forward else -1.0
            return sign * amp_v * 100.0 + self._noise() * 0.1
        amp_factor = max(0.2, self.pulse_amplitude / 50.0)
        cd_peak = 22.0 * amp_factor * math.exp(-0.5 * ((e_v - (-0.82)) / 0.028) ** 2)
        pb_peak = 20.0 * amp_factor * math.exp(-0.5 * ((e_v - (-0.64)) / 0.028) ** 2)
        fe_peak = 35.0 * amp_factor * math.exp(-0.5 * ((e_v - 0.20) / 0.038) ** 2)
        baseline = 1.5 + 0.8 * (e_v + 0.7)
        sign = 1.0 if forward else -1.0
        return sign * (baseline + cd_peak + pb_peak + fe_peak) + self._noise()

    def _ca_current_ua(self, t_s: float) -> float:
        if self.cell_mode == "dummy_10k":
            v_applied = self._dac_to_v(self.V_eq if t_s <= self.teq else self.V_start)
            return v_applied * 100.0 + self._noise() * 0.2
        if t_s <= self.teq:
            return 0.2 * self.v_eq + self._noise()
        dt = max(0.05, t_s - self.teq)
        step_drive = max(0.1, self.v_start - self.v_eq)
        # Cottrell decay matched to paper Fig. 6a (~70 uA initial spike decaying to ~20 uA at 10 s)
        raw_i = (22.0 * step_drive / math.sqrt(dt)) + 11.5 + self._noise()
        return max(-160.0, min(160.0, raw_i))

    def _firmware_loop(self) -> None:
        while not self._closed.is_set():
            if not self._cmd_queue:
                time.sleep(0.005)
                continue

            cmd = self._cmd_queue.popleft()
            if cmd in ("S", "D", "C"):
                self._abort_flag.clear()
                self._in_active_run.set()
            self._read_string_delay()
            if self._closed.is_set() or self._unplugged.is_set():
                self._in_active_run.clear()
                continue

            ch = cmd[0]
            if ch == "P":
                self._handle_p(cmd)
                self._emit_line("#")
            elif ch == "S":
                try:
                    self._handle_s()
                finally:
                    self._in_active_run.clear()
                self._emit_line("#")
            elif ch == "D":
                try:
                    self._handle_d()
                finally:
                    self._in_active_run.clear()
                self._emit_line("#")
            elif ch == "C":
                try:
                    self._handle_c()
                finally:
                    self._in_active_run.clear()
                self._emit_line("#")
            elif ch == "!":
                self._abort_flag.clear()
                self._emit_line("#")
            else:
                self._emit_line("#")

    def _handle_p(self, cmd: str) -> None:
        """Replicate PoteVK-3.2.ino lines 64-152."""
        sub = cmd[2:100]
        parts = sub.split(",")
        if len(parts) < 18:
            parts = parts + ["0"] * (18 - len(parts))

        def _f(s: str) -> float:
            try:
                return float(s.strip())
            except ValueError:
                return 0.0

        def _i(s: str) -> int:
            try:
                return int(float(s.strip()))
            except ValueError:
                return 0

        self.v_start = _f(parts[0])
        self.v_stop = _f(parts[1])
        self.V_start = int(512 + self.v_start * 312)
        self.V_stop = int(512 + self.v_stop * 312)
        self.cycle_number = max(1, _i(parts[2]))
        self.srate = max(0.001, _f(parts[3]))
        self.v_eq = _f(parts[4])
        self.teq = max(0, _i(parts[5]))
        self.V_eq = int(512 + self.v_eq * 312)
        self.v_after = _f(parts[6])
        self.V_after = int(512 + self.v_after * 312)
        self.e_apply_after = _i(parts[7])
        self.half_cycle = _i(parts[8])
        self.ca_time = max(1, _i(parts[9]))
        self.ca_datapoints = max(1, min(999, _i(parts[10])))
        self.step_e = _f(parts[11])
        self.i_step_e = max(1, int((self.step_e / 3300.0 * 1024.0) + 0.5 + 1e-3))
        self.pulse_amplitude = _f(parts[12])
        self.i_pulse_amplitude = int((self.pulse_amplitude / 3300.0 * 1024.0) + 0.5 + 1e-3)
        self.i_pulse_period = max(1, _i(parts[13]))
        self.i_pulse_width = max(1, _i(parts[14]))
        self.i_sampling_period = max(1, _i(parts[15]))
        self.half_cycle_dpv = _i(parts[16])
        self.no_e_readback = _i(parts[17])

    def _handle_s(self) -> None:
        """Replicate CV/LSV handler (PoteVK-3.2.ino lines 155-290)."""
        cycles = 1 if self.half_cycle == 1 else self.cycle_number
        self.dac_volt = self.V_eq
        if not self._sleep_interruptible(float(self.teq)):
            self._emit_line("@")
            return

        step_dt = (V_REF / DAC_RESOLUTION_DIVISOR) / max(0.001, self.srate)
        stride = 4 if (self.fast_mode and (self.V_stop - self.V_start) > 40) else 1

        for _ in range(cycles):
            if self._abort_flag.is_set():
                break
            self._emit_line("*")

            # Forward scan
            dac = self.V_start
            while dac <= self.V_stop:
                if self._abort_flag.is_set():
                    break
                self.dac_volt = dac
                e_v = self._dac_to_v(dac)
                i_ua = self._cv_current_ua(e_v, forward=True)
                self._emit_line(f"{e_v:.4f},{i_ua:.4f},")
                if not self._sleep_interruptible(step_dt * stride):
                    break
                if dac < self.V_stop and dac + stride > self.V_stop:
                    dac = self.V_stop
                else:
                    dac += stride

            # Reverse scan
            if not self._abort_flag.is_set() and self.half_cycle == 0:
                dac = self.V_stop
                while dac >= self.V_start:
                    if self._abort_flag.is_set():
                        break
                    self.dac_volt = dac
                    e_v = self._dac_to_v(dac)
                    i_ua = self._cv_current_ua(e_v, forward=False)
                    self._emit_line(f"{e_v:.4f},{i_ua:.4f},")
                    if not self._sleep_interruptible(step_dt * stride):
                        break
                    if dac > self.V_start and dac - stride < self.V_start:
                        dac = self.V_start
                    else:
                        dac -= stride

        if not self._abort_flag.is_set() and self.e_apply_after == 1:
            self.dac_volt = self.V_after
        self._emit_line("@")

    def _handle_d(self) -> None:
        """Replicate DPV handler (PoteVK-3.2.ino lines 379-656)."""
        cycles = 1 if self.half_cycle_dpv == 1 else self.cycle_number
        self.dac_volt = self.V_eq
        if not self._sleep_interruptible(float(self.teq)):
            self._emit_line("$")
            return

        step_dt = self.i_pulse_period / 1000.0
        for _ in range(cycles):
            if self._abort_flag.is_set():
                break
            self._emit_line("*")

            # Forward scan
            dac = self.V_start
            while dac <= self.V_stop:
                if not self._sleep_interruptible(step_dt):
                    break
                self.dac_volt = dac
                e_v = self._dac_to_v(dac)
                di_ua = self._dpv_diff_current_ua(e_v, forward=True)
                self._emit_line(f"{e_v:.4f},{di_ua:.2f},")
                dac += self.i_step_e

            # Reverse scan
            if not self._abort_flag.is_set() and self.half_cycle_dpv == 0:
                dac = self.V_stop
                while dac >= self.V_start:
                    if not self._sleep_interruptible(step_dt):
                        break
                    self.dac_volt = dac
                    e_v = self._dac_to_v(dac)
                    di_ua = self._dpv_diff_current_ua(e_v, forward=False)
                    self._emit_line(f"{e_v:.4f},{di_ua:.2f},")
                    dac -= self.i_step_e

        if not self._abort_flag.is_set() and self.e_apply_after == 1:
            self.dac_volt = self.V_after
        self._emit_line("$")

    def _handle_c(self) -> None:
        """Replicate CA handler (PoteVK-3.2.ino lines 294-376)."""
        total_time = float(self.teq + self.ca_time)
        n_pts = max(1, min(999, self.ca_datapoints))
        dt = total_time / n_pts

        t_data: list[float] = []
        i_data: list[float] = []

        self.dac_volt = self.V_eq
        for pt_idx in range(1, n_pts + 1):
            t_target = pt_idx * dt
            if t_target > self.teq and self.dac_volt != self.V_start:
                self.dac_volt = self.V_start
            if not self._sleep_interruptible(dt):
                break
            t_data.append(t_target)
            i_data.append(self._ca_current_ua(t_target))

        for t_val, i_val in zip(t_data, i_data):
            self._emit_line(f"{t_val:.3f},{i_val:.3f},")
        self._emit_line("*")
        self._emit_line("%")