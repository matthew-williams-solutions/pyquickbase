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
import types
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pyquickbase import (
    AsyncQuickbase,
    AuthenticationError,
    BadRequestError,
    FileUpload,
    InvalidValueError,
    NotFoundError,
    PermissionDeniedError,
    Quickbase,
    ReadOnlyError,
    UnknownFieldError,
    UnknownTableError,
)
from pyquickbase._codegen import render

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
build_reference = importlib.import_module("build_reference")

EXAMPLES = [
    (page.slug, path)
    for page in build_reference.ALL_PAGES
    for path in sorted((build_reference.EXAMPLES / page.slug).glob("*.py"))
    if f"{page.slug}/{path.stem}" not in build_reference.NOT_RUN_LIVE
]
EXAMPLE_IDS = [f"{slug}.{path.stem}" for slug, path in EXAMPLES]
PLACEHOLDER_APP = "bq1234567"
PLACEHOLDER_REALM = "acme.quickbase.com"
PLACEHOLDER_TABLE = "bqtasks123"
# Tables that examples create, which the fixture removes.
MADE_BY_EXAMPLES = {"Job Sites"}
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
    taken = {table.name for table in app.tables(refresh=True)} & {
        "Job Tasks",
        "Job Projects",
        *MADE_BY_EXAMPLES,
    }
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
        for item in app.tables(refresh=True):
            if item.name in MADE_BY_EXAMPLES:
                app.table(item.id).drop(confirm=item.name)


def prepare(slug: str, name: str, tasks: Any, projects: Any) -> None:
    """Add what a few examples need, which the page's setup doesn't show."""
    if slug != "table":
        return
    if name == "relationships":
        tasks.ensure_relationship(projects, foreign_key_label="Project")
    if name in ("download_file", "delete_file_version"):
        tasks.update([{"Record ID#": 1, "Attachment": FileUpload("spec.txt", b"one")}])
    if name == "delete_file_version":
        tasks.update([{"Record ID#": 1, "Attachment": FileUpload("spec.txt", b"two")}])


def check_output(path: Path, printed: str, scratch: Any, tables: tuple[Any, Any]) -> None:
    """Compare what an example printed with the file beside it, hiding real IDs and the realm."""
    printed = (
        printed.replace(scratch.app_id, PLACEHOLDER_APP)
        .replace(scratch.realm, PLACEHOLDER_REALM)
        .replace(tables[0].info().id, PLACEHOLDER_TABLE)
    )
    expected = path.with_suffix(".out")
    if os.environ.get("QB_UPDATE_REFERENCE_OUTPUT") == "1":
        if printed:
            expected.write_text(printed, encoding="utf-8")
        else:
            expected.unlink(missing_ok=True)
    assert printed == (expected.read_text(encoding="utf-8") if expected.exists() else "")


def add_generated_module(tasks: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stand in for the module that `pyquickbase codegen` writes, which examples use."""
    module = types.ModuleType("myapp.tables")
    exec(render([(tasks.info(), tasks.fields())]), module.__dict__)
    monkeypatch.setitem(sys.modules, "myapp", types.ModuleType("myapp"))
    monkeypatch.setitem(sys.modules, "myapp.tables", module)
    return module.JobTasks


def namespace(
    qb: Any, scratch: Any, slug: str, tables: tuple[Any, Any], model: Any
) -> dict[str, Any]:
    app = qb.app(scratch.app_id)
    table_id = tables[0].info().id
    return {
        "qb": qb,
        "app": app,
        "JobTasks": model,
        "tasks": app.typed(model) if slug == "typed-table" else app.table(table_id),
        "projects": app.table(tables[1].info().id),
    }


def with_real_ids(code: str, scratch: Any, tables: tuple[Any, Any]) -> str:
    return code.replace(PLACEHOLDER_APP, scratch.app_id).replace(
        PLACEHOLDER_TABLE, tables[0].info().id
    )


def run_sync(path: Path, slug: str, scratch: Any, tables: tuple[Any, Any], model: Any) -> str:
    code = with_real_ids(build_reference.to_sync(path.read_text(encoding="utf-8")), scratch, tables)
    printed = io.StringIO()
    with Quickbase(realm=scratch.realm, token=scratch.token) as qb:
        names = namespace(qb, scratch, slug, tables, model)
        with contextlib.redirect_stdout(printed):
            exec(compile(code, str(path), "exec"), names)
    return printed.getvalue()


async def run_async(
    path: Path, slug: str, scratch: Any, tables: tuple[Any, Any], model: Any
) -> str:
    code = compile(
        with_real_ids(path.read_text(encoding="utf-8"), scratch, tables),
        str(path),
        "exec",
        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
    )
    printed = io.StringIO()
    async with AsyncQuickbase(realm=scratch.realm, token=scratch.token) as qb:
        names = namespace(qb, scratch, slug, tables, model)
        with contextlib.redirect_stdout(printed):
            result = eval(code, names)
            if inspect.isawaitable(result):
                await result
    return printed.getvalue()


@pytest.fixture
def client_env(scratch: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `from_env()` at the same app as the rest of the run."""
    monkeypatch.setenv("QB_REALM", scratch.realm)
    monkeypatch.setenv("QB_USER_TOKEN", scratch.token)


@pytest.mark.parametrize(("slug", "path"), EXAMPLES, ids=EXAMPLE_IDS)
@pytest.mark.usefixtures("client_env")
def test_the_sync_example_prints_what_the_page_shows(
    slug: str, path: Path, scratch: Any, tables: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(slug, path.stem, *tables)
    model = add_generated_module(tables[0], monkeypatch)
    check_output(path, run_sync(path, slug, scratch, tables, model), scratch, tables)


@pytest.mark.parametrize(("slug", "path"), EXAMPLES, ids=EXAMPLE_IDS)
@pytest.mark.usefixtures("client_env")
def test_the_async_example_prints_what_the_page_shows(
    slug: str, path: Path, scratch: Any, tables: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(slug, path.stem, *tables)
    model = add_generated_module(tables[0], monkeypatch)
    check_output(path, asyncio.run(run_async(path, slug, scratch, tables, model)), scratch, tables)


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


def test_quickbase_answers_an_unknown_app_id_with_a_401(scratch: Any) -> None:
    with Quickbase(realm=scratch.realm, token=scratch.token) as qb:
        app = qb.app("bzzzzzzzz")  # Getting it makes no request, so this doesn't raise.
        with pytest.raises(AuthenticationError):
            app.info()
        with pytest.raises(AuthenticationError):
            app.tables()


def test_a_401_says_that_it_may_be_the_id_rather_than_the_token(scratch: Any) -> None:
    """Quickbase words these the same, so the SDK can't say which, and its message says so."""
    with Quickbase(realm=scratch.realm, token=scratch.token) as qb:
        with pytest.raises(AuthenticationError) as unknown_app:
            qb.app("bzzzzzzzz").info()
    with Quickbase(realm=scratch.realm, token="b3xyz_not_a_real_token") as qb:
        with pytest.raises(AuthenticationError) as bad_token:
            qb.app(scratch.app_id).info()
    assert unknown_app.value.message == bad_token.value.message
    assert unknown_app.value.description == bad_token.value.description
    assert "doesn't exist" in str(unknown_app.value)
    assert "bad token" in str(bad_token.value)


def test_a_token_without_access_to_an_app_gets_a_401_as_well(scratch: Any) -> None:
    restricted = os.environ.get("QB_RESTRICTED_TOKEN")
    if not (restricted and scratch.second_app_id):
        pytest.skip("Set QB_RESTRICTED_TOKEN and QB_SECOND_APP_ID to run this.")
    with Quickbase(realm=scratch.realm, token=restricted) as qb:
        with pytest.raises(AuthenticationError):
            qb.app(scratch.second_app_id).info()


def test_only_an_administrator_can_list_roles(scratch: Any) -> None:
    restricted = os.environ.get("QB_RESTRICTED_TOKEN")
    if not restricted:
        pytest.skip("Set QB_RESTRICTED_TOKEN to run this.")
    with Quickbase(realm=scratch.realm, token=restricted) as qb:
        with pytest.raises(PermissionDeniedError):
            qb.app(scratch.app_id).roles()


def test_request_keeps_to_the_clients_limits(scratch: Any) -> None:
    with Quickbase(realm=scratch.realm, token=scratch.token, read_only=True) as qb:
        with pytest.raises(ReadOnlyError):
            qb.request("POST", "/tables", params={"appId": scratch.app_id}, json={"name": "No"})


def test_a_second_table_with_the_same_name_is_refused(
    scratch: Any, tables: tuple[Any, Any]
) -> None:
    with pytest.raises(BadRequestError):
        scratch.client.app(scratch.app_id).create_table("Job Tasks")
