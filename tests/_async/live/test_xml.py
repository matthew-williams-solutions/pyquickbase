"""The methods built on Quickbase's XML API, against a real app with two tokens.

Nothing here changes the test apps, and no token's value is ever printed.
"""

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

from pyquickbase import (
    AsyncQuickbase,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    Policy,
    PolicyRefusedError,
)
from pyquickbase._xml import ROLE_INFO, roles_from_xml

pytestmark = pytest.mark.anyio


@pytest.fixture
async def restricted(scratch: Any) -> AsyncIterator[AsyncQuickbase]:
    token = os.environ.get("QB_RESTRICTED_TOKEN")
    if not token:
        pytest.skip("Set QB_RESTRICTED_TOKEN to run this.")
    async with AsyncQuickbase(realm=scratch.realm, token=token) as client:
        yield client


async def test_xml_apps_lists_what_each_token_is_assigned_to(
    qb: AsyncQuickbase, restricted: AsyncQuickbase, scratch: Any
) -> None:
    main = {item.id for item in await qb.apps()}
    assert scratch.app_id in main
    if scratch.second_app_id:
        assert scratch.second_app_id in main
    only = await restricted.apps()
    assert [item.id for item in only] == [scratch.app_id]
    assert only[0].name


async def test_xml_a_bad_token_is_refused(scratch: Any) -> None:
    async with AsyncQuickbase(realm=scratch.realm, token="b3xyz_not_a_real_token") as client:
        with pytest.raises(AuthenticationError) as raised:
            await client.apps()
    assert raised.value.quickbase_code == 83


async def test_xml_a_restricted_role_gets_the_same_roles_and_details(
    qb: AsyncQuickbase, restricted: AsyncQuickbase, scratch: Any
) -> None:
    theirs, ours = restricted.app(scratch.app_id), qb.app(scratch.app_id)
    assert [(r.id, r.name, r.access) for r in await theirs.roles()] == [
        (r.id, r.name, r.access) for r in await ours.roles()
    ]
    found, expected = await theirs.info(), await ours.info()
    assert (found.id, found.name, found.description, found.time_zone, found.date_format) == (
        expected.id,
        expected.name,
        expected.description,
        expected.time_zone,
        expected.date_format,
    )
    # API_GetDBInfo's count ignores view rules, so no XML-backed result may carry it.
    assert "numRecords" not in found.raw


async def test_xml_role_info_matches_the_json_api(qb: AsyncQuickbase, scratch: Any) -> None:
    root = await qb._transport.xml.call(ROLE_INFO, scratch.app_id)  # pyright: ignore[reportPrivateUsage]
    assert [(r.id, r.name, r.access) for r in roles_from_xml(root)] == [
        (r.id, r.name, r.access) for r in await qb.app(scratch.app_id).roles(refresh=True)
    ]


async def test_xml_current_user_and_find_user(
    qb: AsyncQuickbase, restricted: AsyncQuickbase
) -> None:
    me, them = await qb.current_user(), await restricted.current_user()
    assert me.id != them.id
    assert me.email
    assert (await qb.find_user(me.email)).id == me.id
    with pytest.raises(NotFoundError):
        await qb.find_user("nobody-here-zz@example.invalid")


async def test_xml_members_needs_an_administrator(
    qb: AsyncQuickbase, restricted: AsyncQuickbase, scratch: Any
) -> None:
    members = await qb.app(scratch.app_id).members()
    assert all(item.roles for item in members if item.kind == "user")
    with pytest.raises(PermissionDeniedError) as raised:
        await restricted.app(scratch.app_id).members()
    assert raised.value.quickbase_code == 3


async def test_xml_member_roles_for_yourself_and_someone_else(
    qb: AsyncQuickbase, restricted: AsyncQuickbase, scratch: Any
) -> None:
    mine = await restricted.app(scratch.app_id).member_roles()
    assert mine
    assert mine[0].assigned_through == "user"
    me = await qb.current_user()
    assert await qb.app(scratch.app_id).member_roles(me.id)
    with pytest.raises(PermissionDeniedError):
        await restricted.app(scratch.app_id).member_roles(me.id)


async def test_xml_a_read_only_client_is_refused(scratch: Any) -> None:
    token = os.environ["QB_USER_TOKEN"]
    async with AsyncQuickbase(realm=scratch.realm, token=token, policy=Policy.read_only()) as qb:
        with pytest.raises(PolicyRefusedError):
            await qb.apps()
        with pytest.raises(PolicyRefusedError):
            await qb.app(scratch.app_id).members()
