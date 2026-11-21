# hunstat/io_files.py
"""Saving (CSV + JSON sidecar), loading (legacy TSV + generic CSV/TSV), and batch conversion."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from typing import Any

from hunstat.models import CycleData, DatasetRun
from hunstat.settings import (
    APP_NAME,
    APP_VERSION,
    TECH_CA,
    TECH_CV,
    TECH_DPV,
    ExperimentParams,
    make_paper_defaults,
)


def save_dataset_csv_json(dataset: DatasetRun, csv_path: str | Path) -> tuple[Path, Path]:
    """Save a DatasetRun to <name>.csv and its companion metadata to <name>.json.

    Formats:
    - CV / LSV / DPV: header `cycle,E_V,I_uA`
    - CA: header `t_s,t_step_s,I_uA` (where t_step_s = t_s - teq is time relative to step)
    """
    target_csv = Path(csv_path)
    if target_csv.suffix.lower() != ".csv":
        target_csv = target_csv.with_suffix(".csv")
    target_json = target_csv.with_suffix(".json")
    target_csv.parent.mkdir(parents=True, exist_ok=True)

    t_eq = float(dataset.params.general.t_eq)

    with open(target_csv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if dataset.technique == TECH_CA:
            writer.writerow(["t_s", "t_step_s", "I_uA"])
            for cyc in dataset.cycles:
                for t_val, i_val in zip(cyc.x, cyc.y_display):
                    t_rel = t_val - t_eq
                    writer.writerow([f"{t_val:.4f}", f"{t_rel:.4f}", f"{i_val:.4f}"])
        else:
            writer.writerow(["cycle", "E_V", "I_uA"])
            for cyc in dataset.cycles:
                for e_val, i_val in zip(cyc.x, cyc.y_display):
                    writer.writerow([str(cyc.cycle_index), f"{e_val:.4f}", f"{i_val:.4f}"])

    meta: dict[str, Any] = {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "name": dataset.name,
        "technique_code": dataset.technique,
        "technique_name": dataset.technique_name,
        "timestamp": dataset.timestamp,
        "port": dataset.port,
        "smoothing": dataset.smoothing_label,
        "parameters": dataset.params.to_dict(),
    }
    if dataset.technique == TECH_CA:
        meta["ca_step_time_s"] = t_eq

    with open(target_json, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    dataset.source_path = str(target_csv)
    return target_csv, target_json


def is_legacy_hunstat_tsv(lines: list[str]) -> bool:
    """Return True if the file starts with the HunStat v4.1 'Version\\t2' / 'Header' block."""
    if len(lines) < 2:
        return False
    first = lines[0].strip()
    second = lines[1].strip()
    return first.startswith("Version") and second == "Header"


def load_legacy_tsv(file_path: str | Path) -> DatasetRun:
    """Parse a legacy HunStat .tsv file (including multi-cycle section index tables)."""
    path = Path(file_path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw_lines = [ln.rstrip("\r\n") for ln in fh.readlines()]

    if not is_legacy_hunstat_tsv(raw_lines):
        raise ValueError(f"Not a valid legacy HunStat TSV file: {path}")

    technique = TECH_CV
    section_ranges: list[tuple[int, int]] = []
    declared_data_count: int | None = None
    data_start_line_idx: int | None = None
    col_header_line: str = ""

    idx = 0
    n_lines = len(raw_lines)
    while idx < n_lines:
        line = raw_lines[idx].strip()
        if line.startswith("Technique"):
            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    technique = int(parts[1].strip())
                except ValueError:
                    technique = TECH_CV
        elif line.startswith("Sections"):
            parts = line.split("\t")
            num_sections = int(parts[1].strip()) if len(parts) >= 2 else 0
            for _ in range(num_sections):
                idx += 1
                if idx >= n_lines:
                    break
                sec_line = raw_lines[idx].strip()
                if not sec_line:
                    break
                sec_parts = sec_line.split("\t")
                if len(sec_parts) >= 2:
                    start_pt = int(sec_parts[0].strip())
                    end_pt = int(sec_parts[1].strip())
                    section_ranges.append((start_pt, end_pt))
        elif line.startswith("Data"):
            parts = line.split("\t")
            if len(parts) >= 2:
                try:
                    declared_data_count = int(parts[1].strip())
                except ValueError:
                    declared_data_count = None
            # Next line is the column header ("Potential/V\tCurrent/uA" or "Time/s\tCurrent/uA")
            if idx + 1 < n_lines:
                col_header_line = raw_lines[idx + 1].strip()
                data_start_line_idx = idx + 2
            break
        idx += 1

    if "Time/s" in col_header_line:
        technique = TECH_CA

    preset_key = "CA" if technique == TECH_CA else ("DPV" if technique == TECH_DPV else "CV")
    params = make_paper_defaults(preset_key)
    params.technique = technique

    # Parse tab-separated numeric data rows after the column header (bounded by Data\t<N> if present)
    all_points: list[tuple[float, float]] = []
    if data_start_line_idx is not None and (declared_data_count is None or declared_data_count > 0):
        for row_str in raw_lines[data_start_line_idx:]:
            s = row_str.strip()
            if not s:
                continue
            cols = s.split("\t") if "\t" in s else re.split(r"[,;]+|\s+", s)
            if len(cols) >= 2:
                try:
                    all_points.append((float(cols[0].strip()), float(cols[1].strip())))
                except ValueError:
                    continue
                if declared_data_count is not None and len(all_points) >= declared_data_count:
                    break

    ds = DatasetRun(
        name=path.stem,
        technique=technique,
        params=params,
        source_path=str(path),
    )

    if section_ranges and all_points:
        for cyc_num, (start_1based, end_1based) in enumerate(section_ranges, start=1):
            cyc = ds.get_or_create_cycle(cyc_num)
            lo = max(0, start_1based - 1)
            hi = min(len(all_points), end_1based)
            for x_val, y_val in all_points[lo:hi]:
                cyc.append_point(x_val, y_val)
    elif all_points:
        cyc = ds.get_or_create_cycle(1)
        for x_val, y_val in all_points:
            cyc.append_point(x_val, y_val)

    return ds


def _try_parse_floats(tokens: list[str]) -> list[float] | None:
    if not tokens:
        return None
    out: list[float] = []
    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        try:
            out.append(float(t))
        except ValueError:
            return None
    return out if len(out) >= 2 else None


def _split_row(line: str) -> list[str]:
    if "\t" in line:
        return [c.strip() for c in line.split("\t") if c.strip() != ""]
    if "," in line:
        return [c.strip() for c in line.split(",") if c.strip() != ""]
    if ";" in line:
        return [c.strip() for c in line.split(";") if c.strip() != ""]
    return line.split()


def load_datafile(file_path: str | Path) -> DatasetRun:
    """Auto-detect and load a legacy HunStat .tsv, a new HunStat .csv (+ .json),
    or any generic 2- or 3-column CSV/TSV/whitespace electrochemistry text file."""
    path = Path(file_path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw_lines = [ln.rstrip("\r\n") for ln in fh.readlines()]

    non_empty = [ln for ln in raw_lines if ln.strip()]
    if is_legacy_hunstat_tsv(non_empty):
        return load_legacy_tsv(path)

    # Check for companion <name>.json sidecar
    json_sidecar = path.with_suffix(".json")
    sidecar_meta: dict[str, Any] = {}
    if json_sidecar.exists():
        try:
            with open(json_sidecar, "r", encoding="utf-8") as jfh:
                sidecar_meta = json.load(jfh)
        except Exception:
            sidecar_meta = {}

    technique = int(sidecar_meta.get("technique_code", TECH_CV))
    if "parameters" in sidecar_meta and isinstance(sidecar_meta["parameters"], dict):
        params = ExperimentParams.from_dict(sidecar_meta["parameters"])
        technique = params.technique
    else:
        params = make_paper_defaults("CV")

    header_tokens: list[str] = []
    numeric_rows: list[list[float]] = []

    for ln in non_empty:
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        tokens = _split_row(stripped)
        vals = _try_parse_floats(tokens)
        if vals is None:
            if not numeric_rows and not header_tokens:
                header_tokens = [t.lower() for t in tokens]
            continue
        numeric_rows.append(vals)

    # Infer column layout from header or column count
    has_cycle_col = False
    is_ca_3col = False
    if header_tokens:
        if header_tokens[0] in ("cycle", "section", "scan"):
            has_cycle_col = True
        elif header_tokens[0] in ("t_s", "time", "time/s", "t"):
            technique = TECH_CA
            params.technique = TECH_CA
            if len(header_tokens) >= 3 and ("step" in header_tokens[1] or "rel" in header_tokens[1]):
                is_ca_3col = True

    ds = DatasetRun(
        name=str(sidecar_meta.get("name") or path.stem),
        technique=technique,
        params=params,
        timestamp=str(sidecar_meta.get("timestamp") or ""),
        port=str(sidecar_meta.get("port") or ""),
        source_path=str(path),
        smoothing_label=str(sidecar_meta.get("smoothing") or "Raw"),
    )

    for vals in numeric_rows:
        if len(vals) >= 3:
            if is_ca_3col or (technique == TECH_CA and not has_cycle_col):
                # Columns: t_s, t_step_s, I_uA
                cyc = ds.get_or_create_cycle(1)
                cyc.append_point(vals[0], vals[2])
            else:
                # Columns: cycle, E_V, I_uA (auto-detect integer cycle column)
                cyc_idx = max(1, int(round(vals[0])))
                cyc = ds.get_or_create_cycle(cyc_idx)
                cyc.append_point(vals[1], vals[2])
        elif len(vals) == 2:
            cyc = ds.get_or_create_cycle(1)
            cyc.append_point(vals[0], vals[1])

    return ds


def convert_legacy_tsv_to_csv(
    tsv_path: str | Path,
    output_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    """Convert one legacy .tsv file into a new .csv + .json pair."""
    src = Path(tsv_path)
    ds = load_legacy_tsv(src)
    out_folder = Path(output_dir) if output_dir is not None else src.parent
    out_csv = out_folder / f"{src.stem}.csv"
    return save_dataset_csv_json(ds, out_csv)


def convert_legacy_folder(
    folder_path: str | Path,
    output_dir: str | Path | None = None,
) -> list[tuple[Path, Path]]:
    """Batch-convert all legacy .tsv files in a directory into .csv + .json pairs."""
    folder = Path(folder_path)
    converted: list[tuple[Path, Path]] = []
    for tsv_file in sorted(folder.iterdir()):
        if tsv_file.is_file() and tsv_file.suffix.lower() == ".tsv":
            try:
                converted.append(convert_legacy_tsv_to_csv(tsv_file, output_dir=output_dir))
            except ValueError:
                continue
    return converted