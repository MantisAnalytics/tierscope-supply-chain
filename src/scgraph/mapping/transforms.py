"""Value transforms from raw cells (xlsx numbers or csv text) to typed field values.

A transform is a name ("str", "trim", "int", "float", "number", "bool", "date"), a one-key object with
options ({"number": {...}}, {"bool": {...}}, {"date": {...}}, {"empty": [...]}, {"constant": v}), or a
list applied in order. Blank cells are missing, not failures.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from scgraph.mapping.tables import is_blank


class TransformError(ValueError):
    pass


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()

NAMES = ("str", "trim", "int", "float", "number", "bool", "date", "scalar")
OPTION_NAMES = ("number", "int", "bool", "date", "empty", "constant")
DEFAULT_TRUE = ("yes", "y", "true", "1", "x")
DEFAULT_FALSE = ("no", "n", "false", "0")
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d")


def check_transform(spec: Any) -> None:
    """Raise TransformError if `spec` is not a valid transform description."""
    if spec is None:
        return
    if isinstance(spec, list):
        for part in spec:
            check_transform(part)
        return
    if isinstance(spec, str):
        if spec not in NAMES:
            raise TransformError(f"unknown transform {spec!r}")
        return
    if isinstance(spec, dict) and len(spec) == 1:
        (name, opts), = spec.items()
        if name not in OPTION_NAMES:
            raise TransformError(f"unknown transform {name!r}")
        if name == "empty" and not isinstance(opts, list):
            raise TransformError("empty transform takes a list of marker values")
        if name == "bool" and not (isinstance(opts, dict) and set(opts) <= {"true", "false"}):
            raise TransformError("bool transform takes {'true': [...], 'false': [...]}")
        return
    raise TransformError(f"invalid transform {spec!r}")


def default_for(field_type: str) -> str:
    return {"str": "str", "int": "int", "float": "number", "bool": "bool", "date": "date"}.get(field_type, "scalar")


def apply(spec: Any, raw: Any, field_type: str = "scalar") -> Any:
    """Transform a raw cell value; returns MISSING for blank cells and empty markers."""
    if spec is None:
        spec = default_for(field_type)
    steps = spec if isinstance(spec, list) else [spec]
    value = raw
    for step in steps:
        name, opts = (step, {}) if isinstance(step, str) else next(iter(step.items()))
        if name == "constant":
            value = opts
            continue
        if is_blank(value):
            return MISSING
        value = _STEPS[name](value, opts)
        if value is MISSING:
            return MISSING
    return _fit(value, field_type)


def _fit(value: Any, field_type: str) -> Any:
    """Whole floats into int fields (`1,0` read as a number is tier 1); other fractions fail here, in the dry run,
    instead of being rejected at load."""
    if field_type == "int" and isinstance(value, float):
        if not value.is_integer():
            raise TransformError(f"not an integer: {value!r}")
        return int(value)
    if field_type == "float" and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def _str(value: Any, _opts: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return str(value).strip()


def _number(value: Any, opts: Any) -> float:
    if isinstance(value, bool):
        raise TransformError(f"not a number: {value!r}")
    if isinstance(value, int | float):
        return float(value)
    opts = opts or {}
    text = str(value).strip().replace(" ", "").replace(" ", "")
    percent = text.endswith("%")
    if percent:
        text = text[:-1]
    thousands, decimal = opts.get("thousands", ","), opts.get("decimal", ".")
    if thousands:
        text = text.replace(thousands, "")
    if decimal != ".":
        text = text.replace(decimal, ".")
    try:
        number = float(text)
    except ValueError:
        raise TransformError(f"not a number: {value!r}") from None
    if percent and opts.get("percent", "fraction") == "fraction":
        number /= 100
    return number


def _int(value: Any, opts: Any) -> int:
    number = _number(value, opts)
    if not number.is_integer():
        raise TransformError(f"not an integer: {value!r}")
    return int(number)


def _bool(value: Any, opts: Any) -> bool:
    if isinstance(value, bool):
        return value
    opts = opts or {}
    label = _str(value, None).casefold()
    true = [str(t).casefold() for t in opts.get("true", DEFAULT_TRUE)]
    false = [str(f).casefold() for f in opts.get("false", DEFAULT_FALSE)]
    if label in true:
        return True
    if label in false:
        return False
    raise TransformError(f"not a boolean label: {value!r}")


def _date(value: Any, opts: Any) -> str:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    formats = [opts["format"]] if isinstance(opts, dict) and "format" in opts else list(DATE_FORMATS)
    for fmt in formats:
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise TransformError(f"not a date: {value!r}")


def _empty(value: Any, markers: list) -> Any:
    folded = {str(m).strip().casefold() for m in markers}
    return MISSING if _str(value, None).casefold() in folded else value


def _scalar(value: Any, _opts: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


_STEPS = {
    "str": _str,
    "trim": _str,
    "number": _number,
    "float": _number,
    "int": _int,
    "bool": _bool,
    "date": _date,
    "empty": _empty,
    "scalar": _scalar,
}
