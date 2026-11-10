"""Every public method of the client against a real app, on temporary tables."""

import os
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
import pytest

import pyquickbase
from pyquickbase import (
    AppInfo,
    AsyncQuickbase,
    F,
    FileUpload,
    FormulaResult,
    InvalidQueryError,
    NotFoundError,
    Period,
    Policy,
    PolicyRefusedError,
    Record,
    RecordPage,
    RetryPolicy,
    SchemaMismatchError,
    SummaryTooLargeError,
    TableInfo,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    UnsupportedReportError,
    WriteError,
    WriteRequest,
    allow,
    fields,
    parse_where,
    tables,
    where_from_dict,
    where_schema,
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
    qb.clear_metadata_cache()
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


MEASURES = [("Code", "text"), ("Done", "percent"), ("Budget", "duration")]
MEASURE_ROWS = [
    {"Code": "M1", "Done": 0.25, "Budget": 1_800_000},
    {"Code": "M2", "Done": 0.5, "Budget": 28_800_000},
    {"Code": "M3", "Done": 1, "Budget": 28_800_001},
    {"Code": "M4", "Done": None, "Budget": None},
]


@pytest.fixture(scope="module")
def measures(scratch: Any) -> str:
    table = scratch.table(*MEASURES)
    scratch.fill(table, MEASURE_ROWS)
    return table.info().id


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        # A percent compares as a fraction, as it's written, though Quickbase's queries use whole
        # percent. A duration compares as milliseconds, which Quickbase reads as nonsense bare.
        (F("Done") >= 0.5, ["M2", "M3"]),
        (F("Done") == 0.25, ["M1"]),
        (F("Done") == 1, ["M3"]),
        (F("Done").is_in([0.25, 1]), ["M1", "M3"]),
        (F("Done") == None, ["M4"]),  # noqa: E711
        (F("Budget") > 28_800_000, ["M3"]),
        (F("Budget") >= timedelta(hours=8), ["M2", "M3"]),
        (F("Budget") < timedelta(hours=1), ["M1"]),
        (F("Budget") == None, ["M4"]),  # noqa: E711
        # Text is sent as written.
        (F("Budget") > "7 hours", ["M2", "M3"]),
        (F("Done") >= "50", ["M2", "M3"]),
        (where_from_dict({"field": "Done", "op": ">", "value": 0.3}), ["M2", "M3"]),
        (where_from_dict({"field": "Budget", "op": "<=", "value": 28_800_000}), ["M1", "M2"]),
    ],
)
async def test_percent_and_duration_filters_compare_as_they_are_written(
    qb: AsyncQuickbase, scratch: Any, measures: str, where: Any, expected: list[str]
) -> None:
    table = get(qb, scratch, measures)
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
    with pytest.raises(InvalidQueryError):
        await table.count(F("Title") == value)
    with pytest.raises(InvalidQueryError):
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
        with pytest.raises(InvalidQueryError):
            await table.count(where)
    assert [c.field_id for c in parse_where("{7.EX.'C1'}", await table.fields())] == [7]


async def test_paging_and_sorting(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    descending = [
        r["Code"] async for r in table.query(select=["Code"], order_by="-Amount", page_size=2)
    ]
    assert descending == ["C5", "C4", "C3", "C2", "C1"]
    assert len([r async for r in table.query(max_total=3, page_size=2)]) == 3
    page = await table.query_page(select=["Code"], order_by="Code", limit=2, skip=2)
    assert isinstance(page, RecordPage)
    assert (page.total, page.skip, page.next_skip, page.has_more) == (5, 2, 4, True)
    assert [r["Code"] for r in page.records] == ["C3", "C4"]
    last = await table.query_page(limit=2, skip=4)
    assert not last.has_more
    assert (await table.query_page(limit=2, skip=100)).records == ()


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
    assert [error.row_index for error in result.failed_rows] == [0]
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
    assert (download.file_name, download.data) == ("a b é.bin", data)
    await table.update([{"Record ID#": record_id, "Attachment": FileUpload("b.txt", b"second")}])
    assert (await table.download_file(record_id, "Attachment")).data == b"second"
    assert (await table.download_file(record_id, "Attachment", version=1)).data == data
    value = (await one(table, "C1", "Attachment"))["Attachment"]
    assert [v.number for v in value.versions] == [1, 2]
    await table.delete_file_version(record_id, "Attachment", version=1)
    with pytest.raises(NotFoundError):
        await table.download_file(record_id, "Attachment", version=1)
    await table.delete_file_version(record_id, "Attachment", version=2)
    assert (await one(table, "C1", "Attachment"))["Attachment"] is None


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
    assert (await report.run_page(limit=2, skip=1)).total == 5
    assert len([r async for r in report.run(max_total=1)]) == 1
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
    link = await child.create_relationship(parent, foreign_key="Parent", lookup_fields=["Name"])
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


async def test_read_only_and_a_policy_of_exact_requests(scratch: Any, seeded: str) -> None:
    async with AsyncQuickbase(
        realm=scratch.realm, token=scratch.token, policy=Policy.read_only()
    ) as qb:
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
            except PolicyRefusedError:
                pass
            except pyquickbase.InvalidValueError:
                pass  # drop() checks the name first
            else:
                pytest.fail("A read-only client sent a write.")
    assert scratch.client.app(scratch.app_id).table(seeded).count() == 5

    records_only = Policy(
        rules=[allow.read(tables.all()), allow.insert(seeded, fields.all())]
    ).bind(scratch.client.app(scratch.app_id))
    async with AsyncQuickbase(realm=scratch.realm, token=scratch.token, policy=records_only) as qb:
        table = get(qb, scratch, seeded)
        await table.insert([{"Title": "Allowed", "Code": "A1"}])
        with pytest.raises(PolicyRefusedError):
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
            metadata_cache_seconds=0,
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

    link = await child.ensure_relationship(parent, foreign_key="Parent")
    assert (await child.ensure_relationship(parent, foreign_key="Parent")).id == link.id
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
    assert caught.value.result.failed_rows[0].reason == "duplicate"
    assert caught.value.result.failed_rows[0].field_id == field.id

    assert any(role.access for role in await app.roles())


async def _filter_codes(table: Any, data: Any, **kwargs: Any) -> set[str]:
    where = where_from_dict(data, **kwargs)
    return {r["Code"] async for r in table.query(where=where, select=["Code"])}


def _cond(field: Any, op: str, value: Any) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


async def test_filters_from_data_match_the_records_they_say(
    qb: AsyncQuickbase, scratch: Any, seeded: str
) -> None:
    table = get(qb, scratch, seeded)
    await table.update(
        [
            {"Record ID#": r.id, "Done": True, "Started": datetime(2027, 3, 4, 5, 6, 7, tzinfo=UTC)}
            async for r in table.query(where=F("Code").is_in(["C1", "C2"]), select=["Code"])
        ]
    )
    await table.insert([{"Title": "Blank", "Code": "C6"}])
    cases: list[tuple[Any, set[str]]] = [
        (_cond("Code", "==", "C1"), {"C1"}),
        (_cond("Code", "!=", "C1"), {"C2", "C3", "C4", "C5", "C6"}),
        (_cond("Code", "in", ["C1", "C3"]), {"C1", "C3"}),
        (_cond("Code", "not_in", ["C1", "C3"]), {"C2", "C4", "C5", "C6"}),
        (_cond("Title", "contains", "Row"), {"C1", "C2", "C3", "C4", "C5"}),
        (_cond("Title", "not_contains", "Row"), {"C6"}),
        (_cond("Title", "starts_with", "Bl"), {"C6"}),
        (_cond("Amount", ">", 3), {"C4", "C5"}),
        (_cond("Amount", "<=", "2"), {"C1", "C2"}),
        (_cond("Amount", "==", None), {"C6"}),
        (_cond("Amount", "!=", None), {"C1", "C2", "C3", "C4", "C5"}),
        (_cond("Amount", "in", [1, None]), {"C1", "C6"}),
        (_cond("Due Date", "<", "2027-01-02"), {"C1"}),
        (_cond("Due Date", "<=", "2027-01-02"), {"C1", "C2"}),
        (_cond("Due Date", ">", "2027-01-04"), {"C5"}),
        (_cond("Due Date", ">=", "2027-01-04"), {"C4", "C5"}),
        (_cond("Due Date", "==", "2027-01-03"), {"C3"}),
        (_cond("Due Date", "==", None), {"C6"}),
        (_cond("Done", "==", True), {"C1", "C2"}),
        (_cond("Done", "!=", True), {"C3", "C4", "C5", "C6"}),
        (_cond("Started", "<", "2027-03-04T05:06:08Z"), {"C1", "C2"}),
        (_cond("Started", "<", "2027-03-04T05:06:07Z"), set()),
        (_cond("Started", ">=", "2027-03-04T00:06:07-05:00"), {"C1", "C2"}),
        (_cond("Started", ">", "2027-03-04T05:06:07Z"), set()),
        (_cond("Started", "<", "2027-03-01"), set()),
        (_cond("Started", ">", "2027-03-01"), {"C1", "C2"}),
        (_cond("Started", "==", None), {"C3", "C4", "C5", "C6"}),
        (_cond(4, "!=", None), {"C1", "C2", "C3", "C4", "C5", "C6"}),
        (_cond(4, "==", None), set()),
        (_cond("Record ID#", ">", 0), {"C1", "C2", "C3", "C4", "C5", "C6"}),
        (
            {
                "all": [
                    _cond("Done", "==", True),
                    {"any": [_cond("Code", "==", "C1"), _cond("Amount", ">", 4)]},
                ]
            },
            {"C1"},
        ),
    ]
    for data, expected in cases:
        assert await _filter_codes(table, data) == expected, data


async def test_filters_from_data_delete_only_what_they_match(
    qb: AsyncQuickbase, scratch: Any, seeded: str
) -> None:
    table = get(qb, scratch, seeded)
    data = {"all": [_cond("Amount", ">=", 2), _cond("Amount", "<", 4)]}
    assert await table.delete(where_from_dict(data)) == 2
    assert await _filter_codes(table, _cond("Record ID#", ">", 0)) == {"C1", "C4", "C5"}


async def test_the_filter_schema_offers_the_tables_fields(
    qb: AsyncQuickbase, scratch: Any, seeded: str
) -> None:
    table = get(qb, scratch, seeded)
    schema = where_schema(fields=await table.fields(), max_depth=0)
    assert "Due Date" in schema["properties"]["field"]["enum"]


async def test_write_rules_limit_tables_actions_and_fields(
    scratch: Any, table_id: str, seeded: str
) -> None:
    other = scratch.table(("Title", "text"))
    other_id = other.info().id
    other.insert([{"Title": "Untouched"}])
    status_field = scratch.client.app(scratch.app_id).table(table_id).resolve_field_ids(["Title"])
    rules = Policy(
        rules=[
            allow.read(tables.all(), max_paging_depth=None),
            allow.insert(table_id, fields.all()),
            allow.update(table_id, status_field),
        ]
    ).bind(scratch.client.app(scratch.app_id))
    async with AsyncQuickbase(realm=scratch.realm, token=scratch.token, policy=rules) as limited:
        table = limited.app(scratch.app_id).table(table_id)
        record = (await one(table, "C1", "Code")).id

        await table.insert([{"Title": "New", "Code": "N1"}])
        await table.update([{"Record ID#": record, "Title": "Changed"}])
        assert (await one(table, "C1", "Title"))["Title"] == "Changed"

        with pytest.raises(PolicyRefusedError, match="updating Code"):
            await table.update([{"Record ID#": record, "Code": "Nope"}])
        with pytest.raises(PolicyRefusedError, match="deleting records from"):
            await table.delete(F("Code") == "C2")
        with pytest.raises(PolicyRefusedError, match="adding a field to"):
            await table.create_field("Nope", "text")
        with pytest.raises(PolicyRefusedError, match="No rule allows it"):
            await limited.app(scratch.app_id).table(other_id).insert([{"Title": "Nope"}])
        await limited.request(
            "POST", "/records/query", json={"from": other_id, "select": [3], "sortBy": []}
        )  # Every table may be read.

    plain = scratch.client.app(scratch.app_id)
    assert plain.table(table_id).count(F("Code") == "C2") == 1
    assert [dict(r)["Title"] for r in plain.table(other_id).query(select=["Title"])] == [
        "Untouched"
    ]
    assert plain.table(table_id).count(F("Code") == "Nope") == 0


async def test_a_custom_policy_runs_on_real_writes(
    scratch: Any, table_id: str, seeded: str
) -> None:
    seen: list[str] = []

    class RecordActions:
        def decide(self, request: Any) -> None:
            if isinstance(request, WriteRequest):
                seen.append(request.action)

    policy = Policy(
        rules=[
            allow.read(table_id, max_paging_depth=None),
            allow.insert(table_id, fields.all()),
            allow.update(table_id, fields.all()),
            RecordActions(),
        ]
    ).bind(scratch.client.app(scratch.app_id))
    async with AsyncQuickbase(realm=scratch.realm, token=scratch.token, policy=policy) as limited:
        table = limited.app(scratch.app_id).table(table_id)
        await table.upsert([{"Code": "C1", "Title": "Upserted"}], merge_on="Code")
        record = (await one(table, "C1", "Code")).id
        await table.update([{"Record ID#": record, "Title": "Updated"}])
        await table.insert([{"Code": "C9", "Title": "Inserted"}])
    assert seen == ["upsert", "update", "insert"]


async def test_previews_match_what_the_write_then_does(
    qb: AsyncQuickbase, scratch: Any, seeded: str
) -> None:
    table = get(qb, scratch, seeded)
    stamp = datetime(2027, 3, 4, 5, 6, 7, tzinfo=UTC)
    first = (await one(table, "C1", "Code")).id
    second = (await one(table, "C2", "Code")).id
    third = (await one(table, "C3", "Code")).id
    await table.update(
        [
            {
                "Record ID#": first,
                "Started": stamp,
                "Price": Decimal("10.50"),
                "Done": True,
                "Tags": ["a", "b"],
            }
        ]
    )
    # Every value here is what the record already holds, written the way a caller might.
    same = {
        "Record ID#": first,
        "Title": "Row 1",
        "Due Date": date(2027, 1, 1),
        "Amount": "1",
        "Started": stamp,
        "Price": Decimal("10.5"),
        "Done": True,
        "Tags": ["b", "a"],
    }
    rows = [
        same,
        {"Record ID#": second, "Title": "Changed", "Amount": None, "Due Date": date(2028, 2, 2)},
        {"Record ID#": third, "Done": False},
        {"Record ID#": 99999999, "Title": "Gone"},
    ]
    before = {r.id: dict(r) async for r in table.query(select=["Title", "Amount"])}
    preview = await table.preview_update(rows)
    assert {r.id: dict(r) async for r in table.query(select=["Title", "Amount"])} == before
    assert preview.missing_ids == (99999999,)
    assert set(preview.unchanged_ids) == {first, third}
    assert [change.record_id for change in preview.changes] == [second]
    fields = preview.changes[0].fields
    assert fields["Title"].before == "Row 2"
    assert fields["Title"].after == "Changed"
    assert fields["Amount"].before == 2
    assert fields["Amount"].after is None
    assert fields["Due Date"].before == date(2027, 1, 2)

    with pytest.raises(WriteError) as caught:
        await table.update(rows)
    result = caught.value.result
    assert set(result.updated_ids) == {second}
    assert set(result.unchanged_ids) == {first, third}


async def test_a_delete_preview_matches_the_delete(
    qb: AsyncQuickbase, scratch: Any, seeded: str
) -> None:
    table = get(qb, scratch, seeded)
    where = where_from_dict({"field": "Amount", "op": ">=", "value": 4})
    page = await table.preview_delete(where, select=["Code"], limit=1)
    assert page.total == 2
    assert len(page.records) == 1
    assert await table.count() == 5
    assert await table.delete(where) == page.total


async def test_a_file_preview_shows_only_names(
    qb: AsyncQuickbase, scratch: Any, seeded: str
) -> None:
    table = get(qb, scratch, seeded)
    record = (await one(table, "C1", "Code")).id
    await table.update([{"Record ID#": record, "Attachment": FileUpload("first.txt", b"one")}])
    await table.update([{"Record ID#": record, "Attachment": FileUpload("second.txt", b"two")}])
    preview = await table.preview_update(
        [{"Record ID#": record, "Attachment": FileUpload("third.txt", b"three")}]
    )
    change = preview.changes[0].fields["Attachment"]
    assert (change.before, change.after) == ("second.txt", "third.txt")


async def test_summarize_matches_the_records(qb: AsyncQuickbase, scratch: Any, seeded: str) -> None:
    table = get(qb, scratch, seeded)
    await table.insert([{"Title": "Row 6", "Code": "C6", "Amount": 10}, {"Code": "C7"}])
    await table.update(
        [
            {"Record ID#": r.id, "Done": True, "Started": datetime(2027, 3, 4, 2, tzinfo=UTC)}
            async for r in table.query(where=F("Code").is_in(["C1", "C2"]), select=["Code"])
        ]
    )
    overall = await table.summarize(
        sum="Amount", avg="Amount", min=["Amount", "Due Date"], max="Started"
    )
    assert len(overall) == 1
    assert overall[0].count == 7
    assert overall[0].sum == {"Amount": 25}
    assert overall[0].avg == {"Amount": 25 / 6}
    assert overall[0].min == {"Amount": 1, "Due Date": date(2027, 1, 1)}
    assert overall[0].max == {"Started": datetime(2027, 3, 4, 2, tzinfo=UTC)}

    by_done = await table.summarize("Done", sum="Amount")
    assert [(row.group["Done"], row.count, row.sum["Amount"]) for row in by_done] == [
        (False, 5, 22),
        (True, 2, 3),
    ]

    by_month = await table.summarize(Period("Due Date", "month"), where=F("Amount") >= 1)
    assert [(row.group["Due Date"], row.count) for row in by_month] == [("2027-01", 5), (None, 1)]

    west = timezone(timedelta(hours=-5))
    by_day = await table.summarize(
        Period("Started", "day", time_zone=west),
        where=F("Done") == True,  # noqa: E712
    )
    assert [row.group["Started"] for row in by_day] == ["2027-03-03"]

    filtered = await table.summarize(
        where=where_from_dict({"field": "Amount", "op": ">", "value": 3}), sum="Amount"
    )
    assert (filtered[0].count, filtered[0].sum) == (3, {"Amount": 19})

    # A count reads no records, so max_records_scanned doesn't limit it. Anything else it does.
    assert (await table.summarize(max_records_scanned=1))[0].count == 7
    with pytest.raises(SummaryTooLargeError) as caught:
        await table.summarize(sum="Amount", max_records_scanned=6)
    assert (caught.value.matched, caught.value.limit) == (7, 6)
    assert (await table.summarize(sum="Amount", max_records_scanned=7))[0].count == 7


async def test_summary_and_chart_reports_read_as_rows(qb: AsyncQuickbase, scratch: Any) -> None:
    app = qb.app(scratch.app_id)
    names = {item.name for item in await app.tables(refresh=True)}
    if "Sample Tasks" not in names:
        pytest.skip("This needs the Sample Tasks table and its reports. See CONTRIBUTING.md.")
    table = app.table("Sample Tasks")
    reports = {item.name for item in await table.reports(refresh=True)}
    if not {"API Summary Test", "API Chart Test"} <= reports:
        pytest.skip("This needs the reports described in CONTRIBUTING.md.")

    summary = await table.report("API Summary Test").run_summary()
    assert summary == [
        {"Task Name": "Book room", "Field Type Sample (tot)": 1, "Field Type Sample (avg)": 1},
        {"Task Name": "Prepare agenda", "Field Type Sample (tot)": 1, "Field Type Sample (avg)": 1},
    ]
    chart = await table.report("API Chart Test").run_summary()
    assert chart == [
        {"Task Name": "Book room", "Field Type Sample (tot)": 1},
        {"Task Name": "Prepare agenda", "Field Type Sample (tot)": 1},
    ]
    assert (
        await table.report("API Summary Test").run_summary(page_size=1, max_total=1) == summary[:1]
    )
    assert await table.report("API Chart Test").run_summary(page_size=1) == chart
    with pytest.raises(UnsupportedReportError, match="run_summary"):
        await table.report("API Summary Test").run_page()
