"""The API reference: it must cover each class, and stop the build on any gaps."""

import ast
import importlib
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

import pyquickbase
from pyquickbase._response import error_from_response

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
build_reference = importlib.import_module("build_reference")
build_llms = importlib.import_module("build_llms")

RESOURCES = {resource.name: resource for resource in build_reference.RESOURCES}
TABLE = RESOURCES["Table"]
METHODS = {name: build_reference.read_methods(item) for name, item in RESOURCES.items()}
SYMBOL_PAGES = {page.name: page for page in build_reference.SYMBOL_PAGES}
SYMBOLS = {page.name: build_reference.read_symbols(page) for page in SYMBOL_PAGES.values()}
SITE = "https://example.com/docs/"
PAGES = {
    **{name: build_reference.build_page(RESOURCES[name], METHODS[name]) for name in RESOURCES},
    **{
        name: build_reference.build_symbol_page(SYMBOL_PAGES[name], SYMBOLS[name])
        for name in SYMBOLS
    },
}
MARKDOWN = {
    **{
        name: build_reference.build_markdown(RESOURCES[name], METHODS[name], SITE)
        for name in RESOURCES
    },
    **{
        name: build_reference.build_symbol_markdown(SYMBOL_PAGES[name], SYMBOLS[name], SITE)
        for name in SYMBOLS
    },
}
EVERY_SYMBOL = [(page, name) for page in SYMBOLS for name in SYMBOLS[page]]
EVERY_METHOD = [(name, method) for name in RESOURCES for method in METHODS[name]]


def ids(params: list[tuple[str, str]]) -> list[str]:
    return [f"{name}.{method}" for name, method in params]


@pytest.mark.parametrize(("name", "member"), EVERY_METHOD, ids=ids(EVERY_METHOD))
def test_every_member_is_on_both_versions_of_the_page(name: str, member: str) -> None:
    method = METHODS[name][member]
    assert f"### `{method.heading}` {{#{method.anchor}}}" in PAGES[name]
    assert method.heading in MARKDOWN[name]


@pytest.mark.parametrize(("name", "member"), EVERY_METHOD, ids=ids(EVERY_METHOD))
def test_examples_parse_and_show_each_client_properly(name: str, member: str) -> None:
    method = METHODS[name][member]
    compile(method.async_code, member, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    compile(method.sync_code, member, "exec")
    for text in ("await ", "async ", "Async", "aclose"):
        assert text not in method.sync_code


@pytest.mark.parametrize(("name", "member"), EVERY_METHOD, ids=ids(EVERY_METHOD))
def test_examples_fit_on_a_phone(name: str, member: str) -> None:
    method = METHODS[name][member]
    assert max(len(line) for line in method.async_code.splitlines()) <= 62


@pytest.mark.parametrize(("page", "name"), EVERY_SYMBOL, ids=ids(EVERY_SYMBOL))
def test_every_name_is_on_both_versions_of_its_page(page: str, name: str) -> None:
    assert f"### `{name}` {{#{name}}}" in PAGES[page]
    assert f"### `{name}`" in MARKDOWN[page]


@pytest.mark.parametrize(("page", "name"), EVERY_SYMBOL, ids=ids(EVERY_SYMBOL))
def test_a_name_has_an_example_unless_there_is_a_reason(page: str, name: str) -> None:
    symbol = SYMBOLS[page][name]
    excused = name in build_reference.NO_EXAMPLE or name in build_reference.ALIASES
    assert bool(symbol.sync_code) != excused
    if symbol.sync_code:
        compile(symbol.async_code, name, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        compile(symbol.sync_code, name, "exec")
        assert max(len(line) for line in symbol.async_code.splitlines()) <= 62
        for text in ("await ", "async ", "Async", "aclose"):
            assert text not in symbol.sync_code
    else:
        assert symbol.definition, "Something must show what it is."


@pytest.mark.parametrize("name", sorted(set(pyquickbase.__all__) - {"__version__"}))
def test_every_public_name_is_in_the_overview(name: str) -> None:
    overview = (ROOT / "docs/reference.md").read_text()
    assert f"`{name}`" in overview
    assert name in build_reference.NAME_LINKS


def test_the_overview_lists_only_public_names() -> None:
    overview = (ROOT / "docs/reference.md").read_text()
    table = overview[overview.index("| Page |") :]
    listed = {
        name
        for row in table.splitlines()[2:]
        for name in re.findall(r"`(\w+)`", row.rsplit("|", 2)[1])
    }
    assert listed == set(pyquickbase.__all__) - {"__version__"}


def test_a_public_name_without_a_page_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    links = {**build_reference.NAME_LINKS}
    del links["WriteResult"]
    monkeypatch.setattr(build_reference, "NAME_LINKS", links)
    with pytest.raises(SystemExit, match="WriteResult"):
        build_reference.check_every_name_is_covered()


def test_an_attribute_without_a_description_stops_the_build() -> None:
    doc = build_reference.parse_docstring("A thing.", "Page")
    with pytest.raises(SystemExit, match=r"Page\.records has no description"):
        build_reference._attributes(pyquickbase.Page, doc, has_constructor=False)


def test_the_full_file_uses_sync_examples_only() -> None:
    pages = build_reference.full_text(SITE)
    assert [title for title, _, _ in pages] == [item[0] for item in build_llms.REFERENCE_PAGES]
    for _, _, text in pages:
        assert "Async example" not in text


def test_a_401_explains_what_it_can_mean() -> None:
    response = httpx.Response(401, json={"message": "Access denied"})
    error = error_from_response(response, "GET", "/apps/bq1")
    assert isinstance(error, pyquickbase.AuthenticationError)
    assert "doesn't exist" in str(error)
    assert "bad token" in str(error)
    assert error.message == "Access denied"


def test_other_statuses_leave_out_the_hint_about_tokens() -> None:
    error = error_from_response(httpx.Response(404, json={}), "GET", "/x")
    assert "token" not in str(error)


@pytest.mark.parametrize("name", PAGES)
def test_anchors_are_unique(name: str) -> None:
    anchors = re.findall(r"\{#([\w-]+)\}", PAGES[name])
    assert len(anchors) == len(set(anchors))


@pytest.mark.parametrize("name", PAGES)
def test_in_page_links_reach_an_anchor(name: str) -> None:
    anchors = set(re.findall(r"\{#([\w-]+)\}", PAGES[name]))
    assert set(re.findall(r"\]\(#([\w-]+)\)", PAGES[name])) <= anchors


@pytest.mark.parametrize("name", PAGES)
def test_each_markdown_page_links_to_the_others(name: str) -> None:
    """The site's nav links the pages, so only the plain Markdown needs its own links."""
    for other in build_reference.ALL_PAGES:
        if other.name != name:
            assert f"reference/{other.slug}.txt" in MARKDOWN[name]


@pytest.mark.parametrize("name", PAGES)
def test_the_markdown_version_is_plain(name: str) -> None:
    for text in ("<div", "markdown>", '=== "', "{#"):
        assert text not in MARKDOWN[name]
    assert "](../" not in MARKDOWN[name]
    assert "pyquickbase._" not in MARKDOWN[name]


def test_links_go_to_a_class_page_where_there_is_one() -> None:
    assert "[`WriteResult`](records.md#WriteResult)" in PAGES["Table"]
    assert "(https://example.com/docs/reference/records/#WriteResult)" in MARKDOWN["Table"]
    assert "[`Table`](table.md)" in PAGES["App"]
    assert "[`App.table()`](app.md#table)" in PAGES["Table"]
    assert "(https://example.com/docs/reference/app/#table)" in MARKDOWN["Table"]


def test_from_env_describes_the_options_it_shares_with_the_constructor() -> None:
    parameters = {item.name: item for item in METHODS["Quickbase"]["from_env"].parameters}
    assert parameters["timeout"].description.startswith("Seconds to allow for each step")
    assert parameters["token_env"].description.startswith("The name of the variable")


def test_the_async_only_name_is_mentioned() -> None:
    assert "The async client calls this `aclose()`." in PAGES["Quickbase"]
    assert ".aclose(" in METHODS["Quickbase"]["close"].async_code
    assert ".close(" in METHODS["Quickbase"]["close"].sync_code


def test_a_property_shows_its_type_without_a_returns_section() -> None:
    method = METHODS["App"]["id"]
    assert method.signature == "id: str"
    assert not any("**Returns**" in line for line in build_reference._describe(method, {}))


def test_a_method_without_an_example_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_reference, "EXAMPLES", tmp_path)
    monkeypatch.setattr(build_reference, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="has no example"):
        build_reference.read_method(TABLE, "insert", {})


def test_an_example_for_no_method_stops_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = ROOT / "docs_src/reference/app"
    folder = tmp_path / "app"
    folder.mkdir()
    for path in source.iterdir():
        (folder / path.name).write_bytes(path.read_bytes())
    (folder / "gone.py").write_text("print(1)")
    monkeypatch.setattr(build_reference, "EXAMPLES", tmp_path)
    with pytest.raises(SystemExit, match="aren't for anything public"):
        build_reference.read_methods(RESOURCES["App"])


def test_a_method_without_a_description_stops_the_build() -> None:
    with pytest.raises(SystemExit, match="has no description"):
        build_reference.parse_docstring(None, "Table.insert")


def test_a_method_missing_from_the_groups_stops_the_build() -> None:
    resource = TABLE._replace(groups=TABLE.groups[1:])
    with pytest.raises(SystemExit, match="don't match its public methods"):
        build_reference.read_methods(resource)


def test_clients_that_differ_stop_the_build() -> None:
    resource = TABLE._replace(async_class=pyquickbase.AsyncApp)
    with pytest.raises(SystemExit, match="no longer have"):
        build_reference.read_methods(resource)


def test_an_argument_without_a_description_stops_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build_reference, "missing_descriptions", lambda _methods: ["insert(rows)"])
    with pytest.raises(SystemExit, match=r"no description: insert\(rows\)"):
        build_reference.read_methods(TABLE)


def test_a_docstring_describing_an_unknown_argument_stops_the_build() -> None:
    doc = build_reference.parse_docstring("Do it.\n\nArgs:\n    nope: Not real.", "Table.x")
    with pytest.raises(SystemExit, match="unknown arguments"):
        build_reference._parameters(pyquickbase.Table.count, doc, "Table.x")


def test_a_named_method_that_does_not_exist_stops_the_build() -> None:
    with pytest.raises(SystemExit, match="doesn't exist"):
        build_reference._add_links("See `App.nope()`.", {})


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


def test_method_links_only_follow_the_call_form() -> None:
    linked: Any = build_reference._add_links("A `count` of 0, unlike `count()`.", METHODS["Table"])
    assert linked == "A `count` of 0, unlike [`count()`](#count)."


def test_llms_index_points_at_each_page() -> None:
    index = build_llms.build_index(build_llms.read_config())
    for page in build_reference.ALL_PAGES:
        assert f"reference/{page.slug}.txt): " in index


def test_the_pages_match_the_list_in_build_llms() -> None:
    assert [item[:2] for item in build_llms.REFERENCE_PAGES] == [
        (item.name, item.slug) for item in build_reference.ALL_PAGES
    ]


def test_tables_and_lists_in_a_docstring_keep_their_lines() -> None:
    doc = build_reference.parse_docstring(
        "Summary.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- One item that\n  wraps.\n- Two.",
        "x",
    )
    assert doc.prose == ["| A | B |\n|---|---|\n| 1 | 2 |", "- One item that wraps.\n- Two."]
