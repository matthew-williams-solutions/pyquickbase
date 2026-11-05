# Generated from the async code by scripts/generate_sync.py. Do not edit.
import base64
import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    FileUpload,
    InvalidResponseError,
    InvalidValueError,
    NotFoundError,
    Quickbase,
    RateLimitError,
    RetryPolicy,
    UnknownFieldError,
    WriteError,
    WriteRefusedError,
    _writes,
)

APP = "bq1234567"
TASKS = "bqtasks"
FILE_PATH = f"/files/{TASKS}/7/8"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 8, "label": "Attachment", "fieldType": "file"},
]


class FakeFiles:
    """Serves a table with a file field, and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.file_response = httpx.Response(
            200,
            content=base64.b64encode(b"contents"),
            headers={
                "content-type": "application/pdf",
                "content-disposition": "attachment; filename*=utf-8''spec.pdf",
            },
        )
        self.fail_write = 0

    def sent(self, prefix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.startswith(f"/v1{prefix}")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks"}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        if path.startswith("/files/"):
            if request.method == "DELETE":
                return httpx.Response(200, json={"versionNumber": 2, "fileName": "spec.pdf"})
            return self.file_response
        if len(self.sent("/records")) == self.fail_write:
            return httpx.Response(413, json={"message": "request entity too large"})
        rows = json.loads(request.content)["data"]
        return httpx.Response(
            200,
            json={
                "data": [],
                "metadata": {
                    "createdRecordIds": list(range(1, len(rows) + 1)),
                    "totalNumberOfRecordsProcessed": len(rows),
                    "unchangedRecordIds": [],
                    "updatedRecordIds": [],
                },
            },
        )


def client(server: Any, **kwargs: Any) -> Quickbase:
    http_client = httpx.Client(transport=httpx.MockTransport(server))
    return Quickbase(realm="acme", token="fake-user-token", http_client=http_client, **kwargs)


def test_insert_uploads_a_file_as_base64() -> None:
    server = FakeFiles()
    with client(server) as qb:
        result = (
            qb.app(APP)
            .table("Tasks")
            .insert([{"Title": "Spec", "Attachment": FileUpload("spec.pdf", b"\x00\xff")}])
        )
    assert result.created_ids == (1,)
    row = json.loads(server.sent("/records")[0].content)["data"][0]
    assert row["8"]["value"] == {"fileName": "spec.pdf", "data": "AP8="}


def test_update_uploads_a_new_version() -> None:
    server = FakeFiles()
    with client(server) as qb:
        (
            qb.app(APP)
            .table("Tasks")
            .update([{"Record ID#": 7, "Attachment": FileUpload("spec.pdf", b"x")}])
        )
    assert json.loads(server.sent("/records")[0].content)["mergeFieldId"] == 3


@pytest.mark.parametrize(
    "value", [None, "", {"fileName": "a.txt", "data": "QQ=="}], ids=["none", "empty", "dict"]
)
def test_a_file_field_refuses_other_values_before_sending(value: Any) -> None:
    server = FakeFiles()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="file field"):
            qb.app(APP).table("Tasks").update([{"Record ID#": 7, "Attachment": value}])
    assert server.sent("/records") == []


def test_large_rows_are_split_across_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_writes, "MAX_ROWS_BYTES", 100)
    server = FakeFiles()
    rows = [{"Title": "x", "Attachment": FileUpload("a.txt", b"y" * 45)} for _ in range(3)]
    with client(server) as qb:
        result = qb.app(APP).table("Tasks").insert(rows)
    assert [len(json.loads(r.content)["data"]) for r in server.sent("/records")] == [1, 1, 1]
    assert len(result.created_ids) == 3


def test_a_rejected_later_batch_reports_earlier_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_writes, "MAX_ROWS_BYTES", 100)
    server = FakeFiles()
    server.fail_write = 2
    rows = [{"Attachment": FileUpload("a.txt", b"y" * 60)} for _ in range(3)]
    with client(server) as qb:
        with pytest.raises(WriteError, match="stopped at row 1") as caught:
            qb.app(APP).table("Tasks").insert(rows)
    assert caught.value.result.created_ids == (1,)


def test_download_latest_decodes_the_contents() -> None:
    server = FakeFiles()
    with client(server) as qb:
        file = qb.app(APP).table("Tasks").download_file(7, "Attachment")
    assert (file.name, file.data, file.content_type) == ("spec.pdf", b"contents", "application/pdf")
    assert server.sent("/files")[0].url.path == f"/v1{FILE_PATH}/0"


def test_download_a_version_by_field_id() -> None:
    server = FakeFiles()
    with client(server) as qb:
        qb.app(APP).table("Tasks").download_file(7, 8, version=2)
    assert server.sent("/files")[0].url.path == f"/v1{FILE_PATH}/2"
    assert "fake-user-token" in server.sent("/files")[0].headers["authorization"]


def test_download_reports_quickbase_errors() -> None:
    server = FakeFiles()
    server.file_response = httpx.Response(404, json={"message": "Item not found"})
    with client(server) as qb:
        with pytest.raises(NotFoundError, match="Item not found"):
            qb.app(APP).table("Tasks").download_file(7, "Attachment")


def test_download_retries_rate_limits() -> None:
    server = FakeFiles()
    good = server.file_response
    responses = [httpx.Response(429, headers={"retry-after": "0"}), good]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/files/"):
            return responses.pop(0)
        return server(request)

    with client(handler, retry=RetryPolicy(max_retries=1, base_delay=0)) as qb:
        file = qb.app(APP).table("Tasks").download_file(7, "Attachment")
    assert file.data == b"contents"


def test_download_stops_after_too_many_rate_limits() -> None:
    server = FakeFiles()
    server.file_response = httpx.Response(429, headers={"retry-after": "0"})
    with client(server, retry=RetryPolicy(max_retries=0)) as qb:
        with pytest.raises(RateLimitError):
            qb.app(APP).table("Tasks").download_file(7, "Attachment")


def test_download_reports_a_malformed_response() -> None:
    server = FakeFiles()
    server.file_response = httpx.Response(200, content=b"no headers")
    with client(server) as qb:
        with pytest.raises(InvalidResponseError, match="file"):
            qb.app(APP).table("Tasks").download_file(7, "Attachment")


def test_delete_file_version() -> None:
    server = FakeFiles()
    with client(server) as qb:
        assert qb.app(APP).table("Tasks").delete_file_version(7, "Attachment", version=2) is None
    request = server.sent("/files")[0]
    assert (request.method, request.url.path) == ("DELETE", f"/v1{FILE_PATH}/2")


@pytest.mark.parametrize("version", [0, -1, True, "2", None])
def test_delete_refuses_a_bad_version_before_sending(version: Any) -> None:
    server = FakeFiles()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="version"):
            qb.app(APP).table("Tasks").delete_file_version(7, "Attachment", version=version)
    assert server.requests == []


@pytest.mark.parametrize("version", [0, -1, True, "2"])
def test_download_refuses_a_bad_version_before_sending(version: Any) -> None:
    server = FakeFiles()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="version"):
            qb.app(APP).table("Tasks").download_file(7, "Attachment", version=version)
    assert server.requests == []


@pytest.mark.parametrize("record_id", [0, "7", "7/../8"])
def test_a_bad_record_id_is_refused_before_sending(record_id: Any) -> None:
    server = FakeFiles()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="record_id"):
            qb.app(APP).table("Tasks").download_file(record_id, "Attachment")
    assert server.requests == []


def test_other_fields_are_refused_before_sending() -> None:
    server = FakeFiles()
    with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        with pytest.raises(InvalidValueError, match="'Title' isn't a file"):
            table.download_file(7, "Title")
        with pytest.raises(InvalidValueError, match="'Title' isn't a file"):
            table.delete_file_version(7, "Title", version=1)
        with pytest.raises(UnknownFieldError):
            table.download_file(7, "Nope")
    assert server.sent("/files") == []


def test_read_only_client_downloads_but_cant_change_files() -> None:
    server = FakeFiles()
    with client(server, read_only=True) as qb:
        table = qb.app(APP).table("Tasks")
        assert (table.download_file(7, "Attachment")).data == b"contents"
        with pytest.raises(WriteRefusedError):
            table.delete_file_version(7, "Attachment", version=2)
        with pytest.raises(WriteRefusedError):
            table.insert([{"Attachment": FileUpload("a.txt", b"x")}])
    assert [r.method for r in server.sent("/files")] == ["GET"]
