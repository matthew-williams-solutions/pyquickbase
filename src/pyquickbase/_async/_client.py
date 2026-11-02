"""The async Quickbase client."""

import re
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

import httpx

from .._auth import UserTokenAuth
from .._config import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    build_user_agent,
    normalize_realm,
    read_env,
    validate_base_url,
    validate_token,
)
from .._retry import RetryPolicy
from .._schema import SchemaCache
from ..errors import ConfigurationError
from ._resources import AsyncApp
from ._transport import AsyncTransport

DEFAULT_SCHEMA_TTL = 300.0
_APP_ID = re.compile(r"[A-Za-z0-9]+")


class AsyncQuickbase:
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
        http_client: httpx.AsyncClient | None = None,
        schema_ttl: float = DEFAULT_SCHEMA_TTL,
    ) -> None:
        self._realm = normalize_realm(realm)
        self._transport = AsyncTransport(
            realm=self._realm,
            auth=UserTokenAuth(validate_token(token)),
            base_url=validate_base_url(base_url),
            retry=RetryPolicy() if retry is None else retry,
            timeout=timeout,
            user_agent=build_user_agent(user_agent),
            http_client=http_client,
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
        http_client: httpx.AsyncClient | None = None,
        schema_ttl: float = DEFAULT_SCHEMA_TTL,
    ) -> Self:
        """Create a client from the `QB_REALM` and `QB_USER_TOKEN` environment variables."""
        realm, token = read_env()
        return cls(
            realm=realm,
            token=token,
            base_url=base_url,
            retry=retry,
            timeout=timeout,
            user_agent=user_agent,
            http_client=http_client,
            schema_ttl=schema_ttl,
        )

    @property
    def realm(self) -> str:
        """The full realm hostname, such as `acme.quickbase.com`."""
        return self._realm

    def app(self, app_id: str) -> AsyncApp:
        """Get an app by ID, the `bq...` part of its URL. This makes no network calls."""
        if not _APP_ID.fullmatch(app_id):
            raise ConfigurationError("An app ID contains only letters and numbers.")
        return AsyncApp(self._transport, self._schema, app_id)

    def clear_schema_cache(self) -> None:
        """Forget cached metadata, for example after changing a table's fields."""
        self._schema.clear()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Call any Quickbase endpoint directly and return its decoded JSON body.

        This is the escape hatch for endpoints without a dedicated method. `path` is relative
        to the API root, such as `/tables/abc123`, and the body is None if the response is empty.
        """
        return await self._transport.request(method, path, params=params, json=json)

    async def aclose(self) -> None:
        """Close connections. Safe to call more than once."""
        await self._transport.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(realm={self._realm!r})"
