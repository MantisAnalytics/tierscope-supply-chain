"""Chip-market shortage curves from ShockBench-Flow episodes and the agent's network benchmark results.

`benchmarks/shockbench.json` is written by `benchmark/scripts/export_shockbench.py` in this repository, which runs
ShockBench in its own environment (it needs Python 3.13 and numba); this app only reads it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("benchmarks/shockbench.json")
DEFAULT_EPISODE = 16
CALM = 0.99


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any] | None:
    p = Path(path)
    return json.loads(p.read_text()) if p.is_file() else None


def data(path: Path = DEFAULT_PATH) -> dict[str, Any] | None:
    return _load(str(path))


def shortage_window(fill: list[float]) -> list[float]:
    """The first run of weeks below full supply, ending once two weeks in a row are back at full supply."""
    start = next((i for i, f in enumerate(fill) if f < CALM), None)
    if start is None:
        return []
    end = len(fill)
    for i in range(start, len(fill) - 1):
        if fill[i] >= CALM and fill[i + 1] >= CALM:
            end = i
            break
    return [round(float(f), 4) for f in fill[start:end]]


def curve(episode: int = DEFAULT_EPISODE, path: Path = DEFAULT_PATH) -> list[float] | None:
    d = data(path)
    if d is None or str(episode) not in d["curves"]:
        return None
    return shortage_window(d["curves"][str(episode)]["fill"]) or None


def episodes(path: Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    """Episodes with a shortage, their depth and length, for choosing one in the app."""
    d = data(path)
    if d is None:
        return []
    out = []
    for ep, c in d["curves"].items():
        window = shortage_window(c["fill"])
        if window:
            out.append({"episode": int(ep), "weeks": len(window), "low": min(window)})
    return sorted(out, key=lambda e: e["episode"])


def network_results(path: Path = DEFAULT_PATH) -> dict[str, Any] | None:
    d = data(path)
    return d.get("network_results") if d else None
