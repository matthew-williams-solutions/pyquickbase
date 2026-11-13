"""Keep the docs honest: examples must parse, and every public name must be documented."""

import ast
import re
import textwrap
from pathlib import Path

import pytest

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
# The generated reference pages have their own tests, in test_reference.py.
PAGES = [
    ROOT / "README.md",
    *sorted(path for path in (ROOT / "docs").rglob("*.md") if "reference" not in path.parent.parts),
]
FENCE = re.compile(r"^( *)```python\n(.*?)^\1```", re.DOTALL | re.MULTILINE)


def examples() -> list[tuple[str, str]]:
    return [
        (f"{page.relative_to(ROOT)}:{page.read_text()[: m.start()].count(chr(10)) + 1}", m[2])
        for page in PAGES
        for m in FENCE.finditer(page.read_text())
    ]


@pytest.mark.parametrize(("where", "code"), examples(), ids=[where for where, _ in examples()])
def test_examples_parse(where: str, code: str) -> None:
    source = textwrap.dedent(code)
    compile(source, where, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def test_there_are_examples_to_check() -> None:
    assert len(examples()) > 20


def test_install_pins_match_the_version() -> None:
    pins = {page: re.findall(r"pyquickbase==([^\s`'\"]+)", page.read_text()) for page in PAGES}
    assert all(
        pins[ROOT / page] for page in ("README.md", "docs/index.md", "docs/getting-started.md")
    )
    assert {pin for found in pins.values() for pin in found} == {pyquickbase.__version__}


def test_the_changelog_covers_this_version() -> None:
    assert f"## [{pyquickbase.__version__}]" in (ROOT / "CHANGELOG.md").read_text()


def test_field_types_lists_every_type_create_field_takes() -> None:
    """The page is hand-written, from what live tests confirmed, so its list of types is checked."""
    from pyquickbase._schema_plan import FIELD_TYPES

    page = (ROOT / "docs/field-types.md").read_text(encoding="utf-8")
    types: list[str] = []
    for table in re.findall(r"^\| `type` \|.*?(?=^$)", page, re.MULTILINE | re.DOTALL):
        types += re.findall(r"^\| `([a-z-]+)` \|", table, re.MULTILINE)[1:]  # After the header.
    assert sorted(types) == sorted(FIELD_TYPES)
    assert "Not tested" not in page


def _sdk_calls() -> tuple[set[str], set[str]]:
    """Every endpoint path, with `{}` for each part filled in, and XML action the SDK sends."""
    paths: set[str] = set()
    actions: set[str] = set()
    for path in (ROOT / "src/pyquickbase/_sync").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.JoinedStr):
                parts = node.values
                text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in parts)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            else:
                continue
            if re.fullmatch(r"/[a-z]+(/[a-z{}]+)*", text):
                paths.add(text)
    for path in (ROOT / "src/pyquickbase").glob("*.py"):
        actions |= set(re.findall(r'"(API_\w+)"', path.read_text(encoding="utf-8")))
    return paths, actions


def test_the_api_calls_page_has_every_call_the_sdk_makes() -> None:
    """Some paths are built in pieces, so each piece must be part of a path on the page."""
    page = re.sub(r"\{\w+\}", "{}", (ROOT / "docs/api-calls.md").read_text(encoding="utf-8"))
    paths, actions = _sdk_calls()
    assert paths
    assert actions
    assert [path for path in sorted(paths) if path not in page] == []
    assert [action for action in sorted(actions) if f"`{action}`" not in page] == []
