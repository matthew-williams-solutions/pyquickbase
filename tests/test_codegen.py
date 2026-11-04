"""Tests for generating classes from a schema, and for the command, which use no real network."""

import ast
import json
import keyword
import subprocess
import sys
import types
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from pyquickbase import UNSET, Field, FileUpload, Record, SchemaMismatchError, TableInfo
from pyquickbase._cli import main
from pyquickbase._codegen import _RESERVED_NAMES, HEADER, render
from pyquickbase._models import field_from_api, table_info_from_api
from pyquickbase._typed import is_writable, order_refs, raise_for_problems, schema_problems


def field(id: int, label: str, field_type: str = "text", mode: str = "") -> Field:
    return field_from_api({"id": id, "label": label, "fieldType": field_type, "mode": mode})


def table(name: str = "Tasks", id: str = "bqtasks") -> TableInfo:
    return table_info_from_api({"id": id, "name": name})


BUILT_IN = [
    field(1, "Date Created", "timestamp"),
    field(2, "Date Modified", "timestamp"),
    field(3, "Record ID#", "recordid"),
    field(4, "Record Owner", "user"),
    field(5, "Last Modified By", "user"),
]
TASK_FIELDS = [
    *BUILT_IN,
    field(6, "Title"),
    field(7, "Due Date", "date"),
    field(8, "Amount", "numeric"),
    field(9, "Done", "checkbox"),
    field(10, "Attachment", "file"),
    field(11, "Total", "numeric", "formula"),
    field(12, "Parent Name", "text", "lookup"),
    field(13, "Sum", "numeric", "summary"),
    field(14, "Where", "address"),
    field(15, "Strange New Type", "something-new"),
    field(16, "Tags", "multitext"),
]


def load(source: str) -> types.ModuleType:
    module = types.ModuleType("generated_tables")
    sys.modules[module.__name__] = module
    exec(compile(source, "generated_tables.py", "exec"), module.__dict__)
    return module


def names_of(fields: list[Field]) -> dict[str, int]:
    cls = load(render([(table(), fields)])).Tasks
    return {column.label: column.name for column in cls.__columns__}


def test_generated_module_reads_a_record_with_its_types() -> None:
    tasks = load(render([(table(), TASK_FIELDS)])).Tasks
    labels = {f.label: f.id for f in TASK_FIELDS}
    values = dict.fromkeys(labels.values(), None) | {3: 7, 6: "Pour", 7: date(2027, 1, 2)}
    record = tasks.from_record(Record(values, labels))
    assert (record.id, record.title, record.due_date) == (7, "Pour", date(2027, 1, 2))
    assert repr(record) == f"Tasks(id=7, fields={len(TASK_FIELDS)})"
    assert "Pour" not in repr(record)
    with pytest.raises(FrozenInstanceError):
        record.title = "Changed"


def test_new_only_accepts_writable_fields_and_leaves_out_unset_ones() -> None:
    tasks = load(render([(table(), TASK_FIELDS)])).Tasks
    assert tasks.new(title="Pour") == {6: "Pour"}
    assert tasks.new(title=None, done=True) == {6: None, 9: True}
    upload = FileUpload("a.txt", b"x")
    assert tasks.new(attachment=upload) == {10: upload}
    for read_only in (
        "id",
        "date_created",
        "total",
        "parent_name",
        "sum_",
        "where",
        "record_owner",
    ):
        with pytest.raises(TypeError):
            tasks.new(**{read_only: 1})
    with pytest.raises(TypeError):
        tasks.new(strange_new_type=1)


def test_change_needs_the_record_id() -> None:
    tasks = load(render([(table(), TASK_FIELDS)])).Tasks
    assert tasks.change(7, title="Pour") == {3: 7, 6: "Pour"}
    assert tasks.change(7, title=UNSET) == {3: 7}
    with pytest.raises(TypeError):
        tasks.change(title="Pour")


def test_fields_work_in_queries_by_id() -> None:
    tasks = load(render([(table(), TASK_FIELDS)])).Tasks
    assert (tasks.fields.title == "x").compile  # a real condition
    assert tasks.fields.title.id == 6
    assert tasks.fields.title.desc == -6
    assert order_refs(None) is None
    assert order_refs(tasks.fields.title) == [6]
    assert order_refs(-6) == [-6]
    assert order_refs([tasks.fields.title.desc, 7]) == [-6, 7]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Due Date", "due_date"),
        ("  spaced  ", "spaced"),
        ("_private", "private"),
        ("__dunder__", "dunder"),
        ("Résumé", "resume"),
        ("1st", "f_1st"),
        ("class", "class_"),
        ("type", "type_"),
        ("exit", "exit_"),
        ("Lazy", "lazy"),
        ("frozendict", "frozendict"),
        ("sentinel", "sentinel"),
        ("None", "none"),
        ("match", "match_"),
        ("str", "str_"),
        ("date", "date"),
        ("id", "id_"),
        ("fields", "fields_"),
        ("new", "new_"),
        ("change", "change_"),
        ("cls", "cls_"),
        ("from_record", "from_record_"),
        ("日本語", "field_6"),
        ("😀", "field_6"),
        ("\\", "field_6"),
        ("a" * 200, "a" * 60),
    ],
)
def test_labels_become_safe_names(label: str, expected: str) -> None:
    assert names_of([*BUILT_IN, field(6, label)])[label] == expected


def test_collisions_keep_the_oldest_field_on_the_plain_name() -> None:
    fields = [*BUILT_IN, field(9, "Due-Date"), field(6, "Due Date"), field(7, "due_date")]
    assert names_of(fields) == {
        **names_of(BUILT_IN),
        "Due Date": "due_date",
        "due_date": "due_date_7",
        "Due-Date": "due_date_9",
    }


def test_adding_a_field_never_renames_an_existing_one() -> None:
    before = names_of([*BUILT_IN, field(6, "Due Date"), field(7, "Due-Date")])
    after = names_of([*BUILT_IN, field(6, "Due Date"), field(7, "Due-Date"), field(8, "Due/Date")])
    assert before.items() <= after.items()
    assert after["Due/Date"] == "due_date_8"


def test_a_suffixed_name_that_is_also_taken_still_gets_a_unique_name() -> None:
    fields = [*BUILT_IN, field(6, "a b"), field(7, "a_b_8"), field(8, "a-b")]
    names = names_of(fields)
    assert len(set(names.values())) == len(fields)
    assert names["a-b"] == "a_b_8_"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Crew Members", "CrewMembers"),
        ("1st Table", "T1stTable"),
        ("Row", "Row_"),
        ("unset", "Unset_"),
        ("exception", "Exception_"),
        ("日本語", "TableBqtasks"),
    ],
)
def test_table_names_become_safe_class_names(name: str, expected: str) -> None:
    module = load(render([(table(name), BUILT_IN)]))
    assert hasattr(module, expected)


def test_tables_that_collapse_to_one_name_stay_separate() -> None:
    module = load(
        render([(table("Crew Members", "bqa"), BUILT_IN), (table("Crew-Members", "bqb"), BUILT_IN)])
    )
    assert module.CrewMembers.__table_id__ == "bqa"
    assert module.CrewMembers_bqb.__table_id__ == "bqb"


def test_a_table_named_like_another_tables_helper_stays_separate() -> None:
    module = load(
        render([(table("Tasks", "bqa"), BUILT_IN), (table("Tasks Fields", "bqb"), BUILT_IN)])
    )
    assert module.Tasks.__table_id__ == "bqa"
    assert module.TasksFields_bqb.__table_id__ == "bqb"


def test_a_table_with_only_built_in_fields_generates_working_writers() -> None:
    tasks = load(render([(table(), BUILT_IN)])).Tasks
    assert tasks.new() == {}
    assert tasks.change(7) == {3: 7}


HOSTILE = [
    'x"""; import os #',
    "x\nimport os\nraise SystemExit('owned')",
    "x'); raise SystemExit('owned') #",
    "\\",
    '"',
    "'",
    "{}{0}%s",
    "\r\n\t\x00",
    "\u2028import os",
    "#",
    "class X: pass",
    "__import__('os').system('echo owned')",
]


@pytest.mark.parametrize("label", HOSTILE)
def test_a_hostile_label_stays_a_string(label: str) -> None:
    source = render([(table(label, "bqtasks"), [*BUILT_IN, field(6, label)])])
    tree = ast.parse(source)  # raises if the label broke out of its string
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom)]
    assert len(imports) == 4
    top_level = {type(node) for node in tree.body}
    assert top_level == {ast.Import, ast.ImportFrom, ast.ClassDef}
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert calls <= {"Column", "TypedField", "dataclass"}
    module = load(source)  # loading it must not raise SystemExit or run anything else
    generated = [item for item in vars(module).values() if hasattr(item, "__columns__")]
    assert generated[0].__columns__[-1].label == label


def test_a_hostile_table_id_stays_a_string() -> None:
    source = render([(table("Tasks", "x'); raise SystemExit('owned') #"), BUILT_IN)])
    assert load(source).Tasks.__table_id__ == "x'); raise SystemExit('owned') #"


def test_source_is_stable_and_holds_no_realm() -> None:
    first = render([(table("B", "bqb"), BUILT_IN), (table("A", "bqa"), BUILT_IN)])
    second = render([(table("A", "bqa"), BUILT_IN), (table("B", "bqb"), BUILT_IN)])
    assert first == second
    assert first.startswith(HEADER)


def test_unset_has_a_readable_repr() -> None:
    assert repr(UNSET) == "UNSET"


def test_keywords_of_this_python_are_reserved() -> None:
    # A new keyword can't be an attribute, so a failure here needs a decision about naming.
    assert set(keyword.kwlist) <= _RESERVED_NAMES


def test_is_writable() -> None:
    assert is_writable(field(6, "A"))
    assert is_writable(field(6, "A", "file"))
    assert not is_writable(field(3, "A", "recordid"))
    assert not is_writable(field(1, "A", "timestamp"))
    assert not is_writable(field(6, "A", "numeric", "formula"))
    assert not is_writable(field(6, "A", "address"))
    assert not is_writable(field(6, "A", "dblink"))
    assert not is_writable(field(6, "A", "new-type"))


def test_schema_problems_lists_each_change() -> None:
    tasks = load(
        render(
            [
                (
                    table(),
                    [
                        *BUILT_IN,
                        field(6, "Title"),
                        field(7, "Amount", "numeric"),
                        field(8, "Total", "numeric"),
                    ],
                )
            ]
        )
    ).Tasks
    same = [
        *BUILT_IN,
        field(6, "Renamed"),
        field(7, "Amount", "numeric"),
        field(8, "Total", "numeric"),
        field(9, "Extra"),
    ]
    assert schema_problems(tasks, same) == []
    changed = [*BUILT_IN, field(7, "Amount", "text"), field(8, "Total", "numeric", "formula")]
    problems = schema_problems(tasks, changed)
    assert problems == [
        "title (field 6) no longer exists, or this token can't see it",
        "amount was numeric and is now text",
        "total is now read-only",
    ]
    assert (
        schema_problems(
            load(render([(table(), [*BUILT_IN, field(8, "Total", "numeric", "formula")])])).Tasks,
            [*BUILT_IN, field(8, "Total", "numeric")],
        )
        == []
    )
    with pytest.raises(SchemaMismatchError, match=r"Tasks doesn't match.*codegen"):
        raise_for_problems(tasks, problems)
    raise_for_problems(tasks, [])


def test_missing_value_is_reported_clearly() -> None:
    from pyquickbase import InvalidResponseError

    tasks = load(render([(table(), BUILT_IN)])).Tasks
    with pytest.raises(InvalidResponseError, match="missing a field"):
        tasks.from_record(Record({3: 1}, {"Record ID#": 3}))


def test_generated_code_type_checks_and_catches_mistakes(tmp_path: Path) -> None:
    label_fields = [
        *TASK_FIELDS,
        field(30, "date"),
        field(31, "datetime"),
        field(32, "Any"),
        field(33, "int"),
    ]
    (tmp_path / "tables.py").write_text(render([(table(), label_fields)]))
    (tmp_path / "good.py").write_text(
        "from pyquickbase import Quickbase, FileUpload\n"
        "from tables import Tasks\n"
        "def use(qb: Quickbase) -> None:\n"
        "    tasks = qb.app('bq').typed(Tasks)\n"
        "    where = Tasks.fields.title == 'x'\n"
        "    for task in tasks.query(where=where, order_by=Tasks.fields.title.desc):\n"
        "        title: str = task.title\n"
        "        amount: float | None = task.amount\n"
        "        print(title, amount)\n"
        "    tasks.insert([Tasks.new(title='a', attachment=FileUpload('a', b''))])\n"
        "    tasks.update([Tasks.change(1, title=None)])\n"
    )
    (tmp_path / "bad.py").write_text(
        "from pyquickbase import Quickbase\n"
        "from tables import Tasks\n"
        "def use(qb: Quickbase) -> None:\n"
        "    tasks = qb.app('bq').typed(Tasks)\n"
        "    for task in tasks.query():\n"
        "        print(task.nope)\n"  # 1: unknown field
        "        wrong: int = task.title\n"  # 2: wrong type
        "        print(wrong)\n"
        "    Tasks.new(total=1)\n"  # 3: read-only field
        "    Tasks.new(title=1)\n"  # 4: wrong value type
        "    Tasks.new(attachment=None)\n"  # 5: file fields take only FileUpload
    )
    (tmp_path / "pyrightconfig.json").write_text(json.dumps({"typeCheckingMode": "strict"}))
    command = [sys.executable, "-m", "pyright", "--pythonpath", sys.executable, "--outputjson"]
    result = subprocess.run(
        [*command, "."], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    errors = {
        (Path(item["file"]).name, item["range"]["start"]["line"] + 1)
        for item in json.loads(result.stdout)["generalDiagnostics"]
        if item["severity"] == "error"
    }
    assert errors == {("bad.py", line) for line in (6, 7, 9, 10, 11)}


class Server:
    """Serves one app's metadata to the command, and refuses anything that isn't a read."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/tables"):
            return httpx.Response(
                200, json=[{"id": "bqtasks", "name": "Tasks"}, {"id": "bqother", "name": "Other"}]
            )
        return httpx.Response(
            200,
            json=[
                {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
                {"id": 6, "label": "Title", "fieldType": "text"},
            ],
        )


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Server:
    monkeypatch.setenv("QB_REALM", "acme")
    monkeypatch.setenv("QB_USER_TOKEN", "fake-user-token")
    return Server()


def run(server: Any, *args: str) -> int:
    return main(["codegen", *args], http_client=httpx.Client(transport=httpx.MockTransport(server)))


def test_command_writes_a_module(server: Server, tmp_path: Path) -> None:
    output = tmp_path / "tables.py"
    assert run(server, "--app", "bq123", "--output", str(output)) == 0
    source = output.read_text()
    assert "class Tasks(TypedRecord)" in source
    assert "class Other(TypedRecord)" in source
    assert "acme" not in source
    assert "fake-user-token" not in source
    assert {request.method for request in server.requests} == {"GET"}
    assert [item.name for item in tmp_path.iterdir()] == ["tables.py"]


def test_command_can_limit_tables(server: Server, tmp_path: Path, capsys: Any) -> None:
    output = tmp_path / "tables.py"
    assert run(server, "--app", "bq123", "--output", str(output), "--table", "Tasks") == 0
    assert "class Other(" not in output.read_text()
    assert run(server, "--app", "bq123", "--output", str(output), "--table", "bqother") == 0
    assert "class Tasks(" not in output.read_text()
    assert run(server, "--app", "bq123", "--output", str(output), "--table", "Nope") == 1
    assert "no table 'Nope'" in capsys.readouterr().err


def test_check_reports_a_stale_file_and_changes_nothing(
    server: Server, tmp_path: Path, capsys: Any
) -> None:
    output = tmp_path / "tables.py"
    assert run(server, "--app", "bq123", "--output", str(output), "--check") == 1
    assert not output.exists()
    assert run(server, "--app", "bq123", "--output", str(output)) == 0
    assert run(server, "--app", "bq123", "--output", str(output), "--check") == 0
    output.write_text(output.read_text() + "# changed\n")
    changed = output.read_text()
    assert run(server, "--app", "bq123", "--output", str(output), "--check") == 1
    assert output.read_text() == changed
    assert "out of date" in capsys.readouterr().err


def test_command_wont_replace_a_file_it_didnt_generate(
    server: Server, tmp_path: Path, capsys: Any
) -> None:
    output = tmp_path / "tables.py"
    output.write_text("important = True\n")
    assert run(server, "--app", "bq123", "--output", str(output)) == 1
    assert output.read_text() == "important = True\n"
    assert "wasn't generated" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no Unix file modes")
def test_command_keeps_a_readable_file_mode(server: Server, tmp_path: Path) -> None:
    output = tmp_path / "tables.py"
    assert run(server, "--app", "bq123", "--output", str(output)) == 0
    assert output.stat().st_mode & 0o777 == 0o644
    output.chmod(0o640)
    assert run(server, "--app", "bq123", "--output", str(output)) == 0
    assert output.stat().st_mode & 0o777 == 0o640


def test_command_replaces_its_own_file(server: Server, tmp_path: Path) -> None:
    output = tmp_path / "tables.py"
    output.write_text(HEADER + "# old\n")
    assert run(server, "--app", "bq123", "--output", str(output)) == 0
    assert "# old" not in output.read_text()


def test_failed_write_leaves_nothing_behind(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    def fail(source: str, target: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", fail)
    assert run(server, "--app", "bq123", "--output", str(tmp_path / "tables.py")) == 1
    assert list(tmp_path.iterdir()) == []
    assert "disk full" in capsys.readouterr().err


def test_errors_never_show_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("QB_REALM", "acme")
    monkeypatch.setenv("QB_USER_TOKEN", "fake-user-token")

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Access denied"})

    assert run(denied, "--app", "bq123", "--output", str(tmp_path / "t.py")) == 1
    err = capsys.readouterr().err
    assert "Access denied" in err
    assert "fake-user-token" not in err


def test_missing_environment_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.delenv("QB_REALM", raising=False)
    monkeypatch.delenv("QB_USER_TOKEN", raising=False)
    assert run(Server(), "--app", "bq123", "--output", str(tmp_path / "t.py")) == 1
    assert "QB_REALM" in capsys.readouterr().err


def test_the_command_needs_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_generated_files_are_utf8_whatever_the_locale(tmp_path: Path) -> None:
    # Python before 3.15 uses the locale's encoding by default, which on Windows can't hold labels
    # like these, and source files are read back as UTF-8.
    output = tmp_path / "tables.py"
    source = render([(table(), [*BUILT_IN, field(6, "Déjà 日本語")])])
    code = (
        "import pathlib, sys\n"
        "from pyquickbase._cli import _write\n"
        f"source = {source!a}\n"
        "path = pathlib.Path(sys.argv[1])\n"
        "_write(path, source)\n"
        "_write(path, source)\n"
    )
    env = {"PATH": "", "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    run = subprocess.run(
        [sys.executable, "-c", code, str(output)], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0, run.stderr[-300:]
    assert output.read_bytes().decode("utf-8") == source
    compile(output.read_bytes(), "tables.py", "exec")


def test_links_made_for_a_relationship_are_left_out_by_default() -> None:
    add = field_from_api(
        {
            "id": 7,
            "label": "Add Task",
            "fieldType": "url",
            "mode": "formula",
            "properties": {"formula": 'URLRoot() & "db/" & [_DBID_T] & "?a=API_GenAddRecordForm"'},
        }
    )
    records = field(8, "Task records", "dblink")
    other_link = field_from_api(
        {
            "id": 9,
            "label": "Site",
            "fieldType": "url",
            "mode": "formula",
            "properties": {"formula": '"https://example.com"'},
        }
    )
    fields = [*BUILT_IN, field(6, "Title"), add, records, other_link]
    source = render([(table(), fields)])
    assert "add_task" not in source
    assert "task_records" not in source
    assert "site" in source
    included = render([(table(), fields)], include_links=True)
    assert "add_task" in included
    assert "task_records" in included


def test_decimal_is_only_imported_when_used() -> None:
    assert "Decimal" not in render([(table(), [*BUILT_IN, field(6, "Title")])])
    assert "as _Decimal" in render([(table(), [*BUILT_IN, field(6, "Price", "currency")])])


def test_codegen_can_read_another_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json=[])

    monkeypatch.setenv("QB_REALM", "acme")
    monkeypatch.delenv("QB_USER_TOKEN", raising=False)
    monkeypatch.setenv("QB_SERVICE_TOKEN", "service-secret")
    code = main(
        ["codegen", "--app", "bq1", "--output", str(tmp_path / "t.py"),
         "--token-env", "QB_SERVICE_TOKEN"],
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )  # fmt: skip
    assert code == 0
    assert seen == ["QB-USER-TOKEN service-secret"]
