import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    ConfigurationError,
    F,
    InvalidResponseError,
    QueryError,
    UnknownFieldError,
    UnknownTableError,
)

pytestmark = pytest.mark.anyio

APP = "bq1234567"
TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text", "appearsByDefault": True},
    {"id": 7, "label": "Status", "fieldType": "text-multiple-choice", "appearsByDefault": True},
]


class FakeQuickbase:
    """Serves a two-table app with a fixed number of records, and records every request."""

    def __init__(self, total: int = 0, max_page: int | None = None) -> None:
        self.total = total
        self.max_page = max_page
        self.requests: list[httpx.Request] = []
        self.fields = FIELDS
        self.tables = [{"id": TASKS, "name": "Tasks"}, {"id": "bqprojects", "name": "Projects"}]
        self.relationships = [
            {"id": n, "parentTableId": "bqprojects", "childTableId": TASKS} for n in (1, 2, 3)
        ]

    def paths(self) -> list[str]:
        return [request.url.path.removeprefix("/v1") for request in self.requests]

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith("/query")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if path == f"/apps/{APP}":
            return httpx.Response(200, json={"id": APP, "name": "Sandbox"})
        if path == "/tables":
            return httpx.Response(200, json=self.tables)
        if path == "/fields":
            return httpx.Response(200, json=self.fields)
        if path.endswith("/relationships"):
            skip = int(request.url.params["skip"])
            return httpx.Response(
                200,
                json={
                    "relationships": self.relationships[skip : skip + 2],
                    "metadata": {"totalRelationships": len(self.relationships)},
                },
            )
        return self.query(json.loads(request.content))

    def query(self, body: dict[str, Any]) -> httpx.Response:
        top, skip = body["options"]["top"], body["options"]["skip"]
        if self.max_page:
            top = min(top, self.max_page)
        ids = range(skip + 1, min(skip + top, self.total) + 1)
        return httpx.Response(
            200,
            json={
                "fields": [{"id": 3, "label": "Record ID#", "type": "recordid"}],
                "data": [{"3": {"value": n}} for n in ids],
                "metadata": {"totalRecords": self.total, "skip": skip},
            },
        )


def client(server: Any, **kwargs: Any) -> AsyncQuickbase:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return AsyncQuickbase(realm="acme", token="fake-user-token", http_client=http_client, **kwargs)


async def test_app_info_and_tables_are_cached() -> None:
    server = FakeQuickbase()
    async with client(server) as qb:
        app = qb.app(APP)
        assert app.id == APP
        assert (await app.info()).name == "Sandbox"
        assert (await app.info()).name == "Sandbox"
        assert [table.name for table in await app.tables()] == ["Tasks", "Projects"]
        await app.tables()
    assert server.paths() == [f"/apps/{APP}", "/tables"]
    assert server.requests[1].url.params["appId"] == APP


async def test_schema_ttl_zero_disables_caching_and_clearing_forgets() -> None:
    server = FakeQuickbase()
    async with client(server, schema_ttl=0) as qb:
        await qb.app(APP).tables()
        await qb.app(APP).tables()
    assert len(server.requests) == 2

    server = FakeQuickbase()
    async with client(server) as qb:
        await qb.app(APP).tables()
        qb.clear_schema_cache()
        await qb.app(APP).tables()
    assert len(server.requests) == 2


@pytest.mark.parametrize("app_id", ["", "../tables", "a/b", "a b"])
async def test_app_id_must_be_plain(app_id: str) -> None:
    async with client(FakeQuickbase()) as qb:
        with pytest.raises(ConfigurationError):
            qb.app(app_id)


@pytest.mark.parametrize("ref", ["Tasks", TASKS])
async def test_table_found_by_name_or_id(ref: str) -> None:
    async with client(FakeQuickbase()) as qb:
        assert (await qb.app(APP).table(ref).info()).id == TASKS


async def test_table_outside_the_app_is_refused() -> None:
    server = FakeQuickbase()
    async with client(server) as qb:
        with pytest.raises(UnknownTableError, match="Did you mean 'Tasks'"):
            await qb.app(APP).table("Task").fields()
        with pytest.raises(UnknownTableError, match=r"no table 'bqother'\.$"):
            await qb.app(APP).table("bqother").count()
    assert "/fields" not in server.paths()
    assert "/records/query" not in server.paths()


async def test_new_table_is_found_after_refreshing_the_cache() -> None:
    server = FakeQuickbase()
    async with client(server) as qb:
        app = qb.app(APP)
        await app.tables()
        server.tables = [*server.tables, {"id": "bqnew", "name": "New"}]
        assert (await app.table("New").info()).id == "bqnew"


async def test_fields_and_relationships() -> None:
    server = FakeQuickbase()
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        assert [item.label for item in await table.fields()] == ["Record ID#", "Title", "Status"]
        await table.fields()
        assert [item.id for item in await table.relationships()] == [1, 2, 3]
        assert [item.id for item in await table.relationships()] == [1, 2, 3]
    assert server.paths().count("/fields") == 1
    assert server.paths().count(f"/tables/{TASKS}/relationships") == 2  # 3 rows, 2 per page


async def test_relationships_stop_on_an_empty_page() -> None:
    server = FakeQuickbase()
    server.relationships = []
    async with client(server) as qb:
        assert await qb.app(APP).table("Tasks").relationships() == []


async def test_query_yields_every_page_and_sends_labels_as_ids() -> None:
    server = FakeQuickbase(total=5)
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        records = [
            record.id
            async for record in table.query(
                where=F("Status") == "O'Brien", select=["Title"], order_by="-Title", page_size=2
            )
        ]
    assert records == [1, 2, 3, 4, 5]
    bodies = server.bodies()
    assert [body["options"] for body in bodies] == [
        {"top": 2, "skip": 0},
        {"top": 2, "skip": 2},
        {"top": 2, "skip": 4},
    ]
    assert bodies[0]["from"] == TASKS
    assert bodies[0]["where"] == "{7.EX.'O\\'Brien'}"
    assert bodies[0]["select"] == [3, 6]
    assert bodies[0]["sortBy"] == [{"fieldId": 6, "order": "DESC"}, {"fieldId": 3, "order": "ASC"}]


async def test_query_handles_pages_shorter_than_requested() -> None:
    server = FakeQuickbase(total=5, max_page=2)
    async with client(server) as qb:
        records = [r.id async for r in qb.app(APP).table("Tasks").query(page_size=1000)]
    assert records == [1, 2, 3, 4, 5]
    assert [body["options"]["skip"] for body in server.bodies()] == [0, 2, 4]


async def test_query_limit() -> None:
    server = FakeQuickbase(total=5)
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        assert [r.id async for r in table.query(max_total=3, page_size=2)] == [1, 2, 3]
    assert [body["options"]["top"] for body in server.bodies()] == [2, 1]


async def test_query_stops_early_without_fetching_more() -> None:
    server = FakeQuickbase(total=5)
    async with client(server) as qb:
        async for record in qb.app(APP).table("Tasks").query(page_size=2):
            assert record.id == 1
            break
    assert len(server.bodies()) == 1


async def test_query_with_no_matches() -> None:
    async with client(FakeQuickbase(total=0)) as qb:
        assert [r async for r in qb.app(APP).table("Tasks").query()] == []


async def test_query_refuses_a_stuck_response_instead_of_truncating() -> None:
    server = FakeQuickbase(total=5)
    server.query = lambda body: httpx.Response(  # type: ignore[method-assign]
        200, json={"fields": [], "data": [], "metadata": {"totalRecords": 5, "skip": 0}}
    )
    async with client(server) as qb:
        with pytest.raises(InvalidResponseError, match="empty page"):
            await qb.app(APP).table("Tasks").query().__anext__()


async def test_query_page() -> None:
    server = FakeQuickbase(total=5)
    async with client(server) as qb:
        page = await qb.app(APP).table("Tasks").query_page(limit=2, skip=2)
    assert [record.id for record in page.records] == [3, 4]
    assert (page.total, page.has_more, page.next_skip) == (5, True, 4)


async def test_count_fetches_only_one_record() -> None:
    server = FakeQuickbase(total=5)
    async with client(server) as qb:
        assert await qb.app(APP).table("Tasks").count(F("Title").contains("x")) == 5
    body = server.bodies()[0]
    assert body["select"] == [3]
    assert body["options"]["top"] == 1
    assert body["where"] == "{6.CT.'x'}"


@pytest.mark.parametrize(
    "arguments",
    [{"limit": 0}, {"limit": -1}, {"skip": -1}],
)
async def test_query_page_rejects_bad_paging_without_sending(arguments: dict[str, int]) -> None:
    server = FakeQuickbase(total=5)
    async with client(server) as qb:
        with pytest.raises(QueryError):
            await qb.app(APP).table("Tasks").query_page(**arguments)
    assert server.requests == []


@pytest.mark.parametrize("arguments", [{"page_size": 0}, {"max_total": 0}])
async def test_query_rejects_bad_paging_when_called(arguments: dict[str, int]) -> None:
    server = FakeQuickbase(total=5)
    async with client(server) as qb:
        with pytest.raises(QueryError):
            qb.app(APP).table("Tasks").query(**arguments)
    assert server.requests == []


async def test_bad_queries_are_never_sent() -> None:
    server = FakeQuickbase(total=5)
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        with pytest.raises(QueryError, match="uppercase"):
            await table.count("{6.ex.'x'}")
        with pytest.raises(UnknownFieldError):
            await table.count(F("Nope") == "x")
    assert server.bodies() == []


async def test_new_field_is_found_after_refreshing_the_cache() -> None:
    server = FakeQuickbase(total=1)
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        await table.fields()
        server.fields = [*FIELDS, {"id": 8, "label": "New", "fieldType": "text"}]
        assert await table.count(F("New") == "x") == 1
    assert server.bodies()[0]["where"] == "{8.EX.'x'}"


async def test_unexpected_response_shape_raises_a_quickbase_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    async with client(handler) as qb:
        with pytest.raises(InvalidResponseError, match="unexpected tables"):
            await qb.app(APP).tables()
        with pytest.raises(InvalidResponseError, match="unexpected app"):
            await qb.app(APP).info()


async def test_reprs() -> None:
    async with client(FakeQuickbase()) as qb:
        app = qb.app(APP)
        assert APP in repr(app)
        assert "Tasks" in repr(app.table("Tasks"))
