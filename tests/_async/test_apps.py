"""Creating, copying, and deleting apps, and where fields are used."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    InvalidValueError,
    Policy,
    PolicyRefusedError,
    UnknownFieldError,
    allow,
)
from tests.policy_helpers import bind

pytestmark = pytest.mark.anyio

APP = "bq1234567"
TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 7, "label": "Status", "fieldType": "text"},
]


def usage_row(field_id: int, label: str, **counts: int) -> dict[str, Any]:
    kinds = ("reports", "forms", "tableRules")
    return {
        "field": {"id": field_id, "name": label, "type": "Text"},
        "usage": {kind: {"count": counts.get(kind, 0)} for kind in kinds},
    }


class Fake:
    """Answers like Quickbase for apps and field usage, and records every request."""

    def __init__(self, *, usage: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self.usage = usage if usage is not None else [
            usage_row(3, "Record ID#", reports=5),
            usage_row(6, "Title", reports=2, forms=1),
            usage_row(7, "Status"),
        ]  # fmt: skip

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        self.sent.append((request.method, path, body))
        app = {"id": "bqnewapp", "name": (body or {}).get("name", "Jobs"), "description": ""}
        match request.method, path:
            case "POST", "/apps":
                return httpx.Response(200, json=app)
            case "POST", "/apps/bq1234567/copy":
                return httpx.Response(200, json={**app, "ancestorId": APP})
            case "GET", "/apps/bq1234567":
                return httpx.Response(200, json={"id": APP, "name": "Jobs", "description": ""})
            case "DELETE", "/apps/bq1234567":
                return httpx.Response(200, json={"deletedAppId": APP})
            case "GET", "/tables":
                return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": 3}])
            case "GET", "/fields":
                return httpx.Response(200, json=FIELDS)
            case "GET", "/fields/usage":
                return httpx.Response(200, json=self.usage)
            case "GET", _ if path.startswith("/fields/usage/"):
                wanted = int(path.rsplit("/", 1)[1])
                rows = [row for row in self.usage if row["field"]["id"] == wanted]
                return httpx.Response(200, json=rows)
        return httpx.Response(404, json={"message": "Not found", "description": path})

    def paths(self) -> list[str]:
        return [f"{method} {path}" for method, path, _ in self.sent]


def client(fake: Fake, **kwargs: Any) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, **kwargs)


async def test_creating_an_app_returns_it_with_its_details_cached() -> None:
    fake = Fake()
    async with client(fake) as qb:
        app = await qb.create_app("Field Jobs", description="Crews and sites")
        info = await app.info()
    assert (app.id, info.name) == ("bqnewapp", "Field Jobs")
    assert fake.sent == [
        (
            "POST",
            "/apps",
            {"name": "Field Jobs", "assignToken": True, "description": "Crews and sites"},
        )
    ]


async def test_copying_an_app_sends_each_option() -> None:
    fake = Fake()
    async with client(fake) as qb:
        copy = await qb.app(APP).copy("Jobs (staging)", keep_data=True, assign_token=False)
    assert copy.id == "bqnewapp"
    assert fake.sent[0][2] == {
        "name": "Jobs (staging)",
        "properties": {
            "keepData": True,
            "excludeFiles": True,
            "usersAndRoles": False,
            "assignUserToken": False,
        },
    }


@pytest.mark.parametrize("name", ["", "   "])
async def test_an_app_needs_a_name(name: str) -> None:
    fake = Fake()
    async with client(fake) as qb:
        with pytest.raises(InvalidValueError, match="needs a name"):
            await qb.create_app(name)
        with pytest.raises(InvalidValueError, match="needs a name"):
            await qb.app(APP).copy(name)
    assert fake.sent == []


async def test_dropping_an_app_needs_its_exact_name() -> None:
    fake = Fake()
    async with client(fake) as qb:
        app = qb.app(APP)
        with pytest.raises(InvalidValueError, match="exact name"):
            await app.drop(confirm="jobs")
        assert "DELETE /apps/bq1234567" not in fake.paths()
        await app.drop(confirm="Jobs")
    assert fake.sent[-1] == ("DELETE", "/apps/bq1234567", {"name": "Jobs"})


async def test_dropping_an_app_forgets_what_was_cached() -> None:
    fake = Fake()
    async with client(fake) as qb:
        app = qb.app(APP)
        await app.tables()
        await app.drop(confirm="Jobs")
        await app.tables()
    assert fake.paths().count("GET /tables") == 2


@pytest.mark.parametrize(
    "policy", [Policy.read_only(), bind(allow.schema(TASKS, "create_field"), allow.create_tables())]
)
async def test_a_client_with_a_policy_never_makes_or_deletes_apps(policy: Any) -> None:
    fake = Fake()
    async with client(fake, policy=policy) as qb:
        app = qb.app(APP)
        with pytest.raises(PolicyRefusedError, match="create, copy, or delete an app"):
            await qb.create_app("Jobs")
        with pytest.raises(PolicyRefusedError, match="create, copy, or delete an app"):
            await app.copy("Jobs")
        with pytest.raises(PolicyRefusedError, match="create, copy, or delete an app"):
            await app.drop(confirm="Jobs")
    assert fake.sent == []


async def test_field_usage_counts_each_field_in_the_tables_order() -> None:
    fake = Fake()
    async with client(fake) as qb:
        found = await qb.app(APP).table("Tasks").field_usage()
    assert [(item.label, dict(item.counts), item.total) for item in found] == [
        ("Record ID#", {"reports": 5}, 5),
        ("Title", {"reports": 2, "forms": 1}, 3),
        ("Status", {}, 0),
    ]
    assert found[1].describe() == "2 reports and 1 form"
    assert found[2].describe() == "nothing"
    assert fake.paths().count("GET /fields/usage") == 1


async def test_field_usage_of_chosen_fields_asks_about_each() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app(APP).table("Tasks")
        found = await tasks.field_usage(["Status", 6])
        with pytest.raises(UnknownFieldError):
            await tasks.field_usage("Nope")
    assert [item.field_id for item in found] == [7, 6]
    assert "GET /fields/usage/7" in fake.paths()
    assert "GET /fields/usage" not in fake.paths()


async def test_field_usage_fills_in_fields_a_response_left_out() -> None:
    # Quickbase may answer for only some fields, such as a page of a large table.
    fake = Fake()
    first_page = fake.usage[:1]

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/fields/usage":
            fake.sent.append(("GET", "/fields/usage", None))
            return httpx.Response(200, json=first_page)
        return fake(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    async with AsyncQuickbase(realm="acme", token="t", http_client=http) as qb:
        found = await qb.app(APP).table("Tasks").field_usage()
    assert [item.field_id for item in found] == [3, 6, 7]
    assert {"GET /fields/usage/6", "GET /fields/usage/7"} <= set(fake.paths())


async def test_a_policy_shows_usage_only_for_fields_it_shows() -> None:
    fake = Fake()
    async with client(fake, policy=bind(allow.read(TASKS, ["Title"]))) as qb:
        found = await qb.app(APP).table("Tasks").field_usage()
    assert [item.label for item in found] == ["Record ID#", "Title"]
