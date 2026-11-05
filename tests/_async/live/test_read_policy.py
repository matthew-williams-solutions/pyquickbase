"""Read policies against a real app, on a temporary table.

These check what Quickbase returns once `ReadRules` trims it, which mocks can't: the shape of user
and file values, the columns of a report, and which metadata keys are left.
"""

import json
from typing import Any

import pytest

from pyquickbase import (
    AsyncQuickbase,
    F,
    FileUpload,
    QueryError,
    ReadAccess,
    ReadRefusedError,
    ReadRules,
    UnknownFieldError,
    UnknownTableError,
    WriteAccess,
    WriteRules,
)

pytestmark = pytest.mark.anyio

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


def policy(table: Any, ids: dict[str, int], **access: Any) -> ReadRules:
    fields = [ids["Title"], ids["Status"], ids["Attachment"], RECORD_OWNER]
    return ReadRules({table.info().id: ReadAccess(**{"fields": fields, **access})})


async def limited(scratch: Any, rules: ReadRules, **kwargs: Any) -> AsyncQuickbase:
    return AsyncQuickbase(realm=scratch.realm, token=scratch.token, read_policy=rules, **kwargs)


async def test_hidden_fields_and_tables_look_as_if_they_dont_exist(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    async with await limited(scratch, policy(table, ids)) as qb:
        app = qb.app(scratch.app_id)
        assert [item.id for item in await app.tables()] == [table.info().id]
        tasks = app.table(table.info().id)
        labels = {field.label for field in await tasks.fields()}
        assert labels == {"Record ID#", "Record Owner", "Title", "Status", "Attachment"}
        assert all("formula" not in json.dumps(dict(field.raw)) for field in await tasks.fields())
        with pytest.raises(UnknownFieldError):
            await tasks.first(select=["Notes"])
        with pytest.raises(QueryError):
            await tasks.count(f"{{{ids['Notes']}.EX.'private one'}}")
        other = next(item for item in table_names(scratch) if item != table.info().name)
        with pytest.raises(UnknownTableError):
            await app.table(other).fields()


def table_names(scratch: Any) -> list[str]:
    return [item.name for item in scratch.client.app(scratch.app_id).tables(refresh=True)]


async def test_values_leave_out_emails_and_uploaders(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    async with await limited(scratch, policy(table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        record = await tasks.get(1, select=["Title", "Record Owner", "Attachment"])
    assert record is not None
    owner = record["Record Owner"]
    assert set(owner) == {"id", "name"}
    attachment = record["Attachment"]
    assert attachment["versions"]
    assert all(
        set(item) <= {"versionNumber", "fileName", "uploaded"} for item in attachment["versions"]
    )
    assert "@" not in json.dumps(dict(record), default=str)


async def test_user_emails_are_kept_when_asked(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    async with await limited(scratch, policy(table, ids, user_emails=True)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        record = await tasks.get(1, select=["Record Owner"])
    assert record is not None
    assert "@" in record["Record Owner"]["email"]


async def test_default_queries_return_only_allowed_fields(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    async with await limited(scratch, policy(table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        records = [record async for record in tasks.query(limit=10)]
    assert len(records) == 2
    allowed = {"Record ID#", "Title", "Status", "Attachment", "Record Owner"}
    assert all(set(record) <= allowed for record in records)


async def test_filters_sorts_and_windows_are_held_to_the_rules(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    rules = policy(table, ids, filter=[ids["Status"]], sort=[ids["Status"]], max_records=1)
    async with await limited(scratch, rules) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        assert await tasks.count(F("Status") == "Open") == 1
        with pytest.raises(ReadRefusedError, match="filtering"):
            await tasks.count(F("Title") == "One")
        with pytest.raises(ReadRefusedError, match="sorting"):
            await tasks.first(order_by="Title")
        with pytest.raises(ReadRefusedError, match="at most 1"):
            [record async for record in tasks.query(limit=2)]
        page = await tasks.query_page(top=1)
        assert (len(page.records), page.total, page.has_more) == (1, 2, True)


async def test_summaries_group_users_without_emails(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    async with await limited(scratch, policy(table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        rows = await tasks.summarize(group_by="Record Owner")
        assert [row.count for row in rows] == [2]
        assert "@" not in str(rows[0].group["Record Owner"])
    async with await limited(scratch, policy(table, ids, max_summary_records=1)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        with pytest.raises(ReadRefusedError, match="summarizing at most 1"):
            await tasks.summarize(group_by="Status")


async def test_a_listed_report_loses_hidden_columns(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    async with await limited(scratch, policy(table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        with pytest.raises(ReadRefusedError, match="isn't a listed table report"):
            await tasks.report(LIST_ALL).run_page(top=5)
    async with await limited(scratch, policy(table, ids, reports=[LIST_ALL])) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        assert [item.id for item in await tasks.reports()] == [LIST_ALL]
        assert all("query" not in item.raw for item in await tasks.reports())
        page = await tasks.report(LIST_ALL).run_page(top=5)
    assert page.records
    hidden = {"Notes", "Echo"}
    assert all(not hidden & set(record) for record in page.records)
    assert "private" not in json.dumps([dict(record) for record in page.records], default=str)


async def test_downloads_need_the_field_in_files(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    async with await limited(scratch, policy(table, ids)) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        with pytest.raises(ReadRefusedError, match="downloading"):
            await tasks.download_file(1, "Attachment")
    async with await limited(scratch, policy(table, ids, files=[ids["Attachment"]])) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        assert (await tasks.download_file(1, "Attachment")).data == b"hello"


async def test_write_only_fields_can_be_written_but_not_read(
    scratch: Any, table: Any, ids: dict[str, int]
) -> None:
    writes = WriteRules({table.info().id: WriteAccess(update=[ids["Notes"]])})
    rules = policy(table, ids, write_only=[ids["Notes"]])
    async with await limited(scratch, rules, write_policy=writes) as qb:
        tasks = qb.app(scratch.app_id).table(table.info().id)
        result = await tasks.update([{"Record ID#": 2, "Notes": "private two"}])
        assert result.unchanged_ids == (2,)
        with pytest.raises(ReadRefusedError, match="returning"):
            await tasks.first(select=["Notes"])
        with pytest.raises(ReadRefusedError, match="formula"):
            await tasks.run_formula("1")
        with pytest.raises(ReadRefusedError, match="can't be changed"):
            await tasks.create_field("Copy", "text", formula="[Notes]")
    assert "Copy" not in {field.label for field in table.fields(refresh=True)}
