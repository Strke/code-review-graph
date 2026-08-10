"""Tools for grouping changed code into diff clusters."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..changes import _parse_unified_diff, map_changes_to_nodes
from ..graph import GraphNode, GraphStore
from ._common import _get_store

# ---------------------------------------------------------------------------
# Diff cluster tools
# ---------------------------------------------------------------------------


def load_diff_input(json_path: str) -> tuple[list[str], str]:
    """Load and validate changed files and their combined diff.

    Args:
        json_path: Path to a JSON file containing a ``changed_files`` string
            array and a ``diff`` string.

    Returns:
        A tuple containing the changed file names and combined diff.

    Raises:
        ValueError: If the file cannot be read or its contents are invalid.
    """
    path = Path(json_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object.")

    changed_files = payload.get("changed_files")
    if not isinstance(changed_files, list) or not all(
        isinstance(file_name, str) for file_name in changed_files
    ):
        raise ValueError("'changed_files' must be an array of strings.")

    diff = payload.get("diff")
    if not isinstance(diff, str):
        raise ValueError("'diff' must be a string.")

    return changed_files, diff


def cluster_connected_nodes(
    store: GraphStore,
    changed_nodes: list[GraphNode],
) -> list[list[GraphNode]]:
    """Partition changed nodes into weakly connected components.

    Only non-containment edges whose endpoints are both changed nodes are
    considered. Edge direction is ignored, and an isolated node forms a
    cluster by itself.
    """
    changed_nodes = [node for node in changed_nodes if node.kind != "File"]
    nodes_by_name = {node.qualified_name: node for node in changed_nodes}
    adjacency = {name: set() for name in nodes_by_name}

    for edge in store.get_edges_among(set(nodes_by_name)):
        if edge.kind == "CONTAINS":
            continue
        adjacency[edge.source_qualified].add(edge.target_qualified)
        adjacency[edge.target_qualified].add(edge.source_qualified)

    clusters: list[list[GraphNode]] = []
    visited: set[str] = set()
    for node in changed_nodes:
        start = node.qualified_name
        if start in visited:
            continue

        component: list[GraphNode] = []
        stack = [start]
        visited.add(start)
        while stack:
            current = stack.pop()
            component.append(nodes_by_name[current])
            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        clusters.append(component)

    return clusters


def split_diff_by_file(diff: str) -> dict[str, str]:
    """Split a stacked unified diff into complete per-file sections."""
    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    file_diffs: dict[str, str] = {}
    for section in sections:
        if not section.strip():
            continue
        file_match = re.search(r"^\+\+\+ b/(.+)$", section, re.MULTILINE)
        if file_match is None:
            continue
        file_path = file_match.group(1)
        file_diffs[file_path] = section
    return file_diffs


def _select_diff_hunks(
    file_diff: str,
    node_ranges: list[tuple[int, int]],
) -> str:
    """Return the file header and hunks overlapping the given node ranges."""
    first_hunk = re.search(r"^@@ ", file_diff, re.MULTILINE)
    if first_hunk is None:
        return ""

    header = file_diff[:first_hunk.start()]
    hunks = re.split(
        r"(?=^@@ )",
        file_diff[first_hunk.start():],
        flags=re.MULTILINE,
    )
    selected: list[str] = []
    hunk_pattern = re.compile(
        r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@"
    )
    for hunk in hunks:
        match = hunk_pattern.match(hunk)
        if match is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        end = start if count == 0 else start + count - 1
        if any(node_start <= end and node_end >= start
               for node_start, node_end in node_ranges):
            selected.append(hunk)

    return header + "".join(selected) if selected else ""


def save_diff_clusters(
    clusters: list[list[GraphNode]],
    diff: str,
    output_dir: Path,
    repo_root: Path,
) -> list[Path]:
    """Write one changed-files and diff JSON document per cluster."""
    file_diffs = split_diff_by_file(diff)
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_path in output_dir.glob("cluster-*.json"):
        stale_path.unlink()

    output_paths: list[Path] = []
    for index, cluster in enumerate(clusters, start=1):
        cluster_files: list[str] = []
        seen_files: set[str] = set()
        node_ranges_by_file: dict[str, list[tuple[int, int]]] = {}
        for node in cluster:
            node_path = Path(node.file_path)
            try:
                file_path = node_path.relative_to(repo_root).as_posix()
            except ValueError:
                file_path = node_path.as_posix()
            if file_path not in seen_files:
                seen_files.add(file_path)
                cluster_files.append(file_path)
            node_ranges_by_file.setdefault(file_path, []).append(
                (node.line_start, node.line_end)
            )

        cluster_diff = "".join(
            _select_diff_hunks(
                file_diffs[file_path],
                node_ranges_by_file[file_path],
            )
            for file_path in cluster_files
            if file_path in file_diffs
        )
        output_path = output_dir / f"cluster-{index}.json"
        output_path.write_text(
            json.dumps(
                {
                    "changed_files": cluster_files,
                    "diff": cluster_diff,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        output_paths.append(output_path)

    return output_paths


def _relativize_node_file(node: GraphNode, root: Path) -> str:
    """Return the node's file path relative to *root* as a posix string."""
    p = Path(node.file_path)
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def get_diff_cluster(
    json_path: str,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Load diff input and group the changed code into clusters."""
    try:
        changed_files, diff = load_diff_input(json_path)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}

    diff_ranges = _parse_unified_diff(diff)
    store, root = _get_store(repo_root)
    try:
        changed_nodes = map_changes_to_nodes(store, diff_ranges)
        clusters = cluster_connected_nodes(store, changed_nodes)
    finally:
        store.close()

    output_dir = Path(json_path).expanduser().parent / "diff-clusters"
    cluster_paths = save_diff_clusters(clusters, diff, output_dir, root)

    file_diffs = split_diff_by_file(diff)
    node_file_set = {_relativize_node_file(n, root) for n in changed_nodes}

    unmatched_files: dict[str, str] = {}
    for fp in file_diffs:
        if fp not in diff_ranges:
            unmatched_files[fp] = (
                "Could not parse hunk headers — "
                "the diff may contain syntax errors."
            )
        else:
            has_node = any(
                fp == nf or nf.endswith("/" + fp) for nf in node_file_set
            )
            if not has_node:
                unmatched_files[fp] = (
                    "No matching nodes found in the knowledge graph "
                    "(file may not be tracked or changed lines do not "
                    "overlap with any function/class)."
                )

    unmatched_path = None
    if unmatched_files:
        unmatched_diff = "".join(
            file_diffs[fp] for fp in unmatched_files if fp in file_diffs
        )
        unmatched_path = output_dir / "cluster-unmatched.json"
        unmatched_path.write_text(
            json.dumps(
                {
                    "changed_files": list(unmatched_files.keys()),
                    "diff": unmatched_diff,
                    "unmatched_reasons": unmatched_files,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    result: dict[str, Any] = {
        "status": "ok",
        "summary": f"Saved {len(cluster_paths)} diff cluster(s) to {output_dir}",
        "changed_files": changed_files,
        "diff": diff,
        "diff_ranges": diff_ranges,
        "cluster_files": [str(path) for path in cluster_paths],
    }
    if unmatched_path is not None:
        result["unmatched_cluster"] = str(unmatched_path)
        result["unmatched_count"] = len(unmatched_files)

    return result
