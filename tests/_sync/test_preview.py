# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Previews through a client: they read what they need, and never write."""

import json
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx
import pytest

from pyquickbase import (
    Column,
    F,
    FileUpload,
    InvalidValueError,
    QueryError,
    Quickbase,
    ReadOnlyError,
    TypedRecord,
    UnknownFieldError,
    where_from_dict,
)

TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text", "appearsByDefault": True},
    {"id": 7, "label": "Status", "fieldType": "text", "appearsByDefault": True},
    {"id": 13, "label": "Attachment", "fieldType": "file"},
]
RESPONSE_FIELDS = [{"id": f["id"], "label": f["label"], "type": f["fieldType"]} for f in FIELDS]


class Fake:
    """Serves records, and records every query, and any write that gets through."""

    def __init__(self, records: dict[int, dict[str, Any]] | None = None) -> None:
        self.records = records or {}
        self.queries: list[dict[str, Any]] = []
        self.writes: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": 3}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        if path == "/records/query":
            body = json.loads(request.content)
            self.queries.append(body)
            top = body["options"]["top"]
            matched = [
                {
                    str(field_id): {"value": value}
                    for field_id, value in {3: record_id, **fields}.items()
                }
                for record_id, fields in self.records.items()
                if f"{{3.EX.'{record_id}'}}" in body["where"] or "{3." not in body["where"]
            ]
            return httpx.Response(
                200,
                json={
                    "data": matched[:top],
                    "fields": RESPONSE_FIELDS,
                    "metadata": {
                        "totalRecords": len(matched),
                        "numRecords": len(matched[:top]),
                        "skip": 0,
                        "numFields": 3,
                    },
                },
            )
        self.writes.append(f"{request.method} {path}")
        return httpx.Response(500)


def client(fake: Fake, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, **kwargs)


def test_an_update_preview_compares_without_writing() -> None:
    fake = Fake({1: {6: "old"}, 2: {6: "same"}})
    with client(fake, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = tasks.preview_update(
            [
                {"Record ID#": 1, "Title": "new"},
                {"Record ID#": 2, "Title": "same"},
                {"Record ID#": 3, "Title": "gone"},
            ]
        )
    assert [change.record_id for change in preview.changes] == [1]
    assert preview.changes[0].fields["Title"].before == "old"
    assert preview.changes[0].fields["Title"].after == "new"
    assert preview.unchanged_ids == (2,)
    assert preview.missing_ids == (3,)
    assert fake.writes == []
    assert fake.queries[0]["select"][0] == 3
    assert 6 in fake.queries[0]["select"]
    assert 7 not in fake.queries[0]["select"]


def test_many_records_are_read_in_batches() -> None:
    fake = Fake({n: {6: "same"} for n in range(1, 251)})
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        rows = [{"Record ID#": n, "Title": "same"} for n in range(1, 251)]
        preview = tasks.preview_update(rows)
    assert [query["options"]["top"] for query in fake.queries] == [100, 100, 50]
    assert len(preview.unchanged_ids) == 250


def test_an_upload_isnt_kept_in_the_preview() -> None:
    fake = Fake({1: {13: {"url": "", "versions": []}}})
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = tasks.preview_update(
            [{"Record ID#": 1, "Attachment": FileUpload("a.txt", b"s3cret")}]
        )
    change = preview.changes[0].fields["Attachment"]
    assert (change.before, change.after) == (None, "a.txt")


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([{"Title": "x"}], InvalidValueError),
        ([{"Record ID#": "1", "Title": "x"}], InvalidValueError),
        ([{}], InvalidValueError),
        ([{"Record ID#": 1, "Nope": "x"}], UnknownFieldError),
        ([{"Record ID#": 1, "Attachment": "x"}], InvalidValueError),
    ],
)
def test_bad_rows_raise_without_reading_records(rows: Any, error: Any) -> None:
    fake = Fake()
    with client(fake) as qb:
        with pytest.raises(error):
            qb.app("bq1234567").table("Tasks").preview_update(rows)
    assert fake.queries == []


def test_no_rows_is_an_empty_preview() -> None:
    fake = Fake()
    with client(fake) as qb:
        preview = qb.app("bq1234567").table("Tasks").preview_update([])
    assert preview.changes == ()
    assert fake.queries == []


def test_a_delete_preview_counts_every_match_without_deleting() -> None:
    fake = Fake({1: {6: "a"}, 2: {6: "b"}, 3: {6: "c"}})
    with client(fake, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        page = tasks.preview_delete(F("Status") == "Done", select=["Title"], top=2)
    assert page.total == 3
    assert len(page.records) == 2
    assert fake.queries[0]["where"] == "{7.EX.'Done'}"
    assert fake.writes == []


def test_a_delete_preview_takes_a_filter_from_data() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        tasks.preview_delete(where_from_dict({"field": "Status", "op": "==", "value": "x"}))
    assert fake.queries[0]["where"] == "{7.EX.'x'}"


@pytest.mark.parametrize("where", [None, "", "   ", "{7.ex.'x'}", "{99.EX.'x'}", 5])
def test_a_delete_preview_checks_where_like_a_delete(where: Any) -> None:
    fake = Fake()
    with client(fake) as qb:
        with pytest.raises(QueryError):
            qb.app("bq1234567").table("Tasks").preview_delete(where)
    assert fake.queries == []


def test_previews_work_where_the_write_would_be_refused() -> None:
    fake = Fake({1: {6: "old"}})
    with client(fake, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert (tasks.preview_update([{"Record ID#": 1, "Title": "new"}])).changes
        with pytest.raises(ReadOnlyError):
            tasks.update([{"Record ID#": 1, "Title": "new"}])


@dataclass(frozen=True, slots=True, repr=False)
class Task(TypedRecord):
    __table_id__: ClassVar[str] = TASKS
    __columns__: ClassVar[tuple[Column, ...]] = (
        Column("id", 3, "recordid", False, "Record ID#"),
        Column("title", 6, "text", True, "Title"),
    )
    id: int
    title: str

    @classmethod
    def change(cls, id: int, title: str) -> Any:
        return cls._row({"id": id, "title": title})


def test_typed_tables_can_preview() -> None:
    fake = Fake({1: {6: "old"}})
    with client(fake) as qb:
        tasks = qb.app("bq1234567").typed(Task)
        preview = tasks.preview_update([Task.change(1, "new")])
        assert preview.changes[0].fields["Title"].after == "new"
        page = tasks.preview_delete(F("Status") == "x", top=1)
    assert page.total == 1
    assert page.records[0] == Task(id=1, title="old")
    assert fake.writes == []


def test_a_typed_preview_refuses_a_row_for_another_table() -> None:
    class Other(Task):
        __table_id__: ClassVar[str] = "bqother"

    with client(Fake()) as qb:
        with pytest.raises(InvalidValueError, match="made for table bqother"):
            qb.app("bq1234567").typed(Task).preview_update([Other.change(1, "x")])
