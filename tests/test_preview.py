"""Tests for comparing values in a preview, which do no network access."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyquickbase import InvalidValueError, RecordChange, UpdatePreview, User, ValueChange
from pyquickbase._models import FieldIndex, Record, field_from_api, file_from_api
from pyquickbase._preview import build_update_preview, id_batches, is_same, merge_rows

USER = User(id="123.abc", name="A", email="a@example.com")


def field(kind: str) -> Any:
    return field_from_api({"id": 6, "label": "Value", "fieldType": kind})


@pytest.mark.parametrize(
    ("kind", "before", "after"),
    [
        ("text", "x", "x"),
        ("text", "5", 5),
        ("text", "", ""),
        ("text", "", None),
        ("text-multiple-choice", "Open", "Open"),
        ("numeric", 5, 5),
        ("numeric", 5, 5.0),
        ("numeric", 5, "5"),
        ("numeric", 5.5, "5.50"),
        ("numeric", None, ""),
        ("currency", 0.1, "0.10"),
        ("percent", 0.5, 0.5),
        ("rating", 3, 3),
        ("duration", 1000, 1000),
        ("date", date(2027, 1, 2), "2027-01-02"),
        ("date", None, ""),
        ("timestamp", datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC), "2027-01-02T03:04:05+00:00"),
        ("timestamp", datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC), "2027-01-02T03:04:05.250+00:00"),
        ("timestamp", datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC), "2027-01-01T22:04:05-05:00"),
        ("timestamp", None, ""),
        ("checkbox", True, True),
        ("checkbox", False, False),
        ("checkbox", False, ""),
        ("user", USER, "a@example.com"),
        ("user", USER, "A@EXAMPLE.COM"),
        ("user", USER, "123.abc"),
        ("user", USER, {"id": "123.abc"}),
        ("user", None, ""),
        ("multiuser", [USER], ["a@example.com"]),
        ("multiuser", [], []),
        ("multitext", ["a", "b"], ["b", "a"]),
        ("multitext", [], ""),
    ],
)
def test_values_that_wouldnt_change(kind: str, before: Any, after: Any) -> None:
    assert is_same(field(kind), before, after)


@pytest.mark.parametrize(
    ("kind", "before", "after"),
    [
        ("text", "x", "y"),
        ("text", "x", "X"),  # Quickbase stores what's written.
        ("text", "x", ""),
        ("text", "", "x"),
        ("text", "true", True),
        ("text", "[1]", [1]),
        ("numeric", 5, 6),
        ("numeric", 5, "abc"),
        ("numeric", 1, True),
        ("numeric", None, 0),
        ("numeric", 0, ""),
        ("date", date(2027, 1, 2), "2027-01-03"),
        ("date", date(2027, 1, 2), "soon"),
        ("date", date(2027, 1, 2), 5),
        ("date", "unexpected", "2027-01-02"),
        ("date", date(2027, 1, 2), ""),
        ("timestamp", datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC), "2027-01-02T03:04:06+00:00"),
        ("timestamp", datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC), "2027-01-02T03:04:05"),
        ("timestamp", datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC), "soon"),
        ("timestamp", "unexpected", "2027-01-02T03:04:05+00:00"),
        ("checkbox", True, False),
        ("checkbox", False, True),
        ("checkbox", True, ""),
        ("checkbox", True, "true"),
        ("checkbox", False, "false"),
        ("user", USER, "b@example.com"),
        ("user", USER, 5),
        ("user", "unexpected", "a@example.com"),
        ("user", None, "a@example.com"),
        ("user", USER, ""),
        ("multiuser", [USER], ["b@example.com"]),
        ("multiuser", [USER], "a@example.com"),
        ("multiuser", [USER], ["a@example.com", "b@example.com"]),
        ("multitext", ["a"], ["a", "b"]),
        ("multitext", ["a"], "a"),
        ("file", None, {"fileName": "a.txt", "data": ""}),
        (
            "file",
            file_from_api({"versions": [{"versionNumber": 1, "fileName": "a.txt"}]}),
            {"fileName": "a.txt", "data": ""},
        ),
    ],
)
def test_values_that_would_change_or_arent_certain(kind: str, before: Any, after: Any) -> None:
    assert not is_same(field(kind), before, after)


def test_an_unknown_field_type_compares_as_text_or_counts_as_a_change() -> None:
    assert is_same(field("futuretype"), "x", "x")
    assert not is_same(field("futuretype"), {"a": 1}, {"a": 1})


def test_a_timestamp_in_another_zone_is_the_same_moment() -> None:
    zone = timezone(timedelta(hours=9))
    moment = datetime(2027, 1, 2, 12, tzinfo=zone)
    assert is_same(field("timestamp"), moment, "2027-01-02T03:00:00+00:00")


# Merging rows


def cell(value: Any) -> dict[str, Any]:
    return {"value": value}


def test_rows_group_by_record_and_a_later_row_wins() -> None:
    rows = [
        {"3": cell(1), "6": cell("a"), "7": cell("b")},
        {"3": cell(2), "6": cell("c")},
        {"3": cell(1), "6": cell("z")},
    ]
    assert merge_rows(rows) == {1: {6: "z", 7: "b"}, 2: {6: "c"}}


@pytest.mark.parametrize("record_id", ["1", 0, -1, True, 1.5, None])
def test_a_record_id_must_be_a_positive_integer(record_id: Any) -> None:
    with pytest.raises(InvalidValueError, match="Record ID#"):
        merge_rows([{"3": cell(record_id), "6": cell("a")}])


def test_ids_are_read_in_batches() -> None:
    assert [len(batch) for batch in id_batches(list(range(250)))] == [100, 100, 50]
    assert list(id_batches([])) == []


# Building a preview

FIELDS = FieldIndex(
    field_from_api({"id": number, "label": label, "fieldType": kind})
    for number, label, kind in (
        (3, "Record ID#", "recordid"),
        (6, "Title", "text"),
        (7, "Hours", "numeric"),
        (8, "Done", "checkbox"),
        (13, "Attachment", "file"),
    )
)
LABELS = {"Record ID#": 3, "Title": 6, "Hours": 7, "Done": 8, "Attachment": 13}


def record(**values: Any) -> Record:
    return Record({LABELS[key.replace("_", " ")]: value for key, value in values.items()}, LABELS)


def test_a_preview_sorts_records_into_what_would_happen() -> None:
    rows = {1: {6: "new", 7: 5}, 2: {6: "same"}, 3: {6: "x"}, 4: {}}
    current = {1: record(Title="old", Hours=5), 2: record(Title="same"), 4: record(Title="a")}
    preview = build_update_preview(rows, current, FIELDS)
    assert preview.changes == (
        RecordChange(record_id=1, fields={"Title": ValueChange(before="old", after="new")}),
    )
    assert preview.unchanged_ids == (2, 4)
    assert preview.missing_ids == (3,)


def test_empty_values_read_as_none() -> None:
    preview = build_update_preview({1: {6: "", 7: 5}}, {1: record(Title="old", Hours=None)}, FIELDS)
    fields = preview.changes[0].fields
    assert fields["Title"] == ValueChange(before="old", after=None)
    assert fields["Hours"] == ValueChange(before=None, after=5)


def test_clearing_a_checkbox_reads_as_false() -> None:
    preview = build_update_preview({1: {8: ""}}, {1: record(Done=True)}, FIELDS)
    assert preview.changes[0].fields["Done"] == ValueChange(before=True, after=False)


def test_a_field_the_response_left_out_counts_as_a_change() -> None:
    preview = build_update_preview({1: {6: "new"}}, {1: record(Hours=1)}, FIELDS)
    assert preview.changes[0].fields["Title"] == ValueChange(before=None, after="new")


def test_a_file_shows_only_its_name() -> None:
    stored = file_from_api(
        {
            "url": "/files/x",
            "versions": [
                {
                    "versionNumber": 2,
                    "fileName": "new.txt",
                    "creator": {"email": "s3cret@example.com"},
                },
                {
                    "versionNumber": 1,
                    "fileName": "old.txt",
                    "creator": {"email": "s3cret@example.com"},
                },
            ],
        }
    )
    written = {"fileName": "up.txt", "data": "czNjcmV0"}
    preview = build_update_preview({1: {13: written}}, {1: record(Attachment=stored)}, FIELDS)
    change = preview.changes[0].fields["Attachment"]
    assert change == ValueChange(before="new.txt", after="up.txt")
    assert "s3cret" not in str(change.before)
    assert "czNjcmV0" not in str(change.after)


@pytest.mark.parametrize(
    "stored", [file_from_api({"url": "", "versions": []}), None, "x", {"versions": ["x"]}]
)
def test_a_file_field_with_nothing_usable_reads_as_none(stored: Any) -> None:
    preview = build_update_preview(
        {1: {13: {"fileName": "up.txt", "data": ""}}}, {1: record(Attachment=stored)}, FIELDS
    )
    assert preview.changes[0].fields["Attachment"].before is None


def test_reprs_never_show_values() -> None:
    change = ValueChange(before="s3cret-before", after="s3cret-after")
    preview = UpdatePreview(changes=(RecordChange(record_id=1, fields={"Title": change}),))
    for text in (repr(change), repr(preview), repr(preview.changes[0])):
        assert "s3cret" not in text
    assert "Title" in repr(preview.changes[0])


@given(
    st.decimals(
        allow_nan=False, allow_infinity=False, places=4, min_value=-(10**9), max_value=10**9
    )
)
def test_a_number_written_as_text_compares_equal(value: Decimal) -> None:
    assert is_same(field("numeric"), float(value), str(value)) == (
        Decimal(str(float(value))) == value
    )


@given(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(),
        st.text(max_size=5),
        st.lists(st.integers(), max_size=2),
    )
)
def test_any_value_can_be_compared_on_any_type(value: Any) -> None:
    for kind in (
        "text",
        "numeric",
        "date",
        "timestamp",
        "checkbox",
        "user",
        "multiuser",
        "multitext",
        "file",
    ):
        assert isinstance(is_same(field(kind), value, value), bool)


def test_a_user_compares_by_id_however_its_written() -> None:
    held = User(id="1.a", name="Ann", email="ann@example.com")
    assert is_same(field("user"), held, User(id="1.a", name="", email=None))
    assert is_same(field("user"), held, {"id": "1.a"})
    assert not is_same(field("user"), held, User(id="2.b", name="Ann", email="ann@example.com"))
