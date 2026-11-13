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
    title = build_reference.TITLES[RESOURCES[name].slug][member]
    assert f"### {title} {{#{method.anchor}}}" in PAGES[name]
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
    assert max(len(line) for line in method.async_code.splitlines()) <= build_reference.LINE_LIMIT


@pytest.mark.parametrize(("page", "name"), EVERY_SYMBOL, ids=ids(EVERY_SYMBOL))
def test_every_name_is_on_both_versions_of_its_page(page: str, name: str) -> None:
    titles = build_reference.TITLES.get(SYMBOL_PAGES[page].slug, {})
    heading = titles.get(name, f"`{name}`")
    assert f"### {heading} {{#{name}}}" in PAGES[page]
    assert f"### `{name}`" in MARKDOWN[page]


@pytest.mark.parametrize(("page", "name"), EVERY_SYMBOL, ids=ids(EVERY_SYMBOL))
def test_only_what_is_called_has_a_title(page: str, name: str) -> None:
    titled = name in build_reference.TITLES.get(SYMBOL_PAGES[page].slug, {})
    assert titled == build_reference._is_called(name, SYMBOLS[page][name])


@pytest.mark.parametrize(("page", "name"), EVERY_SYMBOL, ids=ids(EVERY_SYMBOL))
def test_a_name_has_an_example_unless_there_is_a_reason(page: str, name: str) -> None:
    symbol = SYMBOLS[page][name]
    excused = name in build_reference.NO_EXAMPLE or name in build_reference.ALIASES
    assert bool(symbol.sync_code) != excused
    if symbol.sync_code:
        compile(symbol.async_code, name, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        compile(symbol.sync_code, name, "exec")
        assert (
            max(len(line) for line in symbol.async_code.splitlines()) <= build_reference.LINE_LIMIT
        )
        for text in ("await ", "async ", "Async", "aclose"):
            assert text not in symbol.sync_code
    else:
        assert symbol.definition, "Something must show what it is."


@pytest.mark.parametrize("name", sorted(set(pyquickbase.__all__) - {"__version__"}))
def test_every_public_name_is_in_the_overview(name: str) -> None:
    overview = build_reference.build_overview()
    assert f"- [`{name}`]({build_reference.NAME_LINKS[name]})" in overview


def test_the_overview_lists_only_public_names() -> None:
    overview = build_reference.build_overview()
    names = overview[overview.index("{#names}") :]
    listed = set(re.findall(r"^- \[`(\w+)`\]", names, re.MULTILINE))
    assert listed == set(pyquickbase.__all__) - {"__version__"}


def test_the_overview_has_a_card_for_each_section() -> None:
    overview = build_reference.build_overview()
    for section in build_reference.SECTIONS:
        assert f"-   **{section.title}**" in overview
        for slug, _ in section.pages:
            assert f"]({slug}.md) " in overview


def test_the_nav_matches_the_sections() -> None:
    nav = [page for page in build_llms.read_config().pages if page.section == "Reference"]
    expected = [("Overview", "reference/index.md")]
    for section in build_reference.SECTIONS:
        expected += [
            (build_reference.PAGES_BY_SLUG[slug].name, f"reference/{slug}.md")
            for slug, _ in section.pages
        ]
        expected += [(title, path) for title, path, _ in section.extra]
    assert [(page.title, page.path) for page in nav] == expected


def test_a_page_outside_every_section_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_reference, "SECTIONS", build_reference.SECTIONS[1:])
    with pytest.raises(SystemExit, match="exactly one section"):
        build_reference.check_every_name_is_covered()


def test_a_public_name_without_a_page_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    links = {**build_reference.NAME_LINKS}
    del links["WriteResult"]
    monkeypatch.setattr(build_reference, "NAME_LINKS", links)
    with pytest.raises(SystemExit, match="WriteResult"):
        build_reference.check_every_name_is_covered()


def test_an_attribute_without_a_description_stops_the_build() -> None:
    doc = build_reference.parse_docstring("A thing.", "RecordPage")
    with pytest.raises(SystemExit, match=r"Page\.records has no description"):
        build_reference._attributes(pyquickbase.RecordPage, doc, has_constructor=False)


def test_the_full_file_uses_sync_examples_only() -> None:
    pages = build_reference.full_text(SITE)
    assert [title for title, _, _ in pages] == [item[0] for item in build_llms.reference_pages()]
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
        "Returns:\n    A thing.\n\nRaises:\n    InvalidQueryError: Bad.",
        "Table.x",
    )
    assert doc.summary == "Summary."
    assert doc.prose == ["More text."]
    assert doc.args == {"a": "First line."}
    assert doc.returns == "A thing."
    assert doc.raises == [("InvalidQueryError", "Bad.")]


def test_method_links_only_follow_the_call_form() -> None:
    linked: Any = build_reference._add_links("A `count` of 0, unlike `count()`.", METHODS["Table"])
    assert linked == "A `count` of 0, unlike [`count()`](#count)."


def test_llms_index_points_at_each_page() -> None:
    index = build_llms.build_index(build_llms.read_config())
    for page in build_reference.ALL_PAGES:
        assert f"reference/{page.slug}.txt): " in index


def test_build_llms_lists_every_page() -> None:
    listed = {slug for _, slug, _ in build_llms.reference_pages()}
    assert listed == {item.slug for item in build_reference.ALL_PAGES}


def test_tables_and_lists_in_a_docstring_keep_their_lines() -> None:
    doc = build_reference.parse_docstring(
        "Summary.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- One item that\n  wraps.\n- Two.",
        "x",
    )
    assert doc.prose == ["| A | B |\n|---|---|\n| 1 | 2 |", "- One item that wraps.\n- Two."]


_PYTHON_BLOCK = re.compile(r"^( *)```python\n(.*?)^\1```", re.DOTALL | re.MULTILINE)


_DESCRIPTION = re.compile(
    r'<div class="ref-text" markdown>(.*?)</div>\n<div class="ref-code"', re.DOTALL
)


def _long_lines(text: str, limit: int) -> list[str]:
    return [
        line.removeprefix(indent)
        for indent, block in _PYTHON_BLOCK.findall(text)
        for line in block.splitlines()
        if len(line.removeprefix(indent)) > limit
    ]


@pytest.mark.parametrize("name", sorted(PAGES))
def test_python_on_a_page_fits_its_column(name: str) -> None:
    """Code wider than its column hides past the edge of its box, so nothing may be."""
    page = PAGES[name]
    assert _long_lines(page, build_reference.LINE_LIMIT) == []
    for description in _DESCRIPTION.findall(page):
        assert _long_lines(description, build_reference.TEXT_LINE_LIMIT) == []


def _code_shown(name: str) -> list[str]:
    if name in METHODS:
        return [method.signature for method in METHODS[name].values()]
    shown: list[str] = []
    for symbol in SYMBOLS[name].values():
        shown += [symbol.signature, symbol.definition]
        shown += [method.signature for method in symbol.methods]
    return [code for code in shown if code]


def _as_python(code: str) -> str:
    """Turn a signature, with each overload on its own, into source that `ast` can parse."""
    if re.match(r"^\w+ = |^\w+: ", code):
        return code
    if code.startswith("class "):
        return f"{code}: ..."
    return "\n".join(f"def {part}: ..." for part in re.split(r"\n(?=\w+\()", code))


@pytest.mark.parametrize("name", sorted(PAGES))
def test_signatures_and_definitions_are_python(name: str) -> None:
    for code in _code_shown(name):
        ast.parse(_as_python(code))


def test_a_long_alias_is_shown_by_name() -> None:
    methods = {method.heading: method for method in SYMBOLS["Policies"]["BoundPolicy"].methods}
    signature = methods["BoundPolicy.allows()"].signature
    assert "verb: PolicyVerb," in signature
    assert "Literal" not in signature


def test_a_split_union_is_put_in_parentheses() -> None:
    code = build_reference.format_code(
        "rule(tables: str | TableSelector | Sequence[str | TableSelector], "
        "fields: str | int | FieldSelector | Sequence[str | int | FieldSelector] | None = None)"
    )
    assert code == (
        "rule(\n"
        "    tables: (\n"
        "        str | TableSelector | Sequence[str | TableSelector]\n"
        "    ),\n"
        "    fields: (\n"
        "        str\n"
        "        | int\n"
        "        | FieldSelector\n"
        "        | Sequence[str | int | FieldSelector]\n"
        "        | None\n"
        "    ) = None,\n"
        ")"
    )


ERROR_CLASSES = [
    item
    for item in vars(pyquickbase.errors).values()
    if isinstance(item, type) and issubclass(item, pyquickbase.QuickbaseError)
]


@pytest.mark.parametrize("error", ERROR_CLASSES, ids=lambda item: item.__name__)
def test_every_errors_doc_url_points_to_an_anchor_on_the_errors_page(error: type) -> None:
    (page,) = [item for item in SYMBOL_PAGES.values() if item.slug == "errors"]
    site = re.search(r"^site_url: (\S+)$", (ROOT / "mkdocs.yml").read_text(), re.M)
    assert site is not None
    anchor = error.__name__
    assert f"{site[1]}reference/{page.slug}/" == pyquickbase.errors.DOCS_URL
    assert f"{{#{anchor}}}" in PAGES[page.name]
