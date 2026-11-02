# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Fixtures for the live tests of both clients. See CONTRIBUTING.md to run them."""

import os
from collections.abc import Iterator
from typing import Any

import pytest

from pyquickbase import Quickbase

BACKENDS = ["asyncio", "trio"] if os.environ.get("QB_LIVE_TRIO") == "1" else ["asyncio"]


@pytest.fixture(params=BACKENDS)
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def qb(scratch: Any, anyio_backend: str) -> Iterator[Quickbase]:
    with Quickbase(realm=scratch.realm, token=scratch.token) as client:
        yield client
