import datetime as dt
import json

import openpyxl
import pytest

from scgraph.mapping import transforms
from scgraph.mapping.execute import execute
from scgraph.mapping.spec import SpecError, parse_spec
from scgraph.mapping.tables import TableFile
from scgraph.mapping.transforms import MISSING, TransformError


def save_formula_workbook(path, rows, cached):
    """openpyxl cannot compute formulas, so write cached values the way a spreadsheet app would."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    for r in rows:
        ws.append(r)
    wb.save(path)
    # Patch cached values into the sheet XML: openpyxl writes <c r="C2"><f>B2*2</f><v /></c>.
    import zipfile

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                text = data.decode()
                for ref, value in cached.items():
                    start = text.index(f'<c r="{ref}">')
                    end = text.index("</c>", start)
                    text = text[:start] + text[start:end].replace("<v />", f"<v>{value}</v>") + text[end:]
                data = text.encode()
            dst.writestr(item, data)
    tmp.replace(path)


def test_formula_cell_reads_cached_value(tmp_path):
    path = tmp_path / "f.xlsx"
    save_formula_workbook(path, [["ID", "Qty", "Double"], ["A", 2, "=B2*2"]], {"C2": 4})
    region = TableFile(path).region("Data", header_row=1, start=2)
    assert region.rows[0].cells["C"] == 4


def make_workbook(path, sheets):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
    wb.save(path)
    return path


def test_region_with_explicit_end_and_stop_at_blank(tmp_path):
    path = make_workbook(tmp_path / "r.xlsx", {"S": [
        ["ID", "Name"], ["A", "x"], ["B", "y"], [None, "tail"], ["C", "z"], [], ["Total", 3],
    ]})
    table = TableFile(path)
    assert [r.number for r in table.region("S", header_row=1, start=2, end=3).rows] == [2, 3]
    assert [r.cells["A"] for r in table.region("S", header_row=1, start=2, stop_at_blank="ID").rows] == ["A", "B"]
    every = table.region("S", header_row=1, start=2)
    assert [r.number for r in every.rows] == [2, 3, 4, 5, 7]  # wholly blank row 6 skipped


def test_csv_region(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("Supplier ID;Country\nS1;Germany\nS2;France\n")
    region = TableFile(path).region(None, header_row=1, start=2)
    assert region.column("Country") == "B"
    assert [r.cells["B"] for r in region.rows] == ["Germany", "France"]


@pytest.mark.parametrize(
    ("spec", "raw", "field_type", "expected"),
    [
        ("number", "1,234.5", "float", 1234.5),
        ({"number": {"thousands": ".", "decimal": ","}}, "1.234,5", "float", 1234.5),
        ("number", "12.5%", "float", 0.125),
        ({"number": {"percent": "points"}}, "12.5%", "float", 12.5),
        ("number", 7, "float", 7.0),
        ("int", 3.0, "int", 3),
        ("int", "1,000", "int", 1000),
        ({"bool": {"true": ["Yes"], "false": ["No"]}}, "Yes", "bool", True),
        ({"bool": {"true": ["Yes"], "false": ["No"]}}, "no", "bool", False),
        (None, "Y", "bool", True),
        ("date", dt.datetime(2026, 9, 10, 0, 0), "date", "2026-09-10"),
        ("date", "10.09.2026", "date", "2026-09-10"),
        ({"date": {"format": "%m/%d/%Y"}}, "09/10/2026", "date", "2026-09-10"),
        ("trim", "  Bremen ", "str", "Bremen"),
        (None, 12345.0, "str", "12345"),
        ([{"empty": ["n/a", "-"]}, "number"], "n/a", "float", MISSING),
        ([{"empty": ["n/a", "-"]}, "number"], "5", "float", 5.0),
        ({"constant": "EUR"}, None, "str", "EUR"),
        (None, None, "str", MISSING),
        (None, "   ", "float", MISSING),
        (None, 2.0, "scalar", 2.0),
    ],
)
def test_transforms(spec, raw, field_type, expected):
    assert transforms.apply(spec, raw, field_type) == expected


@pytest.mark.parametrize(
    ("spec", "raw"), [("number", "abc"), ("int", 2.5), ({"bool": {"true": ["Yes"]}}, "Maybe"), ("date", "soon")]
)
def test_transform_errors(spec, raw):
    with pytest.raises(TransformError):
        transforms.apply(spec, raw)


COMPONENT_SPEC = {
    "version": 1,
    "file_kind": "xlsx",
    "metadata": {"author": "model", "model": "muse-spark-1.1", "status": "draft"},
    "regions": [{
        "id": "components",
        "sheet": "Components",
        "header_row": 1,
        "rows": {"start": 2, "stop_at_blank": "Component ID*"},
        "confidence": 0.9,
        "produces": [
            {"entity": "Component", "key": {"column": "Component ID*"}},
            {"relation": "SUPPLIES", "from": {"column": "Supplier ID*"}, "to": {"column": "Component ID*"},
             "identity": {"site": {"column": "Production Site ID*"}}, "when_present": ["Supplier ID*"]},
            {"relation": "DELIVERED_TO", "from": {"column": "Component ID*"},
             "to": {"column": "Production Site ID*"}, "when_present": ["Production Site ID*"]},
        ],
        "columns": {
            "Component Description*": {"role": "fact", "target": "Component.description",
                                       "rationale": "free-text part name"},
            "Single Source": {"role": "fact", "target": "Component.single_source", "confidence": 0.6,
                              "transform": {"bool": {"true": ["Yes"], "false": ["No"]}}},
            "Annual Spend": {"role": "fact", "target": "Component.annual_spend"},
            "Business Exposure (% of Sales)": {"role": "derived", "metric": "business_exposure_pct",
                                               "entity": "Component"},
        },
    }],
    "parameters": [{"name": "margin", "sheet": "Weights", "cell": "B30", "label": {"cell": "A30", "equals": "Margin"}}],
}


@pytest.fixture
def components_xlsx(tmp_path):
    weights = [[None, None]] * 29 + [["Margin", 0.19]]
    return make_workbook(tmp_path / "c.xlsx", {
        "Components": [
            ["Component ID*", "Component Description*", "Production Site ID*", "Supplier ID*", "Single Source",
             "Annual Spend", "Business Exposure (% of Sales)", "Comment"],
            ["C1", "BALL", "X1", "S1", "Yes", "1,000.5", 34.1, "check"],
            ["C2", "PIN", "X1", None, "No", "lots", None, None],
            [None, None, None, None, None, None, 0, None],
        ],
        "Weights": weights,
    })


def test_components_row_yields_fields_and_sourcing(components_xlsx):
    spec = parse_spec(COMPONENT_SPEC)
    result = execute(spec, components_xlsx, run_id="r1")
    row2 = [f for f in result.facts if f.source.row == 2]
    assert [(f.kind, f.type, f.key, f.field, f.value) for f in row2] == [
        ("entity", "Component", ("C1",), None, None),
        ("relation", "SUPPLIES", ("S1", "C1", "X1"), None, None),
        ("relation", "DELIVERED_TO", ("C1", "X1"), None, None),
        ("field", "Component", ("C1",), "description", "BALL"),
        ("field", "Component", ("C1",), "single_source", True),
        ("field", "Component", ("C1",), "annual_spend", 1000.5),
    ]
    single = next(f for f in row2 if f.field == "single_source")
    assert (single.confidence, single.method, single.run_id) == (0.6, "mapping", "r1")
    assert next(f for f in row2 if f.field == "description").confidence == 0.9
    assert (single.source.file, single.source.sheet, single.source.row, single.source.column) == \
        ("c.xlsx", "Components", 2, "E")
    # C2 has no supplier: no SUPPLIES, but DELIVERED_TO; the spend cell fails and yields no fact.
    row3 = [(f.kind, f.type, f.field) for f in result.facts if f.source.row == 3]
    assert ("relation", "SUPPLIES", None) not in row3 and ("relation", "DELIVERED_TO", None) in row3
    assert ("field", "Component", "annual_spend") not in row3
    # Derived column -> oracle value, no field fact.
    assert [(o.entity_id, o.metric, o.value) for o in result.oracle] == [("C1", "business_exposure_pct", 34.1)]
    assert not any(f.field == "business_exposure_pct" for f in result.facts)
    assert [(p.name, p.value, p.source.column, p.source.row) for p in result.parameters] == [("margin", 0.19, "B", 30)]
    assert spec.metadata["status"] == "draft"


def test_transform_failure_reported_with_location(components_xlsx):
    result = execute(parse_spec(COMPONENT_SPEC), components_xlsx)
    [failure] = result.failures
    assert (failure.source.sheet, failure.source.row, failure.source.column) == ("Components", 3, "F")
    assert failure.raw == "lots" and failure.column == "Annual Spend"


def test_unmapped_column_reported(components_xlsx):
    result = execute(parse_spec(COMPONENT_SPEC), components_xlsx)
    assert [(u.column, u.header) for u in result.unmapped] == [("H", "Comment")]


def test_execution_is_deterministic(components_xlsx):
    spec = parse_spec(COMPONENT_SPEC)
    assert execute(spec, components_xlsx, run_id="r").facts == execute(spec, components_xlsx, run_id="r").facts


def test_bad_target_field_rejected_naming_region_column_field():
    bad = json.loads(json.dumps(COMPONENT_SPEC))
    bad["regions"][0]["columns"]["Component Description*"]["target"] = "Component.colour"
    with pytest.raises(SpecError) as err:
        parse_spec(bad)
    message = str(err.value)
    assert "region components" in message and "Component Description*" in message and "colour" in message


def test_parameter_label_mismatch(components_xlsx):
    bad = json.loads(json.dumps(COMPONENT_SPEC))
    bad["parameters"][0]["label"]["equals"] = "Margin %"
    with pytest.raises(SpecError, match="Margin %"):
        execute(parse_spec(bad), components_xlsx)


def test_known_anomaly_marks_oracle_value(components_xlsx):
    data = json.loads(json.dumps(COMPONENT_SPEC))
    data["known_anomalies"] = [{"sheet": "Components", "cell": "G2", "reason": "typed over formula"}]
    [oracle] = execute(parse_spec(data), components_xlsx).oracle
    assert oracle.anomaly == "typed over formula"
    data["known_anomalies"] = [{"sheet": "Components", "cell": "G2"}]
    with pytest.raises(SpecError, match="reason"):
        parse_spec(data)


def test_spec_name_overrides_the_loaded_path(components_xlsx):
    from scgraph.mapping.spec import parse_spec

    result = execute(parse_spec(COMPONENT_SPEC), components_xlsx, spec_name="gen/x.json")
    assert {f.origin["spec"] for f in result.facts} == {"gen/x.json"}


def test_numbers_fit_the_field_type():
    from scgraph.mapping import transforms

    assert transforms.apply({"number": {"decimal": ",", "thousands": "."}}, "1,0", "int") == 1
    assert transforms.apply("number", 3, "float") == 3.0 and isinstance(transforms.apply("number", 3, "float"), float)
    with pytest.raises(transforms.TransformError):
        transforms.apply("number", "1.5", "int")
