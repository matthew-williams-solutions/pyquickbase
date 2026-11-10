"""Giving, taking away, and moving roles, through the JSON trustees endpoints."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    InvalidValueError,
    PermissionDeniedError,
    Policy,
    PolicyRefusedError,
    RoleAssignment,
    RoleAssignmentResult,
    RoleAssignmentUpdate,
    WriteError,
    allow,
    tables,
    tool_error,
)
from tests.xml_fake import APP, REALM, TOKEN

pytestmark = pytest.mark.anyio

USER = "58390212.abcd"


class FakeTrustees:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status, self.body = status, body
        self.sent: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content)
        self.sent.append((request.method, path, body))
        result = self.body or {"success": body, "failure": []}
        return httpx.Response(self.status, json=result)


def client(handler: Any, **options: Any) -> AsyncQuickbase:
    return AsyncQuickbase(
        realm=REALM,
        token=TOKEN,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **options,
    )


async def test_roles_are_added_and_reported() -> None:
    fake = FakeTrustees()
    async with client(fake) as qb:
        result = await qb.app(APP).create_role_assignments([RoleAssignment(USER, 11)])
    assert fake.sent == [
        ("POST", f"/app/{APP}/trustees", [{"id": USER, "roleId": 11, "type": "user"}])
    ]
    assert result == RoleAssignmentResult(changed=(RoleAssignment(USER, 11),))


async def test_roles_are_removed() -> None:
    fake = FakeTrustees()
    async with client(fake) as qb:
        await qb.app(APP).delete_role_assignments([RoleAssignment("9.ab", 11, kind="group")])
    assert fake.sent[0][0] == "DELETE"
    assert fake.sent[0][2] == [{"id": "9.ab", "roleId": 11, "type": "group"}]


async def test_roles_are_moved() -> None:
    fake = FakeTrustees()
    async with client(fake) as qb:
        result = await qb.app(APP).update_role_assignments([RoleAssignmentUpdate(USER, 11, 12)])
    assert fake.sent[0][0] == "PATCH"
    assert fake.sent[0][2] == [{"id": USER, "roleId": 12, "oldRoleId": 11, "type": "user"}]
    assert result.changed == (RoleAssignmentUpdate(USER, 11, 12),)


async def test_a_partial_failure_raises_with_what_happened() -> None:
    body = {
        "success": [{"id": USER, "roleId": 11, "type": "user"}],
        "failure": [
            {"error": "Role does not exist", "trustee": {"id": USER, "roleId": 99, "type": "user"}}
        ],
    }
    fake = FakeTrustees(status=207, body=body)
    async with client(fake) as qb:
        with pytest.raises(WriteError) as raised:
            await qb.app(APP).delete_role_assignments(
                [RoleAssignment(USER, 11), RoleAssignment(USER, 99)]
            )
    result = raised.value.result
    assert result.changed == (RoleAssignment(USER, 11),)
    assert result.failed[0].error == "Role does not exist"
    assert result.failed[0].change == RoleAssignment(USER, 99)
    assert tool_error(raised.value)["error"]["data_changed"] == "partly"


async def test_a_full_failure_changes_nothing() -> None:
    body = {
        "success": [],
        "failure": [
            {
                "error": "Trustee already has this role",
                "trustee": {"id": USER, "roleId": 11, "type": "user"},
            }
        ],
    }
    async with client(FakeTrustees(status=207, body=body)) as qb:
        with pytest.raises(WriteError) as raised:
            await qb.app(APP).create_role_assignments([RoleAssignment(USER, 11)])
    assert raised.value.result.changed == ()
    assert tool_error(raised.value)["error"]["data_changed"] == "no"


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [("x", 1)],
        [RoleAssignment("", 1)],
        [RoleAssignment(USER, 0)],
        [RoleAssignment(USER, 1, kind="team")],  # pyright: ignore[reportArgumentType]
        "nope",
    ],
)
async def test_a_bad_assignment_sends_nothing(bad: Any) -> None:
    fake = FakeTrustees()
    async with client(fake) as qb:
        with pytest.raises(InvalidValueError):
            await qb.app(APP).create_role_assignments(bad)
    assert not fake.sent


async def test_a_403_is_reported() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Access denied"})

    async with client(handler) as qb:
        with pytest.raises(PermissionDeniedError):
            await qb.app(APP).create_role_assignments([RoleAssignment(USER, 11)])


@pytest.mark.parametrize(
    "method", ["create_role_assignments", "delete_role_assignments", "update_role_assignments"]
)
@pytest.mark.parametrize(
    "policy",
    [
        Policy.read_only(),
        Policy(),
        Policy(rules=[allow.code_pages(write=True), allow.create_tables()]),
    ],
    ids=["read_only", "empty", "code-pages"],
)
async def test_no_policy_can_allow_a_role_change(method: str, policy: Policy) -> None:
    fake = FakeTrustees()
    item = (
        RoleAssignmentUpdate(USER, 1, 2)
        if method == "update_role_assignments"
        else RoleAssignment(USER, 1)
    )
    async with client(fake, policy=policy) as qb:
        with pytest.raises(PolicyRefusedError):
            await getattr(qb.app(APP), method)([item])
    assert not fake.sent


async def test_request_cant_reach_role_changes_with_a_policy() -> None:
    from tests.policy_helpers import bind

    fake = FakeTrustees()
    bound = bind(
        allow.read(tables.all()),
        tables={"bqtasks": ("Tasks", [{"id": 3, "label": "Record ID#", "fieldType": "recordid"}])},
    )
    async with client(fake, policy=bound) as qb:
        with pytest.raises(PolicyRefusedError):
            await qb.request("POST", f"/app/{APP}/trustees", json=[])
    assert not fake.sent


async def test_a_timeout_isnt_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    async with client(handler) as qb:
        with pytest.raises(Exception, match="timed out"):
            await qb.app(APP).create_role_assignments([RoleAssignment(USER, 11)])
    assert calls == 1
