"""Graph labels and indexes (see SPEC.md). `ensure_schema` is idempotent."""

LABELS = (
    "Person", "Repo", "Module", "File", "Issue", "PullRequest", "Label", "Topic", "Decision",
    "User", "Session", "Episode", "Fact", "Agent", "Task", "SyncState",
)

FULLTEXT = {
    "Issue": ("title", "body"),
    "PullRequest": ("title", "body"),
    "Decision": ("summary", "rationale"),
    "File": ("path",),
    "Topic": ("name",),
    "Person": ("login", "name"),
    "Fact": ("text",),
}

REL_TYPES = (
    "AUTHORED", "REVIEWED", "COMMENTED_ON", "MODIFIES", "IN_MODULE", "IN_REPO", "IMPORTS", "CLOSES",
    "REFERENCES", "HAS_LABEL", "OWNS", "DECIDED_IN", "MADE", "AFFECTS", "ABOUT", "SUPERSEDES",
    "HAD", "CONTAINS", "NEXT", "CITED", "KNOWS", "LEARNED_FROM", "CLAIMED", "HANDOFF_TO", "FOR", "TOUCHED",
)


def _try(graph, query: str) -> bool:
    try:
        graph.query(query)
        return True
    except Exception as e:  # redis ResponseError: "Attribute 'x' is already indexed"
        if "already indexed" in str(e) or "already exists" in str(e):
            return False
        raise


def ensure_schema(graph) -> int:
    """Create the range index on `key` for every label and the full-text indexes. Returns #created."""
    created = 0
    for label in LABELS:
        created += _try(graph, f"CREATE INDEX FOR (n:{label}) ON (n.key)")
    for label, fields in FULLTEXT.items():
        for field in fields:  # one call per field so adding a field later stays idempotent
            created += _try(graph, f"CALL db.idx.fulltext.createNodeIndex('{label}', '{field}')")
    return created
