# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Send one logical request: auth, headers, retries, and error mapping."""

import secrets
import time
from collections.abc import Mapping
from typing import Any

import httpx

from .._combine import read_rules_in, write_rules_in
from .._config import build_url, check_read_only, is_read
from .._policy import WritePolicy, WriteRules, check_policy, describe_write
from .._read_policy import ReadPolicy, ReadRequest, ReadRules, check_read, describe_read
from .._response import error_from_response, error_from_transport, parse_response
from .._retry import RetryPolicy, parse_retry_after
from ..errors import ReadRefusedError, WriteRefusedError

# Failures where nothing reached Quickbase, so any request is safe to send again.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# Failures after a request may have reached Quickbase, which only a read is safe to send again.
_READ_FAILURES = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
# A gateway or overloaded server, which only a read is safe to send again.
_READ_RETRY_STATUSES = frozenset({502, 503, 504})


class Transport:
    def __init__(
        self,
        *,
        realm: str,
        auth: httpx.Auth,
        base_url: str,
        retry: RetryPolicy,
        timeout: float,
        user_agent: str,
        http_client: httpx.Client | None,
        read_only: bool = False,
        write_policy: WritePolicy | None = None,
        read_policy: ReadPolicy | None = None,
        confirmation_key: bytes | None = None,
    ) -> None:
        self.realm = realm
        # Signs the confirmations previews return. Without a shared key, each client makes its own,
        # so a confirmation only works with operations from the client that issued it.
        self.confirmation_key = (
            secrets.token_bytes(32) if confirmation_key is None else confirmation_key
        )
        self._read_policy = read_policy
        self._read_rules = read_rules_in(read_policy)
        self._read_only = read_only
        self._write_policy = write_policy
        self._auth = auth
        self._base_url = base_url
        self._retry = retry
        self._headers = {"QB-Realm-Hostname": realm, "User-Agent": user_agent}
        self._owns_http = http_client is None
        self._http = httpx.Client(timeout=timeout) if http_client is None else http_client

    def request(
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
    ) -> Any:
        """Send a request, and return its decoded body, trimmed by any `ReadRules`.

        `report_type` and `summarizing` describe a read the request can't, as `describe_read()`
        explains, and `parent_table_id` a relationship's parent. `sources_checked` says that a
        formula, lookup, or summary field only reads what the client may read. `request()` on the
        client can't pass any of these.
        """
        method = method.upper()
        self._check_write(
            method,
            path,
            params,
            json,
            key_field_id=key_field_id,
            parent_table_id=parent_table_id,
            sources_checked=sources_checked,
        )
        read = self._check_read(method, path, params, json, report_type, summarizing)
        response = self._send(method, path, params, json, timeout)
        payload = parse_response(response, method, path)
        for rules in self._read_rules:
            payload = rules._trim_response(read, method, path, payload)  # pyright: ignore[reportPrivateUsage]
        return payload

    @property
    def read_rules(self) -> tuple[ReadRules, ...]:
        """Every `ReadRules` in the read policy, for describing what a client may read."""
        return self._read_rules

    @property
    def write_rules(self) -> tuple[WriteRules, ...]:
        """Every `WriteRules` in the write policy, for describing what a client may write."""
        return write_rules_in(self._write_policy)

    @property
    def read_only(self) -> bool:
        return self._read_only

    def summary_limit(self, table_id: str) -> int | None:
        """The most records the read rules let `summarize()` read from a table, if any."""
        limits = [
            found
            for rules in self._read_rules
            if (found := rules._summary_limit(table_id)) is not None  # pyright: ignore[reportPrivateUsage]
        ]
        return min(limits, default=None)

    def record_limit(self, table_id: str) -> int | None:
        """The most records the read rules let one query reach in a table, if any."""
        limits = [
            found
            for rules in self._read_rules
            if (found := rules._record_limit(table_id)) is not None  # pyright: ignore[reportPrivateUsage]
        ]
        return min(limits, default=None)

    @property
    def limited(self) -> bool:
        """Whether the client has any limits on what it reads or writes."""
        return self._read_only or self._read_policy is not None or self._write_policy is not None

    def table_created(self, table_id: str) -> None:
        """Give a table this client just created the access its rules give new tables."""
        for rules in (*self._read_rules, *self.write_rules):
            rules._adopt(table_id)  # pyright: ignore[reportPrivateUsage]

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
    ) -> None:
        """Apply the limits on writes, which come first, so a refused write says so."""
        if self._read_only:
            check_read_only(method, path)
        if is_read(method, path) or (self._write_policy is None and self._read_policy is None):
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
            refused = ReadRefusedError if self._read_policy is not None else WriteRefusedError
            raise refused(
                f"This client has limits, so it only makes {' and '.join(sorted(request.derived))} "
                "fields through methods such as create_field() and ensure_lookup_field(), which "
                f"check what they read. {method} {path} wasn't sent."
            )
        if self._write_policy is not None:
            check_policy(self._write_policy, request)

    def _check_read(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
        report_type: str | None,
        summarizing: bool,
    ) -> ReadRequest | None:
        if self._read_policy is None:
            return None
        read = describe_read(
            method, path, params, json, report_type=report_type, summarizing=summarizing
        )
        if read is not None:
            check_read(self._read_policy, read)
        return read

    def get_raw(self, path: str, *, timeout: float | None = None) -> httpx.Response:
        """GET something that isn't JSON, such as a file, with the usual retries and errors."""
        self._check_write("GET", path, None, None)
        self._check_read("GET", path, None, None, None, False)
        response = self._send("GET", path, None, None, timeout)
        if not response.is_success:
            raise error_from_response(response, "GET", path)
        return response

    def _send(
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
                response = self._http.request(
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
                    time.sleep(self._retry.backoff(attempt))
                    attempt += 1
                    continue
                if response.status_code != 429:
                    return response
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                wait = self._retry.rate_limit_delay(attempt, retry_after)
                if wait is None:
                    raise error_from_response(response, method, path)
                delay = wait
            time.sleep(delay)
            attempt += 1

    def close(self) -> None:
        """Close the HTTP client, unless the caller supplied it and so still owns it."""
        if self._owns_http:
            self._http.close()
