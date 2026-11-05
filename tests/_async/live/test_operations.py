"""Operations against a real app, on a temporary table.

These check what mocks can't: that a scope holds against real records, that a file uploaded from a
model's text reads back, and that a formula and a report run through an operation.
"""

from typing import Any

import pytest

from pyquickbase import AsyncQuickbase, F, QueryError, tool_result

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def table(scratch: Any) -> Any:
    made = scratch.table(
        ("Title", "text"),
        ("Project", "text"),
        ("Hours", "numeric"),
        ("Due Date", "date"),
        ("Attachment", "file"),
    )
    made.insert(
        [
            {"Title": "Pour slab", "Project": "Alpha", "Hours": 8},
            {"Title": "Frame walls", "Project": "Alpha", "Hours": 12},
            {"Title": "Paint", "Project": "Beta", "Hours": 4},
        ]
    )
    return made


async def test_a_query_stays_inside_its_scope(qb: AsyncQuickbase, scratch: Any, table: Any) -> None:
    tasks = qb.app(scratch.app_id).table(table.info().id)
    search = await tasks.operation("query", fields=["Title", "Hours"])
    page = await search.run(
        {"where": {"field": "Hours", "op": ">", "value": 0}, "select": ["Title"]},
        scope=F("Project") == "Alpha",
    )
    assert sorted(record["Title"] for record in page.records) == ["Frame walls", "Pour slab"]
    assert tool_result(page)["page"]["total"] == 2


async def test_an_update_outside_its_scope_changes_nothing(
    qb: AsyncQuickbase, scratch: Any, table: Any
) -> None:
    tasks = qb.app(scratch.app_id).table(table.info().id)
    update = await tasks.operation("update", fields=["Hours"])
    with pytest.raises(QueryError, match="aren't ones this tool may use"):
        await update.run(
            {"records": [{"Record ID#": 3, "Hours": 99}]}, scope=F("Project") == "Alpha"
        )
    assert (await tasks.get(3, select=["Hours"]) or {})["Hours"] == 4
    result = await update.run(
        {"records": [{"Record ID#": 1, "Hours": 9}]}, scope=F("Project") == "Alpha"
    )
    assert result.updated_ids == (1,)


async def test_values_from_json_are_written_by_type(
    qb: AsyncQuickbase, scratch: Any, table: Any
) -> None:
    tasks = qb.app(scratch.app_id).table(table.info().id)
    insert = await tasks.operation("insert")
    result = await insert.run(
        {
            "records": [
                {
                    "Title": "Hang doors",
                    "Project": "Beta",
                    "Due Date": "2027-03-01",
                    "Attachment": {"file_name": "notes.txt", "text": "Two doors, left hinge."},
                }
            ]
        }
    )
    (created,) = result.created_ids
    download = await tasks.operation("download_file")
    found = await download.run({"record_id": created, "field": "Attachment"})
    assert tool_result(found)["file"]["text"] == "Two doors, left hinge."
    record = await tasks.get(created, select=["Due Date"])
    assert record is not None
    assert str(record["Due Date"]) == "2027-03-01"


async def test_a_formula_and_a_report(qb: AsyncQuickbase, scratch: Any, table: Any) -> None:
    tasks = qb.app(scratch.app_id).table(table.info().id)
    formula = await tasks.operation("run_formula", formula="[Hours] * 2")
    assert (await formula.run({"record_id": 2})).value == 24
    report = await tasks.operation("run_report", reports=["1"])
    page = await report.run({"report": "1", "limit": 2})
    assert len(page.records) == 2
    with pytest.raises(QueryError, match="scope"):
        await report.run({"report": "1"}, scope=F("Project") == "Alpha")
