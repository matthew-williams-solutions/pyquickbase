"""Exactly what each method sends. The live tests show Quickbase accepts these."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from pyquickbase import AsyncQuickbase, F, FileUpload

pytestmark = pytest.mark.anyio

APP = "bq1234567"
TABLE = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 7, "label": "Attachment", "fieldType": "file"},
    {"id": 8, "label": "New", "fieldType": "text"},
]
REPORT = {"id": "1", "name": "All", "type": "table"}
FIELD = {"id": 8, "label": "New", "fieldType": "text"}
PAGE = {
    "fields": [{"id": 3, "label": "Record ID#", "type": "recordid"}],
    "data": [],
    "metadata": {"totalRecords": 0, "skip": 0},
}
WRITTEN = {
    "metadata": {
        "createdRecordIds": [1],
        "updatedRecordIds": [],
        "unchangedRecordIds": [],
    }
}


class Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if request.method == "GET":
            return self.get(path)
        if path == "/tables":
            return httpx.Response(200, json={"id": TABLE, "name": "Tasks"})
        if path == "/formula/run":
            return httpx.Response(200, json={"result": "1", "resultType": "number"})
        if path == "/records/query" or path.endswith("/run"):
            return httpx.Response(200, json=PAGE)
        if path == "/records":
            body = {"numberDeleted": 1} if request.method == "DELETE" else WRITTEN
            return httpx.Response(200, json=body)
        if path.startswith("/fields") and request.method == "POST":
            return httpx.Response(200, json=FIELD)
        if path.endswith("/relationship"):
            link = {"id": 1, "parentTableId": TABLE, "childTableId": TABLE}
            return httpx.Response(200, json=link)
        return httpx.Response(200, json={})

    def get(self, path: str) -> httpx.Response:
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TABLE, "name": "Tasks"}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        if path == "/reports":
            return httpx.Response(200, json=[REPORT])
        if path.startswith("/apps/"):
            return httpx.Response(200, json={"id": APP, "name": "App"})
        if path.endswith("/relationships"):
            metadata = {"totalRelationships": 0}
            return httpx.Response(200, json={"relationships": [], "metadata": metadata})
        disposition = {"content-disposition": "attachment; filename*=utf-8''a.txt"}
        return httpx.Response(200, content=b"eA==", headers=disposition)

    def last(self) -> tuple[str, str, dict[str, str], Any]:
        request = self.requests[-1]
        body = json.loads(request.content) if request.content else None
        return (
            request.method,
            request.url.path.removeprefix("/v1"),
            dict(request.url.params),
            body,
        )

    def fetched(self, path: str) -> int:
        return sum(1 for r in self.requests if r.method == "GET" and r.url.path == f"/v1{path}")


def client(server: Recorder, **kwargs: Any) -> AsyncQuickbase:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return AsyncQuickbase(realm="acme", token="fake-user-token", http_client=http_client, **kwargs)


APP_CALLS: list[tuple[str, Callable[[Any], Any], tuple[str, str, dict[str, str], Any]]] = [
    ("app-info", lambda app: app.info(), ("GET", f"/apps/{APP}", {}, None)),
    ("tables", lambda app: app.tables(), ("GET", "/tables", {"appId": APP}, None)),
    (
        "create-table",
        lambda app: app.create_table(
            "T", description="D", single_record_name="S", plural_record_name="P"
        ),
        (
            "POST",
            "/tables",
            {"appId": APP},
            {"name": "T", "description": "D", "singleRecordName": "S", "pluralRecordName": "P"},
        ),
    ),
]
TABLE_CALLS: list[tuple[str, Callable[[Any], Any], tuple[str, str, dict[str, str], Any]]] = [
    ("fields", lambda t: t.fields(refresh=True), ("GET", "/fields", {"tableId": TABLE}, None)),
    (
        "relationships",
        lambda t: t.relationships(),
        ("GET", f"/tables/{TABLE}/relationships", {"skip": "0"}, None),
    ),
    ("reports", lambda t: t.reports(), ("GET", "/reports", {"tableId": TABLE}, None)),
    (
        "query-page",
        lambda t: t.query_page(
            where=F("Title") == "a", select=["Title"], order_by="-Title", top=5, skip=2
        ),
        (
            "POST",
            "/records/query",
            {},
            {
                "from": TABLE,
                "select": [3, 6],
                "where": "{6.EX.'a'}",
                "sortBy": [{"fieldId": 6, "order": "DESC"}, {"fieldId": 3, "order": "ASC"}],
                "options": {"top": 5, "skip": 2},
            },
        ),
    ),
    (
        "count",
        lambda t: t.count(),
        (
            "POST",
            "/records/query",
            {},
            {
                "from": TABLE,
                "select": [3],
                "sortBy": [{"fieldId": 3, "order": "ASC"}],
                "options": {"top": 1, "skip": 0},
            },
        ),
    ),
    (
        "insert",
        lambda t: t.insert([{"Title": "a"}]),
        ("POST", "/records", {}, {"to": TABLE, "data": [{"6": {"value": "a"}}]}),
    ),
    (
        "update",
        lambda t: t.update([{"Record ID#": 1, "Title": None}]),
        (
            "POST",
            "/records",
            {},
            {
                "to": TABLE,
                "data": [{"3": {"value": 1}, "6": {"value": ""}}],
                "mergeFieldId": 3,
            },
        ),
    ),
    (
        "upsert",
        lambda t: t.upsert([{"Title": "a"}], merge_on="Title"),
        (
            "POST",
            "/records",
            {},
            {"to": TABLE, "data": [{"6": {"value": "a"}}], "mergeFieldId": 6},
        ),
    ),
    (
        "upload",
        lambda t: t.update([{"Record ID#": 1, "Attachment": FileUpload("a.txt", b"x")}]),
        (
            "POST",
            "/records",
            {},
            {
                "to": TABLE,
                "data": [
                    {"3": {"value": 1}, "7": {"value": {"fileName": "a.txt", "data": "eA=="}}}
                ],
                "mergeFieldId": 3,
            },
        ),
    ),
    (
        "delete",
        lambda t: t.delete(F("Title") == "a"),
        ("DELETE", "/records", {}, {"from": TABLE, "where": "{6.EX.'a'}"}),
    ),
    (
        "formula",
        lambda t: t.run_formula("1 + 1", record_id=2),
        ("POST", "/formula/run", {}, {"formula": "1 + 1", "from": TABLE, "rid": 2}),
    ),
    (
        "download-latest",
        lambda t: t.download_file(1, "Attachment"),
        ("GET", f"/files/{TABLE}/1/7/0", {}, None),
    ),
    (
        "download-version",
        lambda t: t.download_file(1, 7, version=2),
        ("GET", f"/files/{TABLE}/1/7/2", {}, None),
    ),
    (
        "delete-version",
        lambda t: t.delete_file_version(1, "Attachment", version=2),
        ("DELETE", f"/files/{TABLE}/1/7/2", {}, None),
    ),
    (
        "create-field",
        lambda t: t.create_field("New", "text", choices=["a"], properties={"maxLength": 5}),
        (
            "POST",
            "/fields",
            {"tableId": TABLE},
            {
                "label": "New",
                "fieldType": "text",
                "properties": {"maxLength": 5, "choices": ["a"]},
            },
        ),
    ),
    (
        "create-formula-field",
        lambda t: t.create_field("New", "numeric", formula="1 + 1"),
        (
            "POST",
            "/fields",
            {"tableId": TABLE},
            {"label": "New", "fieldType": "numeric", "properties": {"formula": "1 + 1"}},
        ),
    ),
    (
        "create-field-settings",
        lambda t: t.create_field("New", "text", required=True, unique=False),
        ("POST", "/fields/8", {"tableId": TABLE}, {"required": True, "unique": False}),
    ),
    (
        "update-field",
        lambda t: t.update_field(
            "Title", label="L", required=True, unique=False, properties={"maxLength": 5}
        ),
        (
            "POST",
            "/fields/6",
            {"tableId": TABLE},
            {"label": "L", "required": True, "unique": False, "properties": {"maxLength": 5}},
        ),
    ),
    (
        "delete-fields",
        lambda t: t.delete_fields(["Title", 7]),
        ("DELETE", "/fields", {"tableId": TABLE}, {"fieldIds": [6, 7]}),
    ),
    (
        "relationship",
        lambda t: t.create_relationship(t, foreign_key_label="P", lookup_fields=["Title"]),
        (
            "POST",
            f"/tables/{TABLE}/relationship",
            {},
            {"parentTableId": TABLE, "foreignKeyField": {"label": "P"}, "lookupFieldIds": [6]},
        ),
    ),
    (
        "report-page",
        lambda t: t.report("All").run_page(top=5, skip=2),
        ("POST", "/reports/1/run", {"tableId": TABLE, "top": "5", "skip": "2"}, None),
    ),
    (
        "drop",
        lambda t: t.drop(confirm="Tasks"),
        ("DELETE", f"/tables/{TABLE}", {"appId": APP}, None),
    ),
]


@pytest.mark.parametrize(
    ("call", "expected"), [c[1:] for c in APP_CALLS], ids=[c[0] for c in APP_CALLS]
)
async def test_app_requests(call: Callable[[Any], Any], expected: Any) -> None:
    server = Recorder()
    async with client(server) as qb:
        await call(qb.app(APP))
    assert server.last() == expected


@pytest.mark.parametrize(
    ("call", "expected"), [c[1:] for c in TABLE_CALLS], ids=[c[0] for c in TABLE_CALLS]
)
async def test_table_requests(call: Callable[[Any], Any], expected: Any) -> None:
    server = Recorder()
    async with client(server) as qb:
        await call(qb.app(APP).table("Tasks"))
    assert server.last() == expected


async def test_report_run_pages_by_query_parameters() -> None:
    server = Recorder()
    async with client(server) as qb:
        report = qb.app(APP).table("Tasks").report("1")
        assert [r async for r in report.run(page_size=7)] == []
    assert server.last() == (
        "POST",
        "/reports/1/run",
        {"tableId": TABLE, "top": "7", "skip": "0"},
        None,
    )


async def test_every_request_has_the_realm_and_token() -> None:
    server = Recorder()
    async with client(server) as qb:
        await qb.app(APP).table("Tasks").count()
    for request in server.requests:
        assert request.headers["QB-Realm-Hostname"] == "acme.quickbase.com"
        assert request.headers["Authorization"] == "QB-USER-TOKEN fake-user-token"
        assert request.url.host == "api.quickbase.com"
        assert request.url.scheme == "https"


async def test_changing_the_schema_forgets_what_was_cached() -> None:
    server = Recorder()
    async with client(server) as qb:
        app = qb.app(APP)
        table = app.table("Tasks")

        await app.tables()
        await app.create_table("New")
        before = server.fetched("/tables")
        await app.tables()
        assert server.fetched("/tables") == before + 1

        for change in (
            lambda: table.create_field("New", "text"),
            lambda: table.update_field("Title", label="L"),
            lambda: table.delete_fields("Title"),
            lambda: table.create_relationship(table, foreign_key_label="P"),
        ):
            await table.fields()
            await table.relationships()
            await change()
            fields = server.fetched("/fields")
            links = server.fetched(f"/tables/{TABLE}/relationships")
            await table.fields()
            await table.relationships()
            assert server.fetched("/fields") == fields + 1
            assert server.fetched(f"/tables/{TABLE}/relationships") == links + 1


async def test_cached_metadata_isnt_fetched_twice() -> None:
    server = Recorder()
    async with client(server) as qb:
        app = qb.app(APP)
        table = app.table("Tasks")
        for _ in range(2):
            await app.info()
            await table.fields()
            await table.relationships()
            await table.reports()
    assert server.fetched(f"/apps/{APP}") == 1
    assert server.fetched("/fields") == 1
    assert server.fetched(f"/tables/{TABLE}/relationships") == 1
    assert server.fetched("/reports") == 1


async def test_dropping_a_table_forgets_it() -> None:
    server = Recorder()
    async with client(server) as qb:
        app = qb.app(APP)
        table = app.table("Tasks")
        await table.reports()
        await table.drop(confirm="Tasks")
        before = server.fetched("/tables"), server.fetched("/fields"), server.fetched("/reports")
        await table.fields()
        await table.reports()
    after = server.fetched("/tables"), server.fetched("/fields"), server.fetched("/reports")
    assert after == (before[0] + 1, before[1] + 1, before[2] + 1)
