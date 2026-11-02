"""Tests for preparing file uploads and reading downloads, which do no network access."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from pyquickbase import FileDownload, FileUpload, InvalidValueError
from pyquickbase._models import FieldIndex, field_from_api, file_download_from_response
from pyquickbase._writes import (
    MAX_ROWS_BYTES,
    build_rows,
    file_upload_to_api,
    require_id,
    split_batches,
)

INDEX = FieldIndex(
    [
        field_from_api({"id": 6, "label": "Title", "fieldType": "text"}),
        field_from_api({"id": 7, "label": "Attachment", "fieldType": "file"}),
    ]
)


def test_upload_is_base64_encoded_for_every_byte_value() -> None:
    data = bytes(range(256))
    built = build_rows([{"Attachment": FileUpload("a.bin", data)}], INDEX)
    value = built[0]["7"]["value"]
    assert value["fileName"] == "a.bin"
    assert file_download_from_response(_response(value["data"].encode())).data == data


def test_empty_file_is_allowed() -> None:
    assert file_upload_to_api(FileUpload("empty.txt", b"")) == {"fileName": "empty.txt", "data": ""}


@pytest.mark.parametrize(
    "name",
    ["", "../x.txt", "a/b.txt", "C:\\x\\y.txt", "nul\x00.txt", "a\nb.txt", "a\r\nb.txt", "del\x7f"],
)
def test_upload_refuses_names_that_could_hurt_whoever_downloads_them(name: str) -> None:
    with pytest.raises(InvalidValueError, match="file name"):
        FileUpload(name, b"x")


@pytest.mark.parametrize("name", ["résumé 日本語.pdf", "a b;c,d'e\".txt", ".hidden", "a\xa0b.txt"])
def test_upload_allows_ordinary_names(name: str) -> None:
    assert FileUpload(name, b"x").name == name


def test_upload_needs_bytes() -> None:
    with pytest.raises(InvalidValueError, match="bytes"):
        FileUpload("a.txt", "text")  # type: ignore[arg-type]


def test_contents_stay_out_of_reprs() -> None:
    assert repr(FileUpload("a.txt", b"secret")) == "FileUpload('a.txt', 6 bytes)"
    file = FileDownload(name="a.txt", data=b"secret", content_type="text/plain")
    assert repr(file) == "FileDownload('a.txt', 6 bytes)"


@pytest.mark.parametrize(
    "value",
    [None, "", "text", {"fileName": "a.txt", "data": "QQ=="}, {"fileName": "a.txt"}, b"bytes"],
    ids=["none", "empty", "string", "dict", "dict-without-data", "bytes"],
)
def test_file_field_takes_only_a_file_upload(value: Any) -> None:
    with pytest.raises(InvalidValueError, match="Row 0 sets file field 'Attachment'"):
        build_rows([{"Attachment": value}], INDEX)


def test_file_upload_refused_for_other_fields() -> None:
    with pytest.raises(InvalidValueError, match="Row 0 sends a file to 'Title'"):
        build_rows([{"Title": FileUpload("a.txt", b"x")}], INDEX)


def _response(content: bytes, disposition: str = "attachment; filename*=utf-8''a.bin") -> Any:
    headers = {"content-disposition": disposition, "content-type": "application/pdf"}
    return SimpleNamespace(content=content, headers=headers)


def test_download_name_is_decoded_without_being_cleaned() -> None:
    file = file_download_from_response(
        _response(b"", "attachment; filename*=utf-8''..%2Fr%C3%A9.txt")
    )
    assert file.name == "../ré.txt"
    assert file.data == b""
    assert file.content_type == "application/pdf"


@pytest.mark.parametrize(
    "response",
    [_response(b"not base64!"), _response(b"QQ==", 'attachment; filename="a.txt"')],
    ids=["bad-body", "no-name"],
)
def test_download_refuses_a_malformed_response(response: Any) -> None:
    with pytest.raises(ValueError, match=r"base64|file name"):
        file_download_from_response(response)


@pytest.mark.parametrize("value", [0, -1, True, "7", "1/../2", 1.5, None])
def test_require_id_refuses_anything_but_a_positive_integer(value: Any) -> None:
    with pytest.raises(InvalidValueError, match="positive integer"):
        require_id("record_id", value)


def test_require_id_allows_a_positive_integer() -> None:
    require_id("record_id", 1)


def _row(size: int) -> dict[str, Any]:
    """A row that takes exactly `size` bytes in a request, counting its separator."""
    empty = len(json.dumps({"6": ""}, separators=(",", ":"))) + 1
    return {"6": "x" * (size - empty)}


def test_batches_follow_the_row_count() -> None:
    rows = [{"6": "x"}] * 5
    assert list(split_batches(rows, 2)) == [(0, 2), (2, 4), (4, 5)]
    assert list(split_batches(rows, 5)) == [(0, 5)]
    assert list(split_batches([], 5)) == []


def test_batches_split_at_the_size_limit_exactly() -> None:
    half = MAX_ROWS_BYTES // 2
    fits = [_row(half), _row(MAX_ROWS_BYTES - half)]
    assert list(split_batches(fits, 1000)) == [(0, 2)]
    over = [_row(half), _row(MAX_ROWS_BYTES - half + 1)]
    assert list(split_batches(over, 1000)) == [(0, 1), (1, 2)]


def test_batches_count_encoded_bytes_not_characters() -> None:
    wide = {"6": "é" * (MAX_ROWS_BYTES // 2)}  # two bytes each in UTF-8
    assert list(split_batches([wide, {"6": "x"}], 1000)) == [(0, 1), (1, 2)]


def test_a_row_too_large_alone_still_gets_a_request() -> None:
    rows = [_row(10), _row(MAX_ROWS_BYTES + 1), _row(10)]
    assert list(split_batches(rows, 1000)) == [(0, 1), (1, 2), (2, 3)]
