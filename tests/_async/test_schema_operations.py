"""Schema operations for a model: describe, plan, and apply only with the plan's confirmation."""

from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    ConfigurationError,
    ConfirmationError,
    InvalidValueError,
    QueryError,
    ReadAccess,
    ReadRules,
    SchemaAccess,
    SchemaChangeResult,
    SchemaPlan,
    WriteAccess,
    WriteRules,
    tool_error,
    tool_result,
)
from tests.schema_fake import APP, PROJECTS, TASKS, FakeApp

pytestmark = pytest.mark.anyio

NEW_FIELD = {"kind": "create_field", "table": "Tasks", "label": "Due", "type": "date"}


def client(fake: FakeApp, **kwargs: Any) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, **kwargs)


def kinds(schema: dict[str, Any]) -> list[str]:
    items = schema["properties"]["changes"]["items"]["oneOf"]
    return [item["properties"]["kind"]["const"] for item in items]


async def test_a_plan_then_its_confirmation_applies_the_changes() -> None:
    fake = FakeApp()
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await app.operation("plan_schema")
        apply = await app.operation("apply_schema")
        shown = tool_result(await plan.run({"changes": [NEW_FIELD]}))
        assert shown["plan"]["changes"][0]["summary"] == 'Add field "Due" (date) to Tasks.'
        assert shown["expires_at"].endswith("Z")
        assert not fake.writes()
        result = await apply.run({"changes": [NEW_FIELD], "confirmation": shown["confirmation"]})
    assert isinstance(result, SchemaChangeResult)
    assert tool_result(result)["applied"][0]["summary"] == 'Add field "Due" (date) to Tasks.'
    assert fake.writes() == [("POST", "/fields")]


@pytest.mark.parametrize(
    ("confirmation", "match"),
    [(None, "needs the confirmation that plan_schema returns"), ("1.x", "has expired")],
)
async def test_apply_needs_a_current_confirmation(confirmation: Any, match: str) -> None:
    fake = FakeApp()
    async with client(fake) as qb:
        apply = await qb.app(APP).operation("apply_schema")
        arguments: dict[str, Any] = {"changes": [NEW_FIELD]}
        if confirmation is not None:
            arguments["confirmation"] = confirmation
        with pytest.raises(QueryError, match=match):
            await apply.run(arguments)
    assert not fake.writes()


async def test_a_confirmation_only_applies_the_changes_it_was_given_for() -> None:
    fake = FakeApp()
    async with client(fake) as qb:
        app = qb.app(APP)
        confirmation = await (await app.operation("plan_schema")).run({"changes": [NEW_FIELD]})
        other = {**NEW_FIELD, "label": "Deadline"}
        with pytest.raises(QueryError, match="these arguments differ"):
            await (await app.operation("apply_schema")).run(
                {"changes": [other], "confirmation": confirmation.confirmation}
            )
    assert not fake.writes()


async def test_a_confirmation_is_refused_once_the_schema_changes() -> None:
    fake = FakeApp()
    change = {"kind": "update_field", "table": "Tasks", "field": "Code", "label": "Ref"}
    async with client(fake) as qb:
        app = qb.app(APP)
        plan = await (await app.operation("plan_schema")).run({"changes": [change]})
        fake.fields[TASKS][7]["unique"] = True  # Someone changes the field in the UI.
        with pytest.raises(QueryError, match="the schema has changed since"):
            await (await app.operation("apply_schema")).run(
                {"changes": [change], "confirmation": plan.confirmation}
            )
    assert not fake.writes()


async def test_a_plan_with_blockers_or_nothing_to_do_gives_no_confirmation() -> None:
    fake = FakeApp()
    async with client(fake) as qb:
        plan = await qb.app(APP).operation("plan_schema")
        blocked = await plan.run({"changes": [{**NEW_FIELD, "label": "Hours"}]})
        done = await plan.run(
            {"changes": [{"kind": "create_field", "table": "Tasks", "label": "Hours",
                          "type": "numeric"}]}
        )  # fmt: skip
    assert isinstance(blocked, SchemaPlan)
    assert isinstance(done, SchemaPlan)
    assert (blocked.confirmation, done.confirmation) == ("", "")
    shown = tool_result(blocked)
    assert shown["blockers"]
    assert "confirmation" not in shown


async def test_the_schema_offers_only_what_the_rules_could_allow() -> None:
    fake = FakeApp()
    rules = WriteRules(
        {TASKS: WriteAccess(schema=SchemaAccess(create_fields=True, field_types=["text", "date"]))}
    )
    async with client(fake, write_policy=rules) as qb:
        plan = await qb.app(APP).operation("plan_schema", max_changes=3)
    schema = plan.schema
    assert kinds(schema) == ["create_field"]
    item = schema["properties"]["changes"]["items"]["oneOf"][0]
    assert item["properties"]["type"]["enum"] == ["text", "date"]
    assert "formula" not in item["properties"]
    assert "one of Tasks." in item["properties"]["table"]["description"]
    assert schema["properties"]["changes"]["maxItems"] == 3


async def test_a_change_the_tool_doesnt_offer_is_refused() -> None:
    fake = FakeApp()
    rules = WriteRules({TASKS: WriteAccess(schema=SchemaAccess(create_fields=True))})
    async with client(fake, write_policy=rules) as qb:
        plan = await qb.app(APP).operation("plan_schema", max_changes=1)
        with pytest.raises(InvalidValueError, match="isn't one this tool offers: create_field"):
            await plan.run({"changes": [{"kind": "delete_table", "table": "Tasks"}]})
        with pytest.raises(InvalidValueError, match="at most 1"):
            await plan.run({"changes": [NEW_FIELD, NEW_FIELD]})
        with pytest.raises(InvalidValueError, match="doesn't take: confirmation"):
            await plan.run({"changes": [NEW_FIELD], "confirmation": "x"})


async def test_a_client_that_cant_change_the_schema_cant_offer_it() -> None:
    async with client(FakeApp(), read_only=True) as qb:
        with pytest.raises(ConfigurationError, match="allow no schema change"):
            await qb.app(APP).operation("plan_schema")
        assert (await qb.app(APP).operation("describe_schema")).name == "describe_schema"
    with pytest.raises(ConfigurationError, match="name must be one of"):
        async with client(FakeApp()) as qb:
            await qb.app(APP).operation("drop_app")  # pyright: ignore[reportArgumentType]


async def test_describe_shows_what_the_client_can_read() -> None:
    fake = FakeApp()
    reads = ReadRules({TASKS: ReadAccess(fields=[6, 9, 10, 11]), PROJECTS: ReadAccess(fields=[6])})
    async with client(fake, read_policy=reads) as qb:
        describe = await qb.app(APP).operation("describe_schema")
        everything = tool_result(await describe.run({}))
        only = tool_result(await describe.run({"tables": ["Projects"]}))
        with pytest.raises(InvalidValueError, match="no table 'jobs'"):
            await describe.run({"tables": ["Jobs"]})
    tasks = everything["tables"][0]
    assert [item["label"] for item in tasks["fields"]] == [
        "Record ID#", "Hours", "Project", "Double", "Project - Name"
    ]  # fmt: skip
    assert tasks["fields"][3]["formula"] == "[Hours] * 2"
    assert tasks["relationships"] == [
        {"parent_table_id": PROJECTS, "foreign_key": "Project", "lookups": []}
    ]
    assert [item["name"] for item in only["tables"]] == ["Projects"]


async def test_a_refused_confirmation_says_how_to_get_one() -> None:  # F20
    fake = FakeApp()
    async with client(fake) as qb:
        apply = await qb.app(APP).operation("apply_schema")
        with pytest.raises(ConfirmationError) as missing:
            await apply.run({"changes": [NEW_FIELD], "confirmation": "1.x"})
        error = tool_error(missing.value)["error"]
        assert error["code"] == "needs_confirmation"
        assert "Preview or plan the same change again" in error["hint"]
        assert "a plan's confirmation lasts 15 minutes" in error["message"]
        async with client(fake) as other:
            elsewhere = await (await other.app(APP).operation("plan_schema")).run(
                {"changes": [NEW_FIELD]}
            )
        with pytest.raises(ConfirmationError, match="another client or server process"):
            await apply.run({"changes": [NEW_FIELD], "confirmation": elsewhere.confirmation})
