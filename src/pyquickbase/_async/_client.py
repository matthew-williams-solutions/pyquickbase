"""The async Quickbase client."""

import re
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self

import httpx

from .._auth import UserTokenAuth
from .._bound import BoundPolicy
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
from .._models import AppListing, UserAccount, parse
from .._policy import Policy
from .._retry import RetryPolicy
from .._schema import SchemaCache
from .._xml import CURRENT_USER, FIND_USER, GRANTED_APPS, apps_from_xml, user_from_xml
from ..errors import ConfigurationError, InvalidValueError
from ._resources import AsyncApp, app_from_payload, require_app_name
from ._transport import AsyncTransport

DEFAULT_SCHEMA_TTL = 300.0
_MIN_KEY_BYTES = 32
_APP_ID = re.compile(r"[A-Za-z0-9]+")


class AsyncQuickbase:
    """A Quickbase API client. Use it as a context manager so connections get closed.

    Args:
        realm: Your realm, as `acme`, `acme.quickbase.com`, or a realm URL.
        token: A Quickbase user token.
        base_url: The API root. Must use HTTPS, except for loopback addresses.
        retry: How to retry rate limits, connection failures, and reads that time out or get a
            502, 503, or 504. A write isn't retried after anything that may have reached Quickbase.
        timeout: Seconds to allow for each step of a request, such as connecting or waiting for
            the response, as httpx applies it. Ignored if you pass `http_client`.
        user_agent: Identifies your application, placed before the SDK's own User-Agent.
        http_client: Your own configured httpx client, for proxies, custom TLS, event hooks,
            or custom transports. You remain responsible for closing it.
        metadata_cache_seconds: Seconds to cache app, table, and field metadata. Use 0 to
            disable caching.
        policy: What the client may do, as a `BoundPolicy` from `Policy.bind()`, or a `Policy`
            that names no table or field, such as `Policy.read_only()`. Every request is checked
            before it's sent, including through `request()`, and a refused one raises
            `PolicyRefusedError`. What the policy hides is left out of every response, so a hidden
            field acts as if it didn't exist. Without one, the client does whatever the token can.
        confirmation_key: The secret that signs the confirmations an operation's preview returns,
            at least 32 random bytes, such as from `secrets.token_bytes(32)`. Give several clients,
            or processes, the same key so that any of them accepts a preview another made. Without
            it, each client makes its own.

    Raises:
        ConfigurationError: `realm`, `token`, or `base_url` can't be used, or `policy` names
            tables or fields but isn't bound.
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
        metadata_cache_seconds: float = DEFAULT_SCHEMA_TTL,
        policy: Policy | BoundPolicy | None = None,
        confirmation_key: bytes | None = None,
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
            policy=_check_policy(policy),
            confirmation_key=_check_key(confirmation_key),
        )
        self._schema = SchemaCache(metadata_cache_seconds)

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str = DEFAULT_BASE_URL,
        retry: RetryPolicy | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        metadata_cache_seconds: float = DEFAULT_SCHEMA_TTL,
        policy: Policy | BoundPolicy | None = None,
        confirmation_key: bytes | None = None,
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
            metadata_cache_seconds=metadata_cache_seconds,
            policy=policy,
            confirmation_key=confirmation_key,
        )

    @property
    def policy(self) -> BoundPolicy | None:
        """The client's current policy. Creating a table replaces it with one that knows the
        table, so save this one with `to_json()` to use it elsewhere."""
        return self._transport.policy

    @property
    def realm(self) -> str:
        """The full realm hostname, such as `acme.quickbase.com`."""
        return self._realm

    def app(self, app_id: str) -> AsyncApp:
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
        return AsyncApp(self._transport, self._schema, app_id)

    async def apps(self, *, admin_only: bool = False) -> list[AppListing]:
        """List the apps in this realm that the token can reach.

        A token assigned to some apps lists only those, even if its user can reach more. Each app
        comes with its name, including for a role that can't read the app's details. An app that
        isn't listed doesn't exist or isn't assigned to the token, and Quickbase can't say which.

        Args:
            admin_only: List only the apps the token's user administers.

        Returns:
            An `AppListing` for each app.

        Raises:
            AuthenticationError: The token isn't valid.
            ConfigurationError: The realm isn't on `quickbase.com` or `quickbase.eu`, or the client
                has a custom `base_url`. Nothing was sent.
            PolicyRefusedError: The client has a policy, which covers one app. Nothing was sent.
        """
        fields: dict[str, str | int] = {
            "realmAppsOnly": "true",
            "withembeddedtables": 0,
            "includeancestors": 1,
        }
        if admin_only:
            fields["adminOnly"] = 1
        root = await self._transport.xml.call(GRANTED_APPS, "main", fields)
        return parse(lambda found: apps_from_xml(found, self._realm), root, "apps")

    async def current_user(self) -> UserAccount:
        """Get the user the token belongs to.

        Returns:
            A `UserAccount`.

        Raises:
            AuthenticationError: The token isn't valid.
            ConfigurationError: The realm isn't on `quickbase.com` or `quickbase.eu`, or the client
                has a custom `base_url`. Nothing was sent.
            PolicyRefusedError: The client has a policy. Nothing was sent.
        """
        root = await self._transport.xml.call(CURRENT_USER, "main")
        return parse(user_from_xml, root, "user")

    async def find_user(self, email: str) -> UserAccount:
        """Find a user by email address, such as to give them a role.

        Args:
            email: Their email address.

        Returns:
            A `UserAccount`.

        Raises:
            InvalidValueError: `email` is empty. Nothing was sent.
            NotFoundError: No user has this email address.
            ConfigurationError: The realm isn't on `quickbase.com` or `quickbase.eu`, or the client
                has a custom `base_url`. Nothing was sent.
            PolicyRefusedError: The client has a policy. Nothing was sent.
        """
        if not isinstance(email, str) or not email.strip():  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidValueError("find_user() needs an email address.")
        root = await self._transport.xml.call(FIND_USER, "main", {"email": email.strip()})
        return parse(user_from_xml, root, "user")

    async def create_app(
        self, name: str, *, description: str | None = None, assign_token: bool = True
    ) -> AsyncApp:
        """Create an empty app and return it. Use `app.copy()` to start from another app.

        Args:
            name: The app's name. Quickbase allows several apps with the same name.
            description: The app's description.
            assign_token: Let this client's token reach the app, which a token limited to some
                apps can't otherwise do.

        Returns:
            An `App` for the new app.

        Raises:
            InvalidValueError: `name` is empty. Nothing was sent.
            PermissionDeniedError: The token's user can't create apps in this realm.
            PolicyRefusedError: The client has a policy, which covers one app. Nothing was sent.
        """
        body = {
            "name": require_app_name(name, "create"),
            "assignToken": assign_token,
            **({} if description is None else {"description": description}),
        }
        payload = await self._transport.request("POST", "/apps", json=body)
        return app_from_payload(self._transport, self._schema, payload)

    def clear_metadata_cache(self) -> None:
        """Forget cached metadata, for example after changing a table's fields.

        Use this after a change made outside this client, such as in the Quickbase interface. The
        client's own schema methods keep the cache current.
        """
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

        This is the escape hatch for endpoints without a dedicated method. The client's retries
        and its policy still apply.

        Args:
            method: The HTTP method, such as `GET`.
            path: The endpoint, relative to the API root, such as `/tables/abc123`.
            params: Values to send in the query string.
            json: The value to send as the JSON body.

        Returns:
            The decoded JSON body, or None if the response is empty.

        Raises:
            PolicyRefusedError: The client's policy doesn't allow this request. It wasn't sent.
            APIError: Quickbase returned an error. This is a subclass such as `NotFoundError` or
                `PermissionDeniedError`, for the status it returned.
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


def _check_policy(policy: Any) -> BoundPolicy | None:
    if policy is None or isinstance(policy, BoundPolicy):
        return policy
    if isinstance(policy, Policy):
        return policy.bind_offline()
    raise ConfigurationError("policy takes a Policy or a BoundPolicy.")


def _check_key(key: bytes | None) -> bytes | None:
    if key is None:
        return None
    if not isinstance(key, bytes) or len(key) < _MIN_KEY_BYTES:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ConfigurationError(
            f"confirmation_key must be at least {_MIN_KEY_BYTES} random bytes, such as from "
            "secrets.token_bytes(32)."
        )
    return key
