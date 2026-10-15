"""End-to-end ingest against a respx-mocked GitHub, into a throwaway test_<uuid> tenant graph."""

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
import respx

from tracebrain.db import get_graph
from tracebrain.ingest.github import GitHub
from tracebrain.ingest.sync import aingest_repo, graph_counts

R = "acme/widget"
API = "https://api.github.com"
U = lambda login, t="User": {"login": login, "type": t, "avatar_url": f"https://a/{login}",
                             "html_url": f"https://github.com/{login}"}


def item(n, title, body, user, labels=(), pr=False, comments=0, updated="2024-03-01T00:00:00Z"):
    it = {"number": n, "title": title, "body": body, "user": U(user), "state": "closed" if pr else "open",
          "labels": [{"name": l, "color": "fff"} for l in labels], "html_url": f"https://github.com/{R}/issues/{n}",
          "created_at": "2024-01-01T00:00:00Z", "updated_at": updated, "closed_at": None, "comments": comments}
    if pr:
        it["pull_request"] = {"merged_at": "2024-02-01T00:00:00Z"}
    return it


ISSUES_P1 = [item(3, "Add query cache", "Fixes #1. Related to #2 and other/repo#5.", "alice",
                  labels=["perf"], pr=True, comments=2),
             item(4, "Bump deps", "", "dependabot[bot]", pr=True)]
ISSUES_P2 = [item(1, "Queries are slow", "See #3", "bob", labels=["perf", "good first issue"], comments=1),
             item(2, "Docs", "nothing", "carol")]
TREE = ["widget/__init__.py", "widget/core.py", "widget/cache.py", "tests/test_cache.py", "README.md",
        "pyproject.toml", ".github/CODEOWNERS"]
SOURCES = {
    "widget/__init__.py": "from .core import run\n",
    "widget/core.py": "import os\nfrom widget.cache import Cache\n",
    "widget/cache.py": "from . import core\n",
    "tests/test_cache.py": "import widget.cache\nimport pytest\n",
    ".github/CODEOWNERS": "* @carol\n/widget/cache.py @alice\n",
}
ROUTES = {
    f"/repos/{R}": {"default_branch": "main", "description": "Widgets", "html_url": f"https://github.com/{R}"},
    f"/repos/{R}/issues/3/comments": [{"user": U("bob"), "body": "LRU or TTL?", "created_at": "2024-01-05T00:00:00Z"},
                                      {"user": U("alice"), "body": "LRU.", "created_at": "2024-01-06T00:00:00Z"}],
    f"/repos/{R}/issues/1/comments": [{"user": U("alice"), "body": "on it", "created_at": "2024-01-02T00:00:00Z"}],
    f"/repos/{R}/pulls/3/files": [{"filename": "widget/cache.py", "additions": 40, "deletions": 2},
                                  {"filename": "docs/old.md", "additions": 1, "deletions": 0}],
    f"/repos/{R}/pulls/3/reviews": [
        {"user": U("bob"), "state": "COMMENTED", "submitted_at": "2024-01-07T00:00:00Z", "body": ""},
        {"user": U("bob"), "state": "APPROVED", "submitted_at": "2024-01-08T00:00:00Z", "body": "LGTM, LRU it is"},
        {"user": U("alice"), "state": "COMMENTED", "submitted_at": "2024-01-08T00:00:00Z", "body": "self"}],
    f"/repos/{R}/pulls/3/comments": [{"user": U("carol"), "body": "nit", "created_at": "2024-01-07T00:00:00Z"}],
    f"/repos/{R}/pulls/4/files": [], f"/repos/{R}/pulls/4/reviews": [], f"/repos/{R}/pulls/4/comments": [],
    f"/repos/{R}/commits/main": {"sha": "abc123"},
    f"/repos/{R}/git/trees/abc123": {"tree": [{"path": p, "type": "blob"} for p in TREE]},
}


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == f"/repos/{R}/issues":
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=ISSUES_P2)
        nxt = f'<{API}/repos/{R}/issues?page=2&per_page=100>; rel="next"'
        return httpx.Response(200, json=ISSUES_P1, headers={"Link": nxt})
    if path.startswith("/users/"):
        login = path.rsplit("/", 1)[1]
        return httpx.Response(200, json={"login": login, "name": login.title()})
    if path.startswith(f"/repos/{R}/contents/"):
        p = path.removeprefix(f"/repos/{R}/contents/")
        return httpx.Response(200, text=SOURCES[p]) if p in SOURCES else httpx.Response(404, json={})
    if path in ROUTES:
        return httpx.Response(200, json=ROUTES[path])
    return httpx.Response(404, json={"message": "Not Found"})


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        tool = kw["tools"][0]["name"]
        if tool == "record_decisions":
            data = {"decisions": [{
                "summary": "Use an LRU cache for query results", "rationale": "Bounded memory.",
                "status": "accepted", "quote": "LRU.", "proposer_login": "alice",
                "approver_logins": ["bob", "mallory"], "affected_paths": ["widget/cache.py", "widget/"],
                "topics": ["Query Caching"]}]} if "Add query cache" in kw["messages"][0]["content"] \
                else {"decisions": []}
        else:
            data = {"pairs": []}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok"),
                                        SimpleNamespace(type="tool_use", name=tool, input=data, id="t1")])


@pytest.fixture
def graph():
    g = get_graph(f"test_{uuid.uuid4().hex[:8]}")
    yield g
    try:
        g.delete()
    except Exception:
        pass


def q(g, cypher, **params):
    return g.query(cypher, params).result_set


async def run(g, llm=None, **kw):
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        mock.route().mock(side_effect=handler)
        async with GitHub(token="t", log=lambda *_: None) as gh:
            return await aingest_repo(R, graph=g, gh=gh, llm=llm, log=lambda *_: None, **kw)


async def test_ingest_graph_shape_and_idempotency(graph):
    llm = FakeLLM()
    counts = await run(graph, llm=llm)
    first = graph_counts(graph)
    n, e = first["nodes"], first["edges"]
    assert n["Issue"] == 2 and n["PullRequest"] == 2 and n["Repo"] == 1 and n["SyncState"] == 1
    assert counts["github"]["issues"] == 2 and counts["github"]["prs"] == 2

    # keys / props
    assert q(graph, "MATCH (p:Person {key:'person:dependabot[bot]'}) RETURN p.bot")[0][0] is True
    assert q(graph, "MATCH (p:Person {key:'person:alice'}) RETURN p.name, p.bot")[0] == ["Alice", False]
    assert q(graph, "MATCH (p:PullRequest {key:'pr:acme/widget#3'}) RETURN p.merged, p.number")[0] == [True, 3]

    # CLOSES / REFERENCES (other/repo#5 ignored)
    assert q(graph, "MATCH (a)-[:CLOSES]->(b) RETURN a.key, b.key") == [["pr:acme/widget#3", "issue:acme/widget#1"]]
    refs = {tuple(r) for r in q(graph, "MATCH (a)-[:REFERENCES]->(b) RETURN a.key, b.key")}
    assert refs == {("pr:acme/widget#3", "issue:acme/widget#2"), ("issue:acme/widget#1", "pr:acme/widget#3")}

    # reviews: latest decisive state, self-review excluded
    assert q(graph, "MATCH (p:Person)-[r:REVIEWED]->(:PullRequest) RETURN p.login, r.state, r.count") == \
        [["bob", "APPROVED", 2]]
    commented = {tuple(r) for r in q(graph, "MATCH (p:Person)-[c:COMMENTED_ON]->(x) RETURN p.login, x.number, c.count")}
    assert commented == {("bob", 3, 1), ("alice", 3, 1), ("carol", 3, 1), ("alice", 1, 1)}
    assert q(graph, "MATCH (:PullRequest)-[m:MODIFIES]->(f:File {path:'widget/cache.py'}) RETURN m.additions") == [[40]]

    # labels -> topics (non-topic label skipped)
    assert {r[0] for r in q(graph, "MATCH (:Issue)-[:HAS_LABEL]->(l:Label) RETURN l.key")} == \
        {"label:acme/widget:perf", "label:acme/widget:good first issue"}
    assert {r[0] for r in q(graph, "MATCH (:Issue)-[:ABOUT]->(t:Topic) RETURN t.key")} == {"topic:perf"}

    # code: modules + imports
    imports = {tuple(r) for r in q(graph, "MATCH (a:File)-[:IMPORTS]->(b:File) RETURN a.path, b.path")}
    assert imports == {("widget/__init__.py", "widget/core.py"), ("widget/core.py", "widget/cache.py"),
                       ("widget/cache.py", "widget/core.py"), ("tests/test_cache.py", "widget/cache.py")}
    assert q(graph, "MATCH (:File {path:'widget/core.py'})-[:IN_MODULE]->(m:Module)-[:IN_REPO]->(r:Repo) "
                    "RETURN m.key, r.key") == [["module:acme/widget:widget", "repo:acme/widget"]]
    assert q(graph, "MATCH (f:File {path:'README.md'}) RETURN count(f)") == [[0]]  # docs aren't code files

    # CODEOWNERS -> OWNS
    owns = {tuple(r) for r in q(graph, "MATCH (p:Person)-[:OWNS]->(x) RETURN p.login, x.key")}
    assert ("alice", "file:acme/widget:widget/cache.py") in owns
    assert ("carol", "module:acme/widget:widget") in owns and ("carol", "module:acme/widget:tests") in owns

    # decisions (fake LLM): mallory isn't a participant -> no MADE edge
    assert counts["decisions"]["decisions"] == 1
    made = {tuple(r) for r in q(graph, "MATCH (p:Person)-[m:MADE]->(d:Decision) RETURN p.login, m.role")}
    assert made == {("alice", "proposer"), ("bob", "approver")}
    assert q(graph, "MATCH (d:Decision)-[:DECIDED_IN]->(x) RETURN x.key") == [["pr:acme/widget#3"]]
    assert {r[0] for r in q(graph, "MATCH (d:Decision)-[:AFFECTS]->(x) RETURN x.key")} == \
        {"file:acme/widget:widget/cache.py", "module:acme/widget:widget"}
    assert q(graph, "MATCH (d:Decision)-[:ABOUT]->(t:Topic) RETURN t.key") == [["topic:query-caching"]]
    assert "tools" in llm.calls[0] and "output_config" not in llm.calls[0]

    # incremental: SyncState recorded and used as `since`
    last = q(graph, "MATCH (s:SyncState {key:'sync:acme/widget'}) RETURN s.last_synced_at")[0][0]
    second = await run(graph, llm=llm)
    assert second["since"] == last
    # full re-ingest: no new nodes or edges
    await run(graph, llm=llm, full=True)
    assert graph_counts(graph) == first


async def test_ingest_without_llm_skips_decisions(graph, monkeypatch):
    monkeypatch.setattr("tracebrain.config.llm_configured", lambda: False)
    counts = await run(graph, with_code=False)
    assert "decisions" not in counts and "code" not in counts
    assert q(graph, "MATCH (d:Decision) RETURN count(d)") == [[0]]
    assert counts["owns"] > 0  # CODEOWNERS still applied from the tree


async def test_rate_limit_retry(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr("tracebrain.ingest.github.asyncio.sleep", fake_sleep)
    calls = iter([httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"}),
                  httpx.Response(429, headers={"retry-after": "3"}),
                  httpx.Response(200, json={"ok": 1})])
    with respx.mock(base_url=API) as mock:
        mock.get("/rate").mock(side_effect=lambda req: next(calls))
        async with GitHub(token="t", log=lambda *_: None) as gh:
            assert await gh.json("/rate") == {"ok": 1}
    assert slept == [1, 3]


def test_schema_idempotent():
    from tracebrain.schema import ensure_schema
    g = get_graph(f"test_{uuid.uuid4().hex[:8]}")
    try:
        assert ensure_schema(g) > 0
        assert ensure_schema(g) == 0
    finally:
        g.delete()
