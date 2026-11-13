"""Drop the tables and apps that live test runs left behind.

A run drops its own tables when it ends, but one that's killed, or loses its connection, can't. So
each live run calls this first, and you can run it yourself:

    set -a && . ./.env.live && set +a
    uv run scripts/clean_live_tables.py --dry-run     # list what would be dropped
    uv run scripts/clean_live_tables.py               # drop it

A table counts as left behind only if the live tests make tables with its name, `ZZ Live <hex>` or
one of the fixed names the examples need, and it's older than `--older-than` hours. That leaves
alone any table a run still in progress is using, as a full run takes about two hours. The apps are
`QB_APP_ID` and `QB_SECOND_APP_ID`, which must be sandbox apps, as CONTRIBUTING.md says.

An app counts as left behind only if its name is exactly one the app test makes, `ZZ Live App
<hex>` or its ` copy` or ` empty`, it's older than `--older-than` hours, and it isn't one of those
two apps. Live runs drop only tables before they start, so an app is deleted only when you run
this.
"""

import argparse
import os
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

PREFIX = "ZZ Live "
# Tables the docs and reference examples make by name, because the examples show those names.
FIXED_NAMES = frozenset(
    {"Tasks", "Projects", "Job Tasks", "Job Projects", "Job Sites", "Technicians"}
)
DEFAULT_HOURS = 6.0
# Exactly the names test_apps.py gives the apps it makes and copies.
APP_NAME = re.compile(r"ZZ Live App [0-9a-f]{8}(?: copy| empty)?")


def is_temporary(name: str) -> bool:
    return name.startswith(PREFIX) or name in FIXED_NAMES


def _created(raw: Any) -> datetime | None:
    text = raw.get("created") if hasattr(raw, "get") else None
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def left_behind(tables: Iterable[Any], *, older_than: timedelta, now: datetime) -> list[Any]:
    """The temporary tables made before `now - older_than`. A table with no creation time is
    kept, as its age can't be known."""
    found: list[Any] = []
    for info in tables:
        created = _created(info.raw)
        if is_temporary(info.name) and created is not None and now - created > older_than:
            found.append(info)
    return found


def clean(
    client: Any, app_ids: Iterable[str], *, older_than: timedelta, dry_run: bool
) -> list[str]:
    """Drop the tables left behind in these apps, and return a line for each."""
    now = datetime.now(UTC)
    report: list[str] = []
    for app_id in app_ids:
        app = client.app(app_id)
        for info in left_behind(app.tables(refresh=True), older_than=older_than, now=now):
            line = f"{app_id} {info.id} {info.name!r}, created {info.raw.get('created')}"
            if not dry_run:
                app.table(info.id).drop(confirm=info.name)
            report.append(line)
    return report


def left_behind_apps(
    client: Any, keep: Iterable[str], *, older_than: timedelta, now: datetime
) -> list[Any]:
    """The apps the app test made before `now - older_than`, never one in `keep`. An app with no
    creation time is kept, as its age can't be known."""
    kept = set(keep)
    found: list[Any] = []
    for listing in client.apps():
        if listing.id in kept or not APP_NAME.fullmatch(listing.name):
            continue
        info = client.app(listing.id).info()
        created = _created(info.raw)
        if APP_NAME.fullmatch(info.name) and created is not None and now - created > older_than:
            found.append(info)
    return found


def clean_apps(
    client: Any, keep: Iterable[str], *, older_than: timedelta, dry_run: bool
) -> list[str]:
    """Delete the apps left behind, and return a line for each."""
    report: list[str] = []
    found = left_behind_apps(client, keep, older_than=older_than, now=datetime.now(UTC))
    for info in found:
        line = f"app {info.id} {info.name!r}, created {info.raw.get('created')}"
        if not dry_run:
            client.app(info.id).drop(confirm=info.name)
        report.append(line)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Drop the tables that live test runs left behind.")
    parser.add_argument("--older-than", type=float, default=DEFAULT_HOURS, metavar="HOURS")
    parser.add_argument("--dry-run", action="store_true", help="List them, and drop nothing.")
    args = parser.parse_args(argv)

    from pyquickbase import Quickbase

    app_ids = [
        os.environ[name] for name in ("QB_APP_ID", "QB_SECOND_APP_ID") if os.environ.get(name)
    ]
    if not app_ids:
        sys.exit("Set QB_APP_ID, and QB_SECOND_APP_ID if there is one, to the test apps.")
    with Quickbase.from_env() as client:
        older_than = timedelta(hours=args.older_than)
        report = clean(client, app_ids, older_than=older_than, dry_run=args.dry_run)
        apps = clean_apps(client, app_ids, older_than=older_than, dry_run=args.dry_run)
    verb = "Would drop" if args.dry_run else "Dropped"
    for line in [*report, *apps]:
        print(f"{verb} {line}")
    if report or apps:
        print(f"{verb} {len(report)} tables and {len(apps)} apps.")
    else:
        print("Nothing was left behind.")


if __name__ == "__main__":
    main()
