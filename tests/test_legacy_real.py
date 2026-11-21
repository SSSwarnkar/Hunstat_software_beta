# tests/test_legacy_real.py
"""Step 1 verification tests: load real legacy .tsv files from tests/data/ and verify
technique, cycle/section count, and point counts against each file's header block."""

from __future__ import annotations

from pathlib import Path
import sys

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # type: ignore[import-not-found]

from hunstat.io_files import convert_legacy_tsv_to_csv, load_datafile, load_legacy_tsv
from hunstat.settings import TECH_CA, TECH_CV, TECH_DPV

DATA_DIR = Path(__file__).resolve().parent / "data"

EXPECTED_REAL_FILES = [
    "GCE_100mvs_cv.tsv",
    "GCE_10mvs_cv.tsv",
    "GCE_20mvpers_CV.tsv",
    "GCE_10mvpers_CV.tsv",
    "ferrous_1.tsv",
    "ferrous_2_test.tsv",
]


def _parse_raw_tsv_header(path: Path) -> tuple[int, list[tuple[int, int]], int, str, int]:
    """Independently inspect a legacy .tsv header for ground-truth verification."""
    raw_lines = [
        ln.rstrip("\r\n")
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
    ]
    assert len(raw_lines) >= 6, f"{path.name}: file too short ({len(raw_lines)} lines)"
    assert raw_lines[0].strip() == "Version\t2", f"{path.name}: unexpected line 0: {raw_lines[0]!r}"
    assert raw_lines[1].strip() == "Header", f"{path.name}: unexpected line 1: {raw_lines[1]!r}"

    tech = int(raw_lines[2].split("\t")[1].strip())
    num_sections = int(raw_lines[3].split("\t")[1].strip())
    sections: list[tuple[int, int]] = []
    idx = 4
    for _ in range(num_sections):
        parts = raw_lines[idx].strip().split("\t")
        sections.append((int(parts[0]), int(parts[1])))
        idx += 1

    while idx < len(raw_lines) and not raw_lines[idx].strip():
        idx += 1

    assert idx < len(raw_lines) and raw_lines[idx].startswith("Data\t"), (
        f"{path.name}: missing 'Data\\t<N>' line at line {idx + 1}"
    )
    declared_data_n = int(raw_lines[idx].split("\t")[1].strip())
    col_header = raw_lines[idx + 1].strip() if (idx + 1) < len(raw_lines) else ""
    if "Time/s" in col_header:
        tech = TECH_CA

    actual_numeric_rows = 0
    for ln in raw_lines[idx + 2 :]:
        s = ln.strip()
        if not s:
            continue
        cols = s.split("\t")
        if len(cols) >= 2:
            float(cols[0])
            float(cols[1])
            actual_numeric_rows += 1

    return tech, sections, declared_data_n, col_header, actual_numeric_rows


def _discover_real_tsv_files() -> list[Path]:
    if not DATA_DIR.exists():
        return []
    return sorted(p for p in DATA_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".tsv")


def test_real_legacy_tsv_files_in_tests_data(tmp_path: Path) -> None:
    """Load every real .tsv file in tests/data/, verify technique, cycles, and points,
    and verify round-trip conversion to .csv + .json."""
    tsv_files = _discover_real_tsv_files()
    if not tsv_files:
        pytest.skip(f"No .tsv files found in {DATA_DIR}; place real .tsv files in tests/data/ to run.")

    print("\n--- Real Legacy .tsv Verification Summary ---")
    for tsv_path in tsv_files:
        tech, sections, declared_n, col_header, actual_rows = _parse_raw_tsv_header(tsv_path)
        ds = load_legacy_tsv(tsv_path)

        expected_cycles = len(sections) if (declared_n > 0 and sections) else (1 if declared_n > 0 else 0)
        print(
            f"{tsv_path.name}: Technique={ds.technique} ({ds.technique_name}), "
            f"Sections={len(sections)}, LoadedCycles={len(ds.cycles)}, "
            f"Data={declared_n} (actual_rows={actual_rows}, loaded_pts={ds.total_points}), "
            f"Columns={col_header!r}"
        )

        assert ds.technique in (TECH_CV, TECH_DPV, TECH_CA)
        assert ds.technique == tech, f"{tsv_path.name}: technique mismatch ({ds.technique} != {tech})"
        assert len(ds.cycles) == expected_cycles, (
            f"{tsv_path.name}: cycle count mismatch ({len(ds.cycles)} != {expected_cycles})"
        )
        assert ds.total_points == declared_n, (
            f"{tsv_path.name}: total_points ({ds.total_points}) != Data header ({declared_n})"
        )

        for cyc, (start_1, end_1) in zip(ds.cycles, sections):
            expected_sec_len = end_1 - start_1 + 1
            assert len(cyc.x) == expected_sec_len, (
                f"{tsv_path.name} cycle {cyc.cycle_index}: len={len(cyc.x)} != {expected_sec_len}"
            )

        out_csv, out_json = convert_legacy_tsv_to_csv(tsv_path, output_dir=tmp_path)
        assert out_csv.exists() and out_json.exists()
        reloaded = load_datafile(out_csv)
        assert reloaded.technique == ds.technique
        assert len(reloaded.cycles) == len(ds.cycles)
        assert reloaded.total_points == ds.total_points


def test_declared_data_count_bounds_trailing_lines(tmp_path: Path) -> None:
    """Verify load_legacy_tsv respects Data\\t<N> and ignores trailing non-data lines."""
    raw_tsv = (
        "Version\t2\r\n"
        "Header\r\n"
        "Technique\t1\r\n"
        "Sections\t1\r\n"
        "1\t2\r\n"
        "\r\n"
        "Data\t2\r\n"
        "Potential/V\tCurrent/uA\r\n"
        "-0.8000\t12.5000\r\n"
        "-0.6000\t24.0000\r\n"
        "-0.4000\t99.9999\r\n"
    )
    f = tmp_path / "bounded_dpv.tsv"
    f.write_bytes(raw_tsv.encode("ascii"))

    ds = load_legacy_tsv(f)
    assert ds.technique == TECH_DPV
    assert len(ds.cycles) == 1
    assert ds.total_points == 2
    assert ds.cycles[0].y_raw == [12.5, 24.0]