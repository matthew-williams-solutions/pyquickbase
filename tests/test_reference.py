"""The two-column API reference: it must cover Table, and stop the build on any gaps."""

import ast
import importlib
import inspect
import sys
from pathlib import Path

import pytest

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
build_reference = importlib.import_module("build_reference")
build_llms = importlib.import_module("build_llms")

METHODS = build_reference.read_methods()
PAGE = build_reference.build_page(METHODS)
MARKDOWN = build_reference.build_markdown(METHODS, "https://example.com/docs/")
PUBLIC = [
    name
    for name, member in vars(pyquickbase.Table).items()
    if inspect.isfunction(member) and not name.startswith("_")
]


@pytest.mark.parametrize("name", PUBLIC)
def test_every_method_is_on_both_versions_of_the_page(name: str) -> None:
    assert f"### `{name}()` {{#{name}}}" in PAGE
    assert f"### `Table.{name}()`" in MARKDOWN


@pytest.mark.parametrize("name", PUBLIC)
def test_examples_parse_and_show_each_client_properly(name: str) -> None:
    method = METHODS[name]
    compile(method.async_code, name, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    compile(method.sync_code, name, "exec")
    assert "await " not in method.sync_code
    assert "async " not in method.sync_code


@pytest.mark.parametrize("name", PUBLIC)
def test_examples_fit_in_the_code_column(name: str) -> None:
    assert max(len(line) for line in METHODS[name].sync_code.splitlines()) <= 62
    assert max(len(line) for line in METHODS[name].async_code.splitlines()) <= 62


def test_a_method_without_an_example_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_reference, "EXAMPLES", tmp_path)
    monkeypatch.setattr(build_reference, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="has no example"):
        build_reference.read_method("insert")


def test_a_method_without_a_description_stops_the_build() -> None:
    with pytest.raises(SystemExit, match="has no description"):
        build_reference.parse_docstring(None, "Table.insert")


def test_a_method_missing_from_the_groups_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_reference, "GROUPS", build_reference.GROUPS[1:])
    with pytest.raises(SystemExit, match="GROUPS doesn't match"):
        build_reference.read_methods()


def test_an_argument_without_a_description_stops_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build_reference, "missing_descriptions", lambda _methods: ["insert(rows)"])
    with pytest.raises(SystemExit, match=r"no description: insert\(rows\)"):
        build_reference.read_methods()


def test_a_docstring_describing_an_unknown_argument_stops_the_build() -> None:
    doc = build_reference.parse_docstring("Do it.\n\nArgs:\n    nope: Not real.", "Table.x")
    with pytest.raises(SystemExit, match="unknown arguments"):
        build_reference._parameters(pyquickbase.Table.count, doc, "Table.x")


def test_docstrings_are_split_into_their_parts() -> None:
    doc = build_reference.parse_docstring(
        "Summary.\n\nMore text.\n\nArgs:\n    a: First\n        line.\n\n"
        "Returns:\n    A thing.\n\nRaises:\n    QueryError: Bad.",
        "Table.x",
    )
    assert doc.summary == "Summary."
    assert doc.prose == ["More text."]
    assert doc.args == {"a": "First line."}
    assert doc.returns == "A thing."
    assert doc.raises == [("QueryError", "Bad.")]


def test_the_markdown_version_is_plain() -> None:
    for text in ("<div", "markdown>", '=== "', "{#"):
        assert text not in MARKDOWN
    assert "](../" not in MARKDOWN
    assert "(https://example.com/docs/reference/#pyquickbase.WriteResult)" in MARKDOWN


def test_every_link_from_the_page_goes_to_a_public_name() -> None:
    assert "`WriteResult`](../reference.md#pyquickbase.WriteResult)" in PAGE
    assert "`_" not in PAGE
    assert "pyquickbase._" not in MARKDOWN


def test_method_links_only_follow_the_call_form() -> None:
    linked = build_reference._add_links("A `count` of 0, unlike `count()`.", METHODS)
    assert linked == "A `count` of 0, unlike [`count()`](#count)."


def test_llms_index_points_at_the_page() -> None:
    assert "reference/table.txt): " in build_llms.build_index(build_llms.read_config())
