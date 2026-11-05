"""Tests for summarizing records, which do no network access."""

import math
import statistics
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyquickbase import (
    F,
    InvalidResponseError,
    Period,
    QueryError,
    SummaryRow,
    UnknownFieldError,
)
from pyquickbase._models import Record, field_from_api
from pyquickbase._summary import Summary

SPEC = (
    (3, "Record ID#", "recordid"),
    (6, "Status", "text"),
    (7, "Hours", "numeric"),
    (8, "Due", "date"),
    (9, "Started", "timestamp"),
    (10, "Owner", "user"),
    (11, "Tags", "multitext"),
    (12, "Team", "multiuser"),
    (13, "File", "file"),
    (14, "Price", "currency"),
    (15, "At", "timeofday"),
    (16, "Done", "checkbox"),
    (17, "Spent", "duration"),
)
FIELDS = [
    field_from_api({"id": number, "label": label, "fieldType": kind})
    for number, label, kind in SPEC
]
LABELS = {label: number for number, label, _ in SPEC}


def rec(**values: Any) -> Record:
    return Record({LABELS[key]: value for key, value in values.items()}, LABELS)


def summarize(records: list[Record], group_by: Any = None, **options: Any) -> list[SummaryRow]:
    plan = dict.fromkeys(("total", "average", "minimum", "maximum"))
    plan.update(options)
    summary = Summary(FIELDS, group_by=group_by, **plan)
    for record in records:
        summary.add(record)
    return summary.rows()


def test_counting_everything() -> None:
    rows = summarize([rec(), rec(), rec()])
    assert [(row.group, row.count) for row in rows] == [({}, 3)]


def test_no_records_is_one_empty_row_without_groups() -> None:
    row = summarize([], total="Hours", average="Hours", minimum="Hours", maximum="Hours")[0]
    assert row.count == 0
    assert row.total == {"Hours": 0}
    assert row.average == {"Hours": None}
    assert row.minimum == {"Hours": None}
    assert row.maximum == {"Hours": None}


def test_no_records_is_an_empty_list_with_groups() -> None:
    assert summarize([], group_by="Status") == []


def test_grouping_and_every_calculation() -> None:
    rows = summarize(
        [
            rec(Status="b", Hours=1),
            rec(Status="a", Hours=2),
            rec(Status="b", Hours=4),
            rec(Status="a", Hours=None),
        ],
        group_by="Status",
        total="Hours",
        average="Hours",
        minimum="Hours",
        maximum="Hours",
    )
    assert [row.group for row in rows] == [{"Status": "a"}, {"Status": "b"}]
    first, second = rows
    assert first.count == 2  # The empty value still counts as a record.
    assert first.total == {"Hours": 2}
    assert first.average == {"Hours": 2}  # And doesn't count toward the average.
    assert second.count == 2
    assert second.total == {"Hours": 5}
    assert second.average == {"Hours": 2.5}
    assert second.minimum == {"Hours": 1}
    assert second.maximum == {"Hours": 4}


def test_totals_are_exact() -> None:
    rows = summarize([rec(Price=0.1) for _ in range(3)], total="Price", average="Price")
    assert rows[0].total == {"Price": 0.3}  # 0.1 + 0.1 + 0.1 is 0.30000000000000004 as floats.
    assert rows[0].average == {"Price": 0.1}


def test_a_field_can_be_used_several_ways_and_by_id_or_f() -> None:
    row = summarize(
        [rec(Hours=2, Price=3)], total=[7, F("Price")], average="Hours", maximum=["Hours", "Price"]
    )[0]
    assert row.total == {"Hours": 2, "Price": 3}
    assert row.average == {"Hours": 2}
    assert row.maximum == {"Hours": 2, "Price": 3}


def test_several_groups_and_empty_ones_sort_last() -> None:
    rows = summarize(
        [
            rec(Status="b", Hours=1),
            rec(Status="", Hours=1),
            rec(Status="a", Hours=2),
            rec(Status="a", Hours=None),
        ],
        group_by=["Status", "Hours"],
    )
    assert [(row.group["Status"], row.group["Hours"]) for row in rows] == [
        ("a", 2),
        ("a", None),
        ("b", 1),
        (None, 1),
    ]


def test_groups_with_a_missing_field_read_as_empty() -> None:
    assert summarize([rec()], group_by="Status")[0].group == {"Status": None}


def test_users_group_by_email_or_id() -> None:
    rows = summarize(
        [
            rec(Owner={"email": "a@example.com", "id": "1.a", "name": "A"}),
            rec(Owner={"email": "a@example.com", "id": "1.a", "name": "Renamed"}),
            rec(Owner={"id": "2.b"}),
            rec(Owner=None),
        ],
        group_by="Owner",
    )
    assert [(row.group["Owner"], row.count) for row in rows] == [
        ("2.b", 1),
        ("a@example.com", 2),
        (None, 1),
    ]


def test_booleans_and_numbers_can_group() -> None:
    rows = summarize([rec(Done=True), rec(Done=False)], group_by="Done")
    assert [row.group["Done"] for row in rows] == [False, True]


@pytest.mark.parametrize(
    ("unit", "label"),
    [
        ("day", "2026-03-14"),
        ("week", "2026-W11"),
        ("month", "2026-03"),
        ("quarter", "2026-Q1"),
        ("year", "2026"),
    ],
)
def test_period_labels(unit: Any, label: str) -> None:
    rows = summarize([rec(Due=date(2026, 3, 14))], group_by=Period("Due", unit))
    assert rows[0].group == {"Due": label}


@pytest.mark.parametrize(
    ("day", "quarter"),
    [
        (date(2026, 1, 1), "2026-Q1"),
        (date(2026, 3, 31), "2026-Q1"),
        (date(2026, 4, 1), "2026-Q2"),
        (date(2026, 9, 30), "2026-Q3"),
        (date(2026, 12, 31), "2026-Q4"),
    ],
)
def test_quarter_edges(day: date, quarter: str) -> None:
    assert summarize([rec(Due=day)], group_by=Period("Due", "quarter"))[0].group["Due"] == quarter


def test_iso_weeks_can_fall_in_another_year() -> None:
    assert summarize([rec(Due=date(2027, 1, 1))], group_by=Period("Due", "week"))[0].group == {
        "Due": "2026-W53"
    }


def test_periods_sort_in_order_with_empty_ones_last() -> None:
    days = [date(2027, 2, 1), None, date(2026, 12, 1), date(2027, 1, 1)]
    rows = summarize([rec(Due=day) for day in days], group_by=Period("Due", "month"))
    assert [row.group["Due"] for row in rows] == ["2026-12", "2027-01", "2027-02", None]


def test_a_timestamps_date_depends_on_the_time_zone() -> None:
    moment = datetime(2027, 1, 1, 2, tzinfo=UTC)
    assert summarize([rec(Started=moment)], group_by=Period("Started", "day"))[0].group == {
        "Started": "2027-01-01"
    }
    west = timezone(timedelta(hours=-5))
    assert summarize([rec(Started=moment)], group_by=Period("Started", "day", tz=west))[
        0
    ].group == {"Started": "2026-12-31"}


def test_minimums_and_maximums_of_dates_and_times() -> None:
    row = summarize(
        [
            rec(Due=date(2027, 1, 1), Started=datetime(2027, 1, 1, tzinfo=UTC), At="13:30:00"),
            rec(Due=date(2026, 1, 1), Started=datetime(2026, 1, 1, tzinfo=UTC), At="09:00:00"),
            rec(Due=None, Started=None, At=""),
        ],
        minimum=["Due", "Started", "At"],
        maximum=["Due", "At", "Record ID#"],
    )[0]
    assert row.minimum == {
        "Due": date(2026, 1, 1),
        "Started": datetime(2026, 1, 1, tzinfo=UTC),
        "At": "09:00:00",
    }
    assert row.maximum == {"Due": date(2027, 1, 1), "At": "13:30:00", "Record ID#": None}


def test_durations_add_up() -> None:
    assert summarize([rec(Spent=1000), rec(Spent=500)], total="Spent")[0].total == {"Spent": 1500}


@pytest.mark.parametrize(
    "options",
    [
        {"total": "Status"},
        {"average": "Due"},
        {"total": "Done"},
        {"total": "Record ID#"},
        {"minimum": "Status"},
        {"maximum": "Owner"},
        {"total": [None]},
        {"group_by": "Tags"},
        {"group_by": "Team"},
        {"group_by": "File"},
        {"group_by": ["Status", "Status"]},
        {"group_by": ["Due", Period("Due", "month")]},
        {"group_by": [Period("Due", "year"), Period("Due", "month")]},
        {"group_by": 1.5},
        {"group_by": [1.5]},
        {"group_by": Period("Status", "month")},
        {"group_by": Period("Hours", "month")},
    ],
)
def test_arguments_it_cant_use_are_refused(options: dict[str, Any]) -> None:
    with pytest.raises(QueryError):
        summarize([], **options)


@pytest.mark.parametrize(
    "options",
    [{"group_by": "Nope"}, {"total": "Nope"}, {"total": True}, {"group_by": Period("Nope", "day")}],
)
def test_unknown_fields_are_refused(options: dict[str, Any]) -> None:
    with pytest.raises(UnknownFieldError):
        summarize([], **options)


@pytest.mark.parametrize(
    "arguments",
    [
        ("Due", "decade"),
        ("Due", "Month"),
        ("Due", None),
        (True, "day"),
        (None, "day"),
        ("Due", "day", "UTC"),
    ],
)
def test_bad_periods_are_refused(arguments: tuple[Any, ...]) -> None:
    with pytest.raises(QueryError):
        Period(*arguments)


@pytest.mark.parametrize(
    ("record", "options"),
    [
        (rec(Hours="abc"), {"total": "Hours"}),
        (rec(Hours=True), {"average": "Hours"}),
        (rec(Hours=[1]), {"total": "Hours"}),
        (rec(Hours="abc"), {"maximum": "Hours"}),
        (rec(Due="soon"), {"minimum": "Due"}),
        (rec(At=5), {"minimum": "At"}),
        (rec(Due="soon"), {"group_by": Period("Due", "month")}),
        (rec(Status=["a"]), {"group_by": "Status"}),
        (rec(Owner={"name": "No email or ID"}), {"group_by": "Owner"}),
    ],
)
def test_a_value_that_doesnt_match_its_type_raises_rather_than_being_skipped(
    record: Record, options: dict[str, Any]
) -> None:
    with pytest.raises(InvalidResponseError):
        summarize([record], **options)


def test_errors_and_reprs_dont_show_values() -> None:
    with pytest.raises(InvalidResponseError) as caught:
        summarize([rec(Hours="s3cret")], total="Hours")
    assert "s3cret" not in str(caught.value)
    row = summarize([rec(Status="s3cret", Hours=1)], group_by="Status", total="Hours")[0]
    assert "s3cret" not in repr(row)
    assert "Status" in repr(row)


def test_it_only_selects_the_fields_it_needs() -> None:
    summary = Summary(
        FIELDS,
        group_by=["Status", Period("Due", "year")],
        total="Hours",
        average="Hours",
        minimum="Price",
        maximum=None,
    )
    assert summary.select == [6, 8, 7, 14]


AMOUNTS = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, width=32)


@given(st.lists(st.tuples(st.sampled_from("abc"), st.none() | AMOUNTS), max_size=30))
def test_results_match_a_plain_calculation(pairs: list[tuple[str, float | None]]) -> None:
    rows = summarize(
        [rec(Status=status, Hours=hours) for status, hours in pairs],
        group_by="Status",
        total="Hours",
        average="Hours",
        minimum="Hours",
        maximum="Hours",
    )
    assert [row.group["Status"] for row in rows] == sorted({status for status, _ in pairs})
    assert sum(row.count for row in rows) == len(pairs)
    for row in rows:
        values = [h for s, h in pairs if s == row.group["Status"] and h is not None]
        assert row.count == sum(1 for s, _ in pairs if s == row.group["Status"])
        assert math.isclose(row.total["Hours"], math.fsum(values), abs_tol=1e-6)
        if values:
            assert math.isclose(
                row.average["Hours"] or 0.0, statistics.fmean(values), rel_tol=1e-6, abs_tol=1e-6
            )
            assert row.minimum["Hours"] == min(values)
            assert row.maximum["Hours"] == max(values)
        else:
            assert row.average["Hours"] is None


@given(st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 12, 31)))
def test_period_labels_sort_like_their_dates(day: date) -> None:
    later = day + timedelta(days=40)
    for unit in ("day", "month", "quarter", "year", "week"):
        rows = summarize([rec(Due=later), rec(Due=day)], group_by=Period("Due", unit))
        labels = [row.group["Due"] for row in rows]
        assert labels == sorted(labels)
        assert len(labels) <= 2
