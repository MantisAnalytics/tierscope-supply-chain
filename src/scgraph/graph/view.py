"""Read-only networkx view of the graph built from the store's active values."""

from __future__ import annotations

import networkx as nx

from scgraph.graph.rows import to_json
from scgraph.graph.store import GraphStore


def build_view(store: GraphStore) -> nx.MultiDiGraph:
    """Nodes are keyed (type, id); edges carry type, key, identity, active fields and occurrences.

    Assembly nodes get a `root` attribute: the component at the top of their BOM.
    """
    graph = nx.MultiDiGraph()
    fields = store.active_fields()
    for type_name, node_id, referenced_only in store.nodes():
        graph.add_node(
            (type_name, node_id),
            type=type_name,
            id=node_id,
            referenced_only=referenced_only,
            fields=fields.get(("node", type_name, node_id), {}),
        )
    occurrences = store.occurrences()
    for edge in store.edges():
        graph.add_edge(
            (edge.src_type, edge.src_id),
            (edge.dst_type, edge.dst_id),
            key=edge.id,
            type=edge.type,
            edge_key=edge.key,
            identity=edge.identity(),
            fields=fields.get(("edge", edge.type, to_json(list(edge.key))), {}),
            occurrences=occurrences.get(edge.id, []),
        )
    for (type_name, node_id), data in graph.nodes(data=True):
        if type_name == "Assembly":
            data["root"] = assembly_root(graph, node_id)
    return graph


def assembly_root(graph: nx.MultiDiGraph, assembly_id: str) -> str | None:
    """The component whose part number is the assembly id and that has no parent in that assembly's BOM.

    Falls back to the single parentless component of the assembly's BOM when no part number matches.
    """
    parents, children = set(), set()
    for (_, src), (_, dst), data in graph.edges(data=True):
        if data["type"] == "CONTAINS" and data["identity"].get("assembly") == assembly_id:
            parents.add(src)
            children.add(dst)
    tops = parents - children
    for comp in sorted(tops | children):
        attrs = graph.nodes.get(("Component", comp), {})
        if attrs.get("fields", {}).get("part_number") == assembly_id:
            return comp if comp not in children else None
    return next(iter(tops)) if len(tops) == 1 else None
