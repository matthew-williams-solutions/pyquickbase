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
    BadRequestError,
    CodePageChangedError,
    InvalidConfirmationError,
    InvalidValueError,
    NotFoundError,
    PermissionDeniedError,
    Policy,
    PolicyRefusedError,
    Quickbase,
    RoleAssignment,
    RoleAssignmentUpdate,
    SchemaMismatchError,
    SetKeyField,
    WriteError,
    allow,
)
from pyquickbase._xml import ROLE_INFO, SET_KEY_FIELD, roles_from_xml


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


# The live tests reuse this one page, as Quickbase's API can't delete one.
PAGE = "pyquickbase-live-test.html"
RESTING_TEXT = (
    "<!doctype html>\n<title>pyquickbase live test page</title>\n"
    "<p>Used by pyquickbase's live tests. Safe to overwrite.</p>\n"
)


@pytest.fixture
def page(qb: Quickbase, scratch: Any) -> Iterator[Any]:
    found = qb.app(scratch.app_id).code_page(PAGE)
    try:
        yield found
    finally:
        found.write(RESTING_TEXT)


@pytest.mark.parametrize(
    "text",
    [
        "plain",
        "",
        "one\ntwo\nthree\n",
        "no newline at the end\nsecond",
        "\nstarts with a line break",
        "blank\n\n\nlines\n",
        "ünïcode 日本語 🎉\n",
        "]]> & <b>markup</b> <BR/>\n",
        "tabs\tand trailing spaces   \n",
        "windows\r\nline\r\nendings\r\n",
        "old mac\rline\r",
    ],
    ids=[
        "plain",
        "empty",
        "lines",
        "no-final-newline",
        "leading-newline",
        "blank-lines",
        "non-ascii",
        "markup",
        "tabs",
        "crlf",
        "cr",
    ],
)
def test_xml_code_page_round_trip(page: Any, text: str) -> None:
    written = page.write(text)
    expected = text.replace("\r\n", "\n").replace("\r", "\n")
    assert (page.read()).text == expected
    assert written.content.text == expected
    assert written.content.id is not None


def test_xml_code_page_write_reports_what_it_replaced(page: Any) -> None:
    page.write("first\n")
    written = page.write("second\n")
    assert written.previous.text == "first\n"
    assert written.created is False


def test_xml_code_page_a_stale_hash_writes_nothing(page: Any) -> None:
    page.write("kept\n")
    with pytest.raises(CodePageChangedError):
        page.write("lost\n", expected_sha256="0" * 64)
    assert (page.read()).text == "kept\n"


def test_xml_code_page_preview_doesnt_write(page: Any) -> None:
    page.write("before\n")
    preview = page.preview_change("after\n")
    assert "-before" in preview.diff
    assert "+after" in preview.diff
    assert (page.read()).text == "before\n"


def test_xml_code_page_list_finds_the_test_page(qb: Quickbase, scratch: Any) -> None:
    found = {item.name: item for item in qb.app(scratch.app_id).code_pages()}
    assert found[PAGE].type == "Text"
    assert found[PAGE].id > 0
    home = [item for item in found.values() if item.type == "Home Page"]
    assert home
    with pytest.raises(BadRequestError):
        qb.app(scratch.app_id).code_page(home[0].id).read()


def test_xml_code_page_by_id_is_the_same_page(page: Any, qb: Quickbase, scratch: Any) -> None:
    page.write("by id\n")
    page_id = next(item.id for item in qb.app(scratch.app_id).code_pages() if item.name == PAGE)
    found = qb.app(scratch.app_id).code_page(page_id).read()
    assert found.text == "by id\n"


def test_xml_code_page_missing(qb: Quickbase, scratch: Any) -> None:
    with pytest.raises(NotFoundError):
        qb.app(scratch.app_id).code_page("pyquickbase-no-such-page.html").read()
    with pytest.raises(NotFoundError):
        qb.app(scratch.app_id).code_page(999999).write("x")


def test_xml_code_page_restricted_reads_but_cant_write(restricted: Quickbase, scratch: Any) -> None:
    found = restricted.app(scratch.app_id).code_page(PAGE)
    found.read()
    with pytest.raises(PermissionDeniedError) as raised:
        found.write("should not land\n")
    assert raised.value.quickbase_code == 3


def test_xml_code_page_operations_require_a_confirmation(page: Any, scratch: Any) -> None:
    token = os.environ["QB_USER_TOKEN"]
    policy = Policy(rules=[allow.code_pages(write=True)])
    with Quickbase(realm=scratch.realm, token=token, policy=policy) as qb:
        app = qb.app(scratch.app_id)
        preview = app.operation("preview_code_page_change")
        apply = app.operation("apply_code_page_change")
        shown = preview.run({"code_page": PAGE, "text": "from a model\n"})
        with pytest.raises(InvalidConfirmationError):
            apply.run({"code_page": PAGE, "text": "from a model\n"})
        apply.run({"code_page": PAGE, "text": "from a model\n", "confirmation": shown.confirmation})
    assert (page.read()).text == "from a model\n"


def test_xml_code_page_a_policy_without_the_grant_is_refused(scratch: Any) -> None:
    token = os.environ["QB_USER_TOKEN"]
    with Quickbase(realm=scratch.realm, token=token, policy=Policy.read_only()) as qb:
        with pytest.raises(PolicyRefusedError):
            qb.app(scratch.app_id).code_page(PAGE).read()


def trustees(qb: Quickbase, app_id: str) -> list[dict[str, Any]]:
    return qb.request("GET", f"/app/{app_id}/trustees")


@pytest.fixture
def role_nine_user(qb: Quickbase, scratch: Any) -> Iterator[str]:
    """The Integration Tests user that has no access, put back at role 9 even if a test fails."""
    found = [
        item
        for item in trustees(qb, scratch.app_id)
        if item["roleId"] == 9 and item["type"] == "user"
    ]
    if len(found) != 1:
        pytest.skip("This needs exactly one user at role 9.")
    member = found[0]["id"]
    try:
        yield member
    finally:
        now = [item["roleId"] for item in trustees(qb, scratch.app_id) if item["id"] == member]
        extra = [RoleAssignment(member, role) for role in now if role != 9]
        if extra:
            qb.app(scratch.app_id).delete_role_assignments(extra)
        if 9 not in now:
            qb.app(scratch.app_id).create_role_assignments([RoleAssignment(member, 9)])
        final = [item["roleId"] for item in trustees(qb, scratch.app_id) if item["id"] == member]
        assert final == [9], "the role-9 user wasn't restored"


def test_xml_role_changes_add_change_remove_and_partial_failure(
    qb: Quickbase, scratch: Any, role_nine_user: str
) -> None:
    app = qb.app(scratch.app_id)
    moved = app.update_role_assignments([RoleAssignmentUpdate(role_nine_user, 9, 10)])
    assert moved.changed == (RoleAssignmentUpdate(role_nine_user, 9, 10),)
    app.update_role_assignments([RoleAssignmentUpdate(role_nine_user, 10, 9)])
    added = app.create_role_assignments([RoleAssignment(role_nine_user, 10)])
    assert added.changed == (RoleAssignment(role_nine_user, 10),)
    with pytest.raises(WriteError) as duplicate:
        app.create_role_assignments([RoleAssignment(role_nine_user, 10)])
    assert duplicate.value.result.failed[0].error == "Trustee already has this role"
    with pytest.raises(WriteError) as partial:
        app.delete_role_assignments(
            [RoleAssignment(role_nine_user, 10), RoleAssignment(role_nine_user, 99999)]
        )
    assert partial.value.result.changed == (RoleAssignment(role_nine_user, 10),)
    assert partial.value.result.failed[0].error == "Role does not exist"


def test_xml_role_changes_are_refused_for_a_restricted_role(
    restricted: Quickbase, scratch: Any, role_nine_user: str
) -> None:
    with pytest.raises(PermissionDeniedError):
        restricted.app(scratch.app_id).create_role_assignments([RoleAssignment(role_nine_user, 10)])


def test_xml_role_changes_are_refused_by_any_policy(scratch: Any, role_nine_user: str) -> None:
    token = os.environ["QB_USER_TOKEN"]
    with Quickbase(realm=scratch.realm, token=token, policy=Policy.read_only()) as qb:
        with pytest.raises(PolicyRefusedError):
            qb.app(scratch.app_id).create_role_assignments([RoleAssignment(role_nine_user, 10)])


def test_xml_key_field_is_set_through_a_plan(qb: Quickbase, scratch: Any) -> None:
    table = scratch.table(("Title", "text"), ("Code", "text", {"unique": True}))
    table_id = table.info().id
    app = qb.app(scratch.app_id)
    plan = app.preview_schema_changes([SetKeyField(table=table_id, field="Code")])
    assert plan.changes[0].status == "ready"
    app.apply_schema_changes(plan)
    live = qb.app(scratch.app_id).table(table_id)
    code_id = (live.resolve_field_ids(["Code"]))[0]
    assert (live.info()).key_field_id == code_id
    with pytest.raises(InvalidValueError):
        live.insert([{"Code": "A-1"}])
    # A plan made against the old state is refused once the key has moved on.
    stale = app.preview_schema_changes([SetKeyField(table=table_id, field="Record ID#")])
    app.apply_schema_changes(stale)
    assert (live.info()).key_field_id == 3
    with pytest.raises(SchemaMismatchError):
        app.apply_schema_changes(stale)


def test_xml_key_field_is_refused_for_a_restricted_role(
    qb: Quickbase, restricted: Quickbase, scratch: Any
) -> None:
    table = scratch.table(("Code", "text", {"unique": True}))
    table_id = table.info().id
    # The role can't see this table, so `Table` couldn't find it. The refusal is what matters.
    with pytest.raises(PermissionDeniedError):
        restricted._transport.xml.call(SET_KEY_FIELD, table_id, {"fid": 6})  # pyright: ignore[reportPrivateUsage]
    assert (qb.app(scratch.app_id).table(table_id).info()).key_field_id == 3
