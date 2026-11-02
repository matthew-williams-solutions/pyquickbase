"""Prepare records for writing and interpret the results. No network access.

Quickbase silently ignores a null value, reports failed rows inside a successful response, and
creates a record when a merge value doesn't match. See docs/api-notes.md for the tested behavior.
"""

import base64
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unicodedata import category

from ._models import Field, FieldIndex
from .errors import InvalidValueError

RowInput = Mapping[str | int, Any]
FILE_FIELD_TYPE = "file"
# Quickbase returns 413 above 40 MiB. This leaves room for the rest of the request body.
MAX_ROWS_BYTES = 40 * 1024 * 1024 - 1024


@dataclass(frozen=True, slots=True)
class FileUpload:
    """A file to write to a file attachment field, as a value in `insert()`, `update()`, or
    `upsert()`.

    `name` can't contain a path or control characters, because Quickbase stores them as sent and
    could pass them to whoever downloads the file later.
    """

    name: str
    data: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.name or any(c in "/\\" or category(c) == "Cc" for c in self.name):
            raise InvalidValueError(
                f"A file name can't be empty or contain a path or control characters: {self.name!r}"
            )
        if not isinstance(self.data, bytes):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidValueError("A file's data must be bytes.")

    def __repr__(self) -> str:
        # File contents may be sensitive and reprs end up in logs, so show only the shape.
        return f"FileUpload({self.name!r}, {len(self.data)} bytes)"

    def to_api(self) -> dict[str, str]:
        return {"fileName": self.name, "data": base64.b64encode(self.data).decode("ascii")}


def require_id(name: str, value: int) -> None:
    """Check a number that goes into a URL path, so a string can't change the path."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise InvalidValueError(f"{name} must be a positive integer, got {value!r}.")


def require_formula(formula: str) -> None:
    """Refuse a blank formula. Quickbase returns an empty result, and blanks a field's values."""
    if not isinstance(formula, str) or not formula.strip():  # pyright: ignore[reportUnnecessaryIsInstance]
        raise InvalidValueError("A formula can't be blank.")


def field_properties(properties: Mapping[str, Any] | None, formula: str | None) -> dict[str, Any]:
    """Merge a field's properties and formula, refusing a blank formula however it was given."""
    merged = dict(properties or {})
    if formula is not None:
        merged["formula"] = formula
    if "formula" in merged:
        require_formula(merged["formula"])
    return merged


def require_file_field(item: Field) -> None:
    if item.type != FILE_FIELD_TYPE:
        raise InvalidValueError(f"Field {item.label!r} isn't a file attachment field.")


def split_batches(rows: Sequence[Mapping[str, Any]], batch_size: int) -> Iterator[tuple[int, int]]:
    """Yield `(start, end)` ranges of rows that fit in one request.

    A row too large on its own still gets a request, so that Quickbase decides and reports it.
    """
    start = size = 0
    for index, row in enumerate(rows):
        row_size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode()) + 1
        if index > start and (index - start >= batch_size or size + row_size > MAX_ROWS_BYTES):
            yield start, index
            start, size = index, 0
        size += row_size
    if rows:
        yield start, len(rows)


def to_api_value(value: Any) -> Any:
    """Render a Python value for writing. `None` clears the field, which null doesn't do."""
    if value is None:
        return ""
    if isinstance(value, bool | int | float | str | list | dict):
        return value  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise InvalidValueError("A datetime needs a timezone, so its meaning is clear.")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise InvalidValueError(f"Can't write a value of type {type(value).__name__}.")


def _cell_value(item: Field, value: Any, row: int) -> Any:
    """Check file values here, since Quickbase accepts the mistakes and silently loses the file."""
    if item.type == FILE_FIELD_TYPE:
        if isinstance(value, FileUpload):
            return value.to_api()
        raise InvalidValueError(
            f"Row {row} sets file field {item.label!r} to something other than a FileUpload. "
            "To keep the field empty, leave it out. To remove a file, use delete_file_version()."
        )
    if isinstance(value, FileUpload):
        raise InvalidValueError(f"Row {row} sends a file to {item.label!r}, not a file field.")
    return to_api_value(value)


def build_rows(rows: Iterable[RowInput], fields: FieldIndex) -> list[dict[str, dict[str, Any]]]:
    """Turn `{label or ID: value}` rows into Quickbase's `{"6": {"value": ...}}` form."""
    built: list[dict[str, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if not row:
            raise InvalidValueError(f"Row {index} has no fields.")
        cells: dict[str, dict[str, Any]] = {}
        for ref, value in row.items():
            item = fields.resolve(ref)
            key = str(item.id)
            if key in cells:
                raise InvalidValueError(f"Row {index} sets field {key} more than once.")
            cells[key] = {"value": _cell_value(item, value, index)}
        built.append(cells)
    return built


def require_field(rows: Sequence[Mapping[str, Any]], field_id: int, purpose: str) -> None:
    """Check every built row sets the field, since one row without it fails the whole request."""
    for index, row in enumerate(rows):
        if str(field_id) not in row:
            raise InvalidValueError(f"Row {index} doesn't set field {field_id}, {purpose}.")


def refuse_field(rows: Sequence[Mapping[str, Any]], field_id: int, purpose: str) -> None:
    for index, row in enumerate(rows):
        if str(field_id) in row:
            raise InvalidValueError(f"Row {index} sets field {field_id}, {purpose}.")


@dataclass(frozen=True, slots=True, kw_only=True)
class RowError:
    """Why one row failed. `index` is its position in the rows you passed, starting from 0."""

    index: int
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteResult:
    """What a write did. If rows fail, `WriteError.result` holds this, including what succeeded."""

    created_ids: tuple[int, ...] = ()
    updated_ids: tuple[int, ...] = ()
    unchanged_ids: tuple[int, ...] = ()
    errors: tuple[RowError, ...] = ()

    def merged(self, payload: Mapping[str, Any], offset: int) -> "WriteResult":
        """Add one request's response. `offset` is the position of its first row."""
        metadata = payload["metadata"]
        line_errors: Mapping[str, list[str]] = metadata.get("lineErrors") or {}
        errors = tuple(
            RowError(index=int(line) - 1 + offset, messages=tuple(messages))
            for line, messages in line_errors.items()
        )
        return replace(
            self,
            created_ids=(*self.created_ids, *metadata["createdRecordIds"]),
            updated_ids=(*self.updated_ids, *metadata["updatedRecordIds"]),
            unchanged_ids=(*self.unchanged_ids, *metadata["unchangedRecordIds"]),
            errors=(*self.errors, *errors),
        )

    def summary(self) -> str:
        """Describe what failed. Quickbase's messages name field IDs rather than values."""
        first = self.errors[0]
        return (
            f"{len(self.errors)} row(s) failed. The first is row {first.index}: "
            f"{'; '.join(first.messages)} See `.result` for every row."
        )
