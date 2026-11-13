"""Which tables the live tests' cleanup drops. No network access."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pyquickbase import AppInfo, TableInfo

PATH = Path(__file__).resolve().parent.parent / "scripts" / "clean_live_tables.py"
SPEC = importlib.util.spec_from_file_location("clean_live_tables", PATH)
assert SPEC is not None
assert SPEC.loader is not None
cleaner: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleaner)

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def table(name: str, hours_old: float | None) -> TableInfo:
    raw = {} if hours_old is None else {"created": (NOW - timedelta(hours=hours_old)).isoformat()}
    return TableInfo(id=name, name=name, description="", key_field_id=3, raw=raw)


def test_only_old_temporary_tables_are_left_behind() -> None:
    tables = [
        table("ZZ Live 1a2b3c4d", 30),
        table("ZZ Live 5e6f7a8b", 1),  # A run still going.
        table("Job Tasks", 30),
        table("Sample Tasks", 30),
        table("Sample Records", 300),
        table("ZZ Live 9c0d1e2f", None),  # Its age can't be known.
        table("zz live lowercase", 30),
    ]
    found = cleaner.left_behind(tables, older_than=timedelta(hours=6), now=NOW)
    assert [item.name for item in found] == ["ZZ Live 1a2b3c4d", "Job Tasks"]


class FakeApps:
    """A client whose apps() and app().info() answer from a list, and record what's dropped."""

    def __init__(self, apps: list[tuple[str, str, float | None]]) -> None:
        self.found = {app_id: (name, hours) for app_id, name, hours in apps}
        self.dropped: list[tuple[str, str]] = []

    def apps(self) -> list[Any]:
        return [SimpleNamespace(id=app_id, name=name) for app_id, (name, _) in self.found.items()]

    def app(self, app_id: str) -> Any:
        name, hours = self.found[app_id]
        raw = {} if hours is None else {"created": (NOW - timedelta(hours=hours)).isoformat()}
        info = AppInfo(
            id=app_id, name=name, description="", time_zone=None, date_format=None, raw=raw
        )

        def drop(*, confirm: str) -> None:
            self.dropped.append((app_id, confirm))

        return SimpleNamespace(info=lambda: info, drop=drop)


def test_only_old_apps_the_app_test_made_are_left_behind() -> None:
    client = FakeApps(
        [
            ("bq1", "ZZ Live App 1a2b3c4d", 30),
            ("bq2", "ZZ Live App 1a2b3c4d copy", 30),
            ("bq3", "ZZ Live App 1a2b3c4d empty", 30),
            ("bq4", "ZZ Live App 5e6f7a8b", 1),  # A run still going.
            ("bq5", "ZZ Live App 9c0d1e2f", None),  # Its age can't be known.
            ("bq6", "ZZ Live App notahex1", 30),
            ("bq7", "ZZ Live App 1a2b3c4d copy 2", 30),
            ("bq8", "Customer Portal", 300),
            ("bqtest", "ZZ Live App 00000000", 30),  # A test app, which is never touched.
        ]
    )
    found = cleaner.left_behind_apps(client, ["bqtest"], older_than=timedelta(hours=6), now=NOW)
    assert [item.id for item in found] == ["bq1", "bq2", "bq3"]


def test_a_dry_run_deletes_no_app() -> None:
    client = FakeApps([("bq1", "ZZ Live App 1a2b3c4d", 30)])
    report = cleaner.clean_apps(client, [], older_than=timedelta(hours=6), dry_run=True)
    assert len(report) == 1
    assert client.dropped == []
    cleaner.clean_apps(client, [], older_than=timedelta(hours=6), dry_run=False)
    assert client.dropped == [("bq1", "ZZ Live App 1a2b3c4d")]
