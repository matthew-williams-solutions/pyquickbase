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
    InvalidQueryError,
    InvalidResponseError,
    Period,
    SummaryRow,
    UnknownFieldError,
    User,
)
from pyquickbase._models import Record, field_from_api
from pyquickbase._summary import GroupSort, Summary, sort_rows

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
    compare = options.pop("compare_periods", False)
    summary = Summary(FIELDS, group_by=group_by, statistics=options, compare_periods=compare)
    for record in records:
        summary.add(record)
    return summary.rows()


def test_counting_everything() -> None:
    rows = summarize([rec(), rec(), rec()])
    assert [(row.group, row.count) for row in rows] == [({}, 3)]


def test_no_records_is_one_empty_row_without_groups() -> None:
    row = summarize([], sum="Hours", avg="Hours", min="Hours", max="Hours")[0]
    assert row.count == 0
    assert row.sum == {"Hours": 0}
    assert row.avg == {"Hours": None}
    assert row.min == {"Hours": None}
    assert row.max == {"Hours": None}


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
        sum="Hours",
        avg="Hours",
        min="Hours",
        max="Hours",
    )
    assert [row.group for row in rows] == [{"Status": "a"}, {"Status": "b"}]
    first, second = rows
    assert first.count == 2  # The empty value still counts as a record.
    assert first.sum == {"Hours": 2}
    assert first.avg == {"Hours": 2}  # And doesn't count toward the average.
    assert second.count == 2
    assert second.sum == {"Hours": 5}
    assert second.avg == {"Hours": 2.5}
    assert second.min == {"Hours": 1}
    assert second.max == {"Hours": 4}


def test_totals_are_exact() -> None:
    rows = summarize([rec(Price=0.1) for _ in range(3)], sum="Price", avg="Price")
    assert rows[0].sum == {"Price": 0.3}  # 0.1 + 0.1 + 0.1 is 0.30000000000000004 as floats.
    assert rows[0].avg == {"Price": 0.1}


def test_a_field_can_be_used_several_ways_and_by_id_or_f() -> None:
    row = summarize(
        [rec(Hours=2, Price=3)], sum=[7, F("Price")], avg="Hours", max=["Hours", "Price"]
    )[0]
    assert row.sum == {"Hours": 2, "Price": 3}
    assert row.avg == {"Hours": 2}
    assert row.max == {"Hours": 2, "Price": 3}


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
            rec(Owner=User(email="a@example.com", id="1.a", name="A")),
            rec(Owner=User(email="a@example.com", id="1.a", name="Renamed")),
            rec(Owner=User(id="2.b", name="")),
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
    assert summarize([rec(Started=moment)], group_by=Period("Started", "day", time_zone=west))[
        0
    ].group == {"Started": "2026-12-31"}


def test_minimums_and_maximums_of_dates_and_times() -> None:
    row = summarize(
        [
            rec(Due=date(2027, 1, 1), Started=datetime(2027, 1, 1, tzinfo=UTC), At="13:30:00"),
            rec(Due=date(2026, 1, 1), Started=datetime(2026, 1, 1, tzinfo=UTC), At="09:00:00"),
            rec(Due=None, Started=None, At=""),
        ],
        min=["Due", "Started", "At"],
        max=["Due", "At", "Record ID#"],
    )[0]
    assert row.min == {
        "Due": date(2026, 1, 1),
        "Started": datetime(2026, 1, 1, tzinfo=UTC),
        "At": "09:00:00",
    }
    assert row.max == {"Due": date(2027, 1, 1), "At": "13:30:00", "Record ID#": None}


def test_durations_add_up() -> None:
    assert summarize([rec(Spent=1000), rec(Spent=500)], sum="Spent")[0].sum == {"Spent": 1500}


@pytest.mark.parametrize(
    "options",
    [
        {"sum": "Status"},
        {"avg": "Due"},
        {"sum": "Done"},
        {"sum": "Record ID#"},
        {"min": "Status"},
        {"max": "Owner"},
        {"sum": [None]},
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
    with pytest.raises(InvalidQueryError):
        summarize([], **options)


@pytest.mark.parametrize(
    "options",
    [{"group_by": "Nope"}, {"sum": "Nope"}, {"sum": True}, {"group_by": Period("Nope", "day")}],
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
    with pytest.raises(InvalidQueryError):
        Period(*arguments)


@pytest.mark.parametrize(
    ("record", "options"),
    [
        (rec(Hours="abc"), {"sum": "Hours"}),
        (rec(Hours=True), {"avg": "Hours"}),
        (rec(Hours=[1]), {"sum": "Hours"}),
        (rec(Hours="abc"), {"max": "Hours"}),
        (rec(Due="soon"), {"min": "Due"}),
        (rec(At=5), {"min": "At"}),
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
        summarize([rec(Hours="s3cret")], sum="Hours")
    assert "s3cret" not in str(caught.value)
    row = summarize([rec(Status="s3cret", Hours=1)], group_by="Status", sum="Hours")[0]
    assert "s3cret" not in repr(row)
    assert "Status" in repr(row)


def test_it_only_selects_the_fields_it_needs() -> None:
    summary = Summary(
        FIELDS,
        group_by=["Status", Period("Due", "year")],
        statistics={"sum": "Hours", "avg": "Hours", "min": "Price", "max": None},
    )
    assert summary.select == [6, 8, 7, 14]


AMOUNTS = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, width=32)


@given(st.lists(st.tuples(st.sampled_from("abc"), st.none() | AMOUNTS), max_size=30))
def test_results_match_a_plain_calculation(pairs: list[tuple[str, float | None]]) -> None:
    rows = summarize(
        [rec(Status=status, Hours=hours) for status, hours in pairs],
        group_by="Status",
        sum="Hours",
        avg="Hours",
        median="Hours",
        std_dev="Hours",
        min="Hours",
        max="Hours",
        distinct_count="Hours",
    )
    assert [row.group["Status"] for row in rows] == sorted({status for status, _ in pairs})
    assert sum(row.count for row in rows) == len(pairs)
    for row in rows:
        values = [h for s, h in pairs if s == row.group["Status"] and h is not None]
        assert row.count == sum(1 for s, _ in pairs if s == row.group["Status"])
        assert math.isclose(row.sum["Hours"], math.fsum(values), abs_tol=1e-6)
        if values:
            assert math.isclose(
                row.avg["Hours"] or 0.0, statistics.fmean(values), rel_tol=1e-6, abs_tol=1e-6
            )
            assert row.min["Hours"] == min(values)
            assert row.max["Hours"] == max(values)
            assert math.isclose(
                row.median["Hours"] or 0.0, statistics.median(values), rel_tol=1e-6, abs_tol=1e-6
            )
        else:
            assert row.avg["Hours"] is None
            assert row.median["Hours"] is None
        assert row.distinct_count["Hours"] == len(set(values))
        if len(values) > 1:
            expected = statistics.stdev(values)
            assert math.isclose(row.std_dev["Hours"] or 0.0, expected, rel_tol=1e-6, abs_tol=1e-3)
        else:
            assert row.std_dev["Hours"] is None


@given(st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 12, 31)))
def test_period_labels_sort_like_their_dates(day: date) -> None:
    later = day + timedelta(days=40)
    for unit in ("day", "month", "quarter", "year", "week"):
        rows = summarize([rec(Due=later), rec(Due=day)], group_by=Period("Due", unit))
        labels = [row.group["Due"] for row in rows]
        assert labels == sorted(labels)
        assert len(labels) <= 2


def plan(group_by: Any = None, fields: Any = FIELDS, **options: Any) -> Summary:
    return Summary(fields, group_by=group_by, statistics=options)


def test_only_counting_needs_no_records() -> None:
    assert plan().counts_only
    assert not plan("Status").counts_only
    assert not plan(sum="Hours").counts_only
    assert not plan(min="Due").counts_only


@pytest.mark.parametrize(
    ("group_by", "options", "size"),
    [
        ("Status", {}, 25_000),
        ("Status", {"sum": "Hours"}, 16_666),
        (["Status", "Due"], {"sum": "Hours", "avg": "Price"}, 10_000),
        # A field used twice is read once.
        ("Status", {"sum": "Hours", "avg": "Hours", "max": "Hours"}, 16_666),
    ],
)
def test_pages_shrink_as_more_fields_are_read(group_by: Any, options: Any, size: int) -> None:
    assert plan(group_by, **options).page_size() == size


def test_long_text_makes_pages_smaller_but_never_below_a_thousand() -> None:
    notes = [*FIELDS, field_from_api({"id": 20, "label": "Notes", "fieldType": "text-multi-line"})]
    assert plan("Notes", fields=notes).page_size() == 10_000
    wide = [
        *FIELDS,
        *(
            field_from_api({"id": 100 + n, "label": f"N{n}", "fieldType": "numeric"})
            for n in range(80)
        ),
    ]
    assert plan(sum=[f"N{n}" for n in range(80)], fields=wide).page_size() == 1_000


def row(status: Any, count: int, hours: float | None = None) -> SummaryRow:
    return SummaryRow(
        group={"Status": status}, count=count, sum={"Hours": hours} if hours is not None else {}
    )


def statuses(rows: list[SummaryRow]) -> list[Any]:
    return [item.group["Status"] for item in rows]


def test_groups_sort_by_count_with_ties_in_their_first_order() -> None:
    rows = [row("a", 2), row("b", 5), row("c", 2), row(None, 9)]
    assert statuses(sort_rows(rows, [GroupSort("count", descending=True)])) == [None, "b", "a", "c"]
    assert statuses(sort_rows(rows, [GroupSort("count")])) == ["a", "c", "b", None]


def test_empty_values_sort_last_in_either_direction() -> None:
    rows = [row(None, 1), row("b", 1), row("a", 1)]
    assert statuses(sort_rows(rows, [GroupSort("group", "Status")])) == ["a", "b", None]
    assert statuses(sort_rows(rows, [GroupSort("group", "Status", True)])) == ["b", "a", None]


def test_groups_sort_by_a_total_then_another_key() -> None:
    rows = [row("a", 1, 10), row("b", 3, 30), row("c", 2, 30), row("d", 1)]
    keys = [GroupSort("sum", "Hours", True), GroupSort("count", descending=True)]
    assert statuses(sort_rows(rows, keys)) == ["b", "c", "a", "d"]


def test_no_keys_keep_the_order() -> None:
    rows = [row("b", 1), row("a", 2)]
    assert sort_rows(rows, []) == rows


def test_a_median_and_spread_ignore_empty_values() -> None:
    records = [rec(Hours=hours) for hours in (1, 2, 3, 10, None)]
    [row] = summarize(records, median="Hours", std_dev="Price")
    assert row.median == {"Hours": 2.5}
    assert row.std_dev == {"Price": None}


def test_distinct_count_counts_users_and_dates_once_each() -> None:
    ann, bob = User(id="1.a", name="Ann", email="ann@x"), User(id="2.b", name="Bob", email="bob@x")
    records = [rec(Owner=ann, Due=date(2027, 1, 1)), rec(Owner=ann), rec(Owner=bob), rec()]
    [row] = summarize(records, distinct_count=["Owner", "Due"])
    assert row.distinct_count == {"Owner": 2, "Due": 1}


def test_distinct_count_refuses_fields_with_several_values() -> None:
    with pytest.raises(InvalidQueryError, match="several of its values"):
        summarize([], distinct_count="Tags")


@pytest.mark.parametrize("name", ["median", "std_dev"])
def test_a_spread_needs_numbers(name: str) -> None:
    with pytest.raises(InvalidQueryError, match="needs a numeric field"):
        summarize([], **{name: "Status"})


def test_each_period_is_compared_with_the_one_before() -> None:
    records = [
        rec(Due=date(2027, 1, 5), Hours=4),
        rec(Due=date(2027, 1, 20), Hours=6),
        rec(Due=date(2027, 2, 3), Hours=7),
        rec(Due=date(2027, 4, 1), Hours=1),
        rec(Hours=100),
    ]
    rows = summarize(
        records, group_by=Period("Due", "month"), sum="Hours", avg="Hours", compare_periods=True
    )
    changes = {row.group["Due"]: row.change for row in rows}
    assert changes["2027-01"] is None  # Nothing before it was summarized.
    assert changes[None] is None
    february = changes["2027-02"]
    assert february is not None
    assert (february.previous, february.count) == ("2027-01", -1)
    assert february.values == {"sum": {"Hours": -3.0}, "avg": {"Hours": 2.0}}
    april = changes["2027-04"]
    assert april is not None
    # March had no records, so its sum is 0 and it has no average.
    assert (april.previous, april.count) == ("2027-03", 1)
    assert april.values == {"sum": {"Hours": 1.0}, "avg": {"Hours": None}}


def test_periods_are_compared_within_the_other_groups() -> None:
    records = [
        rec(Status="a", Due=date(2027, 1, 1)),
        rec(Status="a", Due=date(2027, 2, 1)),
        rec(Status="a", Due=date(2027, 2, 2)),
        rec(Status="b", Due=date(2027, 2, 1)),
    ]
    rows = summarize(records, group_by=["Status", Period("Due", "month")], compare_periods=True)
    found = {
        (row.group["Status"], row.group["Due"]): row.change and row.change.count for row in rows
    }
    assert found == {("a", "2027-01"): None, ("a", "2027-02"): 1, ("b", "2027-02"): 1}


@pytest.mark.parametrize(
    ("unit", "label", "previous"),
    [
        ("day", "2027-03-01", "2027-02-28"),
        ("week", "2027-W01", "2026-W53"),
        ("month", "2027-01", "2026-12"),
        ("quarter", "2027-Q1", "2026-Q4"),
        ("quarter", "2027-Q3", "2027-Q2"),
        ("year", "2027", "2026"),
    ],
)
def test_the_period_before_crosses_years(unit: Any, label: str, previous: str) -> None:
    from pyquickbase._summary import _previous_period  # pyright: ignore[reportPrivateUsage]

    assert _previous_period(label, unit) == previous


@pytest.mark.parametrize(
    "group_by", [None, "Status", [Period("Due", "month"), Period("Started", "year")]]
)
def test_comparing_needs_exactly_one_period(group_by: Any) -> None:
    with pytest.raises(InvalidQueryError, match="exactly one Period"):
        summarize([], group_by=group_by, compare_periods=True)


def test_rows_can_be_sorted_by_a_new_statistic() -> None:
    records = [rec(Status=s, Hours=h) for s, h in (("a", 1), ("a", 9), ("b", 4), ("b", 5))]
    rows = summarize(records, group_by="Status", median="Hours")
    ordered = sort_rows(rows, [GroupSort("median", "Hours", descending=True)])
    assert [row.group["Status"] for row in ordered] == ["a", "b"]
