# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Every public method of the client against a real app, on temporary tables."""

import importlib.util
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

import pyquickbase
from pyquickbase import (
    UNSET,
    AppInfo,
    F,
    FileUpload,
    FormulaResult,
    NotFoundError,
    Page,
    QueryError,
    Quickbase,
    ReadOnlyError,
    Record,
    RetryPolicy,
    SchemaMismatchError,
    TableInfo,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    WriteError,
    parse_where,
)

FIELDS = (
    ("Title", "text"),
    ("Code", "text", {"unique": True}),
    ("Due Date", "date"),
    ("Started", "timestamp"),
    ("Amount", "numeric"),
    ("Price", "currency"),
    ("Done", "checkbox"),
    ("Tags", "multitext"),
    ("Attachment", "file"),
    ("Doubled", "numeric", {"formula": "[Amount] * 2"}),
)
SEED = [
    {"Title": f"Row {n}", "Code": f"C{n}", "Due Date": date(2027, 1, n), "Amount": n}
    for n in range(1, 6)
]


@pytest.fixture(scope="module")
def table_id(scratch: Any) -> str:
    return scratch.table(*FIELDS).info().id


@pytest.fixture
def seeded(scratch: Any, table_id: str) -> str:
    table = scratch.client.app(scratch.app_id).table(table_id)
    scratch.fill(table, SEED)
    return table_id


def get(qb: Quickbase, scratch: Any, table_id: str) -> Any:
    return qb.app(scratch.app_id).table(table_id)


def one(table: Any, code: str, *select: str) -> Record:
    found = [r for r in table.query(select=list(select), where=F("Code") == code)]
    assert len(found) == 1
    return found[0]


def test_the_expected_package_is_being_tested() -> None:
    expected = os.environ.get("QB_LIVE_VERSION")
    if expected:
        assert pyquickbase.__version__ == expected
        assert "site-packages" in pyquickbase.__file__


def test_client_and_app_details(qb: Quickbase, scratch: Any, table_id: str) -> None:
    assert qb.realm == scratch.realm or scratch.realm in qb.realm
    app = qb.app(scratch.app_id)
    assert app.id == scratch.app_id
    assert isinstance(app.info(), AppInfo)
    tables = app.tables()
    assert all(isinstance(item, TableInfo) for item in tables)
    assert table_id in {item.id for item in tables}
    qb.clear_schema_cache()
    assert table_id in {item.id for item in app.tables(refresh=True)}


def test_from_env_reads_the_same_app(scratch: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QB_REALM", scratch.realm)
    monkeypatch.setenv("QB_USER_TOKEN", scratch.token)
    with Quickbase.from_env(user_agent="live-test/1") as qb:
        assert (qb.app(scratch.app_id).info()).id == scratch.app_id


def test_finding_a_table_by_name_and_id(qb: Quickbase, scratch: Any, table_id: str) -> None:
    table = get(qb, scratch, table_id)
    info = table.info()
    assert (qb.app(scratch.app_id).table(info.name).info()).id == table_id
    assert info.key_field_id == 3
    with pytest.raises(UnknownTableError):
        qb.app(scratch.app_id).table("No Such Table At All").info()


def test_fields_and_labels(qb: Quickbase, scratch: Any, table_id: str) -> None:
    table = get(qb, scratch, table_id)
    fields = {item.label: item for item in table.fields()}
    assert fields["Title"].type == "text"
    assert fields["Code"].unique
    assert fields["Doubled"].mode == "formula"
    assert fields["Doubled"].formula == "[Amount] * 2"
    assert table.resolve_field_ids(["Title", 3]) == [fields["Title"].id, 3]
    with pytest.raises(UnknownFieldError, match="Did you mean 'Title'"):
        table.resolve_field_ids(["Titel"])


def test_values_read_with_their_types(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    started = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    table.upsert(
        [{"Code": "C1", "Started": started, "Price": 19.99, "Done": True, "Tags": ["a", "b"]}],
        merge_on="Code",
    )
    record = one(
        table, "C1", "Title", "Due Date", "Started", "Amount", "Price", "Done", "Tags", "Doubled"
    )
    assert isinstance(record.id, int)
    assert record["Due Date"] == date(2027, 1, 1)
    assert record["Started"] == started
    assert record["Amount"] == 1
    assert record["Price"] == 19.99
    assert record["Done"] is True
    assert record["Tags"] == ["a", "b"]
    assert record["Doubled"] == 2
    assert dict(record)["Title"] == "Row 1"
    assert record[3] == record.id
    assert "Row 1" not in repr(record)
    empty = one(table, "C2", "Started", "Tags", "Price", "Done")
    assert empty["Started"] is None
    assert empty["Tags"] == []
    assert empty["Price"] is None
    assert empty["Done"] is False


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        (F("Amount") > 3, ["C4", "C5"]),
        (F("Amount") >= 4, ["C4", "C5"]),
        (F("Amount") < 2, ["C1"]),
        (F("Amount") <= 1, ["C1"]),
        (F("Amount") != 1, ["C2", "C3", "C4", "C5"]),
        (F("Code") == "c1", ["C1"]),
        (F("Title").contains("ow 2"), ["C2"]),
        (F("Title").not_contains("ow"), []),
        (F("Title").starts_with("Row 3"), ["C3"]),
        (F("Title").not_starts_with("Row"), []),
        (F("Code").is_in(["C1", "C3"]), ["C1", "C3"]),
        (F("Due Date") < date(2027, 1, 2), ["C1"]),
        (F("Due Date") >= date(2027, 1, 5), ["C5"]),
        (F("Started") == None, ["C1", "C2", "C3", "C4", "C5"]),  # noqa: E711
        (F("Started") != None, []),  # noqa: E711
        ((F("Amount") > 1) & (F("Amount") < 3), ["C2"]),
        ((F("Amount") == 1) | (F("Amount") == 5), ["C1", "C5"]),
        (((F("Amount") == 1) | (F("Amount") == 5)) & (F("Code") == "C5"), ["C5"]),
        (F("Amount").op("GTE", 5), ["C5"]),
        ("{7.EX.'C1'}OR{7.EX.'C2'}", ["C1", "C2"]),
    ],
)
def test_query_conditions_match_what_they_should(
    qb: Quickbase, scratch: Any, seeded: str, where: Any, expected: list[str]
) -> None:
    table = get(qb, scratch, seeded)
    found = [r["Code"] for r in table.query(select=["Code"], where=where, order_by="Code")]
    assert found == expected
    assert table.count(where) == len(expected)


@pytest.mark.parametrize(
    "value",
    [
        "O'Brien",
        "back\\slash",
        "two  spaces",
        "percent%",
        "dé jà 日本語",
        "a.b",
        "'",
        "\\'",
        "AND",
        "OR",
        "x OR y",
        "x AND y",
        "x\tOR\ty",
        "OR y",
        "x OR",
        "_curuser_",
        "TRUE",
        "*",
        "%",
        "_",
    ],
)
def test_text_with_special_characters_is_found_exactly(
    qb: Quickbase, scratch: Any, seeded: str, value: str
) -> None:
    table = get(qb, scratch, seeded)
    table.insert([{"Title": value, "Code": "SPECIAL"}])
    found = [r["Code"] for r in table.query(select=["Code"], where=F("Title") == value)]
    assert found == ["SPECIAL"]
    assert table.count(F("Title") == "zz" + value) == 0


@pytest.mark.parametrize(
    "value",
    [
        "zz' OR 3.GT.'0",
        "zz OR Row 1",
        "Row 1 OR Row 2",
        "zz OR C1",
        "OR Row 1",
        "Row 1 OR",
        "Row 1\tOR\tRow 2",
        "zz AND Row 1",
    ],
)
def test_a_hostile_value_cant_widen_a_query(
    qb: Quickbase, scratch: Any, seeded: str, value: str
) -> None:
    table = get(qb, scratch, seeded)
    assert table.count(F("Title") == value) == 0
    assert table.count(F("Title").contains(value)) == 0
    assert table.delete(F("Title") == value) == 0
    assert table.count() == 5


@pytest.mark.parametrize(
    "value",
    [
        "zz'}OR{3.GT.'0",
        "_FID_6",
        "_fid_6",
        "_Fid_6",
        "_FID_6x",
        "_FID_ 6",
        "x OR _FID_6",
        " _FID_6",
    ],
)
def test_a_value_quickbase_would_read_as_syntax_is_refused(
    qb: Quickbase, scratch: Any, seeded: str, value: str
) -> None:
    table = get(qb, scratch, seeded)
    with pytest.raises(QueryError):
        table.count(F("Title") == value)
    with pytest.raises(QueryError):
        table.delete(F("Title") == value)
    assert table.count() == 5


@pytest.mark.parametrize(
    "rows",
    [
        [{"Title": ""}, {"Title": "x"}],
        [{"Title": "x"}, {"Title": None}, {"Title": "y"}],
        [{"Tags": []}, {"Tags": ["a"]}],
    ],
)
def test_a_row_with_no_values_is_refused_rather_than_lost(
    qb: Quickbase, scratch: Any, seeded: str, rows: list[dict[str, Any]]
) -> None:
    table = get(qb, scratch, seeded)
    with pytest.raises(pyquickbase.InvalidValueError, match="no values"):
        table.insert(rows)
    assert table.count() == 5


def test_created_ids_line_up_with_rows(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    codes = [f"L{n}" for n in range(20)]
    made = table.insert([{"Title": code, "Code": code} for code in codes])
    stored = {
        r.id: r["Code"] for r in table.query(select=["Code"], where=F("Title").starts_with("L"))
    }
    assert [stored[record_id] for record_id in made.created_ids] == codes


def test_mistakes_in_where_strings_are_refused(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    for where in ("{7.ex.'C1'}", "{7.NOPE.'C1'}", "{999.EX.'C1'}", "{7.EX.'C1'}AND", "nonsense"):
        with pytest.raises(QueryError):
            table.count(where)
    assert [c.field_id for c in parse_where("{7.EX.'C1'}", table.fields())] == [7]


def test_paging_and_sorting(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    descending = [r["Code"] for r in table.query(select=["Code"], order_by="-Amount", page_size=2)]
    assert descending == ["C5", "C4", "C3", "C2", "C1"]
    assert len([r for r in table.query(limit=3, page_size=2)]) == 3
    page = table.query_page(select=["Code"], order_by="Code", top=2, skip=2)
    assert isinstance(page, Page)
    assert (page.total, page.skip, page.next_skip, page.has_more) == (5, 2, 4, True)
    assert [r["Code"] for r in page.records] == ["C3", "C4"]
    last = table.query_page(top=2, skip=4)
    assert not last.has_more
    assert (table.query_page(top=2, skip=100)).records == ()


def test_writes_report_what_they_did(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    created = table.insert([{"Title": "New", "Code": "N1"}])
    new_id = created.created_ids[0]
    assert (table.update([{"Record ID#": new_id, "Title": "Changed"}])).updated_ids == (new_id,)
    assert (table.update([{"Record ID#": new_id, "Title": "Changed"}])).unchanged_ids == (new_id,)
    table.update([{"Record ID#": new_id, "Title": None, "Amount": None, "Due Date": None}])
    cleared = one(table, "N1", "Title", "Amount", "Due Date")
    assert (cleared["Title"], cleared["Amount"], cleared["Due Date"]) == ("", None, None)
    assert table.delete(F("Code") == "N1") == 1
    assert table.count(F("Code") == "N1") == 0


def test_a_row_failure_keeps_the_other_rows(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    with pytest.raises(WriteError) as caught:
        table.update(
            [
                {"Record ID#": 99999999, "Title": "Missing"},
                {"Record ID#": (one(table, "C1")).id, "Title": "Written"},
            ]
        )
    result = caught.value.result
    assert [error.index for error in result.errors] == [0]
    assert len(result.updated_ids) == 1
    assert (one(table, "C1", "Title"))["Title"] == "Written"
    assert table.count() == 5


def test_writing_a_number_as_decimal(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    table.upsert([{"Code": "C1", "Price": Decimal("19.99")}], merge_on="Code")
    assert (one(table, "C1", "Price"))["Price"] == 19.99


def test_files(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    record_id = (one(table, "C1")).id
    data = bytes(range(256))
    table.update([{"Record ID#": record_id, "Attachment": FileUpload("a b é.bin", data)}])
    download = table.download_file(record_id, "Attachment")
    assert (download.name, download.data) == ("a b é.bin", data)
    table.update([{"Record ID#": record_id, "Attachment": FileUpload("b.txt", b"second")}])
    assert (table.download_file(record_id, "Attachment")).data == b"second"
    assert (table.download_file(record_id, "Attachment", version=1)).data == data
    value = (one(table, "C1", "Attachment"))["Attachment"]
    assert [v["versionNumber"] for v in value["versions"]] == [1, 2]
    table.delete_file_version(record_id, "Attachment", version=1)
    with pytest.raises(NotFoundError):
        table.download_file(record_id, "Attachment", version=1)
    table.delete_file_version(record_id, "Attachment", version=2)
    assert (one(table, "C1", "Attachment"))["Attachment"]["versions"] == []


def test_formulas(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    record_id = (one(table, "C3")).id
    result = table.run_formula("[Amount] + 1", record_id=record_id)
    assert isinstance(result, FormulaResult)
    assert (result.type, result.value) == ("number", Decimal(4))
    assert (table.run_formula("1 > 2")).value is False
    assert (table.run_formula('"text"')).value == "text"
    assert (table.run_formula("Today()")).type == "date"


def test_reports(qb: Quickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    reports = table.reports(refresh=True)
    assert {report.name for report in reports} >= {"List All", "List Changes"}
    report = table.report("List All")
    assert (report.info()).type == "table"
    assert (table.report("1").info()).name == "List All"
    records = [r for r in report.run(page_size=2)]
    assert len(records) == 5
    assert (report.run_page(top=2, skip=1)).total == 5
    assert len([r for r in report.run(limit=1)]) == 1
    with pytest.raises(UnknownReportError):
        table.report("No Such Report").info()


def test_schema_changes(qb: Quickbase, scratch: Any) -> None:
    table = qb.app(scratch.app_id).table((scratch.table(("A", "text"))).info().id)
    made = table.create_field("Extra", "text", required=False, choices=None)
    assert made.label == "Extra"
    changed = table.update_field("Extra", label="Renamed")
    assert changed.label == "Renamed"
    assert "Renamed" in {item.label for item in table.fields()}
    formula = table.create_field("F", "numeric", formula="1 + 1")
    assert (table.update_field("F", formula="2 + 2")).formula == "2 + 2"
    table.delete_fields(["Renamed", formula.id])
    assert not {"Renamed", "F"} & {item.label for item in table.fields()}


def test_relationships(qb: Quickbase, scratch: Any) -> None:
    parent = qb.app(scratch.app_id).table(scratch.table(("Name", "text")).info().id)
    child = qb.app(scratch.app_id).table(scratch.table(("Title", "text")).info().id)
    link = child.create_relationship(parent, foreign_key_label="Parent", lookup_fields=["Name"])
    assert link.parent_table_id == (parent.info()).id
    assert link.child_table_id == (child.info()).id
    assert link.foreign_key_field_id is not None
    assert len(link.lookup_field_ids) == 1
    assert [item.id for item in child.relationships(refresh=True)] == [link.id]
    assert parent.relationships(refresh=True) == []


def test_drop_needs_the_exact_name(qb: Quickbase, scratch: Any) -> None:
    from pyquickbase import InvalidValueError

    made = scratch.table(("A", "text"))
    scratch.created.remove(made)
    table = qb.app(scratch.app_id).table(made.info().id)
    name = (table.info()).name
    with pytest.raises(InvalidValueError):
        table.drop(confirm=name + "x")
    table.drop(confirm=name)
    with pytest.raises(UnknownTableError):
        table.info()


def test_read_only_and_allowed_writes(scratch: Any, seeded: str) -> None:
    with Quickbase(realm=scratch.realm, token=scratch.token, read_only=True) as qb:
        table = get(qb, scratch, seeded)
        assert table.count() == 5
        assert (table.run_formula("1 + 1")).value == 2
        assert len([r for r in table.report("List All").run()]) == 5
        for attempt in (
            lambda: table.insert([{"Title": "x"}]),
            lambda: table.delete("{3.GT.'0'}"),
            lambda: table.create_field("X", "text"),
            lambda: table.drop(confirm=""),
        ):
            try:
                attempt()
            except ReadOnlyError:
                pass
            except pyquickbase.InvalidValueError:
                pass  # drop() checks the name first
            else:
                pytest.fail("A read-only client sent a write.")
    assert scratch.client.app(scratch.app_id).table(seeded).count() == 5
    writes = {("POST", "/records")}
    with Quickbase(realm=scratch.realm, token=scratch.token, allowed_writes=writes) as qb:
        table = get(qb, scratch, seeded)
        table.insert([{"Title": "Allowed", "Code": "A1"}])
        with pytest.raises(ReadOnlyError):
            table.delete(F("Code") == "A1")


def test_custom_http_client_and_retry_settings(scratch: Any, seeded: str) -> None:
    seen: list[str] = []

    def log(request: httpx.Request) -> None:
        seen.append(request.headers["user-agent"])

    with httpx.Client(event_hooks={"request": [log]}, timeout=60) as http:
        with Quickbase(
            realm=scratch.realm,
            token=scratch.token,
            http_client=http,
            retry=RetryPolicy(max_retries=1),
            user_agent="live-test/1",
            schema_ttl=0,
        ) as qb:
            assert get(qb, scratch, seeded).count() == 5
    assert seen
    assert seen[0].startswith("live-test/1 pyquickbase/")


def test_request_escape_hatch(qb: Quickbase, scratch: Any, seeded: str) -> None:
    body = qb.request("GET", "/fields", params={"tableId": seeded})
    assert isinstance(body, list)
    with pytest.raises(pyquickbase.APIError) as caught:
        qb.request("GET", "/fields", params={"tableId": "bqnosuchtable"})
    assert caught.value.status_code in (400, 401, 404)
    assert caught.value.request_id
    assert scratch.token not in str(caught.value)


def test_a_bad_token_is_an_authentication_error(scratch: Any) -> None:
    with Quickbase(realm=scratch.realm, token="fake-user-token") as qb:
        with pytest.raises(pyquickbase.QuickbaseError) as caught:
            qb.app(scratch.app_id).tables()
    assert "fake-user-token" not in str(caught.value)


def generated_class(scratch: Any, table_id: str, tmp_path: Path) -> Any:
    """Run the installed command, as a user would, and import what it wrote."""
    output = tmp_path / "live_tables.py"
    command = [sys.executable, "-c", "from pyquickbase._cli import main; raise SystemExit(main())"]
    arguments = ["codegen", "--app", scratch.app_id, "--output", str(output), "--table", table_id]
    env = {**os.environ, "QB_REALM": scratch.realm, "QB_USER_TOKEN": scratch.token}
    subprocess.run([*command, *arguments], env=env, check=True, capture_output=True)
    subprocess.run([*command, *arguments, "--check"], env=env, check=True, capture_output=True)
    source = output.read_text()
    assert scratch.token not in source
    assert scratch.realm not in source
    name = f"live_tables_{table_id}"
    spec = importlib.util.spec_from_file_location(name, output)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return next(
        value
        for key, value in vars(module).items()
        if isinstance(value, type)
        and issubclass(value, pyquickbase.TypedRecord)
        and value is not pyquickbase.TypedRecord
        and key != "TypedRecord"
    )


def test_generated_classes_read_and_write(
    qb: Quickbase, scratch: Any, seeded: str, tmp_path: Path
) -> None:
    Tasks = generated_class(scratch, seeded, tmp_path)
    tasks = qb.app(scratch.app_id).typed(Tasks)
    tasks.check_schema()
    found = [
        task
        for task in tasks.query(
            where=Tasks.fields.amount > 3,
            order_by=Tasks.fields.amount.desc,
            page_size=1,
        )
    ]
    assert [task.code for task in found] == ["C5", "C4"]
    assert found[0].due_date == date(2027, 1, 5)
    assert found[0].doubled == 10
    assert found[0].started is None
    assert found[0].tags == []
    assert found[0].attachment == {"url": "", "versions": []}
    assert "Row" not in repr(found[0])
    assert tasks.count(Tasks.fields.code == "C1") == 1

    made = tasks.insert([Tasks.new(title="Typed", code="T1", amount=7, due_date=date(2027, 2, 1))])
    new_id = made.created_ids[0]
    assert (tasks.update([Tasks.change(new_id, title="Typed 2", amount=UNSET)])).updated_ids
    tasks.update([Tasks.change(new_id, due_date=None)])
    assert tasks.upsert([Tasks.new(code="T1", amount=8)], merge_on=Tasks.fields.code)
    rows = [t for t in tasks.query(where=Tasks.fields.code == "T1")]
    assert len(rows) == 1
    row = rows[0]
    assert (row.title, row.amount, row.due_date) == ("Typed 2", 8, None)
    tasks.update([Tasks.change(new_id, attachment=FileUpload("t.txt", b"typed"))])
    assert (tasks.table.download_file(new_id, "Attachment")).data == b"typed"
    assert tasks.delete(Tasks.fields.code == "T1") == 1
    with pytest.raises(TypeError):
        Tasks.new(doubled=1)


def test_check_schema_notices_what_first_use_doesnt(
    scratch: Any, qb: Quickbase, tmp_path: Path
) -> None:
    made = scratch.table(("Title", "text"), ("Gone", "text"), ("Changing", "text"))
    Tasks = generated_class(scratch, made.info().id, tmp_path)
    tasks = qb.app(scratch.app_id).typed(Tasks)
    assert tasks.count() == 0
    made.delete_fields("Gone")
    assert tasks.count() == 0  # Checked once already, so this isn't noticed.
    with pytest.raises(SchemaMismatchError, match=r"gone \(field \d+\) no longer exists"):
        tasks.check_schema()
    with pytest.raises(SchemaMismatchError):
        tasks.count()
