# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""The four cases the Policy design asked to confirm against a real app, on temporary tables.

Each needs Quickbase's own answer: whether it refuses a label a hidden field already uses, what a
summary over a row-limited table counts, whether a lookup needs more than read access on its
parent, and whether a policy loaded with `from_json()` still covers a table its client creates.
"""

import secrets
from typing import Any

import pytest

from pyquickbase import (
    APIError,
    BoundPolicy,
    CreateField,
    CreateLookupField,
    CreateSummaryField,
    F,
    Policy,
    Quickbase,
    allow,
    fields,
    limit,
    tables,
)


def client(scratch: Any, bound: BoundPolicy) -> Quickbase:
    return Quickbase(realm=scratch.realm, token=scratch.token, policy=bound)


def test_a_label_a_hidden_field_uses_is_blocked_as_quickbase_would_refuse_it(
    scratch: Any,
) -> None:
    table = scratch.table(("Title", "text"), ("Salary", "numeric"))
    info = table.info()
    (title,) = table.resolve_field_ids(["Title"])
    bound = Policy(
        rules=[allow.read(info.id, [title]), allow.schema(info.id, "create_field")]
    ).bind(scratch.client.app(scratch.app_id))
    with client(scratch, bound) as limited:
        plan = limited.app(scratch.app_id).preview_schema_changes(
            [CreateField(table=info.id, label="salary", type="text")]
        )
    assert plan.changes[0].status == "blocked"
    assert "A field you can't see already uses this label" in plan.blockers[0]
    # The blocker's premise: Quickbase itself refuses the label, whatever its case.
    with pytest.raises(APIError):
        table.create_field("salary", "text")


def test_a_summary_over_a_row_limited_child_counts_only_the_records_it_reaches(
    scratch: Any,
) -> None:
    parent = scratch.table(("Name", "text"))
    child = scratch.table(("Title", "text"), ("Billable", "checkbox"))
    parent_id, child_id = parent.info().id, child.info().id
    app = scratch.client.app(scratch.app_id)
    app.table(child_id).create_relationship(app.table(parent_id), foreign_key="Parent")
    parent.insert([{"Name": "Roof"}])
    child.insert(
        [
            {"Title": "a", "Billable": True, "Parent": 1},
            {"Title": "b", "Billable": True, "Parent": 1},
            {"Title": "c", "Billable": False, "Parent": 1},
        ]
    )
    bound = Policy(
        rules=[
            allow.read(tables.all()),
            allow.schema(parent_id, "summary"),
            limit.rows(child_id, F("Billable") == True),  # noqa: E712
        ]
    ).bind(app)
    change = CreateSummaryField(
        child_table=child_id, foreign_key="Parent", label="Billable Count", function="count"
    )
    with client(scratch, bound) as limited:
        plan = limited.app(scratch.app_id).preview_schema_changes([change])
        assert plan.changes[0].status == "ready", plan.blockers
        limited.app(scratch.app_id).apply_schema_changes(plan)
    (made,) = [item for item in parent.fields(refresh=True) if item.label == "Billable Count"]
    assert made.mode == "summary"
    assert parent.first(select=["Billable Count"])["Billable Count"] == 2


def test_a_lookup_needs_only_read_access_on_the_parent_field(scratch: Any) -> None:
    parent = scratch.table(("Name", "text"), ("Budget", "numeric"))
    child = scratch.table(("Title", "text"))
    parent_id, child_id = parent.info().id, child.info().id
    (name,) = parent.resolve_field_ids(["Name"])
    app = scratch.client.app(scratch.app_id)
    app.table(child_id).create_relationship(app.table(parent_id), foreign_key="Parent")
    bound = Policy(
        rules=[
            allow.read(child_id),
            allow.read(parent_id, [name]),
            allow.schema(child_id, "lookup"),
        ]
    ).bind(app)
    with client(scratch, bound) as limited:
        app_limited = limited.app(scratch.app_id)
        ready = app_limited.preview_schema_changes(
            [CreateLookupField(child_table=child_id, foreign_key="Parent", parent_field="Name")]
        )
        assert ready.changes[0].status == "ready", ready.blockers
        app_limited.apply_schema_changes(ready)
        refused = app_limited.preview_schema_changes(
            [CreateLookupField(child_table=child_id, foreign_key="Parent", parent_field="Budget")]
        )
        assert refused.changes[0].status == "blocked"
    labels = {item.label for item in child.fields(refresh=True)}
    assert "Parent - Name" in labels
    assert "Parent - Budget" not in labels


def test_a_policy_loaded_from_json_covers_a_table_its_client_creates(scratch: Any) -> None:
    saved = (
        Policy(
            rules=[
                allow.create_tables(),
                allow.schema(tables.created(), "create_field"),
                allow.read(tables.created()),
                allow.insert(tables.created(), fields.all()),
            ]
        )
        .bind(scratch.client.app(scratch.app_id))
        .to_json()
    )
    name = f"ZZ Live {secrets.token_hex(4)}"
    with client(scratch, BoundPolicy.from_json(saved)) as limited:
        app = limited.app(scratch.app_id)
        plan = app.preview_schema_changes([CreateField(table=name, label="Notes", type="text")])
        assert plan.blockers, "A table that doesn't exist yet can't take a field"
        made = app.create_table(name)
        scratch.created.append(scratch.client.app(scratch.app_id).table(name))
        table_id = (made.info()).id
        assert limited.policy is not None
        assert table_id in limited.policy.created
        notes = made.create_field("Notes", "text")
        made.insert([{"Notes": "hello"}])
        assert (made.first(select=["Notes"]))["Notes"] == "hello"
        # Saved again, the policy still knows the table, so a new client can carry on.
        again = BoundPolicy.from_json(limited.policy.to_json())
    assert table_id in again.created
    # A policy pins labels when it's bound, so a created table's fields go by ID.
    assert again.allows("insert", table_id, field=notes.id)
    assert not again.allows("delete", table_id)
