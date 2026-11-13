"""Names in the hand-written docs link to the API reference, once per section."""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
link_names = importlib.import_module("link_names")


@pytest.mark.parametrize("page", link_names.pages())
def test_every_page_is_linked(page: str) -> None:
    """Run scripts/link_names.py if this fails."""
    text = (link_names.DOCS / page).read_text(encoding="utf-8")
    assert link_names.link_page(text, page) == text


@pytest.mark.parametrize(
    ("code", "target"),
    [
        ("WriteResult", "writes.md#WriteResult"),
        ("upsert()", "writes.md#Table-upsert"),
        ("insert()", "writes.md#Table-insert"),
        ("tasks.upsert()", "writes.md#Table-upsert"),
        ("Table.upsert()", "writes.md#Table-upsert"),
        ("AsyncTable.upsert()", "writes.md#Table-upsert"),
        ("qb.policy", "client.md#Quickbase-policy"),
        ("allow.read()", "rules.md#allow-read"),
        ("bound.allows()", "policies.md#BoundPolicy-allows"),
        ("limit.rows()", "rules.md#limit-rows"),
    ],
)
def test_what_a_mention_links_to(code: str, target: str) -> None:
    assert link_names.target_of(code) == target


@pytest.mark.parametrize(
    "code",
    [
        "run()",  # Report, TableOperation, and AppOperation all have one.
        "info()",
        "url()",
        "limit",  # Also an argument, as `limit=`.
        "fields",
        "tables",
        "upsert",  # A word, unless it's called.
        "page.records",
        "tasks.upsert(rows)",
        "Status",
    ],
)
def test_what_isnt_linked(code: str) -> None:
    assert link_names.target_of(code) is None


def test_only_the_first_mention_in_each_section_is_linked() -> None:
    text = "Use `upsert()`, then `upsert()` again.\n\n## Next\n\nAnd `tasks.upsert()`.\n"
    linked = link_names.link_page(text, "guides/writing.md")
    assert linked == (
        "Use [`upsert()`](../reference/writes.md#Table-upsert), then `upsert()` again.\n\n"
        "## Next\n\nAnd [`tasks.upsert()`](../reference/writes.md#Table-upsert).\n"
    )


def test_a_link_already_there_counts_as_the_first_mention() -> None:
    text = "See [the method](../reference/writes.md#Table-upsert), and `upsert()`.\n"
    assert link_names.link_page(text, "guides/writing.md") == text


def test_code_headings_links_and_front_matter_are_left_alone() -> None:
    text = (
        '---\ndescription: "Uses `WriteResult`."\n---\n\n# `WriteResult`\n\n'
        "```python\nresult: WriteResult\n```\n\n"
        "[`WriteResult` and more](https://example.com)\n"
    )
    assert link_names.link_page(text, "guides/writing.md") == text


def test_a_page_at_the_top_links_down_into_the_reference() -> None:
    linked = link_names.link_page("Raises `WriteError`.\n", "index.md")
    assert linked == "Raises [`WriteError`](reference/errors.md#WriteError).\n"


def test_linking_twice_changes_nothing() -> None:
    text = "Use `upsert()` and `WriteResult`.\n"
    once = link_names.link_page(text, "guides/writing.md")
    assert link_names.link_page(once, "guides/writing.md") == once
