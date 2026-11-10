"""Apps, field usage, events, and DataFrames against a real app, on temporary tables and apps.

The app test makes its own apps, named `ZZ Live App ...`, and deletes exactly those. Find one left
by a run that was killed with `client.apps()`, and delete it in the interface.
"""

import os
import secrets
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import polars as pl
import pytest

from pyquickbase import (
    AsyncQuickbase,
    DeleteField,
    F,
    PermissionDeniedError,
    QuickbaseError,
)

pytestmark = pytest.mark.anyio

FIELDS = (("Title", "text"), ("Due", "date"), ("Started", "timestamp"), ("Hours", "numeric"))


async def test_field_usage_covers_every_field_in_one_request(
    qb: AsyncQuickbase, scratch: Any
) -> None:
    table = scratch.table(*FIELDS)
    tasks = qb.app(scratch.app_id).table(table.info().id)
    every = await tasks.field_usage()
    assert [item.field_id for item in every] == [item.id for item in await tasks.fields()]
    title = next(item for item in every if item.label == "Title")
    # Quickbase puts a new field in the reports it makes for every table.
    assert title.counts.get("reports", 0) >= 1
    assert "report" in title.describe()
    # Counts settle a few seconds after a field is made, so compare only which field it is.
    [one] = await tasks.field_usage("Title")
    assert (one.field_id, one.label) == (title.field_id, title.label)


async def test_a_plan_says_where_a_deleted_field_is_used(qb: AsyncQuickbase, scratch: Any) -> None:
    table = scratch.table(*FIELDS)
    plan = await qb.app(scratch.app_id).preview_schema_changes(
        [DeleteField(table=table.info().id, field="Hours")]
    )
    assert any(item.startswith("Quickbase lists it in") for item in plan.changes[0].warnings)


async def test_events_and_previews(qb: AsyncQuickbase, scratch: Any) -> None:
    table = scratch.table(*FIELDS)
    scratch.fill(table, [{"Title": "a"}])
    app = qb.app(scratch.app_id)
    events = await app.triggers()
    assert all(item.type for item in events)
    tasks = app.table(table.info().id)
    # A new table has nothing watching it.
    assert await tasks.triggers() == []
    assert (await tasks.preview_delete(F("Title") == "a")).triggers == ()
    assert (await tasks.preview_update([{"Record ID#": 1, "Title": "b"}])).triggers == ()


async def test_a_restricted_user_previews_without_events(scratch: Any) -> None:
    token, table_id = os.environ.get("QB_RESTRICTED_TOKEN"), os.environ.get("QB_VIEWABLE_TABLE_ID")
    if not token or not table_id:
        pytest.skip("Set QB_RESTRICTED_TOKEN and QB_VIEWABLE_TABLE_ID to run this.")
    async with AsyncQuickbase(realm=scratch.realm, token=token) as qb:
        app = qb.app(scratch.app_id)
        table = app.table(table_id)
        with pytest.raises(PermissionDeniedError):
            await app.triggers()
        with pytest.raises(PermissionDeniedError):
            await table.field_usage()
        preview = await table.preview_delete(F("Category") == "Alpha")
    assert preview.triggers is None
    assert preview.total >= 1


async def test_a_dataframe_goes_in_and_comes_back(qb: AsyncQuickbase, scratch: Any) -> None:
    table = scratch.table(*FIELDS)
    tasks = qb.app(scratch.app_id).table(table.info().id)
    started = datetime(2027, 1, 15, 9, 30, tzinfo=UTC)
    frame = pd.DataFrame(
        {
            "Title": ["Pour slab", "Frame walls"],
            "Due": [date(2027, 1, 15), None],
            "Started": pd.to_datetime([started, None], utc=True),
            "Hours": [8.0, float("nan")],
        }
    )
    result = await tasks.insert(frame)
    assert len(result.created_ids) == 2
    select = ["Title", "Due", "Started", "Hours"]
    back = await tasks.query(select=select, order_by="Title").to_pandas()
    assert back["Title"].tolist() == ["Frame walls", "Pour slab"]
    assert back.loc[1, "Due"] == date(2027, 1, 15)
    assert back.loc[1, "Started"] == started
    assert back.loc[1, "Hours"] == 8
    assert pd.isna(back.loc[0, "Hours"])
    polars = await tasks.query(select=select, order_by="Title").to_polars()
    assert polars.schema["Due"] == pl.Date
    assert polars.row(1, named=True)["Started"] == started
    updates = pd.DataFrame({"Record ID#": result.created_ids, "Hours": [1, 2]})
    preview = await tasks.preview_update(updates)
    assert len(preview.changes) == 2
    await tasks.update(updates)
    assert (await tasks.query(select=["Hours"]).to_pandas())["Hours"].tolist() == [1, 2]


async def test_apps_can_be_made_copied_and_deleted(qb: AsyncQuickbase) -> None:
    name = f"ZZ Live App {secrets.token_hex(4)}"
    try:
        made = await qb.create_app(name, description="A temporary app from the live tests")
    except PermissionDeniedError:
        pytest.skip("This token's user can't create apps in this realm.")
    made_apps = [made]
    try:
        assert (await made.info()).name == name
        things = await made.create_table("Things")
        await things.create_field("Name", "text")
        await things.insert([{"Name": "kept"}])
        copy = await made.copy(f"{name} copy", keep_data=True)
        made_apps.append(copy)
        assert copy.id != made.id
        copied = copy.table("Things")
        assert [record["Name"] async for record in copied.query(select=["Name"])] == ["kept"]
        empty = await made.copy(f"{name} empty")
        made_apps.append(empty)
        assert await empty.table("Things").count() == 0
    finally:
        failed: list[str] = []
        for app in reversed(made_apps):
            try:
                info = await app.info()
                assert info.name.startswith("ZZ Live App "), info.name
                await app.drop(confirm=info.name)
            except QuickbaseError as error:
                failed.append(f"{app.id}: {type(error).__name__}")
        assert not failed, f"Delete these apps in the Quickbase interface: {failed}"
