# hunstat/worker.py
"""Serial port auto-detection, liveness probing, park_safe(), and background run worker."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable

from hunstat.protocol import (
    build_p_command,
    build_park_p_command,
    compute_read_timeout_s,
    expected_run_duration_s,
    parse_device_line,
    technique_end_marker,
    technique_start_command,
    validate_params,
)
from hunstat.settings import (
    BAUD_RATE,
    DEFAULT_PARK_V,
    LINE_ENDING,
    PROBE_TIMEOUT_S,
    SEEED_VID,
    SIMULATOR_PORT_DUMMY,
    SIMULATOR_PORT_FIRST,
    SIMULATOR_PORTS,
    TECH_CA,
    ExperimentParams,
)
from hunstat.simulator import SimulatedSerialPort

try:
    import serial  # type: ignore[import-untyped]
    import serial.tools.list_ports  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    serial = None  # type: ignore[assignment]

try:
    from PySide6.QtCore import QCoreApplication, QThread, Signal  # type: ignore[import-not-found]
    _HAS_PYSIDE6 = True
except ImportError:  # pragma: no cover
    QCoreApplication = None  # type: ignore[assignment]
    _HAS_PYSIDE6 = False


class _SignalProxy:
    """Delivers signals synchronously in CLI/pytest when no Qt event loop is running,
    and via standard Qt QueuedConnection when QCoreApplication is active."""

    def __init__(self, qt_signal: Any = None) -> None:
        self._qt_signal = qt_signal
        self._direct_callbacks: list[Callable[..., Any]] = []

    def connect(self, cb: Callable[..., Any]) -> None:
        self._direct_callbacks.append(cb)
        if self._qt_signal is not None:
            try:
                self._qt_signal.connect(cb)
            except Exception:
                pass

    def emit(self, *args: Any) -> None:
        if (
            self._qt_signal is not None
            and QCoreApplication is not None
            and QCoreApplication.instance() is not None
        ):
            self._qt_signal.emit(*args)
        else:
            for cb in list(self._direct_callbacks):
                cb(*args)


if _HAS_PYSIDE6:
    _BaseThread = QThread
else:
    class _BaseThread:  # type: ignore[no-redef]
        """Fallback thread wrapper with QThread-compatible start/wait API."""

        def __init__(self, parent: Any = None) -> None:
            self._thread: threading.Thread | None = None

        def run(self) -> None:
            pass

        def start(self) -> None:
            self._thread = threading.Thread(target=self.run, daemon=True)
            self._thread.start()

        def wait(self, msecs: int | None = None) -> bool:
            if self._thread is not None:
                timeout = (msecs / 1000.0) if msecs is not None else None
                self._thread.join(timeout=timeout)
                return not self._thread.is_alive()
            return True

        def isRunning(self) -> bool:
            return self._thread is not None and self._thread.is_alive()


@dataclass
class PortInfo:
    """Metadata for a discovered serial or simulator port."""

    device: str
    description: str
    is_seeed: bool = False
    is_simulator: bool = False


def list_candidate_ports(include_simulator: bool = True) -> list[PortInfo]:
    """List available serial ports sorted with Seeeduino XIAO (VID 0x2886) first."""
    hardware_ports: list[PortInfo] = []
    if serial is not None:
        for p in serial.tools.list_ports.comports():
            is_seeed = (getattr(p, "vid", None) == SEEED_VID) or (
                "2886" in (getattr(p, "hwid", "") or "").upper()
            )
            desc = getattr(p, "description", "") or p.device
            hardware_ports.append(
                PortInfo(device=p.device, description=desc, is_seeed=is_seeed, is_simulator=False)
            )
    hardware_ports.sort(key=lambda item: (not item.is_seeed, item.device))

    if include_simulator:
        for sim_name in SIMULATOR_PORTS:
            hardware_ports.append(
                PortInfo(device=sim_name, description=sim_name, is_seeed=False, is_simulator=True)
            )
    return hardware_ports


def open_serial_connection(
    port_name: str,
    timeout: float = 0.2,
    fast_sim: bool = False,
) -> Any:
    """Open either a SimulatedSerialPort or a real pyserial Serial instance."""
    if port_name == SIMULATOR_PORT_DUMMY:
        return SimulatedSerialPort(cell_mode="dummy_10k", fast_mode=fast_sim)
    if port_name in SIMULATOR_PORTS or port_name.lower().startswith("simulator"):
        return SimulatedSerialPort(cell_mode="fe_cn6", fast_mode=fast_sim)
    if serial is None:
        raise RuntimeError("pyserial is not installed; only Simulator ports are available.")
    return serial.Serial(
        port=port_name,
        baudrate=BAUD_RATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout,
        write_timeout=2.0,
    )


def probe_port(
    port: str | Any,
    timeout_s: float = PROBE_TIMEOUT_S,
    fast_sim: bool = False,
) -> bool:
    """Probe a port with '!' and verify '#' arrives within timeout_s (2.0 s default)."""
    owns_port = isinstance(port, str)
    conn = None
    try:
        conn = open_serial_connection(port, timeout=0.1, fast_sim=fast_sim) if owns_port else port
        conn.reset_input_buffer()
        conn.write(("!" + LINE_ENDING).encode("ascii"))
        conn.flush()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = conn.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="ignore").strip()
            if line == "#":
                return True
        return False
    except Exception:
        return False
    finally:
        if owns_port and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def auto_detect_port(include_simulator_fallback: bool = True) -> str | None:
    """Auto-detect the HunStat port: try Seeed VID 0x2886 first, then other COM ports."""
    candidates = list_candidate_ports(include_simulator=False)
    for info in candidates:
        if probe_port(info.device, timeout_s=PROBE_TIMEOUT_S):
            return info.device
    if include_simulator_fallback:
        return SIMULATOR_PORT_FIRST
    return None


def park_safe(
    conn: Any,
    v_park: float = DEFAULT_PARK_V,
    log_cb: Callable[[str], None] | None = None,
    timeout_s: float = 4.0,
) -> bool:
    """Safely park the working electrode potential at v_park using a 1-point LSV with After-E."""
    def _log(msg: str) -> None:
        if log_cb is not None:
            log_cb(msg)

    try:
        conn.reset_input_buffer()
        p_cmd = build_park_p_command(v_park)
        _log(f"TX [park]: {p_cmd}")
        conn.write((p_cmd + LINE_ENDING).encode("ascii"))
        conn.flush()

        deadline = time.monotonic() + timeout_s
        got_p_ack = False
        while time.monotonic() < deadline:
            raw = conn.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="ignore").strip()
            if line:
                _log(f"RX [park]: {line}")
            if line == "#":
                got_p_ack = True
                break
        if not got_p_ack:
            return False

        _log("TX [park]: S")
        conn.write(("S" + LINE_ENDING).encode("ascii"))
        conn.flush()

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = conn.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="ignore").strip()
            if line:
                _log(f"RX [park]: {line}")
            if line == "#":
                return True
        return False
    except Exception as exc:
        _log(f"WARN [park]: park_safe failed ({exc})")
        return False


class SerialRunWorker(_BaseThread):
    """Background worker executing a single electrochemical run without blocking the GUI.

    Emits:
    - point(int cycle, float x, float y, bool saturated)
    - section_started(int cycle)
    - status_changed(str status_text)
    - progress(float elapsed_s, float total_s)
    - finished(bool aborted)
    - error(str message)
    - log_line(str text)
    """

    if _HAS_PYSIDE6:
        _qt_point = Signal(int, float, float, bool)
        _qt_section_started = Signal(int)
        _qt_status_changed = Signal(str)
        _qt_progress = Signal(float, float)
        _qt_finished = Signal(bool)
        _qt_error = Signal(str)
        _qt_log_line = Signal(str)

    def __init__(
        self,
        port: str | Any,
        params: ExperimentParams,
        v_park: float = DEFAULT_PARK_V,
        fast_sim: bool = False,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.point = _SignalProxy(getattr(self, "_qt_point", None))
        self.section_started = _SignalProxy(getattr(self, "_qt_section_started", None))
        self.status_changed = _SignalProxy(getattr(self, "_qt_status_changed", None))
        self.progress = _SignalProxy(getattr(self, "_qt_progress", None))
        self.finished = _SignalProxy(getattr(self, "_qt_finished", None))
        self.error = _SignalProxy(getattr(self, "_qt_error", None))
        self.log_line = _SignalProxy(getattr(self, "_qt_log_line", None))

        self._port_arg = port
        self.params = params
        self.v_park = v_park
        self.fast_sim = fast_sim

        self._abort_requested = threading.Event()
        self._conn: Any = None
        self._owns_conn = isinstance(port, str)

    def request_abort(self) -> None:
        """Signal the worker to send '!' immediately on its serial connection."""
        self._abort_requested.set()

    def _emit_log(self, msg: str) -> None:
        self.log_line.emit(msg)

    def _wait_for_hash(self, conn: Any, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = conn.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="ignore").strip()
            if line:
                self._emit_log(f"RX: {line}")
            if line == "#":
                return True
        return False

    def run(self) -> None:
        val = validate_params(self.params)
        if not val.ok:
            self.status_changed.emit("Error")
            self.error.emit("; ".join(val.errors))
            return

        conn = None
        need_park = False
        aborted = False

        try:
            conn = (
                open_serial_connection(self._port_arg, timeout=0.1, fast_sim=self.fast_sim)
                if self._owns_conn
                else self._port_arg
            )
            self._conn = conn
            conn.reset_input_buffer()

            # 1. Send P command and wait for '#'
            self.status_changed.emit("Sending parameters...")
            p_cmd = build_p_command(self.params)
            self._emit_log(f"TX: {p_cmd}")
            conn.write((p_cmd + LINE_ENDING).encode("ascii"))
            conn.flush()

            if not self._wait_for_hash(conn, timeout_s=4.0):
                raise TimeoutError("Timed out waiting for '#' acknowledgement after P command.")

            need_park = (not self.params.general.e_apply_after) or (
                self.params.technique == TECH_CA
            )

            if self._abort_requested.is_set():
                aborted = True
                park_safe(conn, self.v_park, log_cb=self._emit_log)
                self.status_changed.emit("Idle")
                self.finished.emit(True)
                return

            # 2. Send S / D / C command
            start_cmd = technique_start_command(self.params.technique)
            end_marker = technique_end_marker(self.params.technique)
            self._emit_log(f"TX: {start_cmd}")
            conn.write((start_cmd + LINE_ENDING).encode("ascii"))
            conn.flush()

            t_eq = float(self.params.general.t_eq)
            total_expected = expected_run_duration_s(self.params)
            read_timeout = compute_read_timeout_s(self.params)

            cmd_accept_s = 0.01 if self.fast_sim else 1.0
            run_start = time.monotonic()
            deadline = run_start + read_timeout + cmd_accept_s

            current_cycle = 0
            sent_abort_byte = False
            got_end_marker = False
            got_final_hash = False
            in_equilibration = t_eq > 0
            if in_equilibration:
                self.status_changed.emit("Equilibrating")
            else:
                self.status_changed.emit("Running")

            while time.monotonic() < deadline:
                if self._abort_requested.is_set() and not sent_abort_byte:
                    aborted = True
                    need_park = True
                    sent_abort_byte = True
                    self.status_changed.emit("Aborting...")
                    self._emit_log("TX: !")
                    conn.write(("!" + LINE_ENDING).encode("ascii"))
                    conn.flush()
                    deadline = max(deadline, time.monotonic() + 5.0)

                elapsed = max(0.0, time.monotonic() - run_start - cmd_accept_s)
                if in_equilibration and elapsed >= t_eq and not sent_abort_byte:
                    in_equilibration = False
                    self.status_changed.emit("Running")
                self.progress.emit(min(elapsed, total_expected), total_expected)

                raw = conn.readline()
                if not raw:
                    continue

                line_str = raw.decode("ascii", errors="ignore").strip()
                if not line_str:
                    continue
                self._emit_log(f"RX: {line_str}")

                parsed = parse_device_line(line_str)
                if parsed.kind == "section":
                    in_equilibration = False
                    if not sent_abort_byte:
                        self.status_changed.emit("Running")
                    if self.params.technique != TECH_CA:
                        current_cycle += 1
                        self.section_started.emit(current_cycle)
                    elif current_cycle == 0:
                        current_cycle = 1
                        self.section_started.emit(1)
                elif parsed.kind == "data":
                    if current_cycle == 0:
                        current_cycle = 1
                        self.section_started.emit(1)
                    self.point.emit(current_cycle, parsed.x, parsed.y, parsed.saturated)
                elif parsed.raw == end_marker:
                    got_end_marker = True
                elif parsed.kind == "ack" and got_end_marker:
                    got_final_hash = True
                    break

            if not got_final_hash:
                raise TimeoutError(
                    f"Device timed out before completing run (end_marker={got_end_marker})."
                )

            if need_park:
                park_safe(conn, self.v_park, log_cb=self._emit_log)

            self.progress.emit(total_expected, total_expected)
            self.status_changed.emit("Idle" if aborted else "Done")
            self.finished.emit(aborted)

        except Exception as exc:
            if conn is not None:
                try:
                    park_safe(conn, self.v_park, log_cb=self._emit_log, timeout_s=2.0)
                except Exception:
                    pass
            self.status_changed.emit("Error")
            self.error.emit(str(exc))
        finally:
            if self._owns_conn and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            self._conn = None