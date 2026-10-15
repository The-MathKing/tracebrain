"""Builder B shared test fixtures: a synthetic org graph in a throwaway tenant + a scripted fake LLM.

Import in tests with `from fixtures_b import *` (pytest puts tests/ on sys.path via rootdir conftest-less
rootdir insertion; we also add it explicitly below)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tracebrain.db import get_db

R = "acme/widgets"


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


SEED = [
    # people
    ("CREATE (:Person {key:'person:alice', login:'alice', name:'Alice A', title:'Alice A'})", {}),
    ("CREATE (:Person {key:'person:bob', login:'bob', name:'Bob B', title:'Bob B'})", {}),
    ("CREATE (:Person {key:'person:carol', login:'carol', name:'Carol C', title:'Carol C'})", {}),
    ("CREATE (:Person {key:'person:dependabot[bot]', login:'dependabot[bot]', title:'dependabot[bot]'})", {}),
    # repo / modules / files
    (f"CREATE (:Repo {{key:'repo:{R}', name:'widgets', title:'{R}', description:'Widget engine'}})", {}),
    (f"CREATE (:Module {{key:'module:{R}:core', path:'core', title:'core'}})", {}),
    (f"CREATE (:Module {{key:'module:{R}:api', path:'api', title:'api'}})", {}),
    (f"MATCH (r:Repo) MATCH (m:Module) CREATE (m)-[:IN_REPO]->(r)", {}),
]
FILES = {"core/vector_index.py": "core", "core/graph.py": "core", "api/server.py": "api", "api/routes.py": "api",
         "core/util.py": "core"}
IMPORTS = [("core/graph.py", "core/vector_index.py"), ("api/server.py", "core/graph.py"),
           ("api/routes.py", "api/server.py"), ("core/vector_index.py", "core/util.py")]


def fk(path: str) -> str:
    return f"file:{R}:{path}"


def seed(g) -> None:
    for q, p in SEED:
        g.query(q, p)
    for path, mod in FILES.items():
        g.query("MATCH (m:Module {key:$m}) CREATE (:File {key:$k, path:$p, title:$p, language:'python'})-[:IN_MODULE]->(m)",
                {"m": f"module:{R}:{mod}", "k": fk(path), "p": path})
    for a, b in IMPORTS:
        g.query("MATCH (a:File {key:$a}), (b:File {key:$b}) CREATE (a)-[:IMPORTS]->(b)", {"a": fk(a), "b": fk(b)})
    issues = [
        (1, "Vector index crashes on empty graph", "Creating a vector index on an empty graph segfaults the server.",
         "closed", "alice", 300),
        (2, "Vector index crash when graph is empty", "Server crashes creating vector index with no nodes.", "open",
         "carol", 5),
        (3, "Add docs for routes", "Document the api routes module", "open", "bob", 20),
    ]
    for n, title, body, state, author, age in issues:
        g.query(f"MATCH (p:Person {{key:$pk}}), (r:Repo) CREATE (p)-[:AUTHORED]->(:Issue {{key:$k, number:$n, title:$t, "
                f"body:$b, state:$s, created_at:$c, url:$u}})-[:IN_REPO]->(r)",
                {"pk": f"person:{author}", "k": f"issue:{R}#{n}", "n": n, "t": title, "b": body, "s": state,
                 "c": _ago(age), "u": f"https://github.com/{R}/issues/{n}"})
    prs = [
        (10, "Fix vector index crash on empty graph", "fixes #1", "alice", 290, ["core/vector_index.py", "core/graph.py"], ["bob"]),
        (11, "Refactor graph traversal", "", "bob", 30, ["core/graph.py"], ["alice"]),
        (12, "Bump deps", "", "dependabot[bot]", 2, ["core/util.py"], []),
        (13, "Speed up vector search", "", "carol", 10, ["core/vector_index.py"], ["alice"]),
    ]
    for n, title, body, author, age, files, reviewers in prs:
        g.query("MATCH (p:Person {key:$pk}), (r:Repo) CREATE (p)-[:AUTHORED]->(:PullRequest {key:$k, number:$n, "
                "title:$t, body:$b, state:'closed', merged:true, created_at:$c, merged_at:$c, url:$u})-[:IN_REPO]->(r)",
                {"pk": f"person:{author}", "k": f"pr:{R}#{n}", "n": n, "t": title, "b": body, "c": _ago(age),
                 "u": f"https://github.com/{R}/pull/{n}"})
        for f in files:
            g.query("MATCH (pr:PullRequest {key:$pr}), (f:File {key:$f}) CREATE (pr)-[:MODIFIES {additions:10, deletions:2}]->(f)",
                    {"pr": f"pr:{R}#{n}", "f": fk(f)})
        for rv in reviewers:
            g.query("MATCH (p:Person {key:$p}), (pr:PullRequest {key:$pr}) CREATE (p)-[:REVIEWED {state:'APPROVED', at:$at}]->(pr)",
                    {"p": f"person:{rv}", "pr": f"pr:{R}#{n}", "at": _ago(age)})
    g.query("MATCH (pr:PullRequest {key:$pr}), (i:Issue {key:$i}) CREATE (pr)-[:CLOSES]->(i)",
            {"pr": f"pr:{R}#10", "i": f"issue:{R}#1"})
    g.query("MATCH (p:Person {key:'person:bob'}), (i:Issue {key:$i}) CREATE (p)-[:COMMENTED_ON {count:3, last_at:$at}]->(i)",
            {"i": f"issue:{R}#2", "at": _ago(4)})
    g.query("MATCH (p:Person {key:'person:carol'}), (m:Module {key:$m}) CREATE (p)-[:OWNS]->(m)", {"m": f"module:{R}:core"})
    # decisions with a SUPERSEDES chain
    g.query("CREATE (:Topic {key:'topic:vector-index', name:'vector index', title:'vector index'})")
    g.query("MATCH (i:PullRequest {key:$src}), (m:Module {key:$m}), (t:Topic), (a:Person {key:'person:alice'}), "
            "(b:Person {key:'person:bob'}) "
            "CREATE (d1:Decision {key:'decision:aaaaaaaaaaaa', title:'Use HNSW for the vector index', "
            "summary:'Use HNSW for the vector index', rationale:'Faster approximate search than brute force', "
            "status:'superseded', decided_at:$c1})-[:DECIDED_IN]->(i), (d1)-[:AFFECTS]->(m), (d1)-[:ABOUT]->(t), "
            "(a)-[:MADE {role:'proposer'}]->(d1), (b)-[:MADE {role:'approver'}]->(d1)",
            {"src": f"pr:{R}#10", "m": f"module:{R}:core", "c1": _ago(290)})
    g.query("MATCH (old:Decision {key:'decision:aaaaaaaaaaaa'}), (pr:PullRequest {key:$src}), (c:Person {key:'person:carol'}) "
            "CREATE (d2:Decision {key:'decision:bbbbbbbbbbbb', title:'Switch vector index to quantized HNSW', "
            "summary:'Switch vector index to quantized HNSW', rationale:'Memory usage halves with int8 quantization', "
            "status:'accepted', decided_at:$c})-[:SUPERSEDES]->(old), (d2)-[:DECIDED_IN]->(pr), "
            "(c)-[:MADE {role:'proposer'}]->(d2)", {"src": f"pr:{R}#13", "c": _ago(10)})


@pytest.fixture
def tenant():
    """A seeded `test_<uuid>` tenant; the graph is deleted afterwards. Never touches org_falkordb."""
    t = f"test_{uuid.uuid4().hex[:12]}"
    g = get_db().select_graph(f"org_{t}")
    try:
        from tracebrain.schema import ensure_schema

        ensure_schema(g)
    except ImportError:
        pass
    seed(g)
    yield t
    try:
        g.delete()
    except Exception:
        pass


@pytest.fixture
def graph(tenant):
    return get_db().select_graph(f"org_{tenant}")


# ---------------------------------------------------------------- fake Anthropic client
def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, input, id=None):
    return SimpleNamespace(type="tool_use", id=id or f"toolu_{uuid.uuid4().hex[:10]}", name=name, input=input)


def message(*blocks, stop_reason=None):
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=None,
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))


class _Stream:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        for b in self._msg.content:
            if b.type == "text":
                for i in range(0, len(b.text), 16):
                    yield b.text[i:i + 16]

    def get_final_message(self):
        return self._msg


class FakeAnthropic:
    """Scripted client. `script` is a list of messages, or callables(kwargs)->message, consumed in order
    by both messages.stream() and messages.create(). Every request's kwargs are kept in `.calls`."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream, create=self._create)

    def _next(self, kwargs):
        # Snapshot messages (the loop mutates the list afterwards).
        self.calls.append({**kwargs, "messages": list(kwargs.get("messages", []))})
        if not self.script:
            raise AssertionError("FakeAnthropic script exhausted")
        item = self.script.pop(0)
        return item(kwargs) if callable(item) else item

    def _stream(self, **kwargs):
        return _Stream(self._next(kwargs))

    def _create(self, **kwargs):
        return self._next(kwargs)
