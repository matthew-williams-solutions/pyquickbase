# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""A policy's reads against a real app, on a temporary table.

These check what Quickbase returns once a policy trims it, which mocks can't: the shape of user
and file values, the columns of a report, and which metadata keys are left.
"""

import json
from typing import Any

import pytest

from pyquickbase import (
    F,
    FileUpload,
    Policy,
    PolicyRefusedError,
    QueryError,
    Quickbase,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    allow,
    tool_result,
)
from pyquickbase._bound import BoundPolicy

RECORD_OWNER = 4
# The first report of a new table, List All.
LIST_ALL = "1"


@pytest.fixture(scope="module")
def table(scratch: Any) -> Any:
    made = scratch.table(
        ("Title", "text"),
        ("Status", "text"),
        ("Notes", "text"),
        ("Attachment", "file"),
        ("Echo", "text", {"formula": "[Notes]"}),
    )
    made.insert(
        [
            {"Title": "One", "Status": "Open", "Notes": "private one"},
            {"Title": "Two", "Status": "Done", "Notes": "private two"},
        ]
    )
    made.update([{"Record ID#": 1, "Attachment": FileUpload("a.txt", b"hello")}])
    return made


@pytest.fixture(scope="module")
def ids(table: Any) -> dict[str, int]:
    labels = ["Title", "Status", "Notes", "Attachment", "Echo"]
    return dict(zip(labels, table.resolve_field_ids(labels), strict=True))


def policy(
    scratch: Any,
    table: Any,
    ids: dict[str, int],
    *extra: Any,
    files: Any = (),
    reports: Any = None,
    **access: Any,
) -> BoundPolicy:
    """What `ReadAccess(fields=[Title, Status, Attachment, Record Owner], ...)` allowed."""
    table_id = table.info().id
    fields = [ids["Title"], ids["Status"], ids["Attachment"], RECORD_OWNER]
    rules: list[Any] = [allow.read(table_id, fields, **access), *extra]
    if files:
        rules.append(allow.download(table_id, list(files)))
    if reports:
        rules.append(allow.report(table_id, list(reports)))
    return Policy(rules=rules).bind(scratch.client.app(scratch.app_id))


def limited(scratch: Any, rules: BoundPolicy) -> Quickbase:
    return Quickbase(realm=scratch.realm, token=scratch.token, policy=rules)


def test_hidden_fields_and_tables_look_as_if_they_dont_exist(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    with limited(scratch, policy(scratch, table, ids)) as qb:
        app = qb.app(scratch.app_id)
        assert [item.id for item in app.tables()] == [table.info().id]
        tasks = app.table(table.info().id)
        labels = {field.label for field in tasks.fields()}
        assert labels == {"Record ID#", "Record Owner", "Title", "Status", "Attachment"}
        assert all("formula" not in json.dumps(dict(field.raw)) for field in tasks.fields())
        with pytest.raises(UnknownFieldError):
            tasks.first(select=["Notes"])
        with pytest.raises(QueryError):
            tasks.count(f"{{{ids['Notes']}.EX.'private one'}}")
        other = next(item for item in table_names(scratch) if item != table.info().name)
        with pytest.raises(UnknownTableError):
            app.table(other).fields()


def table_names(scratch: Any) -> list[str]:
    return [item.name for item in scratch.client.app(scratch.app_id).tables(refresh=True)]


def test_values_leave_out_emails_and_uploaders(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    with limited(scratch, policy(scratch, table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        record = tasks.get(1, select=["Title", "Record Owner", "Attachment"])
    assert record is not None
    owner = record["Record Owner"]
    assert owner.id
    assert owner.email is None
    attachment = record["Attachment"]
    assert attachment.versions
    assert all(item.creator is None for item in attachment.versions)
    assert "@" not in repr(dict(record))
    assert "@" not in json.dumps(tool_result([record]))


def test_user_emails_are_kept_when_asked(scratch: Any, table: Any, ids: dict[str, int]) -> None:
    with limited(scratch, policy(scratch, table, ids, user_emails=True)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        record = tasks.get(1, select=["Record Owner"])
    assert record is not None
    assert "@" in (record["Record Owner"].email or "")


def test_default_queries_return_only_allowed_fields(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    with limited(scratch, policy(scratch, table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        records = [record for record in tasks.query(max_total=10)]
    assert len(records) == 2
    allowed = {"Record ID#", "Title", "Status", "Attachment", "Record Owner"}
    assert all(set(record) <= allowed for record in records)


def test_filters_sorts_and_windows_are_held_to_the_rules(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    rules = policy(scratch, table, ids, filter=[ids["Status"]], sort=[ids["Status"]], max_records=1)
    with limited(scratch, rules) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        assert tasks.count(F("Status") == "Open") == 1
        with pytest.raises(PolicyRefusedError, match="filtering"):
            tasks.count(F("Title") == "One")
        with pytest.raises(PolicyRefusedError, match="sorting"):
            tasks.first(order_by="Title")
        with pytest.raises(PolicyRefusedError, match="at most 1"):
            [record for record in tasks.query(max_total=2)]
        page = tasks.query_page(limit=1)
        assert (len(page.records), page.total, page.has_more) == (1, 2, True)


def test_summaries_group_users_without_emails(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    with limited(scratch, policy(scratch, table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        rows = tasks.summarize(group_by="Record Owner")
        assert [row.count for row in rows] == [2]
        assert "@" not in str(rows[0].group["Record Owner"])
    with limited(scratch, policy(scratch, table, ids, max_summary_records=1)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        with pytest.raises(PolicyRefusedError, match="summarizing at most 1"):
            tasks.summarize(group_by="Status")


def test_a_listed_report_loses_hidden_columns(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    with limited(scratch, policy(scratch, table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        # An unlisted report looks as if it doesn't exist.
        with pytest.raises(UnknownReportError):
            tasks.report(LIST_ALL).run_page(limit=5)
    with limited(scratch, policy(scratch, table, ids, reports=[LIST_ALL])) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        assert [item.id for item in tasks.reports()] == [LIST_ALL]
        assert all("query" not in item.raw for item in tasks.reports())
        page = tasks.report(LIST_ALL).run_page(limit=5)
    assert page.records
    hidden = {"Notes", "Echo"}
    assert all(not hidden & set(record) for record in page.records)
    assert "private" not in json.dumps([dict(record) for record in page.records], default=str)


def test_downloads_need_their_own_grant(scratch: Any, table: Any, ids: dict[str, int]) -> None:
    with limited(scratch, policy(scratch, table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        with pytest.raises(PolicyRefusedError, match="downloading"):
            tasks.download_file(1, "Attachment")
    with limited(scratch, policy(scratch, table, ids, files=[ids["Attachment"]])) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        assert (tasks.download_file(1, "Attachment")).data == b"hello"


def test_write_only_fields_can_be_written_but_not_read(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    table_id = table.info().id
    rules = policy(scratch, table, ids, allow.update(table_id, [ids["Notes"]]))
    with limited(scratch, rules) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        result = tasks.update([{"Record ID#": 2, "Notes": "private two"}])
        assert result.unchanged_ids == (2,)
        with pytest.raises(PolicyRefusedError, match="reading Notes"):
            tasks.first(select=["Notes"])
        with pytest.raises(PolicyRefusedError, match="formula"):
            tasks.run_formula("1")
    # Even a policy that allows formulas can't add one that would copy a field it can't read.
    schema = allow.schema(table_id, "add_field", "formula")
    notes = allow.update(table_id, [ids["Notes"]])
    with limited(scratch, policy(scratch, table, ids, notes, schema)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        with pytest.raises(PolicyRefusedError, match="this client can't read"):
            tasks.create_field("Copy", "text", formula="[Notes]")
    assert "Copy" not in {field.label for field in table.fields(refresh=True)}
