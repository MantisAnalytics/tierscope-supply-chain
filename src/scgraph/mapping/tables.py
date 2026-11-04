"""Read xlsx and csv files as sheets of raw cell values, and cut regions out of them."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter

# Name of the single sheet a csv file exposes.
CSV_SHEET = None
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


def decode_text(data: bytes) -> tuple[str, str]:
    """Text of a csv export and the encoding read: UTF-8 (BOM or not), UTF-16 with a BOM, else Windows-1252.

    Windows-1252 leaves five byte values undefined; a file holding those is read as Latin-1, which decodes any byte.
    """
    if data.startswith(UTF16_BOMS):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8-sig"), "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


class TableError(ValueError):
    pass


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


@dataclass(frozen=True)
class Row:
    number: int
    cells: dict[str, Any]  # column letter -> raw value


@dataclass
class Region:
    file: str
    sheet: str | None
    header_row: int | None
    header: dict[str, str | None]  # column letter -> header text, in column order
    rows: list[Row] = field(default_factory=list)

    def column(self, name: str) -> str | None:
        """Column letter for a header text, or the name itself when it is a column letter of the region."""
        for letter, text in self.header.items():
            if text is not None and text == name:
                return letter
        for letter, text in self.header.items():
            if text is not None and text.strip().casefold() == name.strip().casefold():
                return letter
        return name if name in self.header else None

    def label(self, letter: str) -> str:
        return self.header.get(letter) or letter


class TableFile:
    """A workbook (cached formula values, never formulas) or a csv file seen as one sheet."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.name = self.path.name  # the file in reports and source locations; a batch names archive members by path
        suffix = self.path.suffix.lower()
        self._grids: dict[str | None, list[list[Any]]] = {}
        self.label: str | None = None  # names an in-memory table in source locations
        self.encoding: str | None = None  # csv: the text encoding read
        if suffix in (".xlsx", ".xlsm"):
            wb = openpyxl.load_workbook(self.path, data_only=True)
            self.kind = "xlsx"
            for ws in wb.worksheets:
                self._grids[ws.title] = [list(r) for r in ws.iter_rows(values_only=True)]
            wb.close()
        elif suffix in (".csv", ".tsv", ".txt"):
            self.kind = "csv"
            text, self.encoding = decode_text(self.path.read_bytes())
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            self._grids[CSV_SHEET] = [list(r) for r in csv.reader(io.StringIO(text, newline=""), dialect)]
        else:
            raise TableError(f"unsupported table file {self.path.name}")

    @classmethod
    def from_rows(cls, name: str, rows: list[list[Any]], label: str | None = None) -> TableFile:
        """A table that is not a file of its own, such as a table read from a PDF page; behaves like a csv."""
        table = cls.__new__(cls)
        table.path, table.kind, table.label, table.encoding = Path(name), "csv", label, None
        table.name = name
        table._grids = {CSV_SHEET: [list(r) for r in rows]}
        return table

    @property
    def sheet_names(self) -> list[str | None]:
        return list(self._grids)

    def _grid(self, sheet: str | None) -> list[list[Any]]:
        if self.kind == "csv":
            return self._grids[CSV_SHEET]
        if sheet not in self._grids:
            raise TableError(f"sheet {sheet!r} not found in {self.name}")
        return self._grids[sheet]

    def cell(self, sheet: str | None, ref: str) -> Any:
        letters = "".join(ch for ch in ref if ch.isalpha())
        row = int(ref[len(letters):])
        grid = self._grid(sheet)
        col = column_index_from_string(letters)
        if row > len(grid) or col > len(grid[row - 1]):
            return None
        return grid[row - 1][col - 1]

    def region(
        self,
        sheet: str | None,
        *,
        header_row: int | None,
        start: int,
        end: int | None = None,
        stop_at_blank: str | None = None,
    ) -> Region:
        """Rows `start..end` (1-based, inclusive), or until the first row blank in `stop_at_blank`.

        Wholly blank rows are skipped. Columns are those with a header or with data in the region.
        """
        grid = self._grid(sheet)
        width = max((len(r) for r in grid), default=0)
        header_cells = grid[header_row - 1] if header_row and header_row <= len(grid) else []
        header = {
            get_column_letter(i + 1): (str(v).strip() if not is_blank(v) else None)
            for i, v in enumerate(header_cells + [None] * (width - len(header_cells)))
        }
        region = Region(file=self.name, sheet=sheet if self.kind == "xlsx" else self.label,
                        header_row=header_row, header=header)
        stop_col = region.column(stop_at_blank) if stop_at_blank else None
        if stop_at_blank and stop_col is None:
            raise TableError(f"stop_at_blank column {stop_at_blank!r} not found in {sheet!r}")
        last = min(end or len(grid), len(grid))
        for number in range(start, last + 1):
            values = grid[number - 1]
            cells = {get_column_letter(i + 1): v for i, v in enumerate(values)}
            if stop_col and is_blank(cells.get(stop_col)):
                break
            if all(is_blank(v) for v in values):
                continue
            region.rows.append(Row(number, cells))
        used = {k for row in region.rows for k, v in row.cells.items() if not is_blank(v)}
        region.header = {k: v for k, v in header.items() if v is not None or k in used}
        return region
