"""Which tables the live tests' cleanup drops. No network access."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pyquickbase import TableInfo

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
