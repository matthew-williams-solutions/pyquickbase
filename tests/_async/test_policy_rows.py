"""Row limits and created tables through a client: what reaches Quickbase."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    BoundPolicy,
    F,
    PolicyRefusedError,
    allow,
    limit,
    tables,
)

from ..policy_helpers import bind, field, link

pytestmark = pytest.mark.anyio

APP = "bq1234567"
TASKS = "bqtasks"
PROJECTS = "bqprojects"
TASK_FIELDS = [
    field(3, "Record ID#", "recordid"),
    field(6, "Title"),
    field(7, "Billable", "checkbox"),
    field(8, "Hours", "numeric"),
    field(9, "Project", "numeric", properties={"foreignKey": True}),
]
PROJECT_FIELDS = [field(3, "Record ID#", "recordid"), field(6, "Name")]
RELATIONSHIP = link(9, PROJECTS, TASKS, 9)


class Fake:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self.fields = {TASKS: list(TASK_FIELDS), PROJECTS: list(PROJECT_FIELDS)}

    def bodies(self, method: str, path: str) -> list[Any]:
        return [body for m, p, body in self.sent if (m, p) == (method, path)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        self.sent.append((request.method, path, body))
        table = request.url.params.get("tableId", "")
        if path == "/tables" and request.method == "POST":
            return httpx.Response(200, json={"id": "bqnew", "name": body["name"]})
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": 3},
                                             {"id": PROJECTS, "name": "Projects"}])  # fmt: skip
        if path == "/fields":
            return httpx.Response(200, json=self.fields[table])
        if path.endswith("/relationships"):
            rows = [RELATIONSHIP] if path.split("/")[2] == TASKS else []
            metadata = {"totalRelationships": len(rows)}
            return httpx.Response(200, json={"relationships": rows, "metadata": metadata})
        if path == "/records/query":
            return httpx.Response(200, json={"data": [], "fields": [],
                                             "metadata": {"totalRecords": 2, "numRecords": 0,
                                                          "skip": 0}})  # fmt: skip
        if path == "/records" and request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": 2})
        if path.startswith(f"/tables/{TASKS}/relationship/"):
            for summary in body.get("summaryFields") or ():
                self.fields[PROJECTS].append(
                    {"id": 40, "label": summary["label"], "fieldType": "numeric",
                     "mode": "summary", "properties": {
                         "summaryReferenceFieldId": 9, "summaryTargetFieldId": 0,
                         "summaryFunction": "COUNT", "summaryQuery": summary.get("where", "")}}
                )  # fmt: skip
            return httpx.Response(200, json=RELATIONSHIP)
        if path == "/tables" and request.method == "POST":
            return httpx.Response(200, json={"id": "bqnew", "name": body["name"]})
        return httpx.Response(404, json={"message": "Not found"})


def policy(*rules: Any) -> BoundPolicy:
    return bind(
        allow.read(tables.all()),
        *rules,
        tables={TASKS: ("Tasks", TASK_FIELDS), PROJECTS: ("Projects", PROJECT_FIELDS)},
        relationships=[RELATIONSHIP],
        app_id=APP,
    )


def client(fake: Fake, bound: BoundPolicy) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, policy=bound)


BILLABLE = limit.rows(TASKS, F("Billable") == True)  # noqa: E712


async def test_the_row_limit_is_anded_onto_every_query_and_delete() -> None:
    fake = Fake()
    async with client(fake, policy(allow.delete(TASKS), BILLABLE)) as qb:
        tasks = qb.app(APP).table("Tasks")
        await tasks.count()
        await tasks.count(F("Hours") > 2)
        await tasks.delete(F("Hours") > 2)
        await qb.request(
            "POST",
            "/records/query",
            json={"from": TASKS, "select": [6], "sortBy": [], "options": {"top": 1}},
        )
    wheres = [body["where"] for body in fake.bodies("POST", "/records/query")]
    assert wheres == [
        "{7.EX.'true'}",
        "({7.EX.'true'})AND({8.GT.'2'})",
        "{7.EX.'true'}",
    ]
    (deleted,) = fake.bodies("DELETE", "/records")
    assert deleted["where"] == "({7.EX.'true'})AND({8.GT.'2'})"


async def test_a_summary_of_a_row_limited_child_gets_the_limit_in_its_filter() -> None:
    fake = Fake()
    async with client(fake, policy(allow.schema(PROJECTS, "summary"), BILLABLE)) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (await tasks.relationships())[0]
        await tasks.ensure_count_field(relationship, "Long Tasks", where=F("Hours") > 4)
        await tasks.ensure_count_field(relationship, "Long Tasks", where=F("Hours") > 4)
    (sent,) = fake.bodies("POST", f"/tables/{TASKS}/relationship/9")
    assert sent["summaryFields"][0]["where"] == "({7.EX.'true'})AND({8.GT.'4'})"


async def test_a_lookup_from_a_row_limited_parent_is_refused() -> None:
    fake = Fake()
    rows = limit.rows(PROJECTS, F("Name") == "Mine")
    async with client(fake, policy(allow.schema(TASKS, "lookup"), rows)) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (await tasks.relationships())[0]
        with pytest.raises(PolicyRefusedError, match="Refused by rows") as caught:
            await tasks.ensure_lookup_field(relationship, "Name")
    assert caught.value.decision.rule == "limit.rows('bqprojects', F('Name') == 'Mine')"
    assert not fake.bodies("POST", f"/tables/{TASKS}/relationship/9")


async def test_creating_a_table_gives_the_client_a_new_policy() -> None:
    fake = Fake()
    bound = policy(allow.create_tables(), allow.read(tables.created()))
    async with client(fake, bound) as qb:
        assert qb.policy is bound
        await qb.app(APP).create_table("Visits")
        assert qb.policy is not bound
        assert qb.policy is not None
        assert qb.policy.created == ("bqnew",)
        saved = qb.policy.to_json()
    assert bound.created == ()
    assert BoundPolicy.from_json(saved).created == ("bqnew",)
