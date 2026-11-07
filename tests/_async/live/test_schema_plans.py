"""Schema plans against a real app, on temporary tables.

These check what mocks can't: that each change a plan makes is the one Quickbase applies, that
planning again finds them all done, and that the operations' confirmation holds against real
metadata.
"""

import secrets
from typing import Any

import pytest

from pyquickbase import (
    AsyncQuickbase,
    ConfirmationError,
    CreateField,
    CreateLookupField,
    CreateRelationship,
    CreateSummaryField,
    CreateTable,
    DeleteField,
    DeleteRelationship,
    DeleteTable,
    Policy,
    UpdateField,
    UpdateTable,
    allow,
    tool_result,
)

pytestmark = pytest.mark.anyio


async def test_a_plan_links_tables_and_planning_again_finds_it_done(
    qb: AsyncQuickbase, scratch: Any
) -> None:
    parent = scratch.table(("Name", "text"))
    child = scratch.table(("Title", "text"))
    parent_name, child_name = parent.info().name, child.info().name
    child.insert([{"Title": "a"}, {"Title": "b"}])
    changes = [
        CreateField(table=child_name, label="Hours", type="numeric"),
        CreateField(table=child_name, label="Double", type="numeric", formula="[Hours] * 2"),
        CreateRelationship(child=child_name, parent=parent_name, foreign_key="Parent"),
        CreateLookupField(child=child_name, foreign_key="Parent", parent_field="Name"),
        CreateSummaryField(
            child=child_name, foreign_key="Parent", label="Total Hours", child_field="Hours"
        ),
        CreateSummaryField(child=child_name, foreign_key="Parent", label="Count", function="COUNT"),
    ]
    app = qb.app(scratch.app_id)
    plan = await app.plan_schema(changes)
    assert [item.status for item in plan.changes] == ["change"] * 6, plan.blockers
    result = await app.apply_schema(plan)
    assert len(result.applied) == 6

    again = await app.plan_schema(changes)
    assert [item.status for item in again.changes] == ["no_change"] * 6
    labels = {item.label for item in await app.table(child_name).fields(refresh=True)}
    assert {"Hours", "Double", "Parent", "Parent - Name"} <= labels

    unlink = await app.plan_schema([DeleteRelationship(child=child_name, foreign_key="Parent")])
    warnings = " ".join(unlink.changes[0].warnings)
    assert '"Parent - Name"' in warnings
    assert '"Total Hours"' in warnings
    await app.apply_schema(unlink)
    assert await app.table(child_name).relationships(refresh=True) == []
    labels = {item.label for item in await app.table(child_name).fields(refresh=True)}
    assert "Parent" in labels
    assert "Parent - Name" not in labels


async def test_a_plan_counts_what_a_deletion_loses(qb: AsyncQuickbase, scratch: Any) -> None:
    table = scratch.table(("Title", "text"), ("Code", "text"))
    table.insert([{"Title": "a", "Code": "x"}, {"Title": "b", "Code": "x"}, {"Title": "c"}])
    name = table.info().name
    app = qb.app(scratch.app_id)
    plan = await app.plan_schema(
        [
            DeleteField(table=name, field="Code"),
            UpdateField(table=name, field="Title", required=True),
            UpdateField(table=name, field="Title", unique=True),
        ]
    )
    code, required, unique = plan.changes
    assert code.affected_records == 2
    assert required.status == "change"
    assert unique.status == "change"
    duplicate = await app.plan_schema([UpdateField(table=name, field="Code", unique=True)])
    assert "1 values appear on more than one record" in duplicate.blockers[0]


async def test_a_plan_can_create_change_and_drop_a_table(qb: AsyncQuickbase, scratch: Any) -> None:
    name = f"ZZ Live {secrets.token_hex(4)}"
    app = qb.app(scratch.app_id)
    plan = await app.plan_schema(
        [
            CreateTable(name=name, single_record_name="Visit"),
            CreateField(table=name, label="Notes", type="text"),
            UpdateTable(table=name, description="Made by a plan."),
        ]
    )
    assert not plan.blockers
    await app.apply_schema(plan)
    made = scratch.client.app(scratch.app_id).table(name)
    scratch.created.append(made)
    assert made.info().description == "Made by a plan."
    drop = await app.plan_schema([DeleteTable(table=name)])
    assert drop.changes[0].affected_records == 0
    await app.apply_schema(drop)
    scratch.created.remove(made)
    assert name not in {item.name for item in await app.tables(refresh=True)}


async def test_operations_hold_a_model_to_its_limits(scratch: Any) -> None:
    table = scratch.table(("Title", "text"), ("Salary", "numeric"))
    info = table.info()
    title, salary = table.resolve_field_ids(["Title", "Salary"])
    policy = Policy(
        rules=[
            allow.read(info.id, [title], filter=[title, salary]),
            allow.schema(info.id, "add_field", "formula"),
        ]
    )
    bound = policy.bind(scratch.client.app(scratch.app_id))
    async with AsyncQuickbase(realm=scratch.realm, token=scratch.token, policy=bound) as limited:
        app = limited.app(scratch.app_id)
        plan = await app.operation("plan_schema")
        apply = await app.operation("apply_schema")
        hidden = {"kind": "create_field", "table": info.name, "label": "Pay", "type": "numeric",
                  "formula": "[Salary] / 12"}  # fmt: skip
        refused = tool_result(await plan.run({"changes": [hidden]}))
        assert "uses [Salary]" in refused["blockers"][0]
        assert "confirmation" not in refused

        shown = {**hidden, "label": "Shout", "type": "text", "formula": "Upper([Title])"}
        planned = tool_result(await plan.run({"changes": [shown]}))
        with pytest.raises(ConfirmationError, match="these arguments differ"):
            await apply.run({"changes": [hidden], "confirmation": planned["confirmation"]})
        result = await apply.run({"changes": [shown], "confirmation": planned["confirmation"]})
        assert len(result.applied) == 1
    labels = {item.label for item in table.fields(refresh=True)}
    assert "Shout" in labels
    assert "Pay" not in labels
