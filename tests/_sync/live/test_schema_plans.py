# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Schema plans against a real app, on temporary tables.

These check what mocks can't: that each change a plan makes is the one Quickbase applies, that
planning again finds them all done, and that the operations' confirmation holds against real
metadata.
"""

import secrets
from typing import Any

import pytest

from pyquickbase import (
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
    Quickbase,
    UpdateField,
    UpdateTable,
    allow,
    tool_result,
)


def test_a_plan_links_tables_and_planning_again_finds_it_done(qb: Quickbase, scratch: Any) -> None:
    parent = scratch.table(("Name", "text"))
    child = scratch.table(("Title", "text"))
    parent_name, child_name = parent.info().name, child.info().name
    child.insert([{"Title": "a"}, {"Title": "b"}])
    changes = [
        CreateField(table=child_name, label="Hours", type="numeric"),
        CreateField(table=child_name, label="Double", type="numeric", formula="[Hours] * 2"),
        CreateRelationship(child_table=child_name, parent_table=parent_name, foreign_key="Parent"),
        CreateLookupField(child_table=child_name, foreign_key="Parent", parent_field="Name"),
        CreateSummaryField(
            child_table=child_name, foreign_key="Parent", label="Total Hours", child_field="Hours"
        ),
        CreateSummaryField(
            child_table=child_name, foreign_key="Parent", label="Count", function="count"
        ),
    ]
    app = qb.app(scratch.app_id)
    plan = app.preview_schema_changes(changes)
    assert [item.status for item in plan.changes] == ["ready"] * 6, plan.blockers
    result = app.apply_schema_changes(plan)
    assert len(result.applied) == 6

    again = app.preview_schema_changes(changes)
    assert [item.status for item in again.changes] == ["already_done"] * 6
    labels = {item.label for item in app.table(child_name).fields(refresh=True)}
    assert {"Hours", "Double", "Parent", "Parent - Name"} <= labels

    unlink = app.preview_schema_changes(
        [DeleteRelationship(child_table=child_name, foreign_key="Parent")]
    )
    warnings = " ".join(unlink.changes[0].warnings)
    assert '"Parent - Name"' in warnings
    assert '"Total Hours"' in warnings
    app.apply_schema_changes(unlink)
    assert app.table(child_name).relationships(refresh=True) == []
    labels = {item.label for item in app.table(child_name).fields(refresh=True)}
    assert "Parent" in labels
    assert "Parent - Name" not in labels


def test_a_plan_counts_what_a_deletion_loses(qb: Quickbase, scratch: Any) -> None:
    table = scratch.table(("Title", "text"), ("Code", "text"))
    table.insert([{"Title": "a", "Code": "x"}, {"Title": "b", "Code": "x"}, {"Title": "c"}])
    name = table.info().name
    app = qb.app(scratch.app_id)
    plan = app.preview_schema_changes(
        [
            DeleteField(table=name, field="Code"),
            UpdateField(table=name, field="Title", required=True),
            UpdateField(table=name, field="Title", unique=True),
        ]
    )
    code, required, unique = plan.changes
    assert code.affected_records == 2
    assert required.status == "ready"
    assert unique.status == "ready"
    duplicate = app.preview_schema_changes([UpdateField(table=name, field="Code", unique=True)])
    assert "1 values appear on more than one record" in duplicate.blockers[0]


def test_a_plan_can_create_change_and_drop_a_table(qb: Quickbase, scratch: Any) -> None:
    name = f"ZZ Live {secrets.token_hex(4)}"
    app = qb.app(scratch.app_id)
    plan = app.preview_schema_changes(
        [
            CreateTable(name=name, single_record_name="Visit"),
            CreateField(table=name, label="Notes", type="text"),
            UpdateTable(table=name, description="Made by a plan."),
        ]
    )
    assert not plan.blockers
    app.apply_schema_changes(plan)
    made = scratch.client.app(scratch.app_id).table(name)
    scratch.created.append(made)
    assert made.info().description == "Made by a plan."
    drop = app.preview_schema_changes([DeleteTable(table=name)])
    assert drop.changes[0].affected_records == 0
    app.apply_schema_changes(drop)
    scratch.created.remove(made)
    assert name not in {item.name for item in app.tables(refresh=True)}


def test_operations_hold_a_model_to_its_limits(scratch: Any) -> None:
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
    with Quickbase(realm=scratch.realm, token=scratch.token, policy=bound) as limited:
        app = limited.app(scratch.app_id)
        plan = app.operation("preview_schema_changes")
        apply = app.operation("apply_schema_changes")
        hidden = {"action": "create_field", "table": info.name, "label": "Pay", "type": "numeric",
                  "formula": "[Salary] / 12"}  # fmt: skip
        refused = tool_result(plan.run({"changes": [hidden]}))
        assert "uses [Salary]" in refused["blockers"][0]
        assert "confirmation" not in refused

        shown = {**hidden, "label": "Shout", "type": "text", "formula": "Upper([Title])"}
        planned = tool_result(plan.run({"changes": [shown]}))
        with pytest.raises(ConfirmationError, match="these arguments differ"):
            apply.run({"changes": [hidden], "confirmation": planned["confirmation"]})
        result = apply.run({"changes": [shown], "confirmation": planned["confirmation"]})
        assert len(result.applied) == 1
    labels = {item.label for item in table.fields(refresh=True)}
    assert "Shout" in labels
    assert "Pay" not in labels
