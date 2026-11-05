"""Tests for filters built from plain data, which do no network access."""
# pyright: reportUnknownMemberType=false

import contextlib
import itertools
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from pyquickbase import F, QueryError, UnknownFieldError, where_from_dict, where_schema
from pyquickbase._models import FieldIndex, field_from_api
from pyquickbase._query import compile_where

FIELDS = FieldIndex(
    field_from_api({"id": number, "label": label, "fieldType": kind})
    for number, label, kind in (
        (3, "Record ID#", "recordid"),
        (6, "Title", "text"),
        (7, "Status", "text-multiple-choice"),
        (8, "Due", "date"),
        (9, "Started", "timestamp"),
        (10, "Hours", "numeric"),
        (11, "Done", "checkbox"),
        (12, "Owner", "user"),
        (13, "Team", "multiuser"),
        (14, "Price", "currency"),
    )
)


def cond(field: Any, op: Any, value: Any) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


def compiled(data: Any, **kwargs: Any) -> str:
    return compile_where(where_from_dict(data, **kwargs), FIELDS)


@pytest.mark.parametrize(
    ("op", "operator"),
    [
        ("==", "EX"),
        ("!=", "XEX"),
        ("<", "LT"),
        ("<=", "LTE"),
        (">", "GT"),
        (">=", "GTE"),
        ("contains", "CT"),
        ("not_contains", "XCT"),
        ("starts_with", "SW"),
        ("not_starts_with", "XSW"),
        ("HAS", "HAS"),
    ],
)
def test_operators_on_text(op: str, operator: str) -> None:
    assert compiled(cond("Title", op, "x")) == f"{{6.{operator}.'x'}}"


def test_labels_and_ids_both_name_a_field() -> None:
    assert compiled(cond(6, "==", "x")) == compiled(cond("Title", "==", "x"))


def test_groups_nest_like_f() -> None:
    data = {
        "all": [
            cond("Status", "==", "Open"),
            {"any": [cond("Hours", ">", 3), cond("Title", "contains", "urgent")]},
        ]
    }
    expected = (F("Status") == "Open") & ((F("Hours") > 3) | F("Title").contains("urgent"))
    assert compiled(data) == compile_where(expected, FIELDS)


def test_a_group_of_one_is_that_condition() -> None:
    assert compiled({"any": [{"all": [cond("Title", "==", "x")]}]}) == "{6.EX.'x'}"


def test_in_and_not_in() -> None:
    assert compiled(cond("Status", "in", ["a", "b"])) == "{7.EX.'a'}OR{7.EX.'b'}"
    assert compiled(cond("Status", "not_in", ["a", "b"])) == "{7.XEX.'a'}AND{7.XEX.'b'}"
    assert compiled(cond("Status", "in", ["a"])) == "{7.EX.'a'}"
    assert compiled(cond("Status", "in", ["a", None])) == "{7.EX.'a'}OR{7.EX.''}"


def test_it_combines_with_f_so_code_can_fix_the_scope() -> None:
    where = (F("Status") == "Open") & where_from_dict(cond("Title", "==", "x"))
    assert compile_where(where, FIELDS) == "{7.EX.'Open'}AND{6.EX.'x'}"


@pytest.mark.parametrize(
    "data",
    [
        None,
        "{}",
        [],
        5,
        {},
        {"all": []},
        {"any": "x"},
        {"all": {"field": "Title"}},
        {"all": [cond("Title", "==", "x")], "any": [cond("Title", "==", "y")]},
        {"all": [cond("Title", "==", "x")], "field": "Title"},
        {"all": ["x"]},
        {"field": "Title", "op": "=="},
        {"field": "Title", "value": "x"},
        {"op": "==", "value": "x"},
        {**cond("Title", "==", "x"), "extra": 1},
        {"Field": "Title", "op": "==", "value": "x"},
        {"not": cond("Title", "==", "x")},
        cond(True, "==", "x"),
        cond(None, "==", "x"),
        cond(["Title"], "==", "x"),
        cond("Title", None, "x"),
        cond("Title", ["=="], "x"),
    ],
)
def test_bad_shapes_are_refused(data: Any) -> None:
    with pytest.raises(QueryError):
        where_from_dict(data)


@pytest.mark.parametrize("op", ["ex", "=", "===", "like", "IR", "XIR", "", "EX'}OR{3.GT.'0"])
def test_unknown_operators_are_refused(op: str) -> None:
    with pytest.raises(QueryError, match=r"where\.op"):
        where_from_dict(cond("Title", op, "x"))


@pytest.mark.parametrize(
    ("op", "value"),
    [
        ("in", "abc"),  # Would otherwise search for "a", "b", and "c".
        ("in", []),
        ("not_in", []),
        ("in", None),
        ("in", {"a": 1}),
        ("in", [["a"]]),
        ("==", ["a"]),
        ("==", {"a": 1}),
    ],
)
def test_bad_value_shapes_are_refused(op: str, value: Any) -> None:
    with pytest.raises(QueryError):
        where_from_dict(cond("Title", op, value))


@pytest.mark.parametrize("op", ["contains", "not_contains", "starts_with", "<", ">=", "HAS"])
@pytest.mark.parametrize("value", [None, ""])
def test_empty_values_only_work_with_equality(op: str, value: Any) -> None:
    with pytest.raises(QueryError, match="empty value"):
        where_from_dict(cond("Title", op, value))


def test_empty_values_match_empty_fields_when_explicit() -> None:
    assert compiled(cond("Title", "==", None)) == "{6.EX.''}"
    assert compiled(cond("Due", "!=", None)) == "{8.XEX.''}"


@pytest.mark.parametrize("value", [" ", "\t\n", "   "])
def test_whitespace_values_are_refused(value: str) -> None:
    with pytest.raises(QueryError, match="whitespace"):
        where_from_dict(cond("Title", "==", value))


@pytest.mark.parametrize(
    "value",
    ["a{b", "a}b", "_FID_6", " _fid_6", "x _FID_6", float("nan"), float("inf"), 10**400],
)
def test_values_quickbase_would_misread_are_refused(value: Any) -> None:
    with pytest.raises(QueryError):
        where_from_dict(cond("Title", "==", value))


def test_injection_stays_inside_the_value() -> None:
    with pytest.raises(QueryError):
        where_from_dict(cond("Title", "==", "zz'}OR{3.GT.'0"))
    assert compiled(cond("Title", "==", "it's")) == "{6.EX.'it\\'s'}"
    assert compiled(cond("Title", "==", "a OR b")) == "{6.EX.'a or b'}"


@pytest.mark.parametrize("field", ["Owner", "Team"])
def test_curuser_is_refused_on_user_fields(field: str) -> None:
    with pytest.raises(QueryError, match="_curuser_"):
        compiled(cond(field, "==", "_curuser_"))


def test_user_fields_use_the_operators_that_match() -> None:
    assert compiled(cond("Owner", "==", "a@example.com")) == "{12.TV.'a@example.com'}"


def test_dates_must_be_real_dates() -> None:
    assert compiled(cond("Due", "==", "2026-12-01")) == "{8.EX.'2026-12-01'}"
    assert compiled(cond("Due", "<", "2026-12-01")) == "{8.BF.'2026-12-01'}"
    assert compiled(cond("Due", ">=", date(2026, 12, 1))) == "{8.OAF.'2026-12-01'}"


@pytest.mark.parametrize(
    "value",
    [
        "today",
        "1 day ago",
        "garbage",
        "12/01/2026",
        "2026-13-01",
        "20261201",
        "2026-12-01T00:00:00Z",
    ],
)
def test_other_text_on_a_date_is_refused(value: str) -> None:
    with pytest.raises(QueryError, match="YYYY-MM-DD"):
        compiled(cond("Due", "<", value))


def test_timestamps_become_milliseconds() -> None:
    expected = "{9.BF.'1796117400000'}"
    assert compiled(cond("Started", "<", "2026-12-01T09:30:00Z")) == expected
    assert compiled(cond("Started", "<", "2026-12-01T04:30:00-05:00")) == expected
    moment = datetime(2026, 12, 1, 9, 30, tzinfo=UTC)
    assert compiled(cond("Started", "<", moment)) == expected
    assert compiled(cond("Started", ">", "2026-12-01")) == "{9.AF.'2026-12-01'}"


@pytest.mark.parametrize(
    "value",
    ["2026-12-01T09:30:00", "yesterday", "1796117400000", "20261201T093000", "T", "2026-13-01"],
)
def test_other_text_on_a_timestamp_is_refused(value: str) -> None:
    with pytest.raises(QueryError):
        compiled(cond("Started", "<", value))


def test_a_datetime_needs_a_time_zone() -> None:
    with pytest.raises(QueryError, match="timezone"):
        where_from_dict(cond("Started", "<", datetime(2026, 12, 1)))


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (5, "5"),
        (1.5, "1.5"),
        ("5", "5"),
        (Decimal("2.50"), "2.50"),
        ("1e3", "1000"),
        (1e22, "10000000000000000000000"),
    ],
)
def test_numbers(value: Any, text: str) -> None:
    assert compiled(cond("Hours", ">", value)) == f"{{10.GT.'{text}'}}"


@pytest.mark.parametrize("field", ["Hours", "Price", "Record ID#"])
@pytest.mark.parametrize(
    "value", ["abc", "NaN", "Infinity", "sNaN", True, "1e999999999", "1e-999999999", "5 OR 6"]
)
def test_other_values_on_a_number_are_refused(field: str, value: Any) -> None:
    with pytest.raises(QueryError, match="number"):
        compiled(cond(field, "==", value))


def test_huge_decimals_are_refused_without_being_written_out() -> None:
    with pytest.raises(QueryError):
        where_from_dict(cond("Title", "==", Decimal("1e999999999")))


def test_checkboxes() -> None:
    assert compiled(cond("Done", "==", True)) == "{11.EX.'true'}"
    assert compiled(cond("Done", "!=", "FALSE")) == "{11.XEX.'false'}"
    for value in ("yes", 1):
        with pytest.raises(QueryError, match="true or false"):
            compiled(cond("Done", "==", value))
    with pytest.raises(QueryError, match="can only be compared"):
        compiled(cond("Done", "<", True))


@pytest.mark.parametrize("field", ["Due", "Started", "Hours"])
def test_text_operators_are_refused_on_other_types(field: str) -> None:
    with pytest.raises(QueryError, match="can only be compared"):
        compiled(cond(field, "contains", "2026"))


def test_unknown_fields_raise_with_a_suggestion() -> None:
    with pytest.raises(UnknownFieldError, match="Did you mean 'Status'"):
        compiled(cond("Statuss", "==", "x"))


def test_fields_limits_what_a_filter_can_use() -> None:
    allowed = ["Status", 6]
    assert compiled(cond("Status", "==", "x"), fields=allowed) == "{7.EX.'x'}"
    assert compiled(cond(6, "==", "x"), fields=allowed) == "{6.EX.'x'}"
    for field in ("Hours", 7, "Title", "status"):  # A label doesn't cover its ID, or the reverse.
        with pytest.raises(QueryError, match=r"where\.all\[1\]\.field"):
            where_from_dict(
                {"all": [cond("Status", "==", "x"), cond(field, "==", "x")]}, fields=allowed
            )


@pytest.mark.parametrize("fields", ["Status", [], ()])
def test_unusable_field_lists_are_refused(fields: Any) -> None:
    with pytest.raises(QueryError):
        where_from_dict(cond("Status", "==", "x"), fields=fields)
    with pytest.raises(QueryError):
        where_schema(fields=fields)


def test_nesting_is_limited_even_for_data_that_contains_itself() -> None:
    data: dict[str, Any] = {"all": []}
    data["all"].append(data)
    with pytest.raises(QueryError, match="nested"):
        where_from_dict(data)


def test_the_size_of_a_filter_is_limited() -> None:
    where_from_dict({"all": [cond("Title", "==", "x")] * 100})
    with pytest.raises(QueryError, match="at most 100"):
        where_from_dict({"all": [cond("Title", "==", "x")] * 101})
    with pytest.raises(QueryError, match="at most 100"):
        where_from_dict(cond("Title", "in", ["x"] * 101))
    with pytest.raises(QueryError, match="at most 100"):
        where_from_dict({"all": [cond("Title", "in", ["x"] * 60)] * 2})


def test_errors_say_where_and_never_repeat_the_values() -> None:
    secret = "s3cret-value"
    cases = [
        ({"all": [cond("Title", "==", "x"), cond("Title", secret, "x")]}, r"where\.all\[1\]\.op"),
        ({"any": [{secret: 1, "op": "==", "field": "Title"}]}, r"where\.any\[0\]"),
        (cond(secret, "==", ["x"]), "where"),
        (cond("Title", "in", [{secret: 1}]), "where"),
    ]
    for data, where in cases:
        with pytest.raises(QueryError, match=where) as caught:
            where_from_dict(data)
        assert secret not in str(caught.value)
    with pytest.raises(QueryError) as caught:
        compiled(cond("Due", "<", secret))
    assert secret not in str(caught.value)


# The schema


def test_the_schema_is_valid_and_can_go_anywhere_in_a_tool() -> None:
    for depth in range(6):
        schema = where_schema(max_depth=depth)
        Draft202012Validator.check_schema(schema)
        # A $ref would point at the root of the tool's schema, where nothing defines it.
        assert "$ref" not in json.dumps(schema)
    tool = {"type": "object", "properties": {"where": where_schema()}, "required": ["where"]}
    Draft202012Validator(tool).validate({"where": cond("Title", "==", "x")})


VALID = [
    cond("Title", "==", "x"),
    cond(6, "!=", None),
    cond("Hours", ">", 1.5),
    cond("Done", "==", True),
    cond("Status", "in", ["a", "b", None]),
    cond("Team", "HAS", "a@example.com"),
    {"all": [cond("Title", "==", "x")]},
    {"all": [cond("Title", "==", "x"), {"any": [cond("Hours", "<", 1), cond("Hours", ">", 9)]}]},
    {"any": [cond("Title", "==", "x"), {"all": [cond("Hours", ">", 1), cond("Hours", "<", 9)]}]},
]


@pytest.mark.parametrize("data", VALID)
def test_what_the_schema_allows_is_accepted(data: Any) -> None:
    Draft202012Validator(where_schema()).validate(data)
    assert compiled(data)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"field": "Title", "op": "=="},
        {**cond("Title", "==", "x"), "extra": 1},
        cond("Title", "like", "x"),
        cond("Title", "IR", "x"),
        {"all": []},
        {"all": [cond("Title", "==", "x")], "any": [cond("Title", "==", "x")]},
        {"not": cond("Title", "==", "x")},
    ],
)
def test_what_the_schema_refuses_is_refused(data: Any) -> None:
    assert not Draft202012Validator(where_schema()).is_valid(data)
    with pytest.raises(QueryError):
        where_from_dict(data)


def test_a_depth_limits_how_far_the_schema_nests() -> None:
    inner = {"any": [cond("Title", "==", "x")]}
    assert not Draft202012Validator(where_schema(max_depth=0)).is_valid(inner)
    assert Draft202012Validator(where_schema(max_depth=1)).is_valid(inner)
    nested = {"all": [inner]}
    assert not Draft202012Validator(where_schema(max_depth=1)).is_valid(nested)
    assert Draft202012Validator(where_schema(max_depth=2)).is_valid(nested)
    # Deeper filters are still accepted when they're sent anyway.
    assert compiled({"all": [{"any": [nested]}]})


def test_every_operator_in_the_schema_is_accepted() -> None:
    condition = where_schema(max_depth=0)
    for op in condition["properties"]["op"]["enum"]:
        value = ["x"] if op in ("in", "not_in") else "x"
        assert compiled(cond("Title", op, value))


def test_the_schema_can_list_fields() -> None:
    schema = where_schema(fields=["Title", "Status"], max_depth=0)
    assert schema["properties"]["field"] == {"enum": ["Status", "Title"]}
    field = field_from_api({"id": 6, "label": "Title", "fieldType": "text"})
    assert where_schema(fields=[field], max_depth=0)["properties"]["field"]["enum"] == ["Title"]
    assert not Draft202012Validator(schema).is_valid(cond("Hours", "==", 1))


@pytest.mark.parametrize("depth", [-1, 6, True])
def test_the_schema_depth_is_limited(depth: Any) -> None:
    with pytest.raises(QueryError, match="max_depth"):
        where_schema(max_depth=depth)


def test_the_default_schema_stays_small() -> None:
    # It's sent to the model with every request, so growth here costs every user of a tool.
    assert len(json.dumps(where_schema())) < 3000
    sizes = [len(json.dumps(where_schema(max_depth=depth))) for depth in range(1, 6)]
    steps = [after - before for before, after in itertools.pairwise(sizes)]
    assert max(steps) - min(steps) < 100  # Each level adds about the same, rather than doubling.


def test_each_schema_is_a_new_copy() -> None:
    first = where_schema()
    first["anyOf"][0]["properties"]["op"]["enum"].clear()
    assert where_schema()["anyOf"][0]["properties"]["op"]["enum"]
    # Parts that repeat within one schema don't share anything either.
    assert first["anyOf"][1]["properties"]["all"]["items"]["anyOf"][0]["properties"]["op"]["enum"]


# Randomized checks

TEXT = st.text(max_size=20).filter(
    lambda text: text.strip() and "{" not in text and "}" not in text
)
TEXT = TEXT.filter(lambda text: "_fid_" not in text.lower())
JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=10),
    lambda inner: (
        st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=5), inner, max_size=3)
    ),
    max_leaves=10,
)


@given(TEXT)
def test_values_compile_the_same_as_f(value: str) -> None:
    assert compiled(cond("Title", "==", value)) == compile_where(F("Title") == value, FIELDS)


@given(st.integers(min_value=-(10**18), max_value=10**18))
def test_numbers_compile_the_same_as_f(value: int) -> None:
    assert compiled(cond("Hours", ">", value)) == compile_where(F("Hours") > value, FIELDS)


@given(JSON_VALUES)
def test_any_json_raises_query_error_or_builds(data: Any) -> None:
    for candidate in (
        data,
        {"all": [data]},
        cond("Title", "==", data),
        cond("Hours", "in", [data]),
    ):
        with contextlib.suppress(QueryError, UnknownFieldError):
            compile_where(where_from_dict(candidate), FIELDS)


def test_fields_can_be_field_objects_in_both_halves() -> None:
    from pyquickbase import Field

    status = Field(
        id=7,
        label="Status",
        type="text",
        mode=None,
        required=False,
        unique=False,
        appears_by_default=True,
        choices=(),
        allow_new_choices=False,
        raw={},
    )
    schema = where_schema(fields=[status])
    assert schema["anyOf"][0]["properties"]["field"]["enum"] == ["Status"]
    where_from_dict({"field": "Status", "op": "==", "value": "x"}, fields=[status])
    with pytest.raises(QueryError, match="may use"):
        where_from_dict({"field": "Title", "op": "==", "value": "x"}, fields=[status])


def test_descriptions_are_added_and_must_be_for_offered_fields() -> None:
    schema = where_schema(fields=["Status"], descriptions={"Status": "Open or Done."})
    assert schema["description"].endswith(" Fields: Status: Open or Done.")
    with pytest.raises(QueryError, match="isn't one of the fields"):
        where_schema(fields=["Status"], descriptions={"Title": "x"})
    with pytest.raises(QueryError, match="needs fields"):
        where_schema(descriptions={"Title": "x"})
    with pytest.raises(QueryError, match="isn't empty"):
        where_schema(fields=["Status"], descriptions={"Status": " "})
