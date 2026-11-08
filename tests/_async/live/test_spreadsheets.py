"""CSV and Excel files against a real app, on temporary tables."""

import io
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from pyquickbase import AsyncQuickbase, InvalidValueError

pytestmark = pytest.mark.anyio

FIELDS = (
    ("Title", "text"),
    ("Hours", "numeric"),
    ("Share", "percent"),
    ("Due", "date"),
    ("Started", "timestamp"),
    ("Done", "checkbox"),
    ("Who", "user"),
    ("Tags", "multitext", {"choices": ["red", "blue", "green"]}),
)
SELECT = ["Title", "Hours", "Share", "Due", "Started", "Done", "Tags"]


async def owner_email(tasks: Any) -> str:
    """The token owner's email, from a record they own."""
    record_id = (await tasks.insert([{"Title": "owner"}])).created_ids[0]
    record = await tasks.get(record_id, select=["Record Owner"])
    await tasks.delete(f"{{3.EX.'{record_id}'}}")
    return record["Record Owner"].email


async def test_a_csv_with_text_values_is_written_exactly(qb: AsyncQuickbase, scratch: Any) -> None:
    table = scratch.table(*FIELDS)
    tasks = qb.app(scratch.app_id).table(table.info().id)
    email = await owner_email(tasks)
    text = io.StringIO(
        "Title,Hours,Share,Due,Started,Done,Who,Tags\n"
        f'Pour slab,"1,234.5",45%,2027-01-15,2027-01-15T09:30:00Z,yes,{email.upper()},red; blue\n'
        "Frame walls,,0.5,,,no,,\n"
    )
    rows = await tasks.rows_from_file(text)
    assert rows.ok, rows.errors
    result = await tasks.insert(rows)
    first = await tasks.get(result.created_ids[0], select=[*SELECT, "Who"])
    assert first is not None
    assert first["Hours"] == 1234.5
    assert first["Share"] == 0.45
    assert first["Due"] == date(2027, 1, 15)
    assert first["Started"] == datetime(2027, 1, 15, 9, 30, tzinfo=UTC)
    assert first["Done"] is True
    assert first["Who"].email.casefold() == email.casefold()
    assert sorted(first["Tags"]) == ["blue", "red"]
    second = await tasks.get(result.created_ids[1], select=["Hours", "Share", "Done"])
    assert second is not None
    assert (second["Hours"], second["Share"], second["Done"]) == (None, 0.5, False)


@pytest.mark.parametrize("suffix", ["csv", "xlsx"])
async def test_an_export_reads_back_without_a_change(
    qb: AsyncQuickbase, scratch: Any, tmp_path: Path, suffix: str
) -> None:
    table = scratch.table(*FIELDS)
    scratch.fill(
        table,
        [
            {
                "Title": "=HYPERLINK(1)",
                "Hours": 8.25,
                "Share": 0.125,
                "Due": date(2027, 1, 15),
                "Started": datetime(2027, 1, 15, 9, 30, tzinfo=UTC),
                "Done": True,
                "Tags": ["red", "green"],
            },
            {"Title": "Frame walls"},
        ],
    )
    tasks = qb.app(scratch.app_id).table(table.info().id)
    path = tmp_path / f"tasks.{suffix}"
    records = tasks.query(select=SELECT)
    written = await (records.to_csv(path) if suffix == "csv" else records.to_excel(path))
    assert written == 2
    rows = await tasks.rows_from_file(path)
    assert rows.ok, rows.errors
    preview = await tasks.preview_update(rows)
    assert preview.changes == ()
    assert len(preview.unchanged_ids) == 2


async def test_a_value_quickbase_would_lose_is_refused_before_sending(
    qb: AsyncQuickbase, scratch: Any
) -> None:
    table = scratch.table(*FIELDS)
    tasks = qb.app(scratch.app_id).table(table.info().id)
    with pytest.raises(InvalidValueError, match="leave the field empty"):
        await tasks.insert([{"Title": "x", "Hours": "1,234.5"}])
    with pytest.raises(InvalidValueError, match="without an id"):
        await tasks.insert([{"Title": "x", "Who": {"email": "someone@example.com"}}])
    assert await tasks.count() == 0
