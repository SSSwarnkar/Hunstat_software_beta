# tests/test_stage3.py
"""Stage 3 unit tests: CSV+JSON save/load, legacy TSV multi-cycle loader, generic importer,
batch TSV-to-CSV converter, and GUI PNG/SVG plot export."""

from __future__ import annotations

import json
from pathlib import Path
import sys

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # type: ignore[import-not-found]
from PySide6.QtWidgets import QApplication

from hunstat.gui import HunStatMainWindow
from hunstat.io_files import (
    convert_legacy_folder,
    convert_legacy_tsv_to_csv,
    load_datafile,
    load_legacy_tsv,
    save_dataset_csv_json,
)
from hunstat.models import DatasetRun
from hunstat.settings import TECH_CA, TECH_CV, make_paper_defaults


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def test_save_and_reload_cv_and_ca_csv_json(tmp_path: Path) -> None:
    # 1. Multi-cycle CV save & reload
    cv_ds = DatasetRun(name="SampleCV", technique=TECH_CV, params=make_paper_defaults("CV"), port="COM3")
    c1 = cv_ds.get_or_create_cycle(1)
    c1.append_point(-0.2, -10.5)
    c1.append_point(0.2, 45.2)
    c2 = cv_ds.get_or_create_cycle(2)
    c2.append_point(-0.2, -9.8)
    c2.append_point(0.2, 44.1)

    csv_path, json_path = save_dataset_csv_json(cv_ds, tmp_path / "sample_cv.csv")
    assert csv_path.exists()
    assert json_path.exists()

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "cycle,E_V,I_uA"
    assert lines[1] == "1,-0.2000,-10.5000"
    assert lines[3] == "2,-0.2000,-9.8000"

    reloaded_cv = load_datafile(csv_path)
    assert reloaded_cv.technique == TECH_CV
    assert len(reloaded_cv.cycles) == 2
    assert len(reloaded_cv.cycles[0].x) == 2
    assert abs(reloaded_cv.cycles[1].y_raw[1] - 44.1) < 1e-4

    # 2. CA save & reload (verify t_s, t_step_s, I_uA columns and ca_step_time_s in JSON)
    ca_params = make_paper_defaults("CA")
    ca_params.general.t_eq = 2
    ca_ds = DatasetRun(name="SampleCA", technique=TECH_CA, params=ca_params, port="COM3")
    ca_cyc = ca_ds.get_or_create_cycle(1)
    ca_cyc.append_point(1.0, 0.05)   # Pre-step baseline (t_step_s = -1.0)
    ca_cyc.append_point(2.5, 62.30)  # Post-step decay (t_step_s = +0.5)

    ca_csv, ca_json = save_dataset_csv_json(ca_ds, tmp_path / "sample_ca.csv")
    ca_lines = ca_csv.read_text(encoding="utf-8").splitlines()
    assert ca_lines[0] == "t_s,t_step_s,I_uA"
    assert ca_lines[1] == "1.0000,-1.0000,0.0500"
    assert ca_lines[2] == "2.5000,0.5000,62.3000"

    meta = json.loads(ca_json.read_text(encoding="utf-8"))
    assert meta["ca_step_time_s"] == 2.0

    reloaded_ca = load_datafile(ca_csv)
    assert reloaded_ca.technique == TECH_CA
    assert len(reloaded_ca.cycles) == 1
    assert abs(reloaded_ca.cycles[0].x[1] - 2.5) < 1e-4
    assert abs(reloaded_ca.cycles[0].y_raw[1] - 62.30) < 1e-4


def test_legacy_tsv_loader_and_batch_converter(tmp_path: Path) -> None:
    # Synthesize an exact replica of the 2-section legacy TSV format (like GCE_100mvs_cv.tsv)
    legacy_content = (
        "Version\t2\r\n"
        "Header\r\n"
        "Technique\t0\r\n"
        "Sections\t2\r\n"
        "1\t3\r\n"
        "4\t6\r\n"
        "\r\n"
        "Data\t6\r\n"
        "Potential/V\tCurrent/uA\r\n"
        "-0.4028\t-11.1182\r\n"
        "0.0000\t15.2000\r\n"
        "0.4000\t80.5000\r\n"
        "-0.4028\t-33.7573\r\n"
        "0.0000\t14.1000\r\n"
        "0.4000\t78.9000\r\n"
    )
    tsv_file = tmp_path / "GCE_mock_cv.tsv"
    tsv_file.write_bytes(legacy_content.encode("ascii"))

    # Also test an empty/aborted legacy file like GCE_10mvpers_CV.tsv (Sections 0, Data 0)
    empty_legacy = (
        "Version\t2\r\n"
        "Header\r\n"
        "Technique\t0\r\n"
        "Sections\t0\r\n"
        "\r\n"
        "Data\t0\r\n"
        "Potential/V\tCurrent/uA\r\n"
    )
    empty_tsv = tmp_path / "GCE_empty.tsv"
    empty_tsv.write_bytes(empty_legacy.encode("ascii"))

    ds = load_legacy_tsv(tsv_file)
    assert ds.technique == TECH_CV
    assert len(ds.cycles) == 2
    assert len(ds.cycles[0].x) == 3
    assert len(ds.cycles[1].x) == 3
    assert abs(ds.cycles[0].y_raw[0] - (-11.1182)) < 1e-4
    assert abs(ds.cycles[1].y_raw[0] - (-33.7573)) < 1e-4

    ds_empty = load_legacy_tsv(empty_tsv)
    assert len(ds_empty.cycles) == 0

    # Batch convert entire folder
    out_dir = tmp_path / "converted"
    pairs = convert_legacy_folder(tmp_path, output_dir=out_dir)
    assert len(pairs) == 2
    reloaded = load_datafile(out_dir / "GCE_mock_cv.csv")
    assert len(reloaded.cycles) == 2
    assert reloaded.total_points == 6


def test_generic_whitespace_and_csv_importer(tmp_path: Path) -> None:
    generic_txt = tmp_path / "generic_2col.txt"
    generic_txt.write_text(
        "# External potentiostat export\nPotential_V Current_uA\n-0.10 -2.5\n0.10 12.3\n0.30 45.0\n",
        encoding="utf-8",
    )
    ds = load_datafile(generic_txt)
    assert len(ds.cycles) == 1
    assert ds.total_points == 3
    assert abs(ds.cycles[0].x[2] - 0.30) < 1e-6
    assert abs(ds.cycles[0].y_raw[2] - 45.0) < 1e-6


def test_gui_open_save_and_export_png_svg(qapp: QApplication, tmp_path: Path) -> None:
    win = HunStatMainWindow(fast_sim=True)

    # Create a TSV file, open it via GUI, save as CSV+JSON, and export PNG & SVG
    tsv_file = tmp_path / "ferrous_test.tsv"
    tsv_file.write_text(
        "Version\t2\nHeader\nTechnique\t0\nSections\t1\n1\t3\n\nData\t3\n"
        "Potential/V\tCurrent/uA\n0.0000\t-11.7627\n0.2000\t42.0000\n0.4000\t18.5000\n",
        encoding="utf-8",
    )
    loaded = win.open_files([tsv_file])
    assert len(loaded) == 1
    assert win.tree_selection.topLevelItemCount() == 1

    saved = win.save_selected_dataset(tmp_path / "exported_ferrous.csv")
    assert saved is not None
    assert saved[0].exists() and saved[1].exists()

    png_path = win.export_plot_image(tmp_path / "plot.png")
    svg_path = win.export_plot_image(tmp_path / "plot.svg")
    assert png_path.exists() and png_path.stat().st_size > 0
    assert svg_path.exists() and svg_path.stat().st_size > 0
    win.close()