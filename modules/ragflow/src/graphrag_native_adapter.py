"""Audited minimal graspologic adapter for native-Windows RAGFlow.

RAGFlow 0.27.1 uses only hierarchical Leiden and largest-connected-component
selection on its active GraphRAG indexing path.  The full graspologic fork is
not installable with NumPy 2 on CPython 3.13, while its Rust backend is.
This module deliberately exposes only those two operations.
"""

from __future__ import annotations

from typing import Any, NamedTuple

import graspologic_native
import networkx as nx


class HierarchicalCluster(NamedTuple):
    node: Any
    cluster: int
    parent_cluster: int | None
    level: int
    is_final_cluster: bool


class _IdentityMapper:
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def encode(self, value: Any) -> str:
        encoded = str(value)
        if encoded in self._values and self._values[encoded] != value:
            raise ValueError(
                "Two distinct GraphRAG node IDs have the same string representation"
            )
        self._values[encoded] = value
        return encoded

    def decode(self, value: str) -> Any:
        return self._values[value]


def largest_connected_component(graph: nx.Graph) -> nx.Graph:
    """Return a copy of the largest (weakly, for directed input) component."""

    if not isinstance(graph, nx.Graph):
        raise TypeError("graph must be a networkx graph")
    components = (
        nx.weakly_connected_components(graph)
        if graph.is_directed()
        else nx.connected_components(graph)
    )
    nodes = max(components, key=len)
    return graph.subgraph(nodes).copy()


def hierarchical_leiden(
    graph: nx.Graph,
    max_cluster_size: int = 1000,
    random_seed: int | None = None,
) -> list[HierarchicalCluster]:
    """Run the exact Leiden backend used by graspologic with audited defaults."""

    if not isinstance(graph, nx.Graph):
        raise TypeError("graph must be a networkx graph")
    if graph.is_directed() or graph.is_multigraph():
        raise ValueError("hierarchical Leiden requires an undirected simple graph")
    if max_cluster_size <= 0:
        raise ValueError("max_cluster_size must be positive")
    if random_seed is not None and random_seed <= 0:
        raise ValueError("random_seed must be positive")

    identities = _IdentityMapper()
    edges: list[tuple[str, str, float]] = []
    for source, target, attributes in graph.edges(data=True):
        edges.append(
            (
                identities.encode(source),
                identities.encode(target),
                float(attributes.get("weight", 1.0)),
            )
        )

    native_clusters = graspologic_native.hierarchical_leiden(
        edges=edges,
        starting_communities=None,
        resolution=1.0,
        randomness=0.001,
        iterations=1,
        use_modularity=True,
        max_cluster_size=max_cluster_size,
        seed=random_seed,
    )
    return [
        HierarchicalCluster(
            node=identities.decode(cluster.node),
            cluster=cluster.cluster,
            parent_cluster=cluster.parent_cluster,
            level=cluster.level,
            is_final_cluster=cluster.is_final_cluster,
        )
        for cluster in native_clusters
    ]
