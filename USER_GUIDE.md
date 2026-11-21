<!-- USER_GUIDE.md -->
# HunStat User Guide & Worked Examples

This guide walks through each electrochemical technique, analysis workflow, and hardware bring-up step supported by HunStat, using both the built-in simulator and real hardware.

## 1. Hardware Bring-Up Checklist

1. **Firmware Check**: Flash `PoteVK-3.2.ino` onto the Seeeduino XIAO SAMD21 using the Arduino IDE (`Seeeduino XIAO` board profile).
2. **USB Connection**: Connect the Seeeduino XIAO via a data-capable USB-C cable and confirm a serial port with USB VID `0x2886` appears in Windows Device Manager.
3. **Port Liveness Probe**: Launch HunStat, click **Settings** on the toolbar, select the Seeeduino COM port, and click **Probe Port**—confirm it reports `"Device responded with '#'"` within 2 seconds.
4. **10 kOhm Dummy-Cell Wiring**:
   - Clip **RE** (Reference Electrode) and **CE** (Counter Electrode) leads together.
   - Connect a **10 kOhm (+/-1%) resistor** between the joined **RE+CE** clips and the **WE** (Working Electrode) clip.
5. **Dummy-Cell Self-Test**:
   - Open **Analysis -> 10 kOhm Dummy-Cell Self-Test Wizard...**, select the hardware COM port, and click **Run Self-Test (LSV 0 -> 1 V @ 0.1 V/s)**.
   - Confirm the status shows **PASS**, measured resistance is within **9.0 to 11.0 kOhm**, current at `1.0 V` is **~90 to 110 uA**, `R^2 >= 0.99`, and no saturation warning (`|I| > 160 uA`) is shown.
6. **Abort & Safe Parking Check**:
   - Start a slow CV sweep (`0.02 V/s`) and click **Abort** mid-sweep.
   - Confirm the status bar returns to **Idle** and the Serial Log shows `TX: !` followed by `TX [park]: P ...` and `TX [park]: S`.

## 2. Worked Example 1 — 10 kOhm Dummy-Cell Self-Test (LSV)

**Goal:** Verify transimpedance amplifier (TIA) gain (`10 kOhm => 100 uA/V`) and DAC linearity before running wet-chemistry experiments.

1. **Wiring (Real Hardware):** Join **RE** and **CE** together and connect a `10 kOhm` resistor between **RE+CE** and **WE**. (For simulation, no hardware is needed.)
2. Open **Analysis -> 10 kOhm Dummy-Cell Self-Test Wizard...**.
3. Select your hardware COM port or `Simulator (10 kOhm Dummy Cell)`.
4. Click **Run Self-Test (LSV 0 -> 1 V @ 0.1 V/s)**.
5. **Expected Readout:**
   - Linear I-V trace from `(0.00 V, 0.0 uA)` to `(1.00 V, ~100.0 uA)`.
   - **Measured Resistance:** `10.000 kOhm` (PASS window: `9.000` to `11.000 kOhm`).
   - **Current at 1.0 V:** `~100.00 uA`.
   - **Test Status:** `PASS — Dummy cell within 10% of 10.0 kOhm`.
   - If a resistor `< 6.25 kOhm` is connected, `|I|` exceeds `160 uA` and the red **TIA saturation warning** banner appears.

## 3. Worked Example 2 — Cyclic Voltammetry (CV) & Randles-Sevcik Analysis

**Goal:** Characterize the reversible `Fe(CN)6 3-/4-` redox couple across scan rates and verify diffusion control (`Ip` proportional to `sqrt(v)`).

1. In **Settings**, select `Simulator (Fe(CN)6 3-/4-)` (or connect a 3-electrode cell in `5 mM K3[Fe(CN)6] / K4[Fe(CN)6]` in `0.1 M KCl`).
2. In the **Quick Preset** dropdown, select **CV (Paper Default)**:
   - `Start E = -0.200 V`, `End E = +0.600 V`, `Equilibration E = -0.200 V`, `t_eq = 2 s`
   - `Scan rate = 0.050 V/s`, `Cycles = 2`, `Half-cycle (LSV) = unchecked`
3. Click **Run** on the toolbar and wait for Cycles 1 and 2 to finish.
4. Open the **Analysis Tools** dock -> **CV Metrics** tab and click **Compute CV Metrics for Selected Dataset**:
   - Inspect `Epa ~ +0.23 V`, `Epc ~ +0.17 V`, `delta_Ep ~ 60 mV`, `|Ipa/Ipc| ~ 1.0`, and `E1/2 ~ +0.20 V`.
5. Repeat the CV run at `Scan rate = 0.025 V/s`, `0.100 V/s`, and `0.200 V/s`.
6. Switch to the **Randles-Sevcik** tab in the Analysis dock:
   - Click **Refresh Loaded Files** to populate the table with each dataset's scan rate `v (V/s)` and anodic peak current `Ip (uA)`.
   - Click **Fit Ip vs sqrt(v)** to plot `Ip` vs. `sqrt(v)` and inspect the linear regression slope, intercept, and `R^2 > 0.99`.

## 4. Worked Example 3 — Linear Sweep Voltammetry (LSV), Baseline/Peak Picking, Calibration & Diode Turn-On

**Goal:** Record a single forward oxidation sweep (`half_cycle = True`), subtract a two-point linear baseline, solve an unknown ascorbic acid concentration from the paper calibration curve, and measure diode turn-on voltage.

1. Select **LSV (Paper Default)** from the **Quick Preset** dropdown:
   - `Start E = -0.200 V`, `End E = +0.550 V`, `Scan rate = 0.100 V/s`, `Half-cycle (LSV) = checked`.
2. Click **Run** to record a single forward sweep.
3. In the **Analysis Tools** dock -> **Baseline & Peak** tab:
   - Click **Click 2 Baseline Pts on Plot**, then click two points along the pre-peak baseline region (e.g., `-0.18 V` and `-0.02 V`), or enter `Baseline E1` and `Baseline E2` directly and click **Find Peak**.
   - HunStat draws the dashed baseline, marks the peak with a red symbol and vertical drop-line, and updates the main window's **I peak, uA** readout with the baseline-subtracted peak height `Ip`.
4. Switch to the **Calibration** tab:
   - Click **Load Paper Ascorbic Acid** (standards from `0.50` to `5.00 mM` following `I = 5.865*c + 4.06 uA`).
   - Set `Unknown Current I (uA) = 18.611` and click **Fit Calibration**.
   - Confirm `Slope = 5.8650`, `Intercept = 4.0600`, `R^2 = 1.00000`, and `Unknown c = 2.481 mM` (`2.48 mM`).
   - Optional: Set `Blank sigma (uA) = 0.1955` and re-fit to obtain `LOD = 3 * sigma_blank / slope = 0.1000 mM`.
5. For a diode I-V sweep, open the **Diode / CA** tab, set `Threshold |I| (uA)` (e.g., `10.0 uA`), and click **Find Turn-On E** to mark and read out the first potential `E` where `|I|` exceeds the threshold.

## 5. Worked Example 4 — Differential Pulse Voltammetry (DPV) & Standard Addition

**Goal:** Suppress capacitive charging current to quantify trace `Pb2+` and `Cd2+` via standard addition.

1. Select **DPV (Paper Default)** from the **Quick Preset** dropdown:
   - `Start E = -1.000 V`, `End E = -0.400 V`, `Equilibration E = -1.100 V`, `t_eq = 2 s`
   - `Step E = 3.0 mV` (`>= 1.61 mV` hardware floor; quantises to `1 DAC LSB = 3.223 mV`)
   - `Pulse amplitude = 50.0 mV`, `Pulse period = 100 ms`, `Pulse width = 50 ms`, `Sampling period = 3 ms`, `Half-cycle = checked`.
2. Click **Show Real Values** to verify `i_StepE = 1` (`3.223 mV`) and `i_PulseAmplitude = 16` (`51.56 mV`).
3. Click **Run** to record the DPV trace showing resolved peaks near `-0.82 V` (`Cd2+`) and `-0.64 V` (`Pb2+`).
4. Open the **Analysis Tools** dock -> **Std Addition** tab:
   - Click **Paper Pb Preset** (`I = 20.11*c + 9.77`, `Dilution Factor = 20.0`) and click **Fit Std Addition** to verify `x-intercept = -0.4858 ppm` and `Original conc = 9.717 ppm` (`~9.72 ppm`).
   - Click **Paper Cd Preset** (`I = 22.17*c + 10.93`, `Dilution Factor = 20.0`) and click **Fit Std Addition** to verify `x-intercept = -0.4930 ppm` and `Original conc = 9.860 ppm` (`~9.86 ppm`).

## 6. Worked Example 5 — Chronoamperometry (CA)

**Goal:** Record a potential-step Cottrell decay transient and evaluate the current at a specified time after the potential step (`t_step = t_eq`).

1. Select **CA (Paper Default)** from the **Quick Preset** dropdown:
   - `Equilibration E = 0.000 V`, `t_eq = 1 s` (pre-step baseline held at `0.000 V` for `1 s`)
   - `Start E = +0.800 V` (step potential applied at `t = t_eq`)
   - `CA time = 10 s`, `CA datapoints = 200` (`<= 999` firmware array limit).
2. Click **Run**. The firmware buffers the time-current pairs in RAM (`CAIData`/`CAtData`), streams all `200` points at completion, and automatically runs `park_safe()` to return the working electrode to `0.00 V`.
3. Open the **Analysis Tools** dock -> **Diode / CA** tab:
   - Enter `t_query after step (s) = 5.00` and click **Evaluate I(t_step + t_query)** to read the interpolated current at `t = 1.0 + 5.0 = 6.0 s`.
4. Click **Save** on the toolbar to write `<name>.csv` (with columns `t_s,t_step_s,I_uA` where `t_step_s = t_s - t_eq`) and `<name>.json` (containing `"ca_step_time_s": 1.0`).