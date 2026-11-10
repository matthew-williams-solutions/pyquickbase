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
    BadRequestError,
    CodePageChangedError,
    ConfirmationError,
    InvalidValueError,
    NotFoundError,
    PermissionDeniedError,
    Policy,
    PolicyRefusedError,
    RoleAssignment,
    RoleChange,
    SchemaMismatchError,
    SetKeyField,
    WriteError,
    allow,
)
from pyquickbase._xml import ROLE_INFO, SET_KEY_FIELD, roles_from_xml

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


# The live tests reuse this one page, as Quickbase's API can't delete one.
PAGE = "pyquickbase-live-test.html"
RESTING_TEXT = (
    "<!doctype html>\n<title>pyquickbase live test page</title>\n"
    "<p>Used by pyquickbase's live tests. Safe to overwrite.</p>\n"
)


@pytest.fixture
async def page(qb: AsyncQuickbase, scratch: Any) -> AsyncIterator[Any]:
    found = qb.app(scratch.app_id).code_page(PAGE)
    try:
        yield found
    finally:
        await found.write(RESTING_TEXT)


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
async def test_xml_code_page_round_trip(page: Any, text: str) -> None:
    written = await page.write(text)
    expected = text.replace("\r\n", "\n").replace("\r", "\n")
    assert (await page.read()).text == expected
    assert written.page.text == expected
    assert written.page.id is not None


async def test_xml_code_page_write_reports_what_it_replaced(page: Any) -> None:
    await page.write("first\n")
    written = await page.write("second\n")
    assert written.previous.text == "first\n"
    assert written.created is False


async def test_xml_code_page_a_stale_hash_writes_nothing(page: Any) -> None:
    await page.write("kept\n")
    with pytest.raises(CodePageChangedError):
        await page.write("lost\n", expected_sha256="0" * 64)
    assert (await page.read()).text == "kept\n"


async def test_xml_code_page_preview_doesnt_write(page: Any) -> None:
    await page.write("before\n")
    preview = await page.preview_write("after\n")
    assert "-before" in preview.diff
    assert "+after" in preview.diff
    assert (await page.read()).text == "before\n"


async def test_xml_code_page_list_finds_the_test_page(qb: AsyncQuickbase, scratch: Any) -> None:
    found = {item.name: item for item in await qb.app(scratch.app_id).code_pages()}
    assert found[PAGE].type == "Text"
    assert found[PAGE].id > 0
    home = [item for item in found.values() if item.type == "Home Page"]
    assert home
    with pytest.raises(BadRequestError):
        await qb.app(scratch.app_id).code_page(home[0].id).read()


async def test_xml_code_page_by_id_is_the_same_page(
    page: Any, qb: AsyncQuickbase, scratch: Any
) -> None:
    await page.write("by id\n")
    page_id = next(
        item.id for item in await qb.app(scratch.app_id).code_pages() if item.name == PAGE
    )
    found = await qb.app(scratch.app_id).code_page(page_id).read()
    assert found.text == "by id\n"


async def test_xml_code_page_missing(qb: AsyncQuickbase, scratch: Any) -> None:
    with pytest.raises(NotFoundError):
        await qb.app(scratch.app_id).code_page("pyquickbase-no-such-page.html").read()
    with pytest.raises(NotFoundError):
        await qb.app(scratch.app_id).code_page(999999).write("x")


async def test_xml_code_page_restricted_reads_but_cant_write(
    restricted: AsyncQuickbase, scratch: Any
) -> None:
    found = restricted.app(scratch.app_id).code_page(PAGE)
    await found.read()
    with pytest.raises(PermissionDeniedError) as raised:
        await found.write("should not land\n")
    assert raised.value.quickbase_code == 3


async def test_xml_code_page_operations_require_a_confirmation(page: Any, scratch: Any) -> None:
    token = os.environ["QB_USER_TOKEN"]
    policy = Policy(rules=[allow.code_pages(write=True)])
    async with AsyncQuickbase(realm=scratch.realm, token=token, policy=policy) as qb:
        app = qb.app(scratch.app_id)
        preview = await app.operation("preview_code_page_change")
        apply = await app.operation("apply_code_page_change")
        shown = await preview.run({"page": PAGE, "text": "from a model\n"})
        with pytest.raises(ConfirmationError):
            await apply.run({"page": PAGE, "text": "from a model\n"})
        await apply.run(
            {"page": PAGE, "text": "from a model\n", "confirmation": shown.confirmation}
        )
    assert (await page.read()).text == "from a model\n"


async def test_xml_code_page_a_policy_without_the_grant_is_refused(scratch: Any) -> None:
    token = os.environ["QB_USER_TOKEN"]
    async with AsyncQuickbase(realm=scratch.realm, token=token, policy=Policy.read_only()) as qb:
        with pytest.raises(PolicyRefusedError):
            await qb.app(scratch.app_id).code_page(PAGE).read()


async def trustees(qb: AsyncQuickbase, app_id: str) -> list[dict[str, Any]]:
    return await qb.request("GET", f"/app/{app_id}/trustees")


@pytest.fixture
async def role_nine_user(qb: AsyncQuickbase, scratch: Any) -> AsyncIterator[str]:
    """The Integration Tests user that has no access, put back at role 9 even if a test fails."""
    found = [
        item
        for item in await trustees(qb, scratch.app_id)
        if item["roleId"] == 9 and item["type"] == "user"
    ]
    if len(found) != 1:
        pytest.skip("This needs exactly one user at role 9.")
    member = found[0]["id"]
    try:
        yield member
    finally:
        now = [
            item["roleId"] for item in await trustees(qb, scratch.app_id) if item["id"] == member
        ]
        extra = [RoleAssignment(member, role) for role in now if role != 9]
        if extra:
            await qb.app(scratch.app_id).remove_role_assignments(extra)
        if 9 not in now:
            await qb.app(scratch.app_id).add_role_assignments([RoleAssignment(member, 9)])
        final = [
            item["roleId"] for item in await trustees(qb, scratch.app_id) if item["id"] == member
        ]
        assert final == [9], "the role-9 user wasn't restored"


async def test_xml_role_changes_add_change_remove_and_partial_failure(
    qb: AsyncQuickbase, scratch: Any, role_nine_user: str
) -> None:
    app = qb.app(scratch.app_id)
    moved = await app.change_role_assignments([RoleChange(role_nine_user, 9, 10)])
    assert moved.changed == (RoleChange(role_nine_user, 9, 10),)
    await app.change_role_assignments([RoleChange(role_nine_user, 10, 9)])
    added = await app.add_role_assignments([RoleAssignment(role_nine_user, 10)])
    assert added.changed == (RoleAssignment(role_nine_user, 10),)
    with pytest.raises(WriteError) as duplicate:
        await app.add_role_assignments([RoleAssignment(role_nine_user, 10)])
    assert duplicate.value.result.failed[0].error == "Trustee already has this role"
    with pytest.raises(WriteError) as partial:
        await app.remove_role_assignments(
            [RoleAssignment(role_nine_user, 10), RoleAssignment(role_nine_user, 99999)]
        )
    assert partial.value.result.changed == (RoleAssignment(role_nine_user, 10),)
    assert partial.value.result.failed[0].error == "Role does not exist"


async def test_xml_role_changes_are_refused_for_a_restricted_role(
    restricted: AsyncQuickbase, scratch: Any, role_nine_user: str
) -> None:
    with pytest.raises(PermissionDeniedError):
        await restricted.app(scratch.app_id).add_role_assignments(
            [RoleAssignment(role_nine_user, 10)]
        )


async def test_xml_role_changes_are_refused_by_any_policy(
    scratch: Any, role_nine_user: str
) -> None:
    token = os.environ["QB_USER_TOKEN"]
    async with AsyncQuickbase(realm=scratch.realm, token=token, policy=Policy.read_only()) as qb:
        with pytest.raises(PolicyRefusedError):
            await qb.app(scratch.app_id).add_role_assignments([RoleAssignment(role_nine_user, 10)])


async def test_xml_key_field_is_set_through_a_plan(qb: AsyncQuickbase, scratch: Any) -> None:
    table = scratch.table(("Title", "text"), ("Code", "text", {"unique": True}))
    table_id = table.info().id
    app = qb.app(scratch.app_id)
    plan = await app.preview_schema_changes([SetKeyField(table=table_id, field="Code")])
    assert plan.changes[0].status == "ready"
    await app.apply_schema_changes(plan)
    live = qb.app(scratch.app_id).table(table_id)
    code_id = (await live.resolve_field_ids(["Code"]))[0]
    assert (await live.info()).key_field_id == code_id
    with pytest.raises(InvalidValueError):
        await live.insert([{"Code": "A-1"}])
    # A plan made against the old state is refused once the key has moved on.
    stale = await app.preview_schema_changes([SetKeyField(table=table_id, field="Record ID#")])
    await app.apply_schema_changes(stale)
    assert (await live.info()).key_field_id == 3
    with pytest.raises(SchemaMismatchError):
        await app.apply_schema_changes(stale)


async def test_xml_key_field_is_refused_for_a_restricted_role(
    qb: AsyncQuickbase, restricted: AsyncQuickbase, scratch: Any
) -> None:
    table = scratch.table(("Code", "text", {"unique": True}))
    table_id = table.info().id
    # The role can't see this table, so `Table` couldn't find it. The refusal is what matters.
    with pytest.raises(PermissionDeniedError):
        await restricted._transport.xml.call(SET_KEY_FIELD, table_id, {"fid": 6})  # pyright: ignore[reportPrivateUsage]
    assert (await qb.app(scratch.app_id).table(table_id).info()).key_field_id == 3
