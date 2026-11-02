"""Authentication. Keeping the token here means no other object ever holds it."""

from collections.abc import Generator

import httpx


class UserTokenAuth(httpx.Auth):
    """Add a Quickbase user token to each request."""

    def __init__(self, token: str) -> None:
        self._header = f"QB-USER-TOKEN {token}"

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = self._header
        yield request

    def __repr__(self) -> str:
        return "UserTokenAuth(token=***)"
