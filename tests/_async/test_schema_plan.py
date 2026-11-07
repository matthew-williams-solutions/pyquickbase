"""Planning schema changes and applying the plan, against a fake app that keeps its state."""

from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    CreateField,
    CreateLookupField,
    CreateRelationship,
    CreateSummaryField,
    CreateTable,
    DeleteField,
    DeleteRelationship,
    DeleteTable,
    InvalidValueError,
    SchemaChangeError,
    SchemaMismatchError,
    UpdateField,
    UpdateTable,
    allow,
    deny,
    fields,
    tables,
    tool_error,
    tool_result,
)
from pyquickbase._decision import SCHEMA_VERBS
from tests.schema_fake import FakeApp

ANY_SCHEMA = allow.schema(tables.all(), *SCHEMA_VERBS)
"""What a client without write rules could do."""

pytestmark = pytest.mark.anyio

APP = "bq1234567"
TASKS = "bqtasks"
PROJECTS = "bqprojects"


def client(fake: FakeApp, **kwargs: Any) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, **kwargs)


async def test_a_plan_changes_nothing_and_says_what_each_change_does() -> None:
    fake = FakeApp()
    changes = [
        CreateField(table="Tasks", label="Due", type="date", help_text="When it's due."),
        UpdateField(table="Tasks", field="Status", choices=("Open", "Done", "Blocked")),
        UpdateTable(table="Projects", description="Client work."),
    ]
    async with client(fake) as qb:
        plan = await qb.app(APP).preview_schema_changes(changes)
    assert not fake.writes()
    assert [item.status for item in plan.changes] == ["ready"] * 3
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


async def test_applying_a_plan_makes_its_changes_in_order() -> None:
    fake = FakeApp()
    changes = [
        CreateTable(name="Visits", single_record_name="Visit"),
        CreateField(table="Visits", label="Notes", type="text"),
        CreateRelationship(child_table="Visits", parent_table="Projects", foreign_key="Project"),
        CreateLookupField(child_table="Visits", foreign_key="Project", parent_field="Name"),
        CreateSummaryField(
            child_table="Visits", foreign_key="Project", label="Visits", function="count"
        ),
    ]
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await app.preview_schema_changes(changes)
        assert [item.status for item in plan.changes] == ["ready"] * 5, plan.blockers
        result = await app.apply_schema_changes(plan)
        assert len(result.applied) == 5
        again = await app.preview_schema_changes(changes)
    assert [item.status for item in again.changes] == ["already_done"] * 5
    visits = next(item for item in fake.tables.values() if item["name"] == "Visits")
    labels = [item["label"] for item in fake.fields[visits["id"]]]
    assert labels[-3:] == ["Notes", "Project", "Lookup 6"]
    assert fake.fields[PROJECTS][-1]["label"] == "Visits"


async def test_a_change_the_app_already_has_is_no_change() -> None:
    fake = FakeApp()
    changes = [
        CreateTable(name="tasks"),
        CreateField(table="Tasks", label="Hours", type="numeric"),
        CreateField(table="Tasks", label="Double", type="numeric", formula="[Hours] * 2"),
        UpdateField(table="Tasks", field="Code", label="Code"),
        CreateRelationship(child_table="Tasks", parent_table="Projects", foreign_key="Project"),
        CreateSummaryField(
            child_table="Tasks", foreign_key="Project", label="Total Hours", child_field="Hours"
        ),
    ]
    async with client(fake) as qb:
        plan = await qb.app(APP).preview_schema_changes(changes)
        result = await qb.app(APP).apply_schema_changes(plan)
    assert [item.status for item in plan.changes] == ["already_done"] * 6
    assert not plan.has_changes
    assert (result.applied, len(result.already_done)) == ((), 6)
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
        (CreateRelationship(child_table="Tasks", parent_table="Projects", foreign_key="Code"),
         'already has a field labeled "Code"'),
        (CreateLookupField(child_table="Tasks", foreign_key="Owner", parent_field="Name"),
         'no relationship with the foreign key "Owner"'),
        (CreateSummaryField(child_table="Tasks", foreign_key="Project", label="N", function="count",
                            child_field="Hours"), "takes no child_field"),
        (CreateSummaryField(child_table="Tasks", foreign_key="Project", label="N"),
         "needs the child"),
    ],
)  # fmt: skip
async def test_a_change_that_cant_be_made_blocks_the_plan(change: Any, match: str) -> None:
    fake = FakeApp()
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await app.preview_schema_changes([change])
        assert plan.changes[0].status == "blocked"
        assert any(match in item for item in plan.blockers), plan.blockers
        with pytest.raises(InvalidValueError, match="nothing was changed"):
            await app.apply_schema_changes(plan)
    assert not fake.writes()


async def test_a_plan_counts_what_a_deletion_loses_and_what_uses_it() -> None:
    fake = FakeApp()
    fake.counts = {f"{TASKS} {{6.XEX.''}}": 12, f"{TASKS} ": 40}
    async with client(fake) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [DeleteField(table="Tasks", field="Hours"), DeleteTable(table="Tasks")]
        )
    hours, table = plan.changes
    assert (hours.risk, hours.affected_records) == ("destructive", 12)
    assert "12 records lose a value in it." in hours.warnings
    assert any('"Double"' in item for item in hours.warnings)
    assert any("Summaries on Projects" in item and "Total Hours" in item for item in hours.warnings)
    assert table.affected_records == 40
    assert any("child of Projects" in item for item in table.warnings)


async def test_making_a_field_required_needs_every_record_to_have_a_value() -> None:
    fake = FakeApp()
    fake.counts = {f"{TASKS} {{8.EX.''}}": 3, f"{TASKS} ": 5}
    async with client(fake) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                UpdateField(table="Tasks", field="Code", required=True),
                CreateField(table="Tasks", label="Owner", type="text", required=True),
            ]
        )
    assert plan.changes[0].blockers == (
        "3 records have no value in it, so it can't be required yet. Fill them in first.",
    )
    assert "has records" in plan.changes[1].blockers[0]


async def test_deleting_a_relationship_lists_what_goes_with_it() -> None:
    fake = FakeApp()
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await app.preview_schema_changes(
            [DeleteRelationship(child_table="Tasks", foreign_key="Project")]
        )
        warnings = " ".join(plan.changes[0].warnings)
        assert '"Project - Name"' in warnings
        assert '"Total Hours"' in warnings
        await app.apply_schema_changes(plan)
    assert fake.writes() == [("DELETE", f"/tables/{TASKS}/relationship/9")]


async def test_a_plan_is_refused_if_the_schema_changed_since() -> None:
    fake = FakeApp()
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await app.preview_schema_changes(
            [UpdateField(table="Tasks", field="Code", label="Ref")]
        )
        fake.fields[TASKS][7]["label"] = "Reference"  # Someone renames it in the UI.
        with pytest.raises(SchemaMismatchError, match="changed since"):
            await app.apply_schema_changes(plan)
    assert not fake.writes()


async def test_an_unrelated_change_doesnt_spoil_a_plan() -> None:
    fake = FakeApp()
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await app.preview_schema_changes(
            [UpdateField(table="Tasks", field="Code", label="Ref")]
        )
        fake.fields[TASKS][6]["properties"]["choices"].append("Waiting")
        await app.apply_schema_changes(plan)
    assert fake.writes() == [("POST", "/fields/8")]


async def test_a_failure_stops_the_plan_and_says_what_was_made() -> None:
    fake = FakeApp()
    changes = [
        CreateField(table="Tasks", label="A", type="text"),
        UpdateTable(table="Tasks", description="x"),
        CreateField(table="Tasks", label="B", type="text"),
    ]
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await app.preview_schema_changes(changes)
        fake.fail = f"POST /tables/{TASKS}"
        with pytest.raises(SchemaChangeError, match="1 after it weren't attempted") as caught:
            await app.apply_schema_changes(plan)
        result = caught.value.result
        assert [item.change for item in result.applied] == [changes[0]]
        assert result.failed is not None
        assert result.failed.change == changes[1]
        assert [item.change for item in result.not_attempted] == [changes[2]]
        assert tool_error(caught.value)["error"]["data_changed"] == "partly"
        fake.fail = None
        again = await app.preview_schema_changes(changes)
    assert [item.status for item in again.changes] == ["already_done", "ready", "ready"]


async def test_a_plan_reports_what_the_clients_limits_would_refuse() -> None:
    fake = FakeApp()
    policy = fake.bind(allow.read(TASKS), allow.schema(TASKS, "add_field", field_types=["text"]))
    async with client(fake, policy=policy) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                CreateField(table="Tasks", label="Due", type="date"),
                DeleteField(table="Tasks", field="Code"),
                CreateField(table="Tasks", label="Note", type="text", unique=True),
            ]
        )
    due, code, note = plan.changes
    assert "only allows fields of type text" in due.blockers[0]
    assert "deleting Code (8) in Tasks" in code.blockers[0]
    assert "changing Code (8) in Tasks" not in note.blockers[0]
    assert "changing" in note.blockers[0]


async def test_a_plan_reports_a_formula_that_reads_too_much() -> None:
    fake = FakeApp()
    reads = allow.read(TASKS, [6, 7, 8, 10], filter=[6, 7, 8, 9, 10])
    async with client(fake, policy=fake.bind(reads, ANY_SCHEMA)) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                CreateField(table="Tasks", label="Key", type="numeric", formula="[Project] + 1"),
                CreateField(table="Tasks", label="Triple", type="numeric", formula="[Hours] * 3"),
            ]
        )
    assert "uses [Project]" in plan.changes[0].blockers[0]
    assert plan.changes[1].status == "ready"


async def test_preview_schema_changes_needs_a_change() -> None:
    async with client(FakeApp()) as qb:
        with pytest.raises(InvalidValueError, match="at least one"):
            await qb.app(APP).preview_schema_changes([])


# Findings from testing the schema tools by hand. Each names its finding.


async def test_a_formula_may_use_a_field_made_earlier_in_the_plan() -> None:  # F1
    changes = [
        CreateField(table="Tasks", label="Base", type="numeric"),
        CreateField(table="Tasks", label="Twice", type="numeric", formula="[Base] * 2"),
    ]
    fake = FakeApp()
    every = fake.bind(allow.read(TASKS), ANY_SCHEMA)
    listed = fake.bind(allow.read(TASKS, [6, 8]), ANY_SCHEMA)
    async with client(FakeApp(), policy=every) as qb:
        plan = await qb.app(APP).preview_schema_changes(changes)
    assert [item.status for item in plan.changes] == ["ready", "ready"], plan.blockers
    async with client(FakeApp(), policy=listed) as qb:
        plan = await qb.app(APP).preview_schema_changes(changes)
    # A new field gets new_fields, and isn't one of the fields the rule names, so it's hidden.
    assert "uses [Base]" in plan.changes[1].blockers[0]


def add_owners(fake: FakeApp) -> None:
    """Another parent of Tasks, whose Full Name has the same ID as Projects' Name."""
    fake.tables["bqowners"] = {"id": "bqowners", "name": "Owners", "description": "",
                               "keyFieldId": 3}  # fmt: skip
    fake.fields["bqowners"] = [
        *fake.built_in(),
        {"id": 6, "label": "Full Name", "fieldType": "text"},
    ]
    fake.fields[TASKS] += [
        {"id": 12, "label": "Owner", "fieldType": "numeric", "properties": {"foreignKey": True}},
        {"id": 13, "label": "Owner - Full Name", "fieldType": "text", "mode": "lookup",
         "properties": {"lookupReferenceFieldId": 12, "lookupTargetFieldId": 6}},
    ]  # fmt: skip
    fake.relationships.append(
        {"id": 12, "parentTableId": "bqowners", "childTableId": TASKS,
         "foreignKeyField": {"id": 12, "label": "Owner"}}
    )  # fmt: skip


async def test_a_deletion_only_lists_lookups_of_that_table() -> None:  # F13
    fake = FakeApp()
    add_owners(fake)
    async with client(fake) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [DeleteField(table="Projects", field="Name")]
        )
    lookups = [item for item in plan.changes[0].warnings if item.startswith("Lookups")]
    assert lookups == ['Lookups on Tasks stop working: "Project - Name".']


async def test_a_deletion_follows_formulas_to_what_uses_them() -> None:  # F22, F15
    fake = FakeApp()
    fake.fields[TASKS][9]["properties"]["formula"] = "[_FID_6] * 2"  # Double, by Hours' ID
    fake.fields[PROJECTS].append(
        {"id": 8, "label": "Total Double", "fieldType": "numeric", "mode": "summary",
         "properties": {"summaryReferenceFieldId": 9, "summaryTargetFieldId": 10,
                        "summaryFunction": "SUM"}}
    )  # fmt: skip
    async with client(fake) as qb:
        plan = await qb.app(APP).preview_schema_changes([DeleteField(table="Tasks", field="Hours")])
    warnings = plan.changes[0].warnings
    assert any('"Double"' in item and item.startswith("Formulas") for item in warnings)
    assert 'Summaries on Projects stop working, through "Double": "Total Double".' in warnings


async def test_a_summary_can_filter_its_child_records() -> None:
    fake = FakeApp()
    change = CreateSummaryField(
        child_table="Tasks", foreign_key="Project", label="Open Hours", child_field="Hours",
        where=" {7.EX.'Open'} ",
    )  # fmt: skip
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await app.preview_schema_changes([change])
        planned = plan.changes[0]
        assert planned.status == "ready", plan.blockers
        assert planned.settings["where"].after == "{7.EX.'Open'}"
        assert planned.summary.endswith("its Tasks records that match {7.EX.'Open'}.")
        await app.apply_schema_changes(plan)
        again = await app.preview_schema_changes([change])
        other = await app.preview_schema_changes(
            [CreateSummaryField(child_table="Tasks", foreign_key="Project", label="Open Hours",
                                child_field="Hours")]
        )  # fmt: skip
    assert fake.fields[PROJECTS][-1]["properties"]["summaryQuery"] == "{7.EX.'Open'}"
    assert again.changes[0].status == "already_done"
    assert other.changes[0].status == "blocked"


@pytest.mark.parametrize(
    ("where", "match"),
    [("{nonsense", "expected"), ("{99.EX.1}", "field 99 doesn't exist")],
)
async def test_a_summary_filter_quickbase_would_save_broken_blocks_the_plan(
    where: str, match: str
) -> None:
    change = CreateSummaryField(
        child_table="Tasks", foreign_key="Project", label="X", child_field="Hours", where=where
    )
    async with client(FakeApp()) as qb:
        plan = await qb.app(APP).preview_schema_changes([change])
    assert plan.changes[0].status == "blocked"
    assert any("filter can't be used" in item and match in item for item in plan.blockers)


async def test_deleting_a_field_a_summary_filters_on_warns() -> None:
    fake = FakeApp()
    fake.fields[PROJECTS].append(
        {"id": 8, "label": "Open Hours", "fieldType": "numeric", "mode": "summary",
         "properties": {"summaryReferenceFieldId": 9, "summaryTargetFieldId": 6,
                        "summaryFunction": "SUM", "summaryQuery": "{7.EX.'Open'}"}}
    )  # fmt: skip
    async with client(fake) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [DeleteField(table="Tasks", field="Status")]
        )
    assert 'Summaries on Projects stop working: "Open Hours".' in plan.changes[0].warnings


async def test_a_label_a_hidden_field_uses_is_a_blocker() -> None:  # F14
    fake = FakeApp()
    policy = fake.bind(allow.read(TASKS, [6, 7, 8]), allow.schema(TASKS, "add_field"))
    async with client(FakeApp(), policy=policy) as qb:
        hidden = await qb.app(APP).preview_schema_changes(
            [CreateField(table="Tasks", label="double", type="text")]
        )
        free = await qb.app(APP).preview_schema_changes(
            [CreateField(table="Tasks", label="Pay", type="text")]
        )
    assert hidden.changes[0].blockers == (
        "A field you can't see already uses this label, so Quickbase would refuse it. Choose "
        "another label.",
    )
    assert "Double" not in str(hidden)
    assert not free.changes[0].blockers
    assert not free.changes[0].warnings


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (CreateField(table="Tasks", label="Kind", type="text", choices=("a", "b")),
         "not a text field"),
        (UpdateField(table="Tasks", field="Code", choices=("a",)), "not a text field"),
    ],
)  # fmt: skip
async def test_choices_only_go_on_fields_that_take_them(change: Any, match: str) -> None:  # F17
    async with client(FakeApp()) as qb:
        plan = await qb.app(APP).preview_schema_changes([change])
    assert match in plan.changes[0].blockers[0]


async def test_a_label_that_only_differs_in_spaces_is_another_field() -> None:  # F16
    # Quickbase keeps a label's spaces exactly, so these are new fields, which look like old ones.
    async with client(FakeApp()) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                CreateField(table="Tasks", label="  Hours ", type="numeric"),
                UpdateField(table="Tasks", field="Code", label="Double "),
                CreateField(table="Tasks", label="Notes", type="text"),
            ]
        )
    hours, code, notes = plan.changes
    assert hours.status == "ready"
    assert hours.warnings[0].startswith('"  Hours " looks like "Hours", which Tasks already has.')
    assert code.status == "ready"
    assert any('looks like "Double"' in item for item in code.warnings)
    assert not notes.warnings


async def test_a_table_name_matches_ignoring_surrounding_spaces() -> None:  # F16
    # Quickbase refuses a table name in use, ignoring case and surrounding spaces.
    async with client(FakeApp()) as qb:
        plan = await qb.app(APP).preview_schema_changes([CreateTable(name=" tasks ")])
    assert plan.changes[0].status == "already_done"
    assert "ignoring case and spaces" in plan.changes[0].summary


async def test_a_plan_says_what_it_can_only_check_when_applied() -> None:  # F17, F18
    async with client(FakeApp()) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                CreateField(table="Tasks", label="Kind", type="text-multiple-choice"),
                CreateField(table="Tasks", label="Late", type="checkbox", formula="[Code] & 'x'"),
            ]
        )
    kind, late = plan.changes
    assert "no choices yet" in kind.warnings[0]
    assert "only checks that the formula's result is a checkbox" in late.warnings[0]
    assert "the changes before it in the plan stay made" in late.warnings[0]


async def test_a_name_that_matches_says_how() -> None:  # F19
    async with client(FakeApp()) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                CreateTable(name="tasks"),
                CreateField(table="Tasks", label="hours", type="numeric"),
            ]
        )
    table, field = plan.changes
    assert table.status == "already_done"
    assert "Later changes to tasks change the existing Tasks" in table.warnings[0]
    assert field.summary == 'Tasks already has field "Hours", which matches ignoring case.'


async def test_refusals_name_tables_and_say_what_the_other_table_needs() -> None:  # F3, F4
    fake = FakeApp()
    # A lookup is granted on the child, and needs read access to the parent's field, which this
    # client may only filter on.
    policy = fake.bind(
        allow.read(TASKS),
        allow.filter(PROJECTS, fields.all()),
        allow.schema(TASKS, "lookup", "add_field", field_types=["date"]),
    )
    async with client(fake, policy=policy) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                CreateField(table="Tasks", label="Note", type="text"),
                CreateLookupField(
                    child_table="Tasks", foreign_key="Project", parent_field="Total Hours"
                ),
            ]
        )
    field, lookup = plan.changes
    assert "The table only allows fields of type date" in field.blockers[0]
    assert "adding a field to Tasks" in field.blockers[0]
    assert "Total Hours (7) of Projects" in lookup.blockers[0]


async def test_counts_say_what_they_count() -> None:  # F5, F7, F21
    fake = FakeApp()
    fake.counts = {f"{TASKS} {{8.EX.''}}": 1, f"{TASKS} {{7.EX.'Done'}}": 4, f"{TASKS} ": 5}
    async with client(fake) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                UpdateField(table="Tasks", field="Code", required=True),
                UpdateField(table="Tasks", field="Status", choices=("Open",)),
            ]
        )
    required, choices = plan.changes
    assert required.blockers[0].startswith("1 record has no value in it")
    assert choices.warnings == (
        "These choices are taken away, but records that hold one keep it: 'Done' (4 records hold).",
    )


# What the policy would do once a plan is applied


async def test_a_new_field_says_who_can_read_and_write_it() -> None:
    fake = FakeApp()
    policy = fake.bind(allow.read(TASKS), allow.schema(TASKS, "add_field", "formula"))
    async with client(fake, policy=policy) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [CreateField(table="Tasks", label="Notes", type="text")]
        )
    assert plan.changes[0].policy_effects == (
        "It can be read, through allow.read('bqtasks').",
        "It can't be written until the policy is bound again.",
    )
    assert tool_result(plan)["changes"][0]["policy_effects"] == list(plan.changes[0].policy_effects)


async def test_a_derived_field_says_which_classes_it_gets_and_what_hides_it() -> None:
    fake = FakeApp()
    policy = fake.bind(
        allow.read(tables.all()),
        allow.schema(TASKS, "add_field", "formula", "lookup"),
        deny.read(TASKS, fields.classed("sensitive")),
        classes={"sensitive": [("Projects", "Name"), ("Tasks", "Code")]},
    )
    async with client(fake, policy=policy) as qb:
        plan = await qb.app(APP).preview_schema_changes(
            [
                CreateField(table="Tasks", label="Code Copy", type="text", formula="[Hours] & 1"),
                CreateLookupField(
                    child_table="Tasks", foreign_key="Project", parent_field="Name", label="Name"
                ),
            ]
        )
    formula, lookup = plan.changes
    assert not any("class" in item for item in formula.policy_effects)
    assert "Once bound again, it's in class sensitive." in lookup.policy_effects
    assert "deny.read('bqtasks', fields.classed('sensitive')) then hides it." in (
        lookup.policy_effects
    )
    assert lookup.status == "ready"


async def test_deleting_the_only_field_a_rule_names_is_a_blocker() -> None:
    fake = FakeApp()
    policy = fake.bind(
        allow.read(TASKS),
        allow.update(TASKS, ["Code"]),
        allow.insert(TASKS, ["Code", "Hours"]),
        allow.schema(TASKS, "delete_field"),
    )
    async with client(fake, policy=policy) as qb:
        plan = await qb.app(APP).preview_schema_changes([DeleteField(table="Tasks", field="Code")])
    (change,) = plan.changes
    assert change.status == "blocked"
    assert change.policy_effects == (
        "allow.update('bqtasks', ['Code']) loses Code (8).",
        "allow.insert('bqtasks', ['Code', 'Hours']) loses Code (8).",
    )
    assert change.blockers == (
        "allow.update('bqtasks', ['Code']) would match nothing once Code (8) is deleted, so it "
        "would no longer mean what it says. Change the rule first, then bind the policy again.",
    )


async def test_deleting_the_only_table_a_rule_names_is_a_blocker() -> None:
    fake = FakeApp()
    policy = fake.bind(
        allow.read([TASKS, PROJECTS]),
        allow.update(PROJECTS, fields.all()),
        allow.schema(PROJECTS, "delete_table"),
    )
    async with client(fake, policy=policy) as qb:
        plan = await qb.app(APP).preview_schema_changes([DeleteTable(table="Projects")])
    (change,) = plan.changes
    assert "allow.read(['bqtasks', 'bqprojects']) loses table Projects." in change.policy_effects
    assert any(item.startswith("allow.update('bqprojects', fields.all()) would match nothing")
               for item in change.blockers)  # fmt: skip
