# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""The methods built on Quickbase's XML API, against a real app with two tokens.

Nothing here changes the test apps, and no token's value is ever printed.
"""

import os
from collections.abc import Iterator
from typing import Any

import pytest

from pyquickbase import (
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    Policy,
    PolicyRefusedError,
    Quickbase,
)
from pyquickbase._xml import ROLE_INFO, roles_from_xml


@pytest.fixture
def restricted(scratch: Any) -> Iterator[Quickbase]:
    token = os.environ.get("QB_RESTRICTED_TOKEN")
    if not token:
        pytest.skip("Set QB_RESTRICTED_TOKEN to run this.")
    with Quickbase(realm=scratch.realm, token=token) as client:
        yield client


def test_xml_apps_lists_what_each_token_is_assigned_to(
    qb: Quickbase, restricted: Quickbase, scratch: Any
) -> None:
    main = {item.id for item in qb.apps()}
    assert scratch.app_id in main
    if scratch.second_app_id:
        assert scratch.second_app_id in main
    only = restricted.apps()
    assert [item.id for item in only] == [scratch.app_id]
    assert only[0].name


def test_xml_a_bad_token_is_refused(scratch: Any) -> None:
    with Quickbase(realm=scratch.realm, token="b3xyz_not_a_real_token") as client:
        with pytest.raises(AuthenticationError) as raised:
            client.apps()
    assert raised.value.quickbase_code == 83


def test_xml_a_restricted_role_gets_the_same_roles_and_details(
    qb: Quickbase, restricted: Quickbase, scratch: Any
) -> None:
    theirs, ours = restricted.app(scratch.app_id), qb.app(scratch.app_id)
    assert [(r.id, r.name, r.access) for r in theirs.roles()] == [
        (r.id, r.name, r.access) for r in ours.roles()
    ]
    found, expected = theirs.info(), ours.info()
    assert (found.id, found.name, found.description, found.time_zone, found.date_format) == (
        expected.id,
        expected.name,
        expected.description,
        expected.time_zone,
        expected.date_format,
    )
    # API_GetDBInfo's count ignores view rules, so no XML-backed result may carry it.
    assert "numRecords" not in found.raw


def test_xml_role_info_matches_the_json_api(qb: Quickbase, scratch: Any) -> None:
    root = qb._transport.xml.call(ROLE_INFO, scratch.app_id)  # pyright: ignore[reportPrivateUsage]
    assert [(r.id, r.name, r.access) for r in roles_from_xml(root)] == [
        (r.id, r.name, r.access) for r in qb.app(scratch.app_id).roles(refresh=True)
    ]


def test_xml_current_user_and_find_user(qb: Quickbase, restricted: Quickbase) -> None:
    me, them = qb.current_user(), restricted.current_user()
    assert me.id != them.id
    assert me.email
    assert (qb.find_user(me.email)).id == me.id
    with pytest.raises(NotFoundError):
        qb.find_user("nobody-here-zz@example.invalid")


def test_xml_members_needs_an_administrator(
    qb: Quickbase, restricted: Quickbase, scratch: Any
) -> None:
    members = qb.app(scratch.app_id).members()
    assert all(item.roles for item in members if item.kind == "user")
    with pytest.raises(PermissionDeniedError) as raised:
        restricted.app(scratch.app_id).members()
    assert raised.value.quickbase_code == 3


def test_xml_member_roles_for_yourself_and_someone_else(
    qb: Quickbase, restricted: Quickbase, scratch: Any
) -> None:
    mine = restricted.app(scratch.app_id).member_roles()
    assert mine
    assert mine[0].assigned_through == "user"
    me = qb.current_user()
    assert qb.app(scratch.app_id).member_roles(me.id)
    with pytest.raises(PermissionDeniedError):
        restricted.app(scratch.app_id).member_roles(me.id)


def test_xml_a_read_only_client_is_refused(scratch: Any) -> None:
    token = os.environ["QB_USER_TOKEN"]
    with Quickbase(realm=scratch.realm, token=token, policy=Policy.read_only()) as qb:
        with pytest.raises(PolicyRefusedError):
            qb.apps()
        with pytest.raises(PolicyRefusedError):
            qb.app(scratch.app_id).members()
