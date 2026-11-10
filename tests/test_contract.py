"""Decisions we've promised to keep through 1.x. A failure here needs a deliberate choice."""

import base64
import dataclasses
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

import pyquickbase
from pyquickbase import (
    AppInfo,
    ConfigurationError,
    Field,
    FileDownload,
    FileUpload,
    FormulaResult,
    InvalidResponseError,
    Page,
    Relationship,
    ReportInfo,
    RetryPolicy,
    RowError,
    TableInfo,
    WhereCondition,
    WriteResult,
)
from pyquickbase._config import DEFAULT_BASE_URL, validate_base_url
from pyquickbase._models import (
    field_from_api,
    file_download_from_response,
    parse,
    relationship_from_api,
    table_info_from_api,
)
from pyquickbase._operations import writable_fields
from pyquickbase._query import OPERATORS, build_query_body
from pyquickbase._writes import MAX_ROWS_BYTES, split_batches

ALL_TYPES = [
    "text", "text-multiple-choice", "text-multi-line", "rich-text", "email", "url", "phone",
    "address", "timeofday", "numeric", "currency", "rating", "percent", "duration", "date",
    "timestamp", "checkbox", "multitext", "user", "multiuser", "file", "dblink", "some-new-type",
]  # fmt: skip


def test_operators() -> None:
    assert {
        "EX", "XEX", "CT", "XCT", "SW", "XSW", "LT", "LTE", "GT", "GTE",
        "BF", "OBF", "AF", "OAF", "IR", "XIR", "HAS", "XHAS", "TV", "XTV",
    } == OPERATORS  # fmt: skip


def test_tools_write_only_confirmed_types() -> None:
    fields = [
        field_from_api({"id": 6 + index, "label": f"A {kind}", "fieldType": kind})
        for index, kind in enumerate([*ALL_TYPES, "predecessor"])
    ]
    assert {item.type for item in writable_fields(fields)} == {
        "text", "text-multiple-choice", "text-multi-line", "rich-text", "numeric", "currency",
        "rating", "percent", "multitext", "email", "url", "duration", "date", "timestamp",
        "timeofday", "checkbox", "phone", "file", "predecessor",
    }  # fmt: skip


def test_built_in_fields_end_at_five() -> None:
    def text(field_id: int) -> Field:
        return field_from_api({"id": field_id, "label": "A", "fieldType": "text"})

    assert [item.id for item in writable_fields([text(5), text(6)])] == [6]


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8000", "http://127.0.0.1", "http://[::1]:8000", "https://a.example"],
)
def test_addresses_allowed_as_a_base_url(url: str) -> None:
    assert validate_base_url(url + "/") == url


@pytest.mark.parametrize(
    "url", ["http://a.example", "http://127.0.0.2", "http://localhost.example", "ftp://localhost"]
)
def test_addresses_refused_as_a_base_url(url: str) -> None:
    with pytest.raises(ConfigurationError):
        validate_base_url(url)


def test_request_size_limit() -> None:
    assert MAX_ROWS_BYTES == 40 * 1024 * 1024 - 1024
    assert DEFAULT_BASE_URL == "https://api.quickbase.com/v1"


def test_batches_are_measured_in_bytes() -> None:
    # Each character takes two bytes, so these fit by characters but not by bytes.
    wide = "é" * (MAX_ROWS_BYTES // 3)
    rows = [{"6": {"value": wide}}, {"6": {"value": wide}}]
    assert list(split_batches(rows, 1000)) == [(0, 1), (1, 2)]


def test_a_new_batch_starts_with_an_empty_count() -> None:
    size = MAX_ROWS_BYTES // 2 - 100
    rows = [{"6": {"value": "x" * size}} for _ in range(5)]
    assert list(split_batches(rows, 1000)) == [(0, 2), (2, 4), (4, 5)]


METADATA: list[Any] = [
    AppInfo(
        id="a", name="n", description="", time_zone=None, date_format=None, raw={"s": "SECRET"}
    ),
    TableInfo(id="a", name="n", description="", key_field_id=None, raw={"s": "SECRET"}),
    Field(
        id=1,
        label="l",
        type="text",
        mode=None,
        required=False,
        unique=False,
        appears_by_default=False,
        choices=(),
        allow_new_choices=False,
        raw={"s": "SECRET"},
    ),
    Relationship(
        id=1,
        parent_table_id="p",
        child_table_id="c",
        foreign_key_field_id=None,
        lookup_field_ids=(),
        is_cross_app=False,
        raw={"s": "SECRET"},
    ),
    ReportInfo(
        id="1", name="n", type="table", description="", incomplete=False, raw={"s": "SECRET"}
    ),
]
VALUES: list[Any] = [
    *METADATA,
    Page(records=(), total=0, skip=0),
    FormulaResult(text="1", type="number"),
    FileDownload(file_name="a", data=b"SECRET", content_type=None),
    FileUpload("a", b"SECRET"),
    WriteResult(),
    RowError(index=0, messages=()),
    WhereCondition(1, "EX", "v"),
    RetryPolicy(),
]


@pytest.mark.parametrize("value", VALUES, ids=lambda v: type(v).__name__)
def test_public_values_cant_be_changed(value: Any) -> None:
    name = dataclasses.fields(value)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, name, "changed")
    assert not hasattr(value, "__dict__")


@pytest.mark.parametrize("value", METADATA, ids=lambda v: type(v).__name__)
def test_raw_stays_out_of_reprs_and_comparisons(value: Any) -> None:
    assert "SECRET" not in repr(value)
    assert value == dataclasses.replace(value, raw={"other": 1})


def test_keyword_only_where_promised() -> None:
    for kind in (AppInfo, TableInfo, Field, Relationship, ReportInfo, Page, FormulaResult):
        assert all(item.kw_only for item in dataclasses.fields(kind))
    for kind in (FileDownload, WriteResult, RowError, RetryPolicy):
        assert all(item.kw_only for item in dataclasses.fields(kind))
    assert not any(item.kw_only for item in dataclasses.fields(FileUpload))


def test_file_contents_stay_out_of_reprs() -> None:
    assert "SECRET" not in repr(FileUpload("a", b"SECRET"))
    assert "SECRET" not in repr(FileDownload(file_name="a", data=b"SECRET", content_type=None))
    assert "SECRET" not in repr(FormulaResult(text="SECRET", type="text"))


DEFAULTS = [
    ("Quickbase", "__init__", "timeout", 30.0),
    ("Quickbase", "__init__", "schema_ttl", 300.0),
    ("Quickbase", "__init__", "policy", None),
    ("Quickbase", "__init__", "base_url", "https://api.quickbase.com/v1"),
    ("Table", "query", "page_size", 1000),
    ("Table", "query_page", "limit", 100),
    ("Table", "query_page", "skip", 0),
    ("Table", "insert", "batch_size", 1000),
    ("Table", "update", "batch_size", 1000),
    ("Table", "upsert", "batch_size", 1000),
    ("Report", "run", "page_size", 1000),
    ("Report", "run_page", "limit", 100),
    ("Report", "run_page", "skip", 0),
]


@pytest.mark.parametrize(("owner", "method", "name", "value"), DEFAULTS)
def test_defaults(owner: str, method: str, name: str, value: Any) -> None:
    for prefix in ("", "Async"):
        function = getattr(getattr(pyquickbase, prefix + owner), method)
        assert inspect.signature(function).parameters[name].default == value


def test_retry_defaults() -> None:
    policy = RetryPolicy()
    assert (policy.max_retries, policy.base_delay, policy.max_delay, policy.max_retry_after) == (
        4,
        1.0,
        30.0,
        60.0,
    )


def test_retry_settings_can_be_zero_but_not_negative() -> None:
    RetryPolicy(max_retries=0, base_delay=0, max_delay=0, max_retry_after=0)
    for setting in ("max_retries", "base_delay", "max_delay", "max_retry_after"):
        with pytest.raises(ConfigurationError):
            RetryPolicy(**{setting: -1})


def test_metadata_settings_are_read() -> None:
    field = field_from_api(
        {
            "id": 6,
            "label": "A",
            "fieldType": "text",
            "required": True,
            "unique": True,
            "appearsByDefault": True,
            "mode": "lookup",
            "properties": {"choices": ["a"], "allowNewChoices": True, "formula": "1"},
        }
    )
    assert field.required
    assert field.unique
    assert field.appears_by_default
    assert field.mode == "lookup"
    assert field.choices == ("a",)
    assert field.allow_new_choices
    assert field.formula == "1"
    link = relationship_from_api(
        {"id": 1, "parentTableId": "p", "childTableId": "c", "isCrossApp": True}
    )
    assert link.is_cross_app
    assert table_info_from_api({"id": "t", "name": "n", "description": "d"}).description == "d"


def test_download_contents_must_be_strict_base64() -> None:
    headers = {"content-disposition": "attachment; filename*=utf-8''a.txt"}
    good = SimpleNamespace(headers=headers, content=base64.b64encode(b"x"))
    assert file_download_from_response(good).data == b"x"
    bad = SimpleNamespace(headers=headers, content=b"eA==!")
    with pytest.raises(InvalidResponseError):
        parse(file_download_from_response, bad, "file")


def test_negative_field_ids_sort_descending() -> None:
    fields = [
        field_from_api({"id": 3, "label": "Record ID#", "fieldType": "recordid"}),
        field_from_api({"id": 6, "label": "A", "fieldType": "text"}),
    ]

    def first_sort(ref: int) -> dict[str, Any]:
        return build_query_body("t", fields, where=None, select=None, order_by=ref)["sortBy"][0]

    assert first_sort(-6) == {"fieldId": 6, "order": "DESC"}
    assert first_sort(6) == {"fieldId": 6, "order": "ASC"}


def test_listing_apps_never_widens_past_one_realm() -> None:
    import inspect

    from pyquickbase import Quickbase
    from pyquickbase._xml import GRANTED_APPS

    assert list(inspect.signature(Quickbase.apps).parameters) == ["self", "admin_only"]
    assert GRANTED_APPS.action == "API_GrantedDBs"


def test_a_policy_cant_allow_a_role_change() -> None:
    from pyquickbase import Policy
    from pyquickbase._write_request import describe_write

    everything = Policy.read_only().bind_offline()
    for method in ("POST", "PATCH", "DELETE"):
        request = describe_write(method, "/app/bq123/trustees", [{"id": "1"}])
        assert request.action == "change_role_assignments"
        assert not everything.decide(request).allowed
