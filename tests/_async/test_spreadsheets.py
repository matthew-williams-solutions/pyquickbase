"""Importing and exporting files through a client."""

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import openpyxl
import pytest

from pyquickbase import AsyncQuickbase, InvalidValueError, allow
from tests.policy_helpers import bind

pytestmark = pytest.mark.anyio

TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 7, "label": "Hours", "fieldType": "numeric"},
    {"id": 8, "label": "Due", "fieldType": "date"},
    {"id": 9, "label": "Double", "fieldType": "numeric", "mode": "formula"},
]
RESPONSE_FIELDS = [{"id": f["id"], "label": f["label"], "type": f["fieldType"]} for f in FIELDS]


class Fake:
    """Serves five records two at a time, and records every write."""

    def __init__(self) -> None:
        self.writes: list[Any] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": 3}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        body = json.loads(request.content)
        if path == "/records":
            self.writes.append(body)
            ids = list(range(10, 10 + len(body["data"])))
            meta = {"createdRecordIds": ids, "updatedRecordIds": [], "unchangedRecordIds": []}
            return httpx.Response(200, json={"metadata": meta})
        skip, top = body["options"]["skip"], body["options"]["top"]
        rows = [
            {"3": {"value": n}, "6": {"value": f"T{n}"}, "8": {"value": f"2027-01-0{n}"}}
            for n in range(1, 6)
        ][skip : skip + top]
        fields = [item for item in RESPONSE_FIELDS if item["id"] in (3, 6, 8)]
        meta = {"totalRecords": 5, "numRecords": len(rows), "skip": skip}
        return httpx.Response(200, json={"data": rows, "fields": fields, "metadata": meta})


def client(fake: Fake, **kwargs: Any) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, **kwargs)


async def test_a_file_is_read_for_the_tables_fields_and_written() -> None:
    fake = Fake()
    text = io.StringIO('Title,Hours,Due\nPour slab,"1,234.5",2027-01-15\nFrame walls,,\n')
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        rows = await tasks.rows_from_file(text)
        assert rows.ok
        result = await tasks.insert(rows)
    assert result.created_ids == (10, 11)
    assert fake.writes[0]["data"] == [
        {"6": {"value": "Pour slab"}, "7": {"value": "1234.5"}, "8": {"value": "2027-01-15"}},
        {"6": {"value": "Frame walls"}, "7": {"value": ""}, "8": {"value": ""}},
    ]


async def test_a_file_with_errors_writes_nothing() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        rows = await tasks.rows_from_file(io.StringIO("Title,Hours\nA,1\nB,lots\n"))
        assert [(error.row, error.column) for error in rows.errors] == [(3, "Hours")]
        with pytest.raises(InvalidValueError, match="Row 3, column 'Hours'"):
            await tasks.insert(rows)
        with pytest.raises(InvalidValueError, match="nothing was"):
            await tasks.preview_update(rows)
    assert fake.writes == []


async def test_a_hidden_field_is_not_a_column_a_file_can_use() -> None:
    policy = bind(allow.read(TASKS, ["Title"]), allow.insert(TASKS, ["Title"]))
    async with client(Fake(), policy=policy) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(InvalidValueError, match="aren't fields of the table"):
            await tasks.rows_from_file(io.StringIO("Title,Hours\nA,1\n"))


async def test_a_query_exports_every_page_to_csv(tmp_path: Path) -> None:
    path = tmp_path / "tasks.csv"
    async with client(Fake()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        written = await tasks.query(select=["Title", "Due"], page_size=2).to_csv(path)
    assert written == 5
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "Record ID#,Title,Due"
    assert lines[1:3] == ["1,T1,2027-01-01", "2,T2,2027-01-02"]
    assert len(lines) == 6


async def test_a_query_exports_to_excel(tmp_path: Path) -> None:
    path = tmp_path / "tasks.xlsx"
    async with client(Fake()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert await tasks.query(select=["Title", "Due"]).to_excel(path, sheet="Tasks") == 5
        rows = await tasks.rows_from_file(path, skip_read_only=True, sheet="Tasks")
    book = openpyxl.load_workbook(path)
    assert book["Tasks"]["C2"].value.date() == date(2027, 1, 1)
    assert rows.ok
    assert rows.rows[4] == {"Record ID#": 5, "Title": "T5", "Due": date(2027, 1, 5)}


async def test_an_export_of_nothing_still_has_a_header() -> None:
    class Empty(Fake):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/records/query"):
                fields = [item for item in RESPONSE_FIELDS if item["id"] in (3, 6)]
                meta = {"totalRecords": 0, "numRecords": 0, "skip": 0}
                return httpx.Response(200, json={"data": [], "fields": fields, "metadata": meta})
            return super().__call__(request)

    out = io.StringIO()
    async with client(Empty()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert await tasks.query(select=["Title"]).to_csv(out) == 0
    assert out.getvalue().strip() == "Record ID#,Title"
