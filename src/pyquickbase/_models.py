"""Typed views of Quickbase metadata and records. No network access."""

import base64
import difflib
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar
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
    """An app's metadata. `raw` holds everything Quickbase returned."""

    id: str
    name: str
    description: str
    time_zone: str | None
    date_format: str | None
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "AppInfo":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description") or "",
            time_zone=data.get("timeZone"),
            date_format=data.get("dateFormat"),
            raw=data,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TableInfo:
    """A table's metadata. `raw` holds everything Quickbase returned."""

    id: str
    name: str
    description: str
    key_field_id: int | None
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "TableInfo":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description") or "",
            key_field_id=data.get("keyFieldId"),
            raw=data,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Field:
    """A field's metadata. `mode` is `formula`, `lookup`, `summary`, or None for plain fields.

    `formula` is the formula's text, or None if the field doesn't have one.
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
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Field":
        properties: Mapping[str, Any] = data.get("properties") or {}
        return cls(
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
            raw=data,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Relationship:
    """A parent-child link between two tables."""

    id: int
    parent_table_id: str
    child_table_id: str
    foreign_key_field_id: int | None
    lookup_field_ids: tuple[int, ...]
    is_cross_app: bool
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Relationship":
        foreign_key: Mapping[str, Any] = data.get("foreignKeyField") or {}
        return cls(
            id=data["id"],
            parent_table_id=data["parentTableId"],
            child_table_id=data["childTableId"],
            foreign_key_field_id=foreign_key.get("id"),
            lookup_field_ids=tuple(item["id"] for item in data.get("lookupFields") or ()),
            is_cross_app=bool(data.get("isCrossApp")),
            raw=data,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportInfo:
    """A report's metadata. `type` is `table`, `summary`, `chart`, or another Quickbase type.

    `incomplete` is true when Quickbase can't run the report through the API, as with one that asks
    the user for a value. `raw` holds everything Quickbase returned.
    """

    id: str
    name: str
    type: str
    description: str
    incomplete: bool
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "ReportInfo":
        return cls(
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
    return f"This table has no report {ref!r}.{hint}"


def require_table_report(report: ReportInfo) -> None:
    """Refuse reports whose rows aren't records.

    Other types return the same response shape, but with field IDs that are column positions, so
    a total in the second column would read as Record ID#. Only `type` tells them apart.
    """
    if report.type != "table":
        raise UnsupportedReportError(
            f"Report {report.name!r} is a {report.type} report, and only table reports can be read "
            "as records. Use qb.request() for the raw response."
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
            raise UnknownFieldError(f"This table has no field with ID {ref!r}.")
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
        raise UnknownFieldError(f"This table has no field labeled {label!r}.{hint}")


def _convert(value: Any, field_type: str) -> Any:
    """Turn dates and timestamps into Python objects, leaving anything unexpected unchanged."""
    if value == "" and field_type in ("date", "timestamp"):
        return None  # how Quickbase returns an empty date
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
    objects and timestamp fields are timezone-aware `datetime` objects, or None when empty.
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

    `name` and `content_type` aren't trustworthy. Quickbase stores names as sent, such as
    `../../x`, so don't use `name` as a path without cleaning it, and `content_type` is based on
    the name rather than the contents.
    """

    name: str
    data: bytes = field(repr=False)
    content_type: str | None

    @classmethod
    def from_response(cls, response: Any) -> "FileDownload":
        """Quickbase sends the contents as base64, whatever `Content-Type` says."""
        found = _DISPOSITION_NAME.search(response.headers["content-disposition"])
        if found is None:
            raise ValueError("No file name in the response.")
        return cls(
            name=unquote(found[1]),
            data=base64.b64decode(response.content, validate=True),
            content_type=response.headers.get("content-type"),
        )

    def __repr__(self) -> str:
        # File contents may be sensitive and reprs end up in logs, so show only the shape.
        return f"FileDownload({self.name!r}, {len(self.data)} bytes)"


@dataclass(frozen=True, slots=True, kw_only=True)
class FormulaResult:
    """The result of `table.run_formula()`.

    `text` is exactly what Quickbase sent, which is always a string, and `type` is Quickbase's name
    for its type, such as `number`, `bool`, or `date`.
    """

    text: str = field(repr=False)
    type: str

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "FormulaResult":
        return cls(text=data["result"], type=data["resultType"])

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


@dataclass(frozen=True, slots=True, kw_only=True)
class Page:
    """One page of query results. `total` counts every match, not just this page."""

    records: tuple[Record, ...]
    total: int
    skip: int

    @property
    def has_more(self) -> bool:
        return self.next_skip < self.total

    @property
    def next_skip(self) -> int:
        """The `skip` that fetches the following page."""
        return self.skip + len(self.records)

    @classmethod
    def from_api(cls, data: Mapping[str, Any]) -> "Page":
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
        return cls(records=records, total=metadata["totalRecords"], skip=metadata["skip"])
