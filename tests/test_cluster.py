"""Tests for grouping changed graph nodes into connected components."""

import json

from code_review_graph.graph import GraphEdge, GraphNode
from code_review_graph.tools.cluster import (
    cluster_changed_files,
    cluster_connected_nodes,
    save_diff_clusters,
    split_diff_by_file,
)


def _node(name: str) -> GraphNode:
    return GraphNode(
        id=hash(name),
        kind="Function",
        name=name,
        qualified_name=f"app.py::{name}",
        file_path="app.py",
        line_start=1,
        line_end=2,
        language="python",
        parent_name=None,
        params=None,
        return_type=None,
        is_test=False,
        file_hash=None,
        extra={},
    )


def _edge(
    source: GraphNode,
    target: GraphNode,
    kind: str = "CALLS",
) -> GraphEdge:
    return GraphEdge(
        id=hash((source.qualified_name, target.qualified_name)),
        kind=kind,
        source_qualified=source.qualified_name,
        target_qualified=target.qualified_name,
        file_path="app.py",
        line=1,
        extra={},
    )


class _Store:
    def __init__(self, edges: list[GraphEdge]) -> None:
        self.edges = edges

    def get_edges_among(self, qualified_names: set[str]) -> list[GraphEdge]:
        return [
            edge
            for edge in self.edges
            if edge.source_qualified in qualified_names
            and edge.target_qualified in qualified_names
        ]


def test_cluster_connected_nodes_partitions_all_nodes() -> None:
    first = _node("first")
    second = _node("second")
    third = _node("third")
    isolated = _node("isolated")
    store = _Store([_edge(second, first), _edge(second, third)])

    clusters = cluster_connected_nodes(  # type: ignore[arg-type]
        store,
        [first, second, third, isolated],
    )

    assert [set(node.name for node in cluster) for cluster in clusters] == [
        {"first", "second", "third"},
        {"isolated"},
    ]


def test_cluster_connected_nodes_handles_empty_input() -> None:
    assert cluster_connected_nodes(  # type: ignore[arg-type]
        _Store([]),
        [],
    ) == []


def test_cluster_connected_nodes_excludes_file_nodes() -> None:
    file_node = _node("app.py")
    file_node.kind = "File"
    function = _node("function")

    clusters = cluster_connected_nodes(  # type: ignore[arg-type]
        _Store([_edge(file_node, function, "CONTAINS")]),
        [file_node, function],
    )

    assert [[node.name for node in cluster] for cluster in clusters] == [
        ["function"],
    ]


def test_cluster_connected_nodes_ignores_contains_edges() -> None:
    container = _node("container")
    first = _node("first")
    second = _node("second")
    store = _Store([
        _edge(container, first, "CONTAINS"),
        _edge(container, second, "CONTAINS"),
    ])

    clusters = cluster_connected_nodes(  # type: ignore[arg-type]
        store,
        [container, first, second],
    )

    assert [set(node.name for node in cluster) for cluster in clusters] == [
        {"container"},
        {"first"},
        {"second"},
    ]


def test_cluster_changed_files_projects_node_edges_to_files(tmp_path) -> None:
    first = _node("first")
    first.file_path = "a.py"
    second = _node("second")
    second.file_path = "b.py"
    isolated = _node("isolated")
    isolated.file_path = "c.py"

    class _FileStore(_Store):
        def get_nodes_by_file(self, file_path: str) -> list[GraphNode]:
            return [
                node for node in (first, second, isolated)
                if node.file_path == file_path
            ]

        def get_files_matching(self, suffix: str) -> list[str]:
            return []

    clusters = cluster_changed_files(
        _FileStore([_edge(first, second)]),  # type: ignore[arg-type]
        ["a.py", "b.py", "c.py", "missing.py"],
        tmp_path,
    )

    assert clusters == [["a.py", "b.py"], ["c.py"], ["missing.py"]]


def test_split_diff_by_file_preserves_complete_sections() -> None:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n"
        "+++ b/src/a.py\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/b.py b/src/b.py\n"
        "--- a/src/b.py\n"
        "+++ b/src/b.py\n"
        "@@ -2 +2 @@\n-old\n+new\n"
    )

    sections = split_diff_by_file(diff)

    assert set(sections) == {"src/a.py", "src/b.py"}
    assert sections["src/a.py"].startswith("diff --git a/src/a.py")
    assert "src/b.py" not in sections["src/a.py"]


def test_save_diff_clusters_deduplicates_files_and_stacks_diff(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    output_dir = tmp_path / "diff-clusters"
    first = _node("first")
    first.file_path = str(repo_root / "src/a.py")
    second = _node("second")
    second.file_path = str(repo_root / "src/a.py")
    third = _node("third")
    third.file_path = str(repo_root / "src/b.py")
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/src/b.py b/src/b.py\n"
        "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-old\n+new\n"
    )

    paths = save_diff_clusters(
        [[first, second, third]],
        diff,
        output_dir,
        repo_root,
    )

    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["changed_files"] == ["src/a.py", "src/b.py"]
    assert payload["diff"].count("diff --git") == 2


def test_save_diff_clusters_selects_hunks_by_node_range(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    output_dir = tmp_path / "diff-clusters"
    first = _node("first")
    first.file_path = str(repo_root / "src/app.py")
    first.line_start = 10
    first.line_end = 20
    second = _node("second")
    second.file_path = str(repo_root / "src/app.py")
    second.line_start = 40
    second.line_end = 50
    diff = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -11 +11 @@\n-old first\n+new first\n"
        "@@ -41 +41 @@\n-old second\n+new second\n"
    )

    paths = save_diff_clusters(
        [[first], [second]],
        diff,
        output_dir,
        repo_root,
    )

    first_payload = json.loads(paths[0].read_text(encoding="utf-8"))
    second_payload = json.loads(paths[1].read_text(encoding="utf-8"))
    assert "new first" in first_payload["diff"]
    assert "new second" not in first_payload["diff"]
    assert "new second" in second_payload["diff"]
    assert "new first" not in second_payload["diff"]
    assert first_payload["diff"].startswith("diff --git a/src/app.py")
    assert second_payload["diff"].startswith("diff --git a/src/app.py")
