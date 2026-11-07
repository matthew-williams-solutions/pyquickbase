"""Schema changes on a client with a policy: what's checked before anything is sent."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    ConfigurationError,
    InvalidValueError,
    PolicyRefusedError,
    SchemaMismatchError,
    UnknownFieldError,
    UnknownTableError,
    allow,
    fields,
    tables,
    tool_error,
)
from pyquickbase._decision import SCHEMA_VERBS

from ..policy_helpers import bind as bind_tables
from ..policy_helpers import link

pytestmark = pytest.mark.anyio

APP = "bq1234567"
TASKS = "bqtasks"
PROJECTS = "bqprojects"


class Fake:
    """Answers schema requests like Quickbase, and records each one."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self.tables = [{"id": TASKS, "name": "Tasks"}, {"id": PROJECTS, "name": "Projects"}]
        self.fields: dict[str, list[dict[str, Any]]] = {
            TASKS: [
                {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
                {"id": 6, "label": "Hours", "fieldType": "numeric"},
                {"id": 7, "label": "Salary", "fieldType": "numeric"},
                {"id": 8, "label": "Owner", "fieldType": "user"},
                {"id": 9, "label": "Project", "fieldType": "numeric",
                 "properties": {"foreignKey": True}},
                {"id": 10, "label": "Double", "fieldType": "numeric", "mode": "formula",
                 "properties": {"formula": "[Hours] * 2"}},
                {"id": 11, "label": "Pay", "fieldType": "numeric", "mode": "formula",
                 "properties": {"formula": "[Salary] / 12"}},
                {"id": 12, "label": "Link", "fieldType": "url", "mode": "formula",
                 "properties": {"formula": '"https://example.com"'}},
            ],
            PROJECTS: [
                {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
                {"id": 6, "label": "Name", "fieldType": "text"},
                {"id": 7, "label": "Budget", "fieldType": "numeric"},
            ],
        }  # fmt: skip
        self.relationship = {
            "id": 9, "parentTableId": PROJECTS, "childTableId": TASKS,
            "foreignKeyField": {"id": 9, "label": "Project"}, "lookupFields": [],
        }  # fmt: skip

    def writes(self) -> list[tuple[str, str, Any]]:
        return [item for item in self.sent if item[0] != "GET"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        self.sent.append((request.method, path, body))
        table = request.url.params.get("tableId", "")
        route = (request.method, path)
        if route == ("GET", "/tables"):
            return httpx.Response(200, json=self.tables)
        if route == ("GET", "/fields"):
            return httpx.Response(200, json=self.fields.get(table, []))
        if route == ("GET", f"/tables/{TASKS}/relationships"):
            rows = [self.relationship]
            return httpx.Response(200, json={"relationships": rows,
                                             "metadata": {"totalRelationships": 1}})  # fmt: skip
        if route == ("POST", "/tables"):
            created = {"id": "bqnew", "name": body["name"]}
            self.tables.append(created)
            self.fields["bqnew"] = [{"id": 3, "label": "Record ID#", "fieldType": "recordid"}]
            return httpx.Response(200, json=created)
        if route == ("POST", f"/tables/{TASKS}"):
            self.tables[0] = {**self.tables[0], **body}
            return httpx.Response(200, json=self.tables[0])
        if route == ("POST", "/fields"):
            created = {"id": 50, "label": body["label"], "fieldType": body["fieldType"]}
            self.fields.setdefault(table, []).append(created)
            return httpx.Response(200, json=created)
        if request.method == "POST" and path.startswith("/fields/"):
            field_id = int(path.rsplit("/", 1)[1])
            found = next(item for item in self.fields[table] if item["id"] == field_id)
            return httpx.Response(200, json={**found, **body})
        if request.method == "POST" and path.startswith(f"/tables/{TASKS}/relationship/"):
            if body.get("lookupFieldIds"):
                self.fields[TASKS].append(
                    {"id": 51, "label": "Project - Name", "fieldType": "text", "mode": "lookup",
                     "properties": {"lookupReferenceFieldId": 9, "lookupTargetFieldId": 6}}
                )  # fmt: skip
            for summary in body.get("summaryFields") or ():
                self.fields[PROJECTS].append(
                    {"id": 52, "label": summary["label"], "fieldType": "numeric",
                     "mode": "summary", "properties": {
                         "summaryReferenceFieldId": 9,
                         "summaryTargetFieldId": summary.get("summaryFid", 0),
                         "summaryFunction": summary["accumulationType"],
                         **({"summaryQuery": summary["where"]} if "where" in summary
                            else {})}}
                )  # fmt: skip
            return httpx.Response(200, json=self.relationship)
        if request.method == "DELETE" and path == f"/tables/{TASKS}/relationship/9":
            return httpx.Response(200, json={"relationshipId": 9})
        return httpx.Response(404, json={"message": "Not found", "description": path})


def client(fake: Fake, **kwargs: Any) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, **kwargs)


def bind(*rules: Any, extra: dict[int, Any] | None = None) -> Any:
    """Bind against the fake's schema, with any fields only the policy should know about."""
    fake = Fake()
    projects = [*fake.fields[PROJECTS], *(extra or {}).values()]
    return bind_tables(
        *rules,
        tables={TASKS: ("Tasks", fake.fields[TASKS]), PROJECTS: ("Projects", projects)},
        relationships=[link(9, PROJECTS, TASKS, 9, summaries=list(extra or {}))],
        app_id=APP,
    )


def reads(task_fields: Any = (3, 6, 8, 9, 10), projects: Any = (6,), **access: Any) -> list[Any]:
    """Salary and Budget can be filtered on but not returned, so they can be named."""
    every = task_fields is True
    task_reads = fields.all() if every else list(task_fields)
    task_filters = fields.all() if every else [*task_fields, 7]
    project_reads = fields.all() if projects is True else list(projects)
    project_filters = fields.all() if projects is True else [6, 7]
    return [
        allow.read(TASKS, task_reads, filter=task_filters, sort=task_reads, **access),
        allow.read(PROJECTS, project_reads, filter=project_filters, sort=project_reads),
    ]


def writes(*verbs: Any) -> Any:
    return allow.schema([TASKS, PROJECTS], *verbs)


ANY_SCHEMA = allow.schema(tables.all(), *SCHEMA_VERBS)
"""What a client without write rules could do."""


# Changing a table


async def test_update_info_renames_and_the_table_still_works() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app(APP).table("Tasks")
        info = await tasks.update_info(name="Jobs", plural_record_name="Jobs")
        assert info.name == "Jobs"
        assert (await tasks.info()).name == "Jobs"
        with pytest.raises(InvalidValueError, match="at least one"):
            await tasks.update_info()
    assert fake.writes() == [
        ("POST", f"/tables/{TASKS}", {"name": "Jobs", "pluralRecordName": "Jobs"})
    ]


async def test_delete_relationship_goes_to_the_child() -> None:
    fake = Fake()
    async with client(fake) as qb:
        app = qb.app(APP)
        relationship = (await app.table("Tasks").relationships())[0]
        with pytest.raises(InvalidValueError, match="call delete_relationship"):
            await app.table("Projects").delete_relationship(relationship)
        await app.table("Tasks").delete_relationship(relationship)
    assert fake.writes() == [("DELETE", f"/tables/{TASKS}/relationship/9", None)]


async def test_deleting_a_relationship_needs_both_tables_to_allow_it() -> None:
    fake = Fake()
    total = {"id": 20, "label": "Total Hours", "fieldType": "numeric", "mode": "summary"}
    policy = bind(
        allow.read(tables.all()),
        allow.schema([TASKS, PROJECTS], "unlink"),
        allow.schema(TASKS, "delete_field"),
        extra={20: total},
    )
    async with client(fake, policy=policy) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (await tasks.relationships())[0]
        with pytest.raises(PolicyRefusedError, match=r"deleting Total Hours \(20\) in Projects"):
            await tasks.delete_relationship(relationship)
    assert not fake.writes()


# New tables


async def test_a_new_table_gets_the_access_for_new_tables() -> None:
    fake = Fake()
    policy = bind(
        allow.create_tables(),
        allow.schema(tables.created(), "add_field"),
        allow.read(tables.created()),
    )
    async with client(fake, policy=policy) as qb:
        table = await qb.app(APP).create_table("Visits")
        await table.create_field("Notes", "text")
        assert [item.label for item in await table.fields()] == ["Record ID#", "Notes"]
        assert qb.policy is not None
        assert qb.policy.created == ("bqnew",)
    assert policy.created == ()


async def test_without_access_for_new_tables_a_new_one_is_off_limits() -> None:
    fake = Fake()
    async with client(fake, policy=bind(allow.create_tables())) as qb:
        table = await qb.app(APP).create_table("Visits")
        # Nothing allows anything in it, so it's hidden even from the client that made it.
        with pytest.raises(UnknownTableError):
            await table.create_field("Notes", "text")


# Formulas


async def test_a_formula_may_use_readable_fields() -> None:
    fake = Fake()
    rules = writes("add_field", "change_field", "formula")
    async with client(fake, policy=bind(*reads(), rules)) as qb:
        tasks = qb.app(APP).table("Tasks")
        await tasks.create_field("Triple", "numeric", formula="[Hours] * 3")
        await tasks.update_field("Double", formula="Round([hours] * 2)")
    sent = [body["properties"]["formula"] for _, _, body in fake.writes()]
    assert sent == ["[Hours] * 3", "Round([hours] * 2)"]


@pytest.mark.parametrize(
    ("formula", "match"),
    [
        ("[Salary] / 12", r"uses \[Salary\]"),
        ("[Missing]", r"uses \[Missing\]"),
        ('GetRecords("{3.GT.0}")', r"calls GetRecords\(\)"),
        ("ToText([Owner])", "a user field"),
    ],
)
async def test_a_formula_that_could_show_more_is_refused(formula: str, match: str) -> None:
    fake = Fake()
    rules = writes("add_field", "formula")
    async with client(fake, policy=bind(*reads(), rules)) as qb:
        with pytest.raises(PolicyRefusedError, match=match):
            await qb.app(APP).table("Tasks").create_field("New", "text", formula=formula)
    assert not fake.writes()


async def test_a_user_field_is_fine_when_emails_are_shown() -> None:
    fake = Fake()
    async with client(fake, policy=bind(*reads(user_emails=True), ANY_SCHEMA)) as qb:
        await qb.app(APP).table("Tasks").create_field("Who", "text", formula="ToText([Owner])")
    assert len(fake.writes()) == 1


async def test_a_formula_without_a_policy_isnt_checked() -> None:
    fake = Fake()
    async with client(fake) as qb:
        await qb.app(APP).table("Tasks").create_field("Pay", "numeric", formula="[Salary] / 12")
        await qb.request(
            "POST", "/fields", params={"tableId": TASKS},
            json={"label": "Raw", "fieldType": "numeric", "properties": {"formula": "1"}},
        )  # fmt: skip
    assert len(fake.writes()) == 2


async def test_with_a_policy_only_the_sdk_may_make_a_formula() -> None:
    fake = Fake()
    async with client(fake, policy=bind(writes("add_field", "formula"))) as qb:
        with pytest.raises(PolicyRefusedError, match="check what they read"):
            await qb.request(
                "POST", "/fields", params={"tableId": TASKS},
                json={"label": "Raw", "fieldType": "numeric", "properties": {"formula": "1"}},
            )  # fmt: skip
    assert not fake.writes()


async def test_a_link_formula_cant_be_changed_under_a_policy() -> None:
    fake = Fake()
    policy = bind(allow.read(tables.all()), writes("change_field", "formula"))
    async with client(fake, policy=policy) as qb:
        with pytest.raises(PolicyRefusedError, match="acts as them"):
            await qb.app(APP).table("Tasks").update_field("Link", formula='"https://x"')
    assert not fake.writes()


async def test_a_formula_is_only_shown_when_it_reveals_nothing() -> None:
    fake = Fake()
    async with client(fake, policy=bind(*reads(task_fields=(3, 6, 10, 11)))) as qb:
        fields = {item.label: item for item in await qb.app(APP).table("Tasks").fields()}
    assert fields["Double"].formula == "[Hours] * 2"
    assert fields["Pay"].formula is None


# Lookups and summaries


async def test_a_lookup_of_a_hidden_parent_field_is_refused() -> None:
    fake = Fake()
    async with client(fake, policy=bind(*reads(task_fields=True), ANY_SCHEMA)) as qb:
        app = qb.app(APP)
        tasks = app.table("Tasks")
        relationship = (await tasks.relationships())[0]
        with pytest.raises(PolicyRefusedError, match=rf"Budget \(7\) of Projects \({PROJECTS}\)"):
            await tasks.ensure_lookup_field(relationship, "Budget")
        await tasks.ensure_lookup_field(relationship, "Name")
    assert [path for method, path, _ in fake.writes()] == [f"/tables/{TASKS}/relationship/9"]


async def test_a_summary_of_a_hidden_child_field_is_refused() -> None:
    fake = Fake()
    async with client(fake, policy=bind(*reads(projects=True), ANY_SCHEMA)) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (await tasks.relationships())[0]
        with pytest.raises(PolicyRefusedError, match=rf"Salary \(7\) of Tasks \({TASKS}\)"):
            await tasks.ensure_summary_field(relationship, "Salary", "Total Salary")
        await tasks.ensure_summary_field(relationship, "Hours", "Total Hours")
    async with client(fake, policy=bind(*reads(task_fields=(3, 6)), ANY_SCHEMA)) as limited:
        child = limited.app(APP).table("Tasks")
        hidden_key = (await child.relationships())[0]
        # The foreign key can't be read, so even a count would show which tasks each project has.
        with pytest.raises(PolicyRefusedError, match=r"Project \(9\) of Tasks"):
            await child.ensure_count_field(hidden_key, "Tasks")
    assert len(fake.writes()) == 1


async def test_a_summary_filter_needs_fields_the_client_may_filter_on() -> None:
    fake = Fake()
    rules = [
        allow.read(TASKS, [3, 6, 8, 9, 10], filter=[3, 6, 7, 9]),
        allow.read(PROJECTS),
        ANY_SCHEMA,
    ]
    async with client(fake, policy=bind(*rules)) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (await tasks.relationships())[0]
        # Owner is returned but not filterable, so a count of matching tasks would show more.
        with pytest.raises(PolicyRefusedError, match=rf"filter uses field 8 of Tasks \({TASKS}\)"):
            await tasks.ensure_count_field(relationship, "Mine", where="{8.EX.'alex'}")
        with pytest.raises(PolicyRefusedError, match="field reference"):
            await tasks.ensure_count_field(relationship, "Same", where="{6.EX._FID_8}")
        # Pay can be changed, so it can be named, but not filtered on.
        with pytest.raises(PolicyRefusedError, match="field 11 of Tasks"):
            await tasks.ensure_count_field(relationship, "High Pay", where="{11.GT.5000}")
        # Salary can be filtered on, though not returned.
        await tasks.ensure_count_field(relationship, "Well Paid", where="{7.GT.5000}")
        # Salary can't be read, so the policy hides the filter, and it's matched without it.
        await tasks.ensure_count_field(relationship, "Well Paid", where="{7.GT.9000}")
    assert len(fake.writes()) == 1
    assert fake.writes()[0][2]["summaryFields"][0]["where"] == "{7.GT.5000}"


async def test_a_summary_filter_the_client_can_read_is_compared_again() -> None:
    fake = Fake()
    async with client(fake, policy=bind(allow.read(tables.all()), ANY_SCHEMA)) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (await tasks.relationships())[0]
        await tasks.ensure_count_field(relationship, "Long", where="{6.GT.8}")
        await tasks.ensure_count_field(relationship, "Long", where="{6.GT.8}")
        with pytest.raises(SchemaMismatchError, match="isn't this summary"):
            await tasks.ensure_count_field(relationship, "Long", where="{6.GT.9}")
    assert len(fake.writes()) == 1


async def test_a_new_field_the_rules_hide_says_it_was_made() -> None:
    fake = Fake()
    async with client(fake, policy=bind(*reads(), ANY_SCHEMA)) as qb:
        tasks = qb.app(APP).table("Tasks")
        with pytest.raises(UnknownFieldError, match="was created") as caught:
            await tasks.ensure_lookup_field((await tasks.relationships())[0], "Name")
    assert tool_error(caught.value)["error"]["changed"] == "some"


async def test_a_summary_needs_the_parents_grant() -> None:
    fake = Fake()
    policy = bind(
        allow.read(tables.all()),
        allow.schema([TASKS, PROJECTS], "link"),
        allow.schema(TASKS, "summary"),
    )
    async with client(fake, policy=policy) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (await tasks.relationships())[0]
        with pytest.raises(PolicyRefusedError, match=f"adding a summary to Projects \\({PROJECTS}"):
            await tasks.ensure_summary_field(relationship, "Hours", "Total Hours")


# Confirmation keys


async def test_a_shared_confirmation_key_must_be_long_enough() -> None:
    key = bytes(range(32))
    async with client(Fake(), confirmation_key=key) as qb:
        assert qb._transport.confirmation_key == key  # pyright: ignore[reportPrivateUsage]
    for bad in (b"short", "a" * 40):
        with pytest.raises(ConfigurationError, match="at least 32"):
            client(Fake(), confirmation_key=bad)
