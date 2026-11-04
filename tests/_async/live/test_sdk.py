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
    AsyncQuickbase,
    F,
    FileUpload,
    FormulaResult,
    NotFoundError,
    Page,
    QueryError,
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

pytestmark = pytest.mark.anyio

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


def get(qb: AsyncQuickbase, scratch: Any, table_id: str) -> Any:
    return qb.app(scratch.app_id).table(table_id)


async def one(table: Any, code: str, *select: str) -> Record:
    found = [r async for r in table.query(select=list(select), where=F("Code") == code)]
    assert len(found) == 1
    return found[0]


def test_the_expected_package_is_being_tested() -> None:
    expected = os.environ.get("QB_LIVE_VERSION")
    if expected:
        assert pyquickbase.__version__ == expected
        assert "site-packages" in pyquickbase.__file__


async def test_client_and_app_details(qb: AsyncQuickbase, scratch: Any, table_id: str) -> None:
    assert qb.realm == scratch.realm or scratch.realm in qb.realm
    app = qb.app(scratch.app_id)
    assert app.id == scratch.app_id
    assert isinstance(await app.info(), AppInfo)
    tables = await app.tables()
    assert all(isinstance(item, TableInfo) for item in tables)
    assert table_id in {item.id for item in tables}
    qb.clear_schema_cache()
    assert table_id in {item.id for item in await app.tables(refresh=True)}


async def test_from_env_reads_the_same_app(scratch: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QB_REALM", scratch.realm)
    monkeypatch.setenv("QB_USER_TOKEN", scratch.token)
    async with AsyncQuickbase.from_env(user_agent="live-test/1") as qb:
        assert (await qb.app(scratch.app_id).info()).id == scratch.app_id


async def test_finding_a_table_by_name_and_id(
    qb: AsyncQuickbase, scratch: Any, table_id: str
) -> None:
    table = get(qb, scratch, table_id)
    info = await table.info()
    assert (await qb.app(scratch.app_id).table(info.name).info()).id == table_id
    assert info.key_field_id == 3
    with pytest.raises(UnknownTableError):
        await qb.app(scratch.app_id).table("No Such Table At All").info()


async def test_fields_and_labels(qb: AsyncQuickbase, scratch: Any, table_id: str) -> None:
    table = get(qb, scratch, table_id)
    fields = {item.label: item for item in await table.fields()}
    assert fields["Title"].type == "text"
    assert fields["Code"].unique
    assert fields["Doubled"].mode == "formula"
    assert fields["Doubled"].formula == "[Amount] * 2"
    assert await table.resolve_field_ids(["Title", 3]) == [fields["Title"].id, 3]
    with pytest.raises(UnknownFieldError, match="Did you mean 'Title'"):
        await table.resolve_field_ids(["Titel"])


async def test_values_read_with_their_types(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    started = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    await table.upsert(
        [{"Code": "C1", "Started": started, "Price": 19.99, "Done": True, "Tags": ["a", "b"]}],
        merge_on="Code",
    )
    record = await one(
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
    empty = await one(table, "C2", "Started", "Tags", "Price", "Done")
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
async def test_query_conditions_match_what_they_should(
    qb: AsyncQuickbase, scratch: Any, seeded: str, where: Any, expected: list[str]
) -> None:
    table = get(qb, scratch, seeded)
    found = [r["Code"] async for r in table.query(select=["Code"], where=where, order_by="Code")]
    assert found == expected
    assert await table.count(where) == len(expected)


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
async def test_text_with_special_characters_is_found_exactly(
    qb: AsyncQuickbase, scratch: Any, seeded: str, value: str
) -> None:
    table = get(qb, scratch, seeded)
    await table.insert([{"Title": value, "Code": "SPECIAL"}])
    found = [r["Code"] async for r in table.query(select=["Code"], where=F("Title") == value)]
    assert found == ["SPECIAL"]
    assert await table.count(F("Title") == "zz" + value) == 0


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
        "\\_FID_6",
        "_FID\\_6",
        "'_FID_6",
        ",_FID_6",
        "x_FID_6",
        "\u00a0Row 1",
    ],
)
async def test_a_hostile_value_cant_widen_a_query(
    qb: AsyncQuickbase, scratch: Any, seeded: str, value: str
) -> None:
    table = get(qb, scratch, seeded)
    assert await table.count(F("Title") == value) == 0
    assert await table.count(F("Title").contains(value)) == 0
    assert await table.delete(F("Title") == value) == 0
    assert await table.count() == 5


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
        "\t_FID_6",
    ],
)
async def test_a_value_quickbase_would_read_as_syntax_is_refused(
    qb: AsyncQuickbase, scratch: Any, seeded: str, value: str
) -> None:
    table = get(qb, scratch, seeded)
    with pytest.raises(QueryError):
        await table.count(F("Title") == value)
    with pytest.raises(QueryError):
        await table.delete(F("Title") == value)
    assert await table.count() == 5


@pytest.mark.parametrize(
    "rows",
    [
        [{"Title": ""}, {"Title": "x"}],
        [{"Title": "x"}, {"Title": None}, {"Title": "y"}],
        [{"Tags": []}, {"Tags": ["a"]}],
    ],
)
async def test_a_row_with_no_values_is_refused_rather_than_lost(
    qb: AsyncQuickbase, scratch: Any, seeded: str, rows: list[dict[str, Any]]
) -> None:
    table = get(qb, scratch, seeded)
    with pytest.raises(pyquickbase.InvalidValueError, match="no values"):
        await table.insert(rows)
    assert await table.count() == 5


async def test_created_ids_line_up_with_rows(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    codes = [f"L{n}" for n in range(20)]
    made = await table.insert([{"Title": code, "Code": code} for code in codes])
    stored = {
        r.id: r["Code"]
        async for r in table.query(select=["Code"], where=F("Title").starts_with("L"))
    }
    assert [stored[record_id] for record_id in made.created_ids] == codes


async def test_mistakes_in_where_strings_are_refused(
    qb: AsyncQuickbase, scratch: Any, seeded: str
) -> None:
    table = get(qb, scratch, seeded)
    for where in ("{7.ex.'C1'}", "{7.NOPE.'C1'}", "{999.EX.'C1'}", "{7.EX.'C1'}AND", "nonsense"):
        with pytest.raises(QueryError):
            await table.count(where)
    assert [c.field_id for c in parse_where("{7.EX.'C1'}", await table.fields())] == [7]


async def test_paging_and_sorting(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    descending = [
        r["Code"] async for r in table.query(select=["Code"], order_by="-Amount", page_size=2)
    ]
    assert descending == ["C5", "C4", "C3", "C2", "C1"]
    assert len([r async for r in table.query(limit=3, page_size=2)]) == 3
    page = await table.query_page(select=["Code"], order_by="Code", top=2, skip=2)
    assert isinstance(page, Page)
    assert (page.total, page.skip, page.next_skip, page.has_more) == (5, 2, 4, True)
    assert [r["Code"] for r in page.records] == ["C3", "C4"]
    last = await table.query_page(top=2, skip=4)
    assert not last.has_more
    assert (await table.query_page(top=2, skip=100)).records == ()


async def test_writes_report_what_they_did(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    created = await table.insert([{"Title": "New", "Code": "N1"}])
    new_id = created.created_ids[0]
    assert (await table.update([{"Record ID#": new_id, "Title": "Changed"}])).updated_ids == (
        new_id,
    )
    assert (await table.update([{"Record ID#": new_id, "Title": "Changed"}])).unchanged_ids == (
        new_id,
    )
    await table.update([{"Record ID#": new_id, "Title": None, "Amount": None, "Due Date": None}])
    cleared = await one(table, "N1", "Title", "Amount", "Due Date")
    assert (cleared["Title"], cleared["Amount"], cleared["Due Date"]) == ("", None, None)
    assert await table.delete(F("Code") == "N1") == 1
    assert await table.count(F("Code") == "N1") == 0


async def test_a_row_failure_keeps_the_other_rows(
    qb: AsyncQuickbase, scratch: Any, seeded: str
) -> None:
    table = get(qb, scratch, seeded)
    with pytest.raises(WriteError) as caught:
        await table.update(
            [
                {"Record ID#": 99999999, "Title": "Missing"},
                {"Record ID#": (await one(table, "C1")).id, "Title": "Written"},
            ]
        )
    result = caught.value.result
    assert [error.index for error in result.errors] == [0]
    assert len(result.updated_ids) == 1
    assert (await one(table, "C1", "Title"))["Title"] == "Written"
    assert await table.count() == 5


async def test_writing_a_number_as_decimal(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    await table.upsert([{"Code": "C1", "Price": Decimal("19.99")}], merge_on="Code")
    assert (await one(table, "C1", "Price"))["Price"] == 19.99


async def test_files(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    record_id = (await one(table, "C1")).id
    data = bytes(range(256))
    await table.update([{"Record ID#": record_id, "Attachment": FileUpload("a b é.bin", data)}])
    download = await table.download_file(record_id, "Attachment")
    assert (download.name, download.data) == ("a b é.bin", data)
    await table.update([{"Record ID#": record_id, "Attachment": FileUpload("b.txt", b"second")}])
    assert (await table.download_file(record_id, "Attachment")).data == b"second"
    assert (await table.download_file(record_id, "Attachment", version=1)).data == data
    value = (await one(table, "C1", "Attachment"))["Attachment"]
    assert [v["versionNumber"] for v in value["versions"]] == [1, 2]
    await table.delete_file_version(record_id, "Attachment", version=1)
    with pytest.raises(NotFoundError):
        await table.download_file(record_id, "Attachment", version=1)
    await table.delete_file_version(record_id, "Attachment", version=2)
    assert (await one(table, "C1", "Attachment"))["Attachment"]["versions"] == []


async def test_formulas(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    record_id = (await one(table, "C3")).id
    result = await table.run_formula("[Amount] + 1", record_id=record_id)
    assert isinstance(result, FormulaResult)
    assert (result.type, result.value) == ("number", Decimal(4))
    assert (await table.run_formula("1 > 2")).value is False
    assert (await table.run_formula('"text"')).value == "text"
    assert (await table.run_formula("Today()")).type == "date"


async def test_reports(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    reports = await table.reports(refresh=True)
    assert {report.name for report in reports} >= {"List All", "List Changes"}
    report = table.report("List All")
    assert (await report.info()).type == "table"
    assert (await table.report("1").info()).name == "List All"
    records = [r async for r in report.run(page_size=2)]
    assert len(records) == 5
    assert (await report.run_page(top=2, skip=1)).total == 5
    assert len([r async for r in report.run(limit=1)]) == 1
    with pytest.raises(UnknownReportError):
        await table.report("No Such Report").info()


async def test_schema_changes(qb: AsyncQuickbase, scratch: Any) -> None:
    table = qb.app(scratch.app_id).table((scratch.table(("A", "text"))).info().id)
    made = await table.create_field("Extra", "text", required=False, choices=None)
    assert made.label == "Extra"
    changed = await table.update_field("Extra", label="Renamed")
    assert changed.label == "Renamed"
    assert "Renamed" in {item.label for item in await table.fields()}
    formula = await table.create_field("F", "numeric", formula="1 + 1")
    assert (await table.update_field("F", formula="2 + 2")).formula == "2 + 2"
    await table.delete_fields(["Renamed", formula.id])
    assert not {"Renamed", "F"} & {item.label for item in await table.fields()}


async def test_relationships(qb: AsyncQuickbase, scratch: Any) -> None:
    parent = qb.app(scratch.app_id).table(scratch.table(("Name", "text")).info().id)
    child = qb.app(scratch.app_id).table(scratch.table(("Title", "text")).info().id)
    link = await child.create_relationship(
        parent, foreign_key_label="Parent", lookup_fields=["Name"]
    )
    assert link.parent_table_id == (await parent.info()).id
    assert link.child_table_id == (await child.info()).id
    assert link.foreign_key_field_id is not None
    assert len(link.lookup_field_ids) == 1
    assert [item.id for item in await child.relationships(refresh=True)] == [link.id]
    assert await parent.relationships(refresh=True) == []


async def test_drop_needs_the_exact_name(qb: AsyncQuickbase, scratch: Any) -> None:
    from pyquickbase import InvalidValueError

    made = scratch.table(("A", "text"))
    scratch.created.remove(made)
    table = qb.app(scratch.app_id).table(made.info().id)
    name = (await table.info()).name
    with pytest.raises(InvalidValueError):
        await table.drop(confirm=name + "x")
    await table.drop(confirm=name)
    with pytest.raises(UnknownTableError):
        await table.info()


async def test_read_only_and_allowed_writes(scratch: Any, seeded: str) -> None:
    async with AsyncQuickbase(realm=scratch.realm, token=scratch.token, read_only=True) as qb:
        table = get(qb, scratch, seeded)
        assert await table.count() == 5
        assert (await table.run_formula("1 + 1")).value == 2
        assert len([r async for r in table.report("List All").run()]) == 5
        for attempt in (
            lambda: table.insert([{"Title": "x"}]),
            lambda: table.delete("{3.GT.'0'}"),
            lambda: table.create_field("X", "text"),
            lambda: table.drop(confirm=""),
        ):
            try:
                await attempt()
            except ReadOnlyError:
                pass
            except pyquickbase.InvalidValueError:
                pass  # drop() checks the name first
            else:
                pytest.fail("A read-only client sent a write.")
    assert scratch.client.app(scratch.app_id).table(seeded).count() == 5
    writes = {("POST", "/records")}
    async with AsyncQuickbase(
        realm=scratch.realm, token=scratch.token, allowed_writes=writes
    ) as qb:
        table = get(qb, scratch, seeded)
        await table.insert([{"Title": "Allowed", "Code": "A1"}])
        with pytest.raises(ReadOnlyError):
            await table.delete(F("Code") == "A1")


async def test_custom_http_client_and_retry_settings(scratch: Any, seeded: str) -> None:
    seen: list[str] = []

    async def log(request: httpx.Request) -> None:
        seen.append(request.headers["user-agent"])

    async with httpx.AsyncClient(event_hooks={"request": [log]}, timeout=60) as http:
        async with AsyncQuickbase(
            realm=scratch.realm,
            token=scratch.token,
            http_client=http,
            retry=RetryPolicy(max_retries=1),
            user_agent="live-test/1",
            schema_ttl=0,
        ) as qb:
            assert await get(qb, scratch, seeded).count() == 5
    assert seen
    assert seen[0].startswith("live-test/1 pyquickbase/")


async def test_request_escape_hatch(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    body = await qb.request("GET", "/fields", params={"tableId": seeded})
    assert isinstance(body, list)
    with pytest.raises(pyquickbase.APIError) as caught:
        await qb.request("GET", "/fields", params={"tableId": "bqnosuchtable"})
    assert caught.value.status_code in (400, 401, 404)
    assert caught.value.request_id
    assert scratch.token not in str(caught.value)


async def test_a_bad_token_is_an_authentication_error(scratch: Any) -> None:
    async with AsyncQuickbase(realm=scratch.realm, token="fake-user-token") as qb:
        with pytest.raises(pyquickbase.QuickbaseError) as caught:
            await qb.app(scratch.app_id).tables()
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


async def test_generated_classes_read_and_write(
    qb: AsyncQuickbase, scratch: Any, seeded: str, tmp_path: Path
) -> None:
    Tasks = generated_class(scratch, seeded, tmp_path)
    tasks = qb.app(scratch.app_id).typed(Tasks)
    await tasks.check_schema()
    found = [
        task
        async for task in tasks.query(
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
    assert await tasks.count(Tasks.fields.code == "C1") == 1

    made = await tasks.insert(
        [Tasks.new(title="Typed", code="T1", amount=7, due_date=date(2027, 2, 1))]
    )
    new_id = made.created_ids[0]
    assert (await tasks.update([Tasks.change(new_id, title="Typed 2", amount=UNSET)])).updated_ids
    await tasks.update([Tasks.change(new_id, due_date=None)])
    assert await tasks.upsert([Tasks.new(code="T1", amount=8)], merge_on=Tasks.fields.code)
    rows = [t async for t in tasks.query(where=Tasks.fields.code == "T1")]
    assert len(rows) == 1
    row = rows[0]
    assert (row.title, row.amount, row.due_date) == ("Typed 2", 8, None)
    await tasks.update([Tasks.change(new_id, attachment=FileUpload("t.txt", b"typed"))])
    assert (await tasks.table.download_file(new_id, "Attachment")).data == b"typed"
    assert await tasks.delete(Tasks.fields.code == "T1") == 1
    with pytest.raises(TypeError):
        Tasks.new(doubled=1)


async def test_generated_classes_read_a_page_at_a_time(
    qb: AsyncQuickbase, scratch: Any, seeded: str, tmp_path: Path
) -> None:
    Tasks = generated_class(scratch, seeded, tmp_path)
    tasks = qb.app(scratch.app_id).typed(Tasks)
    where = Tasks.fields.amount > 1

    first = await tasks.query_page(where=where, order_by=Tasks.fields.amount.desc, top=2)
    assert all(isinstance(task, Tasks) for task in first.records)
    assert [task.code for task in first.records] == ["C5", "C4"]
    assert first.records[0].due_date == date(2027, 1, 5)
    assert (first.total, first.skip, first.has_more, first.next_skip) == (4, 0, True, 2)

    last = await tasks.query_page(
        where=where, order_by=Tasks.fields.amount.desc, top=2, skip=first.next_skip
    )
    assert [task.code for task in last.records] == ["C3", "C2"]
    assert (last.skip, last.has_more, last.next_skip) == (2, False, 4)

    # The untyped table takes the same fields, and sorts the same way.
    untyped = await tasks.table.query_page(where=where, order_by=Tasks.fields.amount, top=1)
    assert untyped.records[0]["Code"] == "C2"
    assert (await tasks.table.first(order_by=Tasks.fields.amount.desc))["Code"] == "C5"


async def test_check_schema_notices_what_first_use_doesnt(
    scratch: Any, qb: AsyncQuickbase, tmp_path: Path
) -> None:
    made = scratch.table(("Title", "text"), ("Gone", "text"), ("Changing", "text"))
    Tasks = generated_class(scratch, made.info().id, tmp_path)
    tasks = qb.app(scratch.app_id).typed(Tasks)
    assert await tasks.count() == 0
    made.delete_fields("Gone")
    assert await tasks.count() == 0  # Checked once already, so this isn't noticed.
    with pytest.raises(SchemaMismatchError, match=r"gone \(field \d+\) no longer exists"):
        await tasks.check_schema()
    with pytest.raises(SchemaMismatchError):
        await tasks.count()


async def test_setup_methods_create_once_and_read_back_exactly(
    qb: AsyncQuickbase, scratch: Any
) -> None:
    app = qb.app(scratch.app_id)
    parent_id = scratch.table(("Name", "text")).info().id
    child_id = scratch.table(("Hours", "numeric")).info().id
    parent, child = app.table(parent_id), app.table(child_id)

    assert (await app.ensure_table((await parent.info()).name)).__class__ is parent.__class__
    field = await child.ensure_field("Code", "text", unique=True, required=True)
    assert field.unique
    assert field.required
    assert (await child.ensure_field("code", "text", unique=True)).id == field.id
    with pytest.raises(SchemaMismatchError):
        await child.ensure_field("Code", "numeric")

    link = await child.ensure_relationship(parent, foreign_key_label="Parent")
    assert (await child.ensure_relationship(parent, foreign_key_label="Parent")).id == link.id
    foreign_key = next(f for f in await child.fields(refresh=True) if f.label == "Parent")
    assert foreign_key.is_foreign_key
    assert foreign_key.decimal_places == 0

    # Two in a row, because resending an existing lookup's ID creates an unrelated lookup.
    await parent.ensure_field("Owner", "text")
    name = await child.ensure_lookup_field(link, "Name", label="Parent Name")
    owner = await child.ensure_lookup_field(link, "Owner")
    assert name.label == "Parent Name"
    assert owner.mode == "lookup"
    assert (await child.ensure_lookup_field(link, "Name", label="Parent Name")).id == name.id
    lookups = [f for f in await child.fields(refresh=True) if f.mode == "lookup"]
    assert len(lookups) == 2

    summary = await child.ensure_summary_field(link, "Hours", "Hours Sum")
    assert summary.mode == "summary"
    assert summary.summary_function == "SUM"
    assert (await child.ensure_summary_field(link, "Hours", "Hours Sum")).id == summary.id
    total = await parent.ensure_field("Total", "numeric", formula="Nz([Hours Sum])")
    assert (await parent.ensure_field("Total", "numeric", formula="Nz([Hours Sum])")).id == total.id

    created = await parent.insert([{"Name": "P"}])
    record = await parent.get(created.created_ids[0], select=["Hours Sum", "Total"])
    assert record is not None
    assert record["Hours Sum"] is None
    assert record["Total"] == 0
    assert await parent.get(999999) is None
    assert (await parent.first(where=F("Name") == "P", select=["Name"])) is not None

    await child.insert([{"Code": "a", "Parent": created.created_ids[0], "Hours": 2}])
    with pytest.raises(WriteError) as caught:
        await child.insert([{"Code": "a"}])
    assert caught.value.result.errors[0].kind == "duplicate"
    assert caught.value.result.errors[0].field_id == field.id

    assert any(role.access for role in await app.roles())
