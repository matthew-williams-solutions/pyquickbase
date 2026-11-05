"""Run the docs' Python examples against a sandbox app, so a wrong example can't reach a release."""

import ast
import asyncio
import inspect
import secrets
import sys
import textwrap
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import pyquickbase
from pyquickbase import FileUpload, Quickbase, TypedRecord
from pyquickbase._codegen import render
from tests.test_docs import examples

# Each key is text from an example, so the entry survives edits elsewhere on the page.
SKIPPED = {
    'tasks.delete(f"{{6.EX': "A counterexample, whose first line raises on purpose",
    "httpx.post(url, headers=headers, json=body)": "Shows raw httpx, with placeholder values",
    'app.ensure_table("Projects")': "Makes lasting tables. test_sdk.py tests these methods",
    "movements.insert(": "Needs a table the page doesn't set up, with a role that can only add",
    'token_env="QB_SERVICE_TOKEN"': "Needs a second token in the environment",
    'report("Hours by Project")': "Needs a report built in the UI. test_sdk.py tests this method",
}

# These change a table's fields, or need a field that's unique, so they get their own table.
NEEDS_OWN_TABLE = (
    "create_field",
    "create_relationship",
    "create_table",
    "delete_fields",
    ".drop(",
    "ensure_",
    "merge_on",
    "update_field",
)

STATUSES = ["Not Started", "Open", "Blocked", "Done", "Cancelled"]
ROWS = [
    {"Title": "Order chairs", "Status": "Open"},
    {"Title": "Book venue", "Status": "Finished"},  # Not a valid status, as in the docs
    {"Title": "Send invites", "Status": "Open"},
]


def add_fields(table: Any, *, unique_code: bool = False) -> None:
    # Field IDs matter: reading.md uses 7 for Status and 9 for its date.
    table.create_field("Title", "text")
    table.create_field("Status", "text-multiple-choice", choices=STATUSES)
    table.create_field("Notes", "text")
    table.create_field("Due Date", "date")
    table.create_field("Due", "date")
    table.create_field("Amount", "numeric")
    table.create_field("Code", "text", unique=unique_code)
    table.create_field("Project", "text")
    table.create_field("Attachment", "file")
    table.create_field("Old Field", "text")
    table.create_field("Older Field", "text")


@pytest.fixture(scope="module")
def shared_tasks(scratch: Any) -> Any:
    """A table named Tasks, as the examples expect. Records 7 and 8 hold two file versions."""
    app = scratch.client.app(scratch.app_id)
    taken = {table.name for table in app.tables(refresh=True)} & {"Tasks", "Projects"}
    if taken:
        pytest.skip(f"This needs an app without these tables: {sorted(taken)}")
    table = app.create_table("Tasks")
    scratch.created.append(table)
    add_fields(table)
    table.insert(
        [
            {"Title": f"Task {number}", "Status": "Open", "Amount": 5, "Project": "Roof"}
            for number in range(1, 11)
        ]
    )
    for record_id in (7, 8):
        for text in (b"one", b"two"):
            upload = FileUpload("spec.txt", text)
            table.update([{"Record ID#": record_id, "Attachment": upload}])
    return table


@pytest.fixture
def world(scratch: Any, shared_tasks: Any, tmp_path: Path, monkeypatch: Any) -> Iterator[Any]:
    """Make the sandbox settings available as the docs' placeholders expect, and clean up after."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "spec.pdf").write_bytes(b"spec")
    (tmp_path / "downloads").mkdir()
    with Quickbase(realm=scratch.realm, token=scratch.token) as qb:
        app = qb.app(scratch.app_id)
        keep = {table.id for table in app.tables(refresh=True)}
        yield types.SimpleNamespace(qb=qb, app=app, scratch=scratch, shared=shared_tasks)
        for table in app.tables(refresh=True):
            if table.id not in keep:
                app.table(table.id).drop(confirm=table.name)


def generated_module(table: Any, fields: Any) -> types.ModuleType:
    """Stand in for the `myapp.tables` module the codegen guide has you generate."""
    module = types.ModuleType("myapp.tables")
    exec(render([(table, fields)]), module.__dict__)
    module.Tasks = next(
        item
        for item in vars(module).values()
        if inspect.isclass(item) and issubclass(item, TypedRecord) and item is not TypedRecord
    )
    return module


def prepare(code: str, world: Any, table_name: str, table_id: str) -> str:
    if 'table("Projects")' in code:
        parent = world.app.create_table(f"ZZ Live {secrets.token_hex(4)}")
        parent.create_field("Name", "text")
        code = code.replace('table("Projects")', f"table({parent.info().id!r})")
    code = code.replace('realm="acme"', f"realm={world.scratch.realm!r}")
    code = code.replace('token="your_user_token"', f"token={world.scratch.token!r}")
    code = code.replace("bq1234567", world.scratch.app_id)
    code = code.replace("bq7654321", table_id)
    code = code.replace('tasks.report("Open tasks")', 'tasks.report("List All")')
    code = code.replace('tasks.report("12")', 'tasks.report("1")')
    code = code.replace("tasks.delete_file_version(7,", "tasks.delete_file_version(8,")
    code = code.replace('.table("Tasks")', f".table({table_name!r})")
    return code.replace('confirm="Tasks"', f"confirm={table_name!r}")


def settings_for(code: str, world: Any, tasks: Any, monkeypatch: Any) -> dict[str, Any]:
    namespace: dict[str, Any] = {name: getattr(pyquickbase, name) for name in pyquickbase.__all__}
    module = generated_module(tasks.info(), tasks.fields())
    monkeypatch.setitem(sys.modules, "myapp", types.ModuleType("myapp"))
    monkeypatch.setitem(sys.modules, "myapp.tables", module)
    namespace.update(
        __name__="example",
        date=__import__("datetime").date,
        httpx=httpx,
        qb=world.qb,
        app=world.app,
        tasks=tasks,
        rows=ROWS,
        status="Open",
        Tasks=module.Tasks,
    )
    if "Tasks." in code and ".typed(" not in code:
        namespace["tasks"] = world.app.typed(module.Tasks)  # As set up earlier on that page
    if "projects" in code and "projects =" not in code:
        namespace["projects"] = world.scratch.client.app(world.scratch.app_id).create_table(
            f"ZZ Live {secrets.token_hex(4)}"
        )
        namespace["projects"].create_field("Name", "text")
    if "file." in code and "file =" not in code:
        namespace["file"] = tasks.download_file(7, "Attachment")
    return namespace


@pytest.mark.parametrize(("where", "code"), examples(), ids=[where for where, _ in examples()])
def test_example_runs(where: str, code: str, world: Any, monkeypatch: Any) -> None:
    source = textwrap.dedent(code)
    for text, reason in SKIPPED.items():
        if text in source:
            pytest.skip(reason)
    tasks = world.app.table("Tasks")
    if any(text in source for text in NEEDS_OWN_TABLE):
        tasks = world.app.create_table(f"ZZ Live {secrets.token_hex(4)}")
        add_fields(tasks, unique_code="merge_on" in source)
        tasks.insert([{"Title": f"Task {number}"} for number in range(1, 11)])
    source = prepare(source, world, tasks.info().name, tasks.info().id)
    namespace = settings_for(source, world, tasks, monkeypatch)
    before = {key: namespace[key] for key in ("qb", "app", "tasks")}
    compiled = compile(source, where, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    result = eval(compiled, namespace)
    if inspect.iscoroutine(result):
        asyncio.run(result)
    if "open_tasks" in namespace:
        assert namespace["open_tasks"]("Roof")
    made = namespace.get("qb")
    if made is not before["qb"] and isinstance(made, Quickbase):
        made.close()
    if "http_client" in namespace:
        namespace["http_client"].close()


def test_every_skip_still_matches_an_example() -> None:
    sources = [textwrap.dedent(code) for _, code in examples()]
    assert all(any(text in source for source in sources) for text in SKIPPED)
