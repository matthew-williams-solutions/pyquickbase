"""Tests for tool_result() and tool_error(), which do no network access."""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, ClassVar, get_args

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyquickbase import (
    APIError,
    AppInfo,
    AuthenticationError,
    BadRequestError,
    Column,
    ConfigurationError,
    Decision,
    Field,
    FieldChange,
    FileDownload,
    FormulaResult,
    InvalidResponseError,
    InvalidValueError,
    NetworkError,
    NotFoundError,
    Page,
    PermissionDeniedError,
    PolicyRefusedError,
    QueryError,
    QuickbaseError,
    RateLimitError,
    Record,
    RecordChange,
    ReportInfo,
    RequestTimeoutError,
    RowError,
    SchemaMismatchError,
    ServerError,
    SummaryRow,
    TableInfo,
    ToolErrorCode,
    TypedPage,
    TypedRecord,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    UnsupportedReportError,
    UpdatePreview,
    User,
    WriteError,
    WritePermissionError,
    WriteResult,
    tool_error,
    tool_result,
)
from pyquickbase._models import file_from_api

LABELS = {"Record ID#": 3, "Title": 6, "Due Date": 7, "Started": 8, "Owner": 9, "Spec": 10}
USER = {"id": "1.ab", "name": "Ann"}
FILE = {
    "url": "/files/x",
    "versions": [
        {"versionNumber": 1, "fileName": "old.txt", "uploaded": "2026-01-01T00:00:00Z"},
        {"versionNumber": 2, "fileName": "spec.txt", "uploaded": "2026-01-02T00:00:00Z"},
    ],
}
EMPTY_FILE: dict[str, Any] = {"url": "", "versions": []}


def record(**values: Any) -> Record:
    """A record from keyword arguments, where `Record_ID` means `Record ID#`."""
    labels = {"Record_ID": "Record ID#"}
    return Record(
        {LABELS[labels.get(key, key.replace("_", " "))]: value for key, value in values.items()},
        LABELS,
    )


def full(record_id: int = 1) -> Record:
    return Record(
        {
            3: record_id,
            6: "Pour slab",
            7: date(2027, 1, 15),
            8: datetime(2027, 1, 15, 9, 30, tzinfo=timezone(timedelta(hours=-5))),
            9: USER,
            10: FILE,
        },
        LABELS,
    )


def empty(record_id: int = 2) -> Record:
    return Record({3: record_id, 6: "", 7: None, 8: None, 9: None, 10: EMPTY_FILE}, LABELS)


def dumps(value: Any) -> str:
    return json.dumps(value, allow_nan=False)


# Results


def test_a_page_has_records_and_paging() -> None:
    found = tool_result(Page(records=(full(), empty()), total=10, skip=4))
    assert found["ok"] is True
    assert found["records"] == [
        {
            "Record ID#": 1,
            "Title": "Pour slab",
            "Due Date": "2027-01-15",
            "Started": "2027-01-15T14:30:00Z",
            "Owner": USER,
            "Spec": {"url": "/files/x", "versions": FILE["versions"]},
        },
        {
            "Record ID#": 2,
            "Title": None,
            "Due Date": None,
            "Started": None,
            "Owner": None,
            "Spec": None,
        },
    ]
    assert found["page"] == {
        "returned": 2,
        "total": 10,
        "skip": 4,
        "next_skip": 6,
        "has_more": True,
    }
    dumps(found)


def test_compact_sends_each_label_once_and_file_names() -> None:
    found = tool_result(Page(records=(full(), empty()), total=2, skip=0), compact=True)
    assert found["columns"] == ["Record ID#", "Title", "Due Date", "Started", "Owner", "Spec"]
    assert found["rows"] == [
        [1, "Pour slab", "2027-01-15", "2027-01-15T14:30:00Z", USER, "spec.txt"],
        [2, None, None, None, None, None],
    ]
    assert found["page"]["has_more"] is False
    assert "records" not in found


def test_compact_rows_line_up_when_records_hold_different_fields() -> None:
    found = tool_result(
        [record(Record_ID=1, Title="a"), record(Record_ID=2, Owner=USER)], compact=True
    )
    assert found["columns"] == ["Record ID#", "Title", "Owner"]
    assert found["rows"] == [[1, "a", None], [2, None, USER]]


def test_compact_is_smaller() -> None:
    page = Page(records=tuple(full(n) for n in range(1, 51)), total=50, skip=0)
    assert len(dumps(tool_result(page, compact=True))) < len(dumps(tool_result(page))) * 0.7


def test_max_records_cuts_and_says_so() -> None:
    found = tool_result(Page(records=(full(1), full(2), full(3)), total=3, skip=0), max_records=2)
    assert [item["Record ID#"] for item in found["records"]] == [1, 2]
    assert found["page"] == {
        "returned": 2,
        "total": 3,
        "skip": 0,
        "next_skip": 2,
        "has_more": True,
    }


def test_a_list_doesnt_know_its_total() -> None:
    found = tool_result([full(1), full(2)])
    assert found["page"] == {
        "returned": 2,
        "total": None,
        "skip": None,
        "next_skip": None,
        "has_more": False,
    }
    assert tool_result([full(1), full(2)], max_records=1)["page"]["has_more"] is True


def test_an_empty_list_is_no_records() -> None:
    found = tool_result([])
    assert found["records"] == []
    assert found["page"]["returned"] == 0


def test_dictionaries_count_as_records() -> None:
    found = tool_result([{"Hours (tot)": 12.0, "Status": "Open"}])
    assert found["records"] == [{"Hours (tot)": 12.0, "Status": "Open"}]


def test_one_record_or_none() -> None:
    assert tool_result(full())["record"]["Title"] == "Pour slab"
    assert tool_result(full(), compact=True)["record"]["Spec"] == "spec.txt"
    assert tool_result(None) == {"ok": True, "record": None}


def test_max_text_shortens_and_says_so() -> None:
    found = tool_result(record(Record_ID=1, Title="x" * 30), max_text=10)
    assert found["record"]["Title"] == "xxxxxxxxxx… (20 more characters)"
    assert found["text_shortened"] is True
    assert "text_shortened" not in tool_result(record(Record_ID=1, Title="short"), max_text=10)


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class Task(TypedRecord):
    __table_id__: ClassVar[str] = "bqtasks"
    __columns__: ClassVar[tuple[Column, ...]] = (
        Column("id", 3, "recordid", False, "Record ID#"),
        Column("title", 6, "text", True, "Title"),
        Column("price", 7, "currency", True, "Price", "decimal"),
    )
    id: int
    title: str | None
    price: Decimal | None


def test_generated_classes_use_attribute_names() -> None:
    task = Task(id=1, title="Pour slab", price=Decimal("12.50"))
    page = TypedPage(records=(task,), total=1, skip=0)
    assert tool_result(page)["records"] == [{"id": 1, "title": "Pour slab", "price": 12.5}]
    assert tool_result(task)["record"]["id"] == 1


def test_decimals_are_numbers_only_when_exact() -> None:
    assert tool_result(Decimal("12.50"))["value"] == 12.5
    assert tool_result(Decimal("0.1"))["value"] == 0.1
    assert tool_result(Decimal("12345678901234567890.123"))["value"] == "12345678901234567890.123"
    assert tool_result(Decimal("NaN"))["value"] is None


def test_plain_values() -> None:
    assert tool_result(7) == {"ok": True, "value": 7}
    assert tool_result(True) == {"ok": True, "value": True}
    assert tool_result(date(2027, 1, 2))["value"] == "2027-01-02"
    assert tool_result(time(13, 30))["value"] == "13:30:00"
    assert tool_result(datetime(2027, 1, 2, 3, tzinfo=UTC))["value"] == "2027-01-02T03:00:00Z"
    assert tool_result(float("inf"))["value"] is None
    assert tool_result("")["value"] is None


def test_every_kind_of_empty_value_is_null() -> None:
    values = {3: 1, 6: "", 7: [], 8: {}, 9: None, 10: EMPTY_FILE}
    found = tool_result(Record(values, LABELS))["record"]
    assert [found[label] for label in LABELS if label != "Record ID#"] == [None] * 5


def test_write_results() -> None:
    found = tool_result(WriteResult(created_ids=(4, 5), unchanged_ids=(1,)))
    assert found == {"ok": True, "created_ids": [4, 5], "updated_ids": [], "unchanged_ids": [1]}


def test_summaries() -> None:
    rows = [
        SummaryRow(group={"Status": "Open"}, count=2, total={"Hours": 20.0}),
        SummaryRow(group={"Status": None}, count=1, minimum={"Due": date(2027, 1, 1)}),
    ]
    assert tool_result(rows)["groups"] == [
        {"group": {"Status": "Open"}, "count": 2, "total": {"Hours": 20.0}},
        {"group": {"Status": None}, "count": 1, "minimum": {"Due": "2027-01-01"}},
    ]
    assert tool_result([SummaryRow(count=3)])["groups"] == [{"group": {}, "count": 3}]


def test_previews() -> None:
    change = RecordChange(record_id=1, fields={"Status": FieldChange("Open", "Done")})
    preview = UpdatePreview(changes=(change,), unchanged_ids=(2,), missing_ids=(9,))
    assert tool_result(preview) == {
        "ok": True,
        "changes": [{"record_id": 1, "fields": {"Status": {"before": "Open", "after": "Done"}}}],
        "unchanged_ids": [2],
        "missing_ids": [9],
    }


def test_formula_results() -> None:
    assert tool_result(FormulaResult(text="2", type="number")) == {
        "ok": True,
        "value": 2.0,
        "type": "number",
    }


@pytest.mark.parametrize(
    "value",
    [
        AppInfo(id="x", name="x", description="", time_zone=None, date_format=None, raw={}),
        {"a": 1},
        object(),
    ],
)
def test_other_values_are_refused(value: Any) -> None:
    with pytest.raises(TypeError, match="can't describe"):
        tool_result(value)


def test_a_value_of_another_type_inside_a_record_is_refused() -> None:
    with pytest.raises(TypeError, match="bytes"):
        tool_result(Record({3: 1, 6: b"x"}, LABELS))


@pytest.mark.parametrize("kwargs", [{"max_records": 0}, {"max_text": -1}, {"max_records": 1.5}])
def test_bad_limits(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="positive"):
        tool_result([], **kwargs)


def test_compact_must_be_a_bool() -> None:
    with pytest.raises(TypeError):
        tool_result([], compact="yes")  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats()
    | st.text()
    | st.dates()
    | st.datetimes(timezones=st.just(UTC))
    | st.decimals(),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(), inner, max_size=3),
    max_leaves=10,
)


@given(st.lists(json_values, max_size=6), st.booleans())
def test_results_always_dump_as_json(values: list[Any], compact: bool) -> None:
    labels = {f"F{index}": index + 6 for index in range(len(values))}
    item = Record({index + 6: value for index, value in enumerate(values)}, labels)
    dumps(tool_result(Page(records=(item,), total=1, skip=0), compact=compact, max_text=5))


# Errors


def written(**ids: Any) -> WriteResult:
    return WriteResult(**{key: tuple(value) for key, value in ids.items()})


@pytest.mark.parametrize(
    ("error", "code", "changed", "retryable"),
    [
        (ConfigurationError("x"), "configuration", "none", False),
        (QueryError("x"), "invalid_query", "none", False),
        (InvalidValueError("x"), "invalid_value", "none", False),
        (PolicyRefusedError(Decision(allowed=False, verb="read")), "read_refused", "none", False),
        (PolicyRefusedError(Decision(allowed=False, verb="filter")), "read_refused", "none", False),
        (
            PolicyRefusedError(Decision(allowed=False, verb="update")),
            "write_refused",
            "none",
            False,
        ),
        (
            PolicyRefusedError(Decision(allowed=False, verb="lookup")),
            "write_refused",
            "none",
            False,
        ),
        (UnknownTableError("x"), "unknown_table", "none", False),
        (UnknownFieldError("x"), "unknown_field", "none", False),
        (UnknownReportError("x"), "unknown_report", "none", False),
        (UnsupportedReportError("x"), "unsupported_report", "none", False),
        (SchemaMismatchError("x"), "schema_mismatch", "none", False),
        (AuthenticationError("x", status_code=401), "authentication", "none", False),
        (PermissionDeniedError("x", status_code=403), "permission_denied", "none", False),
        (NotFoundError("x", status_code=404), "not_found", "none", False),
        (BadRequestError("x", status_code=400), "bad_request", "none", False),
        (RateLimitError("x", status_code=429), "rate_limited", "none", True),
        (ServerError("x", status_code=503), "server_error", "unknown", True),
        (APIError("x", status_code=413), "api_error", "none", False),
        (RequestTimeoutError("x"), "timeout", "unknown", True),
        (NetworkError("x"), "network", "unknown", True),
        (InvalidResponseError("x"), "invalid_response", "unknown", False),
        (QuickbaseError("x"), "error", "none", False),
        (WriteError("x", result=written()), "write_failed", "none", False),
        (WritePermissionError("x", result=written()), "write_permission", "none", False),
        (KeyError("s3cret"), "internal", "unknown", False),
    ],
)
def test_each_error_has_a_code(
    error: BaseException, code: str, changed: str, retryable: bool
) -> None:
    found = tool_error(error)
    assert found["ok"] is False
    details = found["error"]
    assert (details["code"], details["changed"], details["retryable"]) == (code, changed, retryable)
    assert details["hint"]
    dumps(found)


def test_every_code_is_documented_and_has_a_hint() -> None:
    from pyquickbase._output import _HINTS  # pyright: ignore[reportPrivateUsage]

    assert set(_HINTS) == set(get_args(ToolErrorCode))


def test_other_exceptions_say_nothing_about_themselves() -> None:
    found = tool_error(ValueError("s3cret value"))["error"]
    assert "s3cret" not in dumps(found)
    assert found["message"] == "An unexpected error occurred in the tool."


def test_sdk_messages_are_kept() -> None:
    assert tool_error(QueryError("where.op isn't an operator."))["error"]["message"] == (
        "where.op isn't an operator."
    )


def test_api_errors_give_their_status_and_rate_limits_their_wait() -> None:
    assert tool_error(BadRequestError("x", status_code=400))["error"]["status"] == 400
    found = tool_error(RateLimitError("x", status_code=429, retry_after=5.0))["error"]
    assert found["retry_after"] == 5.0


def test_failed_rows_and_what_was_written() -> None:
    failure = RowError(index=1, messages=("Duplicate",), kind="duplicate", field_id=10)
    result = WriteResult(created_ids=(4,), errors=(failure,))
    found = tool_error(WriteError("1 row failed", result=result))["error"]
    assert found["code"] == "partial_write"
    assert found["changed"] == "some"
    assert found["written"] == {"created_ids": [4], "updated_ids": [], "unchanged_ids": []}
    assert found["failed_rows"] == [
        {"index": 1, "kind": "duplicate", "field_id": 10, "messages": ["Duplicate"]}
    ]


def test_every_row_failing_changed_nothing() -> None:
    failure = RowError(index=0, messages=("x",))
    found = tool_error(WriteError("x", result=WriteResult(errors=(failure,))))["error"]
    assert (found["code"], found["changed"]) == ("write_failed", "none")


def test_unchanged_records_arent_a_change() -> None:
    found = tool_error(WriteError("x", result=written(unchanged_ids=[1])))["error"]
    assert found["changed"] == "none"


def batch_failure(cause: BaseException, result: WriteResult) -> WriteError:
    try:
        raise WriteError("Writing stopped at row 1000", result=result) from cause
    except WriteError as error:
        return error


def test_a_later_batch_that_may_have_been_written_is_unknown() -> None:
    for cause in (RequestTimeoutError("x"), ServerError("x", status_code=502)):
        found = tool_error(batch_failure(cause, written(created_ids=[1])))["error"]
        assert (found["code"], found["changed"]) == ("partial_write", "unknown")


def test_a_later_batch_that_was_refused_leaves_earlier_ones() -> None:
    error = batch_failure(BadRequestError("x", status_code=400), written(created_ids=[1]))
    found = tool_error(error)["error"]
    assert (found["code"], found["changed"]) == ("partial_write", "some")


def test_a_permission_refusal_after_earlier_rows_were_written() -> None:
    error = WritePermissionError("x", result=written(updated_ids=[1, 2]))
    found = tool_error(error)["error"]
    assert (found["code"], found["changed"]) == ("write_permission", "some")


# Metadata and files


def field(**overrides: Any) -> Field:
    values: dict[str, Any] = {
        "id": 7,
        "label": "Status",
        "type": "text-multiple-choice",
        "mode": None,
        "required": True,
        "unique": False,
        "appears_by_default": True,
        "choices": ("Open", "Done"),
        "allow_new_choices": False,
        "raw": {"properties": {"formula": "s3cret"}},
    }
    return Field(**{**values, **overrides})


def test_metadata_leaves_out_raw() -> None:
    table = TableInfo(id="bqx", name="Tasks", description="", key_field_id=3, raw={"x": "s3cret"})
    assert tool_result(table) == {
        "ok": True,
        "table": {"id": "bqx", "name": "Tasks", "description": None},
    }
    assert tool_result([field(), field(id=8, label="Total", mode="formula", choices=())]) == {
        "ok": True,
        "fields": [
            {
                "id": 7,
                "label": "Status",
                "type": "text-multiple-choice",
                "required": True,
                "unique": False,
                "computed": False,
                "choices": ["Open", "Done"],
            },
            {
                "id": 8,
                "label": "Total",
                "type": "text-multiple-choice",
                "required": True,
                "unique": False,
                "computed": True,
                "mode": "formula",
            },
        ],
    }
    report = ReportInfo(
        id="7", name="Open", type="table", description="x", incomplete=False, raw={}
    )
    assert tool_result([report])["reports"] == [
        {"id": "7", "name": "Open", "type": "table", "description": "x"}
    ]
    assert "s3cret" not in dumps(tool_result(field()))


def test_a_text_file_has_its_text_and_a_binary_one_says_so() -> None:
    text = tool_result(FileDownload(name="a.txt", data="héllo".encode(), content_type="text/plain"))
    assert text["file"] == {
        "name": "a.txt",
        "content_type": "text/plain",
        "size": 6,
        "text": "héllo",
    }
    binary = tool_result(FileDownload(name="a.png", data=b"\x89PNG\x00", content_type=None))
    assert binary["file"]["text"] is None
    assert binary["file"]["binary"] is True
    long = tool_result(FileDownload(name="a.txt", data=b"x" * 30, content_type=None), max_text=10)
    assert long["text_shortened"] is True
    empty = tool_result(FileDownload(name="a.txt", data=b"", content_type=None))
    assert empty["file"]["text"] == ""


def test_users_and_files_are_written_with_fixed_keys() -> None:

    attachment = file_from_api(
        {
            "url": "/files/x",
            "versions": [
                {
                    "versionNumber": 1,
                    "fileName": "a.txt",
                    "uploaded": "2027-01-15T14:30:00Z",
                    "creator": {"id": "1.a", "name": "A", "email": "a@example.com"},
                },
                {"versionNumber": 2, "fileName": "b.txt", "uploaded": ""},
            ],
        }
    )
    owner = User(id="1.a", name="A")
    row = Record({3: 1, 6: owner, 7: attachment}, {"Record ID#": 3, "Owner": 6, "Spec": 7})
    assert tool_result([row])["records"] == [
        {
            "Record ID#": 1,
            "Owner": {"id": "1.a", "name": "A", "email": None},
            "Spec": {
                "url": "/files/x",
                "versions": [
                    {
                        "number": 1,
                        "file_name": "a.txt",
                        "uploaded": "2027-01-15T14:30:00Z",
                        "creator": {"id": "1.a", "name": "A", "email": "a@example.com"},
                    },
                    {"number": 2, "file_name": "b.txt", "uploaded": None, "creator": None},
                ],
            },
        }
    ]
    assert tool_result([row], compact=True)["rows"] == [
        [1, {"id": "1.a", "name": "A", "email": None}, "b.txt"]
    ]
