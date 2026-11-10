# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""What an agent's tool refuses before anything reaches Quickbase. Mistakes here are a model's."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    ConfigurationError,
    F,
    InvalidQueryError,
    InvalidValueError,
    Quickbase,
    allow,
    tables,
)

from ..policy_helpers import bind

APP = "bq1234567"
TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid", "appearsByDefault": True},
    {"id": 6, "label": "Title", "fieldType": "text", "appearsByDefault": True},
    {"id": 7, "label": "Due", "fieldType": "date", "appearsByDefault": True},
    {"id": 8, "label": "Started", "fieldType": "timestamp", "appearsByDefault": True},
    {"id": 9, "label": "At", "fieldType": "timeofday", "appearsByDefault": True},
    {"id": 10, "label": "Hours", "fieldType": "numeric", "appearsByDefault": True},
]


class Fake:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.reports = [{"id": "1", "name": "List All", "type": "table"}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": 3}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        if path == "/reports":
            return httpx.Response(200, json=self.reports)
        if path.endswith("/events"):
            return httpx.Response(200, json=[])
        self.sent.append(f"{request.method} {path}")
        body = json.loads(request.content) if request.content else {}
        if path == "/records/query":
            meta = {"totalRecords": 0, "numRecords": 0, "skip": 0}
            return httpx.Response(200, json={"fields": [], "data": [], "metadata": meta})
        meta = {"createdRecordIds": [1], "updatedRecordIds": [], "unchangedRecordIds": []}
        assert body
        return httpx.Response(200, json={"metadata": meta})


def client(fake: Fake, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, **kwargs)


@pytest.mark.parametrize(
    ("name", "arguments", "error", "match"),
    [
        ("summarize", {"group_by": "Due"}, InvalidQueryError, "group_by must be a list"),
        ("summarize", {"group_by": []}, InvalidQueryError, "group_by must be a list"),
        ("summarize", {"group_by": [{"field": "Due"}]}, InvalidQueryError, "must be a label, or"),
        ("summarize", {"compare_periods": "yes"}, InvalidQueryError, "true or false"),
        ("summarize", {"order_by": [{"nope": 1}]}, InvalidQueryError, "order_by\\[0\\] must be"),
        ("insert", {"records": [{"At": "noon"}]}, InvalidValueError, "time of day"),
        ("insert", {"records": [{"At": 9}]}, InvalidValueError, "time of day"),
        ("insert", {"records": [{"Started": "2026-13-45T09:00:00Z"}]}, InvalidValueError,
         "ISO 8601"),
        ("insert", {"records": [{"Started": "2026-12-01T09:00:00"}]}, InvalidValueError,
         "ISO 8601"),
    ],
    ids=["group-text", "group-empty", "period-keys", "compare", "order", "time-text", "time-number",
         "stamp-invalid", "stamp-no-zone"],
)  # fmt: skip
def test_arguments_a_tool_cant_use_send_nothing(
    name: Any, arguments: dict[str, Any], error: type[Exception], match: str
) -> None:
    fake = Fake()
    with client(fake) as qb:
        operation = qb.app(APP).table("Tasks").operation(name)
        with pytest.raises(error, match=match):
            operation.run(arguments)
    assert fake.sent == []


def test_a_time_of_day_reaches_quickbase_as_written() -> None:
    fake = Fake()
    with client(fake) as qb:
        insert = qb.app(APP).table("Tasks").operation("insert")
        assert (
            "HH:MM"
            in insert.input_schema["properties"]["records"]["items"]["properties"]["At"][
                "description"
            ]
        )
        insert.run({"records": [{"At": "09:30"}]})
    assert fake.sent == ["POST /records"]


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"field_descriptions": 5}, "descriptions takes a dictionary"),
        ({"fields": []}, "fields can't be empty"),
    ],
)
def test_a_tool_that_cant_be_made_says_why(kwargs: dict[str, Any], match: str) -> None:
    with client(Fake()) as qb:
        with pytest.raises(InvalidQueryError, match=match):
            qb.app(APP).table("Tasks").operation("query", **kwargs)


def test_a_report_tool_takes_a_list_of_reports() -> None:
    with client(Fake()) as qb:
        with pytest.raises(ConfigurationError, match="not a single string"):
            qb.app(APP).table("Tasks").operation("run_report", reports="12")


def test_arguments_must_be_an_object() -> None:
    with client(Fake()) as qb:
        operation = qb.app(APP).table("Tasks").operation("query")
        with pytest.raises(InvalidQueryError, match="must be an object"):
            operation.run("Title")  # type: ignore[arg-type]
        schema = qb.app(APP).operation("preview_schema_changes")
        with pytest.raises(InvalidValueError, match="must be an object"):
            schema.run("x")  # type: ignore[arg-type]


def test_a_scope_must_be_built_with_f() -> None:
    with client(Fake()) as qb:
        with pytest.raises(InvalidQueryError, match="built with F"):
            (
                qb.app(APP)
                .table("Tasks")
                .operation(
                    "insert",
                    scope="{6.EX.'x'}",  # type: ignore[arg-type]
                )
            )


def test_an_update_tool_says_what_its_scope_keeps_it_to() -> None:
    with client(Fake()) as qb:
        update = qb.app(APP).table("Tasks").operation("update", scope=F("Title") == "x")
    assert (
        "only changes records that match its scope"
        in (update.input_schema["properties"]["records"]["description"])
    )


def test_a_tool_offers_nothing_the_policy_doesnt_allow() -> None:
    policy = bind(allow.insert(TASKS, [6]), tables={TASKS: ("Tasks", FIELDS)}, app_id=APP)
    with client(Fake(), policy=policy) as qb:
        operation = qb.app(APP).table("Tasks").operation("insert")
    assert list(operation.input_schema["properties"]["records"]["items"]["properties"]) == ["Title"]


def test_comparing_periods_with_nothing_to_compare_is_empty() -> None:
    with client(Fake()) as qb:
        summarize = qb.app(APP).table("Tasks").operation("summarize")
        found = summarize.run(
            {"group_by": [{"field": "Due", "unit": "month"}], "compare_periods": True}
        )
    assert found.rows == ()
    assert found.total == 0


def test_a_tool_shows_only_fields_a_policy_lets_it_filter_select_and_sort() -> None:
    policy = bind(allow.insert(TASKS, [6]), tables={TASKS: ("Tasks", FIELDS)}, app_id=APP)
    with client(Fake(), policy=policy) as qb:
        search = qb.app(APP).table("Tasks").operation("query")
    assert not {"where", "select", "order_by"} & set(search.input_schema["properties"])


def test_describe_schema_takes_a_list_of_table_names() -> None:
    with client(Fake()) as qb:
        describe = qb.app(APP).operation("describe_schema")
        with pytest.raises(InvalidValueError, match="must be a list of table names"):
            describe.run({"tables": "Tasks"})


@pytest.mark.parametrize("max_changes", [0, True, 1.5])
def test_max_changes_must_be_a_positive_whole_number(max_changes: Any) -> None:
    with client(Fake()) as qb:
        with pytest.raises(ConfigurationError, match="positive whole number"):
            qb.app(APP).operation("preview_schema_changes", max_changes=max_changes)


def test_a_client_that_can_create_tables_is_offered_that() -> None:
    policy = bind(
        allow.read(tables.all()), allow.create_tables(), tables={TASKS: ("Tasks", FIELDS)},
        app_id=APP,
    )  # fmt: skip
    with client(Fake(), policy=policy) as qb:
        plan = qb.app(APP).operation("preview_schema_changes")
    assert "create_table" in str(plan.input_schema)


def test_help_text_in_a_tools_change_must_be_text() -> None:
    change = {"action": "update_field", "table": "Tasks", "field": "Title", "help_text": 5}
    with client(Fake()) as qb:
        plan = qb.app(APP).operation("preview_schema_changes")
        with pytest.raises(InvalidValueError, match="must be text"):
            plan.run({"changes": [change]})


def test_reprs_name_what_they_are() -> None:
    with client(Fake()) as qb:
        app = qb.app(APP)
        assert repr(app.table("Tasks").operation("query")).startswith(
            "TableOperation('query', table="
        )
        assert repr(app.operation("describe_schema")).startswith(
            f"AppOperation('describe_schema', app={APP!r})"
        )
        assert repr(app.code_page("a.html")) == f"CodePage('a.html', app={APP!r})"


def test_an_order_by_item_that_isnt_a_field_is_refused() -> None:
    with client(Fake()) as qb:
        query = qb.app(APP).table("Tasks").operation("query")
        with pytest.raises(InvalidQueryError):
            query.run({"order_by": [5]})


def test_a_report_name_held_by_two_others_is_refused_like_any_other() -> None:
    fake = Fake()
    fake.reports = [
        {"id": "1", "name": "Mine", "type": "table"},
        {"id": "2", "name": "Twin", "type": "summary"},
        {"id": "3", "name": "Twin", "type": "summary"},
    ]
    with client(fake) as qb:
        tool = qb.app(APP).table("Tasks").operation("run_report", reports=["1", "2", "3"])
        with pytest.raises(InvalidQueryError, match="isn't a report this tool may run"):
            tool.run({"report": "Twin"})


def test_a_delete_preview_can_choose_its_fields() -> None:
    fake = Fake()
    with client(fake) as qb:
        tool = qb.app(APP).table("Tasks").operation("preview_delete")
        where = {"field": "Title", "op": "==", "value": "x"}
        tool.run({"where": where, "select": ["Title"]})
    assert "POST /records/query" in fake.sent
