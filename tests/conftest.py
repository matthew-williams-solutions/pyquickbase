import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

# Tests that use a real Quickbase app. They never run unless asked for, so CI needs no credentials.
LIVE = os.environ.get("QB_LIVE") == "1"
collect_ignore_glob = [] if LIVE else ["live/*", "_async/live/*", "_sync/live/*"]


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return request.param


@dataclass
class Scratch:
    """Makes temporary tables in a sandbox app, and drops exactly those afterward."""

    realm: str
    token: str = field(repr=False)
    app_id: str
    client: Any
    second_app_id: str | None = None
    created: list[Any] = field(default_factory=list)

    def table(self, *fields: tuple[Any, ...], second_app: bool = False) -> Any:
        """Make a table, in the second app if asked. Without QB_SECOND_APP_ID, that test skips."""
        if second_app and not self.second_app_id:
            pytest.skip("Set QB_SECOND_APP_ID to run this.")
        app = self.client.app(self.second_app_id if second_app else self.app_id)
        table = app.create_table(f"ZZ Live {secrets.token_hex(4)}")
        self.created.append(table)
        for label, field_type, *rest in fields:
            table.create_field(label, field_type, **(rest[0] if rest else {}))
        return table

    def fill(self, table: Any, rows: list[dict[Any, Any]]) -> None:
        """Replace a table's records."""
        if table.count():
            table.delete("{3.GT.'0'}")
        if rows:
            table.insert(rows)


@pytest.fixture(scope="session")
def scratch() -> Iterator[Scratch]:
    from pyquickbase import Quickbase

    realm, token, app_id = (os.environ[n] for n in ("QB_REALM", "QB_USER_TOKEN", "QB_APP_ID"))
    second_app_id = os.environ.get("QB_SECOND_APP_ID")
    with Quickbase(realm=realm, token=token) as client:
        apps = [client.app(item) for item in (app_id, second_app_id) if item]

        def record_counts() -> dict[str, int]:
            return {
                item.id: app.table(item.id).count()
                for app in apps
                for item in app.tables(refresh=True)
            }

        before = record_counts()
        helper = Scratch(realm, token, app_id, client, second_app_id=second_app_id)
        try:
            yield helper
        finally:
            for table in helper.created:
                info = table.info()
                table.drop(confirm=info.name)
        after = record_counts()
    assert after == before, "A table this run didn't create has changed."
