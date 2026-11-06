# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Schema changes on a client with limits: what's checked before anything is sent."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    ConfigurationError,
    InvalidValueError,
    Quickbase,
    ReadAccess,
    ReadRefusedError,
    ReadRules,
    SchemaAccess,
    UnknownFieldError,
    WriteAccess,
    WriteRefusedError,
    WriteRules,
    tool_error,
)

APP = "bq1234567"
TASKS = "bqtasks"
PROJECTS = "bqprojects"


class Fake:
    """Answers schema requests like Quickbase, and records each one."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self.tables = [{"id": TASKS, "name": "Tasks"}, {"id": PROJECTS, "name": "Projects"}]
        self.fields: dict[str, list[dict[str, Any]]] = {
            TASKS: [
                {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
                {"id": 6, "label": "Hours", "fieldType": "numeric"},
                {"id": 7, "label": "Salary", "fieldType": "numeric"},
                {"id": 8, "label": "Owner", "fieldType": "user"},
                {"id": 9, "label": "Project", "fieldType": "numeric",
                 "properties": {"foreignKey": True}},
                {"id": 10, "label": "Double", "fieldType": "numeric", "mode": "formula",
                 "properties": {"formula": "[Hours] * 2"}},
                {"id": 11, "label": "Pay", "fieldType": "numeric", "mode": "formula",
                 "properties": {"formula": "[Salary] / 12"}},
                {"id": 12, "label": "Link", "fieldType": "url", "mode": "formula",
                 "properties": {"formula": '"https://example.com"'}},
            ],
            PROJECTS: [
                {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
                {"id": 6, "label": "Name", "fieldType": "text"},
                {"id": 7, "label": "Budget", "fieldType": "numeric"},
            ],
        }  # fmt: skip
        self.relationship = {
            "id": 9, "parentTableId": PROJECTS, "childTableId": TASKS,
            "foreignKeyField": {"id": 9, "label": "Project"}, "lookupFields": [],
        }  # fmt: skip

    def writes(self) -> list[tuple[str, str, Any]]:
        return [item for item in self.sent if item[0] != "GET"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        self.sent.append((request.method, path, body))
        table = request.url.params.get("tableId", "")
        route = (request.method, path)
        if route == ("GET", "/tables"):
            return httpx.Response(200, json=self.tables)
        if route == ("GET", "/fields"):
            return httpx.Response(200, json=self.fields.get(table, []))
        if route == ("GET", f"/tables/{TASKS}/relationships"):
            rows = [self.relationship]
            return httpx.Response(200, json={"relationships": rows,
                                             "metadata": {"totalRelationships": 1}})  # fmt: skip
        if route == ("POST", "/tables"):
            created = {"id": "bqnew", "name": body["name"]}
            self.tables.append(created)
            self.fields["bqnew"] = [{"id": 3, "label": "Record ID#", "fieldType": "recordid"}]
            return httpx.Response(200, json=created)
        if route == ("POST", f"/tables/{TASKS}"):
            self.tables[0] = {**self.tables[0], **body}
            return httpx.Response(200, json=self.tables[0])
        if route == ("POST", "/fields"):
            created = {"id": 50, "label": body["label"], "fieldType": body["fieldType"]}
            self.fields.setdefault(table, []).append(created)
            return httpx.Response(200, json=created)
        if request.method == "POST" and path.startswith("/fields/"):
            field_id = int(path.rsplit("/", 1)[1])
            found = next(item for item in self.fields[table] if item["id"] == field_id)
            return httpx.Response(200, json={**found, **body})
        if request.method == "POST" and path.startswith(f"/tables/{TASKS}/relationship/"):
            if body.get("lookupFieldIds"):
                self.fields[TASKS].append(
                    {"id": 51, "label": "Project - Name", "fieldType": "text", "mode": "lookup",
                     "properties": {"lookupReferenceFieldId": 9, "lookupTargetFieldId": 6}}
                )  # fmt: skip
            for summary in body.get("summaryFields") or ():
                self.fields[PROJECTS].append(
                    {"id": 52, "label": summary["label"], "fieldType": "numeric",
                     "mode": "summary", "properties": {
                         "summaryReferenceFieldId": 9,
                         "summaryTargetFieldId": summary.get("summaryFid", 0),
                         "summaryFunction": summary["accumulationType"]}}
                )  # fmt: skip
            return httpx.Response(200, json=self.relationship)
        if request.method == "DELETE" and path == f"/tables/{TASKS}/relationship/9":
            return httpx.Response(200, json={"relationshipId": 9})
        return httpx.Response(404, json={"message": "Not found", "description": path})


def client(fake: Fake, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, **kwargs)


def reads(fields: Any = (3, 6, 8, 9, 10), projects: Any = (6,), **access: Any) -> ReadRules:
    """Salary and Budget can be filtered on but not returned, so they can be named."""
    filters = [6, 7] if projects is not True else True
    task_filters = True if fields is True else [*fields, 7]
    return ReadRules(
        {
            TASKS: ReadAccess(fields=fields, filter=task_filters, **access),
            PROJECTS: ReadAccess(fields=projects, filter=filters),
        }
    )


def writes(**grant: Any) -> WriteRules:
    schema = SchemaAccess(**grant)
    return WriteRules({TASKS: WriteAccess(schema=schema), PROJECTS: WriteAccess(schema=schema)})


# Changing a table


def test_update_info_renames_and_the_table_still_works() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app(APP).table("Tasks")
        info = tasks.update_info(name="Jobs", plural_record_name="Jobs")
        assert info.name == "Jobs"
        assert (tasks.info()).name == "Jobs"
        with pytest.raises(InvalidValueError, match="at least one"):
            tasks.update_info()
    assert fake.writes() == [
        ("POST", f"/tables/{TASKS}", {"name": "Jobs", "pluralRecordName": "Jobs"})
    ]


def test_delete_relationship_goes_to_the_child() -> None:
    fake = Fake()
    with client(fake) as qb:
        app = qb.app(APP)
        relationship = (app.table("Tasks").relationships())[0]
        with pytest.raises(InvalidValueError, match="call delete_relationship"):
            app.table("Projects").delete_relationship(relationship)
        app.table("Tasks").delete_relationship(relationship)
    assert fake.writes() == [("DELETE", f"/tables/{TASKS}/relationship/9", None)]


def test_deleting_a_relationship_needs_both_tables_to_allow_it() -> None:
    fake = Fake()
    rules = WriteRules(
        {
            TASKS: WriteAccess(schema=SchemaAccess(relationships=True, delete_fields=True)),
            PROJECTS: WriteAccess(schema=SchemaAccess(relationships=True)),
        }
    )
    with client(fake, write_policy=rules) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (tasks.relationships())[0]
        with pytest.raises(WriteRefusedError, match=f"summary isn't allowed on table {PROJECTS}"):
            tasks.delete_relationship(relationship)
    assert not fake.writes()


# New tables


def test_a_new_table_gets_the_access_for_new_tables() -> None:
    fake = Fake()
    rules = WriteRules(
        {},
        create_tables_in=[APP],
        new_tables=WriteAccess(schema=SchemaAccess(create_fields=True)),
    )
    read = ReadRules({}, new_tables=ReadAccess(fields=True))
    with client(fake, write_policy=rules, read_policy=read) as qb:
        table = qb.app(APP).create_table("Visits")
        table.create_field("Notes", "text")
        assert [item.label for item in table.fields()] == ["Record ID#", "Notes"]


def test_without_access_for_new_tables_a_new_one_is_off_limits() -> None:
    fake = Fake()
    rules = WriteRules({}, create_tables_in=[APP])
    with client(fake, write_policy=rules) as qb:
        table = qb.app(APP).create_table("Visits")
        with pytest.raises(WriteRefusedError, match="isn't listed"):
            table.create_field("Notes", "text")


# Formulas


def test_a_formula_may_use_readable_fields() -> None:
    fake = Fake()
    rules = writes(create_fields=True, update_fields=True, formulas=True)
    with client(fake, read_policy=reads(), write_policy=rules) as qb:
        tasks = qb.app(APP).table("Tasks")
        tasks.create_field("Triple", "numeric", formula="[Hours] * 3")
        tasks.update_field("Double", formula="Round([hours] * 2)")
    sent = [body["properties"]["formula"] for _, _, body in fake.writes()]
    assert sent == ["[Hours] * 3", "Round([hours] * 2)"]


@pytest.mark.parametrize(
    ("formula", "match"),
    [
        ("[Salary] / 12", r"uses \[Salary\]"),
        ("[Missing]", r"uses \[Missing\]"),
        ('GetRecords("{3.GT.0}")', r"calls getrecords\(\)"),
        ("ToText([Owner])", "a user field"),
    ],
)
def test_a_formula_that_could_show_more_is_refused(formula: str, match: str) -> None:
    fake = Fake()
    rules = writes(create_fields=True, formulas=True)
    with client(fake, read_policy=reads(), write_policy=rules) as qb:
        with pytest.raises(ReadRefusedError, match=match):
            qb.app(APP).table("Tasks").create_field("New", "text", formula=formula)
    assert not fake.writes()


def test_a_user_field_is_fine_when_emails_are_shown() -> None:
    fake = Fake()
    with client(fake, read_policy=reads(user_emails=True)) as qb:
        qb.app(APP).table("Tasks").create_field("Who", "text", formula="ToText([Owner])")
    assert len(fake.writes()) == 1


def test_a_formula_without_read_rules_isnt_checked() -> None:
    fake = Fake()
    with client(fake) as qb:
        qb.app(APP).table("Tasks").create_field("Pay", "numeric", formula="[Salary] / 12")
        qb.request(
            "POST", "/fields", params={"tableId": TASKS},
            json={"label": "Raw", "fieldType": "numeric", "properties": {"formula": "1"}},
        )  # fmt: skip
    assert len(fake.writes()) == 2


def test_with_write_rules_only_the_sdk_may_make_a_formula() -> None:
    fake = Fake()
    rules = writes(create_fields=True, formulas=True)
    with client(fake, write_policy=rules) as qb:
        with pytest.raises(WriteRefusedError, match="check what they read"):
            qb.request(
                "POST", "/fields", params={"tableId": TASKS},
                json={"label": "Raw", "fieldType": "numeric", "properties": {"formula": "1"}},
            )  # fmt: skip
    assert not fake.writes()


def test_a_link_formula_cant_be_changed_under_write_rules() -> None:
    fake = Fake()
    rules = writes(update_fields=True, formulas=True)
    with client(fake, write_policy=rules) as qb:
        with pytest.raises(WriteRefusedError, match="acts as them"):
            qb.app(APP).table("Tasks").update_field("Link", formula='"https://x"')
    assert not fake.writes()


def test_a_formula_is_only_shown_when_it_reveals_nothing() -> None:
    fake = Fake()
    with client(fake, read_policy=reads(fields=(3, 6, 10, 11))) as qb:
        fields = {item.label: item for item in qb.app(APP).table("Tasks").fields()}
    assert fields["Double"].formula == "[Hours] * 2"
    assert fields["Pay"].formula is None


# Lookups and summaries


def test_a_lookup_of_a_hidden_parent_field_is_refused() -> None:
    fake = Fake()
    with client(fake, read_policy=reads(fields=True)) as qb:
        app = qb.app(APP)
        tasks = app.table("Tasks")
        relationship = (tasks.relationships())[0]
        with pytest.raises(ReadRefusedError, match=f"field 7 of table {PROJECTS}"):
            tasks.ensure_lookup_field(relationship, "Budget")
        tasks.ensure_lookup_field(relationship, "Name")
    assert [path for method, path, _ in fake.writes()] == [f"/tables/{TASKS}/relationship/9"]


def test_a_summary_of_a_hidden_child_field_is_refused() -> None:
    fake = Fake()
    with client(fake, read_policy=reads(projects=True)) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (tasks.relationships())[0]
        with pytest.raises(ReadRefusedError, match=f"field 7 of table {TASKS}"):
            tasks.ensure_summary_field(relationship, "Salary", "Total Salary")
        tasks.ensure_summary_field(relationship, "Hours", "Total Hours")
    with client(fake, read_policy=reads(fields=(3, 6))) as limited:
        child = limited.app(APP).table("Tasks")
        hidden_key = (child.relationships())[0]
        # The foreign key is hidden, so even a count would show which tasks each project has.
        with pytest.raises(ReadRefusedError, match="foreign key"):
            child.ensure_count_field(hidden_key, "Tasks")
    assert len(fake.writes()) == 1


def test_a_new_field_the_rules_hide_says_it_was_made() -> None:
    fake = Fake()
    with client(fake, read_policy=reads()) as qb:
        tasks = qb.app(APP).table("Tasks")
        with pytest.raises(UnknownFieldError, match="was created") as caught:
            tasks.ensure_lookup_field((tasks.relationships())[0], "Name")
    assert tool_error(caught.value)["error"]["changed"] == "some"


def test_a_summary_needs_the_parents_grant() -> None:
    fake = Fake()
    rules = WriteRules(
        {
            TASKS: WriteAccess(schema=SchemaAccess(relationships=True, summaries=True)),
            PROJECTS: WriteAccess(schema=SchemaAccess(relationships=True)),
        }
    )
    with client(fake, write_policy=rules) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (tasks.relationships())[0]
        with pytest.raises(WriteRefusedError, match=f"summaries isn't allowed on table {PROJECTS}"):
            tasks.ensure_summary_field(relationship, "Hours", "Total Hours")


# Confirmation keys


def test_a_shared_confirmation_key_must_be_long_enough() -> None:
    key = bytes(range(32))
    with client(Fake(), confirmation_key=key) as qb:
        assert qb._transport.confirmation_key == key  # pyright: ignore[reportPrivateUsage]
    for bad in (b"short", "a" * 40):
        with pytest.raises(ConfigurationError, match="at least 32"):
            client(Fake(), confirmation_key=bad)
