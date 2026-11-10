"""Small behaviors with real consequences: values read and written, and arguments refused."""

import pickle
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from pyquickbase import (
    F,
    InvalidValueError,
    SummaryTooLargeError,
    User,
)
from pyquickbase._code_pages import check_text
from pyquickbase._frames import frame_rows
from pyquickbase._models import FieldIndex, field_from_api, file_from_api, page_from_api
from pyquickbase._read_request import shows_only
from pyquickbase._summary import _difference  # pyright: ignore[reportPrivateUsage]
from pyquickbase._write_request import describe_write

INDEX = FieldIndex([field_from_api({"id": 6, "label": "Tags", "fieldType": "multitext"})])


def test_has_and_not_has_compare_a_whole_value() -> None:
    assert F("Tags").has("a").compile(INDEX) == "{6.HAS.'a'}"
    assert F("Tags").not_has("a").compile(INDEX) == "{6.XHAS.'a'}"


def test_a_summary_too_large_error_survives_a_pickle() -> None:
    error = SummaryTooLargeError(5000, 100)
    copy = pickle.loads(pickle.dumps(error))
    assert (type(copy), copy.matched, copy.limit) == (SummaryTooLargeError, 5000, 100)
    assert str(copy) == str(error)


def test_a_cell_holding_an_array_becomes_a_list() -> None:
    frame = pd.DataFrame({"Tags": [np.array(["a", "b"], dtype=object)]})
    assert frame_rows(frame) == [{"Tags": ["a", "b"]}]


def test_numpy_numbers_become_plain_numbers() -> None:
    frame = pd.DataFrame({"Hours": pd.Series([np.int64(2), np.float64(1.5)], dtype=object)})
    rows = frame_rows(frame)
    assert rows == [{"Hours": 2}, {"Hours": 1.5}]
    assert type(rows[0]["Hours"]) is int


def test_a_file_version_with_an_unreadable_date_has_no_date() -> None:
    found = file_from_api(
        {"url": "/f", "versions": [{"versionNumber": 1, "fileName": "a", "uploaded": "soon"}]}
    )
    assert found.versions[0].uploaded_at is None


def page_of(kind: str, value: Any) -> Any:
    return page_from_api(
        {
            "fields": [{"id": 7, "label": "Who", "type": kind}],
            "data": [{"7": {"value": value}}],
            "metadata": {"totalRecords": 1, "skip": 0},
        }
    ).records[0]["Who"]


def test_a_user_list_is_read_as_users() -> None:
    found = page_of("multiuser", [{"id": "1.a", "name": "A", "email": "a@x.com"}])
    assert [type(item) for item in found] == [User]


def test_a_user_list_holding_something_else_is_left_as_it_came() -> None:
    assert page_of("multiuser", ["1.a"]) == ["1.a"]


def test_a_code_pages_text_must_be_text() -> None:
    with pytest.raises(InvalidValueError, match="must be text"):
        check_text(5)


def test_a_formula_that_cant_be_read_is_never_shown() -> None:
    assert not shows_only("[Unclosed", [("Title", 6)])
    assert shows_only("[Title] & 'x'", [("Title", 6)])


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [(3, 1, 2.0), (0.3, 0.1, 0.2), (True, 1, None), ("a", 1, None), (datetime.min, 1, None)],
)
def test_a_change_between_periods_is_only_for_numbers(
    current: Any, previous: Any, expected: float | None
) -> None:
    assert _difference(current, previous) == expected


@pytest.mark.parametrize(
    ("method", "body"),
    [("GET", None), ("POST", None), ("PATCH", {"lookupFieldIds": [6]})],
    ids=["get", "no-body", "patch"],
)
def test_a_relationship_request_that_isnt_a_change_is_unknown(method: str, body: Any) -> None:
    assert describe_write(method, "/tables/bqtasks/relationship/9", body).action == "other"
