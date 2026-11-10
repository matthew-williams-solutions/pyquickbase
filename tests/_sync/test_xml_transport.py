# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Sending XML calls: headers, retries, redirects, and what a policy allows, for every call."""

from typing import Any

import httpx
import pytest

from pyquickbase import (
    AuthenticationError,
    ConfigurationError,
    NotFoundError,
    PermissionDeniedError,
    Policy,
    PolicyRefusedError,
    Quickbase,
    RateLimitError,
    RetryPolicy,
    ServerError,
)
from pyquickbase._xml import (
    APP_SCHEMA,
    FIND_USER,
    GRANTED_APPS,
    ROLE_INFO,
    XML_CALLS,
    XmlCall,
)
from tests.policy_helpers import bind
from tests.xml_fake import APP, REALM, TOKEN, FakeXml, failure

NO_RETRY = RetryPolicy(max_retries=0)
FAST = RetryPolicy(max_retries=2, base_delay=0, max_delay=0)
# Reads a policy can allow, because the JSON API already offers the same data.
POLICY_CAN_ALLOW = {ROLE_INFO, APP_SCHEMA}


def client(handler: Any, *, retry: RetryPolicy = NO_RETRY, **options: Any) -> Quickbase:
    options.setdefault("realm", REALM)
    return Quickbase(
        token=TOKEN,
        retry=retry,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **options,
    )


def target_of(call: XmlCall) -> str:
    return "main" if call.target == "main" else APP


def call_ids(call: XmlCall) -> str:
    return f"{call.policy_action}"


@pytest.mark.parametrize("call", XML_CALLS, ids=call_ids)
def test_no_token_in_any_url_or_body(call: XmlCall) -> None:
    fake = FakeXml()
    with client(fake) as qb:
        qb._transport.xml.call(call, target_of(call), {"note": "hello"})
    request = fake.sent[0]
    assert TOKEN not in str(request.url)
    assert TOKEN.encode() not in request.content
    assert request.headers["Authorization"] == f"QB-USER-TOKEN {TOKEN}"


@pytest.mark.parametrize("call", XML_CALLS, ids=call_ids)
@pytest.mark.parametrize("policy", [Policy.read_only(), Policy()], ids=["read_only", "empty"])
def test_a_client_with_any_policy_is_refused(call: XmlCall, policy: Policy) -> None:
    fake = FakeXml()
    if call in POLICY_CAN_ALLOW and policy.rules:
        pytest.skip("Policy.read_only() allows what the JSON API already offers.")
    with client(fake, policy=policy) as qb:
        with pytest.raises(PolicyRefusedError):
            qb._transport.xml.call(call, target_of(call))
    assert not fake.sent


@pytest.mark.parametrize("call", XML_CALLS, ids=call_ids)
@pytest.mark.parametrize("realm", ["evil.example", "acme.quickbase.com.evil.example", "10.0.0.1"])
def test_a_host_that_isnt_quickbases_gets_nothing(call: XmlCall, realm: str) -> None:
    fake = FakeXml()
    with client(fake, realm=realm) as qb:
        with pytest.raises(ConfigurationError):
            qb._transport.xml.call(call, target_of(call))
    assert not fake.sent


def test_a_request_is_a_post_to_the_realm_with_the_action() -> None:
    fake = FakeXml()
    with client(fake, user_agent="tester") as qb:
        qb.apps()
    request = fake.sent[0]
    assert request.method == "POST"
    assert str(request.url) == "https://acme.quickbase.com/db/main"
    assert request.headers["QUICKBASE-ACTION"] == "API_GrantedDBs"
    assert request.headers["Content-Type"] == "application/xml"
    assert request.headers["User-Agent"].startswith("tester pyquickbase/")
    assert b"<realmAppsOnly>true</realmAppsOnly>" in request.content
    assert b"<withembeddedtables>0</withembeddedtables>" in request.content


def replies(*items: tuple[int, bytes]) -> Any:
    remaining = list(items)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = remaining.pop(0)
        return httpx.Response(status, content=body)

    handler.seen = seen  # type: ignore[attr-defined]  # pyright: ignore[reportFunctionMemberAccess]
    return handler


GOOD = (200, b"<qdbapi><errcode>0</errcode><databases/></qdbapi>")


@pytest.mark.parametrize(
    "first",
    [
        (503, b"busy"),
        (200, failure("X", 100, "Timeout")),
        (400, failure("X", 104, "Database")),
        (429, b""),
        (400, failure("X", 77, "Limit")),
    ],
    ids=["503", "errcode-100", "errcode-104", "429", "errcode-77"],
)
def test_temporary_failures_are_retried(first: tuple[int, bytes]) -> None:
    handler = replies(first, GOOD)
    with client(handler, retry=FAST) as qb:
        assert qb.apps() == []
    assert len(handler.seen) == 2


def test_rate_limited_after_the_retries_run_out() -> None:
    handler = replies(*[(400, failure("X", 77, "Limit"))] * 3)
    with client(handler, retry=FAST) as qb:
        with pytest.raises(RateLimitError):
            qb.apps()
    assert len(handler.seen) == 3


def test_a_temporary_code_is_a_server_error_once_the_retries_run_out() -> None:
    handler = replies(*[(400, failure("X", 101, "Temporary"))] * 3)
    with client(handler, retry=FAST) as qb:
        with pytest.raises(ServerError):
            qb.apps()


def test_a_permanent_failure_isnt_retried() -> None:
    handler = replies((403, failure("X", 3, "Denied")), GOOD)
    with client(handler, retry=FAST) as qb:
        with pytest.raises(PermissionDeniedError):
            qb.apps()
    assert len(handler.seen) == 1


def test_a_dropped_connection_is_retried_for_a_call_safe_to_repeat() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(GOOD[0], content=GOOD[1])

    with client(handler, retry=FAST) as qb:
        assert qb.apps() == []
    assert calls == 2


def test_a_redirect_is_never_followed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/db/main"})

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with Quickbase(realm=REALM, token=TOKEN, retry=NO_RETRY, http_client=http) as qb:
        with pytest.raises(Exception, match="302"):
            qb.apps()
    assert seen == ["https://acme.quickbase.com/db/main"]


def test_apps_are_listed_with_their_names_and_ancestors() -> None:
    with client(FakeXml()) as qb:
        apps = qb.apps(admin_only=False)
    assert [(item.id, item.name) for item in apps] == [
        (APP, "Field Jobs"),
        ("bq7654321", "Field Jobs (Staging)"),
    ]
    assert apps[0].ancestor_id is None
    assert apps[1].ancestor_id == APP
    assert apps[1].oldest_ancestor_id == APP
    assert apps[0].url == f"https://acme.quickbase.com/nav/app/{APP}/action/appoverview"


def test_admin_only_is_sent_when_asked_for() -> None:
    fake = FakeXml()
    with client(fake) as qb:
        qb.apps(admin_only=True)
    assert b"<adminOnly>1</adminOnly>" in fake.sent[0].content


def test_the_current_user_is_described() -> None:
    fake = FakeXml()
    with client(fake) as qb:
        me = qb.current_user()
    assert me.id == "58390212.abcd"
    assert (me.email, me.first_name, me.last_name) == (
        "jordan@example.com",
        "Jordan",
        "Rivera",
    )
    assert me.screen_name is None
    assert b"email" not in fake.sent[0].content


def test_a_user_is_found_by_email() -> None:
    fake = FakeXml()
    with client(fake) as qb:
        found = qb.find_user("  jordan@example.com ")
    assert found.id == "58390212.abcd"
    assert b"<email>jordan@example.com</email>" in fake.sent[0].content


def test_an_email_nobody_has_is_not_found() -> None:
    handler = replies((400, failure("API_GetUserInfo", 2, "Invalid input", "Does not exist.")))
    with client(handler) as qb:
        with pytest.raises(NotFoundError):
            qb.find_user("nobody@example.com")


def test_a_bad_token_is_reported_as_authentication() -> None:
    handler = replies((400, failure("X", 83, "Invalid error code: 83", "User token is invalid")))
    with client(handler) as qb:
        with pytest.raises(AuthenticationError):
            qb.current_user()


def test_the_registry_says_who_may_be_repeated() -> None:
    assert GRANTED_APPS.safe_to_repeat
    assert GRANTED_APPS.read
    assert FIND_USER.codes[2] is NotFoundError


def test_roles_fall_back_when_the_json_api_refuses() -> None:
    fake = FakeXml()
    with client(fake) as qb:
        roles = qb.app(APP).roles()
        qb.app(APP).roles()
    assert [(item.id, item.name, item.access) for item in roles] == [
        (10, "Viewer", "Basic Access"),
        (12, "Administrator", "Administrator"),
    ]
    assert fake.actions() == [None, "API_GetRoleInfo"]


def test_info_falls_back_when_the_json_api_refuses() -> None:
    fake = FakeXml()
    with client(fake) as qb:
        info = qb.app(APP).info()
    assert (info.id, info.name, info.description) == (APP, "Field Jobs", "Crews, sites, and tasks")
    assert info.time_zone == "(UTC-08:00) Pacific Time (US & Canada)"
    assert info.date_format == "MM-DD-YYYY"
    assert fake.actions() == [None, "API_GetSchema"]


def test_the_json_api_comes_first_when_it_answers() -> None:
    body = {"id": APP, "name": "Field Jobs", "description": ""}
    fake = FakeXml(json_status=200, json_body=body)
    with client(fake) as qb:
        assert (qb.app(APP).info()).name == "Field Jobs"
    assert fake.actions() == [None]


@pytest.mark.parametrize("method", ["info", "roles"])
def test_a_retired_fallback_reports_the_original_refusal(method: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/db/"):
            return httpx.Response(404, content=failure("X", 5, "No Such Operation"))
        return httpx.Response(403, json={"message": "Access denied"})

    with client(handler) as qb:
        with pytest.raises(PermissionDeniedError):
            getattr(qb.app(APP), method)()


@pytest.mark.parametrize("method", ["info", "roles"])
def test_a_fallback_read_is_refused_only_when_the_json_one_would_be(method: str) -> None:
    fake = FakeXml()
    with client(fake, policy=Policy()) as qb:
        with pytest.raises(PolicyRefusedError):
            getattr(qb.app(APP), method)()
    assert not fake.sent


def test_members_are_listed_with_their_roles() -> None:
    with client(FakeXml()) as qb:
        members = qb.app(APP).members()
    assert [(item.kind, item.name) for item in members] == [
        ("user", "Jordan Rivera"),
        ("group", "Crew Leads"),
    ]
    assert members[0].last_access is not None
    assert members[1].last_access is None
    assert [role.name for role in members[0].roles] == ["Administrator"]
    assert members[1].roles == ()


def test_a_members_roles_say_how_they_were_assigned() -> None:
    fake = FakeXml()
    with client(fake) as qb:
        found = qb.app(APP).member_roles()
        qb.app(APP).member_roles("58390212.abcd", include_groups=False)
    assert [(item.role.name, item.assigned_through) for item in found] == [("Viewer", "user")]
    assert b"<inclgrps>1</inclgrps>" in fake.sent[0].content
    assert b"userid" not in fake.sent[0].content
    assert b"<userid>58390212.abcd</userid>" in fake.sent[1].content
    assert b"inclgrps" not in fake.sent[1].content


def test_a_policy_can_never_allow_reading_people() -> None:
    from pyquickbase import allow, tables

    fake = FakeXml()
    bound = bind(
        allow.read(tables.all()),
        tables={"bqtasks": ("Tasks", [{"id": 3, "label": "Record ID#", "fieldType": "recordid"}])},
    )
    with client(fake, policy=bound) as qb:
        for attempt in (qb.apps, qb.current_user):
            with pytest.raises(PolicyRefusedError):
                attempt()
        for attempt in (qb.app(APP).members, qb.app(APP).member_roles):
            with pytest.raises(PolicyRefusedError):
                attempt()
    assert not fake.sent


def test_the_trustees_endpoint_is_refused_through_request_under_read_only() -> None:
    fake = FakeXml(json_status=200, json_body=[])
    with client(fake, policy=Policy.read_only()) as qb:
        with pytest.raises(PolicyRefusedError):
            qb.request("GET", f"/app/{APP}/trustees")
    assert not fake.sent


def test_a_malformed_list_is_reported_rather_than_crashing() -> None:
    fake = FakeXml(responses={"API_GrantedDBs": "<databases><dbinfo></dbinfo></databases>"})
    with client(fake) as qb:
        from pyquickbase import InvalidResponseError

        with pytest.raises(InvalidResponseError):
            qb.apps()
