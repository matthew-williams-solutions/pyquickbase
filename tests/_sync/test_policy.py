# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Write policies, used through a client, so every kind of write reaches the check."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    F,
    FileUpload,
    InvalidValueError,
    Quickbase,
    WriteAccess,
    WriteError,
    WriteRefusedError,
    WriteRequest,
    WriteRules,
    all_of,
)

TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text", "appearsByDefault": True},
    {"id": 7, "label": "Status", "fieldType": "text", "appearsByDefault": True},
    {"id": 8, "label": "Code", "fieldType": "text", "unique": True},
    {"id": 13, "label": "Attachment", "fieldType": "file"},
]
EMPTY_PAGE = {
    "data": [],
    "fields": [],
    "metadata": {"totalRecords": 0, "numRecords": 0, "skip": 0, "numFields": 0},
}
WRITTEN = {
    "metadata": {
        "createdRecordIds": [1],
        "updatedRecordIds": [],
        "unchangedRecordIds": [],
        "totalNumberOfRecordsProcessed": 1,
    }
}


class Fake:
    """Records every write, so a test can check that a refused one never arrived."""

    def __init__(self, key_field_id: int = 3) -> None:
        self.key_field_id = key_field_id
        self.writes: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/tables" and request.method == "GET":
            return httpx.Response(
                200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": self.key_field_id}]
            )
        if path == "/fields" and request.method == "GET":
            return httpx.Response(200, json=FIELDS)
        if path == "/records/query":
            return httpx.Response(200, json=EMPTY_PAGE)
        if path == "/reports/1/run":
            return httpx.Response(200, json=EMPTY_PAGE)
        if path == "/formula/run":
            return httpx.Response(200, json={"result": 1})
        self.writes.append(f"{request.method} {path}")
        if path == "/records" and request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": 1})
        if path == "/records":
            return httpx.Response(200, json=WRITTEN)
        return httpx.Response(200, json={})


def client(fake: Fake, policy: Any, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, write_policy=policy, **kwargs)


def test_allowed_writes_are_sent() -> None:
    fake = Fake()
    rules = WriteRules(
        {TASKS: WriteAccess(insert=True, update=[7], delete=True, delete_files=True)}
    )
    with client(fake, rules) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        tasks.insert([{"Title": "x"}])
        tasks.update([{"Record ID#": 1, "Status": "Done"}])
        tasks.delete(F("Title") == "x")
        tasks.delete_file_version(1, "Attachment", version=1)
    assert fake.writes == [
        "POST /records",
        "POST /records",
        "DELETE /records",
        "DELETE /files/bqtasks/1/13/1",
    ]


def test_each_table_method_is_refused_without_being_sent() -> None:
    fake = Fake()
    with client(fake, WriteRules({TASKS: WriteAccess()})) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        calls: list[Any] = [
            lambda: tasks.insert([{"Title": "x"}]),
            lambda: tasks.update([{"Record ID#": 1, "Title": "x"}]),
            lambda: tasks.upsert([{"Code": "x"}], merge_on="Code"),
            lambda: tasks.delete(F("Title") == "x"),
            lambda: tasks.delete_file_version(1, "Attachment", version=1),
            lambda: tasks.create_field("New", "text"),
            lambda: tasks.update_field("Title", label="Renamed"),
            lambda: tasks.delete_fields("Title"),
            lambda: tasks.drop(confirm="Tasks"),
            lambda: qb.app("bq1234567").create_table("New"),
        ]
        for call in calls:
            with pytest.raises(WriteRefusedError):
                call()
    assert fake.writes == []


def test_an_upload_counts_as_setting_its_field() -> None:
    fake = Fake()
    upload = FileUpload("a.txt", b"s3cret")
    with client(fake, WriteRules({TASKS: WriteAccess(update=[7])})) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(WriteRefusedError, match=r"\[13\]") as caught:
            tasks.update([{"Record ID#": 1, "Attachment": upload}])
    assert fake.writes == []
    assert "s3cret" not in str(caught.value)


def test_the_raw_escape_hatch_is_covered_too() -> None:
    fake = Fake()
    with client(fake, WriteRules({TASKS: WriteAccess(insert=[6])})) as qb:
        with pytest.raises(WriteRefusedError):
            qb.request("POST", "/records", json={"to": "bqother", "data": [{"6": {}}]})
        with pytest.raises(WriteRefusedError):
            qb.request("POST", "/records", json={"to": TASKS, "data": [{"07": {}}]})
        with pytest.raises(WriteRefusedError):
            qb.request("POST", "/records", json={"to": TASKS, "data": "x"})
        with pytest.raises(WriteRefusedError):
            qb.request("DELETE", "/tables/bqtasks", params={"appId": "bq1234567"})
        with pytest.raises(WriteRefusedError, match="updating isn't allowed"):
            # request() doesn't know the table's key field, so this could change a record.
            qb.request("POST", "/records", json={"to": TASKS, "data": [{"6": {}}]})
    assert fake.writes == []


def test_a_raw_insert_needs_both_grants() -> None:
    fake = Fake()
    with client(fake, WriteRules({TASKS: WriteAccess(insert=True, update=True)})) as qb:
        qb.request("POST", "/records", json={"to": TASKS, "data": [{"6": {}}]})
    assert fake.writes == ["POST /records"]


def test_insert_is_refused_on_a_table_with_another_key_field() -> None:
    fake = Fake(key_field_id=8)
    rules = WriteRules({TASKS: WriteAccess(insert=True, update=True)})
    with client(fake, rules) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(InvalidValueError, match=r"key field is 'Code'.*merge_on=8"):
            tasks.insert([{"Code": "A"}])
        assert fake.writes == []
        tasks.upsert([{"Code": "A"}], merge_on="Code")
        tasks.update([{"Record ID#": 1, "Code": "A"}])
    assert fake.writes == ["POST /records", "POST /records"]


def test_insert_names_a_key_field_it_cant_see() -> None:
    with client(Fake(key_field_id=99), None) as qb:
        with pytest.raises(InvalidValueError, match="with ID 99"):
            qb.app("bq1234567").table("Tasks").insert([{"Code": "A"}])


def test_reads_arent_affected() -> None:
    def no(_: WriteRequest) -> bool:
        raise AssertionError("A read reached the policy.")

    fake = Fake()
    with client(fake, no) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert tasks.count() == 0
        tasks.fields()
        qb.request("POST", "/records/query", json={"from": TASKS, "select": [6]})
        qb.request("POST", "/reports/1/run", params={"tableId": TASKS})
        qb.request("POST", "/formula/run", json={"formula": "1", "from": TASKS})


def test_it_works_with_allowed_writes_and_both_must_pass() -> None:
    fake = Fake()
    rules = WriteRules({TASKS: WriteAccess(insert=True, delete=True)})
    with client(fake, rules, allowed_writes={("POST", "/records")}) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        tasks.insert([{"Title": "x"}])
        with pytest.raises(WriteRefusedError):  # The rules allow it, and allowed_writes doesn't.
            tasks.delete(F("Title") == "x")
        with pytest.raises(WriteRefusedError):  # allowed_writes allows it, and the rules don't.
            qb.request("POST", "/records", json={"to": "bqother", "data": [{"6": {}}]})
    assert fake.writes == ["POST /records"]


def test_a_later_batch_refused_reports_what_was_written() -> None:
    fake = Fake()
    seen: list[int] = []

    def two_requests(request: WriteRequest) -> bool:
        seen.append(request.row_count or 0)
        return len(seen) < 2

    with client(fake, two_requests) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(WriteError, match="after earlier rows were written") as caught:
            tasks.insert([{"Title": str(n)} for n in range(3)], batch_size=2)
    assert seen == [2, 1]
    assert caught.value.result.created_ids == (1,)
    assert fake.writes == ["POST /records"]


def test_a_custom_policy_sees_the_batch_and_can_combine() -> None:
    fake = Fake()

    def small(request: WriteRequest) -> bool:
        return request.row_count is not None and request.row_count <= 1

    policy = all_of(WriteRules({TASKS: WriteAccess(insert=True)}), small)
    with client(fake, policy) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        tasks.insert([{"Title": "x"}])
        with pytest.raises(WriteRefusedError, match="refused this insert"):
            tasks.insert([{"Title": "x"}, {"Title": "y"}])
    assert fake.writes == ["POST /records"]


def test_from_env_takes_a_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QB_REALM", "acme")
    monkeypatch.setenv("QB_USER_TOKEN", "t")
    fake = Fake()
    http = httpx.Client(transport=httpx.MockTransport(fake))
    rules = WriteRules({})
    with Quickbase.from_env(http_client=http, write_policy=rules) as qb:
        with pytest.raises(WriteRefusedError):
            qb.request("POST", "/records", json=json.loads('{"to": "x", "data": []}'))
    assert fake.writes == []
