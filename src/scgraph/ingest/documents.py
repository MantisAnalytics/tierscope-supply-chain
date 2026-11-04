"""Documents that are not spreadsheets: PDF and text, as tables and located text blocks, and text chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from scgraph.graph.facts import SourceLocation
from scgraph.mapping.tables import decode_text

CHUNK_CHARS = 4000  # about 1,000 tokens
TEXT_SUFFIXES = (".txt", ".md")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


@dataclass
class DocTable:
    page: int | None
    index: int  # 1-based position on its first page
    rows: list[list[str]]

    @property
    def name(self) -> str:
        return f"page {self.page} table {self.index}" if self.page else f"table {self.index}"


@dataclass
class TextBlock:
    text: str
    page: int | None = None  # PDF page; None for text files
    first_line: int = 1  # text files: line number of the block's first character


@dataclass
class Document:
    path: Path
    kind: str  # pdf | text
    tables: list[DocTable] = field(default_factory=list)
    blocks: list[TextBlock] = field(default_factory=list)
    name: str = ""  # the file in reports and source locations; defaults to the file name

    def __post_init__(self) -> None:
        self.name = self.name or self.path.name

    def location(self, block: TextBlock, start: int, end: int, quote: str | None = None) -> SourceLocation:
        """Character span [start, end) of a block as a source location: page and span, or line range."""
        if block.page is not None:
            return SourceLocation(file=self.name, page=block.page, span=(start, end), quote=quote)
        first = block.first_line + block.text.count("\n", 0, start)
        last = block.first_line + block.text.count("\n", 0, max(start, end - 1))
        return SourceLocation(file=self.name, lines=(first, last), quote=quote)


@dataclass
class Chunk:
    block: TextBlock
    start: int  # offset of the chunk in its block
    text: str


def read_document(path: str | Path) -> Document:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(path)
    if suffix in TEXT_SUFFIXES:
        return read_text(path)
    raise ValueError(f"unsupported document {path.name}; expected .pdf, .txt or .md")


def read_text(path: Path) -> Document:
    text, _ = decode_text(path.read_bytes())
    return Document(path, "text", blocks=[TextBlock(text)] if text.strip() else [])


def read_pdf(path: Path) -> Document:
    import pdfplumber

    doc = Document(path, "pdf")
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            found = page.find_tables()
            for index, table in enumerate(found, start=1):
                rows = [[" ".join((cell or "").split()) for cell in row] for row in table.extract()]
                previous = doc.tables[-1] if doc.tables else None
                if (index == 1 and previous is not None and previous.page == number - 1 and rows
                        and previous.rows and rows[0] == previous.rows[0]):
                    previous.rows.extend(rows[1:])  # a table continued with its header repeated
                    continue
                doc.tables.append(DocTable(number, index, rows))
            boxes = [t.bbox for t in found]
            outside = page.filter(lambda obj, boxes=boxes: obj.get("object_type") != "char"
                                  or not any(_inside(obj, b) for b in boxes))
            text = outside.extract_text() or ""
            if text.strip():
                doc.blocks.append(TextBlock(text, page=number))
    return doc


def _inside(obj: dict, bbox: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
    return x0 <= cx <= x1 and top <= cy <= bottom


def chunks(block: TextBlock, max_chars: int = CHUNK_CHARS) -> list[Chunk]:
    """Pack whole paragraphs (split at blank lines) up to `max_chars`; split an oversized paragraph at line ends.

    Never cuts a line, so sentences and table rows written on one line stay whole.
    """
    pieces: list[tuple[int, int]] = []  # [start, end) of paragraphs or, for long ones, lines
    for m in re.finditer(r"\S(?:.*?\S)??(?=\n[ \t]*\n|\s*\Z)", block.text, flags=re.S):
        start, end = m.start(), m.end()
        if end - start <= max_chars:
            pieces.append((start, end))
            continue
        for line in re.finditer(r"[^\n]+", block.text[start:end]):
            pieces.append((start + line.start(), start + line.end()))
    out: list[Chunk] = []
    current: tuple[int, int] | None = None
    for start, end in pieces:
        if current and end - current[0] <= max_chars:
            current = (current[0], end)
        else:
            if current:
                out.append(Chunk(block, current[0], block.text[current[0]:current[1]]))
            current = (start, end)
    if current:
        out.append(Chunk(block, current[0], block.text[current[0]:current[1]]))
    return out


def normalise_text(text: str) -> str:
    return " ".join(text.translate(_QUOTES).split())


def find_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Span of `quote` in `text`, tolerant to whitespace runs and typographic quotes; None if absent."""
    words = normalise_text(quote).split(" ")
    if not words or words == [""]:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    m = re.search(pattern, text.translate(_QUOTES))
    return (m.start(), m.end()) if m else None
