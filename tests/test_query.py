"""Tests for query building, models, and the schema cache, which do no network access."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from pyquickbase import (
    F,
    Field,
    InvalidResponseError,
    QueryError,
    UnknownFieldError,
    WhereCondition,
    parse_where,
)
from pyquickbase._models import (
    FieldIndex,
    app_info_from_api,
    field_from_api,
    page_from_api,
    parse,
    relationship_from_api,
    table_info_from_api,
)
from pyquickbase._query import build_query_body, compile_where, format_value, validate_where
from pyquickbase._schema import SchemaCache


def field(field_id: int, label: str, field_type: str = "text", **extra: Any) -> Field:
    return field_from_api({"id": field_id, "label": label, "fieldType": field_type, **extra})


FIELDS = [
    field(3, "Record ID#", "recordid"),
    field(6, "Title", appearsByDefault=True),
    field(7, "Status", appearsByDefault=True),
    field(9, "Due Date", "date"),
    field(20, "-Odd Label"),
]
INDEX = FieldIndex(FIELDS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (True, "true"),
        (False, "false"),
        (5, "5"),
        (1.5, "1.5"),
        (Decimal("2.50"), "2.50"),
        (date(2026, 1, 2), "2026-01-02"),
        (datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC), "1000"),
        ("plain", "plain"),
        ("O'Brien", "O\\'Brien"),
        ("end'", "end\\'"),
        ("c\\d", "c\\\\d"),
    ],
)
def test_format_value(value: Any, expected: str) -> None:
    assert format_value(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "zz'}OR{3.GT.'0",  # matched every record when tested against the live API
        "zz' }OR{3.GT.'0",
        "zz')OR{3.GT.'0",
        "}",
        "{",
    ],
)
def test_values_that_could_break_out_of_a_condition_are_rejected(value: str) -> None:
    with pytest.raises(QueryError, match="containing"):
        format_value(value)


def test_format_value_rejects_naive_datetimes_and_unknown_types() -> None:
    with pytest.raises(QueryError, match="timezone"):
        format_value(datetime(2026, 1, 2))
    with pytest.raises(QueryError, match="type object"):
        format_value(object())


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (F("Status") == "Done", "{7.EX.'Done'}"),
        (F("Status") != "Done", "{7.XEX.'Done'}"),
        (F(7) == "Done", "{7.EX.'Done'}"),
        (F("Title") == None, "{6.EX.''}"),  # noqa: E711
        (F("Title") < 5, "{6.LT.'5'}"),
        (F("Title") <= 5, "{6.LTE.'5'}"),
        (F("Title") > 5, "{6.GT.'5'}"),
        (F("Title") >= 5, "{6.GTE.'5'}"),
        (F("Due Date") < date(2026, 1, 2), "{9.BF.'2026-01-02'}"),
        (F("Due Date") <= date(2026, 1, 2), "{9.OBF.'2026-01-02'}"),
        (F("Due Date") > date(2026, 1, 2), "{9.AF.'2026-01-02'}"),
        (F("Due Date") >= date(2026, 1, 2), "{9.OAF.'2026-01-02'}"),
        (F("Title").contains("x"), "{6.CT.'x'}"),
        (F("Title").not_contains("x"), "{6.XCT.'x'}"),
        (F("Title").starts_with("x"), "{6.SW.'x'}"),
        (F("Title").not_starts_with("x"), "{6.XSW.'x'}"),
        (F("Title").op("HAS", "x"), "{6.HAS.'x'}"),
        (F("Title").is_in(["x"]), "{6.EX.'x'}"),
        (F("Title").is_in(["x", "y"]), "{6.EX.'x'}OR{6.EX.'y'}"),
        ((F(6) == "a") & (F(7) == "b") & (F(9) == "c"), "{6.EX.'a'}AND{7.EX.'b'}AND{9.EX.'c'}"),
        ((F(6) == "a") | (F(7) == "b"), "{6.EX.'a'}OR{7.EX.'b'}"),
        (((F(6) == "a") | (F(7) == "b")) & (F(9) == "c"), "({6.EX.'a'}OR{7.EX.'b'})AND{9.EX.'c'}"),
    ],
)
def test_expressions_compile(expression: Any, expected: str) -> None:
    assert compile_where(expression, INDEX) == expected


def test_compiled_expressions_pass_the_string_validator() -> None:
    where = ((F(6) == "O'Brien") | (F(7) != "c\\d")) & (F(9) > date(2026, 1, 2))
    compiled = compile_where(where, INDEX)
    assert validate_where(compiled, INDEX) == compiled


@pytest.mark.parametrize(
    "use",
    [
        lambda: bool(F(6) == "a"),
        lambda: (F(6) == "a") and (F(7) == "b"),
        lambda: 1 < F(6) < 5,
    ],
)
def test_python_boolean_logic_is_rejected_rather_than_dropping_a_condition(use: Any) -> None:
    with pytest.raises(QueryError, match="Combine conditions"):
        use()


def test_invalid_builder_input() -> None:
    with pytest.raises(QueryError, match="field label"):
        F(True)
    with pytest.raises(QueryError, match="field label"):
        F(1.5)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    with pytest.raises(QueryError, match="at least one"):
        F(6).is_in([])
    with pytest.raises(QueryError, match="uppercase"):
        F(6).op("ex", "x")  # lowercase acts like CT in the live API
    with pytest.raises(QueryError, match="other conditions"):
        (F(6) == "a") & "b"  # type: ignore[operator]  # pyright: ignore[reportOperatorIssue]
    with pytest.raises(TypeError, match="unhashable"):
        hash(F(6))
    with pytest.raises(QueryError, match="must be a condition"):
        compile_where(5, INDEX)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def test_expression_base_requires_a_compile_method() -> None:
    from pyquickbase._query import Expression

    with pytest.raises(NotImplementedError):
        Expression().compile(INDEX)


def test_unknown_field_in_expression() -> None:
    with pytest.raises(UnknownFieldError, match="Did you mean 'Status'"):
        compile_where(F("Statu") == "x", INDEX)


@pytest.mark.parametrize(
    "where",
    [
        "{6.EX.'x'}",
        "  {6.EX.'x'}  ",
        "{6.EX.x}",
        "{3.EX.5}",
        "{6.EX.''}",
        "{6.EX.}",
        "{6.EX.'O\\'Brien'}",
        "{6.EX.'c\\\\d'}",
        "{6.EX.'a'}AND{7.EX.'b'}",
        "{6.EX.'a'} OR {7.EX.'b'}",
        "({6.EX.'a'}OR{7.EX.'b'})AND{9.BF.'today'}",
        "(({6.EX.'a'}))",
    ],
)
def test_validate_where_accepts(where: str) -> None:
    assert validate_where(where, INDEX) == where.strip()


@pytest.mark.parametrize(
    ("where", "problem"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("garbage", "expected a condition"),
        ("{6.ex.'x'}", "uppercase"),  # acts like CT in the live API
        ("{6.NOPE.'x'}", "uppercase"),  # acts like CT in the live API
        ("{999.EX.'x'}", "field 999 doesn't exist"),  # matches nothing in the live API
        ("{'Title'.EX.'x'}", "expected a condition"),  # matches nothing in the live API
        ("{6.EX.'x'}AND", "ends with AND/OR"),  # accepted by the live API
        ("{6.EX.'x'}and{7.EX.'y'}", "expected AND or OR"),
        ("{6.EX.'x'}{7.EX.'y'}", "expected AND or OR"),
        ("{6.EX.'A' OR 6.EX.'B'}", "expected a condition"),  # a missing brace
        ("{6.EX.'O'Brien'}", "expected a condition"),
        ("{6.EX.'zz\\'}", "expected a condition"),
        ("{6.EX.'x'", "expected a condition"),
        ("({6.EX.'x'}", "unbalanced '('"),
        ("{6.EX.'x'})", "unbalanced ')'"),
        ("()", "expected a condition"),
        ("{6.EX.'x'}ANDY{7.EX.'y'}", "expected AND or OR"),
    ],
)
def test_validate_where_rejects(where: str, problem: str) -> None:
    with pytest.raises(QueryError, match=re.escape(problem)):
        validate_where(where, INDEX)


def test_parse_where_returns_unescaped_conditions() -> None:
    where = " ({6.EX.'O\\'Brien'}OR{7.CT.c}) AND {9.BF.'a\\\\b'} "
    assert parse_where(where, FIELDS) == [
        WhereCondition(6, "EX", "O'Brien"),
        WhereCondition(7, "CT", "c"),
        WhereCondition(9, "BF", "a\\b"),
    ]


def test_parse_where_rejects_what_validation_rejects() -> None:
    with pytest.raises(QueryError, match="uppercase"):
        parse_where("{6.ex.'x'}", FIELDS)


def build(**kwargs: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {"where": None, "select": None, "order_by": None, **kwargs}
    return build_query_body("tbl", FIELDS, **arguments)


def test_body_defaults_to_default_fields_and_record_id_order() -> None:
    assert build() == {
        "from": "tbl",
        "select": [3, 6, 7],
        "sortBy": [{"fieldId": 3, "order": "ASC"}],
    }


@pytest.mark.parametrize(
    ("select", "expected"),
    [
        ("Title", [3, 6]),
        (9, [3, 9]),
        (["Title", 7], [3, 6, 7]),
        (["Title", "Title", "Record ID#"], [3, 6]),
    ],
)
def test_body_select(select: Any, expected: list[int]) -> None:
    assert build(select=select)["select"] == expected


@pytest.mark.parametrize(
    ("order_by", "expected"),
    [
        ("Title", [(6, "ASC"), (3, "ASC")]),
        ("-Title", [(6, "DESC"), (3, "ASC")]),
        (-6, [(6, "DESC"), (3, "ASC")]),
        (6, [(6, "ASC"), (3, "ASC")]),
        ("-Odd Label", [(20, "ASC"), (3, "ASC")]),
        (["-Due Date", "Title"], [(9, "DESC"), (6, "ASC"), (3, "ASC")]),
        ("-Record ID#", [(3, "DESC")]),
    ],
)
def test_body_sort(order_by: Any, expected: list[tuple[int, str]]) -> None:
    sort = build(order_by=order_by)["sortBy"]
    assert [(item["fieldId"], item["order"]) for item in sort] == expected


def test_body_where() -> None:
    assert build(where=F("Title") == "x")["where"] == "{6.EX.'x'}"
    assert build(where=" {6.EX.'x'} ")["where"] == "{6.EX.'x'}"


def test_field_index() -> None:
    assert INDEX.resolve(6).label == "Title"
    assert INDEX.resolve("Title").id == 6
    with pytest.raises(UnknownFieldError, match="ID 99"):
        INDEX.resolve(99)
    with pytest.raises(UnknownFieldError, match="ID True"):
        INDEX.resolve(True)
    with pytest.raises(
        UnknownFieldError, match=r"no field labeled 'zzzz', or this token can't see it\.$"
    ):
        INDEX.resolve("zzzz")
    duplicates = FieldIndex([field(1, "Same"), field(2, "Same")])
    with pytest.raises(UnknownFieldError, match=r"Use an ID: \[1, 2\]"):
        duplicates.resolve("Same")


def page_payload(**metadata: Any) -> dict[str, Any]:
    return {
        "fields": [
            {"id": 3, "label": "Record ID#", "type": "recordid"},
            {"id": 6, "label": "Title", "type": "text"},
            {"id": 9, "label": "Due Date", "type": "date"},
            {"id": 1, "label": "Date Created", "type": "timestamp"},
            {"id": 8, "label": "Not Returned", "type": "text"},
        ],
        "data": [
            {
                "3": {"value": 1},
                "6": {"value": "Excavation"},
                "9": {"value": "2026-10-18"},
                "1": {"value": "2026-10-21T11:49:56Z"},
            }
        ],
        "metadata": {"totalRecords": 3, "skip": 0, **metadata},
    }


def test_record_access_and_conversion() -> None:
    record = page_from_api(page_payload()).records[0]
    assert record.id == 1
    assert record["Title"] == record[6] == "Excavation"
    assert record["Due Date"] == date(2026, 10, 18)
    assert record["Date Created"] == datetime(2026, 10, 21, 11, 49, 56, tzinfo=UTC)
    assert list(record) == ["Record ID#", "Title", "Due Date", "Date Created"]
    assert len(record) == 4
    assert dict(record)["Title"] == "Excavation"
    assert "Not Returned" not in record
    with pytest.raises(KeyError):
        record["Nope"]


def test_record_repr_hides_values() -> None:
    record = page_from_api(page_payload()).records[0]
    assert repr(record) == "Record(id=1, fields=4)"


def test_unexpected_date_formats_stay_as_returned() -> None:
    payload = page_payload()
    payload["data"][0]["9"]["value"] = "not a date"
    payload["data"][0]["1"]["value"] = None
    record = page_from_api(payload).records[0]
    assert record["Due Date"] == "not a date"
    assert record["Date Created"] is None


def test_empty_dates_are_none() -> None:
    payload = page_payload()
    payload["data"][0]["9"]["value"] = ""
    payload["data"][0]["1"]["value"] = ""
    payload["data"][0]["6"]["value"] = ""
    record = page_from_api(payload).records[0]
    assert record["Due Date"] is None
    assert record["Date Created"] is None
    assert record["Title"] == ""


def test_page_position() -> None:
    page = page_from_api(page_payload(skip=1))
    assert (page.skip, page.next_skip, page.total, page.has_more) == (1, 2, 3, True)
    assert not page_from_api(page_payload(skip=2)).has_more


def test_model_parsing() -> None:
    app = app_info_from_api({"id": "a", "name": "App", "timeZone": "UTC", "dateFormat": "YYYY"})
    assert (app.description, app.time_zone, app.date_format) == ("", "UTC", "YYYY")
    table = table_info_from_api({"id": "t", "name": "T", "description": None, "keyFieldId": 3})
    assert (table.description, table.key_field_id) == ("", 3)
    assert table_info_from_api({"id": "t", "name": "T"}).key_field_id is None
    choice = field(7, "S", properties={"choices": ["A"], "allowNewChoices": True}, mode="lookup")
    assert (choice.choices, choice.allow_new_choices, choice.mode) == (("A",), True, "lookup")
    assert field(1, "P", mode="").mode is None
    link = relationship_from_api(
        {
            "id": 12,
            "parentTableId": "p",
            "childTableId": "c",
            "foreignKeyField": {"id": 12},
            "lookupFields": [{"id": 13}],
            "isCrossApp": False,
        }
    )
    assert (link.foreign_key_field_id, link.lookup_field_ids) == (12, (13,))
    bare = relationship_from_api({"id": 1, "parentTableId": "p", "childTableId": "c"})
    assert (bare.foreign_key_field_id, bare.lookup_field_ids) == (None, ())


@pytest.mark.parametrize("payload", [None, {}, [1], {"fields": 5}])
def test_parse_reports_unexpected_shapes(payload: Any) -> None:
    with pytest.raises(InvalidResponseError, match="unexpected records query"):
        parse(page_from_api, payload, "records query")


def test_schema_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    monkeypatch.setattr("pyquickbase._schema.time.monotonic", lambda: now[0])
    cache = SchemaCache(10)
    assert cache.get("a") is None
    cache.set("a", 1)
    assert cache.get("a") == 1
    now[0] = 110.0
    assert cache.get("a") is None
    cache.set("a", 1)
    cache.clear()
    assert cache.get("a") is None
    disabled = SchemaCache(0)
    disabled.set("a", 1)
    assert disabled.get("a") is None
