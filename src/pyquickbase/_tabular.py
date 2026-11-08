"""Records to and from CSV and Excel files. No network access.

A file holds text, or Excel's few types, and Quickbase silently empties a number field given text it
can't read as a number. So each value is converted here, by its field's type, and a value that can't
be converted is reported with its row and column, without writing anything. Errors never repeat the
value, which may be sensitive.

CSV needs nothing beyond the standard library. Excel needs `openpyxl`, from `pyquickbase[excel]`.
"""

import csv
import difflib
import io
import os
import re
from collections.abc import Generator, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import IO, Any, Literal, TypeAlias, cast

from ._frames import rows_or_frame
from ._models import Field, FileAttachment, Record, User
from ._optional import require_library
from .errors import InvalidValueError

FileFormat = Literal["csv", "xlsx"]
"""A file's format: `csv`, which includes tab-separated files, or `xlsx`, Excel's."""

FileLike: TypeAlias = str | os.PathLike[str] | IO[str] | IO[bytes]
"""A path, or an open file: text for CSV, and binary for Excel."""

LIST_SEPARATOR = ";"
"""What separates the values of a multi-select or multi-user field in a cell."""

# A cell starting with one of these is a formula to a spreadsheet, which could run it when the file
# is opened. Such text is written with a leading apostrophe, which spreadsheets show without it.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
_TEXT_TYPES = frozenset(
    {"text", "text-multiple-choice", "text-multi-line", "rich-text", "email", "url", "phone"}
)
_NUMBER_TYPES = frozenset({"numeric", "currency", "percent", "rating"})
_WRITABLE_TYPES = _TEXT_TYPES | _NUMBER_TYPES | {
    "duration", "date", "timestamp", "timeofday", "checkbox", "user", "multiuser", "multitext",
    "recordid",
}  # fmt: skip
# Date Created, Date Modified, and Last Modified By, which Quickbase sets itself.
_SYSTEM_FIELDS = frozenset({1, 2, 5})
_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+")
_TRUE = frozenset({"true", "yes", "y", "1", "checked", "x"})
_FALSE = frozenset({"false", "no", "n", "0", "unchecked"})
_CLOCK_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p")
_EXCEL_SUFFIXES = frozenset({".xlsx", ".xlsm"})
_TAB_SUFFIXES = frozenset({".tsv", ".tab"})
# How many errors a refusal lists before it only counts the rest.
_ERRORS_SHOWN = 5


@dataclass(frozen=True, slots=True, kw_only=True)
class FileRowError:
    """A value in a file that can't be written to its field.

    Attributes:
        row: The row's number as a spreadsheet shows it, counting the header as row 1.
        column: The file's column.
        message: What's wrong, which never repeats the value.
    """

    row: int
    column: str
    message: str

    def __str__(self) -> str:
        return f"Row {self.row}, column {self.column!r}: {self.message}"


@dataclass(frozen=True, slots=True, kw_only=True)
class FileRows:
    """A file's rows, converted for a table's fields, from `Table.rows_from_file()`.

    Pass it to `insert()`, `update()`, `upsert()`, or `preview_update()`, which refuse it, writing
    nothing, while it has errors. Iterating it gives its rows.

    Attributes:
        rows: Each row that has a value, as `{field label: value}`. A blank cell is None, which
            leaves a field empty in `insert()`, and clears it in `update()` and `upsert()`.
        row_numbers: Each row's number in the file, as for `FileRowError.row`, so `rows[0]` came
            from row `row_numbers[0]`. A row whose cells are all blank is left out.
        columns: Which field each column was read into, as `{column: field label}`.
        skipped: The columns that weren't read.
        errors: Every value that can't be written. Fix these in the file, or map or skip their
            columns.

    `repr` shows only its size, because values may be sensitive and reprs end up in logs.
    """

    rows: tuple[Mapping[str, Any], ...] = ()
    row_numbers: tuple[int, ...] = ()
    columns: Mapping[str, str] = field(default_factory=dict[str, str])
    skipped: tuple[str, ...] = ()
    errors: tuple[FileRowError, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every value can be written."""
        return not self.errors

    def raise_for_errors(self) -> None:
        """Raise `InvalidValueError` listing the first errors, if there are any.

        Raises:
            InvalidValueError: The file has values that can't be written.
        """
        if not self.errors:
            return
        shown = "; ".join(str(error) for error in self.errors[:_ERRORS_SHOWN])
        more = len(self.errors) - _ERRORS_SHOWN
        extra = f"; and {more} more" if more > 0 else ""
        raise InvalidValueError(
            f"The file has {len(self.errors)} values that can't be written, so nothing was: "
            f"{shown}{extra}. See FileRows.errors for all of them."
        )

    def __iter__(self) -> Iterator[Mapping[str, Any]]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:
        return f"FileRows(rows={len(self.rows)}, errors={len(self.errors)})"


def given_rows(records: Any) -> list[Mapping[str | int, Any]]:
    """The rows a write was given, from dictionaries, a DataFrame, or a file's rows."""
    if isinstance(records, FileRows):
        records.raise_for_errors()
        return list(cast(Iterable[Mapping[str | int, Any]], records.rows))
    return list(rows_or_frame(records))


# Reading


class _CellError(Exception):
    """A value that can't be converted, with why."""


@dataclass(frozen=True, slots=True)
class _Reading:
    """How to read dates and timestamps that aren't ISO 8601, and naive timestamps."""

    tz: tzinfo
    date_format: str | None
    datetime_format: str | None


def _text(value: Any) -> str:
    """A cell as text. Excel holds whole numbers in text columns as floats, such as 5.0."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return str(value).strip() if isinstance(value, str) else str(value)


def _unescape(text: str) -> str:
    """Text a spreadsheet-safe export wrote with a leading apostrophe, as it was."""
    if len(text) > 1 and text[0] == "'" and text[1] in "".join(_FORMULA_STARTS):
        return text[1:]
    return text


# A plain number field, for reading values such as durations and Record ID#s.
_PLAIN_NUMBER = Field(
    id=0, label="", type="numeric", mode=None, required=False, unique=False,
    appears_by_default=False, choices=(), allow_new_choices=False, raw={},
)  # fmt: skip


def _decimal(value: Any, item: Field) -> Decimal:
    if isinstance(value, bool):
        raise _CellError("isn't a number")
    if isinstance(value, int | float):
        found = Decimal(str(value))
    else:
        text = _text(value).replace(" ", "")
        percent = item.type == "percent" and text.endswith("%")
        if percent:
            text = text[:-1]
        if not _NUMBER.fullmatch(text):
            hint = " Write 45% or 0.45." if item.type == "percent" else ""
            raise _CellError(f"isn't a number, such as 1234.5 or 1,234.5.{hint}")
        found = Decimal(text.replace(",", ""))
        if percent:
            found /= 100
    if not found.is_finite():
        raise _CellError("isn't a finite number")
    return found


def _duration(value: Any) -> int | timedelta:
    if isinstance(value, timedelta):
        return value
    number = _decimal(value, _PLAIN_NUMBER)
    if number != number.to_integral_value():
        raise _CellError("is a duration, which takes whole milliseconds")
    return int(number)


def _date(value: Any, reading: _Reading) -> date:
    if isinstance(value, datetime):
        if value.time() != time():
            raise _CellError("has a time of day, but the field only holds a date")
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    try:
        if reading.date_format:
            return datetime.strptime(text, reading.date_format).date()
        return date.fromisoformat(text)
    except ValueError:
        shown = reading.date_format or "YYYY-MM-DD"
        raise _CellError(
            f"isn't a date in the form {shown}. Set date_format for another."
        ) from None


def _timestamp(value: Any, reading: _Reading) -> datetime:
    if isinstance(value, datetime):
        found = value
    elif isinstance(value, date):
        found = datetime.combine(value, time())
    else:
        text = _text(value)
        try:
            if reading.datetime_format:
                found = datetime.strptime(text, reading.datetime_format)
            else:
                found = datetime.fromisoformat(text)
        except ValueError:
            shown = reading.datetime_format or "ISO 8601, such as 2027-01-15T09:30:00+00:00"
            raise _CellError(
                f"isn't a date and time in the form {shown}. Set datetime_format for another."
            ) from None
    return found if found.tzinfo is not None else found.replace(tzinfo=reading.tz)


def _time_of_day(value: Any) -> str:
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    text = _text(value).upper()
    for form in _CLOCK_FORMATS:
        try:
            return datetime.strptime(text, form).strftime("%H:%M:%S")
        except ValueError:
            continue
    raise _CellError("isn't a time of day, such as 13:30, 13:30:00, or 1:30 PM")


def _checkbox(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _text(value).casefold()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise _CellError("isn't true or false, such as yes, no, 1, or 0")


def _record_id(value: Any) -> int:
    number = _decimal(value, _PLAIN_NUMBER)
    if number != number.to_integral_value() or number < 1:
        raise _CellError("isn't a Record ID#, which is a whole number from 1")
    return int(number)


def _list(value: Any) -> list[str]:
    return [part.strip() for part in _text(value).split(LIST_SEPARATOR) if part.strip()]


def _convert(value: Any, item: Field, reading: _Reading) -> Any:
    """A cell's value as the SDK writes it to this field, or None for a blank cell."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    kind = item.type
    if kind in _TEXT_TYPES:
        return _unescape(_text(value))
    if kind in _NUMBER_TYPES:
        return _decimal(value, item)
    if kind == "duration":
        return _duration(value)
    if kind == "date":
        return _date(value, reading)
    if kind == "timestamp":
        return _timestamp(value, reading)
    if kind == "timeofday":
        return _time_of_day(value)
    if kind == "checkbox":
        return _checkbox(value)
    if kind == "recordid":
        return _record_id(value)
    if kind == "user":
        # Quickbase takes a user's email address, or their user ID, ignoring case.
        return _text(value)
    return _list(value)  # multitext and multiuser


def _why_not_writable(item: Field) -> str | None:
    if item.mode:
        return f"is a {item.mode} field, which Quickbase works out itself"
    if item.id in _SYSTEM_FIELDS:
        return "is set by Quickbase itself"
    if item.type == "file":
        return "is a file field, and a file can't come from a cell"
    if item.type not in _WRITABLE_TYPES:
        return f"is a {item.type} field, which can't be written from a file"
    return None


def _plan_columns(
    headers: Sequence[str],
    fields: Sequence[Field],
    columns: Mapping[str, str | int | None] | None,
    skip_read_only: bool,
) -> tuple[list[tuple[int, str, Field]], list[str]]:
    """Which field each column goes in, as (position, column, field), and the columns skipped."""
    given = dict(columns or {})
    by_label = {item.label: item for item in fields}
    by_id = {item.id: item for item in fields}
    missing = [name for name in given if name not in headers]
    if missing:
        raise InvalidValueError(f"columns names columns the file doesn't have: {missing}.")
    planned: list[tuple[int, str, Field]] = []
    skipped: list[str] = []
    unknown: list[str] = []
    refused: list[str] = []
    for position, header in enumerate(headers):
        if header in given and given[header] is None:
            skipped.append(header)
            continue
        target = given.get(header, header)
        item = by_id.get(target) if isinstance(target, int) else by_label.get(str(target))
        if item is None:
            unknown.append(header)
            continue
        reason = _why_not_writable(item)
        if reason and skip_read_only:
            skipped.append(header)
        elif reason:
            refused.append(f"{header!r} {reason}")
        else:
            planned.append((position, header, item))
    if unknown:
        hints = [
            f"{name!r} (did you mean {close[0]!r}?)" if close else repr(name)
            for name in unknown
            for close in [difflib.get_close_matches(name, list(by_label), n=1)]
        ]
        raise InvalidValueError(
            f"These columns aren't fields of the table: {', '.join(hints)}. Map each to a field "
            "with columns={column: field}, or skip it with columns={column: None}."
        )
    if refused:
        raise InvalidValueError(
            f"These columns can't be written: {'; '.join(refused)}. Skip them with "
            "columns={column: None}, or pass skip_read_only=True."
        )
    targets = [item.label for _, _, item in planned]
    doubled = sorted({label for label in targets if targets.count(label) > 1})
    if doubled:
        raise InvalidValueError(f"Several columns would be written to the same field: {doubled}.")
    return planned, skipped


def _headers(row: Sequence[Any] | None) -> list[str]:
    if not row or all(cell is None or not _text(cell) for cell in row):
        raise InvalidValueError("The file has no header row, which names each column's field.")
    headers = [_text(cell) if cell is not None else "" for cell in row]
    # A spreadsheet often has blank columns after the data.
    while headers and not headers[-1]:
        headers.pop()
    if "" in headers:
        raise InvalidValueError(f"Column {headers.index('') + 1} has no name in the header row.")
    doubled = sorted({name for name in headers if headers.count(name) > 1})
    if doubled:
        raise InvalidValueError(f"The header row names these columns more than once: {doubled}.")
    return headers


def read_rows(
    cells: Iterable[Sequence[Any]],
    fields: Sequence[Field],
    *,
    columns: Mapping[str, str | int | None] | None = None,
    skip_read_only: bool = False,
    tz: tzinfo = UTC,
    date_format: str | None = None,
    datetime_format: str | None = None,
) -> FileRows:
    """Convert a file's rows, the header first, for a table's fields."""
    rows = iter(cells)
    headers = _headers(next(rows, None))
    planned, skipped = _plan_columns(headers, fields, columns, skip_read_only)
    reading = _Reading(tz, date_format, datetime_format)
    found: list[Mapping[str, Any]] = []
    numbers: list[int] = []
    errors: list[FileRowError] = []
    for number, row in enumerate(rows, start=2):
        if all(cell is None or (isinstance(cell, str) and not cell.strip()) for cell in row):
            continue
        extra = row[len(headers) :]
        if any(cell is not None and _text(cell) for cell in extra):
            errors.append(
                FileRowError(row=number, column="", message="has more cells than the header names")
            )
            continue
        values: dict[str, Any] = {}
        for position, header, item in planned:
            cell = row[position] if position < len(row) else None
            try:
                values[item.label] = _convert(cell, item, reading)
            except _CellError as error:
                errors.append(FileRowError(row=number, column=header, message=str(error)))
        found.append(values)
        numbers.append(number)
    return FileRows(
        rows=tuple(found),
        row_numbers=tuple(numbers),
        columns={header: item.label for _, header, item in planned},
        skipped=tuple(skipped),
        errors=tuple(errors),
    )


def file_format(source: Any, given: FileFormat | None) -> FileFormat:
    """A file's format, from what was given, its name, or whether it's open as text."""
    if given is not None:
        if given not in ("csv", "xlsx"):
            raise InvalidValueError("format must be csv or xlsx.")
        return given
    if hasattr(source, "read"):
        return "csv" if isinstance(source, io.TextIOBase) else "xlsx"
    suffix = Path(cast(str, source)).suffix.casefold()
    if suffix in _EXCEL_SUFFIXES:
        return "xlsx"
    if suffix == ".xls":
        raise InvalidValueError("An .xls file is Excel's old format. Save it as .xlsx or .csv.")
    return "csv"


def _delimiter(source: Any, given: str | None) -> str:
    if given is not None:
        return given
    name = getattr(source, "name", source)
    is_path = isinstance(name, str | os.PathLike)
    if is_path and Path(cast(str, name)).suffix.casefold() in _TAB_SUFFIXES:
        return "\t"
    return ","


@contextmanager
def _opened(source: Any, mode: str, encoding: str | None) -> Generator[Any]:
    """A path opened, or a file given as it is, which is the caller's to close."""
    if hasattr(source, "read") or hasattr(source, "write"):
        yield source
        return
    newline = "" if "b" not in mode else None
    with open(source, mode, encoding=encoding, newline=newline) as handle:
        yield handle


def file_cells(
    source: Any,
    file_kind: FileFormat,
    *,
    sheet: str | None,
    encoding: str,
    delimiter: str | None,
) -> Iterator[Sequence[Any]]:
    """A file's rows, as cells."""
    if file_kind == "csv":
        if sheet is not None:
            raise InvalidValueError("sheet is only for Excel files.")
        with _opened(source, "r", encoding) as handle:
            yield from csv.reader(handle, delimiter=_delimiter(source, delimiter))
        return
    openpyxl = require_library("openpyxl", "Reading an Excel file")
    book = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        if sheet is None:
            page = book.worksheets[0]
        elif sheet in book.sheetnames:
            page = book[sheet]
        else:
            raise InvalidValueError(f"The workbook has no sheet {sheet!r}: {book.sheetnames}.")
        yield from page.iter_rows(values_only=True)
    finally:
        book.close()


# Writing


def _cell(value: Any) -> Any:
    """A record's value as a file holds it: a user as their email, or ID if the client's policy
    hides emails, a file as its latest version's name, and a list as one cell."""
    if isinstance(value, User):
        return value.email or value.id
    if isinstance(value, FileAttachment):
        return value.latest.file_name
    if isinstance(value, list):
        return f"{LIST_SEPARATOR} ".join(str(_cell(item)) for item in cast(list[Any], value))
    return value


def _csv_text(value: Any, spreadsheet_safe: bool) -> str:
    found = _cell(value)
    if found is None:
        return ""
    if isinstance(found, bool):
        return "true" if found else "false"
    if isinstance(found, datetime | date):
        return found.isoformat()
    text = str(found)
    if spreadsheet_safe and isinstance(found, str) and text.startswith(_FORMULA_STARTS):
        return f"'{text}"
    return text


class RecordWriter:
    """Writes records to a CSV or Excel file as they come, with a header from the first page."""

    def __init__(self, destination: Any, file_kind: FileFormat, *, spreadsheet_safe: bool) -> None:
        self._destination = destination
        self._kind = file_kind
        self._safe = spreadsheet_safe
        self._header: list[str] | None = None
        self._written = 0
        self._book: Any = None
        self._sheet: Any = None
        self._csv: Any = None
        self._handle: Any = None

    @contextmanager
    def open(self, *, sheet: str = "Records", encoding: str = "utf-8") -> Generator["RecordWriter"]:
        if self._kind == "csv":
            with _opened(self._destination, "w", encoding) as handle:
                self._csv = csv.writer(handle)
                yield self
            return
        openpyxl = require_library("openpyxl", "Writing an Excel file")
        self._book = openpyxl.Workbook(write_only=True)
        self._sheet = self._book.create_sheet(sheet)
        self._sheet.freeze_panes = "A2"
        yield self
        self._book.save(self._destination)

    def add(self, record: Record, columns: Sequence[str]) -> None:
        if self._header is None:
            self.header(columns or [str(label) for label in record])
        assert self._header is not None
        self._row([record.get(label) for label in self._header])
        self._written += 1

    def header(self, columns: Sequence[str]) -> None:
        if self._header is not None:
            return
        self._header = list(columns)
        if self._header:
            self._row(self._header, header=True)

    @property
    def written(self) -> int:
        """How many records were written."""
        return self._written

    def _row(self, values: Sequence[Any], *, header: bool = False) -> None:
        if self._csv is not None:
            self._csv.writerow([_csv_text(value, self._safe) for value in values])
            return
        self._sheet.append([self._excel_cell(value, header=header) for value in values])

    def _excel_cell(self, value: Any, *, header: bool) -> Any:
        cell_class = require_library("openpyxl", "Writing an Excel file").cell.WriteOnlyCell
        found = _cell(value)
        if isinstance(found, datetime) and found.tzinfo is not None:
            # Excel has no time zones, so a timestamp is written as UTC.
            found = found.astimezone(UTC).replace(tzinfo=None)
        cell = cell_class(self._sheet, value=found)
        if isinstance(found, str):
            # Text that starts with = is still text, never a formula.
            cell.data_type = "s"
        if isinstance(found, datetime):
            cell.number_format = "yyyy-mm-dd hh:mm:ss"
        elif isinstance(found, date):
            cell.number_format = "yyyy-mm-dd"
        if header:
            cell.font = require_library("openpyxl", "Writing an Excel file").styles.Font(bold=True)
        return cell


def write_records(
    records: Iterable[Record],
    columns: Sequence[str],
    destination: Any,
    file_kind: FileFormat,
    *,
    spreadsheet_safe: bool,
    sheet: str,
    encoding: str = "utf-8-sig",
) -> int:
    """Write records to a file, and return how many."""
    if file_kind == "xlsx":
        require_library("openpyxl", "to_excel()")
    writer = RecordWriter(destination, file_kind, spreadsheet_safe=spreadsheet_safe)
    with writer.open(sheet=sheet, encoding=encoding):
        for record in records:
            writer.add(record, columns)
        writer.header(columns)
    return writer.written


ColumnMap: TypeAlias = Mapping[str, str | int | None]
"""Which field each file column goes in, by label or ID, or None to skip a column."""
