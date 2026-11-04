
from scgraph.cli import main


def run(argv):
    return main(argv, load_env=lambda: None)


def test_schema_command(capsys):
    assert run(["graph", "schema"]) == 0
    out = capsys.readouterr().out
    assert "domain core" in out and "domain tiers" in out and "domain risk" in out
    assert "relation CONTAINS: Component -> Component (identity: assembly:Assembly)" in out


def test_commands_without_graph(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SCGRAPH_GRAPH_DB", str(tmp_path / "none.sqlite"))
    assert run(["graph", "stats"]) == 1
    assert "no graph yet" in capsys.readouterr().out
