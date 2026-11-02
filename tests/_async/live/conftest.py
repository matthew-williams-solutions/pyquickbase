"""Fixtures for the live tests of both clients. See CONTRIBUTING.md to run them."""

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

from pyquickbase import AsyncQuickbase

BACKENDS = ["asyncio", "trio"] if os.environ.get("QB_LIVE_TRIO") == "1" else ["asyncio"]


@pytest.fixture(params=BACKENDS)
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
async def qb(scratch: Any, anyio_backend: str) -> AsyncIterator[AsyncQuickbase]:
    async with AsyncQuickbase(realm=scratch.realm, token=scratch.token) as client:
        yield client
