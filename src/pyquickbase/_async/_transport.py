"""Send one logical request: auth, headers, retries, and error mapping."""

import secrets
from collections.abc import Mapping
from typing import Any, cast

import anyio
import httpx

from .._bound import BoundPolicy
from .._config import build_url, is_read
from .._decision import Decision
from .._read_request import ReadRequest, describe_read
from .._response import error_from_response, error_from_transport, parse_response
from .._retry import RetryPolicy, parse_retry_after
from .._write_request import describe_write
from ..errors import PolicyRefusedError

# Failures where nothing reached Quickbase, so any request is safe to send again.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# Failures after a request may have reached Quickbase, which only a read is safe to send again.
_READ_FAILURES = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
# A gateway or overloaded server, which only a read is safe to send again.
_READ_RETRY_STATUSES = frozenset({502, 503, 504})


class AsyncTransport:
    def __init__(
        self,
        *,
        realm: str,
        auth: httpx.Auth,
        base_url: str,
        retry: RetryPolicy,
        timeout: float,
        user_agent: str,
        http_client: httpx.AsyncClient | None,
        policy: BoundPolicy | None = None,
        confirmation_key: bytes | None = None,
    ) -> None:
        self.realm = realm
        # Signs the confirmations previews return. Without a shared key, each client makes its own,
        # so a confirmation only works with operations from the client that issued it.
        self.confirmation_key = (
            secrets.token_bytes(32) if confirmation_key is None else confirmation_key
        )
        # Replaced, never changed, when this client creates a table.
        self.policy = policy
        self._auth = auth
        self._base_url = base_url
        self._retry = retry
        self._headers = {"QB-Realm-Hostname": realm, "User-Agent": user_agent}
        self._owns_http = http_client is None
        self._http = httpx.AsyncClient(timeout=timeout) if http_client is None else http_client

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        key_field_id: int | None = None,
        parent_table_id: str | None = None,
        sources_checked: bool = False,
        report_type: str | None = None,
        summarizing: bool = False,
        timeout: float | None = None,
        counted: bool = False,
    ) -> Any:
        """Send a request, and return its decoded body, trimmed by the policy.

        `report_type` and `summarizing` describe a read the request can't, as `describe_read()`
        explains, and `parent_table_id` a relationship's parent. `sources_checked` says that a
        formula, lookup, or summary field only reads what the client may read, and `counted` that
        a delete was counted against the policy's `max_records`. `request()` on the client can't
        pass any of these.
        """
        method = method.upper()
        json = self._limit_rows(method, path, json)
        self._check_write(
            method,
            path,
            params,
            json,
            key_field_id=key_field_id,
            parent_table_id=parent_table_id,
            sources_checked=sources_checked,
            counted=counted,
        )
        read = self._check_read(method, path, params, json, report_type, summarizing)
        response = await self._send(method, path, params, json, timeout)
        payload = parse_response(response, method, path)
        if self.policy is not None:
            payload = self.policy.trim(read, method, path, payload)
        return payload

    @property
    def limited(self) -> bool:
        """Whether the client has a policy."""
        return self.policy is not None

    def summary_limit(self, table_id: str) -> int | None:
        """The most records the policy lets `summarize()` read from a table, if any."""
        return None if self.policy is None else self.policy.max_summary_records(table_id)

    def record_limit(self, table_id: str) -> int | None:
        """The most records the policy lets one query reach in a table, if any."""
        return None if self.policy is None else self.policy.max_records(table_id)

    def delete_limit(self, table_id: str) -> int | None:
        """The most records the policy lets one delete remove from a table, if any."""
        return None if self.policy is None else self.policy.max_delete(table_id)

    def check_write(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        parent_table_id: str | None = None,
    ) -> None:
        """Raise what the policy would raise for this write, without sending it.

        A plan uses this to report a change the client would refuse. Formulas, lookups, and
        summaries count as checked, as a plan checks what they read itself.
        """
        self._check_write(
            method.upper(),
            path,
            params,
            json,
            parent_table_id=parent_table_id,
            sources_checked=True,
            counted=True,
        )

    def table_created(self, table_id: str, name: str) -> None:
        """Give the client a new policy that knows the table it just created."""
        if self.policy is not None:
            self.policy = self.policy.with_created(table_id, name)

    def _limit_rows(self, method: str, path: str, json: Any) -> Any:
        """AND the policy's row limit onto a query or delete."""
        if self.policy is None or path not in ("/records/query", "/records"):
            return json
        if (method, path) not in (("POST", "/records/query"), ("DELETE", "/records")):
            return json
        if not isinstance(json, Mapping):
            return json
        body = cast(Mapping[str, Any], json)
        table_id = body.get("from")
        limit = self.policy.row_limit(table_id) if isinstance(table_id, str) else None
        if limit is None:
            return body
        where = body.get("where")
        combined = (
            limit if not isinstance(where, str) or not where.strip() else f"({limit})AND({where})"
        )
        return {**body, "where": combined}

    def _check_write(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
        *,
        key_field_id: int | None = None,
        parent_table_id: str | None = None,
        sources_checked: bool = False,
        counted: bool = False,
    ) -> None:
        """Apply the policy to a write, which comes first, so a refused write says so."""
        policy = self.policy
        if policy is None or is_read(method, path):
            return
        request = describe_write(
            method,
            path,
            json,
            params=params,
            key_field_id=key_field_id,
            parent_table_id=parent_table_id,
        )
        if request.derived and not sources_checked:
            kinds = " and ".join(sorted(request.derived))
            raise PolicyRefusedError(
                Decision(
                    allowed=False,
                    verb="formula" if "formula" in request.derived else "lookup",
                    layer="derived",
                    reason=f"This client has a policy, so it only makes {kinds} fields through "
                    "methods such as create_field() and ensure_lookup_field(), which check what "
                    f"they read, not {method} {path}",
                )
            )
        decision = policy.decide(request)
        if not decision.allowed:
            raise PolicyRefusedError(decision)
        table_id = request.table_id
        if request.action == "delete" and table_id and not counted:
            most = policy.max_delete(table_id)
            if most is not None:
                raise PolicyRefusedError(
                    Decision(
                        allowed=False,
                        verb="delete",
                        rule=decision.rule,
                        reason=f"The policy allows deleting at most {most} records at once, so "
                        "only table.delete(), which counts them first, can delete here",
                        table=decision.table,
                    )
                )

    def _check_read(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
        report_type: str | None,
        summarizing: bool,
    ) -> ReadRequest | None:
        if self.policy is None:
            return None
        read = describe_read(
            method, path, params, json, report_type=report_type, summarizing=summarizing
        )
        if read is not None:
            decision = self.policy.decide(read)
            if not decision.allowed:
                raise PolicyRefusedError(decision)
        return read

    async def get_raw(self, path: str, *, timeout: float | None = None) -> httpx.Response:
        """GET something that isn't JSON, such as a file, with the usual retries and errors."""
        self._check_write("GET", path, None, None)
        self._check_read("GET", path, None, None, None, False)
        response = await self._send("GET", path, None, None, timeout)
        if not response.is_success:
            raise error_from_response(response, "GET", path)
        return response

    async def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
        timeout: float | None = None,
    ) -> httpx.Response:
        url = build_url(self._base_url, path)
        # httpx reads timeout=None as "never time out", so only a value given is passed on.
        extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        read = is_read(method, path)
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=self._headers,
                    auth=self._auth,
                    **extra,
                )
            except httpx.RequestError as exc:
                retryable = isinstance(exc, _NOT_SENT) or (read and isinstance(exc, _READ_FAILURES))
                if not retryable or attempt >= self._retry.max_retries:
                    raise error_from_transport(exc, method, path) from exc
                delay = self._retry.backoff(attempt)
            else:
                if attempt >= self._retry.max_retries:
                    return response
                if read and response.status_code in _READ_RETRY_STATUSES:
                    await anyio.sleep(self._retry.backoff(attempt))
                    attempt += 1
                    continue
                if response.status_code != 429:
                    return response
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                wait = self._retry.rate_limit_delay(attempt, retry_after)
                if wait is None:
                    raise error_from_response(response, method, path)
                delay = wait
            await anyio.sleep(delay)
            attempt += 1

    async def aclose(self) -> None:
        """Close the HTTP client, unless the caller supplied it and so still owns it."""
        if self._owns_http:
            await self._http.aclose()
