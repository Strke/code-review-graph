"""Tests for grouping changed graph nodes into connected components."""

import json

from code_review_graph.graph import GraphEdge, GraphNode
from code_review_graph.tools.cluster import (
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


def _edge(source: GraphNode, target: GraphNode) -> GraphEdge:
    return GraphEdge(
        id=hash((source.qualified_name, target.qualified_name)),
        kind="CALLS",
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
