"""Tests for preparing writes and reading their results, which do no network access."""

import pickle
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from pyquickbase import (
    InvalidValueError,
    RowError,
    UnknownFieldError,
    WriteError,
    WriteResult,
)
from pyquickbase._models import FieldIndex, field_from_api
from pyquickbase._schema import SchemaCache
from pyquickbase._writes import (
    build_rows,
    merge_write_result,
    refuse_blank_rows,
    refuse_field,
    require_field,
    to_api_value,
    write_result_summary,
)

INDEX = FieldIndex(
    [
        field_from_api({"id": 3, "label": "Record ID#", "fieldType": "recordid"}),
        field_from_api({"id": 6, "label": "Title", "fieldType": "text"}),
        field_from_api({"id": 9, "label": "Due", "fieldType": "date"}),
    ]
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),  # Quickbase silently ignores null, and "" is what clears a field
        (True, True),
        (5, 5),
        (1.5, 1.5),
        ("x", "x"),
        (Decimal("1.50"), "1.50"),
        (date(2026, 1, 2), "2026-01-02"),
        (datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "2026-01-02T03:04:05+00:00"),
        (
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=2))),
            "2026-01-02T03:04:05+02:00",
        ),
        (["a", "b"], ["a", "b"]),
        ({"id": "1.abc"}, {"id": "1.abc"}),
    ],
)
def test_to_api_value(value: Any, expected: Any) -> None:
    assert to_api_value(value) == expected


def test_a_datetime_is_written_with_the_precision_quickbase_accepts() -> None:
    whole = datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert to_api_value(whole) == "2027-01-02T03:04:05+00:00"
    assert to_api_value(whole.replace(microsecond=123456)) == "2027-01-02T03:04:05.123+00:00"
    assert to_api_value(whole.replace(microsecond=500)) == "2027-01-02T03:04:05.000+00:00"


def test_to_api_value_rejects_ambiguous_and_unknown_values() -> None:
    with pytest.raises(InvalidValueError, match="timezone"):
        to_api_value(datetime(2026, 1, 2))
    with pytest.raises(InvalidValueError, match="type object"):
        to_api_value(object())


def test_build_rows_accepts_labels_and_ids() -> None:
    assert build_rows([{"Title": "a", 9: date(2026, 1, 2)}], INDEX) == [
        {"6": {"value": "a"}, "9": {"value": "2026-01-02"}}
    ]


def test_build_rows_rejects_mistakes() -> None:
    with pytest.raises(UnknownFieldError, match="Did you mean 'Title'"):
        build_rows([{"Titel": "a"}], INDEX)
    with pytest.raises(InvalidValueError, match="Row 1 has no fields"):
        build_rows([{"Title": "a"}, {}], INDEX)
    with pytest.raises(InvalidValueError, match="Row 0 sets field 6 more than once"):
        build_rows([{"Title": "a", 6: "b"}], INDEX)


def test_required_and_refused_fields() -> None:
    rows = [{"3": {"value": 1}}, {"6": {"value": "a"}}]
    require_field(rows[:1], 3, "x")
    refuse_field(rows[1:], 3, "x")
    with pytest.raises(InvalidValueError, match="Row 1 doesn't set field 3, because"):
        require_field(rows, 3, "because")
    with pytest.raises(InvalidValueError, match="Row 0 sets field 3, because"):
        refuse_field(rows, 3, "because")


def test_result_collects_batches_and_numbers_rows_from_zero() -> None:
    first = merge_write_result(
        WriteResult(),
        {
            "metadata": {
                "createdRecordIds": [1],
                "updatedRecordIds": [2],
                "unchangedRecordIds": [],
                "lineErrors": {"2": ["Bad value."]},
            }
        },
        0,
    )
    result = merge_write_result(
        first,
        {
            "metadata": {
                "createdRecordIds": [3],
                "updatedRecordIds": [],
                "unchangedRecordIds": [4],
                "lineErrors": {"1": ["Other.", "Second."]},
            }
        },
        1000,
    )
    assert result.created_ids == (1, 3)
    assert result.updated_ids == (2,)
    assert result.unchanged_ids == (4,)
    assert result.errors == (
        RowError(index=1, messages=("Bad value.",)),
        RowError(index=1000, messages=("Other.", "Second.")),
    )
    assert "2 row(s) failed. The first is row 1: Bad value." in write_result_summary(result)


def test_write_error_survives_pickling() -> None:
    error = WriteError("failed", result=WriteResult(created_ids=(1,)))
    copy = pickle.loads(pickle.dumps(error))
    assert str(copy) == "failed"
    assert copy.result.created_ids == (1,)


def test_schema_cache_delete() -> None:
    cache = SchemaCache(10)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.delete("a", "missing")
    assert cache.get("a") is None
    assert cache.get("b") == 2


@pytest.mark.parametrize("value", ["", None, []])
def test_a_row_with_no_values_is_refused(value: Any) -> None:
    rows = build_rows([{"Title": "x"}, {"Title": value}], INDEX)
    with pytest.raises(InvalidValueError, match="Row 1 has no values"):
        refuse_blank_rows(rows)


def test_a_row_with_one_value_is_fine() -> None:
    refuse_blank_rows(build_rows([{"Title": "", "Due": date(2027, 1, 1)}, {"Title": "x"}], INDEX))
