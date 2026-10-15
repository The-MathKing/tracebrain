from tracebrain.ingest.code import module_map, resolve_imports
from tracebrain.ingest.extract import decision_key, pick_comments
from tracebrain.ingest.github import codeowners_rows, parse_codeowners, parse_refs
from tracebrain.ingest.graphio import (
    file_key, issue_key, label_key, module_key, module_of, person_key, pr_key, repo_key, topic_key,
)

R = "acme/widget"


def test_key_formats():
    assert person_key("alice") == "person:alice"
    assert repo_key(R) == "repo:acme/widget"
    assert module_key(R, "widget") == "module:acme/widget:widget"
    assert file_key(R, "widget/a.py") == "file:acme/widget:widget/a.py"
    assert issue_key(R, 7) == "issue:acme/widget#7"
    assert pr_key(R, 8) == "pr:acme/widget#8"
    assert label_key(R, "good first issue") == "label:acme/widget:good first issue"
    assert topic_key("Query Engine!") == "topic:query-engine"
    k = decision_key("pr:acme/widget#8", "Use X")
    assert k.startswith("decision:") and len(k) == len("decision:") + 12


def test_module_of():
    assert module_of("setup.py") == "."
    assert module_of("widget/core/a.py") == "widget"
    assert module_of("src/widget/a.py") == "src/widget"
    assert module_of("sdk/src/widget/core/x/a.py") == "sdk/src/widget/core"


def test_parse_refs():
    text = "Fixes #1, closes: #2 and resolves acme/widget#3. See #4, other/repo#5, #6.\n" \
           "Also https://github.com/acme/widget/issues/9 and https://github.com/x/y/pull/10 and &#11; a#12"
    closes, refs = parse_refs(text, R)
    assert closes == {1, 2, 3}
    assert refs == {4, 6, 9}


def test_resolve_imports():
    paths = ["widget/__init__.py", "widget/core.py", "widget/util/__init__.py", "widget/util/text.py",
             "widget/sub/mod.py", "tests/test_core.py", "src/other/x.py"]
    mods = module_map(paths)
    assert resolve_imports("widget/core.py", "import os\nimport widget.util.text\nfrom .util import text\n", mods) \
        == {"widget/util/text.py"}
    got = resolve_imports("widget/sub/mod.py", "from ..core import y\nfrom ..util import text, nothing\n", mods)
    assert got == {"widget/core.py", "widget/util/text.py", "widget/util/__init__.py"}
    got = resolve_imports("tests/test_core.py", "from widget import core\nimport widget.util.text as t\n"
                                                "from other import x\nimport numpy\n", mods)
    assert got == {"widget/core.py", "widget/util/text.py", "src/other/x.py"}
    got = resolve_imports("widget/__init__.py", "from .core import *\nfrom . import util\n", mods)
    assert got == {"widget/core.py", "widget/util/__init__.py"}
    nested = module_map(["sdk/src/pkg/__init__.py", "sdk/src/pkg/core/a.py", "sdk/src/pkg/core/b.py"])
    assert resolve_imports("sdk/src/pkg/core/a.py", "from pkg.core.b import f\nfrom . import b\n", nested) == \
        {"sdk/src/pkg/core/b.py"}
    # unparsable file falls back to regex
    assert resolve_imports("widget/core.py", "from widget.util import text\nprint 'py2'\n", mods) == \
        {"widget/util/text.py"}


def test_codeowners_rows():
    rules = parse_codeowners("# comment\n*  @root\n/widget/ @alice @acme/team\ndocs/*.md bob@x.com\n*.toml @carol\n")
    assert rules == [("*", ["root"]), ("/widget/", ["alice"]), ("*.toml", ["carol"])]
    mods, files = codeowners_rows(rules, R, ["widget/a.py", "setup.py", "pyproject.toml", "widget/b.toml"])
    assert {(r["src"], r["dst"]) for r in mods} == {
        ("person:root", "module:acme/widget:widget"), ("person:root", "module:acme/widget:."),
        ("person:alice", "module:acme/widget:widget")}
    assert {r["dst"] for r in files} == {"file:acme/widget:pyproject.toml", "file:acme/widget:widget/b.toml"}


def test_pick_comments_drops_bots():
    cs = [{"user": {"login": f"u{i}"}, "body": "x" * i, "created_at": f"2024-01-{i + 1:02d}"} for i in range(1, 20)]
    cs.append({"user": {"login": "ci[bot]"}, "body": "beep", "created_at": "2024-02-01"})
    picked = pick_comments(cs)
    assert len(picked) <= 10 and all(not c["user"]["login"].endswith("[bot]") for c in picked)
    assert picked == sorted(picked, key=lambda c: c["created_at"])
