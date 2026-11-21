<!-- README.md -->
# HunStat — DIY Potentiostat Control & Analysis Software

Desktop control, simulation, and electrochemistry analysis suite for the **HunStat** Seeeduino XIAO SAMD21 potentiostat (`PoteVK-3.2.ino`).

## Key Features

- **Techniques Supported**:
  - **CV** (Cyclic Voltammetry) & **LSV** (Linear Sweep Voltammetry, `half_cycle = True`)
  - **DPV** (Differential Pulse Voltammetry)
  - **CA** (Chronoamperometry)
- **Hardware Safety & Protocol Invariants**:
  - Non-blocking background `SerialRunWorker` thread (`115200` baud, CRLF `\r\n`).
  - Sends `P v1,...,v18` (`<= 98` chars), waits for `#`, then sends `S`, `D`, or `C`; sends only `!` during an active run to abort.
  - Enforces potential clamping within `[-1.60, +1.60] V`, `Start E < End E`, `DPV Step E >= 1.61 mV`, and `CA datapoints <= 999`.
  - Automatically parks the working electrode via `park_safe()` after aborts, serial errors, CA runs, or runs without `After E`.
- **Built-In Hardware Simulator**:
  - `Simulator (Fe(CN)6 3-/4-)`: Reversible redox couple ($E_{1/2} = 0.20\text{ V}$), DPV trace-metal peaks ($\text{Cd}^{2+}, \text{Pb}^{2+}$), and CA Cottrell decay.
  - `Simulator (10 kOhm Dummy Cell)`: Pure $10\text{ k}\Omega$ Ohmic responder ($I = 100\text{ }\mu\text{A/V}$) for self-test verification.
- **File I/O & Legacy Support**:
  - Saves runs as `.csv` (`cycle,E_V,I_uA` for CV/LSV/DPV; `t_s,t_step_s,I_uA` for CA) plus a `.json` metadata sidecar.
  - Opens legacy HunStat `Version\t2` `.tsv` files (single- and multi-cycle) as read-only inputs and batch-converts `.tsv` files/folders to `.csv + .json`.
  - Exports publication-ready plots to `.png` and vector `.svg` (`QSvgGenerator`).
- **Integrated Electrochemistry Analysis Dock & Self-Test Wizard**:
  - Two-point linear baseline subtraction and peak picking (`Ep`, `Ip` height above baseline).
  - Automated CV metrics table ($E_{pa}, E_{pc}, \Delta E_p, I_{pa}, I_{pc}, |I_{pa}/I_{pc}|, E_{1/2}$).
  - Randles–Ševčík linear regression ($I_p$ vs. $v^{1/2}$).
  - Linear calibration curve with LOD ($3\sigma_{\text{blank}} / \text{slope}$) and unknown solver.
  - Standard addition solver ($x$-intercept and dilution-corrected concentration).
  - Diode turn-on voltage detector and CA post-step current interpolator $I(t_{\text{step}} + t_{\text{query}})$.
  - Guided **10 kΩ Dummy-Cell Self-Test Wizard** (`Analysis -> 10 kΩ Dummy-Cell Self-Test Wizard...`).

## Quick Start

```bash
pip install -r requirements.txt
pytest -v
python main.py