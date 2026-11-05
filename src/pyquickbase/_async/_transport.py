"""Send one logical request: auth, headers, retries, and error mapping."""

from collections.abc import Mapping
from typing import Any

import anyio
import httpx

from .._combine import read_rules_in
from .._config import build_url, check_read_only, is_read
from .._policy import WritePolicy, check_policy, describe_write
from .._read_policy import ReadPolicy, ReadRequest, check_read, describe_read
from .._response import error_from_response, error_from_transport, parse_response
from .._retry import RetryPolicy, parse_retry_after


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
        read_only: bool = False,
        write_policy: WritePolicy | None = None,
        read_policy: ReadPolicy | None = None,
    ) -> None:
        self._read_policy = read_policy
        self._read_rules = read_rules_in(read_policy)
        self._read_only = read_only
        self._write_policy = write_policy
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
        report_type: str | None = None,
        summarizing: bool = False,
    ) -> Any:
        """Send a request, and return its decoded body, trimmed by any `ReadRules`.

        `report_type` and `summarizing` describe a read the request can't, as `describe_read()`
        explains, so `request()` on the client can't pass them.
        """
        method = method.upper()
        self._check_write(method, path, json, key_field_id)
        read = self._check_read(method, path, params, json, report_type, summarizing)
        response = await self._send(method, path, params, json)
        payload = parse_response(response, method, path)
        for rules in self._read_rules:
            payload = rules._trim_response(read, method, path, payload)  # pyright: ignore[reportPrivateUsage]
        return payload

    def summary_limit(self, table_id: str) -> int | None:
        """The most records the read rules let `summarize()` read from a table, if any."""
        limits = [
            found
            for rules in self._read_rules
            if (found := rules._summary_limit(table_id)) is not None  # pyright: ignore[reportPrivateUsage]
        ]
        return min(limits, default=None)

    def _check_write(self, method: str, path: str, json: Any, key_field_id: int | None) -> None:
        """Apply the limits on writes, which come first, so a refused write says so."""
        if self._read_only:
            check_read_only(method, path)
        if self._write_policy is not None and not is_read(method, path):
            request = describe_write(method, path, json, key_field_id=key_field_id)
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

    async def get_raw(self, path: str) -> httpx.Response:
        """GET something that isn't JSON, such as a file, with the usual retries and errors."""
        self._check_write("GET", path, None, None)
        self._check_read("GET", path, None, None, None, False)
        response = await self._send("GET", path, None, None)
        if not response.is_success:
            raise error_from_response(response, "GET", path)
        return response

    async def _send(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
    ) -> httpx.Response:
        url = build_url(self._base_url, path)
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method, url, params=params, json=json, headers=self._headers, auth=self._auth
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Nothing reached the server, so this is safe to retry even for writes.
                if attempt >= self._retry.max_retries:
                    raise error_from_transport(exc, method, path) from exc
                delay = self._retry.backoff(attempt)
            except httpx.RequestError as exc:
                raise error_from_transport(exc, method, path) from exc
            else:
                if response.status_code != 429 or attempt >= self._retry.max_retries:
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
