# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""The async Quickbase client."""

import re
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

import httpx

from .._auth import UserTokenAuth
from .._combine import check_read_policy, check_write_policy
from .._config import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    ENV_TOKEN,
    build_user_agent,
    normalize_realm,
    read_env,
    validate_base_url,
    validate_token,
)
from .._policy import WritePolicy
from .._read_policy import ReadPolicy
from .._retry import RetryPolicy
from .._schema import SchemaCache
from ..errors import ConfigurationError
from ._resources import App
from ._transport import Transport

DEFAULT_SCHEMA_TTL = 300.0
_APP_ID = re.compile(r"[A-Za-z0-9]+")


class Quickbase:
    """A Quickbase API client. Use it as a context manager so connections get closed.

    Args:
        realm: Your realm, as `acme`, `acme.quickbase.com`, or a realm URL.
        token: A Quickbase user token.
        base_url: The API root. Must use HTTPS, except for loopback addresses.
        retry: How to retry rate limits and connection failures.
        timeout: Request timeout in seconds. Ignored if you pass `http_client`.
        user_agent: Identifies your application, placed before the SDK's own User-Agent.
        http_client: Your own configured httpx client, for proxies, custom TLS, event hooks,
            or custom transports. You remain responsible for closing it.
        schema_ttl: Seconds to cache app, table, and field metadata. Use 0 to disable caching.
        read_only: Refuse any request that could change data, raising `WriteRefusedError`. Only GET
            requests, record queries, report runs, and formula runs are sent, including through
            `request()`. Running a report adds to its usage count in Quickbase, but changes no
            records.
        write_policy: A function that decides on each write, usually `WriteRules`, for limits by
            table, action, and field. It takes a `WriteRequest`, and only a return value of `True`
            lets the write through. Otherwise, it raises `WriteRefusedError` and isn't sent.
        read_policy: A function that decides on each read, usually `ReadRules`, for limits by
            table, field, report, and number of records. It takes a `ReadRequest`, and only a
            return value of `True` lets the read through. Otherwise, it raises `ReadRefusedError`
            and isn't sent. `ReadRules` also leaves out of each response, and of metadata, what it
            doesn't allow. It works with any of the limits on writes.

    Raises:
        ConfigurationError: `realm`, `token`, or `base_url` can't be used, or the limits on writes
            conflict, such as `read_only` with `write_policy`, or a policy is passed as the wrong
            kind.
    """

    def __init__(
        self,
        *,
        realm: str,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        retry: RetryPolicy | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
        http_client: httpx.Client | None = None,
        schema_ttl: float = DEFAULT_SCHEMA_TTL,
        read_only: bool = False,
        write_policy: WritePolicy | None = None,
        read_policy: ReadPolicy | None = None,
    ) -> None:
        self._realm = normalize_realm(realm)
        self._transport = Transport(
            realm=self._realm,
            auth=UserTokenAuth(validate_token(token)),
            base_url=validate_base_url(base_url),
            retry=RetryPolicy() if retry is None else retry,
            timeout=timeout,
            user_agent=build_user_agent(user_agent),
            http_client=http_client,
            read_only=read_only,
            write_policy=check_write_policy(write_policy, read_only=read_only),
            read_policy=check_read_policy(read_policy),
        )
        self._schema = SchemaCache(schema_ttl)

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str = DEFAULT_BASE_URL,
        retry: RetryPolicy | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
        http_client: httpx.Client | None = None,
        schema_ttl: float = DEFAULT_SCHEMA_TTL,
        read_only: bool = False,
        write_policy: WritePolicy | None = None,
        read_policy: ReadPolicy | None = None,
        token_env: str = ENV_TOKEN,
    ) -> Self:
        """Create a client from the `QB_REALM` and `QB_USER_TOKEN` environment variables.

        It takes the same options as the constructor, other than `realm` and `token`.

        Args:
            token_env: The name of the variable that holds the token, for a program that has
                several.

        Returns:
            A new client.

        Raises:
            ConfigurationError: A variable is missing or empty, or an option can't be used.
        """
        realm, token = read_env(token_env=token_env)
        return cls(
            realm=realm,
            token=token,
            base_url=base_url,
            retry=retry,
            timeout=timeout,
            user_agent=user_agent,
            http_client=http_client,
            schema_ttl=schema_ttl,
            read_only=read_only,
            write_policy=write_policy,
            read_policy=read_policy,
        )

    @property
    def realm(self) -> str:
        """The full realm hostname, such as `acme.quickbase.com`."""
        return self._realm

    def app(self, app_id: str) -> App:
        """Get an app by ID, the `bq...` part of its URL. This makes no network calls.

        Args:
            app_id: The app's ID, which contains only letters and numbers.

        Returns:
            An `App`. An ID that doesn't exist only fails on first use.

        Raises:
            ConfigurationError: `app_id` contains anything but letters and numbers.
        """
        if not _APP_ID.fullmatch(app_id):
            raise ConfigurationError("An app ID contains only letters and numbers.")
        return App(self._transport, self._schema, app_id)

    def clear_schema_cache(self) -> None:
        """Forget cached metadata, for example after changing a table's fields.

        Use this after a change made outside this client, such as in the Quickbase interface. The
        client's own schema methods keep the cache current.
        """
        self._schema.clear()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Call any Quickbase endpoint directly and return its decoded JSON body.

        This is the escape hatch for endpoints without a dedicated method. The client's retries
        and its limits on writes and reads still apply.

        Args:
            method: The HTTP method, such as `GET`.
            path: The endpoint, relative to the API root, such as `/tables/abc123`.
            params: Values to send in the query string.
            json: The value to send as the JSON body.

        Returns:
            The decoded JSON body, or None if the response is empty.

        Raises:
            WriteRefusedError: The client's limits on writes don't allow this request. It wasn't
                sent.
            ReadRefusedError: The client's `read_policy` doesn't allow this request. It wasn't
                sent.
            APIError: Quickbase returned an error. This is a subclass such as `NotFoundError` or
                `PermissionDeniedError`, for the status it returned.
        """
        return self._transport.request(method, path, params=params, json=json)

    def close(self) -> None:
        """Close connections. Safe to call more than once."""
        self._transport.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(realm={self._realm!r})"
