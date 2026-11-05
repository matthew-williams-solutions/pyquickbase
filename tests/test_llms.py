"""The files for AI tools: they must cover the docs, and hold nothing a reader can't use."""

import importlib
import re
import sys
from pathlib import Path

import pytest

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
build_llms = importlib.import_module("build_llms")

CONFIG = build_llms.read_config()
SITE = CONFIG.site_url
INDEX = build_llms.build_index(CONFIG)
FULL = build_llms.build_full(CONFIG)
COVERED = [page for page in CONFIG.pages if page.path not in build_llms.LEFT_OUT]
LINK = re.compile(r"\]\(([^)\s]+)")


def without_code(text: str) -> str:
    text = re.sub(r"^[> ]*```.*?^[> ]*```", "", text, flags=re.DOTALL | re.MULTILINE)
    return re.sub(r"`[^`\n]*`", "", text)


def links(text: str) -> list[str]:
    return LINK.findall(without_code(text))


def source_text() -> str:
    paths = [ROOT / "CHANGELOG.md", *(ROOT / "docs").rglob("*.md")]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_the_nav_lists_every_page() -> None:
    pages = {path.relative_to(ROOT / "docs").as_posix() for path in (ROOT / "docs").rglob("*.md")}
    assert {page.path for page in CONFIG.pages} == pages
    assert "playground.md" in pages


def test_the_nav_keeps_its_sections() -> None:
    assert {page.path: page.section for page in CONFIG.pages}["guides/agents.md"] == "Guides"
    assert {page.path: page.section for page in CONFIG.pages}["reference.md"] is None


@pytest.mark.parametrize("page", COVERED, ids=[page.path for page in COVERED])
def test_every_page_is_covered(page: "build_llms.Page") -> None:
    url = build_llms.page_url(SITE, page.path)
    assert f"]({url}): " in INDEX
    assert f"## {page.title}\n\nSource: <{url}>\n" in FULL


def test_the_playground_is_left_out() -> None:
    assert f"]({SITE}playground/): " not in INDEX
    assert "## Playground" not in FULL
    assert "<iframe" not in FULL


@pytest.mark.parametrize("text", [":::", "--8<--", "!!!"])
def test_no_unconverted_syntax_remains(text: str) -> None:
    assert text not in FULL
    assert text not in INDEX


def test_no_tabs_remain() -> None:
    assert not re.search(r"^\s*=== ", FULL, re.MULTILINE)


def test_the_index_follows_the_format() -> None:
    title, summary, *_ = INDEX.split("\n\n")
    assert title == "# pyquickbase"
    assert summary == f"> {CONFIG.summary}"
    assert "\n" not in summary
    assert "not affiliated" not in summary
    assert "isn't affiliated with or endorsed by Quickbase, Inc." in INDEX
    assert re.findall(r"^## (.+)$", INDEX, re.MULTILINE) == ["Docs", "Guides"]
    assert f"]({SITE}llms-full.txt): " in INDEX


def test_every_link_in_the_index_is_under_the_site() -> None:
    found = links(INDEX)
    assert len(found) == len(COVERED) + 1 + len(build_llms.REFERENCE_PAGES)
    assert all(link.startswith(SITE) for link in found)


def test_every_link_in_the_full_file_is_absolute() -> None:
    found = links(FULL)
    assert len(found) > 20
    assert all(link.startswith("https://") for link in found)


def test_links_to_other_sites_come_from_the_docs_unchanged() -> None:
    sources = source_text()
    for link in links(FULL):
        if not link.startswith(SITE):
            assert link in sources


def test_links_between_pages_reach_real_pages() -> None:
    pages = {build_llms.page_url(SITE, page.path) for page in CONFIG.pages}
    files = {f"{SITE}llms.txt", f"{SITE}llms-full.txt"}
    files |= {f"{SITE}reference/{page[1]}.txt" for page in build_llms.REFERENCE_PAGES}
    for link in links(FULL):
        if link.startswith(SITE) and "/playground-app/" not in link:
            assert link.partition("#")[0] in pages | files


@pytest.mark.parametrize("name", sorted(set(pyquickbase.__all__) - {"__version__"}))
def test_every_public_name_is_written_out(name: str) -> None:
    assert f"`{name}`" in FULL


def test_the_version_is_stated() -> None:
    assert f"version {pyquickbase.__version__} (`pyquickbase.__version__`)" in FULL
    assert f"version {pyquickbase.__version__}" in INDEX


def test_the_reference_holds_real_signatures_and_docstrings() -> None:
    assert "## API reference: Table" in FULL
    assert "query(\n    *,\n    where: Expression | str | None = None" in FULL
    assert "Stop after this many records." in FULL
    assert "Refuse any request that could change data" in FULL
    assert "`Quickbase.realm`" in FULL
    assert "`WriteResult`" in FULL


def test_private_names_stay_out_of_the_reference() -> None:
    assert not re.search(r"pyquickbase\._[a-z]", FULL)
    assert "`Table._" not in FULL
    assert "Transport" not in FULL
    assert "`Table.__init__`" not in FULL


def test_async_methods_are_not_repeated() -> None:
    assert "`Table.query()`" in FULL
    assert "`AsyncTable.query" not in FULL
    assert "`aclose()`" in FULL


def test_warnings_are_kept() -> None:
    assert "> **Warning: Don't run formula text you don't control**\n>\n> A formula" in FULL
    assert "> **Warning: Messages can contain HTML**" in FULL
    assert "> **Warning: Release candidate**" in FULL
    assert "> **Note: A missing app is a 401**" in FULL


def test_the_changelog_is_resolved() -> None:
    assert f"### [{pyquickbase.__version__}]" in FULL


def test_tabs_keep_their_code() -> None:
    assert "**Async:**\n\n```python\nfrom pyquickbase import AsyncQuickbase, F\n" in FULL


def test_there_is_one_top_level_heading() -> None:
    assert len(re.findall(r"^# ", without_code(FULL), re.MULTILINE)) == 1


def test_convert_blocks_handles_nesting_and_leaves_code_alone() -> None:
    source = (
        '=== "A"\n\n    !!! note\n\n        Hi\n\n    ```text\n    !!! warning\n    ```\n\nEnd\n'
    )
    assert build_llms.convert_blocks(source) == (
        "**A:**\n\n> **Note**\n>\n> Hi\n\n```text\n!!! warning\n```\n\nEnd"
    )


@pytest.mark.parametrize(
    ("link", "page", "expected"),
    [
        ("clients.md#retries", "guides/errors.md", "guides/clients/#retries"),
        ("../stability.md", "guides/errors.md", "stability/"),
        ("guides/errors.md", "index.md", "guides/errors/"),
        ("index.md", "guides/errors.md", "guides/"),
        ("../index.md", "guides/errors.md", ""),
        ("#fields", "guides/formulas.md", "guides/formulas/#fields"),
        ("#fields", "index.md", "#fields"),
        ("../llms.txt", "guides/agents.md", "llms.txt"),
        ("playground-app/index.html#x", "index.md", "playground-app/index.html#x"),
    ],
)
def test_links_become_absolute(link: str, page: str, expected: str) -> None:
    assert build_llms.rewrite_links(f"[x]({link})", page, SITE) == f"[x]({SITE}{expected})"


@pytest.mark.parametrize(
    "line",
    [
        "[x](https://example.com/a.md)",
        "[x](mailto:a@example.com)",
        "`[x](y.md)`",
    ],
)
def test_other_links_and_code_stay_as_they_are(line: str) -> None:
    assert build_llms.rewrite_links(line, "index.md", SITE) == line


def test_a_link_definition_becomes_absolute() -> None:
    assert build_llms.rewrite_links("[x]: y.md", "index.md", SITE) == f"[x]: {SITE}y/"


def test_links_in_a_code_block_stay_as_they_are() -> None:
    source = "```text\n[x](y.md)\n```"
    assert build_llms.rewrite_links(source, "index.md", SITE) == source


def test_a_link_outside_the_docs_stops_the_build() -> None:
    with pytest.raises(SystemExit, match="outside the docs"):
        build_llms.rewrite_links("[x](../../secret.md)", "guides/a.md", SITE)


def test_an_unreadable_nav_stops_the_build() -> None:
    config = "site_url: https://x/\nsite_description: y\nnav:\n  - page.md\n"
    with pytest.raises(SystemExit, match="Can't read"):
        build_llms.read_config(config)


def test_a_page_without_a_description_stops_the_build() -> None:
    page = build_llms.Page("New", "guides/new.md", "Guides")
    config = CONFIG._replace(pages=[*CONFIG.pages, page])
    with pytest.raises(SystemExit, match=r"guides/new\.md"):
        build_llms.build_index(config)
