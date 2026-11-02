import json
import sys
import types
from datetime import date
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    QueryError,
    SchemaMismatchError,
    WriteError,
)
from pyquickbase._codegen import render
from pyquickbase._models import field_from_api, table_info_from_api

pytestmark = pytest.mark.anyio

APP = "bq1234567"
TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 7, "label": "Due Date", "fieldType": "date"},
    {"id": 8, "label": "Total", "fieldType": "numeric", "mode": "formula"},
]


def generated() -> Any:
    source = render(
        [(table_info_from_api({"id": TASKS, "name": "Tasks"}), [field_from_api(f) for f in FIELDS])]
    )
    module = types.ModuleType("generated_for_typed")
    sys.modules[module.__name__] = module
    exec(compile(source, module.__name__, "exec"), module.__dict__)
    return module.Tasks


Tasks = generated()


class Server:
    """Serves a table of `total` records, and records every request."""

    def __init__(self, total: int = 3) -> None:
        self.total = total
        self.fields = list(FIELDS)
        self.requests: list[httpx.Request] = []
        self.row_error = False

    def sent(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == f"/v1{path}"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks"}])
        if path == "/fields":
            return httpx.Response(200, json=self.fields)
        if path == "/records/query":
            return self.query(json.loads(request.content))
        if request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": 2})
        metadata: dict[str, Any] = {
            "createdRecordIds": [1],
            "unchangedRecordIds": [],
            "updatedRecordIds": [],
        }
        if self.row_error:
            metadata["lineErrors"] = {"1": ["Bad"]}
        return httpx.Response(200, json={"data": [], "metadata": metadata})

    def query(self, body: dict[str, Any]) -> httpx.Response:
        top, skip = body["options"]["top"], body["options"]["skip"]
        rows = range(skip + 1, min(skip + top, self.total) + 1)
        return httpx.Response(
            200,
            json={
                "fields": [
                    {"id": 3, "label": "Record ID#", "type": "recordid"},
                    {"id": 6, "label": "Title", "type": "text"},
                    {"id": 7, "label": "Due Date", "type": "date"},
                    {"id": 8, "label": "Total", "type": "numeric"},
                ],
                "data": [
                    {
                        "3": {"value": n},
                        "6": {"value": f"Item {n}"},
                        "7": {"value": "2027-01-02"},
                        "8": {"value": None},
                    }
                    for n in rows
                ],
                "metadata": {"totalRecords": self.total, "skip": skip},
            },
        )


def client(server: Any, **kwargs: Any) -> AsyncQuickbase:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return AsyncQuickbase(realm="acme", token="fake-user-token", http_client=http_client, **kwargs)


async def test_query_returns_instances_of_the_class() -> None:
    server = Server(total=3)
    async with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        assert repr(tasks) == "AsyncTypedTable(Tasks)"
        found = [
            task
            async for task in tasks.query(
                where=Tasks.fields.title == "Item",
                order_by=Tasks.fields.due_date.desc,
                page_size=2,
            )
        ]
    assert [task.title for task in found] == ["Item 1", "Item 2", "Item 3"]
    assert found[0] == Tasks(id=1, title="Item 1", due_date=date(2027, 1, 2), total=None)
    body = server.sent("/records/query")[0]
    assert body["select"] == [3, 6, 7, 8]
    assert body["where"] == "{6.EX.'Item'}"
    assert body["sortBy"][0] == {"fieldId": 7, "order": "DESC"}


async def test_query_stops_at_the_limit() -> None:
    async with client(Server(total=50)) as qb:
        found = [t async for t in qb.app(APP).typed(Tasks).query(limit=2)]
    assert len(found) == 2


async def test_query_checks_arguments_before_sending() -> None:
    server = Server()
    async with client(server) as qb:
        with pytest.raises(QueryError):
            qb.app(APP).typed(Tasks).query(limit=0)
    assert server.requests == []


async def test_insert_update_and_upsert_send_rows_by_id() -> None:
    server = Server()
    async with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        assert (await tasks.insert([Tasks.new(title="New")])).created_ids == (1,)
        await tasks.update([Tasks.change(7, title=None)])
        await tasks.upsert([Tasks.new(title="Up")], merge_on=Tasks.fields.title)
    insert, update, upsert = server.sent("/records")
    assert insert["data"] == [{"6": {"value": "New"}}]
    assert "mergeFieldId" not in insert
    assert update["data"] == [{"3": {"value": 7}, "6": {"value": ""}}]
    assert update["mergeFieldId"] == 3
    assert upsert["mergeFieldId"] == 6


async def test_write_errors_still_reach_the_caller() -> None:
    server = Server()
    server.row_error = True
    async with client(server) as qb:
        with pytest.raises(WriteError):
            await qb.app(APP).typed(Tasks).insert([Tasks.new(title="New")])


async def test_count_and_delete() -> None:
    server = Server(total=3)
    async with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        assert await tasks.count() == 3
        assert await tasks.count(Tasks.fields.title == "x") == 3
        assert await tasks.delete(Tasks.fields.title == "x") == 2
        assert await tasks.table.count() == 3


async def test_schema_is_checked_once_on_first_use() -> None:
    server = Server()
    async with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        await tasks.check_schema()
        await tasks.check_schema()
        await tasks.count()
    assert [r.url.path for r in server.requests].count("/v1/fields") == 1


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (FIELDS[:1] + FIELDS[2:], "title .field 6. no longer exists"),
        (
            [FIELDS[0], {**FIELDS[1], "fieldType": "numeric"}, FIELDS[2], FIELDS[3]],
            "title was text and is now numeric",
        ),
        ([*FIELDS[:3], {**FIELDS[3], "mode": ""}], "total is now writable"),
    ],
    ids=["deleted", "type", "writable"],
)
async def test_a_changed_table_refuses_every_use(fields: list[Any], message: str) -> None:
    server = Server()
    server.fields = fields
    async with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        with pytest.raises(SchemaMismatchError, match=message):
            await tasks.count()
        with pytest.raises(SchemaMismatchError, match=message):
            await tasks.insert([Tasks.new(title="x")])
        with pytest.raises(SchemaMismatchError, match=message):
            await tasks.update([Tasks.change(1, title="x")])
        with pytest.raises(SchemaMismatchError, match=message):
            await tasks.upsert([Tasks.new(title="x")], merge_on=Tasks.fields.title)
        with pytest.raises(SchemaMismatchError, match=message):
            await tasks.delete(Tasks.fields.title == "x")
        with pytest.raises(SchemaMismatchError, match=message):
            async for _ in tasks.query():
                pass  # pragma: no cover
    assert server.sent("/records") == []
    assert server.sent("/records/query") == []


async def test_an_old_cache_doesnt_cause_a_false_alarm() -> None:
    server = Server()
    server.fields = FIELDS[:1]
    async with client(server) as qb:
        await qb.app(APP).table("Tasks").fields()
        server.fields = list(FIELDS)
        await qb.app(APP).typed(Tasks).check_schema()


async def test_a_read_only_client_reads_but_cant_write() -> None:
    from pyquickbase import ReadOnlyError

    server = Server()
    async with client(server, read_only=True) as qb:
        tasks = qb.app(APP).typed(Tasks)
        assert len([t async for t in tasks.query()]) == 3
        with pytest.raises(ReadOnlyError):
            await tasks.insert([Tasks.new(title="x")])
