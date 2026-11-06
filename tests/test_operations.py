"""Tests for operation schemas and their checks, which do no network access."""

from datetime import UTC, date, datetime
from typing import Any, get_args

import pytest
from jsonschema import Draft202012Validator

from pyquickbase import (
    ConfigurationError,
    F,
    FileUpload,
    InvalidValueError,
    OperationName,
    Period,
    QueryError,
    ReadAccess,
    ReadRules,
    ReportInfo,
    UnknownFieldError,
    UnknownReportError,
    WriteAccess,
    WriteRules,
)
from pyquickbase._models import field_from_api
from pyquickbase._operations import Allowed, allowed_for, make_spec, unavailable

TASKS = "bqtasks"
FIELDS = [
    field_from_api(item)
    for item in [
        {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
        {"id": 6, "label": "Title", "fieldType": "text", "required": True},
        {
            "id": 7,
            "label": "Status",
            "fieldType": "text-multiple-choice",
            "properties": {"choices": ["Open", "Done"]},
        },
        {"id": 8, "label": "Hours", "fieldType": "numeric"},
        {"id": 9, "label": "Due Date", "fieldType": "date"},
        {"id": 10, "label": "Started", "fieldType": "timestamp"},
        {"id": 11, "label": "Done", "fieldType": "checkbox"},
        {"id": 12, "label": "Tags", "fieldType": "multitext"},
        {"id": 13, "label": "Attachment", "fieldType": "file"},
        {"id": 14, "label": "Doubled", "fieldType": "numeric", "mode": "formula"},
        {"id": 15, "label": "Owner", "fieldType": "user"},
    ]
]
REPORTS = [
    ReportInfo(id="1", name="List All", type="table", description="", incomplete=False, raw={}),
    ReportInfo(id="5", name="Totals", type="summary", description="", incomplete=False, raw={}),
    ReportInfo(id="6", name="Ask", type="table", description="", incomplete=True, raw={}),
]


def allowed(**kwargs: Any) -> Allowed:
    options: dict[str, Any] = {
        "table_id": TASKS,
        "key_field_id": 3,
        "read_rules": (),
        "write_rules": (),
        "read_only": False,
        "wanted": None,
        "wanted_reports": None,
    }
    options.update(kwargs)
    return allowed_for(FIELDS, REPORTS, **options)


def spec(name: OperationName, limits: Allowed | None = None, **kwargs: Any) -> Any:
    options: dict[str, Any] = {
        "descriptions": None,
        "report_descriptions": {},
        "max_records": None,
        "max_rows": 100,
        "formula": "[Hours] * 2" if name == "run_formula" else None,
    }
    options.update(kwargs)
    return make_spec(name, limits or allowed(), **options)


def labels(fields: Any) -> list[str]:
    return [item.label for item in fields]


def valid(schema: dict[str, Any], arguments: dict[str, Any]) -> bool:
    return Draft202012Validator(schema).is_valid(arguments)


# What's offered


def test_every_operation_has_a_valid_schema() -> None:
    for name in get_args(OperationName):
        schema = spec(name).schema
        Draft202012Validator.check_schema(schema)
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "$ref" not in str(schema)


def test_without_limits_every_visible_field_is_offered_where_it_can_be() -> None:
    limits = allowed()
    assert labels(limits.readable) == labels(FIELDS)
    # Formula, built-in, and user fields can't be written from JSON.
    assert labels(limits.insertable) == [
        "Title",
        "Status",
        "Hours",
        "Due Date",
        "Started",
        "Done",
        "Tags",
        "Attachment",
    ]
    assert labels(limits.downloadable) == ["Attachment"]
    assert [item.id for item in limits.reports] == ["1"]
    assert [item.id for item in limits.summary_reports] == ["5"]
    assert limits.can_delete


def test_read_rules_decide_what_is_read_filtered_and_sorted() -> None:
    rules = ReadRules(
        {
            TASKS: ReadAccess(
                fields=[6, 7], filter=[7], sort=False, files=[13], reports=["1"], max_records=30
            )
        }
    )
    limits = allowed(read_rules=(rules,))
    assert labels(limits.readable) == ["Record ID#", "Title", "Status"]
    assert labels(limits.filterable) == ["Record ID#", "Status"]
    assert labels(limits.sortable) == ["Record ID#"]
    assert labels(limits.downloadable) == ["Attachment"]
    assert [item.id for item in limits.reports] == ["1"]
    assert limits.summary_reports == ()
    assert limits.max_records == 30


def test_write_rules_and_read_only_decide_what_is_written() -> None:
    rules = WriteRules({TASKS: WriteAccess(insert=[6], update=True, delete_files=True)})
    limits = allowed(write_rules=(rules,))
    assert labels(limits.insertable) == ["Title"]
    assert "Hours" in labels(limits.updatable)
    assert not limits.can_delete
    assert labels(limits.file_deletable) == ["Attachment"]
    nothing = allowed(read_only=True)
    assert (nothing.insertable, nothing.updatable, nothing.can_delete) == ((), (), False)


def test_a_custom_key_field_means_no_insert() -> None:
    assert allowed(key_field_id=6).insertable == ()


def test_fields_narrows_everything() -> None:
    limits = allowed(wanted=["Status", 9])
    assert labels(limits.readable) == ["Status", "Due Date"]
    assert labels(limits.insertable) == ["Status", "Due Date"]
    with pytest.raises(UnknownFieldError):
        allowed(wanted=["Nope"])
    with pytest.raises(QueryError, match="single string"):
        allowed(wanted="Status")


def test_reports_can_be_chosen_by_id() -> None:
    assert [item.id for item in allowed(wanted_reports=["5"]).summary_reports] == ["5"]
    assert allowed(wanted_reports=["5"]).reports == ()
    with pytest.raises(UnknownReportError, match="'9'"):
        allowed(wanted_reports=["9"])


def test_what_cant_run_says_why() -> None:
    nothing = allowed(read_only=True)
    assert unavailable("insert", nothing, None) is not None
    assert unavailable("delete", nothing, None) is not None
    assert unavailable("query", nothing, None) is None
    assert unavailable("run_formula", nothing, None) == (
        "run_formula needs formula, the text your code chooses"
    )


def test_the_limit_is_the_lower_of_the_tool_and_the_rules() -> None:
    rules = ReadRules({TASKS: ReadAccess(fields=True, max_records=30)})
    schema = spec("query", allowed(read_rules=(rules,)), max_records=50).schema
    assert schema["properties"]["limit"]["maximum"] == 30
    assert spec("query", max_records=10).schema["properties"]["limit"]["maximum"] == 10
    assert spec("summarize").context.limit == 10_000


def test_descriptions_go_where_a_model_reads_them() -> None:
    notes = {"Status": "Open or Done.", 9: "When it's due."}
    schema = spec("query", descriptions=notes).schema
    assert schema["properties"]["where"]["description"].endswith(
        "Fields: Status: Open or Done. Due Date: When it's due."
    )
    record = spec("insert", descriptions=notes).schema["properties"]["records"]["items"]
    assert record["properties"]["Status"]["description"] == "Open or Done."
    assert record["properties"]["Due Date"]["description"].startswith("When it's due. A date")


def test_a_description_must_be_for_an_offered_field() -> None:
    with pytest.raises(UnknownFieldError, match="isn't one of this operation's fields"):
        spec("count", allowed(wanted=["Status"]), descriptions={"Title": "x"})
    with pytest.raises(QueryError, match="isn't empty"):
        spec("query", descriptions={"Title": " "})


def test_formula_is_only_for_run_formula() -> None:
    with pytest.raises(ConfigurationError, match="only for run_formula"):
        spec("query", formula="1")
    with pytest.raises(ConfigurationError, match="formula's text"):
        spec("run_formula", formula=" ")


# Checking arguments


def test_query_arguments_become_a_page_query() -> None:
    query = spec("query")
    arguments = {
        "where": {"field": "Status", "op": "==", "value": "Open"},
        "select": ["Title"],
        "order_by": [{"field": "Due Date", "descending": True}, {"field": "Title"}],
        "limit": 5,
        "skip": 10,
    }
    assert valid(query.schema, arguments)
    call = query.plan(arguments, F("Project") == "x")
    assert call.method == "query_page"
    assert call.arguments["select"] == ["Title"]
    assert call.arguments["order_by"] == [-9, 6]
    assert (call.arguments["top"], call.arguments["skip"]) == (5, 10)
    assert call.arguments["where"] is not None


def test_query_defaults() -> None:
    call = spec("query").plan({}, None)
    assert call.arguments == {"where": None, "top": 20, "skip": 0}


@pytest.mark.parametrize(
    ("arguments", "match"),
    [
        ({"selct": ["Title"]}, "doesn't take"),
        ({"select": ["Nope"]}, r"select\[0\] isn't a field"),
        ({"select": []}, "at least one"),
        ({"select": ["Title", "Title"]}, "more than once"),
        ({"order_by": [{"field": "Nope"}]}, r"order_by\[0\]\.field"),
        ({"order_by": [{"field": "Title", "descending": "yes"}]}, "true or false"),
        ({"order_by": [{"field": "Title"}] * 6}, "1 to 5"),
        ({"limit": 101}, "from 1 to 100"),
        ({"limit": True}, "whole number"),
        ({"skip": -1}, "from 0"),
        ({"where": "Status is Open"}, "must be an object"),
        ({"where": {"field": "Status", "op": "==", "value": "_FID_6"}}, "_FID_6"),
    ],
)
def test_bad_query_arguments_are_refused(arguments: dict[str, Any], match: str) -> None:
    query = spec("query")
    with pytest.raises(QueryError, match=match):
        query.plan(arguments, None)
    assert "Status is Open" not in str(match)


def test_the_arguments_must_be_an_object() -> None:
    with pytest.raises(QueryError, match="an object"):
        spec("count").plan([], None)


def test_a_filter_is_held_to_the_filterable_fields() -> None:
    rules = ReadRules({TASKS: ReadAccess(fields=True, filter=[7])})
    count = spec("count", allowed(read_rules=(rules,)))
    with pytest.raises(QueryError, match="may use"):
        count.plan({"where": {"field": "Title", "op": "==", "value": "x"}}, None)


def test_delete_needs_a_where() -> None:
    delete = spec("delete")
    assert not valid(delete.schema, {})
    with pytest.raises(QueryError, match="need where"):
        delete.plan({}, None)
    with pytest.raises(QueryError, match="required"):
        delete.plan({"where": None}, None)
    call = delete.plan({"where": {"field": "Status", "op": "==", "value": "Done"}}, None)
    assert call.method == "delete"


def test_summarize_arguments() -> None:
    summarize = spec("summarize")
    arguments = {
        "group_by": ["Status", {"field": "Due Date", "unit": "month"}],
        "total": ["Hours"],
        "maximum": ["Due Date"],
    }
    assert valid(summarize.schema, arguments)
    call = summarize.plan(arguments, None)
    assert call.arguments["group_by"] == ["Status", Period("Due Date", "month")]
    assert call.arguments["total"] == ["Hours"]
    assert call.arguments["max_records"] == 10_000
    for bad in (
        {"group_by": ["Tags"]},
        {"group_by": [{"field": "Title", "unit": "month"}]},
        {"group_by": [{"field": "Due Date", "unit": "decade"}]},
        {"total": ["Title"]},
    ):
        with pytest.raises(QueryError):
            summarize.plan(bad, None)


def test_insert_converts_json_values() -> None:
    insert = spec("insert")
    record = {
        "Title": "Pour slab",
        "Status": "Open",
        "Hours": 8,
        "Due Date": "2027-01-15",
        "Started": "2027-01-15T09:30:00Z",
        "Done": False,
        "Tags": ["a", "b"],
        "Attachment": {"file_name": "notes.txt", "text": "hello"},
    }
    assert valid(insert.schema, {"records": [record]})
    rows = insert.plan({"records": [record]}, None).arguments["rows"]
    assert rows[0]["Due Date"] == date(2027, 1, 15)
    assert rows[0]["Started"] == datetime(2027, 1, 15, 9, 30, tzinfo=UTC)
    upload = rows[0]["Attachment"]
    assert isinstance(upload, FileUpload)
    assert upload.name == "notes.txt"
    assert upload.data == b"hello"


def test_insert_writes_a_predecessor_as_text_and_never_offers_a_link() -> None:
    fields = [
        *FIELDS,
        field_from_api({"id": 16, "label": "Depends On", "fieldType": "predecessor"}),
        field_from_api({"id": 17, "label": "Card", "fieldType": "vCardButton"}),
    ]
    options: dict[str, Any] = {
        "table_id": TASKS,
        "key_field_id": 3,
        "read_rules": (),
        "write_rules": (),
        "read_only": False,
        "wanted": None,
        "wanted_reports": None,
    }
    insert = spec("insert", allowed_for(fields, REPORTS, **options))
    record = insert.schema["properties"]["records"]["items"]["properties"]
    assert record["Depends On"] == {"type": ["string", "null"]}
    assert "Card" not in record
    rows = insert.plan({"records": [{"Title": "a", "Depends On": "1;2"}]}, None).arguments["rows"]
    assert rows[0]["Depends On"] == "1;2"


def test_insert_writes_a_duration_as_whole_milliseconds() -> None:
    fields = [*FIELDS, field_from_api({"id": 18, "label": "Spent", "fieldType": "duration"})]
    options: dict[str, Any] = {
        "table_id": TASKS,
        "key_field_id": 3,
        "read_rules": (),
        "write_rules": (),
        "read_only": False,
        "wanted": None,
        "wanted_reports": None,
    }
    insert = spec("insert", allowed_for(fields, REPORTS, **options))
    record = insert.schema["properties"]["records"]["items"]["properties"]
    assert record["Spent"]["type"] == ["integer", "null"]
    assert "3600000" in record["Spent"]["description"]
    rows = insert.plan({"records": [{"Title": "a", "Spent": 3600000.0}]}, None).arguments["rows"]
    assert rows[0]["Spent"] == 3600000
    for bad in (1.5, "1:30:00", True):
        with pytest.raises(InvalidValueError, match="whole milliseconds"):
            insert.plan({"records": [{"Title": "a", "Spent": bad}]}, None)


def test_insert_schema_requires_required_fields() -> None:
    record = spec("insert").schema["properties"]["records"]["items"]
    assert record["required"] == ["Title"]
    assert record["properties"]["Status"]["enum"] == ["Open", "Done", None]


@pytest.mark.parametrize(
    ("record", "match"),
    [
        ({"Doubled": 1}, "may not write"),
        ({"Owner": "a@example.com"}, "may not write"),
        ({"Hours": "8"}, "must be a number"),
        ({"Hours": True}, "must be a number"),
        ({"Hours": float("nan")}, "finite"),
        ({"Due Date": "today"}, "YYYY-MM-DD"),
        ({"Due Date": "2027-02-30"}, "YYYY-MM-DD"),
        ({"Started": "2027-01-15T09:30:00"}, "time zone"),
        ({"Started": "2027-01-15"}, "time zone"),
        ({"Done": "no"}, "true or false"),
        ({"Status": "Maybe"}, "choices"),
        ({"Tags": "a"}, "list of text"),
        ({"Attachment": None}, "file_name"),
        ({"Attachment": {"file_name": "", "text": "x"}}, "file_name"),
        ({"Title": 5}, "must be text"),
    ],
)
def test_bad_values_are_refused_without_repeating_them(record: dict[str, Any], match: str) -> None:
    with pytest.raises(InvalidValueError, match=match) as caught:
        spec("insert").plan({"records": [record]}, None)
    assert "a@example.com" not in str(caught.value)
    assert "today" not in str(caught.value)


def test_a_field_it_may_read_but_not_write_is_named() -> None:
    # The label is one the tool offers to read, so naming it reveals nothing.
    with pytest.raises(InvalidValueError, match=r"records\[0\]\.Doubled can be read"):
        spec("insert").plan({"records": [{"Doubled": 1}]}, None)
    # A field it can't read, or one that doesn't exist, isn't named or confirmed.
    hidden = allowed(read_rules=(ReadRules({TASKS: ReadAccess(fields=[3, 6])}),))
    for key in ("Doubled", "Secret"):
        with pytest.raises(InvalidValueError) as caught:
            spec("insert", hidden).plan({"records": [{key: 1}]}, None)
        assert key not in str(caught.value)
        assert "has a field this tool may not write" in str(caught.value)


def test_null_clears_a_field_except_a_file() -> None:
    rows = spec("insert").plan({"records": [{"Title": None, "Hours": None}]}, None).arguments
    assert rows["rows"] == [{"Title": None, "Hours": None}]


def test_records_are_limited_and_checked() -> None:
    insert = spec("insert", max_rows=2)
    with pytest.raises(InvalidValueError, match="at most 2"):
        insert.plan({"records": [{"Title": "x"}] * 3}, None)
    with pytest.raises(InvalidValueError, match="at least one"):
        insert.plan({"records": []}, None)
    with pytest.raises(InvalidValueError, match=r"records\[0\] must be an object"):
        insert.plan({"records": [{}]}, None)


def test_update_needs_a_record_id_and_a_change() -> None:
    update = spec("update")
    call = update.plan({"records": [{"Record ID#": 4, "Status": "Done"}]}, None)
    assert call.record_ids == (4,)
    assert call.arguments["rows"] == [{"Record ID#": 4, "Status": "Done"}]
    for bad, match in (
        ({"Status": "Done"}, "needs Record ID#"),
        ({"Record ID#": 4}, "doesn't change"),
        ({"Record ID#": "4", "Status": "Done"}, "positive whole number"),
    ):
        with pytest.raises(InvalidValueError, match=match):
            update.plan({"records": [bad]}, None)


def test_reports_are_chosen_from_the_list_and_take_no_scope() -> None:
    run = spec("run_report")
    assert run.schema["properties"]["report"]["enum"] == ["1"]
    call = run.plan({"report": "1", "limit": 3}, None)
    assert (call.method, call.report_id, call.arguments) == ("run_page", "1", {"top": 3, "skip": 0})
    with pytest.raises(QueryError, match="isn't a report this tool may run"):
        run.plan({"report": "5"}, None)
    with pytest.raises(QueryError, match="can't take a scope"):
        run.plan({"report": "1"}, F("Project") == "x")
    summary = spec("summary_report", report_descriptions={"5": "Hours by status."})
    assert "5: Totals, Hours by status." in summary.schema["properties"]["report"]["description"]
    assert summary.plan({"report": "5"}, None).arguments == {"limit": 20}


@pytest.mark.parametrize("report", ["1", 1, "List All", "list all"])
def test_a_report_can_be_named_by_its_id_as_a_number_or_by_its_name(report: Any) -> None:
    # Models send a numeric-looking ID as a number, and often use the name the schema shows.
    assert spec("run_report").plan({"report": report}, None).report_id == "1"
    assert "or its name" in spec("run_report").schema["properties"]["report"]["description"]


@pytest.mark.parametrize("report", [True, 1.0, "2", 2, "List", "Ask", None, ["1"]])
def test_anything_else_is_refused_without_being_repeated(report: Any) -> None:
    with pytest.raises(QueryError, match="isn't a report this tool may run") as caught:
        spec("run_report").plan({"report": report}, None)
    assert "Ask" not in str(caught.value)


def test_a_report_of_the_other_kind_says_which_operation_runs_it() -> None:
    with pytest.raises(
        QueryError, match="it's a summary or chart report, so use the summary_report operation"
    ):
        spec("run_report").plan({"report": "Totals"}, None)
    with pytest.raises(QueryError, match="it's a table report, so use the run_report operation"):
        spec("summary_report").plan({"report": 1}, None)


def test_a_name_shared_by_two_reports_needs_the_id() -> None:
    twins = [
        ReportInfo(id=str(n), name="Same", type="table", description="", incomplete=False, raw={})
        for n in (1, 2)
    ]
    run = make_spec(
        "run_report",
        allowed_for(
            FIELDS,
            twins,
            table_id=TASKS,
            key_field_id=3,
            read_rules=(),
            write_rules=(),
            read_only=False,
            wanted=None,
            wanted_reports=None,
        ),
        descriptions=None,
        report_descriptions={},
        max_records=None,
        max_rows=100,
        formula=None,
    )
    with pytest.raises(QueryError, match="Several reports"):
        run.plan({"report": "same"}, None)
    assert run.plan({"report": "2"}, None).report_id == "2"


def test_a_formula_is_fixed_by_code_and_the_model_picks_the_record() -> None:
    formula = spec("run_formula")
    assert list(formula.schema["properties"]) == ["record_id"]
    call = formula.plan({"record_id": 2}, None)
    assert call.arguments == {"formula": "[Hours] * 2", "record_id": 2}
    assert call.record_ids == (2,)
    with pytest.raises(QueryError, match="doesn't take"):
        formula.plan({"formula": "GetRecords(...)"}, None)
    with pytest.raises(QueryError, match="record_id is required"):
        formula.plan({}, F("Project") == "x")


def test_files() -> None:
    download = spec("download_file")
    call = download.plan({"record_id": 1, "field": "Attachment"}, None)
    assert call.arguments == {"record_id": 1, "field": "Attachment"}
    assert (
        download.plan({"record_id": 1, "field": "Attachment", "version": 2}, None).arguments[
            "version"
        ]
        == 2
    )
    with pytest.raises(QueryError, match="file field"):
        download.plan({"record_id": 1, "field": "Title"}, None)
    delete = spec("delete_file_version")
    with pytest.raises(QueryError, match="need version"):
        delete.plan({"record_id": 1, "field": "Attachment"}, None)


def test_what_plan_accepts_matches_the_schema() -> None:
    examples: dict[OperationName, dict[str, Any]] = {
        "query": {"where": {"all": [{"field": "Hours", "op": ">", "value": 2}]}, "limit": 1},
        "count": {"where": {"field": "Done", "op": "==", "value": True}},
        "preview_delete": {"where": {"field": "Status", "op": "==", "value": "Done"}},
        "preview_update": {"records": [{"Record ID#": 1, "Hours": 3}]},
        "download_file": {"record_id": 1, "field": "Attachment"},
        "run_formula": {"record_id": 1},
        "summary_report": {"report": "5", "limit": 2},
    }
    for name, arguments in examples.items():
        operation = spec(name)
        assert valid(operation.schema, arguments), name
        operation.plan(arguments, None)
