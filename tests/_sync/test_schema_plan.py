# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Planning schema changes and applying the plan, against a fake app that keeps its state."""

from typing import Any

import httpx
import pytest

from pyquickbase import (
    CreateField,
    CreateLookupField,
    CreateRelationship,
    CreateSummaryField,
    CreateTable,
    DeleteField,
    DeleteRelationship,
    DeleteTable,
    InvalidValueError,
    Quickbase,
    ReadAccess,
    ReadRules,
    SchemaAccess,
    SchemaChangeError,
    SchemaMismatchError,
    UpdateField,
    UpdateTable,
    WriteAccess,
    WriteRules,
    tool_error,
)
from tests.schema_fake import FakeApp

APP = "bq1234567"
TASKS = "bqtasks"
PROJECTS = "bqprojects"


def client(fake: FakeApp, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, **kwargs)


def test_a_plan_changes_nothing_and_says_what_each_change_does() -> None:
    fake = FakeApp()
    changes = [
        CreateField(table="Tasks", label="Due", type="date", help_text="When it's due."),
        UpdateField(table="Tasks", field="Status", choices=("Open", "Done", "Blocked")),
        UpdateTable(table="Projects", description="Client work."),
    ]
    with client(fake) as qb:
        plan = qb.app(APP).plan_schema(changes)
    assert not fake.writes()
    assert [item.status for item in plan.changes] == ["change"] * 3
    assert [item.risk for item in plan.changes] == ["additive", "modifying", "modifying"]
    created, updated, renamed = plan.changes
    assert created.summary == 'Add field "Due" (date) to Tasks.'
    assert set(created.settings) == {"label", "type", "help_text"}
    assert updated.settings["choices"].before == ["Open", "Done"]
    assert updated.settings["choices"].after == ["Open", "Done", "Blocked"]
    assert renamed.settings["description"].after == "Client work."
    assert created.link is not None
    assert created.link.endswith(f"/table/{TASKS}/action/listfields")
    assert plan.has_changes
    assert not plan.blockers


def test_applying_a_plan_makes_its_changes_in_order() -> None:
    fake = FakeApp()
    changes = [
        CreateTable(name="Visits", single_record_name="Visit"),
        CreateField(table="Visits", label="Notes", type="text"),
        CreateRelationship(child="Visits", parent="Projects", foreign_key="Project"),
        CreateLookupField(child="Visits", foreign_key="Project", parent_field="Name"),
        CreateSummaryField(child="Visits", foreign_key="Project", label="Visits", function="COUNT"),
    ]
    with client(fake) as qb:
        app = qb.app(APP)
        plan = app.plan_schema(changes)
        assert [item.status for item in plan.changes] == ["change"] * 5, plan.blockers
        result = app.apply_schema(plan)
        assert len(result.applied) == 5
        again = app.plan_schema(changes)
    assert [item.status for item in again.changes] == ["no_change"] * 5
    visits = next(item for item in fake.tables.values() if item["name"] == "Visits")
    labels = [item["label"] for item in fake.fields[visits["id"]]]
    assert labels[-3:] == ["Notes", "Project", "Lookup 6"]
    assert fake.fields[PROJECTS][-1]["label"] == "Visits"


def test_a_change_the_app_already_has_is_no_change() -> None:
    fake = FakeApp()
    changes = [
        CreateTable(name="tasks"),
        CreateField(table="Tasks", label="Hours", type="numeric"),
        CreateField(table="Tasks", label="Double", type="numeric", formula="[Hours] * 2"),
        UpdateField(table="Tasks", field="Code", label="Code"),
        CreateRelationship(child="Tasks", parent="Projects", foreign_key="Project"),
        CreateSummaryField(
            child="Tasks", foreign_key="Project", label="Total Hours", child_field="Hours"
        ),
    ]
    with client(fake) as qb:
        plan = qb.app(APP).plan_schema(changes)
        result = qb.app(APP).apply_schema(plan)
    assert [item.status for item in plan.changes] == ["no_change"] * 6
    assert not plan.has_changes
    assert (result.applied, len(result.unchanged)) == ((), 6)
    assert not fake.writes()


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (CreateField(table="Jobs", label="X", type="text"), "There's no table 'Jobs'"),
        (UpdateField(table="Tasks", field="Nope", label="X"), "Tasks has no field 'Nope'"),
        (CreateField(table="Tasks", label="hours", type="text"), 'already has a field labeled'),
        (UpdateField(table="Tasks", field="Hours", formula="1"), "isn't a formula field"),
        (UpdateField(table="Tasks", field="Code", label="Hours"), "already has a field labeled"),
        (DeleteField(table="Tasks", field="Record ID#"), "built into every table"),
        (UpdateTable(table="Tasks", name="projects"), "already named Projects"),
        (CreateRelationship(child="Tasks", parent="Projects", foreign_key="Code"),
         'already has a field labeled "Code"'),
        (CreateLookupField(child="Tasks", foreign_key="Owner", parent_field="Name"),
         'no relationship with the foreign key "Owner"'),
        (CreateSummaryField(child="Tasks", foreign_key="Project", label="N", function="COUNT",
                            child_field="Hours"), "takes no child_field"),
        (CreateSummaryField(child="Tasks", foreign_key="Project", label="N"), "needs the child"),
    ],
)  # fmt: skip
def test_a_change_that_cant_be_made_blocks_the_plan(change: Any, match: str) -> None:
    fake = FakeApp()
    with client(fake) as qb:
        app = qb.app(APP)
        plan = app.plan_schema([change])
        assert plan.changes[0].status == "blocked"
        assert any(match in item for item in plan.blockers), plan.blockers
        with pytest.raises(InvalidValueError, match="nothing was changed"):
            app.apply_schema(plan)
    assert not fake.writes()


def test_a_plan_counts_what_a_deletion_loses_and_what_uses_it() -> None:
    fake = FakeApp()
    fake.counts = {f"{TASKS} {{6.XEX.''}}": 12, f"{TASKS} ": 40}
    with client(fake) as qb:
        plan = qb.app(APP).plan_schema(
            [DeleteField(table="Tasks", field="Hours"), DeleteTable(table="Tasks")]
        )
    hours, table = plan.changes
    assert (hours.risk, hours.affected_records) == ("destructive", 12)
    assert "12 records lose a value in it." in hours.warnings
    assert any('"Double"' in item for item in hours.warnings)
    assert any("Summaries on Projects" in item and "Total Hours" in item for item in hours.warnings)
    assert table.affected_records == 40
    assert any("child of Projects" in item for item in table.warnings)


def test_making_a_field_required_needs_every_record_to_have_a_value() -> None:
    fake = FakeApp()
    fake.counts = {f"{TASKS} {{8.EX.''}}": 3, f"{TASKS} ": 5}
    with client(fake) as qb:
        plan = qb.app(APP).plan_schema(
            [
                UpdateField(table="Tasks", field="Code", required=True),
                CreateField(table="Tasks", label="Owner", type="text", required=True),
            ]
        )
    assert plan.changes[0].blockers == ("3 records have no value, so it can't be required yet.",)
    assert "has records" in plan.changes[1].blockers[0]


def test_deleting_a_relationship_lists_what_goes_with_it() -> None:
    fake = FakeApp()
    with client(fake) as qb:
        app = qb.app(APP)
        plan = app.plan_schema([DeleteRelationship(child="Tasks", foreign_key="Project")])
        warnings = " ".join(plan.changes[0].warnings)
        assert '"Project - Name"' in warnings
        assert '"Total Hours"' in warnings
        app.apply_schema(plan)
    assert fake.writes() == [("DELETE", f"/tables/{TASKS}/relationship/9")]


def test_a_plan_is_refused_if_the_schema_changed_since() -> None:
    fake = FakeApp()
    with client(fake) as qb:
        app = qb.app(APP)
        plan = app.plan_schema([UpdateField(table="Tasks", field="Code", label="Ref")])
        fake.fields[TASKS][7]["label"] = "Reference"  # Someone renames it in the UI.
        with pytest.raises(SchemaMismatchError, match="changed since"):
            app.apply_schema(plan)
    assert not fake.writes()


def test_an_unrelated_change_doesnt_spoil_a_plan() -> None:
    fake = FakeApp()
    with client(fake) as qb:
        app = qb.app(APP)
        plan = app.plan_schema([UpdateField(table="Tasks", field="Code", label="Ref")])
        fake.fields[TASKS][6]["properties"]["choices"].append("Waiting")
        app.apply_schema(plan)
    assert fake.writes() == [("POST", "/fields/8")]


def test_a_failure_stops_the_plan_and_says_what_was_made() -> None:
    fake = FakeApp()
    changes = [
        CreateField(table="Tasks", label="A", type="text"),
        UpdateTable(table="Tasks", description="x"),
        CreateField(table="Tasks", label="B", type="text"),
    ]
    with client(fake) as qb:
        app = qb.app(APP)
        plan = app.plan_schema(changes)
        fake.fail = f"POST /tables/{TASKS}"
        with pytest.raises(SchemaChangeError, match="1 after it weren't attempted") as caught:
            app.apply_schema(plan)
        result = caught.value.result
        assert [item.change for item in result.applied] == [changes[0]]
        assert result.failed is not None
        assert result.failed.change == changes[1]
        assert [item.change for item in result.not_attempted] == [changes[2]]
        assert tool_error(caught.value)["error"]["changed"] == "some"
        fake.fail = None
        again = app.plan_schema(changes)
    assert [item.status for item in again.changes] == ["no_change", "change", "change"]


def test_a_plan_reports_what_the_clients_limits_would_refuse() -> None:
    fake = FakeApp()
    rules = WriteRules(
        {TASKS: WriteAccess(schema=SchemaAccess(create_fields=True, field_types=["text"]))}
    )
    with client(fake, write_policy=rules) as qb:
        plan = qb.app(APP).plan_schema(
            [
                CreateField(table="Tasks", label="Due", type="date"),
                DeleteField(table="Tasks", field="Code"),
                CreateField(table="Tasks", label="Note", type="text", unique=True),
            ]
        )
    due, code, note = plan.changes
    assert "only allows fields of type text" in due.blockers[0]
    assert "deleting fields isn't allowed" in code.blockers[0]
    assert "changing fields isn't allowed" in note.blockers[0]


def test_a_plan_reports_a_formula_that_reads_too_much() -> None:
    fake = FakeApp()
    reads = ReadRules({TASKS: ReadAccess(fields=[6, 7, 8, 10], filter=[6, 7, 8, 9, 10])})
    with client(fake, read_policy=reads) as qb:
        plan = qb.app(APP).plan_schema(
            [
                CreateField(table="Tasks", label="Key", type="numeric", formula="[Project] + 1"),
                CreateField(table="Tasks", label="Triple", type="numeric", formula="[Hours] * 3"),
            ]
        )
    assert "uses [Project]" in plan.changes[0].blockers[0]
    assert plan.changes[1].status == "change"


def test_plan_schema_needs_a_change() -> None:
    with client(FakeApp()) as qb:
        with pytest.raises(InvalidValueError, match="at least one"):
            qb.app(APP).plan_schema([])
