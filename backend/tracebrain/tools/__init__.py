"""Graph tools. Each is `fn(graph, **args) -> {"data", "trace": {"cypher", "nodes", "edges"}}`."""

from .algos import centrality
from .common import ToolError
from .cypher import run_cypher
from .decisions import trace_decision
from .experts import find_experts
from .impact import blast_radius
from .neighbors import get_neighbors, shortest_path
from .search import find_related_work, search_entities


def recall(graph, user_id: str, query: str = "", limit: int = 8):
    from ..memory import recall as _recall

    return _recall(graph, user_id, query, limit=limit)


def remember(graph, user_id: str, text: str, kind: str = "semantic", about_keys=None):
    from ..memory import remember as _remember

    return _remember(graph, user_id, text, kind, about_keys)


__all__ = ["ToolError", "search_entities", "get_neighbors", "find_experts", "trace_decision", "blast_radius",
           "find_related_work", "shortest_path", "centrality", "run_cypher", "recall", "remember"]
