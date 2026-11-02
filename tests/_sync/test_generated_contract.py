# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Files generated earlier must keep working: users commit them and upgrade the SDK separately.

`tests/fixtures/generated_tables.py` is one of those files, so it's deliberately not regenerated.
From 1.0, a failure here means the change breaks users' generated files.
"""

import base64
import importlib.util
import json
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from pyquickbase import (
    UNSET,
    Column,
    FileUpload,
    Quickbase,
    Record,
    Row,
    SchemaMismatchError,
    TypedField,
    TypedRecord,
)
from pyquickbase._codegen import HEADER

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "generated_tables.py"
APP = "bq1234567"


def load() -> Any:
    spec = importlib.util.spec_from_file_location("frozen_generated_tables", FIXTURE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Tasks


Tasks = load()
LABELS = {
    1: ("Date Created", "timestamp"),
    2: ("Date Modified", "timestamp"),
    3: ("Record ID#", "recordid"),
    4: ("Record Owner", "user"),
    5: ("Last Modified By", "user"),
    6: ("Title", "text"),
    7: ("Due Date", "date"),
    8: ("Started", "timestamp"),
    9: ("Amount", "numeric"),
    10: ("Done", "checkbox"),
    11: ("Tags", "multitext"),
    12: ("Assignee", "user"),
    13: ("Attachment", "file"),
    14: ("Code", "text"),
    15: ("Total", "numeric"),
    16: ("Parent Name", "text"),
    17: ("Strange New Type", "something-new"),
    18: ("class", "text"),
    19: ("Due-Date", "date"),
}
MODES = {15: "formula", 16: "lookup"}
VALUES: dict[int, Any] = {
    1: "2026-01-01T00:00:00Z",
    2: "2026-01-02T00:00:00Z",
    3: 5,
    4: {"id": "1", "name": "Owner"},
    5: None,
    6: "Pour slab",
    7: "2027-01-15",
    8: "",
    9: 1.5,
    10: True,
    11: ["a"],
    12: None,
    13: {"url": "", "versions": []},
    14: "A-1",
    15: None,
    16: "",
    17: "anything",
    18: "x",
    19: "",
}


class Server:
    """Serves the fixture's table, and records every request."""

    def __init__(self) -> None:
        self.types = {id: field_type for id, (_, field_type) in LABELS.items()}
        self.bodies: dict[str, list[Any]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if request.content:
            self.bodies.setdefault(path, []).append(json.loads(request.content))
        if path == "/tables":
            return httpx.Response(200, json=[{"id": "bqtasks", "name": "Tasks"}])
        if path == "/fields":
            return httpx.Response(200, json=[self.field(id) for id in LABELS])
        if path == "/records/query":
            return httpx.Response(200, json=self.page())
        if request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": 1})
        metadata = {"createdRecordIds": [5], "updatedRecordIds": [], "unchangedRecordIds": []}
        return httpx.Response(200, json={"data": [], "metadata": metadata})

    def field(self, id: int) -> dict[str, Any]:
        label = LABELS[id][0]
        return {"id": id, "label": label, "fieldType": self.types[id], "mode": MODES.get(id, "")}

    def page(self) -> dict[str, Any]:
        return {
            "fields": [
                {"id": id, "label": label, "type": self.types[id]}
                for id, (label, _) in LABELS.items()
            ],
            "data": [{str(id): {"value": value} for id, value in VALUES.items()}],
            "metadata": {"totalRecords": 1, "skip": 0},
        }


def client(server: Server) -> Quickbase:
    http_client = httpx.Client(transport=httpx.MockTransport(server))
    return Quickbase(realm="acme", token="fake-user-token", http_client=http_client)


def test_the_file_can_still_be_replaced_by_codegen() -> None:
    assert FIXTURE.read_text().startswith(HEADER)


def test_the_class_keeps_its_shape() -> None:
    assert issubclass(Tasks, TypedRecord)
    assert Tasks.__table_id__ == "bqtasks"
    assert Tasks.__columns__[5] == Column("title", 6, "text", True, "Title")
    assert all(isinstance(column, Column) for column in Tasks.__columns__)
    assert isinstance(Tasks.fields.title, TypedField)
    assert Tasks.fields.title.id == 6
    assert Tasks.fields.title.desc == -6


def test_rows_are_built_by_field_id() -> None:
    row = Tasks.new(title="Pour slab", amount=None, code=UNSET)
    assert isinstance(row, Row)
    assert row == {6: "Pour slab", 9: None}
    assert Tasks.change(5, done=True) == {3: 5, 10: True}


def test_read_only_fields_stay_out_of_rows() -> None:
    with pytest.raises(TypeError):
        Tasks.new(total=1)
    with pytest.raises(TypeError):
        Tasks.change(5, parent_name="x")


def test_from_record_reads_every_column() -> None:
    labels = {label: id for id, (label, _) in LABELS.items()}
    task = Tasks.from_record(Record(VALUES, labels))
    assert (task.id, task.title, task.class_, task.due_date_19) == (5, "Pour slab", "x", "")
    assert repr(task) == "Tasks(id=5, fields=19)"
    with pytest.raises(FrozenInstanceError):
        task.title = "other"


def test_query_and_count() -> None:
    server = Server()
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        found = [
            task
            for task in tasks.query(
                where=(Tasks.fields.title == "Pour slab") & (Tasks.fields.amount > 1),
                order_by=[Tasks.fields.due_date.desc, Tasks.fields.title],
            )
        ]
        total = tasks.count(Tasks.fields.done == True)  # noqa: E712
    task = found[0]
    assert task.id == 5
    assert task.date_created == datetime(2026, 1, 1, tzinfo=UTC)
    assert task.due_date == date(2027, 1, 15)
    assert task.started is None
    assert task.strange_new_type == "anything"
    assert total == 1
    body = server.bodies["/records/query"][0]
    assert sorted(body["select"]) == list(LABELS)
    assert body["where"] == "{6.EX.'Pour slab'}AND{9.GT.'1'}"
    assert body["sortBy"][0] == {"fieldId": 7, "order": "DESC"}


def test_writes() -> None:
    server = Server()
    upload = FileUpload("a.txt", b"x")
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        tasks.insert([Tasks.new(title="a", due_date=date(2027, 1, 15), attachment=upload)])
        tasks.update([Tasks.change(5, title=None)])
        tasks.upsert([Tasks.new(code="A-1")], merge_on=Tasks.fields.code)
        assert tasks.delete(Tasks.fields.done == True) == 1  # noqa: E712
    inserted, updated, upserted, deleted = server.bodies["/records"]
    assert inserted["data"] == [
        {
            "6": {"value": "a"},
            "7": {"value": "2027-01-15"},
            "13": {
                "value": {"fileName": "a.txt", "data": base64.b64encode(b"x").decode()},
            },
        }
    ]
    assert updated["data"] == [{"3": {"value": 5}, "6": {"value": ""}}]
    assert updated["mergeFieldId"] == 3
    assert upserted["mergeFieldId"] == 14
    assert deleted["where"] == "{10.EX.'true'}"


def test_a_changed_table_is_still_reported() -> None:
    server = Server()
    server.types[9] = "text"
    with client(server) as qb:
        with pytest.raises(SchemaMismatchError, match="amount was numeric and is now text"):
            qb.app(APP).typed(Tasks).check_schema()
