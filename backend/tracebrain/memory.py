"""Per-user agent memory stored in the tenant graph.

(User)-[:HAD]->(Session)-[:CONTAINS]->(Episode), (Episode)-[:NEXT]->(Episode) (per-user timeline),
(Episode)-[:CITED]->(any), (User)-[:KNOWS]->(Fact)-[:ABOUT]->(any), (Fact)-[:SUPERSEDES]->(Fact),
(Fact)-[:LEARNED_FROM]->(Episode). Every read is anchored on the user's own node, so users in the
same tenant graph never see each other's facts or episodes.
"""

from __future__ import annotations

import re
import time
import uuid

from .tools.common import Tracer, ToolError, from_fnode, fulltext_query, now_iso, terms

FACT_KINDS = ("semantic", "preference", "procedural")


def user_key(user_id: str) -> str:
    user_id = (user_id or "").strip()
    if not user_id or not re.match(r"^[\w.@-]{1,64}$", user_id):
        raise ToolError("user_id must be 1-64 chars of letters, digits, _ . @ -")
    return f"user:{user_id}"


def ensure_user(graph, user_id: str) -> str:
    uk = user_key(user_id)
    graph.query("MERGE (u:User {key: $k}) ON CREATE SET u.title = $id, u.created_at = $now",
                {"k": uk, "id": user_id, "now": now_iso()})
    return uk


def start_session(graph, user_id: str, session_id: str | None = None) -> str:
    """Returns the session uuid (creates it if needed)."""
    uk = ensure_user(graph, user_id)
    sid = session_id or str(uuid.uuid4())
    if not re.match(r"^[\w-]{1,64}$", sid):
        raise ToolError("invalid session_id")
    sk = f"session:{sid}"
    owner = graph.ro_query("MATCH (u:User)-[:HAD]->(:Session {key: $sk}) RETURN u.key", {"sk": sk}).result_set
    if owner and owner[0][0] != uk:
        raise ToolError("session belongs to another user")
    graph.query(
        "MATCH (u:User {key: $uk}) MERGE (s:Session {key: $sk}) "
        "ON CREATE SET s.started_at = $now, s.started_ts = $ts, s.title = $title "
        "MERGE (u)-[:HAD]->(s)",
        {"uk": uk, "sk": sk, "now": now_iso(), "ts": int(time.time()), "title": f"Session {sid[:8]}"},
    )
    return sid


def add_episode(graph, user_id: str, session_id: str, question: str, answer: str, agent: str,
                cited_keys: list[str] | None = None) -> str:
    uk = user_key(user_id)
    sk = f"session:{session_id}"
    ek = f"episode:{uuid.uuid4()}"
    now, ts = now_iso(), time.time()
    prev = graph.ro_query(
        "MATCH (:User {key: $uk})-[:HAD]->(:Session)-[:CONTAINS]->(e:Episode) "
        "RETURN e.key ORDER BY e.created_ts DESC LIMIT 1", {"uk": uk}).result_set
    graph.query(
        "MATCH (s:Session {key: $sk}) CREATE (s)-[:CONTAINS]->(e:Episode {key: $ek, title: $title, question: $q, "
        "answer: $a, agent: $agent, created_at: $now, created_ts: $ts})",
        {"sk": sk, "ek": ek, "title": question[:120], "q": question, "a": answer, "agent": agent,
         "now": now, "ts": ts},
    )
    if prev:
        graph.query("MATCH (p:Episode {key: $p}), (e:Episode {key: $e}) MERGE (p)-[:NEXT]->(e)",
                    {"p": prev[0][0], "e": ek})
    keys = [k for k in dict.fromkeys(cited_keys or []) if k and not k.startswith(("user:", "session:", "fact:", "episode:"))]
    if keys:
        graph.query("MATCH (e:Episode {key: $ek}) UNWIND $keys AS k MATCH (x {key: k}) MERGE (e)-[:CITED]->(x)",
                    {"ek": ek, "keys": keys[:50]})
    return ek


def _fact_out(f, why: list[str] | None = None) -> dict:
    p = f.properties if hasattr(f, "properties") else f
    d = {"key": p["key"], "text": p.get("text"), "kind": p.get("kind"), "confidence": p.get("confidence"),
         "valid": p.get("valid"), "created_at": p.get("created_at"), "updated_at": p.get("updated_at")}
    if why is not None:
        d["why"] = why
    return d


def remember(graph, user_id: str, text: str, kind: str = "semantic", about_keys: list[str] | None = None,
             confidence: float = 0.8, learned_from: str | None = None, supersedes: list[str] | None = None) -> dict:
    """Store a Fact for the user. Identical valid text is de-duplicated (refreshes updated_at).
    `supersedes` marks those (same user's) facts valid=false and links (new)-[:SUPERSEDES]->(old)."""
    t = Tracer(graph)
    text = (text or "").strip()
    if not text:
        raise ToolError("text is required")
    if kind not in FACT_KINDS:
        raise ToolError(f"kind must be one of {FACT_KINDS}")
    uk = ensure_user(graph, user_id)
    now = now_iso()
    existing = t.q(
        "MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact) WHERE f.valid = true AND toLower(f.text) = toLower($text) "
        "RETURN f LIMIT 1", {"uk": uk, "text": text})
    if existing:
        fk = existing[0][0].properties["key"]
        t.q("MATCH (f:Fact {key: $fk}) SET f.updated_at = $now, f.confidence = $c", {"fk": fk, "now": now,
            "c": max(float(confidence), float(existing[0][0].properties.get("confidence") or 0))}, ro=False)
        created = False
    else:
        fk = f"fact:{uuid.uuid4()}"
        t.q(
            "MATCH (u:User {key: $uk}) CREATE (u)-[:KNOWS]->(f:Fact {key: $fk, title: $title, text: $text, kind: $kind, "
            "confidence: $c, created_at: $now, updated_at: $now, valid: true})",
            {"uk": uk, "fk": fk, "title": text[:120], "text": text, "kind": kind, "c": float(confidence), "now": now},
            ro=False)
        created = True
    t.add_edge(uk, fk, "KNOWS")
    about = [k for k in dict.fromkeys(about_keys or []) if k]
    if about:
        for (k,) in t.q("MATCH (f:Fact {key: $fk}) UNWIND $keys AS k MATCH (x {key: k}) WHERE NOT x:User "
                        "MERGE (f)-[:ABOUT]->(x) RETURN x.key", {"fk": fk, "keys": about}, ro=False):
            t.add_edge(fk, k, "ABOUT")
    if learned_from:
        t.q("MATCH (f:Fact {key: $fk}), (e:Episode {key: $ek}) MERGE (f)-[:LEARNED_FROM]->(e)",
            {"fk": fk, "ek": learned_from}, ro=False)
    superseded = []
    for old in [k for k in (supersedes or []) if k and k != fk]:
        rows = t.q(
            "MATCH (:User {key: $uk})-[:KNOWS]->(o:Fact {key: $ok}), (f:Fact {key: $fk}) "
            "SET o.valid = false, o.updated_at = $now MERGE (f)-[:SUPERSEDES]->(o) RETURN o.key",
            {"uk": uk, "ok": old, "fk": fk, "now": now}, ro=False)
        if rows:
            superseded.append(old)
            t.add_edge(fk, old, "SUPERSEDES")
    f = t.q("MATCH (f:Fact {key: $fk}) RETURN f", {"fk": fk})[0][0]
    t.add_fnode(f)
    return t.result({"fact": _fact_out(f), "created": created, "superseded": superseded})


def invalidate_fact(graph, user_id: str, fact_key: str) -> bool:
    rows = graph.query("MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact {key: $fk}) SET f.valid = false, f.updated_at = $now "
                       "RETURN f.key", {"uk": user_key(user_id), "fk": fact_key, "now": now_iso()}).result_set
    return bool(rows)


def delete_fact(graph, user_id: str, fact_key: str) -> bool:
    rows = graph.query("MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact {key: $fk}) WITH f, f.key AS k DETACH DELETE f "
                       "RETURN k", {"uk": user_key(user_id), "fk": fact_key}).result_set
    return bool(rows)


def _question_entities(graph, query: str, limit: int = 6) -> list[str]:
    keys = re.findall(r"\[\[([^\]]+)\]\]", query or "")
    keys += re.findall(r"\b(?:person|repo|module|file|issue|pr|topic|decision|label):[^\s,\]\)]+", query or "")
    if terms(query):
        from .tools.search import search_entities
        try:
            sr = search_entities(graph, query, limit=limit)
            keys += [r["key"] for r in sr["data"]["results"] if r["score"] >= 0.5]
        except ToolError:
            pass
    return list(dict.fromkeys(keys))[: limit * 2]


def recall(graph, user_id: str, query: str = "", limit: int = 8, episodes: int = 3) -> dict:
    """Relevant memory for a question: (1) full-text match on the user's valid Facts, (2) the user's
    Facts ABOUT entities mentioned in / matched by the question, (3) all preference facts, and
    (4) the last N episodes. Each fact says why it was recalled."""
    t = Tracer(graph)
    uk = user_key(user_id)
    limit = max(1, min(int(limit or 8), 30))
    found: dict[str, tuple] = {}

    def add(f, reason: str, score: float):
        k = f.properties["key"]
        if k in found:
            found[k][1].append(reason)
            found[k] = (f, found[k][1], found[k][2] + score)
        else:
            found[k] = (f, [reason], score)

    ftq = fulltext_query(query or "")
    if ftq:
        rows = t.try_q(
            "CALL db.idx.fulltext.queryNodes('Fact', $q) YIELD node, score "
            "MATCH (:User {key: $uk})-[:KNOWS]->(node) WHERE node.valid = true RETURN node, score LIMIT $lim",
            {"q": ftq, "uk": uk, "lim": limit * 2})
        if rows is None:  # no full-text index yet: substring fallback
            rows = t.q(
                "MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact) WHERE f.valid = true "
                "WITH f, size([x IN $terms WHERE toLower(f.text) CONTAINS x]) AS hits WHERE hits > 0 "
                "RETURN f, hits ORDER BY hits DESC LIMIT $lim", {"uk": uk, "terms": terms(query), "lim": limit * 2})
        for f, s in rows:
            add(f, "text match", 1.0 + min(float(s or 0), 5) / 5)
    ents = _question_entities(graph, query) if query else []
    if ents:
        for f, x in t.q(
            "MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact)-[:ABOUT]->(x) WHERE f.valid = true AND x.key IN $keys "
            "RETURN f, x.key", {"uk": uk, "keys": ents}):
            add(f, f"about {x}", 1.5)
            t.add_edge(f.properties["key"], x, "ABOUT")
    for (f,) in t.q(
        "MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact) WHERE f.valid = true AND f.kind = 'preference' "
        "RETURN f ORDER BY f.updated_at DESC LIMIT 5", {"uk": uk}):
        add(f, "preference", 0.5)
    facts = sorted(found.values(), key=lambda x: -x[2])[:limit]
    out_facts = []
    for f, why, score in facts:
        t.add_fnode(f)
        t.add_edge(uk, f.properties["key"], "KNOWS")
        out_facts.append({**_fact_out(f, why), "score": round(score, 3)})
    eps = []
    n_ep = max(0, min(int(episodes if episodes is not None else 3), 20))
    if n_ep:
        for (e,) in t.q(
            "MATCH (:User {key: $uk})-[:HAD]->(:Session)-[:CONTAINS]->(e:Episode) "
            "RETURN e ORDER BY e.created_ts DESC LIMIT $n", {"uk": uk, "n": n_ep}):
            p = e.properties
            eps.append({"key": p["key"], "question": p.get("question"), "answer": (p.get("answer") or "")[:600],
                        "agent": p.get("agent"), "created_at": p.get("created_at")})
    return t.result({"user": uk, "facts": out_facts, "episodes": eps, "entities": ents})


def get_memory(graph, user_id: str, limit: int = 100) -> dict:
    uk = user_key(user_id)
    facts = [_fact_out(r[0]) for r in graph.ro_query(
        "MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact) OPTIONAL MATCH (f)-[:SUPERSEDES]->(o:Fact) "
        "WITH f, collect(o.key) AS sup RETURN f, sup ORDER BY f.updated_at DESC LIMIT $lim",
        {"uk": uk, "lim": limit}).result_set]
    sup_rows = graph.ro_query(
        "MATCH (:User {key: $uk})-[:KNOWS]->(f:Fact) OPTIONAL MATCH (f)-[:SUPERSEDES]->(o:Fact) "
        "OPTIONAL MATCH (f)-[:ABOUT]->(x) RETURN f.key, collect(DISTINCT o.key), collect(DISTINCT x.key)",
        {"uk": uk}).result_set
    extra = {r[0]: (r[1], r[2]) for r in sup_rows}
    for f in facts:
        f["supersedes"], f["about"] = extra.get(f["key"], ([], []))
    episodes = []
    for e, sk, cited in graph.ro_query(
        "MATCH (:User {key: $uk})-[:HAD]->(s:Session)-[:CONTAINS]->(e:Episode) OPTIONAL MATCH (e)-[:CITED]->(x) "
        "RETURN e, s.key, collect(x.key) ORDER BY e.created_ts DESC LIMIT $lim", {"uk": uk, "lim": limit}).result_set:
        p = e.properties
        episodes.append({"key": p["key"], "session": sk, "question": p.get("question"), "answer": p.get("answer"),
                         "agent": p.get("agent"), "created_at": p.get("created_at"), "cited": cited})
    sessions = []
    for s, n in graph.ro_query(
        "MATCH (:User {key: $uk})-[:HAD]->(s:Session) OPTIONAL MATCH (s)-[:CONTAINS]->(e:Episode) "
        "RETURN s, count(e) ORDER BY s.started_ts DESC LIMIT $lim", {"uk": uk, "lim": limit}).result_set:
        p = s.properties
        sessions.append({"key": p["key"], "session_id": p["key"].split(":", 1)[1], "started_at": p.get("started_at"),
                         "episodes": n})
    return {"user": uk, "facts": facts, "episodes": episodes, "sessions": sessions}


__all__ = ["recall", "remember", "add_episode", "start_session", "ensure_user", "get_memory", "delete_fact",
           "invalidate_fact", "from_fnode"]
