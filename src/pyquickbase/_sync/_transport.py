# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Send one logical request: auth, headers, retries, and error mapping."""

import time
from collections.abc import Mapping
from typing import Any

import httpx

from .._config import build_url, check_allowed
from .._response import error_from_response, error_from_transport, parse_response
from .._retry import RetryPolicy, parse_retry_after


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
        allowed_writes: frozenset[tuple[str, str]] | None = None,
    ) -> None:
        self._allowed_writes = allowed_writes
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
    ) -> Any:
        method = method.upper()
        response = self._send(method, path, params, json)
        return parse_response(response, method, path)

    def get_raw(self, path: str) -> httpx.Response:
        """GET something that isn't JSON, such as a file, with the usual retries and errors."""
        response = self._send("GET", path, None, None)
        if not response.is_success:
            raise error_from_response(response, "GET", path)
        return response

    def _send(
        self, method: str, path: str, params: Mapping[str, Any] | None, json: Any
    ) -> httpx.Response:
        url = build_url(self._base_url, path)
        if self._allowed_writes is not None:
            check_allowed(method, path, self._allowed_writes)
        attempt = 0
        while True:
            try:
                response = self._http.request(
                    method, url, params=params, json=json, headers=self._headers, auth=self._auth
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Nothing reached the server, so this is safe to retry even for writes.
                if attempt >= self._retry.max_retries:
                    raise error_from_transport(exc, method, path) from exc
                delay = self._retry.backoff(attempt)
            except httpx.TransportError as exc:
                raise error_from_transport(exc, method, path) from exc
            else:
                if response.status_code != 429 or attempt >= self._retry.max_retries:
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
