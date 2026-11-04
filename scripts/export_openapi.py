"""Write the API's OpenAPI schema to ui/src/api/openapi.json, from which the UI's types are generated.

    uv run python scripts/export_openapi.py [OUT]
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ui" / "src" / "api" / "openapi.json"


def schema() -> dict:
    from scgraph.api.app import create_app
    from scgraph.config import Config

    with tempfile.TemporaryDirectory() as tmp:  # the schema does not depend on any data
        config = Config.from_env({"SCGRAPH_GRAPH_DB": str(Path(tmp) / "graph.sqlite"),
                                  "SCGRAPH_EVIDENCE_DIR": str(Path(tmp) / "evidence"),
                                  "SCGRAPH_LLM_DB": str(Path(tmp) / "llm.sqlite")})
        return create_app(config).openapi()


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
