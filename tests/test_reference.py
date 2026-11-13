"""The API reference: it must cover each class and name, and stop the build on any gaps."""

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

CLASSES = {source.name: source for source in build_reference.CLASSES}
TABLE = CLASSES["Table"]
REFERENCE = build_reference.read_reference()
METHODS = REFERENCE.methods
SYMBOLS = REFERENCE.symbols
SITE = "https://example.com/docs/"
PAGES_BY_SLUG = build_reference.PAGES_BY_SLUG
PAGES = {slug: build_reference.build_page(page, REFERENCE) for slug, page in PAGES_BY_SLUG.items()}
MARKDOWN = {
    slug: build_reference.build_markdown(page, REFERENCE, SITE)
    for slug, page in PAGES_BY_SLUG.items()
}
PAGE_OF = {
    member: page.slug
    for page in build_reference.PAGES
    for group in page.groups
    for member in group.members
}
EVERY_SYMBOL = sorted(SYMBOLS)
EVERY_METHOD = [(owner, name) for owner, methods in METHODS.items() for name in methods]


def ids(params: list[tuple[str, str]]) -> list[str]:
    return [f"{owner}.{name}" for owner, name in params]


def member(owner: str, name: str) -> str:
    return f"{owner}.{name}"


@pytest.mark.parametrize(("owner", "name"), EVERY_METHOD, ids=ids(EVERY_METHOD))
def test_every_method_is_on_both_versions_of_its_page(owner: str, name: str) -> None:
    method = METHODS[owner][name]
    slug = PAGE_OF[member(owner, name)]
    title = build_reference.TITLES[member(owner, name)]
    label = build_reference.method_labels(PAGES_BY_SLUG[slug], REFERENCE)[member(owner, name)]
    heading = f'### {title} <code class="ref-call">{label}</code> {{#{method.anchor}}}'
    assert heading in PAGES[slug]
    assert method.heading in MARKDOWN[slug]


@pytest.mark.parametrize(("owner", "name"), EVERY_METHOD, ids=ids(EVERY_METHOD))
def test_examples_parse_and_show_each_client_properly(owner: str, name: str) -> None:
    method = METHODS[owner][name]
    compile(method.async_code, name, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    compile(method.sync_code, name, "exec")
    for text in ("await ", "async ", "Async", "aclose"):
        assert text not in method.sync_code


@pytest.mark.parametrize(("owner", "name"), EVERY_METHOD, ids=ids(EVERY_METHOD))
def test_examples_fit_on_a_phone(owner: str, name: str) -> None:
    method = METHODS[owner][name]
    assert max(len(line) for line in method.async_code.splitlines()) <= build_reference.LINE_LIMIT


@pytest.mark.parametrize("name", EVERY_SYMBOL)
def test_every_name_is_on_both_versions_of_its_page(name: str) -> None:
    slug = PAGE_OF[name]
    if build_reference._is_called(name, SYMBOLS[name]):
        title = build_reference.TITLES[name]
        heading = f'### {title} <code class="ref-call">{name}()</code> {{#{name}}}'
    else:
        heading = f"### `{name}` {{#{name}}}"
    assert heading in PAGES[slug]
    assert f"### `{name}`" in MARKDOWN[slug]


@pytest.mark.parametrize("name", sorted(CLASSES))
def test_every_class_has_its_own_entry(name: str) -> None:
    slug = PAGE_OF[name]
    assert f"### `{name}` {{#{name}}}" in PAGES[slug]
    assert build_reference.NAME_LINKS[name] == f"{slug}.md#{name}"
    async_name = CLASSES[name].async_class.__name__
    assert build_reference.NAME_LINKS[async_name] == f"{slug}.md#{name}"


@pytest.mark.parametrize("name", EVERY_SYMBOL)
def test_only_what_is_called_has_a_title(name: str) -> None:
    titled = name in build_reference.TITLES
    assert titled == build_reference._is_called(name, SYMBOLS[name])


@pytest.mark.parametrize("name", EVERY_SYMBOL)
def test_a_name_has_an_example_unless_there_is_a_reason(name: str) -> None:
    symbol = SYMBOLS[name]
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


def test_titles_match_what_is_called() -> None:
    build_reference.check_titles(REFERENCE)


def test_a_missing_title_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    titles = {**build_reference.TITLES}
    del titles["Table.upsert"]
    monkeypatch.setattr(build_reference, "TITLES", titles)
    with pytest.raises(SystemExit, match=r"Table\.upsert"):
        build_reference.check_titles(REFERENCE)


@pytest.mark.parametrize("name", sorted(set(pyquickbase.__all__) - {"__version__"}))
def test_every_public_name_is_in_the_overview(name: str) -> None:
    overview = build_reference.build_overview()
    assert f"- [`{name}`]({build_reference.NAME_LINKS[name]})" in overview


def test_the_overview_lists_only_public_names() -> None:
    overview = build_reference.build_overview()
    names = overview[overview.index("{#names}") : overview.index("{#methods}")]
    listed = set(re.findall(r"^- \[`(\w+)`\]", names, re.MULTILINE))
    assert listed == set(pyquickbase.__all__) - {"__version__"}


@pytest.mark.parametrize(("owner", "name"), EVERY_METHOD, ids=ids(EVERY_METHOD))
def test_every_method_is_in_the_overview(owner: str, name: str) -> None:
    if name == build_reference.CONSTRUCTOR:
        return
    overview = build_reference.build_overview()
    link = build_reference.METHOD_LINKS[(owner, name)]
    assert f"- [`{name}`]({link}) {owner}" in overview


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


def test_old_pages_redirect_to_where_their_contents_went() -> None:
    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    block = config[config.index("redirect_maps:") :].split("\n\n")[0]
    found = dict(re.findall(r"^\s+reference/([\w-]+)\.md: reference/([\w-]+)\.md$", block, re.M))
    assert found == build_reference.OLD_PAGES
    assert not set(found) & set(PAGES_BY_SLUG), "A redirect can't hide a page that exists."
    assert set(found.values()) <= set(PAGES_BY_SLUG)


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


def _without(slug: str, removed: str) -> tuple[Any, ...]:
    """Return the pages with one member taken off one page."""
    pages = []
    for page in build_reference.PAGES:
        if page.slug == slug:
            groups = tuple(
                group._replace(members=tuple(m for m in group.members if m != removed))
                for group in page.groups
            )
            page = page._replace(groups=groups)
        pages.append(page)
    return tuple(pages)


def test_a_method_missing_from_the_pages_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_reference, "PAGES", _without("writes", "Table.upsert"))
    monkeypatch.setattr(build_reference, "METHOD_LINKS", build_reference._method_links())
    with pytest.raises(SystemExit, match="public methods of Table: \\['upsert'\\]"):
        build_reference.check_every_name_is_covered()


def test_something_on_two_pages_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    first = build_reference.PAGES[0]
    doubled = first._replace(
        groups=(*first.groups, build_reference.Group("Again", "again", None, ("WriteResult",)))
    )
    monkeypatch.setattr(build_reference, "PAGES", (doubled, *build_reference.PAGES[1:]))
    with pytest.raises(SystemExit, match="more than one page: \\['WriteResult'\\]"):
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


@pytest.mark.parametrize("slug", PAGES)
def test_anchors_are_unique(slug: str) -> None:
    anchors = re.findall(r"\{#([\w-]+)\}", PAGES[slug])
    assert len(anchors) == len(set(anchors))


def _anchors() -> dict[str, set[str]]:
    return {slug: set(re.findall(r"\{#([\w-]+)\}", page)) for slug, page in PAGES.items()}


@pytest.mark.parametrize("slug", PAGES)
def test_links_between_reference_pages_reach_an_anchor(slug: str) -> None:
    anchors = _anchors()
    for target, anchor in re.findall(r"\]\(([\w-]*)(?:\.md)?#([\w-]+)\)", PAGES[slug]):
        assert anchor in anchors[target or slug], f"{target or slug}#{anchor}"


@pytest.mark.parametrize("slug", PAGES)
def test_each_markdown_page_links_to_the_others(slug: str) -> None:
    """The site's nav links the pages, so only the plain Markdown needs its own links."""
    for other in build_reference.PAGES:
        if other.slug != slug:
            assert f"reference/{other.slug}.txt" in MARKDOWN[slug]


@pytest.mark.parametrize("slug", PAGES)
def test_the_markdown_version_is_plain(slug: str) -> None:
    for text in ("<div", "markdown>", '=== "', "{#", "<code"):
        assert text not in MARKDOWN[slug]
    assert "](../" not in MARKDOWN[slug]
    assert "pyquickbase._" not in MARKDOWN[slug]


@pytest.mark.parametrize("slug", PAGES)
def test_each_page_has_a_description_for_search_engines(slug: str) -> None:
    assert re.search(r'^description: ".+\."$', PAGES[slug], re.M)


def test_links_go_to_where_each_name_is() -> None:
    assert "[`WriteResult`](writes.md#WriteResult)" in PAGES["writes"]
    assert "(https://example.com/docs/reference/writes/#WriteResult)" in MARKDOWN["writes"]
    assert "[`Table`](tables.md#Table)" in PAGES["fields"]
    assert "[`App.table()`](tables.md#App-table)" in PAGES["tables"]
    # A table method's docstring naming another table method on a different page.
    assert "[`rows_from_file()`](files.md#Table-rows_from_file)" in PAGES["writes"]
    assert "(https://example.com/docs/reference/files/#Table-rows_from_file)" in MARKDOWN["writes"]


def test_a_methods_docstring_links_its_own_class_methods_on_other_pages() -> None:
    linked = build_reference._add_links(
        "A `count` of 0, unlike `count()`.", build_reference.scope_of("Table")
    )
    assert linked == "A `count` of 0, unlike [`count()`](records.md#Table-count)."


def test_a_shared_method_name_is_shown_with_its_class() -> None:
    labels = build_reference.method_labels(PAGES_BY_SLUG["links"], REFERENCE)
    assert labels["Table.url"] == "Table.url()"
    assert labels["App.url"] == "App.url()"
    assert labels["Table.record_url"] == "record_url()"
    labels = build_reference.method_labels(PAGES_BY_SLUG["writes"], REFERENCE)
    assert labels["Table.upsert"] == "upsert()"


def test_method_names_reach_search_titles() -> None:
    """Search indexes a heading's text, but not the signature beside it."""
    assert '### Create or update records <code class="ref-call">upsert()</code>' in PAGES["writes"]


def test_from_env_describes_the_options_it_shares_with_the_constructor() -> None:
    parameters = {item.name: item for item in METHODS["Quickbase"]["from_env"].parameters}
    assert parameters["timeout"].description.startswith("Seconds to allow for each step")
    assert parameters["token_env"].description.startswith("The name of the variable")


def test_the_async_only_name_is_mentioned() -> None:
    assert "The async client calls this `aclose()`." in PAGES["client"]
    assert ".aclose(" in METHODS["Quickbase"]["close"].async_code
    assert ".close(" in METHODS["Quickbase"]["close"].sync_code


@pytest.mark.parametrize(
    ("owner", "name", "awaited"),
    [
        ("Table", "query", False),
        ("Table", "upsert", True),
        ("App", "table", False),
        ("App", "code_page", False),
        ("Table", "url", True),
        ("Report", "run", False),
    ],
)
def test_a_method_the_async_client_does_not_await_says_so(
    owner: str, name: str, awaited: bool
) -> None:
    method = METHODS[owner][name]
    assert method.awaited is awaited
    note = "The async client calls this without `await`."
    assert (note in build_reference._describe(method, {})) is not awaited


def test_a_property_shows_its_type_without_a_returns_section() -> None:
    method = METHODS["App"]["id"]
    assert method.signature == "id: str"
    lines = build_reference._describe(method, {})
    assert not any("**Returns**" in line for line in lines)
    assert "The async client calls this without `await`." not in lines


def test_an_example_the_live_tests_skip_says_why() -> None:
    lines = build_reference._describe(METHODS["App"]["drop"], {})
    assert any(line.startswith("The live tests don't run this example.") for line in lines)


def test_a_page_sets_up_only_what_its_examples_use() -> None:
    setups = {
        slug: build_reference.page_setup(page, REFERENCE) for slug, page in PAGES_BY_SLUG.items()
    }
    assert "tasks = " not in setups["client"].async_code
    assert "projects = " in setups["fields"].async_code
    assert "search = await" in setups["operations"].async_code
    assert "plan = await" in setups["operations"].async_code
    assert "page = app.code_page" in setups["code-pages"].async_code
    assert setups["policies"].async_code == ""
    assert "Each example makes its own client" in setups["policies"].note
    assert "An example that makes its own client" in setups["client"].note


def test_a_name_in_a_string_is_not_a_use_of_the_setup() -> None:
    assert build_reference._uses('x = "tasks"\nprint(x)') == set()
    assert build_reference._uses("tasks = 1\nprint(tasks)") == set()
    assert build_reference._uses("print(tasks.count())") == {"tasks"}


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
        build_reference.read_methods(CLASSES["App"])


def test_a_method_without_a_description_stops_the_build() -> None:
    with pytest.raises(SystemExit, match="has no description"):
        build_reference.parse_docstring(None, "Table.insert")


def test_clients_that_differ_stop_the_build() -> None:
    source = TABLE._replace(async_class=pyquickbase.AsyncApp)
    with pytest.raises(SystemExit, match="no longer have"):
        build_reference.read_methods(source)


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


def test_llms_index_points_at_each_page() -> None:
    index = build_llms.build_index(build_llms.read_config())
    for page in build_reference.PAGES:
        assert f"reference/{page.slug}.txt): " in index


def test_build_llms_lists_every_page() -> None:
    listed = {slug for _, slug, _ in build_llms.reference_pages()}
    assert listed == {item.slug for item in build_reference.PAGES}


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


@pytest.mark.parametrize("slug", sorted(PAGES))
def test_python_on_a_page_fits_its_column(slug: str) -> None:
    """Code wider than its column hides past the edge of its box, so nothing may be."""
    page = PAGES[slug]
    assert _long_lines(page, build_reference.LINE_LIMIT) == []
    for description in _DESCRIPTION.findall(page):
        assert _long_lines(description, build_reference.TEXT_LINE_LIMIT) == []


def _code_shown() -> list[str]:
    shown = [method.signature for methods in METHODS.values() for method in methods.values()]
    for symbol in SYMBOLS.values():
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


def test_signatures_and_definitions_are_python() -> None:
    for code in _code_shown():
        ast.parse(_as_python(code))


@pytest.mark.parametrize("source", build_reference.CLASSES, ids=lambda item: item.name)
def test_how_to_get_a_class_is_python(source: Any) -> None:
    compile(source.get, source.name, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    assert max(len(line) for line in source.get.splitlines()) <= build_reference.LINE_LIMIT


def test_a_long_alias_is_shown_by_name() -> None:
    methods = {method.heading: method for method in SYMBOLS["BoundPolicy"].methods}
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
    site = re.search(r"^site_url: (\S+)$", (ROOT / "mkdocs.yml").read_text(), re.M)
    assert site is not None
    assert f"{site[1]}reference/errors/" == pyquickbase.errors.DOCS_URL
    assert PAGE_OF[error.__name__] == "errors"
    assert f"{{#{error.__name__}}}" in PAGES["errors"]


@pytest.mark.parametrize("error", ERROR_CLASSES, ids=lambda item: item.__name__)
def test_every_error_is_in_the_overview_and_the_tree(error: type) -> None:
    overview = "\n".join(build_reference.error_overview(PAGES_BY_SLUG["errors"]))
    name = error.__name__
    assert f"| [`{name}`](#{name}) |" in overview
    assert re.search(rf"^(├─ |└─ |│  |   )*{name}\b", overview, re.M)


def test_the_error_overview_says_what_tool_error_reports() -> None:
    overview = "\n".join(build_reference.error_overview(PAGES_BY_SLUG["errors"]))
    assert "| [`RateLimitError`](#RateLimitError) | `rate_limited` | Yes |" in overview
    assert "| [`UnknownFieldError`](#UnknownFieldError) | `unknown_field` | No |" in overview
    assert "WritePermissionDeniedError  (also PermissionDeniedError)" in overview
    assert "## Every error" in MARKDOWN["errors"]


def test_a_trial_page_shows_what_each_returned_object_holds() -> None:
    page = PAGES["reports"]
    assert "<summary>What a <code>RecordIterator</code> can do</summary>" in page
    assert "<summary>What a <code>ReportInfo</code> holds</summary>" in page
    assert (
        "- [`to_pandas()`](records.md#RecordIterator-to_pandas): Fetch records into pandas" in page
    )
    assert "ref-returned" not in PAGES["writes"]
    assert "ref-returned" not in MARKDOWN["reports"]


def test_a_returned_list_or_namespace_is_shown_by_what_it_holds() -> None:
    lines = build_reference.returned_lines(METHODS["Table"]["summarize"], REFERENCE)
    assert "<summary>What a <code>SummaryRow</code> holds, and what it can do</summary>" in lines
    assert build_reference.returned_lines(METHODS["Table"]["count"], REFERENCE) == []
