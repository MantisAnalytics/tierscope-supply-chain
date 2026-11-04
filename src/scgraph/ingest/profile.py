"""Deterministic table profiling: what the model sees instead of the raw rows. No LLM calls."""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter

from scgraph.mapping.tables import Region, TableFile, is_blank

PREVIEW_ROWS = 15
# Report-style sheets with at most this many non-empty rows are shown whole, so blocks below row 15 are visible.
SMALL_SHEET_ROWS = 40
PREVIEW_CELL_CHARS = 40
SAMPLES = 5
LONG_TEXT = 80
BOOL_LABELS = {"yes", "no", "true", "false", "y", "n"}
_KV = re.compile(r"(?:^|[|;])\s*[A-Za-z][\w ()/-]{0,30}:\s")


@dataclass
class ColumnProfile:
    column: str  # letter
    header: str | None
    fill_rate: float
    distinct: int
    type: str  # empty, int, float, percent, bool, date, id, text, long_text
    samples: list[Any]
    id_pattern: str | None = None
    formula_share: float = 0.0
    max_length: int = 0
    delimiter_score: float = 0.0

    @property
    def computed(self) -> bool:
        return self.formula_share >= 0.5

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if v not in (None, 0, 0.0, [])}
        d["column"], d["header"], d["type"] = self.column, self.header, self.type
        d["fill_rate"] = round(self.fill_rate, 3)
        if self.computed:
            d["computed"] = True
        return d


@dataclass
class SheetProfile:
    sheet: str | None
    rows: int  # last row with data
    columns: int
    preview: list[list[str]]  # "A1=value" cells of the first non-empty rows
    formula_cells: int = 0
    formula_columns: list[str] = field(default_factory=list)  # columns where most filled cells are formulas

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkbookProfile:
    file: str
    kind: str
    sheets: list[SheetProfile] = field(default_factory=list)


class Profiler:
    """Profiles sheets and regions of one file; reads it once for values and once for formulas."""

    def __init__(self, source: str | Path | TableFile) -> None:
        self.table = source if isinstance(source, TableFile) else TableFile(source)
        self.path = self.table.path
        self._formulas: dict[str | None, set[str]] = {}
        if self.table.kind == "xlsx" and self.path.is_file():
            wb = openpyxl.load_workbook(self.path, data_only=False)
            for ws in wb.worksheets:
                self._formulas[ws.title] = {
                    c.coordinate for row in ws.iter_rows() for c in row if _is_formula(c.value)
                }
            wb.close()

    def workbook(self) -> WorkbookProfile:
        return WorkbookProfile(self.path.name, self.table.kind, [self.sheet(s) for s in self.table.sheet_names])

    def sheet(self, sheet: str | None) -> SheetProfile:
        grid = self.table._grid(sheet)
        rows_with_data = [i for i, r in enumerate(grid, 1) if any(not is_blank(v) for v in r)]
        width = max((len(r) for r in grid), default=0)
        preview = []
        shown = rows_with_data if len(rows_with_data) <= SMALL_SHEET_ROWS else rows_with_data[:PREVIEW_ROWS]
        for number in shown:
            cells = [f"{get_column_letter(i)}{number}={_short(v)}" for i, v in enumerate(grid[number - 1], 1)
                     if not is_blank(v)]
            preview.append(cells)
        formulas = self._formulas.get(sheet, set())
        formula_columns = []
        for i in range(1, width + 1):
            letter = get_column_letter(i)
            filled = [n for n in rows_with_data[1:] if i <= len(grid[n - 1]) and not is_blank(grid[n - 1][i - 1])]
            if filled and sum(f"{letter}{n}" in formulas for n in filled) / len(filled) >= 0.5:
                formula_columns.append(letter)
        return SheetProfile(sheet, rows_with_data[-1] if rows_with_data else 0, width, preview,
                            len(formulas), formula_columns)

    def region(self, sheet: str | None, *, header_row: int | None, start: int, end: int | None = None,
               stop_at_blank: str | None = None) -> tuple[Region, list[ColumnProfile]]:
        region = self.table.region(sheet, header_row=header_row, start=start, end=end, stop_at_blank=stop_at_blank)
        formulas = self._formulas.get(sheet, set())
        return region, [self._column(region, letter, formulas) for letter in region.header]

    def _column(self, region: Region, letter: str, formulas: set[str]) -> ColumnProfile:
        values = [row.cells.get(letter) for row in region.rows]
        present = [v for v in values if not is_blank(v)]
        n = len(values) or 1
        texts = [str(v).strip() for v in present]
        formula_share = (
            sum(f"{letter}{row.number}" in formulas for row in region.rows) / n if formulas else 0.0
        )
        col_type = infer_type(present)
        return ColumnProfile(
            column=letter,
            header=region.header.get(letter),
            fill_rate=len(present) / n,
            distinct=len(set(texts)),
            type=col_type,
            samples=[_short(v) for v in list(dict.fromkeys(present))[:SAMPLES]],
            id_pattern=id_pattern(texts) if col_type in ("id", "text") else None,
            formula_share=round(formula_share, 3),
            max_length=max((len(t) for t in texts), default=0),
            delimiter_score=delimiter_score(texts),
        )


def _is_formula(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("=")
    return type(value).__name__ in ("ArrayFormula", "DataTableFormula")


def _short(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date):
        value = value.isoformat()
    if isinstance(value, str):
        value = " ".join(value.split())
        return value if len(value) <= PREVIEW_CELL_CHARS else value[: PREVIEW_CELL_CHARS - 1] + "…"
    return value


def infer_type(values: list[Any]) -> str:
    if not values:
        return "empty"
    kinds = Counter(_kind(v) for v in values)
    kind, count = kinds.most_common(1)[0]
    if count / len(values) < 0.9:
        numeric = kinds["int"] + kinds["float"]
        if numeric / len(values) >= 0.9:
            return "float"
        return "long_text" if any(len(str(v)) > LONG_TEXT for v in values) else "text"
    if kind == "text":
        texts = [str(v).strip() for v in values]
        if max(len(t) for t in texts) > LONG_TEXT:
            return "long_text"
        if id_pattern(texts) and len(set(texts)) >= 0.5 * len(texts):
            return "id"
    return kind


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "int" if value.is_integer() else "float"
    if isinstance(value, dt.datetime | dt.date):
        return "date"
    text = str(value).strip()
    if text.casefold() in BOOL_LABELS:
        return "bool"
    if re.fullmatch(r"-?[\d,. ]+%", text):
        return "percent"
    if re.fullmatch(r"-?\d+", text):
        return "int"
    if re.fullmatch(r"-?[\d,]*\.\d+|-?\d{1,3}(,\d{3})+(\.\d+)?", text):
        return "float"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4}", text):
        return "date"
    return "text"


def id_pattern(texts: list[str]) -> str | None:
    """ID shape(s) covering at least 80% of values, e.g. `S######` or `S###### | T#-DM###`.

    Digits become `#` and letters stay; if that needs more than 3 shapes, letters become `A` too.
    """
    candidates = [t for t in texts if " " not in t and len(t) <= 20]
    if not texts or not candidates:
        return None
    for shape_of in (lambda t: re.sub(r"\d", "#", t), lambda t: re.sub(r"[A-Za-z]", "A", re.sub(r"\d", "#", t))):
        shapes = Counter(shape_of(t) for t in candidates).most_common(3)
        covered, chosen = 0, []
        for shape, count in shapes:
            chosen.append(shape)
            covered += count
            if covered / len(texts) >= 0.8:
                return " | ".join(chosen) if all("#" in c for c in chosen) else None
    return None


def delimiter_score(texts: list[str]) -> float:
    """0..1: how much the values look like `key: value | key: value` records."""
    if not texts:
        return 0.0
    scores = [min(1.0, (t.count("|") + t.count(";") + len(_KV.findall(t))) / 4) for t in texts]
    return round(sum(scores) / len(scores), 3)
