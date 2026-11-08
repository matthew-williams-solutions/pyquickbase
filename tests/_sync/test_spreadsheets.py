# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Importing and exporting files through a client."""

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import openpyxl
import pytest

from pyquickbase import FileTooLargeError, InvalidValueError, Quickbase, allow
from tests.policy_helpers import bind

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


def client(fake: Fake, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, **kwargs)


def test_a_file_is_read_for_the_tables_fields_and_written() -> None:
    fake = Fake()
    text = io.StringIO('Title,Hours,Due\nPour slab,"1,234.5",2027-01-15\nFrame walls,,\n')
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        rows = tasks.rows_from_file(text)
        assert rows.ok
        result = tasks.insert(rows)
    assert result.created_ids == (10, 11)
    assert fake.writes[0]["data"] == [
        {"6": {"value": "Pour slab"}, "7": {"value": "1234.5"}, "8": {"value": "2027-01-15"}},
        {"6": {"value": "Frame walls"}, "7": {"value": ""}, "8": {"value": ""}},
    ]


def test_a_file_with_errors_writes_nothing() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        rows = tasks.rows_from_file(io.StringIO("Title,Hours\nA,1\nB,lots\n"))
        assert [(error.row, error.column) for error in rows.errors] == [(3, "Hours")]
        with pytest.raises(InvalidValueError, match="Row 3, column 'Hours'"):
            tasks.insert(rows)
        with pytest.raises(InvalidValueError, match="nothing was"):
            tasks.preview_update(rows)
    assert fake.writes == []


def test_a_hidden_field_is_not_a_column_a_file_can_use() -> None:
    policy = bind(allow.read(TASKS, ["Title"]), allow.insert(TASKS, ["Title"]))
    with client(Fake(), policy=policy) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(InvalidValueError, match="aren't fields of the table"):
            tasks.rows_from_file(io.StringIO("Title,Hours\nA,1\n"))


def test_a_query_exports_every_page_to_csv(tmp_path: Path) -> None:
    path = tmp_path / "tasks.csv"
    with client(Fake()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        written = tasks.query(select=["Title", "Due"], page_size=2).to_csv(path)
    assert written == 5
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "Record ID#,Title,Due"
    assert lines[1:3] == ["1,T1,2027-01-01", "2,T2,2027-01-02"]
    assert len(lines) == 6


def test_a_query_exports_to_excel(tmp_path: Path) -> None:
    path = tmp_path / "tasks.xlsx"
    with client(Fake()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert tasks.query(select=["Title", "Due"]).to_excel(path, sheet="Tasks") == 5
        rows = tasks.rows_from_file(path, skip_read_only=True, sheet="Tasks")
    book = openpyxl.load_workbook(path)
    assert book["Tasks"]["C2"].value.date() == date(2027, 1, 1)
    assert rows.ok
    assert rows.rows[4] == {"Record ID#": 5, "Title": "T5", "Due": date(2027, 1, 5)}


def test_an_export_of_nothing_still_has_a_header() -> None:
    class Empty(Fake):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/records/query"):
                fields = [item for item in RESPONSE_FIELDS if item["id"] in (3, 6)]
                meta = {"totalRecords": 0, "numRecords": 0, "skip": 0}
                return httpx.Response(200, json={"data": [], "fields": fields, "metadata": meta})
            return super().__call__(request)

    out = io.StringIO()
    with client(Empty()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert tasks.query(select=["Title"]).to_csv(out) == 0
    assert out.getvalue().strip() == "Record ID#,Title"


def test_a_file_over_max_rows_is_refused() -> None:
    with client(Fake()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        text = "Title\nA\nB\nC\n"
        assert len(tasks.rows_from_file(io.StringIO(text), max_rows=3)) == 3
        with pytest.raises(FileTooLargeError) as caught:
            tasks.rows_from_file(io.StringIO(text), max_rows=2)
    assert (caught.value.limit, caught.value.unit) == (2, "rows")


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_a_limit_that_makes_no_sense_is_refused(limit: Any) -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(InvalidValueError, match="max_rows"):
            tasks.rows_from_file(io.StringIO("Title\nA\n"), max_rows=limit)


def test_an_export_larger_than_a_batch_keeps_every_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pyquickbase._sync._resources.WRITE_BATCH", 2)
    path = tmp_path / "tasks.csv"
    with client(Fake()) as qb:
        written = qb.app("bq1234567").table("Tasks").query(select=["Title"]).to_csv(path)
    assert written == 5
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 6
