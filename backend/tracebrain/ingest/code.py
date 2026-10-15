"""Code ingest: default-branch tree -> File/Module nodes, Python imports -> (File)-[:IMPORTS]->(File)."""

import ast
import io
import re
import tarfile

from .github import GitHub
from .graphio import file_key, file_node, language, link_files_to_modules, merge_edges, merge_nodes

MAX_PY_FILES = 2000
MAX_PY_BYTES = 1_000_000
SKIP_DIRS = ("node_modules/", "vendor/", ".git/", "dist/", "build/", "__pycache__/")


def is_code(path: str) -> bool:
    lang = language(path)
    return lang is not None and lang not in ("Markdown", "reST", "JSON", "HTML", "CSS", "Notebook") \
        and not any(f"/{d}" in f"/{path}" for d in SKIP_DIRS)


async def fetch_tree(gh: GitHub, repo: str, branch: str | None = None) -> tuple[str, list[str]]:
    """(commit sha, blob paths) of the default branch."""
    if not branch:
        branch = (await gh.json(f"/repos/{repo}"))["default_branch"]
    sha = (await gh.json(f"/repos/{repo}/commits/{branch}"))["sha"]
    tree = await gh.json(f"/repos/{repo}/git/trees/{sha}", {"recursive": "1"})
    return sha, [t["path"] for t in tree["tree"] if t["type"] == "blob"]


# ---------------------------------------------------------------- import resolution

def module_map(paths: list[str]) -> dict[str, str]:
    """dotted module name -> path for every .py file (also without a leading `src.`)."""
    out = {}
    for p in paths:
        if not p.endswith(".py"):
            continue
        mod = p[:-3].replace("/", ".")
        mod = mod.removesuffix(".__init__")
        out.setdefault(mod, p)
        if "src" in mod.split(".")[:-1]:  # src layout (also nested, e.g. sdk/src/pkg/...)
            out.setdefault(mod.split(".src.", 1)[-1].removeprefix("src."), p)
    return out


def _package(path: str, mods: dict[str, str]) -> str:
    mod = path[:-3].replace("/", ".")
    pkg = mod.removesuffix(".__init__") if mod.endswith(".__init__") else mod.rpartition(".")[0]
    if "src" in pkg.split("."):
        short = pkg.split(".src.", 1)[-1].removeprefix("src.")
        return short if short in mods or any(m.startswith(short + ".") for m in mods) else pkg
    return pkg


def _resolve(name: str, mods: dict[str, str]) -> str | None:
    parts = name.split(".")
    for i in range(len(parts), 0, -1):  # longest prefix that is a repo module
        if (p := mods.get(".".join(parts[:i]))):
            return p
    return None


def _raw_imports(source: str) -> list[tuple[int, str | None, list[str]]]:
    """[(level, module, names)] for import statements; regex fallback when the file doesn't parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        out = []
        for m in re.finditer(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+([\w, ]+)|^\s*import\s+([\w., ]+)", source, re.M):
            if m.group(4):
                out += [(0, n.strip().split(" ")[0], []) for n in m.group(4).split(",") if n.strip()]
            else:
                out.append((len(m.group(1)), m.group(2) or None, [n.strip() for n in m.group(3).split(",")]))
        return out
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(0, a.name, []) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            out.append((node.level, node.module, [a.name for a in node.names]))
    return out


def resolve_imports(path: str, source: str, mods: dict[str, str]) -> set[str]:
    """Repo paths imported by the file at `path`."""
    found = set()
    for level, module, names in _raw_imports(source):
        if level:
            base = _package(path, mods).split(".") if _package(path, mods) else []
            base = base[:len(base) - (level - 1)] if level > 1 else base
            module = ".".join(base + ([module] if module else []))
        if not module:
            continue
        real = [n for n in names if n != "*"]
        targets = [f"{module}.{n}" for n in real if f"{module}.{n}" in mods]  # `from pkg import submodule`
        found.update(mods[t] for t in targets)
        if len(targets) < len(real) or not real:
            if (p := _resolve(module, mods)):
                found.add(p)
    found.discard(path)
    return found


# ---------------------------------------------------------------- ingest

async def ingest_code(gh: GitHub, graph, repo: str, branch: str | None = None, log=print,
                      max_py: int = MAX_PY_FILES) -> tuple[dict, list[str]]:
    """Returns (stats, all blob paths); the paths feed CODEOWNERS matching."""
    sha, paths = await fetch_tree(gh, repo, branch)
    code = [p for p in paths if is_code(p)]
    merge_nodes(graph, "File", [file_node(repo, p, in_tree=True, url=f"https://github.com/{repo}/blob/{sha}/{p}")
                                for p in code])
    link_files_to_modules(graph, repo, code)

    py = [p for p in code if p.endswith(".py")]
    mods = module_map(py)
    sources = await fetch_sources(gh, repo, sha, set(py[:max_py]))
    log(f"{repo}: {len(code)} code files, {len(sources)} .py sources from tarball")
    rows = [{"src": file_key(repo, p), "dst": file_key(repo, t)}
            for p, s in sorted(sources.items()) for t in sorted(resolve_imports(p, s, mods))]
    imports = merge_edges(graph, "IMPORTS", "File", "File", rows)
    return {"files": len(code), "py_parsed": len(sources), "imports": imports, "commit": sha}, paths


async def fetch_sources(gh: GitHub, repo: str, sha: str, wanted: set[str]) -> dict[str, str]:
    """{path: source} for `wanted` paths from ONE tarball (redirects to codeload, outside the core limit)."""
    r = await gh.get(f"/repos/{repo}/tarball/{sha}")
    if r.status_code != 200:
        return {}
    out = {}
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:*") as tar:
        for m in tar:
            path = m.name.split("/", 1)[-1]  # strip "<owner>-<repo>-<sha>/"
            if m.isfile() and path in wanted and m.size <= MAX_PY_BYTES:
                out[path] = tar.extractfile(m).read().decode("utf-8", "replace")
    return out
