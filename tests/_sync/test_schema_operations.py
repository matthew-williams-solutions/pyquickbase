# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Schema operations for a model: describe, plan, and apply only with the plan's confirmation."""

from typing import Any

import httpx
import pytest

from pyquickbase import (
    ConfigurationError,
    InvalidValueError,
    QueryError,
    Quickbase,
    ReadAccess,
    ReadRules,
    SchemaAccess,
    SchemaChangeResult,
    SchemaPlan,
    WriteAccess,
    WriteRules,
    tool_result,
)
from tests.schema_fake import APP, PROJECTS, TASKS, FakeApp

NEW_FIELD = {"kind": "create_field", "table": "Tasks", "label": "Due", "type": "date"}


def client(fake: FakeApp, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, **kwargs)


def kinds(schema: dict[str, Any]) -> list[str]:
    items = schema["properties"]["changes"]["items"]["oneOf"]
    return [item["properties"]["kind"]["const"] for item in items]


def test_a_plan_then_its_confirmation_applies_the_changes() -> None:
    fake = FakeApp()
    with client(fake) as qb:
        app = qb.app(APP)
        plan = app.operation("plan_schema")
        apply = app.operation("apply_schema")
        shown = tool_result(plan.run({"changes": [NEW_FIELD]}))
        assert shown["plan"]["changes"][0]["summary"] == 'Add field "Due" (date) to Tasks.'
        assert shown["expires_at"].endswith("Z")
        assert not fake.writes()
        result = apply.run({"changes": [NEW_FIELD], "confirmation": shown["confirmation"]})
    assert isinstance(result, SchemaChangeResult)
    assert tool_result(result)["applied"][0]["summary"] == 'Add field "Due" (date) to Tasks.'
    assert fake.writes() == [("POST", "/fields")]


@pytest.mark.parametrize(
    ("confirmation", "match"),
    [(None, "needs the confirmation that plan_schema returns"), ("1.x", "has expired")],
)
def test_apply_needs_a_current_confirmation(confirmation: Any, match: str) -> None:
    fake = FakeApp()
    with client(fake) as qb:
        apply = qb.app(APP).operation("apply_schema")
        arguments: dict[str, Any] = {"changes": [NEW_FIELD]}
        if confirmation is not None:
            arguments["confirmation"] = confirmation
        with pytest.raises(QueryError, match=match):
            apply.run(arguments)
    assert not fake.writes()


def test_a_confirmation_only_applies_the_changes_it_was_given_for() -> None:
    fake = FakeApp()
    with client(fake) as qb:
        app = qb.app(APP)
        confirmation = (app.operation("plan_schema")).run({"changes": [NEW_FIELD]})
        other = {**NEW_FIELD, "label": "Deadline"}
        with pytest.raises(QueryError, match="these arguments differ"):
            (app.operation("apply_schema")).run(
                {"changes": [other], "confirmation": confirmation.confirmation}
            )
    assert not fake.writes()


def test_a_confirmation_is_refused_once_the_schema_changes() -> None:
    fake = FakeApp()
    change = {"kind": "update_field", "table": "Tasks", "field": "Code", "label": "Ref"}
    with client(fake) as qb:
        app = qb.app(APP)
        plan = (app.operation("plan_schema")).run({"changes": [change]})
        fake.fields[TASKS][7]["unique"] = True  # Someone changes the field in the UI.
        with pytest.raises(QueryError, match="the schema has changed since"):
            (app.operation("apply_schema")).run(
                {"changes": [change], "confirmation": plan.confirmation}
            )
    assert not fake.writes()


def test_a_plan_with_blockers_or_nothing_to_do_gives_no_confirmation() -> None:
    fake = FakeApp()
    with client(fake) as qb:
        plan = qb.app(APP).operation("plan_schema")
        blocked = plan.run({"changes": [{**NEW_FIELD, "label": "Hours"}]})
        done = plan.run(
            {"changes": [{"kind": "create_field", "table": "Tasks", "label": "Hours",
                          "type": "numeric"}]}
        )  # fmt: skip
    assert isinstance(blocked, SchemaPlan)
    assert isinstance(done, SchemaPlan)
    assert (blocked.confirmation, done.confirmation) == ("", "")
    shown = tool_result(blocked)
    assert shown["blockers"]
    assert "confirmation" not in shown


def test_the_schema_offers_only_what_the_rules_could_allow() -> None:
    fake = FakeApp()
    rules = WriteRules(
        {TASKS: WriteAccess(schema=SchemaAccess(create_fields=True, field_types=["text", "date"]))}
    )
    with client(fake, write_policy=rules) as qb:
        plan = qb.app(APP).operation("plan_schema", max_changes=3)
    schema = plan.schema
    assert kinds(schema) == ["create_field"]
    item = schema["properties"]["changes"]["items"]["oneOf"][0]
    assert item["properties"]["type"]["enum"] == ["text", "date"]
    assert "formula" not in item["properties"]
    assert "one of Tasks." in item["properties"]["table"]["description"]
    assert schema["properties"]["changes"]["maxItems"] == 3


def test_a_change_the_tool_doesnt_offer_is_refused() -> None:
    fake = FakeApp()
    rules = WriteRules({TASKS: WriteAccess(schema=SchemaAccess(create_fields=True))})
    with client(fake, write_policy=rules) as qb:
        plan = qb.app(APP).operation("plan_schema", max_changes=1)
        with pytest.raises(InvalidValueError, match="isn't one this tool offers: create_field"):
            plan.run({"changes": [{"kind": "delete_table", "table": "Tasks"}]})
        with pytest.raises(InvalidValueError, match="at most 1"):
            plan.run({"changes": [NEW_FIELD, NEW_FIELD]})
        with pytest.raises(InvalidValueError, match="doesn't take: confirmation"):
            plan.run({"changes": [NEW_FIELD], "confirmation": "x"})


def test_a_client_that_cant_change_the_schema_cant_offer_it() -> None:
    with client(FakeApp(), read_only=True) as qb:
        with pytest.raises(ConfigurationError, match="allow no schema change"):
            qb.app(APP).operation("plan_schema")
        assert (qb.app(APP).operation("describe_schema")).name == "describe_schema"
    with pytest.raises(ConfigurationError, match="name must be one of"):
        with client(FakeApp()) as qb:
            qb.app(APP).operation("drop_app")  # pyright: ignore[reportArgumentType]


def test_describe_shows_what_the_client_can_read() -> None:
    fake = FakeApp()
    reads = ReadRules({TASKS: ReadAccess(fields=[6, 9, 10, 11]), PROJECTS: ReadAccess(fields=[6])})
    with client(fake, read_policy=reads) as qb:
        describe = qb.app(APP).operation("describe_schema")
        everything = tool_result(describe.run({}))
        only = tool_result(describe.run({"tables": ["Projects"]}))
        with pytest.raises(InvalidValueError, match="no table 'jobs'"):
            describe.run({"tables": ["Jobs"]})
    tasks = everything["tables"][0]
    assert [item["label"] for item in tasks["fields"]] == [
        "Record ID#", "Hours", "Project", "Double", "Project - Name"
    ]  # fmt: skip
    assert tasks["fields"][3]["formula"] == "[Hours] * 2"
    assert tasks["relationships"] == [
        {"parent_table_id": PROJECTS, "foreign_key": "Project", "lookups": []}
    ]
    assert [item["name"] for item in only["tables"]] == ["Projects"]
