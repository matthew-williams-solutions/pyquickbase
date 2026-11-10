"""Records to and from CSV and Excel files, which do no network access."""

import csv
import io
import pickle
import zipfile
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from pyquickbase import (
    FileAttachment,
    FileRows,
    FileTooLargeError,
    FileVersion,
    InvalidValueError,
    Record,
    RecordPage,
    User,
)
from pyquickbase._models import FieldIndex, field_from_api
from pyquickbase._tabular import RecordWriter, file_cells, file_format, given_rows, read_rows
from pyquickbase._writes import build_rows

SPEC = (
    (1, "Date Created", "timestamp", None),
    (3, "Record ID#", "recordid", None),
    (6, "Title", "text", None),
    (7, "Hours", "numeric", None),
    (8, "Share", "percent", None),
    (9, "Took", "duration", None),
    (10, "Due", "date", None),
    (11, "Started", "timestamp", None),
    (12, "At", "timeofday", None),
    (13, "Done", "checkbox", None),
    (14, "Owner", "user", None),
    (15, "Team", "multiuser", None),
    (16, "Tags", "multitext", None),
    (17, "Double", "numeric", "formula"),
    (18, "Spec", "file", None),
    (19, "Cost", "currency", None),
)
FIELDS = [
    field_from_api({"id": number, "label": label, "fieldType": kind, "mode": mode})
    for number, label, kind, mode in SPEC
]


def read(text: str, **options: Any) -> FileRows:
    return read_rows(csv.reader(io.StringIO(text)), FIELDS, **options)


def one(column: str, value: str, **options: Any) -> Any:
    found = read(f'{column}\n"{value}"\n', **options)
    assert found.ok, found.failed_rows
    return found.rows[0][column]


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        ("Title", "  Pour slab  ", "Pour slab"),
        ("Title", "'=SUM(A1)", "=SUM(A1)"),
        ("Title", "'plain", "'plain"),
        ("Hours", "1,234.5", Decimal("1234.5")),
        ("Hours", "-0.5", Decimal("-0.5")),
        ("Hours", ".5", Decimal("0.5")),
        ("Cost", "1 200", Decimal("1200")),
        ("Share", "45%", Decimal("0.45")),
        ("Share", "0.45", Decimal("0.45")),
        ("Took", "3600000", 3_600_000),
        ("Due", "2027-01-15", date(2027, 1, 15)),
        ("Started", "2027-01-15T09:30:00+02:00", datetime(2027, 1, 15, 7, 30, tzinfo=UTC)),
        ("Started", "2027-01-15 09:30", datetime(2027, 1, 15, 9, 30, tzinfo=UTC)),
        ("At", "1:30 PM", "13:30:00"),
        ("At", "09:05", "09:05:00"),
        ("Done", "Yes", True),
        ("Done", "0", False),
        ("Owner", "ann@example.com", "ann@example.com"),
        ("Team", "ann@example.com; bob@example.com", ["ann@example.com", "bob@example.com"]),
        ("Tags", "a;b; ;c", ["a", "b", "c"]),
        ("Record ID#", "42", 42),
    ],
)
def test_each_type_is_read_from_text(column: str, value: str, expected: Any) -> None:
    assert one(column, value) == expected


def test_formats_and_time_zones_can_be_given() -> None:
    assert one("Due", "01/15/2027", date_format="%m/%d/%Y") == date(2027, 1, 15)
    tokyo = timezone(timedelta(hours=9))
    started = one("Started", "15/01/2027 09:30", datetime_format="%d/%m/%Y %H:%M", time_zone=tokyo)
    assert started == datetime(2027, 1, 15, 9, 30, tzinfo=tokyo)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("Hours", "lots", "isn't a number"),
        ("Hours", "1,23", "isn't a number"),
        ("Hours", "NaN", "isn't a number"),
        ("Share", "45 percent", "Write 45% or 0.45"),
        ("Took", "1.5", "whole milliseconds"),
        ("Due", "next week", "isn't a date"),
        ("Started", "soon", "isn't a date and time"),
        ("At", "noon", "isn't a time of day"),
        ("Done", "maybe", "isn't true or false"),
        ("Record ID#", "0", "isn't a Record ID#"),
    ],
)
def test_a_value_that_cant_be_read_is_an_error_with_its_row(
    column: str, value: str, message: str
) -> None:
    found = read(f'Title,{column}\nfine,s3cret\nsecond,"{value}"\n')
    errors = [error for error in found.failed_rows if error.row == 3]
    assert [(error.row, error.column) for error in errors] == [(3, column)]
    assert message in errors[0].message
    assert "s3cret" not in str(found.failed_rows)


def test_blank_cells_are_none_and_blank_rows_are_skipped() -> None:
    found = read("Title,Hours,\nA,,\n,,\n  ,  ,\nB,2,\n")
    assert found.rows == ({"Title": "A", "Hours": None}, {"Title": "B", "Hours": Decimal(2)})
    assert found.row_numbers == (2, 5)


def test_columns_map_and_skip() -> None:
    found = read("Task,Notes,7\nA,x,3\n", columns={"Task": "Title", "Notes": None, "7": 7})
    assert found.rows == ({"Title": "A", "Hours": Decimal(3)},)
    assert found.columns == {"Task": "Title", "7": "Hours"}
    assert found.skipped == ("Notes",)


@pytest.mark.parametrize(
    ("text", "options", "message"),
    [
        ("Titel\nA\n", {}, "did you mean 'Title'"),
        ("Title\nA\n", {"columns": {"Nope": "Title"}}, "doesn't have"),
        ("Title,Title\nA,B\n", {}, "more than once"),
        ("Title,,Hours\nA,,1\n", {}, "Column 2 has no name"),
        ("", {}, "no header row"),
        ("Title,Task\nA,B\n", {"columns": {"Task": "Title"}}, "same field"),
        ("Double\n1\n", {}, "formula field"),
        ("Spec\nx\n", {}, "file field"),
        ("Date Created\n2027-01-01\n", {}, "set by Quickbase"),
    ],
)
def test_a_file_that_cant_be_read_as_a_table_raises(
    text: str, options: dict[str, Any], message: str
) -> None:
    with pytest.raises(InvalidValueError, match=message):
        read(text, **options)


def test_read_only_columns_can_be_skipped() -> None:
    found = read("Record ID#,Title,Double,Date Created\n1,A,2,2027\n", skip_read_only=True)
    assert found.rows == ({"Record ID#": 1, "Title": "A"},)
    assert found.skipped == ("Double", "Date Created")


def test_a_row_with_more_cells_than_columns_is_an_error() -> None:
    found = read("Title\nA,extra\n")
    assert [(error.row, error.message) for error in found.failed_rows] == [
        (2, "has more cells than the header names")
    ]


def test_a_write_refuses_rows_with_errors_and_takes_rows_without() -> None:
    bad = read("Hours\n1\nx\ny\n")
    with pytest.raises(InvalidValueError, match="2 values that can't be written, so nothing was"):
        given_rows(bad)
    good = read("Title\nA\n")
    assert given_rows(good) == [{"Title": "A"}]
    assert list(good) == [{"Title": "A"}]
    assert len(good) == 1
    assert repr(bad) == "FileRows(rows=3, failed_rows=2)"


def test_errors_beyond_the_first_few_are_counted() -> None:
    bad = read("Hours\n" + "x\n" * 8)
    with pytest.raises(InvalidValueError, match="and 3 more"):
        bad.raise_for_failures()


def test_converted_rows_build_as_quickbase_needs() -> None:
    found = read('Hours,Share,Due,Done,Owner\n"1,234.5",45%,2027-01-15,yes,ann@example.com\n')
    [row] = build_rows(given_rows(found), FieldIndex(FIELDS))
    assert row == {
        # A Decimal is sent as its exact digits, which Quickbase reads as a number.
        "7": {"value": "1234.5"},
        "8": {"value": "0.45"},
        "10": {"value": "2027-01-15"},
        "13": {"value": True},
        "14": {"value": "ann@example.com"},
    }


# Files on disk


def test_a_csv_path_with_a_byte_order_mark(tmp_path: Path) -> None:
    path = tmp_path / "tasks.csv"
    path.write_text("Title,Hours\nÉté,2\n", encoding="utf-8-sig")
    cells = list(file_cells(path, "csv", sheet=None, encoding="utf-8-sig", delimiter=None))
    assert cells == [["Title", "Hours"], ["Été", "2"]]


def test_a_tab_separated_path(tmp_path: Path) -> None:
    path = tmp_path / "tasks.tsv"
    path.write_text("Title\tHours\nA, B\t2\n")
    cells = list(file_cells(path, "csv", sheet=None, encoding="utf-8", delimiter=None))
    assert cells[1] == ["A, B", "2"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [("a.xlsx", "xlsx"), ("a.XLSM", "xlsx"), ("a.csv", "csv"), ("a.txt", "csv")],
)
def test_the_format_comes_from_the_name(source: str, expected: str) -> None:
    assert file_format(source, None) == expected


def test_the_format_of_an_open_file_comes_from_its_mode() -> None:
    assert file_format(io.StringIO(), None) == "csv"
    assert file_format(io.BytesIO(), None) == "xlsx"
    assert file_format(io.BytesIO(), "csv") == "csv"


def test_old_excel_files_are_refused() -> None:
    with pytest.raises(InvalidValueError, match="old format"):
        file_format("a.xls", None)
    with pytest.raises(InvalidValueError, match="csv or xlsx"):
        file_format("a.csv", "ods")  # pyright: ignore[reportArgumentType]


def workbook(tmp_path: Path, rows: list[list[Any]], sheet: str = "Sheet1") -> Path:
    book = openpyxl.Workbook()
    page = book.active
    assert page is not None
    page.title = sheet
    for row in rows:
        page.append(row)
    path = tmp_path / "tasks.xlsx"
    book.save(path)
    return path


def test_excel_cells_keep_their_types(tmp_path: Path) -> None:
    path = workbook(
        tmp_path,
        [
            ["Title", "Hours", "Due", "Started", "At", "Done", "Took"],
            [5, 2.5, datetime(2027, 1, 15), datetime(2027, 1, 15, 9, 30), time(13, 30), True, 60],
        ],
    )
    cells = file_cells(path, "xlsx", sheet=None, encoding="utf-8", delimiter=None)
    found = read_rows(cells, FIELDS)
    assert found.ok, found.failed_rows
    assert found.rows[0] == {
        "Title": "5",
        "Hours": Decimal("2.5"),
        "Due": date(2027, 1, 15),
        "Started": datetime(2027, 1, 15, 9, 30, tzinfo=UTC),
        "At": "13:30:00",
        "Done": True,
        "Took": 60,
    }


def test_an_excel_date_with_a_time_is_refused_for_a_date_field(tmp_path: Path) -> None:
    path = workbook(tmp_path, [["Due"], [datetime(2027, 1, 15, 9, 30)]])
    found = read_rows(file_cells(path, "xlsx", sheet=None, encoding="", delimiter=None), FIELDS)
    assert "has a time of day" in found.failed_rows[0].message


def test_an_excel_sheet_is_chosen_by_name(tmp_path: Path) -> None:
    path = workbook(tmp_path, [["Title"], ["A"]], sheet="Tasks")
    cells = file_cells(path, "xlsx", sheet="Tasks", encoding="", delimiter=None)
    assert list(cells) == [("Title",), ("A",)]
    with pytest.raises(InvalidValueError, match="no sheet 'Nope'"):
        list(file_cells(path, "xlsx", sheet="Nope", encoding="", delimiter=None))
    with pytest.raises(InvalidValueError, match="only for Excel"):
        list(file_cells(path, "csv", sheet="Tasks", encoding="", delimiter=None))


# Writing

LABELS = {label: number for number, label, _, _ in SPEC}
ANN = User(id="1.a", name="Ann", email="ann@example.com")
FILE = FileAttachment(
    url="", versions=(FileVersion(number=1, file_name="plan.pdf", uploaded_at=None, creator=None),)
)
RECORD = Record(
    {
        3: 1,
        6: "=HYPERLINK(1)",
        7: 1.5,
        10: date(2027, 1, 15),
        11: datetime(2027, 1, 15, 9, 30, tzinfo=timezone(timedelta(hours=2))),
        13: True,
        14: ANN,
        15: [ANN, User(id="2.b", name="Bob")],
        16: ["a", "b"],
        18: FILE,
        19: None,
    },
    LABELS,
)
COLUMNS = (
    "Record ID#",
    "Title",
    "Hours",
    "Due",
    "Started",
    "Done",
    "Owner",
    "Team",
    "Tags",
    "Spec",
    "Cost",
)


def page(*records: Record) -> RecordPage:
    return RecordPage(records=records, total=len(records), skip=0, columns=COLUMNS)


def test_a_csv_holds_text_that_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    assert page(RECORD).to_csv(path) == 1
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # Excel needs it to read UTF-8.
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows == [
        list(COLUMNS),
        [
            "1",
            "'=HYPERLINK(1)",
            "1.5",
            "2027-01-15",
            "2027-01-15T09:30:00+02:00",
            "true",
            "ann@example.com",
            "ann@example.com; 2.b",
            "a; b",
            "plan.pdf",
            "",
        ],
    ]
    cells = file_cells(path, "csv", sheet=None, encoding="utf-8-sig", delimiter=None)
    back = read_rows(cells, FIELDS, skip_read_only=True)
    assert back.ok, back.failed_rows
    assert back.skipped == ("Spec",)
    assert back.rows[0]["Title"] == "=HYPERLINK(1)"
    assert back.rows[0]["Started"] == datetime(2027, 1, 15, 7, 30, tzinfo=UTC)
    assert back.rows[0]["Team"] == ["ann@example.com", "2.b"]


def test_a_csv_can_leave_formulas_as_they_are() -> None:
    out = io.StringIO()
    page(RECORD).to_csv(out, spreadsheet_safe=False)
    assert ",=HYPERLINK(1)," in out.getvalue()


def test_an_empty_page_still_has_its_header() -> None:
    out = io.StringIO()
    assert page().to_csv(out) == 0
    assert out.getvalue().splitlines() == [",".join(COLUMNS)]


def test_an_excel_file_keeps_types_and_never_holds_a_formula(tmp_path: Path) -> None:
    path = tmp_path / "out.xlsx"
    assert page(RECORD).to_excel(path, sheet="Tasks") == 1
    book = openpyxl.load_workbook(path)
    sheet = book["Tasks"]
    assert [cell.value for cell in sheet[1]] == list(COLUMNS)
    title = sheet["B2"]
    assert (title.value, title.data_type) == ("=HYPERLINK(1)", "s")
    assert sheet["D2"].value == datetime(2027, 1, 15)
    assert sheet["E2"].value == datetime(2027, 1, 15, 7, 30)  # In UTC.
    assert sheet["F2"].value is True
    assert sheet["H2"].value == "ann@example.com; 2.b"
    assert sheet.freeze_panes == "A2"
    cells = file_cells(path, "xlsx", sheet="Tasks", encoding="", delimiter=None)
    back = read_rows(cells, FIELDS, skip_read_only=True)
    assert back.ok, back.failed_rows
    assert back.rows[0]["Due"] == date(2027, 1, 15)
    assert back.rows[0]["Started"] == datetime(2027, 1, 15, 7, 30, tzinfo=UTC)


def test_excel_writes_to_an_open_binary_file() -> None:
    out = io.BytesIO()
    page(RECORD).to_excel(out)
    book = openpyxl.load_workbook(io.BytesIO(out.getvalue()))
    assert book.sheetnames == ["Records"]


# Writes that would lose data


@pytest.mark.parametrize("value", ["1,234.5", "45%", "lots"])
def test_text_a_number_field_would_lose_is_refused(value: str) -> None:
    with pytest.raises(InvalidValueError, match="leave the field empty"):
        build_rows([{"Hours": value}], FieldIndex(FIELDS))


@pytest.mark.parametrize("value", ["0.45", " 12 ", "-3", ".5", ""])
def test_plain_numbers_as_text_still_work(value: str) -> None:
    [row] = build_rows([{"Hours": value}], FieldIndex(FIELDS))
    assert row["7"]["value"] == value


@pytest.mark.parametrize(
    ("label", "value"), [("Owner", {"email": "ann@example.com"}), ("Team", [{"name": "Ann"}])]
)
def test_a_user_without_an_id_is_refused(label: str, value: Any) -> None:
    with pytest.raises(InvalidValueError, match="without an id"):
        build_rows([{label: value}], FieldIndex(FIELDS))


def bomb(size: int) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("big.bin", b"\0" * size)
    buffer.seek(0)
    return buffer


def test_a_workbook_that_unpacks_too_large_is_never_opened() -> None:
    with pytest.raises(FileTooLargeError) as caught:
        list(
            file_cells(
                bomb(1000),
                "xlsx",
                sheet=None,
                encoding="utf-8",
                delimiter=None,
                max_unzipped_bytes=999,
            )
        )
    assert (caught.value.limit, caught.value.unit) == (999, "bytes")


def test_checking_a_workbook_leaves_it_readable() -> None:
    book = openpyxl.Workbook()
    book.active.append(["Title"])  # type: ignore[union-attr]
    book.active.append(["A"])  # type: ignore[union-attr]
    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    rows = list(file_cells(buffer, "xlsx", sheet=None, encoding="utf-8", delimiter=None))
    assert [list(row) for row in rows] == [["Title"], ["A"]]


def test_blank_rows_dont_count_towards_max_rows() -> None:
    fields = [field_from_api({"id": 6, "label": "Title", "fieldType": "text"})]
    found = read_rows(iter([["Title"], ["A"], [""], [None], ["B"]]), fields, max_rows=2)
    assert len(found) == 2


def test_file_too_large_error_survives_pickling() -> None:
    error = pickle.loads(pickle.dumps(FileTooLargeError(5, "rows")))
    assert (error.limit, error.unit) == (5, "rows")
    assert isinstance(error, InvalidValueError)


def test_a_failed_export_closes_the_file_it_opened(tmp_path: Path) -> None:
    writer = RecordWriter(tmp_path / "a.csv", "csv", spreadsheet_safe=True)
    with pytest.raises(RuntimeError), writer.open():
        raise RuntimeError
    assert writer._handle.closed


def test_an_export_leaves_a_file_it_was_given_open() -> None:
    given = io.StringIO()
    writer = RecordWriter(given, "csv", spreadsheet_safe=True)
    with writer.open():
        pass
    assert not given.closed


@pytest.mark.anyio
async def test_the_async_client_parses_on_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    import httpx

    from pyquickbase import AsyncQuickbase

    used: list[threading.Thread] = []
    real = read_rows

    def spy(*args: Any, **kwargs: Any) -> Any:
        used.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr("pyquickbase._async._resources.read_rows", spy)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tables"):
            return httpx.Response(200, json=[{"id": "bqt", "name": "T", "keyFieldId": 3}])
        return httpx.Response(200, json=[{"id": 6, "label": "Title", "fieldType": "text"}])

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncQuickbase(realm="acme", token="t", http_client=http) as qb:
        rows = await qb.app("bq1234567").table("T").rows_from_file(io.StringIO("Title\nA\n"))
    assert len(rows) == 1
    assert used[0] is not threading.current_thread()


TYPED_CELLS = [
    ("Title", 5.0, "5"),
    ("Title", date(2027, 1, 15), "2027-01-15"),
    ("Due", date(2027, 1, 15), date(2027, 1, 15)),
    ("Took", timedelta(minutes=1), timedelta(minutes=1)),
]


@pytest.mark.parametrize(("column", "cell", "expected"), TYPED_CELLS)
def test_a_cell_excel_already_typed_is_read_as_that_type(
    column: str, cell: Any, expected: Any
) -> None:
    found = read_rows(iter([[column], [cell]]), FIELDS)
    assert found.ok, found.failed_rows
    assert found.rows[0][column] == expected


def test_a_date_cell_in_a_timestamp_field_is_midnight_in_its_zone() -> None:
    tokyo = timezone(timedelta(hours=9))
    found = read_rows(iter([["Started"], [date(2027, 1, 15)]]), FIELDS, time_zone=tokyo)
    assert found.rows[0]["Started"] == datetime(2027, 1, 15, tzinfo=tokyo)


@pytest.mark.parametrize(
    ("cell", "message"),
    [(True, "isn't a number"), (float("inf"), "isn't a finite number")],
    ids=["checkbox-value", "infinity"],
)
def test_a_cell_that_isnt_a_usable_number_is_a_row_error(cell: Any, message: str) -> None:
    found = read_rows(iter([["Hours"], [cell]]), FIELDS)
    assert message in found.failed_rows[0].message


def test_a_column_of_a_type_that_cant_be_written_is_refused() -> None:
    address = field_from_api({"id": 40, "label": "Where", "fieldType": "address"})
    with pytest.raises(
        InvalidValueError, match="address field, which can't be written from a file"
    ):
        read_rows(iter([["Where"], ["x"]]), [address])


def test_a_delimiter_can_be_given(tmp_path: Path) -> None:
    source = tmp_path / "rows.csv"
    source.write_text("Title;Hours\nA;1\n")
    with source.open(newline="") as handle:
        found = read_rows(
            file_cells(handle, "csv", sheet=None, encoding="utf-8", delimiter=";"), FIELDS
        )
    assert found.ok
    assert found.rows[0]["Title"] == "A"
