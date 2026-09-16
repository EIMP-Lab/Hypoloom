from __future__ import annotations

import json

import pytest

from hypoloom import cli
from hypoloom.graph_export import export_graph, graph_html
from test_web_observer import _create_fresh_run, _row_census


def test_graph_html_is_offline_escaped_and_preserves_exact_relations():
    graph = {"nodes": [
        {"hypothesis_id": "h-1", "proposer_role": "expert-a", "state": "SUPPORTED", "claim": "<script>alert(1)</script>"},
        {"hypothesis_id": "h-2", "proposer_role": "expert-b", "state": "REFUTED", "claim": "child"}],
        "edges": [{"source": "h-1", "target": "h-2", "relation": "extends"}]}
    rendered = graph_html("run<unsafe>", graph, "2026-09-06")
    assert "<svg" in rendered and 'marker-end="url(#arrow)"' in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<script" not in rendered and "https://" not in rendered
    assert "SUPPORTED" in rendered and "REFUTED" in rendered and "extends" in rendered
    assert "run&lt;unsafe&gt;" in rendered


@pytest.mark.parametrize("suffix", ["html", "json"])
def test_graph_export_reads_real_run_without_writes_and_refuses_overwrite(tmp_path, suffix):
    root = tmp_path / "state"
    _create_fresh_run(root, "observed")
    before = _row_census(root / "state.sqlite")
    target = tmp_path / ("graph." + suffix)
    result = export_graph(root, "observed", target)
    assert result == {"run_id": "observed", "path": str(target), "nodes": 0, "edges": 0, "non_authoritative": True}
    assert target.stat().st_mode & 0o777 == 0o600
    if suffix == "json": assert json.loads(target.read_text())["graph"] == {"nodes": [], "edges": []}
    else: assert "No accepted hypotheses" in target.read_text()
    original = target.read_bytes()
    with pytest.raises(FileExistsError): export_graph(root, "observed", target)
    with pytest.raises(ValueError, match="outside"):
        export_graph(root, "observed", root / "graph.html")
    assert target.read_bytes() == original and _row_census(root / "state.sqlite") == before


def test_cli_graph_export_is_offline_and_has_no_host_lifecycle(tmp_path):
    root = tmp_path / "state"
    _create_fresh_run(root, "observed")
    assert cli.main(["graph", "--state-root", str(root), "--run-id", "observed",
                     "--export", str(tmp_path / "graph.html")]) == 0
    assert not (root / "control.sock").exists()
