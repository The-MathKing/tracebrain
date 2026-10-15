"""Portable snapshots of a tenant graph: export from any FalkorDB, import into any FalkorDB.

    python -m tracebrain.seed export [--tenant falkordb] [--path deploy/snapshot/org_falkordb.json.gz]
    python -m tracebrain.seed import [--tenant falkordb] [--path ...] [--include-memory]

The target DB is whatever `db.get_db()` resolves to: FALKORDB_URL when set, else embedded falkordblite.
Nodes are identified by their `key` property and written with `UNWIND ... MERGE`, so importing twice is a no-op.
Agent memory and coordination state (User/Session/Episode/Fact/Task/Agent) is excluded by default so a fresh
deploy starts with a clean memory; edges touching an excluded node are dropped too.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from . import config
from .db import get_db, graph_name

log = logging.getLogger("tracebrain.seed")

MEMORY_LABELS = frozenset({"User", "Session", "Episode", "Fact", "Task", "Agent"})
DEFAULT_PATH = Path(os.getenv("TRACEBRAIN_SNAPSHOT_PATH") or (config.ROOT / "deploy" / "snapshot" / "org_falkordb.json.gz"))
PAGE = 5000
BATCH = 500
FORMAT_VERSION = 1


def _graph(tenant: str | None):
    t = (tenant or config.TENANT)
    t = t[4:] if t.startswith("org_") else t  # accept "org_x" or "x"
    return get_db().select_graph(graph_name(t)), graph_name(t)


def _q(name: str) -> str:
    """Backtick-quote a label / rel type taken from a snapshot (defends against odd names)."""
    return "`" + name.replace("`", "``") + "`"


def _paged(graph, query: str):
    skip = 0
    while True:
        rows = graph.ro_query(f"{query} SKIP $skip LIMIT $limit", {"skip": skip, "limit": PAGE}).result_set
        yield from rows
        if len(rows) < PAGE:
            return
        skip += PAGE


# ---------------------------------------------------------------------- export
def export_graph(tenant: str | None = None, path: Path | str = DEFAULT_PATH, exclude_memory: bool = True) -> dict:
    graph, gname = _graph(tenant)
    excluded = MEMORY_LABELS if exclude_memory else frozenset()
    nodes, kept, skipped_nokey = [], set(), 0
    for labels, props in _paged(graph, "MATCH (n) RETURN labels(n), properties(n) ORDER BY id(n)"):
        if excluded.intersection(labels):
            continue
        if not props.get("key"):
            skipped_nokey += 1
            continue
        nodes.append({"labels": labels, "props": props})
        kept.add(props["key"])
    edges = []
    for s, sl, t, tl, typ, props in _paged(
            graph, "MATCH (a)-[r]->(b) RETURN a.key, labels(a)[0], b.key, labels(b)[0], type(r), properties(r) "
                   "ORDER BY id(r)"):
        if s in kept and t in kept:
            edges.append({"type": typ, "src": s, "dst": t, "src_label": sl, "dst_label": tl, "props": props or {}})
    snap = {
        "version": FORMAT_VERSION, "graph": gname, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "exclude_memory": exclude_memory, "counts": counts_of(nodes, edges), "nodes": nodes, "edges": edges,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, separators=(",", ":"), default=str)
    if skipped_nokey:
        log.warning("skipped %d nodes without a key", skipped_nokey)
    return {"path": str(path), "bytes": path.stat().st_size, **snap["counts"]}


def counts_of(nodes: list[dict], edges: list[dict]) -> dict:
    labels, rels = defaultdict(int), defaultdict(int)
    for n in nodes:
        for lb in n["labels"]:
            labels[lb] += 1
    for e in edges:
        rels[e["type"]] += 1
    return {"nodes": len(nodes), "edges": len(edges), "labels": dict(sorted(labels.items())),
            "rels": dict(sorted(rels.items()))}


# ---------------------------------------------------------------------- import
def load_snapshot(path: Path | str = DEFAULT_PATH) -> dict:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _ensure_schema(graph) -> None:
    try:
        from .schema import ensure_schema
        ensure_schema(graph)
    except Exception as e:  # noqa: BLE001
        log.warning("ensure_schema failed: %s", e)


def import_snapshot(tenant: str | None = None, path: Path | str = DEFAULT_PATH, include_memory: bool = False,
                    progress=None) -> dict:
    snap = load_snapshot(path)
    graph, gname = _graph(tenant)
    say = progress or (lambda m: log.info(m))
    _ensure_schema(graph)  # indexes on `key` first so the MERGE/MATCH lookups below are index scans
    excluded = frozenset() if include_memory else MEMORY_LABELS

    by_labels: dict[tuple, list] = defaultdict(list)
    keys = set()
    for n in snap["nodes"]:
        if excluded.intersection(n["labels"]):
            continue
        by_labels[tuple(n["labels"])].append({"key": n["props"]["key"], "props": n["props"]})
        keys.add(n["props"]["key"])
    t0, written = time.time(), 0
    for labels, rows in by_labels.items():
        primary, extra = labels[0], labels[1:]
        set_extra = "".join(f" SET n:{_q(lb)}" for lb in extra)
        q = f"UNWIND $rows AS row MERGE (n:{_q(primary)} {{key: row.key}}) SET n += row.props{set_extra}"
        for i in range(0, len(rows), BATCH):
            graph.query(q, {"rows": rows[i:i + BATCH]})
            written += len(rows[i:i + BATCH])
        say(f"nodes {primary}: {len(rows)}")

    by_rel: dict[tuple, list] = defaultdict(list)
    for e in snap["edges"]:
        if e["src"] in keys and e["dst"] in keys:
            by_rel[(e["type"], e["src_label"], e["dst_label"])].append(
                {"s": e["src"], "t": e["dst"], "props": e.get("props") or {}})
    rels = 0
    for (typ, sl, tl), rows in by_rel.items():
        q = (f"UNWIND $rows AS row MATCH (a:{_q(sl)} {{key: row.s}}) MATCH (b:{_q(tl)} {{key: row.t}}) "
             f"MERGE (a)-[r:{_q(typ)}]->(b) SET r += row.props")
        for i in range(0, len(rows), BATCH):
            graph.query(q, {"rows": rows[i:i + BATCH]})
        rels += len(rows)
    say(f"edges: {rels} in {len(by_rel)} groups")
    _ensure_schema(graph)
    return {"graph": gname, "nodes": written, "edges": rels, "seconds": round(time.time() - t0, 1)}


def graph_counts(tenant: str | None = None) -> dict:
    graph, _ = _graph(tenant)
    labels = {r[0]: r[1] for r in graph.ro_query("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*)").result_set}
    rels = {r[0]: r[1] for r in graph.ro_query("MATCH ()-[r]->() RETURN type(r), count(r)").result_set}
    return {"labels": dict(sorted(labels.items())), "rels": dict(sorted(rels.items()))}


def is_empty(tenant: str | None = None) -> bool:
    graph, _ = _graph(tenant)
    try:
        return not graph.ro_query("MATCH (n) RETURN n LIMIT 1").result_set
    except Exception as e:  # noqa: BLE001  (a graph that doesn't exist yet errors on some versions)
        if "empty key" in str(e).lower() or "invalid graph" in str(e).lower():
            return True
        raise


def seed_if_empty(tenant: str | None = None, path: Path | str = DEFAULT_PATH) -> dict | None:
    """Used at app startup when TRACEBRAIN_SEED_ON_START=1: import the bundled snapshot into an empty graph."""
    if not Path(path).exists():
        log.warning("seed-on-start: snapshot %s not found; skipping", path)
        return None
    if not is_empty(tenant):
        log.info("seed-on-start: graph already has data; skipping")
        return None
    log.warning("seed-on-start: graph is empty, importing %s", path)
    res = import_snapshot(tenant, path)
    log.warning("seed-on-start: done %s", res)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tracebrain.seed", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["export", "import", "counts"])
    ap.add_argument("--tenant", default=None, help=f"tenant name or graph name (default {config.TENANT})")
    ap.add_argument("--path", default=str(DEFAULT_PATH))
    ap.add_argument("--exclude-memory", dest="exclude_memory", action="store_true", default=True,
                    help="drop User/Session/Episode/Fact/Task/Agent nodes (default)")
    ap.add_argument("--include-memory", dest="exclude_memory", action="store_false",
                    help="keep memory/coordination nodes")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    target = config.FALKORDB_URL.split("@")[-1] if config.FALKORDB_URL else f"embedded {config.FALKORDB_LITE_PATH}"
    if a.cmd == "export":
        res = export_graph(a.tenant, a.path, a.exclude_memory)
    elif a.cmd == "import":
        print(f"importing into {target}", file=sys.stderr)
        res = import_snapshot(a.tenant, a.path, include_memory=not a.exclude_memory, progress=print)
    else:
        res = graph_counts(a.tenant)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
