"""The committed web app build matches the UI sources, so running the app needs no Node."""

import importlib.util
import json
import shutil

from conftest import ROOT

spec = importlib.util.spec_from_file_location("ui_build_info", ROOT / "scripts" / "ui_build_info.py")
ui_build_info = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui_build_info)


def test_committed_build_is_fresh():
    info = ui_build_info.BUILD_INFO
    assert info.is_file(), "no committed build: run `cd ui && npm run build`"
    assert (info.parent / "index.html").is_file()
    assert json.loads(info.read_text())["source_hash"] == ui_build_info.source_hash(), \
        "the committed build is older than ui/: run `cd ui && npm run build` and commit src/scgraph/api/static/"


def test_hash_follows_the_sources(tmp_path):
    ui = tmp_path / "ui"
    shutil.copytree(ROOT / "ui", ui, ignore=shutil.ignore_patterns("node_modules", "dist"))
    before = ui_build_info.source_hash(ui)
    assert before == ui_build_info.source_hash()
    (ui / "tests").mkdir(exist_ok=True)
    (ui / "tests" / "extra.test.ts").write_text("// tests are not part of the build\n")
    assert ui_build_info.source_hash(ui) == before
    main = ui / "src" / "main.tsx"
    main.write_bytes(main.read_bytes().replace(b"\n", b"\r\n"))  # a CRLF checkout builds the same app
    assert ui_build_info.source_hash(ui) == before
    main.write_text(main.read_text() + "\n// changed\n")
    assert ui_build_info.source_hash(ui) != before
