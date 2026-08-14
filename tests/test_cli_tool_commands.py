"""CLI wrappers for graph tools reconciled from PR #95."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

import code_review_graph.tools  # noqa: F401 - exposes lazy patch targets
from code_review_graph import cli
from code_review_graph.graph import GraphStore
from code_review_graph.parser import EdgeInfo, NodeInfo


@pytest.mark.parametrize(
    ("arguments", "tool_name", "expected"),
    [
        (
            ["query", "callers_of", "target"],
            "query_graph",
            {"pattern": "callers_of", "target": "target"},
        ),
        (
            ["impact", "--files", "a.py", "b.py", "--depth", "3", "--max-results", "20"],
            "get_impact_radius",
            {
                "changed_files": ["a.py", "b.py"],
                "max_depth": 3,
                "max_results": 20,
                "base": "HEAD~1",
            },
        ),
        (
            ["search", "login", "--kind", "Function", "--limit", "7"],
            "semantic_search_nodes",
            {"query": "login", "kind": "Function", "limit": 7},
        ),
        (
            ["flows", "--sort", "depth", "--limit", "9", "--kind", "Function"],
            "list_flows",
            {"sort_by": "depth", "limit": 9, "kind": "Function"},
        ),
        (
            ["flow", "--id", "7", "--source"],
            "get_flow",
            {"flow_id": 7, "flow_name": None, "include_source": True},
        ),
        (
            ["communities", "--sort", "cohesion", "--min-size", "3"],
            "list_communities_func",
            {"sort_by": "cohesion", "min_size": 3},
        ),
        (
            ["community", "--name", "parser", "--members"],
            "get_community_func",
            {
                "community_name": "parser",
                "community_id": None,
                "include_members": True,
            },
        ),
        (
            ["architecture", "--detail-level", "standard"],
            "get_architecture_overview_func",
            {"detail_level": "standard"},
        ),
        (
            ["large-functions", "--min-lines", "80", "--kind", "Class", "--limit", "4"],
            "find_large_functions",
            {
                "min_lines": 80,
                "kind": "Class",
                "file_path_pattern": None,
                "limit": 4,
            },
        ),
        (
            ["refactor", "dead_code", "--kind", "Function", "--path", "src/"],
            "refactor_func",
            {
                "mode": "dead_code",
                "old_name": None,
                "new_name": None,
                "kind": "Function",
                "file_pattern": "src/",
            },
        ),
    ],
)
def test_tool_command_forwards_typed_arguments_as_json(
    arguments, tool_name, expected, tmp_path, monkeypatch, capsys,
):
    repo = tmp_path / "repo"
    nested = repo / "src" / "nested"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "graph.db").touch()
    monkeypatch.setenv("CRG_DATA_DIR", str(data_dir))
    argv = ["code-review-graph", *arguments, "--repo", str(nested)]
    result = {"status": "ok", "tool": tool_name}

    with patch.object(sys, "argv", argv):
        with patch(f"code_review_graph.tools.{tool_name}", return_value=result) as tool:
            cli.main()

    assert json.loads(capsys.readouterr().out) == result
    tool.assert_called_once_with(repo_root=str(repo), **expected)


@pytest.mark.parametrize(
    "arguments",
    [
        ["flow"],
        ["flow", "--id", "1", "--name", "duplicate"],
        ["community"],
        ["community", "--id", "1", "--name", "duplicate"],
        ["refactor", "rename", "--old-name", "only-old"],
        ["impact", "--depth", "-1"],
        ["search", "query", "--limit", "0"],
    ],
)
def test_tool_commands_reject_invalid_or_ambiguous_arguments(arguments):
    with patch.object(sys, "argv", ["code-review-graph", *arguments]):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
    assert exc_info.value.code == 2


def test_tool_command_missing_graph_exits_nonzero(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.setenv("CRG_DATA_DIR", str(tmp_path / "missing"))

    with patch.object(
        sys,
        "argv",
        ["code-review-graph", "query", "callers_of", "target", "--repo", str(repo)],
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

    assert exc_info.value.code == 1
    assert "No graph found" in capsys.readouterr().err


def test_diff_cluster_command_writes_connected_components(
    tmp_path, capsys,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    input_path = tmp_path / "changes.json"
    diff = "".join(
        f"diff --git a/{name}.py b/{name}.py\n"
        f"--- a/{name}.py\n"
        f"+++ b/{name}.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        for name in ("a", "b", "c")
    )
    input_path.write_text(
        json.dumps({
            "changed_files": ["a.py", "b.py", "c.py"],
            "diff": diff,
        }),
        encoding="utf-8",
    )

    with GraphStore(repo / ".code-review-graph" / "graph.db") as store:
        for name in ("a", "b", "c"):
            store.upsert_node(NodeInfo(
                kind="Function",
                name=name,
                file_path=str(repo / f"{name}.py"),
                line_start=1,
                line_end=1,
                language="python",
            ))
        store.upsert_edge(EdgeInfo(
            kind="CALLS",
            source=f"{repo / 'a.py'}::a",
            target=f"{repo / 'b.py'}::b",
            file_path=str(repo / "a.py"),
            line=1,
        ))
        store.commit()

    with patch.object(
        sys,
        "argv",
        [
            "code-review-graph",
            "diff-cluster",
            str(input_path),
            "--repo",
            str(repo),
        ],
    ):
        cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert len(result["cluster_files"]) == 2

    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "diff-clusters").glob("cluster-*.json"))
    ]
    assert payloads[0]["changed_files"] == ["a.py", "b.py"]
    assert payloads[0]["diff"].count("diff --git") == 2
    assert payloads[1]["changed_files"] == ["c.py"]
    assert payloads[1]["diff"].count("diff --git") == 1


def test_file_cluster_command_returns_file_groups(tmp_path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    input_path = tmp_path / "changes.json"
    input_path.write_text(
        json.dumps({"changed_files": ["a.py", "b.py", "c.py"], "diff": ""}),
        encoding="utf-8",
    )

    with GraphStore(repo / ".code-review-graph" / "graph.db") as store:
        for name in ("a", "b", "c"):
            store.upsert_node(NodeInfo(
                kind="Function",
                name=name,
                file_path=str(repo / f"{name}.py"),
                line_start=1,
                line_end=1,
                language="python",
            ))
        store.upsert_edge(EdgeInfo(
            kind="CALLS",
            source=f"{repo / 'a.py'}::a",
            target=f"{repo / 'b.py'}::b",
            file_path=str(repo / "a.py"),
            line=1,
        ))
        store.commit()

    with patch.object(
        sys,
        "argv",
        ["code-review-graph", "file-cluster", str(input_path), "--repo", str(repo)],
    ):
        cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert result["clusters"] == [["a.py", "b.py"], ["c.py"]]
