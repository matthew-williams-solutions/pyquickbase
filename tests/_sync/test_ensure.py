# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Tests for the methods that make setup scripts safe to rerun, using a small fake of Quickbase."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    F,
    InvalidQueryError,
    InvalidResponseError,
    InvalidValueError,
    Quickbase,
    Relationship,
    SchemaMismatchError,
)
from pyquickbase._models import relationship_from_api

APP = "bq1234567"
CHILD = "bqchild"
PARENT = "bqparent"


class FakeSchema:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.tables = [{"id": CHILD, "name": "Tasks"}, {"id": PARENT, "name": "Projects"}]
        self.fields: dict[str, list[dict[str, Any]]] = {
            CHILD: [
                {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
                {"id": 6, "label": "Hours", "fieldType": "numeric"},
                {"id": 7, "label": "Project", "fieldType": "numeric",
                 "properties": {"foreignKey": True}},
                {"id": 8, "label": "Code", "fieldType": "text"},
                {"id": 9, "label": "Status", "fieldType": "text-multiple-choice",
                 "properties": {"choices": ["Open", "Done"]}},
                {"id": 10, "label": "Total", "fieldType": "numeric", "mode": "formula",
                 "properties": {"formula": "1 + 1"}},
            ],
            PARENT: [
                {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
                {"id": 6, "label": "Name", "fieldType": "text"},
                {"id": 7, "label": "Owner", "fieldType": "text"},
            ],
        }  # fmt: skip
        self.relationships = [
            {"id": 7, "parentTableId": PARENT, "childTableId": CHILD,
             "foreignKeyField": {"id": 7, "label": "Project", "type": "numeric"},
             "lookupFields": [], "isCrossApp": False}
        ]  # fmt: skip
        self.next_id = 50
        self.ignore_new_fields = False

    def writes(self) -> list[tuple[str, Any]]:
        return [
            (r.url.path.removeprefix("/v1"), json.loads(r.content))
            for r in self.requests
            if r.method == "POST"
        ]

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        table = request.url.params.get("tableId", "")
        if (request.method, path) == ("GET", "/tables"):
            return httpx.Response(200, json=self.tables)
        if (request.method, path) == ("GET", "/fields"):
            return httpx.Response(200, json=self.fields[table])
        if (request.method, path) == ("GET", f"/tables/{CHILD}/relationships"):
            return httpx.Response(
                200,
                json={
                    "relationships": self.relationships,
                    "metadata": {"totalRelationships": len(self.relationships)},
                },
            )
        body = json.loads(request.content) if request.content else {}
        if (request.method, path) == ("POST", "/tables"):
            created = {"id": "bqnew", "name": body["name"]}
            self.tables = [*self.tables, created]
            self.fields["bqnew"] = []
            return httpx.Response(200, json=created)
        if (request.method, path) == ("POST", "/fields"):
            created = {
                "id": self.new_id(),
                "label": body["label"],
                "fieldType": body["fieldType"],
                "properties": body.get("properties", {}),
            }
            self.fields[table] = [*self.fields[table], created]
            return httpx.Response(200, json=created)
        if request.method == "POST" and path.startswith("/fields/"):
            field_id = int(path.rsplit("/", 1)[1])
            found = next(f for f in self.fields[table] if f["id"] == field_id)
            merged = {**found.get("properties", {}), **body.get("properties", {})}
            found.update({k: v for k, v in body.items() if k != "properties"})
            found["properties"] = merged
            return httpx.Response(200, json=found)
        if (request.method, path) == ("POST", f"/tables/{CHILD}/relationship"):
            field_id = self.new_id()
            self.fields[CHILD] = [
                *self.fields[CHILD],
                {"id": field_id, "label": body["foreignKeyField"]["label"],
                 "fieldType": "numeric", "properties": {"foreignKey": True}},
            ]  # fmt: skip
            created = {
                "id": field_id, "parentTableId": PARENT, "childTableId": CHILD,
                "foreignKeyField": {"id": field_id, "label": body["foreignKeyField"]["label"]},
                "lookupFields": [], "isCrossApp": False,
            }  # fmt: skip
            self.relationships = [*self.relationships, created]
            return httpx.Response(200, json=created)
        if (request.method, path) == ("POST", f"/tables/{CHILD}/relationship/7"):
            if not self.ignore_new_fields:
                self.add_to_relationship(body)
            return httpx.Response(200, json=self.relationships[0])
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    def add_to_relationship(self, body: dict[str, Any]) -> None:
        for target in body.get("lookupFieldIds", []):
            self.fields[CHILD] = [
                *self.fields[CHILD],
                {"id": self.new_id(), "label": f"Project - {target}", "fieldType": "text",
                 "mode": "lookup",
                 "properties": {"lookupReferenceFieldId": 7, "lookupTargetFieldId": target}},
            ]  # fmt: skip
        for summary in body.get("summaryFields", []):
            self.fields[PARENT] = [
                *self.fields[PARENT],
                {"id": self.new_id(), "label": summary["label"], "fieldType": "numeric",
                 "mode": "summary",
                 "properties": {"summaryReferenceFieldId": 7,
                                "summaryTargetFieldId": summary.get("summaryFid", 0),
                                "summaryFunction": summary["accumulationType"],
                                **({"summaryQuery": summary["where"]} if "where" in summary
                                   else {})}},
            ]  # fmt: skip


@pytest.fixture
def fake() -> FakeSchema:
    return FakeSchema()


@pytest.fixture
def app(fake: FakeSchema) -> Any:
    client = httpx.Client(transport=httpx.MockTransport(fake))
    with Quickbase(realm="acme", token="t", http_client=client) as qb:
        yield qb.app(APP)


def project(fake: FakeSchema) -> Relationship:
    return relationship_from_api(fake.relationships[0])


def test_ensure_table_creates_once_and_ignores_case(app: Any, fake: FakeSchema) -> None:
    app.ensure_table("New Table", single_record_name="Thing")
    app.ensure_table("new table")
    assert fake.writes() == [("/tables", {"name": "New Table", "singleRecordName": "Thing"})]
    found = app.ensure_table("TASKS")
    assert len(fake.writes()) == 1
    assert (found.info()).name == "Tasks"


def test_ensure_field_creates_then_leaves_a_matching_field_alone(
    app: Any, fake: FakeSchema
) -> None:
    tasks = app.table("Tasks")
    created = tasks.ensure_field("Notes", "text")
    assert created.label == "Notes"
    count = len(fake.writes())
    assert (tasks.ensure_field("notes", "text")).id == created.id
    assert len(fake.writes()) == count


def test_ensure_field_refuses_a_different_type(app: Any, fake: FakeSchema) -> None:
    with pytest.raises(SchemaMismatchError, match="Code"):
        app.table("Tasks").ensure_field("Code", "numeric")
    assert fake.writes() == []


def test_ensure_field_repairs_required_and_unique(app: Any, fake: FakeSchema) -> None:
    updated = app.table("Tasks").ensure_field("Code", "text", required=True, unique=True)
    assert fake.writes() == [("/fields/8", {"required": True, "unique": True})]
    assert updated.required
    assert updated.unique


def test_ensure_field_updates_only_what_differs(app: Any, fake: FakeSchema) -> None:
    tasks = app.table("Tasks")
    tasks.ensure_field("Total", "numeric", formula="1 + 1")
    tasks.ensure_field("Status", "text-multiple-choice", choices=["Done", "Open"])
    assert fake.writes() == []
    tasks.ensure_field("Total", "numeric", formula="2 + 2")
    assert fake.writes() == [("/fields/10", {"properties": {"formula": "2 + 2"}})]


def test_ensure_field_treats_datetime_as_the_timestamp_it_reports(
    app: Any, fake: FakeSchema
) -> None:
    fake.fields[CHILD] = [
        *fake.fields[CHILD],
        {"id": 20, "label": "When", "fieldType": "timestamp"},
    ]
    app.table("Tasks").ensure_field("When", "datetime")
    assert fake.writes() == []


def test_ensure_relationship_finds_the_existing_one(app: Any, fake: FakeSchema) -> None:
    found = app.table("Tasks").ensure_relationship(app.table("Projects"), foreign_key="project")
    assert found.id == 7
    assert fake.writes() == []


def test_ensure_relationship_creates_a_second_link_to_the_same_parent(
    app: Any, fake: FakeSchema
) -> None:
    tasks, projects = app.table("Tasks"), app.table("Projects")
    created = tasks.ensure_relationship(projects, foreign_key="Reviewed Project")
    assert created.id != 7
    again = tasks.ensure_relationship(projects, foreign_key="Reviewed Project")
    assert again.id == created.id
    assert len(fake.writes()) == 1


def test_ensure_lookup_sends_only_the_new_parent_field(app: Any, fake: FakeSchema) -> None:
    """Sending an existing lookup's ID would make Quickbase create a lookup of another field."""
    tasks = app.table("Tasks")
    rel = project(fake)
    first = tasks.ensure_lookup_field(rel, "Name", label="Project Name")
    second = tasks.ensure_lookup_field(rel, "Owner")
    assert first.label == "Project Name"
    assert second.lookup_target_field_id == 7
    sent = [body for path, body in fake.writes() if path.endswith("/relationship/7")]
    assert sent == [{"lookupFieldIds": [6]}, {"lookupFieldIds": [7]}]


def test_ensure_lookup_finds_one_with_any_label_and_renames_it(app: Any, fake: FakeSchema) -> None:
    tasks = app.table("Tasks")
    rel = project(fake)
    created = tasks.ensure_lookup_field(rel, "Name")
    count = len(fake.writes())
    renamed = tasks.ensure_lookup_field(rel, "Name", label="Project Name")
    assert renamed.id == created.id
    assert renamed.label == "Project Name"
    assert fake.writes()[count:] == [(f"/fields/{created.id}", {"label": "Project Name"})]
    tasks.ensure_lookup_field(rel, "Name", label="Project Name")
    assert len(fake.writes()) == count + 1


def test_ensure_lookup_reports_one_quickbase_did_not_create(app: Any, fake: FakeSchema) -> None:
    fake.ignore_new_fields = True
    with pytest.raises(InvalidResponseError):
        app.table("Tasks").ensure_lookup_field(project(fake), "Name")


def test_ensure_summary_creates_once_on_the_parent(app: Any, fake: FakeSchema) -> None:
    tasks = app.table("Tasks")
    rel = project(fake)
    created = tasks.ensure_summary_field(rel, "Hours", "Total Hours")
    assert created.mode == "summary"
    assert created.summary_function == "SUM"
    assert created.summary_target_field_id == 6
    again = tasks.ensure_summary_field(rel, "Hours", "total hours")
    assert again.id == created.id
    assert fake.writes() == [
        (
            f"/tables/{CHILD}/relationship/7",
            {
                "summaryFields": [
                    {"summaryFid": 6, "label": "Total Hours", "accumulationType": "SUM"}
                ]
            },
        )
    ]


def test_ensure_summary_refuses_a_label_used_by_something_else(app: Any, fake: FakeSchema) -> None:
    tasks = app.table("Tasks")
    with pytest.raises(SchemaMismatchError, match="Owner"):
        tasks.ensure_summary_field(project(fake), "Hours", "Owner")
    tasks.ensure_summary_field(project(fake), "Hours", "Total Hours")
    with pytest.raises(SchemaMismatchError):
        tasks.ensure_summary_field(project(fake), "Hours", "Total Hours", function="avg")


def test_ensure_count_creates_once_without_a_field(app: Any, fake: FakeSchema) -> None:
    tasks = app.table("Tasks")
    rel = project(fake)
    created = tasks.ensure_count_field(rel, "Task Count")
    assert (created.mode, created.summary_function) == ("summary", "COUNT")
    assert created.summary_target_field_id == 0
    again = tasks.ensure_count_field(rel, "task count")
    assert again.id == created.id
    assert fake.writes() == [
        (
            f"/tables/{CHILD}/relationship/7",
            {"summaryFields": [{"label": "Task Count", "accumulationType": "COUNT"}]},
        )
    ]


def test_count_is_its_own_method(app: Any, fake: FakeSchema) -> None:
    tasks = app.table("Tasks")
    with pytest.raises(InvalidValueError, match="ensure_count_field"):
        tasks.ensure_summary_field(project(fake), "Hours", "Count", function="count")
    tasks.ensure_summary_field(project(fake), "Hours", "Total Hours")
    with pytest.raises(SchemaMismatchError):
        tasks.ensure_count_field(project(fake), "Total Hours")
    assert len(fake.writes()) == 1


def test_ensure_summary_reports_one_quickbase_did_not_create(app: Any, fake: FakeSchema) -> None:
    fake.ignore_new_fields = True
    with pytest.raises(InvalidResponseError):
        app.table("Tasks").ensure_summary_field(project(fake), "Hours", "Total Hours")


def test_ensure_summary_sends_a_checked_filter(app: Any, fake: FakeSchema) -> None:
    tasks = app.table("Tasks")
    rel = project(fake)
    created = tasks.ensure_summary_field(rel, "Hours", "Open Hours", where="{9.EX.'Open'}")
    assert created.summary_query == "{9.EX.'Open'}"
    again = tasks.ensure_summary_field(rel, "Hours", "Open Hours", where=" {9.EX.'Open'} ")
    assert again.id == created.id
    with pytest.raises(SchemaMismatchError):
        tasks.ensure_summary_field(rel, "Hours", "Open Hours")
    with pytest.raises(SchemaMismatchError):
        tasks.ensure_summary_field(rel, "Hours", "Open Hours", where="{9.EX.'Done'}")
    counted = tasks.ensure_count_field(rel, "Done Tasks", where=F("Status") == "Done")
    assert counted.summary_query == tasks.compile_where(F("Status") == "Done")
    sent = [body["summaryFields"][0] for _, body in fake.writes()]
    assert sent == [
        {"summaryFid": 6, "label": "Open Hours", "accumulationType": "SUM",
         "where": "{9.EX.'Open'}"},
        {"label": "Done Tasks", "accumulationType": "COUNT", "where": counted.summary_query},
    ]  # fmt: skip


@pytest.mark.parametrize("where", ["{nonsense", "{99.EX.1}", "{9.ex.'Open'}"])
def test_a_summary_filter_quickbase_would_save_broken_sends_nothing(
    app: Any, fake: FakeSchema, where: str
) -> None:
    # Quickbase accepts these with 200, and the summary is then always empty.
    with pytest.raises(InvalidQueryError):
        app.table("Tasks").ensure_summary_field(project(fake), "Hours", "X", where=where)
    assert not fake.writes()
