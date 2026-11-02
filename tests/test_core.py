"""Tests for the code shared by both clients, which does no network access."""

import pickle
from datetime import UTC, datetime

import pytest

import pyquickbase
from pyquickbase import APIError, ConfigurationError, QuickbaseError, RateLimitError, RetryPolicy
from pyquickbase._auth import UserTokenAuth
from pyquickbase._config import (
    build_user_agent,
    normalize_realm,
    read_env,
    validate_base_url,
    validate_token,
)
from pyquickbase._retry import parse_retry_after


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("acme", "acme.quickbase.com"),
        ("ACME.quickbase.com", "acme.quickbase.com"),
        ("https://acme.quickbase.com/db/main", "acme.quickbase.com"),
        (" acme.quickbase.eu ", "acme.quickbase.eu"),
    ],
)
def test_normalize_realm(given: str, expected: str) -> None:
    assert normalize_realm(given) == expected


@pytest.mark.parametrize("realm", ["", "   ", "https://", "bad realm", "a..b", "a_b.quickbase.com"])
def test_normalize_realm_rejects_invalid(realm: str) -> None:
    with pytest.raises(ConfigurationError):
        normalize_realm(realm)


def test_validate_token_strips_whitespace() -> None:
    assert validate_token("  abc\n") == "abc"


@pytest.mark.parametrize("token", ["", "   ", "abc def_secret", "abc\r\nX-Evil: 1", "abc\x00"])
def test_validate_token_rejects_invalid_without_echoing(token: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        validate_token(token)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "url", ["https://api.quickbase.com/v1", "http://localhost:8000", "http://127.0.0.1:8000/v1/"]
)
def test_validate_base_url_accepts(url: str) -> None:
    assert validate_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", ["http://api.quickbase.com/v1", "ftp://x", "api.quickbase.com"])
def test_validate_base_url_rejects(url: str) -> None:
    with pytest.raises(ConfigurationError):
        validate_base_url(url)


def test_user_agent() -> None:
    assert build_user_agent(None).startswith("pyquickbase/")
    assert build_user_agent("app/1").startswith("app/1 pyquickbase/")


def test_read_env() -> None:
    env = {"QB_REALM": "acme", "QB_USER_TOKEN": "abc"}
    assert read_env(env) == ("acme", "abc")


def test_read_env_reports_all_missing() -> None:
    with pytest.raises(ConfigurationError, match="QB_REALM, QB_USER_TOKEN"):
        read_env({"QB_REALM": "  "})


def test_auth_repr_hides_token() -> None:
    assert "secret" not in repr(UserTokenAuth("secret"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("7", 7.0),
        (" 1.5 ", 1.5),
        ("soon", None),
        ("-3", None),
        ("nan", None),
        ("Mon, 02 Nov 2026 10:00:30 GMT", 30.0),
        ("Mon, 02 Nov 2026 10:00:30 -0000", 30.0),
        ("Mon, 02 Nov 2026 09:00:00 GMT", 0.0),
    ],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    now = datetime(2026, 11, 2, 10, 0, 0, tzinfo=UTC)
    assert parse_retry_after(value, now=now) == expected


def test_parse_retry_after_uses_current_time() -> None:
    assert parse_retry_after("Thu, 01 Jan 2015 00:00:00 GMT") == 0.0


def test_backoff_is_capped_and_grows() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=8.0)
    assert 0.5 <= policy.backoff(0) <= 1.0
    assert 2.0 <= policy.backoff(2) <= 4.0
    assert 4.0 <= policy.backoff(5000) <= 8.0


def test_rate_limit_delay() -> None:
    policy = RetryPolicy(max_retry_after=10.0)
    assert policy.rate_limit_delay(0, 10.0) == 10.0
    assert policy.rate_limit_delay(0, 10.5) is None
    assert 0.5 <= (policy.rate_limit_delay(0, None) or 0) <= 1.0


@pytest.mark.parametrize(
    "kwargs",
    [{"max_retries": -1}, {"base_delay": -1}, {"max_delay": -1}, {"max_retry_after": -1}],
)
def test_retry_policy_rejects_negative_values(kwargs: dict[str, float]) -> None:
    with pytest.raises(ConfigurationError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


def test_errors_survive_pickling() -> None:
    error = RateLimitError("summary", retry_after=5.0, status_code=429, request_id="ray")
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is RateLimitError
    assert str(restored) == "summary"
    assert (restored.retry_after, restored.status_code, restored.request_id) == (5.0, 429, "ray")


def test_error_hierarchy() -> None:
    assert issubclass(APIError, QuickbaseError)
    assert issubclass(ConfigurationError, ValueError)


def test_public_exports_exist() -> None:
    assert all(hasattr(pyquickbase, name) for name in pyquickbase.__all__)
    assert not [
        name for name in pyquickbase.__all__ if name.startswith("_") and name != "__version__"
    ]
