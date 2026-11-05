# Generated from the async code by scripts/generate_sync.py. Do not edit.
import json
from datetime import date
from typing import Any

import httpx
import pytest

from pyquickbase import (
    BadRequestError,
    F,
    InvalidResponseError,
    InvalidValueError,
    QueryError,
    Quickbase,
    ServerError,
    UnknownFieldError,
    UnknownTableError,
    WriteError,
    WriteRefusedError,
    tool_error,
)

APP = "bq1234567"
TASKS = "bqtasks"
PROJECTS = "bqprojects"


class FakeQuickbase:
    """Answers schema reads and scripted writes, and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.tables = [{"id": TASKS, "name": "Tasks"}, {"id": PROJECTS, "name": "Projects"}]
        self.fields = [
            {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
            {"id": 6, "label": "Title", "fieldType": "text"},
            {"id": 7, "label": "Code", "fieldType": "text", "unique": True},
        ]
        self.line_errors: dict[int, dict[str, list[str]]] = {}
        self.failures: dict[int, httpx.Response] = {}
        self.delete_field_errors: list[str] = []
        self.deleted_field_ids: list[int] | None = None
        self.field_update: httpx.Response | None = None
        self.writes = 0

    def sent(self, method: str, suffix: str) -> list[Any]:
        return [
            json.loads(r.content)
            for r in self.requests
            if r.method == method and r.url.path.endswith(suffix)
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        route = (request.method, path)
        if route == ("GET", "/tables"):
            return httpx.Response(200, json=self.tables)
        if route == ("GET", "/fields"):
            return httpx.Response(200, json=self.fields)
        if route == ("POST", "/records"):
            return self.write(json.loads(request.content))
        if route == ("DELETE", "/records"):
            return httpx.Response(200, json={"numberDeleted": 2})
        if route == ("POST", "/tables"):
            body = json.loads(request.content)
            table = {"id": "bqnew", "name": body["name"]}
            self.tables = [*self.tables, table]
            return httpx.Response(200, json=table)
        if route == ("POST", "/fields"):
            body = json.loads(request.content)
            field = {"id": 50, "label": body["label"], "fieldType": body["fieldType"]}
            self.fields = [*self.fields, field]
            return httpx.Response(200, json=field)
        if route == ("POST", "/fields/50") and self.field_update is not None:
            return self.field_update
        if route == ("POST", "/fields/50"):
            field = {"id": 50, "label": "New", "fieldType": "text", **json.loads(request.content)}
            return httpx.Response(200, json=field)
        if route == ("DELETE", "/fields"):
            ids = json.loads(request.content)["fieldIds"]
            deleted = ids if self.deleted_field_ids is None else self.deleted_field_ids
            body = {"deletedFieldIds": deleted, "errors": self.delete_field_errors}
            return httpx.Response(200, json=body)
        if route == ("POST", f"/tables/{TASKS}/relationship"):
            return httpx.Response(
                200,
                json={
                    "id": 12,
                    "parentTableId": PROJECTS,
                    "childTableId": TASKS,
                    "foreignKeyField": {"id": 12, "label": "Project", "type": "numeric"},
                    "lookupFields": [{"id": 13, "label": "Title", "type": "text"}],
                    "isCrossApp": False,
                },
            )
        if route == ("DELETE", f"/tables/{TASKS}"):
            self.tables = [t for t in self.tables if t["id"] != TASKS]
            return httpx.Response(200, json={"deletedTableId": TASKS})
        raise AssertionError(f"unexpected request: {route}")  # pragma: no cover

    def write(self, body: dict[str, Any]) -> httpx.Response:
        number = self.writes
        self.writes += 1
        if number in self.failures:
            return self.failures[number]
        errors = self.line_errors.get(number)
        count = len(body["data"]) - len(errors or {})
        ids = [number * 1000 + n for n in range(count)]
        metadata: dict[str, Any] = {
            "createdRecordIds": [] if "mergeFieldId" in body else ids,
            "updatedRecordIds": ids if "mergeFieldId" in body else [],
            "unchangedRecordIds": [],
            "totalNumberOfRecordsProcessed": len(body["data"]),
        }
        if errors:
            metadata["lineErrors"] = errors
        return httpx.Response(207 if errors else 200, json={"data": [], "metadata": metadata})


def client(server: FakeQuickbase, **kwargs: Any) -> Quickbase:
    http_client = httpx.Client(transport=httpx.MockTransport(server))
    return Quickbase(realm="acme", token="fake-user-token", http_client=http_client, **kwargs)


def test_insert_sends_labels_as_ids_and_converts_values() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        result = qb.app(APP).table("Tasks").insert([{"Title": "a", 7: date(2026, 1, 2)}])
    assert result.created_ids == (0,)
    assert server.sent("POST", "/records") == [
        {
            "to": TASKS,
            "data": [{"6": {"value": "a"}, "7": {"value": "2026-01-02"}}],
        }
    ]


def test_insert_accepts_a_generator_even_when_the_schema_refreshes() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        table.fields()
        server.fields = [*server.fields, {"id": 8, "label": "New", "fieldType": "text"}]
        table.insert(row for row in [{"New": "a"}, {"Title": "b"}])
    assert len(server.sent("POST", "/records")[0]["data"]) == 2


def test_insert_refuses_a_record_id_because_that_would_update() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match=r"insert\(\) can't do"):
            qb.app(APP).table("Tasks").insert([{"Record ID#": 1, "Title": "a"}])
    assert server.sent("POST", "/records") == []


def test_update_merges_on_record_id() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        result = qb.app(APP).table("Tasks").update([{"Record ID#": 5, "Title": None}])
    assert result.updated_ids == (0,)
    assert server.sent("POST", "/records")[0] == {
        "to": TASKS,
        "data": [{"3": {"value": 5}, "6": {"value": ""}}],
        "mergeFieldId": 3,
    }


def test_update_requires_a_record_id_on_every_row() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="Row 1 doesn't set field 3"):
            qb.app(APP).table("Tasks").update([{3: 5, "Title": "a"}, {"Title": "b"}])
    assert server.sent("POST", "/records") == []


def test_upsert_merges_on_the_given_field() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        qb.app(APP).table("Tasks").upsert([{"Code": "c", "Title": "a"}], merge_on="Code")
    assert server.sent("POST", "/records")[0]["mergeFieldId"] == 7


def test_upsert_requires_the_merge_field_on_every_row() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        with pytest.raises(InvalidValueError, match="Row 0 doesn't set field 7"):
            table.upsert([{"Title": "a"}], merge_on="Code")
        with pytest.raises(UnknownFieldError):
            table.upsert([{"Title": "a"}], merge_on="Nope")
    assert server.sent("POST", "/records") == []


def test_failed_rows_raise_with_the_full_result() -> None:
    server = FakeQuickbase()
    server.line_errors = {0: {"2": ['Incompatible value for field with ID "9".']}}
    with client(server) as qb:
        with pytest.raises(WriteError, match=r"1 row.* The first is row 1: Incompatible") as caught:
            qb.app(APP).table("Tasks").insert([{"Title": "a"}, {"Title": "b"}])
    assert caught.value.result.created_ids == (0,)
    assert caught.value.result.errors[0].index == 1


def test_large_writes_are_split_and_rows_keep_their_positions() -> None:
    server = FakeQuickbase()
    server.line_errors = {1: {"1": ["Bad."]}}
    rows = [{"Title": str(n)} for n in range(5)]
    with client(server) as qb:
        with pytest.raises(WriteError) as caught:
            qb.app(APP).table("Tasks").insert(rows, batch_size=2)
    assert [len(body["data"]) for body in server.sent("POST", "/records")] == [2, 2, 1]
    result = caught.value.result
    assert result.errors[0].index == 2  # first row of the second batch
    assert len(result.created_ids) == 4


def test_failure_in_a_later_batch_reports_what_was_already_written() -> None:
    server = FakeQuickbase()
    server.failures = {1: httpx.Response(500, json={"message": "Broken"})}
    rows = [{"Title": str(n)} for n in range(5)]
    with client(server) as qb:
        with pytest.raises(WriteError, match="stopped at row 2") as caught:
            qb.app(APP).table("Tasks").insert(rows, batch_size=2)
    assert caught.value.result.created_ids == (0, 1)
    assert isinstance(caught.value.__cause__, ServerError)
    assert server.writes == 2


def test_a_wrong_response_in_a_later_batch_reports_what_was_already_written() -> None:
    server = FakeQuickbase()
    server.failures = {1: httpx.Response(200, json={})}
    rows = [{"Title": str(n)} for n in range(5)]
    with client(server) as qb:
        with pytest.raises(WriteError, match="stopped at row 2") as caught:
            qb.app(APP).table("Tasks").insert(rows, batch_size=2)
    assert caught.value.result.created_ids == (0, 1)
    assert isinstance(caught.value.__cause__, InvalidResponseError)
    assert server.writes == 2


def test_a_wrong_response_in_the_first_batch_is_the_original_error() -> None:
    server = FakeQuickbase()
    server.failures = {0: httpx.Response(200, json={})}
    with client(server) as qb:
        with pytest.raises(InvalidResponseError):
            qb.app(APP).table("Tasks").insert([{"Title": "a"}])


def test_failure_in_the_first_batch_is_the_original_error() -> None:
    server = FakeQuickbase()
    server.failures = {0: httpx.Response(500, json={"message": "Broken"})}
    with client(server) as qb:
        with pytest.raises(ServerError):
            qb.app(APP).table("Tasks").insert([{"Title": "a"}])


def test_empty_writes_send_nothing() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        result = qb.app(APP).table("Tasks").insert([])
    assert result.created_ids == ()
    assert server.sent("POST", "/records") == []


@pytest.mark.parametrize("method", ["insert", "update"])
def test_batch_size_must_be_positive(method: str) -> None:
    with client(FakeQuickbase()) as qb:
        with pytest.raises(QueryError, match="batch_size"):
            getattr(qb.app(APP).table("Tasks"), method)([{"Title": "a"}], batch_size=0)


def test_upsert_batch_size_must_be_positive() -> None:
    with client(FakeQuickbase()) as qb:
        with pytest.raises(QueryError, match="batch_size"):
            qb.app(APP).table("Tasks").upsert([], merge_on="Code", batch_size=0)


def test_delete_checks_where_and_returns_the_count() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        assert table.delete(F("Title") == "a") == 2
        assert table.delete("{6.EX.'b'}") == 2
    assert [body["where"] for body in server.sent("DELETE", "/records")] == [
        "{6.EX.'a'}",
        "{6.EX.'b'}",
    ]


@pytest.mark.parametrize("where", ["", "  ", "{6.ex.'a'}", "garbage", "{99.EX.'a'}"])
def test_delete_never_sends_a_clause_it_cant_verify(where: str) -> None:
    """A lowercase operator acts like "contains" in the live API, so this protects real data."""
    server = FakeQuickbase()
    with client(server) as qb:
        with pytest.raises(QueryError):
            qb.app(APP).table("Tasks").delete(where)
    assert [r for r in server.requests if r.method == "DELETE"] == []


def test_read_only_client_refuses_every_write() -> None:
    server = FakeQuickbase()
    with client(server, read_only=True) as qb:
        app = qb.app(APP)
        table = app.table("Tasks")
        calls = [
            lambda: table.insert([{"Title": "a"}]),
            lambda: table.update([{3: 1, "Title": "a"}]),
            lambda: table.upsert([{"Code": "c"}], merge_on="Code"),
            lambda: table.delete(F("Title") == "a"),
            lambda: table.create_field("New", "text"),
            lambda: table.update_field("Title", label="Name"),
            lambda: table.delete_fields("Title"),
            lambda: table.create_relationship(app.table("Projects"), foreign_key_label="P"),
            lambda: table.drop(confirm="Tasks"),
            lambda: app.create_table("New"),
        ]
        for call in calls:
            with pytest.raises(WriteRefusedError):
                call()
    assert {r.method for r in server.requests} == {"GET"}


def test_create_table() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        app = qb.app(APP)
        app.tables()
        table = app.create_table("New", description="D", single_record_name="Item")
        assert (table.info()).name == "New"
    assert server.sent("POST", "/tables") == [
        {"name": "New", "description": "D", "singleRecordName": "Item"}
    ]
    assert server.requests[1].url.params["appId"] == APP


def test_create_field_with_choices_and_properties() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        table.fields()
        field = table.create_field("New", "text", choices=["A"], properties={"formula": "1"})
        assert field.id == 50
        assert [f.id for f in table.fields()][-1] == 50  # the cache was refreshed
    assert server.sent("POST", "/fields") == [
        {
            "label": "New",
            "fieldType": "text",
            "properties": {"formula": "1", "choices": ["A"]},
        }
    ]


def test_create_field_sets_required_and_unique_in_a_second_request() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        field = qb.app(APP).table("Tasks").create_field("New", "text", unique=True)
    assert field.unique is True
    assert server.sent("POST", "/fields") == [{"label": "New", "fieldType": "text"}]
    assert server.sent("POST", "/fields/50") == [{"unique": True}]


def test_update_field_needs_a_change() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="at least one"):
            qb.app(APP).table("Tasks").update_field("Title")


@pytest.mark.parametrize(("fields", "expected"), [("Title", [6]), (["Title", 7], [6, 7])])
def test_delete_fields(fields: Any, expected: list[int]) -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        qb.app(APP).table("Tasks").delete_fields(fields)
    assert server.sent("DELETE", "/fields") == [{"fieldIds": expected}]


def test_delete_fields_reports_partial_failure() -> None:
    server = FakeQuickbase()
    server.delete_field_errors = ["You can't delete a built-in field."]
    with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        with pytest.raises(WriteError, match="built-in"):
            table.delete_fields("Record ID#")
        with pytest.raises(InvalidValueError, match="at least one"):
            table.delete_fields([])


@pytest.mark.parametrize(
    ("deleted", "code", "changed"), [([6], "partial_write", "some"), ([], "write_failed", "none")]
)
def test_delete_fields_says_whether_any_were_deleted(
    deleted: list[int], code: str, changed: str
) -> None:
    server = FakeQuickbase()
    server.delete_field_errors = ["You can't delete a built-in field."]
    server.deleted_field_ids = deleted
    with client(server) as qb:
        with pytest.raises(WriteError, match=rf"fields \{deleted}\ were") as caught:
            qb.app(APP).table("Tasks").delete_fields(["Title", "Record ID#"])
    error = tool_error(caught.value)["error"]
    assert (error["code"], error["changed"]) == (code, changed)


def test_create_field_says_it_was_created_when_setting_unique_fails() -> None:
    server = FakeQuickbase()
    server.field_update = httpx.Response(400, json={"message": "field contains non-unique values"})
    with client(server) as qb:
        with pytest.raises(BadRequestError, match="Field 50 was created") as caught:
            qb.app(APP).table("Tasks").create_field("New", "text", unique=True)
    assert "non-unique" in str(caught.value)
    assert tool_error(caught.value)["error"]["changed"] == "some"


def test_row_ids_match_new_records_to_rows_when_some_fail() -> None:
    server = FakeQuickbase()
    server.line_errors = {0: {"2": ["bad"]}, 1: {"1": ["bad"]}}
    rows = [{"Title": name} for name in "abcd"]
    with client(server) as qb:
        with pytest.raises(WriteError) as caught:
            qb.app(APP).table("Tasks").insert(rows, batch_size=2)
    result = caught.value.result
    assert result.created_ids == (0, 1000)
    assert result.row_ids == (0, None, None, 1000)


def test_row_ids_stop_at_a_batch_that_failed() -> None:
    server = FakeQuickbase()
    server.failures = {1: httpx.Response(500, json={"message": "down"})}
    with client(server) as qb:
        with pytest.raises(WriteError) as caught:
            qb.app(APP).table("Tasks").insert([{"Title": n} for n in "abc"], batch_size=2)
    assert caught.value.result.row_ids == (0, 1)
    assert isinstance(caught.value.__cause__, ServerError)


def test_row_ids_are_empty_for_an_update() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        result = qb.app(APP).table("Tasks").update([{"Record ID#": 1, "Title": "a"}])
    assert result.row_ids == ()


def test_create_relationship() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        app = qb.app(APP)
        link = app.table("Tasks").create_relationship(
            app.table("Projects"), foreign_key_label="Project", lookup_fields=["Title"]
        )
    assert link.foreign_key_field_id == 12
    assert link.lookup_field_ids == (13,)
    assert server.sent("POST", f"/tables/{TASKS}/relationship") == [
        {
            "parentTableId": PROJECTS,
            "foreignKeyField": {"label": "Project"},
            "lookupFieldIds": [6],
        }
    ]


def test_drop_requires_the_exact_name() -> None:
    server = FakeQuickbase()
    with client(server) as qb:
        app = qb.app(APP)
        table = app.table(TASKS)
        with pytest.raises(InvalidValueError, match="exact name"):
            table.drop(confirm="tasks")
        assert not [r for r in server.requests if r.method == "DELETE"]
        table.drop(confirm="Tasks")
        with pytest.raises(UnknownTableError):
            table.info()
