"""Typed views of Quickbase metadata and records. No network access."""

import base64
import difflib
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar, cast
from urllib.parse import unquote

from .errors import (
    InvalidResponseError,
    UnknownFieldError,
    UnknownReportError,
    UnsupportedReportError,
)

RECORD_ID_FIELD = 3
_DISPOSITION_NAME = re.compile(r"filename\*=utf-8''([^;]+)", re.IGNORECASE)


T = TypeVar("T")


def parse(parser: Callable[[Any], T], payload: Any, what: str) -> T:
    """Run a parser, reporting a response of the wrong shape as a Quickbase-level error."""
    try:
        return parser(payload)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise InvalidResponseError(f"Quickbase returned an unexpected {what} response.") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class AppInfo:
    """An app's metadata.

    Attributes:
        id: The app's ID.
        name: The app's name.
        description: The app's description, which may be empty.
        time_zone: The app's time zone, such as `(UTC) Coordinated Universal Time`, if reported.
        date_format: How the app writes dates, such as `MM-DD-YYYY`, if reported.
        raw: Everything Quickbase returned, for anything not listed here.
    """

    id: str
    name: str
    description: str
    time_zone: str | None
    date_format: str | None
    raw: Mapping[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class Role:
    """A role in an app.

    Attributes:
        id: The role's ID.
        name: The role's name.
        access: Quickbase's name for its level, such as `Basic Access`.
        raw: Everything Quickbase returned, for anything not listed here.
    """

    id: int
    name: str
    access: str
    raw: Mapping[str, Any] = field(repr=False, compare=False)


def role_from_api(data: Mapping[str, Any]) -> Role:
    access: Mapping[str, Any] = data.get("access") or {}
    return Role(
        id=data["id"],
        name=data["name"],
        access=access.get("type") or "",
        raw=data,
    )


def app_info_from_api(data: Mapping[str, Any]) -> AppInfo:
    return AppInfo(
        id=data["id"],
        name=data["name"],
        description=data.get("description") or "",
        time_zone=data.get("timeZone"),
        date_format=data.get("dateFormat"),
        raw=data,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class TableInfo:
    """A table's metadata.

    Attributes:
        id: The table's ID.
        name: The table's name.
        description: The table's description, which may be empty.
        key_field_id: The ID of the table's key field, which is 3, `Record ID#`, unless someone
            changed it in the Quickbase interface. None if Quickbase left it out.
        raw: Everything Quickbase returned, for anything not listed here.
    """

    id: str
    name: str
    description: str
    key_field_id: int | None
    raw: Mapping[str, Any] = field(repr=False, compare=False)


def table_info_from_api(data: Mapping[str, Any]) -> TableInfo:
    return TableInfo(
        id=data["id"],
        name=data["name"],
        description=data.get("description") or "",
        key_field_id=data.get("keyFieldId"),
        raw=data,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Field:
    """A field's metadata.

    Attributes:
        id: The field's ID.
        label: The field's label.
        type: Quickbase's name for the field's type, such as `text` or `numeric`.
        mode: `formula`, `lookup`, `summary`, or None for a plain field.
        required: Whether the field must have a value.
        unique: Whether values must be different on every record.
        appears_by_default: Whether a query returns the field when `select` is left out.
        choices: The options of a multiple-choice field, or empty for other fields.
        allow_new_choices: Quickbase's `allowNewChoices` setting, or False if it isn't reported.
        formula: The formula's text, or None if the field doesn't have one.
        help_text: The help Quickbase shows beside the field on forms, which may be empty.
        decimal_places: Only how a number is displayed: Quickbase still stores `1.5` in a field set
            to 0 places.
        is_foreign_key: Whether the field links to a parent table's record.
        lookup_reference_field_id: For a lookup, the foreign key field on this table.
        lookup_target_field_id: For a lookup, the field it reads on the parent.
        summary_reference_field_id: For a summary, the foreign key field on the child table.
        summary_target_field_id: For a summary, the field it reads on the child.
        summary_function: For a summary, Quickbase's name for its calculation, such as `SUM`.
        raw: Everything Quickbase returned, for anything not listed here.
    """

    id: int
    label: str
    type: str
    mode: str | None
    required: bool
    unique: bool
    appears_by_default: bool
    choices: tuple[str, ...]
    allow_new_choices: bool
    formula: str | None = None
    help_text: str = ""
    decimal_places: int | None = None
    is_foreign_key: bool = False
    lookup_reference_field_id: int | None = None
    lookup_target_field_id: int | None = None
    summary_reference_field_id: int | None = None
    summary_target_field_id: int | None = None
    summary_function: str | None = None
    raw: Mapping[str, Any] = field(repr=False, compare=False)


def field_from_api(data: Mapping[str, Any]) -> Field:
    properties: Mapping[str, Any] = data.get("properties") or {}
    return Field(
        id=data["id"],
        label=data["label"],
        type=data["fieldType"],
        mode=data.get("mode") or None,
        required=bool(data.get("required")),
        unique=bool(data.get("unique")),
        appears_by_default=bool(data.get("appearsByDefault")),
        choices=tuple(properties.get("choices") or ()),
        allow_new_choices=bool(properties.get("allowNewChoices")),
        formula=properties.get("formula") or None,
        help_text=data.get("fieldHelp") or "",
        decimal_places=properties.get("decimalPlaces"),
        is_foreign_key=bool(properties.get("foreignKey")),
        lookup_reference_field_id=properties.get("lookupReferenceFieldId"),
        lookup_target_field_id=properties.get("lookupTargetFieldId"),
        summary_reference_field_id=properties.get("summaryReferenceFieldId"),
        summary_target_field_id=properties.get("summaryTargetFieldId"),
        summary_function=properties.get("summaryFunction"),
        raw=data,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Relationship:
    """A parent-child link between two tables.

    `create_relationship()` and `ensure_lookup_field()` take the parent's field to look up, which
    isn't what `lookup_field_ids` holds.

    Attributes:
        id: The relationship's ID.
        parent_table_id: The parent table's ID.
        child_table_id: The child table's ID.
        foreign_key_field_id: The child's field that holds the parent's Record ID#, if reported.
        lookup_field_ids: The lookup fields, which are on the child.
        summary_field_ids: The summary fields, which are on the parent.
        is_cross_app: Whether Quickbase reports the parent as being in another app.
        raw: Everything Quickbase returned, for anything not listed here.
    """

    id: int
    parent_table_id: str
    child_table_id: str
    foreign_key_field_id: int | None
    lookup_field_ids: tuple[int, ...]
    summary_field_ids: tuple[int, ...] = ()
    is_cross_app: bool
    raw: Mapping[str, Any] = field(repr=False, compare=False)


def relationship_from_api(data: Mapping[str, Any]) -> Relationship:
    foreign_key: Mapping[str, Any] = data.get("foreignKeyField") or {}
    return Relationship(
        id=data["id"],
        parent_table_id=data["parentTableId"],
        child_table_id=data["childTableId"],
        foreign_key_field_id=foreign_key.get("id"),
        lookup_field_ids=tuple(item["id"] for item in data.get("lookupFields") or ()),
        summary_field_ids=tuple(item["id"] for item in data.get("summaryFields") or ()),
        is_cross_app=bool(data.get("isCrossApp")),
        raw=data,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportInfo:
    """A report's metadata.

    Attributes:
        id: The report's ID.
        name: The report's name.
        type: `table`, `summary`, `chart`, or another Quickbase type.
        description: The report's description, which may be empty.
        incomplete: Whether Quickbase can't run the report through the API, as with one that asks
            the user for a value.
        raw: Everything Quickbase returned, for anything not listed here.
    """

    id: str
    name: str
    type: str
    description: str
    incomplete: bool
    raw: Mapping[str, Any] = field(repr=False, compare=False)


def report_info_from_api(data: Mapping[str, Any]) -> ReportInfo:
    return ReportInfo(
        id=data["id"],
        name=data["name"],
        type=data["type"],
        description=data.get("description") or "",
        incomplete=bool(data.get("incomplete")),
        raw=data,
    )


def find_report(reports: Sequence[ReportInfo], ref: str) -> ReportInfo | None:
    """Find a report by ID, then by name. Names can repeat, and guessing could run the wrong one."""
    found = next((item for item in reports if item.id == ref), None)
    if found is not None:
        return found
    matches = [item for item in reports if item.name == ref]
    if len(matches) > 1:
        ids = [item.id for item in matches]
        raise UnknownReportError(f"Several reports are named {ref!r}. Use an ID: {ids}.")
    return matches[0] if matches else None


def unknown_report_message(reports: Sequence[ReportInfo], ref: str) -> str:
    close = difflib.get_close_matches(ref, dict.fromkeys(item.name for item in reports), n=3)
    hint = f" Did you mean {' or '.join(repr(name) for name in close)}?" if close else ""
    return (
        f"This table has no report {ref!r}, or this client can't see it.{hint} Quickbase hides a "
        "report from a role whose access doesn't include a field the report filters or sorts on."
    )


def require_table_report(report: ReportInfo) -> None:
    """Refuse reports whose rows aren't records.

    Other types return the same response shape, but with field IDs that are column positions, so
    a total in the second column would read as Record ID#. Only `type` tells them apart.
    """
    if report.type != "table":
        raise UnsupportedReportError(
            f"Report {report.name!r} is a {report.type} report, and only table reports can be read "
            "as records. Use summary_rows() for a summary or chart report."
        )
    if report.incomplete:
        raise UnsupportedReportError(
            f"Quickbase can't run report {report.name!r} through the API because it's incomplete, "
            "as happens when a report asks the user for a value."
        )


class FieldIndex:
    """Find a table's fields by label or ID."""

    def __init__(self, fields: Iterable[Field]) -> None:
        self._by_id = {item.id: item for item in fields}
        self._by_label: dict[str, list[Field]] = {}
        for item in self._by_id.values():
            self._by_label.setdefault(item.label, []).append(item)

    def has_label(self, label: str) -> bool:
        return label in self._by_label

    def resolve(self, ref: str | int) -> Field:
        """Return the field for a label (`str`) or ID (`int`)."""
        if isinstance(ref, str):
            return self._resolve_label(ref)
        found = None if isinstance(ref, bool) else self._by_id.get(ref)
        if found is None:
            raise UnknownFieldError(
                f"This table has no field with ID {ref!r}, or this client can't see it."
            )
        return found

    def _resolve_label(self, label: str) -> Field:
        matches = self._by_label.get(label, [])
        if len(matches) == 1:
            return matches[0]
        if matches:
            ids = sorted(item.id for item in matches)
            raise UnknownFieldError(f"Several fields are labeled {label!r}. Use an ID: {ids}.")
        close = difflib.get_close_matches(label, self._by_label, n=3)
        hint = f" Did you mean {' or '.join(repr(name) for name in close)}?" if close else ""
        raise UnknownFieldError(
            f"This table has no field labeled {label!r}, or this client can't see it.{hint}"
        )


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class User:
    """A Quickbase user, as a `user` or `multiuser` field holds one.

    `repr` leaves out the email address, because reprs end up in logs. Pass a `User` as a value to
    write it to a user field, or to compare with one in `F`.

    Attributes:
        id: The user's ID.
        name: The user's name, as Quickbase shows it.
        email: The user's email address, or None if the client's `ReadRules` leave it out.
        user_name: The user's Quickbase user name, if Quickbase sent one.
    """

    id: str
    name: str
    email: str | None = None
    user_name: str | None = None

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, name={self.name!r})"


@dataclass(frozen=True, slots=True, kw_only=True)
class FileVersion:
    """One version of a file in a file attachment field.

    Attributes:
        number: The version's number, starting at 1. Numbers aren't reused after a delete.
        file_name: The file's name. Quickbase stores names as sent, such as `../../x`, so don't use
            this as a path without cleaning it.
        uploaded: When it was uploaded, or None if Quickbase didn't say.
        creator: Who uploaded it, or None if the client's `ReadRules` leave it out.
    """

    number: int
    file_name: str
    uploaded: datetime | None
    creator: User | None


@dataclass(frozen=True, slots=True, kw_only=True)
class FileAttachment:
    """A file attachment field's value, which holds each version Quickbase keeps.

    An empty field reads as None, so a `FileAttachment` always has at least one version. Download
    the contents with `table.download_file()`.

    Attributes:
        url: The file's path in Quickbase, relative to the realm.
        versions: Each version Quickbase keeps, oldest first.
    """

    url: str
    versions: tuple[FileVersion, ...]

    @property
    def latest(self) -> FileVersion:
        """The newest version."""
        return max(self.versions, key=lambda version: version.number)


def user_from_api(data: Mapping[str, Any]) -> User:
    return User(
        id=str(data["id"]),
        name=data.get("name") or "",
        email=data.get("email") or None,
        user_name=data.get("userName") or None,
    )


def _uploaded(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None


def _creator(value: Any) -> User | None:
    """Who uploaded a version. A version is still readable without one."""
    if isinstance(value, Mapping) and "id" in value:
        return user_from_api(cast(Mapping[str, Any], value))
    return None


def file_from_api(data: Mapping[str, Any]) -> FileAttachment | None:
    versions = tuple(
        FileVersion(
            number=int(item["versionNumber"]),
            file_name=item.get("fileName") or "",
            uploaded=_uploaded(item.get("uploaded")),
            creator=_creator(item.get("creator")),
        )
        for item in data.get("versions") or ()
    )
    return FileAttachment(url=data.get("url") or "", versions=versions) if versions else None


def _convert_users(value: Any, field_type: str) -> Any:
    if field_type == "user" and isinstance(value, Mapping):
        return user_from_api(cast(Mapping[str, Any], value))
    if field_type == "multiuser" and isinstance(value, list):
        items = cast(list[Any], value)
        if all(isinstance(item, Mapping) for item in items):
            return [user_from_api(item) for item in items]
    return cast(Any, value)


def _convert(value: Any, field_type: str) -> Any:
    """Turn dates, timestamps, users, and files into Python objects, leaving anything unexpected
    unchanged."""
    if value == "" and field_type in ("date", "timestamp"):
        return None  # how Quickbase returns an empty date
    try:
        if field_type in ("user", "multiuser"):
            return _convert_users(value, field_type)
        if field_type == "file" and isinstance(value, Mapping):
            return file_from_api(cast(Mapping[str, Any], value))
    except (KeyError, TypeError, ValueError):
        return cast(Any, value)
    if isinstance(value, str):
        try:
            if field_type == "date":
                return date.fromisoformat(value)
            if field_type == "timestamp":
                return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


class Record(Mapping[str | int, Any]):
    """One record. Read values by field label or ID: `record["Status"]` or `record[7]`.

    Iterating yields labels, so `dict(record)` gives `{label: value}`. Date fields are `date`
    objects and timestamp fields are timezone-aware `datetime` objects, or None when empty. A user
    field holds a `User`, a `multiuser` field a list of them, and a file field a `FileAttachment`,
    or None when empty.
    """

    __slots__ = ("_labels", "_values")

    def __init__(self, values: Mapping[int, Any], labels: Mapping[str, int]) -> None:
        self._values = values
        self._labels = labels

    @property
    def id(self) -> int:
        """The Record ID#. Queries always select it, but a report may not include it.

        Raises:
            KeyError: The record came from a report without a Record ID# column.
        """
        try:
            return self._values[RECORD_ID_FIELD]
        except KeyError:
            raise KeyError("This record has no Record ID#, so its report left it out.") from None

    def __getitem__(self, key: str | int) -> Any:
        field_id = self._labels[key] if isinstance(key, str) else key
        return self._values[field_id]

    def __iter__(self) -> Iterator[str | int]:
        return iter(label for label, field_id in self._labels.items() if field_id in self._values)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __repr__(self) -> str:
        # Values may be sensitive and reprs end up in logs, so show only the shape.
        return f"Record(id={self._values.get(RECORD_ID_FIELD)!r}, fields={len(self)})"


@dataclass(frozen=True, slots=True, kw_only=True)
class FileDownload:
    """A file downloaded from a file attachment field.

    `repr` leaves out the contents, because they may be sensitive and reprs end up in logs.

    Attributes:
        name: The file's name. Quickbase stores names as sent, such as `../../x`, so don't use this
            as a path without cleaning it.
        data: The file's contents.
        content_type: The media type, if reported. It's based on the name rather than the contents,
            so it doesn't tell you what's in the file.
    """

    name: str
    data: bytes = field(repr=False)
    content_type: str | None

    def __repr__(self) -> str:
        # File contents may be sensitive and reprs end up in logs, so show only the shape.
        return f"FileDownload({self.name!r}, {len(self.data)} bytes)"


def file_download_from_response(response: Any) -> FileDownload:
    """Quickbase sends the contents as base64, whatever `Content-Type` says."""
    found = _DISPOSITION_NAME.search(response.headers["content-disposition"])
    if found is None:
        raise ValueError("No file name in the response.")
    return FileDownload(
        name=unquote(found[1]),
        data=base64.b64decode(response.content, validate=True),
        content_type=response.headers.get("content-type"),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class FormulaResult:
    """The result of `table.run_formula()`.

    Attributes:
        text: Exactly what Quickbase sent, which is always a string.
        type: Quickbase's name for its type, such as `number`, `bool`, or `date`.
    """

    text: str = field(repr=False)
    type: str

    @property
    def value(self) -> Any:
        """The result as a Python value, converted only where that's safe.

        A `bool` becomes `True` or `False`, and a `number` becomes a `Decimal`, or None if either
        is empty. A `null` becomes None. Anything else stays as text, including dates and
        timestamps, because they use the app's display format and have no time zone.
        """
        if self.type == "null":
            return None
        if self.type == "bool" and self.text in ("", "0", "1"):
            return None if not self.text else self.text == "1"
        if self.type == "number":
            try:
                return Decimal(self.text) if self.text else None
            except InvalidOperation:
                return self.text
        return self.text


def formula_result_from_api(data: Mapping[str, Any]) -> FormulaResult:
    return FormulaResult(text=data["result"], type=data["resultType"])


@dataclass(frozen=True, slots=True, kw_only=True)
class Page:
    """One page of query results.

    Attributes:
        records: The `Record` objects on this page.
        total: How many records match, not just the ones on this page.
        skip: How many matching records came before this page.
    """

    records: tuple[Record, ...]
    total: int
    skip: int

    @property
    def has_more(self) -> bool:
        """Whether more matching records come after this page."""
        return self.next_skip < self.total

    @property
    def next_skip(self) -> int:
        """The `skip` that fetches the following page."""
        return self.skip + len(self.records)


def require_summary_report(report: ReportInfo) -> None:
    """Refuse reports whose rows are records, or that Quickbase can't run through the API."""
    if report.type not in ("summary", "chart"):
        raise UnsupportedReportError(
            f"Report {report.name!r} is a {report.type} report. Use run() for a table report's "
            "records."
        )
    if report.incomplete:
        raise UnsupportedReportError(
            f"Quickbase can't run report {report.name!r} through the API because it's incomplete, "
            "as happens when a report asks the user for a value."
        )


def summary_rows_from_api(data: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Read a summary or chart report's rows, and how many there are in all.

    Field IDs here are column positions, not fields, so they're only used to match each value to its
    label. Two columns with the same label would lose a value, so that raises an error.
    """
    labels: dict[int, str] = {item["id"]: item["label"] for item in data["fields"]}
    if len(set(labels.values())) != len(labels):
        raise UnsupportedReportError(
            "This report has several columns with the same label, so rows keyed by label would "
            "lose values. Rename them, or use qb.request() for the raw response."
        )
    rows = [{labels[int(key)]: cell["value"] for key, cell in row.items()} for row in data["data"]]
    return rows, data["metadata"]["totalRecords"]


def page_from_api(data: Mapping[str, Any]) -> Page:
    fields = data["fields"]
    labels: dict[str, int] = {item["label"]: item["id"] for item in fields}
    types: dict[int, str] = {item["id"]: item["type"] for item in fields}
    records = tuple(
        Record(
            {
                int(field_id): _convert(cell["value"], types.get(int(field_id), ""))
                for field_id, cell in row.items()
            },
            labels,
        )
        for row in data["data"]
    )
    metadata = data["metadata"]
    return Page(records=records, total=metadata["totalRecords"], skip=metadata["skip"])
