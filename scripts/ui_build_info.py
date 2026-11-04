"""The UI source hash: what the committed build in src/scgraph/api/static/ was built from.

    uv run python scripts/ui_build_info.py            print the hash of ui/
    uv run python scripts/ui_build_info.py --write    write src/scgraph/api/static/build-info.json (`npm run build`)

The hash covers what goes into the build: ui/src, ui/public, index.html, package.json, package-lock.json and the
top-level config files; not node_modules, tests or docs. Line endings are normalised so a checkout with CRLF
gives the same hash.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
BUILD_INFO = ROOT / "src" / "scgraph" / "api" / "static" / "build-info.json"
TREES = ("src", "public")
FILES = ("index.html", "package.json", "package-lock.json", "components.json")
CONFIGS = ("tsconfig*.json", "vite.config.*", "*.config.ts", "*.config.js", "*.config.mjs")


def sources(ui: Path = UI) -> list[Path]:
    found = {ui / name for name in FILES if (ui / name).is_file()}
    found |= {p for pattern in CONFIGS for p in ui.glob(pattern) if p.is_file()}
    found |= {p for tree in TREES if (ui / tree).is_dir() for p in (ui / tree).rglob("*") if p.is_file()}
    return sorted(found)


def source_hash(ui: Path = UI) -> str:
    digest = hashlib.sha256()
    for path in sources(ui):
        digest.update(path.relative_to(ui).as_posix().encode() + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    value = source_hash()
    if argv[:1] == ["--write"]:
        out = Path(argv[1]) if len(argv) > 1 else BUILD_INFO
        out.parent.mkdir(parents=True, exist_ok=True)
        info = {"source_hash": value, "built_at": datetime.now(UTC).isoformat(timespec="seconds")}
        out.write_text(json.dumps(info, indent=1) + "\n")
        print(f"wrote {out}")
    else:
        print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
