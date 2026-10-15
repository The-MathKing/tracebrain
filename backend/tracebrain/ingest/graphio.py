"""Key builders and batched MERGE writers shared by the ingest modules."""

import re
from datetime import datetime

BATCH = 500


def person_key(login: str) -> str:
    return f"person:{login}"


def repo_key(repo: str) -> str:
    return f"repo:{repo}"


def module_key(repo: str, d: str) -> str:
    return f"module:{repo}:{d}"


def file_key(repo: str, path: str) -> str:
    return f"file:{repo}:{path}"


def issue_key(repo: str, n: int) -> str:
    return f"issue:{repo}#{n}"


def pr_key(repo: str, n: int) -> str:
    return f"pr:{repo}#{n}"


def label_key(repo: str, name: str) -> str:
    return f"label:{repo}:{name}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def topic_key(name: str) -> str:
    return f"topic:{slug(name)}"


def sync_key(repo: str) -> str:
    return f"sync:{repo}"


def ts(iso: str | None) -> int | None:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) if iso else None


def is_bot(login: str, user_type: str | None = None) -> bool:
    return login.endswith("[bot]") or user_type == "Bot"


def module_of(path: str) -> str:
    """Module of a repo path: its top-level dir ("." for root files). Under a `src/` layout
    (`[x/]src/<pkg>/<sub>/...`) it is the package's first-level subpackage, or the package itself."""
    parts = path.split("/")
    if "src" in parts[:-1]:
        i = parts.index("src")
        rest = parts[i + 1:-1]
        if rest:
            return "/".join(parts[:i + 1] + rest[:2])
    return "." if len(parts) == 1 else parts[0]


LANGS = {
    "py": "Python", "pyi": "Python", "js": "JavaScript", "jsx": "JavaScript", "ts": "TypeScript",
    "tsx": "TypeScript", "go": "Go", "rs": "Rust", "java": "Java", "kt": "Kotlin", "c": "C", "h": "C",
    "cc": "C++", "cpp": "C++", "hpp": "C++", "rb": "Ruby", "sh": "Shell", "sql": "SQL", "toml": "TOML",
    "yaml": "YAML", "yml": "YAML", "cfg": "Config", "ini": "Config", "md": "Markdown", "rst": "reST",
    "json": "JSON", "ipynb": "Notebook", "html": "HTML", "css": "CSS",
}


def language(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    if name in ("Dockerfile", "Makefile"):
        return name
    return LANGS.get(name.rsplit(".", 1)[-1].lower()) if "." in name else None


def file_node(repo: str, path: str, **extra) -> dict:
    return {"key": file_key(repo, path),
            "props": {"path": path, "title": path, "repo": repo, "language": language(path),
                      "url": f"https://github.com/{repo}/blob/HEAD/{path}", **extra}}


def module_node(repo: str, d: str) -> dict:
    return {"key": module_key(repo, d),
            "props": {"path": d, "title": f"{repo.split('/')[1]}/{d}" if d != "." else f"{repo} (root)",
                      "repo": repo, "url": f"https://github.com/{repo}/tree/HEAD/{d if d != '.' else ''}"}}


def merge_nodes(graph, label: str, rows: list[dict]) -> int:
    """rows: [{"key": str, "props": {...}}]"""
    for i in range(0, len(rows), BATCH):
        graph.query(f"UNWIND $rows AS r MERGE (n:{label} {{key: r.key}}) SET n += r.props",
                    {"rows": rows[i:i + BATCH]})
    return len(rows)


def merge_edges(graph, rel: str, src: str, dst: str, rows: list[dict]) -> int:
    """rows: [{"src": key, "dst": key, "props"?: {...}}]; both endpoints must already exist."""
    rows = [{"props": {}, **r} for r in rows]
    n = 0
    for i in range(0, len(rows), BATCH):
        res = graph.query(
            f"UNWIND $rows AS r MATCH (a:{src} {{key: r.src}}) MATCH (b:{dst} {{key: r.dst}}) "
            f"MERGE (a)-[e:{rel}]->(b) SET e += r.props RETURN count(e)",
            {"rows": rows[i:i + BATCH]})
        n += res.result_set[0][0]
    return n


def link_files_to_modules(graph, repo: str, paths) -> None:
    """MERGE File + Module nodes for `paths` and wire File-[:IN_MODULE]->Module-[:IN_REPO]->Repo."""
    paths = sorted(set(paths))
    mods = sorted({module_of(p) for p in paths})
    merge_nodes(graph, "Module", [module_node(repo, d) for d in mods])
    merge_edges(graph, "IN_REPO", "Module", "Repo", [{"src": module_key(repo, d), "dst": repo_key(repo)} for d in mods])
    merge_edges(graph, "IN_MODULE", "File", "Module",
                [{"src": file_key(repo, p), "dst": module_key(repo, module_of(p))} for p in paths])
