"""Run the API reference's examples on a real app, with both clients, and check their output.

Each run gets new tables, because the printed record IDs depend on them. To record new output, set
`QB_UPDATE_REFERENCE_OUTPUT=1`, and check the change to `docs_src/` before committing it.
"""

import ast
import asyncio
import contextlib
import importlib
import inspect
import io
import os
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pyquickbase import (
    AsyncQuickbase,
    BadRequestError,
    FileUpload,
    InvalidValueError,
    NotFoundError,
    Quickbase,
    UnknownFieldError,
    UnknownTableError,
)

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
build_reference = importlib.import_module("build_reference")

EXAMPLES = sorted(build_reference.EXAMPLES.glob("*.py"))
TASK_ROWS = [
    {
        "Title": "Pour slab",
        "Status": "Open",
        "Hours": 8,
        "Due Date": date(2027, 1, 15),
        "Code": "T-1",
    },
    {
        "Title": "Frame walls",
        "Status": "Open",
        "Hours": 12,
        "Due Date": date(2027, 2, 1),
        "Code": "T-2",
    },
    {"Title": "Hang drywall", "Status": "Done", "Hours": 6, "Code": "T-3"},
]


@pytest.fixture
def tables(scratch: Any) -> Iterator[tuple[Any, Any]]:
    """Make the tables the examples show, and drop them even if an example fails."""
    app = scratch.client.app(scratch.app_id)
    taken = {table.name for table in app.tables(refresh=True)} & {"Job Tasks", "Job Projects"}
    if taken:
        pytest.skip(f"This needs an app without these tables: {sorted(taken)}")
    tasks = app.create_table("Job Tasks")
    scratch.created.append(tasks)
    projects = app.create_table("Job Projects")
    scratch.created.append(projects)
    try:
        tasks.create_field("Title", "text")
        tasks.create_field("Status", "text-multiple-choice", choices=["Open", "Done"])
        tasks.create_field("Hours", "numeric")
        tasks.create_field("Due Date", "date")
        tasks.create_field("Code", "text", unique=True)
        tasks.create_field("Attachment", "file")
        tasks.insert(TASK_ROWS)
        projects.create_field("Name", "text")
        yield tasks, projects
    finally:
        # An example may have dropped its table, and the session drops whatever fails here.
        remaining = {table.name for table in app.tables(refresh=True)}
        for name, table in (("Job Tasks", tasks), ("Job Projects", projects)):
            if name in remaining:
                table.drop(confirm=name)
            scratch.created.remove(table)


def prepare(name: str, tasks: Any, projects: Any) -> None:
    """Add what a few examples need, which the page's setup doesn't show."""
    if name == "relationships":
        tasks.ensure_relationship(projects, foreign_key_label="Project")
    if name in ("download_file", "delete_file_version"):
        tasks.update([{"Record ID#": 1, "Attachment": FileUpload("spec.txt", b"one")}])
    if name == "delete_file_version":
        tasks.update([{"Record ID#": 1, "Attachment": FileUpload("spec.txt", b"two")}])


def check_output(path: Path, printed: str) -> None:
    expected = path.with_suffix(".out")
    if os.environ.get("QB_UPDATE_REFERENCE_OUTPUT") == "1":
        if printed:
            expected.write_text(printed, encoding="utf-8")
        else:
            expected.unlink(missing_ok=True)
    assert printed == (expected.read_text(encoding="utf-8") if expected.exists() else "")


def table_ids(tables: tuple[Any, Any]) -> tuple[str, str]:
    return tables[0].info().id, tables[1].info().id


def run_sync(path: Path, scratch: Any, table_ids: tuple[str, str]) -> str:
    code = build_reference.to_sync(path.read_text(encoding="utf-8"))
    printed = io.StringIO()
    with Quickbase(realm=scratch.realm, token=scratch.token) as qb:
        app = qb.app(scratch.app_id)
        names = {"tasks": app.table(table_ids[0]), "projects": app.table(table_ids[1])}
        with contextlib.redirect_stdout(printed):
            exec(compile(code, str(path), "exec"), names)
    return printed.getvalue()


async def run_async(path: Path, scratch: Any, table_ids: tuple[str, str]) -> str:
    code = compile(
        path.read_text(encoding="utf-8"),
        str(path),
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )
    printed = io.StringIO()
    async with AsyncQuickbase(realm=scratch.realm, token=scratch.token) as qb:
        app = qb.app(scratch.app_id)
        names = {"tasks": app.table(table_ids[0]), "projects": app.table(table_ids[1])}
        with contextlib.redirect_stdout(printed):
            result = eval(code, names)
            if inspect.isawaitable(result):
                await result
    return printed.getvalue()


@pytest.mark.parametrize("path", EXAMPLES, ids=[path.stem for path in EXAMPLES])
def test_the_sync_example_prints_what_the_page_shows(
    path: Path, scratch: Any, tables: tuple[Any, Any]
) -> None:
    prepare(path.stem, *tables)
    check_output(path, run_sync(path, scratch, table_ids(tables)))


@pytest.mark.parametrize("path", EXAMPLES, ids=[path.stem for path in EXAMPLES])
def test_the_async_example_prints_what_the_page_shows(
    path: Path, scratch: Any, tables: tuple[Any, Any]
) -> None:
    prepare(path.stem, *tables)
    check_output(path, asyncio.run(run_async(path, scratch, table_ids(tables))))


def test_getting_a_table_waits_until_first_use_to_check_it(scratch: Any) -> None:
    table = scratch.client.app(scratch.app_id).table("No Such Table")
    with pytest.raises(UnknownTableError):
        table.info()


def test_what_the_docstrings_say_files_and_formulas_raise(tables: tuple[Any, Any]) -> None:
    tasks = tables[0]
    tasks.update([{"Record ID#": 1, "Attachment": FileUpload("spec.txt", b"one")}])
    with pytest.raises(BadRequestError):
        tasks.run_formula("[Hours] * 2")
    with pytest.raises(NotFoundError):
        tasks.run_formula("[Hours] * 2", record_id=999)
    with pytest.raises(NotFoundError):
        tasks.delete_file_version(999, "Attachment", version=1)
    with pytest.raises(NotFoundError):
        tasks.delete_file_version(1, "Attachment", version=99)
    with pytest.raises(NotFoundError):
        tasks.download_file(1, "Attachment", version=99)
    with pytest.raises(InvalidValueError):
        tasks.download_file(1, "Title")
    with pytest.raises(UnknownFieldError):
        tasks.download_file(1, "No Such Field")


def test_upsert_keeps_fields_a_row_leaves_out(tables: tuple[Any, Any]) -> None:
    tasks = tables[0]
    tasks.upsert([{"Code": "T-1", "Status": "Done"}], merge_on="Code")
    task = tasks.get(1, select=["Title", "Status", "Hours"])
    assert (task["Title"], task["Status"], task["Hours"]) == ("Pour slab", "Done", 8)
