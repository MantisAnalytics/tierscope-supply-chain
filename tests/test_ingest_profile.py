import csv
import json

import openpyxl

from scgraph.ingest.layout import (compatible, find_reusable, ignore_layout, layout_of, load_stored,
                                   normalise_header)
from scgraph.ingest.profile import Profiler, delimiter_score, id_pattern

RISK_NOTES = [
    "Confidence: Medium | Supplies: Polyester yarn / polymer chips | Notes: Upstream relationship identified.",
    "Confidence: High | Risk flags: CRITICAL_MATERIAL, SINGLE_SOURCE | Supplies: Nylon raw materials | Notes: x",
    "Confidence: High | Risk flags: CRITICAL_MATERIAL | Supplies: Nylon raw materials | Notes: y",
]


def workbook(path, rows, title="Suppliers"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


SUPPLIER_ROWS = [
    ["Supplier ID*", "Name", "Tier", "Risk Notes", "Exposure"],
    ["S100001", "Acme", 1, None, "=C2*2"],
    ["S100002", "Bolt", 1, None, "=C3*2"],
    ["T2-DM001", "Coil", 2, RISK_NOTES[0], "=C4*2"],
    ["T2-DM002", "Disk", 2, RISK_NOTES[1], "=C5*2"],
    ["T3-DM003", "Edge", 3, RISK_NOTES[2], "=C6*2"],
]


def test_profile_detects_formulas_ids_and_delimited_text(tmp_path):
    profiler = Profiler(workbook(tmp_path / "s.xlsx", SUPPLIER_ROWS))
    _, columns = profiler.region("Suppliers", header_row=1, start=2)
    by_header = {c.header: c for c in columns}
    assert by_header["Exposure"].computed and by_header["Exposure"].formula_share == 1.0
    assert not by_header["Tier"].computed and by_header["Tier"].type == "int"
    assert by_header["Supplier ID*"].type == "id"
    assert set(by_header["Supplier ID*"].id_pattern.split(" | ")) == {"S######", "T#-DM###"}
    assert by_header["Risk Notes"].delimiter_score >= 0.9 and by_header["Risk Notes"].type == "long_text"
    assert by_header["Name"].delimiter_score == 0.0
    assert by_header["Risk Notes"].fill_rate == 0.6
    sheet = profiler.sheet("Suppliers")
    assert sheet.formula_cells == 5 and sheet.preview[0][0] == "A1=Supplier ID*"


def test_id_pattern_and_delimiter_score_helpers():
    assert id_pattern(["DMC-1", "DMC-22"]) == "DMC-# | DMC-##"
    assert id_pattern(["Plot 5, Yingshang"]) is None
    assert delimiter_score(RISK_NOTES) >= 0.9
    assert delimiter_score(["Am Gewerbepark 20, Cuxhaven"]) == 0.0


def test_normalisation_and_type_compatibility():
    assert normalise_header("  Supplier   ID* ") == "supplier id"
    assert compatible("int", "float") and compatible("empty", "text") and compatible("id", "text")
    assert not compatible("float", "text")


def store_spec(directory, name, sheet, profiler, kind="region"):
    if kind == "ignore":
        data = {"version": 1, "regions": [], "ignore_sheets": {sheet: "derived"},
                "layout": ignore_layout(profiler, sheet)}
    else:
        _, columns = profiler.region(sheet, header_row=1, start=2)
        data = {
            "version": 1,
            "regions": [{"id": "suppliers", "sheet": sheet, "header_row": 1, "rows": {"start": 2},
                         "produces": [{"entity": "Supplier", "key": {"column": "Supplier ID*"}}],
                         "columns": {"Name": {"role": "fact", "target": "Supplier.name"}}}],
            "layout": {"kind": "region", "sheet": sheet, "header_row": 1, **layout_of(columns)},
        }
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(json.dumps(data))


def test_layout_match_reuse_and_changes(tmp_path):
    stored_dir = tmp_path / "generated"
    original = Profiler(workbook(tmp_path / "a.xlsx", SUPPLIER_ROWS))
    store_spec(stored_dir, "a__suppliers.json", "Suppliers", original)
    stored = load_stored(stored_dir)

    more_rows = SUPPLIER_ROWS + [["S100009", "Zed", 1, None, "=C7*2"]]
    again = Profiler(workbook(tmp_path / "b.xlsx", more_rows))
    assert [s.path.name for s in find_reusable(stored, again, "Suppliers")] == ["a__suppliers.json"]

    renamed = [["Supplier ID*", "Supplier Name", *SUPPLIER_ROWS[0][2:]], *SUPPLIER_ROWS[1:]]
    assert find_reusable(stored, Profiler(workbook(tmp_path / "c.xlsx", renamed)), "Suppliers") is None

    added = [[*r, "x"] for r in SUPPLIER_ROWS]
    assert find_reusable(stored, Profiler(workbook(tmp_path / "d.xlsx", added)), "Suppliers") is None

    retyped = [SUPPLIER_ROWS[0]] + [[r[0], r[1], "one", r[3], r[4]] for r in SUPPLIER_ROWS[1:]]
    assert find_reusable(stored, Profiler(workbook(tmp_path / "e.xlsx", retyped)), "Suppliers") is None


def test_csv_with_same_headers_matches_xlsx_spec(tmp_path):
    stored_dir = tmp_path / "generated"
    store_spec(stored_dir, "a__suppliers.json", "Suppliers", Profiler(workbook(tmp_path / "a.xlsx", SUPPLIER_ROWS)))
    path = tmp_path / "suppliers.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SUPPLIER_ROWS[0])
        for r in SUPPLIER_ROWS[1:]:
            writer.writerow([*r[:4], r[2] * 2])  # a csv export holds values, not formulas
    [match] = find_reusable(load_stored(stored_dir), Profiler(path), None)
    assert match.path.name == "a__suppliers.json"


def test_ignored_sheet_matches_by_first_row(tmp_path):
    stored_dir = tmp_path / "generated"
    profiler = Profiler(workbook(tmp_path / "a.xlsx", [["Site ID", "BE"], ["X", 1]], title="BE per sites"))
    store_spec(stored_dir, "a__ignore__be-per-sites.json", "BE per sites", profiler, kind="ignore")
    stored = load_stored(stored_dir)
    other = Profiler(workbook(tmp_path / "b.xlsx", [["Site ID", "BE"], ["Y", 2], ["Z", 3]], title="BE per sites"))
    assert find_reusable(stored, other, "BE per sites")
    changed = Profiler(workbook(tmp_path / "c.xlsx", [["Site", "BE"], ["Y", 2]], title="BE per sites"))
    assert find_reusable(stored, changed, "BE per sites") is None
