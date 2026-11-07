"""A policy's writes, used through a client, so every kind of write reaches the check."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    BoundPolicy,
    Decision,
    F,
    FileUpload,
    InvalidValueError,
    Policy,
    PolicyRefusedError,
    ReadRequest,
    WriteError,
    WriteRequest,
    allow,
    fields,
)

from ..policy_helpers import bind

pytestmark = pytest.mark.anyio

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


def client(fake: Fake, policy: Any, **kwargs: Any) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, policy=policy, **kwargs)


def rules(*extra: Any) -> BoundPolicy:
    """A policy that reads everything, as the write tests did without read rules."""
    return bind(allow.read(TASKS, max_paging_depth=None), *extra, tables={TASKS: ("Tasks", FIELDS)})


async def test_writes_the_rules_allow_are_sent() -> None:
    fake = Fake()
    policy = rules(
        allow.insert(TASKS, fields.all()),
        allow.update(TASKS, [7]),
        allow.delete(TASKS),
        allow.delete_file(TASKS),
    )
    async with client(fake, policy) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        await tasks.insert([{"Title": "x"}])
        await tasks.update([{"Record ID#": 1, "Status": "Done"}])
        await tasks.delete(F("Title") == "x")
        await tasks.delete_file_version(1, "Attachment", version=1)
    assert fake.writes == [
        "POST /records",
        "POST /records",
        "DELETE /records",
        "DELETE /files/bqtasks/1/13/1",
    ]


async def test_each_table_method_is_refused_without_being_sent() -> None:
    fake = Fake()
    async with client(fake, rules()) as qb:
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
            with pytest.raises(PolicyRefusedError):
                await call()
    assert fake.writes == []


async def test_an_upload_counts_as_setting_its_field() -> None:
    fake = Fake()
    upload = FileUpload("a.txt", b"s3cret")
    async with client(fake, rules(allow.update(TASKS, [7]))) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(PolicyRefusedError, match=r"Attachment \(13\)") as caught:
            await tasks.update([{"Record ID#": 1, "Attachment": upload}])
    assert fake.writes == []
    assert "s3cret" not in str(caught.value)


async def test_the_raw_escape_hatch_is_covered_too() -> None:
    fake = Fake()
    async with client(fake, rules(allow.insert(TASKS, [6]))) as qb:
        with pytest.raises(PolicyRefusedError):
            await qb.request("POST", "/records", json={"to": "bqother", "data": [{"6": {}}]})
        with pytest.raises(PolicyRefusedError):
            await qb.request("POST", "/records", json={"to": TASKS, "data": [{"07": {}}]})
        with pytest.raises(PolicyRefusedError):
            await qb.request("POST", "/records", json={"to": TASKS, "data": "x"})
        with pytest.raises(PolicyRefusedError):
            await qb.request("DELETE", "/tables/bqtasks", params={"appId": "bq1234567"})
        with pytest.raises(PolicyRefusedError, match="updating Title"):
            # request() doesn't know the table's key field, so this could change a record.
            await qb.request("POST", "/records", json={"to": TASKS, "data": [{"6": {}}]})
    assert fake.writes == []


async def test_a_raw_insert_needs_both_grants() -> None:
    fake = Fake()
    both = rules(allow.insert(TASKS, fields.all()), allow.update(TASKS, fields.all()))
    async with client(fake, both) as qb:
        await qb.request("POST", "/records", json={"to": TASKS, "data": [{"6": {}}]})
    assert fake.writes == ["POST /records"]


async def test_insert_is_refused_on_a_table_with_another_key_field() -> None:
    fake = Fake(key_field_id=8)
    both = rules(allow.insert(TASKS, fields.all()), allow.update(TASKS, fields.all()))
    async with client(fake, both) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(InvalidValueError, match=r"key field is 'Code'.*merge_on=8"):
            await tasks.insert([{"Code": "A"}])
        assert fake.writes == []
        await tasks.upsert([{"Code": "A"}], merge_on="Code")
        await tasks.update([{"Record ID#": 1, "Code": "A"}])
    assert fake.writes == ["POST /records", "POST /records"]


async def test_insert_names_a_key_field_it_cant_see() -> None:
    async with client(Fake(key_field_id=99), None) as qb:
        with pytest.raises(InvalidValueError, match="with ID 99"):
            await qb.app("bq1234567").table("Tasks").insert([{"Code": "A"}])


class Batches:
    """Allows one batch, and refuses the rest."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    def decide(self, request: ReadRequest | WriteRequest) -> Decision | None:
        if not isinstance(request, WriteRequest):
            return None
        self.seen.append(request.row_count or 0)
        if len(self.seen) < 2:
            return None
        return Decision(allowed=False, verb="insert", reason="One batch is enough")


async def test_reads_are_only_held_to_the_read_rules() -> None:
    fake = Fake()
    async with client(fake, Policy.read_only()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert await tasks.count() == 0
        await tasks.fields()
        await qb.request("POST", "/records/query", json={"from": TASKS, "select": [6]})
        await qb.request("POST", "/reports/1/run", params={"tableId": TASKS})
        await qb.request("POST", "/formula/run", json={"formula": "1", "from": TASKS})


async def test_a_later_batch_refused_reports_what_was_written() -> None:
    fake = Fake()
    batches = Batches()
    async with client(fake, rules(allow.insert(TASKS, fields.all()), batches)) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(WriteError, match="after earlier rows were written") as caught:
            await tasks.insert([{"Title": str(n)} for n in range(3)], batch_size=2)
    assert batches.seen == [2, 1]
    assert caught.value.result.created_ids == (1,)
    assert fake.writes == ["POST /records"]


async def test_a_delete_with_a_limit_counts_first_and_request_cant_delete() -> None:
    fake = Fake()
    async with client(fake, rules(allow.delete(TASKS, max_records_per_delete=3))) as qb:
        assert await qb.app("bq1234567").table("Tasks").delete(F("Title") == "x") == 1
        with pytest.raises(PolicyRefusedError, match="counts them first"):
            await qb.request("DELETE", "/records", json={"from": TASKS, "where": "{6.EX.'x'}"})
    assert fake.writes == ["DELETE /records"]


async def test_from_env_takes_a_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QB_REALM", "acme")
    monkeypatch.setenv("QB_USER_TOKEN", "t")
    fake = Fake()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    async with AsyncQuickbase.from_env(http_client=http, policy=rules()) as qb:
        with pytest.raises(PolicyRefusedError):
            await qb.request("POST", "/records", json=json.loads('{"to": "x", "data": []}'))
    assert fake.writes == []
