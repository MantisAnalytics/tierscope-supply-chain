"""Summary rows under a table (TOTAL, Subtotal, Summe, ...): found deterministically and cut off the region."""

from __future__ import annotations

import copy
import re
from typing import Any

from scgraph.mapping.tables import TableError, TableFile, is_blank

# A summary label alone in its cell, any case, with optional punctuation around it: "TOTAL", "Grand total:",
# "Sub-total", "Summe", "Gesamtsumme", "Итого".
SUMMARY_LABEL = re.compile(
    r"^[\W_]*(?:(?:grand|sub|overall)[\s-]*)?"
    r"(?:totals?|sum|sums|summe|zwischensumme|gesamt|gesamtsumme|insgesamt|totale|somme|totaal|suma|razem"
    r"|итого|всего|разом|усього)[\W_]*$",
    re.IGNORECASE,
)
MAX_SUMMARY_ROWS = 5  # trailing rows looked at


def is_summary_label(value: Any) -> bool:
    return isinstance(value, str) and bool(SUMMARY_LABEL.match(value.strip()))


def key_headers(spec: dict[str, Any]) -> list[str]:
    """Header texts of the region's key columns: its declared key columns, else the columns its rows are keyed by."""
    region = spec["regions"][0]
    headers = list(region.get("key_columns", {}))
    if headers:
        return headers
    describes = spec.get("metadata", {}).get("describes")
    for produce in region.get("produces", []):
        if describes in (produce.get("entity"), produce.get("relation")):
            for ref in ("key", "from", "to"):
                refs = produce.get(ref)
                refs = refs if isinstance(refs, list) else [refs] if refs else []
                headers += [r["column"] for r in refs if isinstance(r, dict) and "column" in r]
    return headers


def exclude_summary_rows(spec: dict[str, Any], table: TableFile) -> dict[str, Any]:
    """The spec with its region ended above trailing summary rows, and `excluded_rows` naming them.

    Returns the spec unchanged (the same object) when there is nothing to exclude.
    """
    if not spec.get("regions"):
        return spec
    region_spec = spec["regions"][0]
    rows = region_spec.get("rows", {})
    try:
        region = table.region(region_spec.get("sheet"), header_row=region_spec.get("header_row"),
                              start=rows.get("start", (region_spec.get("header_row") or 0) + 1), end=rows.get("end"),
                              stop_at_blank=rows.get("stop_at_blank"))
    except TableError:
        return spec
    letters = [letter for h in key_headers(spec) if (letter := region.column(h))]
    if not letters:
        return spec
    excluded = []
    for row in reversed(region.rows[-MAX_SUMMARY_ROWS:]):
        labels = [row.cells.get(letter) for letter in letters if not is_blank(row.cells.get(letter))]
        if not labels or not all(is_summary_label(v) for v in labels):
            break
        excluded.append({"row": row.number, "label": str(labels[0]).strip(),
                         "reason": "summary row under the table"})
    if not excluded:
        return spec
    spec = copy.deepcopy(spec)
    region_spec = spec["regions"][0]
    first = min(e["row"] for e in excluded)
    region_spec.setdefault("rows", {})["end"] = first - 1
    found = [{"sheet": region_spec.get("sheet"), **e} for e in sorted(excluded, key=lambda e: e["row"])]
    rows_found = {e["row"] for e in found}
    spec["excluded_rows"] = [e for e in spec.get("excluded_rows", []) if e["row"] not in rows_found] + found
    return spec
