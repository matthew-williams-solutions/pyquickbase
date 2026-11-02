import logging
from collections.abc import Callable

import httpx
import pytest

from pyquickbase import (
    APIError,
    AsyncQuickbase,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    InvalidResponseError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ReadOnlyError,
    RequestTimeoutError,
    RetryPolicy,
    ServerError,
)

pytestmark = pytest.mark.anyio

TOKEN = "fake-user-token"
Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kwargs: object) -> AsyncQuickbase:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncQuickbase(realm="acme", token=TOKEN, http_client=http_client, **kwargs)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr("pyquickbase._async._transport.anyio.sleep", fake_sleep)
    return calls


async def test_sends_quickbase_headers_and_decodes_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "abc"})

    async with make_client(handler, user_agent="my-app/1.0") as qb:
        assert await qb.request("get", "/apps/abc", params={"x": "1"}) == {"id": "abc"}

    request = seen[0]
    assert request.method == "GET"
    assert str(request.url) == "https://api.quickbase.com/v1/apps/abc?x=1"
    assert request.headers["QB-Realm-Hostname"] == "acme.quickbase.com"
    assert request.headers["Authorization"] == f"QB-USER-TOKEN {TOKEN}"
    assert request.headers["User-Agent"].startswith("my-app/1.0 pyquickbase/")


async def test_sends_json_body() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200, json={})

    async with make_client(handler) as qb:
        await qb.request("POST", "/records/query", json={"from": "t"})

    assert b'"from"' in bodies[0]


async def test_empty_response_is_none() -> None:
    async with make_client(lambda _: httpx.Response(204)) as qb:
        assert await qb.request("DELETE", "/x") is None


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, BadRequestError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, NotFoundError),
        (409, APIError),
        (429, RateLimitError),
        (500, ServerError),
        (503, ServerError),
    ],
)
async def test_maps_status_codes_to_errors(status: int, error_type: type[APIError]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"message": "Nope", "description": "Not allowed"},
            headers={"qb-api-ray": "ray-1"},
        )

    async with make_client(handler, retry=RetryPolicy(max_retries=0)) as qb:
        with pytest.raises(error_type) as caught:
            await qb.request("GET", "/apps/abc", params={"secret": "hidden"})

    error = caught.value
    assert type(error) is error_type
    assert error.status_code == status
    assert (error.method, error.path) == ("GET", "/apps/abc")
    assert (error.message, error.description) == ("Nope", "Not allowed")
    assert error.request_id == "ray-1"
    assert "Nope: Not allowed" in str(error)
    assert "ray-1" in str(error)
    assert "hidden" not in str(error)


async def test_non_json_error_body_is_truncated() -> None:
    async with make_client(lambda _: httpx.Response(502, text="x" * 2000)) as qb:
        with pytest.raises(ServerError) as caught:
            await qb.request("GET", "/x")
    assert caught.value.message == "x" * 500


async def test_json_error_body_that_is_not_an_object() -> None:
    async with make_client(lambda _: httpx.Response(400, json=["bad"])) as qb:
        with pytest.raises(BadRequestError) as caught:
            await qb.request("GET", "/x")
    assert caught.value.message is None


async def test_invalid_json_on_success_raises() -> None:
    async with make_client(lambda _: httpx.Response(200, text="<html>")) as qb:
        with pytest.raises(InvalidResponseError):
            await qb.request("GET", "/x")


async def test_retries_rate_limit_using_retry_after(sleeps: list[float]) -> None:
    responses = [
        httpx.Response(429, headers={"retry-after": "7"}),
        httpx.Response(200, json={"ok": True}),
    ]
    async with make_client(lambda _: responses.pop(0)) as qb:
        assert await qb.request("POST", "/records/query", json={}) == {"ok": True}
    assert sleeps == [7.0]


async def test_rate_limit_without_header_backs_off(sleeps: list[float]) -> None:
    responses = [httpx.Response(429), httpx.Response(200, json={})]
    policy = RetryPolicy(base_delay=2.0)
    async with make_client(lambda _: responses.pop(0), retry=policy) as qb:
        await qb.request("GET", "/x")
    assert 1.0 <= sleeps[0] <= 2.0


async def test_rate_limit_retries_run_out(sleeps: list[float]) -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "1"})

    async with make_client(handler, retry=RetryPolicy(max_retries=2)) as qb:
        with pytest.raises(RateLimitError) as caught:
            await qb.request("GET", "/x")
    assert len(calls) == 3
    assert len(sleeps) == 2
    assert caught.value.retry_after == 1.0


async def test_excessive_retry_after_fails_immediately(sleeps: list[float]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "3600"})

    async with make_client(handler) as qb:
        with pytest.raises(RateLimitError) as caught:
            await qb.request("GET", "/x")
    assert sleeps == []
    assert caught.value.retry_after == 3600.0


async def test_server_errors_are_not_retried(sleeps: list[float]) -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    async with make_client(handler) as qb:
        with pytest.raises(ServerError):
            await qb.request("POST", "/records", json={})
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("failure", [httpx.ConnectError("x"), httpx.ConnectTimeout("x")])
async def test_retries_connection_failures(sleeps: list[float], failure: Exception) -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise failure
        return httpx.Response(200, json={})

    async with make_client(handler) as qb:
        assert await qb.request("POST", "/records", json={}) == {}
    assert len(sleeps) == 1


async def test_connection_retries_run_out(sleeps: list[float]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    async with make_client(handler, retry=RetryPolicy(max_retries=1)) as qb:
        with pytest.raises(NetworkError) as caught:
            await qb.request("GET", "/x")
    assert len(sleeps) == 1
    assert isinstance(caught.value.__cause__, httpx.ConnectError)


async def test_read_timeout_on_write_is_not_retried(sleeps: list[float]) -> None:
    calls: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("x")

    async with make_client(handler) as qb:
        with pytest.raises(RequestTimeoutError):
            await qb.request("POST", "/records", json={})
    assert len(calls) == 1
    assert sleeps == []


async def test_read_error_is_not_retried(sleeps: list[float]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("x")

    async with make_client(handler) as qb:
        with pytest.raises(NetworkError):
            await qb.request("POST", "/records", json={})
    assert sleeps == []


@pytest.mark.parametrize(
    "path", ["https://evil.example/x", "//evil.example/x", "tables", "/a?b=1", "/a#b", "/a\\b"]
)
async def test_refuses_paths_that_could_leak_the_token(path: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be sent")

    async with make_client(handler) as qb:
        with pytest.raises(ValueError, match="path must"):
            await qb.request("GET", path)


async def test_token_never_appears_in_output(
    caplog: pytest.LogCaptureFixture, sleeps: list[float]
) -> None:
    caplog.set_level(logging.DEBUG)
    responses = [httpx.Response(429), httpx.Response(401, json={"message": "Denied"})]
    async with make_client(lambda _: responses.pop(0)) as qb:
        with pytest.raises(AuthenticationError) as caught:
            await qb.request("GET", "/x")
        client_repr = repr(qb)

    error = caught.value
    outputs = [client_repr, str(error), repr(error), repr(vars(error)), caplog.text]
    assert all(TOKEN not in output for output in outputs)
    assert len(sleeps) == 1


async def test_closes_its_own_http_client() -> None:
    qb = AsyncQuickbase(realm="acme", token=TOKEN)
    http_client = qb._transport._http
    async with qb:
        assert not http_client.is_closed
    assert http_client.is_closed
    await qb.aclose()


async def test_leaves_a_supplied_http_client_open() -> None:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    async with AsyncQuickbase(realm="acme", token=TOKEN, http_client=http_client):
        pass
    assert not http_client.is_closed
    await http_client.aclose()


async def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QB_REALM", "acme.quickbase.com")
    monkeypatch.setenv("QB_USER_TOKEN", TOKEN)
    async with AsyncQuickbase.from_env() as qb:
        assert qb.realm == "acme.quickbase.com"


async def test_from_env_names_missing_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QB_REALM", raising=False)
    monkeypatch.setenv("QB_USER_TOKEN", TOKEN)
    with pytest.raises(ConfigurationError, match="QB_REALM"):
        AsyncQuickbase.from_env()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/records"),
        ("post", "/records"),
        ("DELETE", "/records"),
        ("POST", "/records/query/"),
        ("POST", "/records/query/../../records"),
        ("POST", "/tables"),
        ("PUT", "/records/query"),
        ("PATCH", "/apps/abc"),
        ("HEAD", "/apps/abc"),
    ],
)
async def test_read_only_client_never_sends_anything_else(method: str, path: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with make_client(handler, read_only=True) as qb:
        with pytest.raises(ReadOnlyError, match="read-only"):
            await qb.request(method, path, json={})
    assert seen == []


@pytest.mark.parametrize(("method", "path"), [("GET", "/apps/abc"), ("POST", "/records/query")])
async def test_read_only_client_allows_reads(method: str, path: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with make_client(handler, read_only=True) as qb:
        assert await qb.request(method, path) == {"ok": True}


async def test_allowed_writes_permit_only_exact_matches() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={})

    async with make_client(handler, allowed_writes={("post", "/records")}) as qb:
        await qb.request("POST", "/records", json={})
        await qb.request("POST", "/records/query", json={})
        await qb.request("GET", "/apps/abc")
        for method, path in (("DELETE", "/records"), ("POST", "/tables"), ("POST", "/records/")):
            with pytest.raises(ReadOnlyError, match="specific writes"):
                await qb.request(method, path, json={})
    assert seen == ["POST /v1/records", "POST /v1/records/query", "GET /v1/apps/abc"]


async def test_empty_allowed_writes_is_read_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing should be sent")  # pragma: no cover

    async with make_client(handler, allowed_writes=[]) as qb:
        with pytest.raises(ReadOnlyError, match="read-only"):
            await qb.request("POST", "/records", json={})


async def test_read_only_and_allowed_writes_conflict() -> None:
    with pytest.raises(ConfigurationError, match="not both"):
        make_client(lambda request: httpx.Response(200), read_only=True, allowed_writes=[])
