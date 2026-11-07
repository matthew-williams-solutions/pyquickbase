"""Prepare records for writing and interpret the results. No network access.

Quickbase silently ignores a null value, reports failed rows inside a successful response, and
creates a record when a merge value doesn't match. See docs/api-notes.md for the tested behavior.
"""

import base64
import json
import math
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from unicodedata import category

from ._models import Field, FieldIndex, User
from ._query import plain_number
from .errors import (
    InvalidValueError,
    PermissionDeniedError,
    UnknownFieldError,
    WriteError,
    WritePermissionError,
)

RowInput = Mapping[str | int, Any]
FILE_FIELD_TYPE = "file"
# Quickbase returns 413 above 40 MiB. This leaves room for the rest of the request body.
MAX_ROWS_BYTES = 40 * 1024 * 1024 - 1024


@dataclass(frozen=True, slots=True)
class FileUpload:
    """A file to write to a file attachment field, as a value in `insert()`, `update()`, or
    `upsert()`.

    `repr` leaves out the contents, because they may be sensitive and reprs end up in logs.

    Args:
        file_name: The file's name. It can't contain a path or control characters, because Quickbase
            stores them as sent and could pass them to whoever downloads the file later.
        data: The file's contents.

    Raises:
        InvalidValueError: `file_name` is empty, or contains a path or control characters.
    """

    file_name: str
    data: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.file_name or any(c in "/\\" or category(c) == "Cc" for c in self.file_name):
            raise InvalidValueError(
                "A file name can't be empty or contain a path or control characters: "
                f"{self.file_name!r}"
            )
        if not isinstance(self.data, bytes):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidValueError("A file's data must be bytes.")

    def __repr__(self) -> str:
        # File contents may be sensitive and reprs end up in logs, so show only the shape.
        return f"FileUpload({self.file_name!r}, {len(self.data)} bytes)"


def file_upload_to_api(upload: FileUpload) -> dict[str, str]:
    return {"fileName": upload.file_name, "data": base64.b64encode(upload.data).decode("ascii")}


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


# Settings Quickbase takes beside a field's `properties`, by argument name. See docs/api-notes.md.
FIELD_SETTINGS = {
    "help_text": "fieldHelp",
    "bold": "bold",
    "no_wrap": "noWrap",
    "find_enabled": "findEnabled",
    "appears_by_default": "appearsByDefault",
    "add_to_forms": "addToForms",
}


def field_settings(**values: Any) -> dict[str, Any]:
    """The settings that were given, by Quickbase's name, refusing a value of the wrong type."""
    found: dict[str, Any] = {}
    for name, value in values.items():
        if value is None:
            continue
        wanted = str if name == "help_text" else bool
        if not isinstance(value, wanted):
            kind = "text" if wanted is str else "True or False"
            raise InvalidValueError(f"{name} takes {kind}.")
        found[FIELD_SETTINGS[name]] = value
    return found


def require_file_field(item: Field) -> None:
    if item.type != FILE_FIELD_TYPE:
        raise InvalidValueError(f"Field {item.label!r} isn't a file attachment field.")


def refuse_blank_rows(rows: Sequence[Mapping[str, Mapping[str, Any]]]) -> None:
    """Refuse rows with no values, which Quickbase can skip silently, shifting `created_ids`."""
    for index, row in enumerate(rows):
        if all(cell["value"] in ("", []) for cell in row.values()):
            raise InvalidValueError(
                f"Row {index} has no values, and Quickbase can silently skip a row like this."
            )


def split_batches(rows: Sequence[Mapping[str, Any]], batch_size: int) -> Iterator[tuple[int, int]]:
    """Yield `(start, end)` ranges of rows that fit in one request.

    A row too large on its own still gets a request, so that Quickbase decides and reports it.
    Rows are measured as httpx 0.28 and later send them, as compact UTF-8 JSON.
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
    if isinstance(value, User):
        return {"id": value.id}
    if isinstance(value, list) and any(isinstance(item, User) for item in value):  # pyright: ignore[reportUnknownVariableType]
        return [to_api_value(item) if isinstance(item, User) else item for item in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidValueError("A number must be finite, as JSON can't hold NaN or infinity.")
    if isinstance(value, bool | int | float | str | list | dict):
        return value  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, Decimal):
        found = plain_number(value)
        if found is None:
            raise InvalidValueError("A Decimal must be finite, and can't be this large.")
        return found
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise InvalidValueError("A datetime needs a timezone, so its meaning is clear.")
        # Quickbase refuses Python's default of six fractional digits, and only accepts three.
        return value.isoformat(timespec="milliseconds" if value.microsecond else "seconds")
    if isinstance(value, date):
        return value.isoformat()
    raise InvalidValueError(f"Can't write a value of type {type(value).__name__}.")


def duration_value(value: Any, label: str) -> int | str:
    """Milliseconds as a whole number, which Quickbase takes, as it drops a fraction silently."""
    if value is None:
        return ""
    if isinstance(value, timedelta):
        milliseconds = value / timedelta(milliseconds=1)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        milliseconds = value
    else:
        raise InvalidValueError(
            f"Field {label!r} is a duration. Write a timedelta, or whole milliseconds as an int."
        )
    if not math.isfinite(milliseconds) or milliseconds != int(milliseconds):
        raise InvalidValueError(
            f"Field {label!r} is a duration in whole milliseconds, and Quickbase would drop the "
            "fraction without saying so."
        )
    return int(milliseconds)


def _cell_value(item: Field, value: Any, row: int) -> Any:
    """Check file values here, since Quickbase accepts the mistakes and silently loses the file."""
    if item.type == FILE_FIELD_TYPE:
        if isinstance(value, FileUpload):
            return file_upload_to_api(value)
        raise InvalidValueError(
            f"Row {row} sets file field {item.label!r} to something other than a FileUpload. "
            "To keep the field empty, leave it out. To remove a file, use delete_file_version()."
        )
    if isinstance(value, FileUpload):
        raise InvalidValueError(f"Row {row} sends a file to {item.label!r}, not a file field.")
    if item.type == "duration":
        return duration_value(value, item.label)
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


def custom_key_refusal(fields: FieldIndex, key_field_id: int) -> InvalidValueError:
    """The error for insert() on a table where a row can change a record instead of creating one."""
    try:
        name = repr(fields.resolve(key_field_id).label)
    except UnknownFieldError:
        name = f"with ID {key_field_id}"
    return InvalidValueError(
        f"This table's key field is {name}, not Record ID#, so Quickbase would change a record "
        f"with the same key instead of creating one. Use upsert() with merge_on={key_field_id}, "
        "which does that on purpose."
    )


def require_field(rows: Sequence[Mapping[str, Any]], field_id: int, purpose: str) -> None:
    """Check every built row sets the field, since one row without it fails the whole request."""
    for index, row in enumerate(rows):
        if str(field_id) not in row:
            raise InvalidValueError(f"Row {index} doesn't set field {field_id}, {purpose}.")


def refuse_field(rows: Sequence[Mapping[str, Any]], field_id: int, purpose: str) -> None:
    for index, row in enumerate(rows):
        if str(field_id) in row:
            raise InvalidValueError(f"Row {index} sets field {field_id}, {purpose}.")


RowErrorReason = Literal["duplicate", "permission", "invalid_choice", "rule", "other"]
# Quickbase only gives text. Message wording isn't a promise, so unknown text counts as "other".
_ROW_ERROR_REASONS: tuple[tuple[RowErrorReason, re.Pattern[str]], ...] = (
    # A rule's message is the app builder's own text, so only the prefix can be matched, and it's
    # checked first so that a message mentioning permissions or choices isn't misread.
    ("rule", re.compile(r"^Table rule validation:()", re.I)),
    ("duplicate", re.compile(r"non-unique value for field with ID ['\"]?(\d+)", re.IGNORECASE)),
    ("invalid_choice", re.compile(r"field with ID ['\"]?(\d+)['\"]? has an invalid choice", re.I)),
    ("permission", re.compile(r"(?:don't have permission|insufficient permissions)()", re.I)),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RowError:
    """Why one row failed.

    Attributes:
        index: The row's position in the rows you passed, starting from 0.
        messages: Quickbase's wording, which can change, so use `reason` to decide what to do. For
            a `rule` failure, this holds the rule's message.
        reason: `duplicate` for a value already in a unique field, `permission` for a refusal by the
            token's role, `invalid_choice` for a value outside a field's choices, `rule` for a
            refusal by one of the table's custom data rules, and `other` for anything else,
            including wording this version doesn't recognize.
        field_id: The field Quickbase named, for `duplicate` and `invalid_choice`.
    """

    index: int
    messages: tuple[str, ...]
    reason: RowErrorReason = "other"
    field_id: int | None = None


def row_error(index: int, messages: tuple[str, ...]) -> RowError:
    """Classify a failed row from Quickbase's messages."""
    for message in messages:
        for reason, pattern in _ROW_ERROR_REASONS:
            found = pattern.search(message)
            if found:
                field_id = int(found.group(1)) if found.group(1) else None
                return RowError(index=index, messages=messages, reason=reason, field_id=field_id)
    return RowError(index=index, messages=messages)


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteResult:
    """What a write did. If rows fail, `WriteError.result` holds this, including what succeeded.

    Attributes:
        created_ids: The Record ID# of each new record, in the order of the rows that succeeded.
            Use `row_ids` to match them to your rows.
        updated_ids: The records whose values changed.
        unchanged_ids: The records that already held these values.
        errors: A `RowError` for each row that failed.
        row_ids: For `insert()`, the new Record ID# of each row, in the order of your rows, or
            None for a row that failed. It stops short at a batch that wasn't sent, or whose
            response can't be matched to its rows, so compare its length with your rows. Empty for
            `update()` and `upsert()`.
    """

    created_ids: tuple[int, ...] = ()
    updated_ids: tuple[int, ...] = ()
    unchanged_ids: tuple[int, ...] = ()
    errors: tuple[RowError, ...] = ()
    row_ids: tuple[int | None, ...] = ()


def write_result_summary(result: WriteResult) -> str:
    """Describe what failed. Quickbase's messages name field IDs rather than values."""
    first = result.errors[0]
    return (
        f"{len(result.errors)} row(s) failed. The first is row {first.index}: "
        f"{'; '.join(first.messages)} See `.result` for every row."
    )


def write_failure(result: WriteResult) -> WriteError:
    """The error for rows that failed, which is about permissions only if they all were."""
    summary = write_result_summary(result)
    if all(item.reason == "permission" for item in result.errors):
        return WritePermissionError(
            summary, result=result, status_code=207, method="POST", path="/records"
        )
    return WriteError(summary, result=result)


def refused_write(exc: PermissionDeniedError, result: WriteResult, start: int) -> WriteError:
    """The error for a request refused with a 403, which can follow batches that were written."""
    summary = str(exc)
    if start:
        summary = f"Writing stopped at row {start}, after earlier rows were written: {summary}"
    return WritePermissionError(
        summary,
        result=result,
        status_code=exc.status_code,
        method=exc.method,
        path=exc.path,
        message=exc.message,
        description=exc.description,
        request_id=exc.request_id,
    )


def _row_ids(
    result: WriteResult, created: Sequence[int], failed: set[int], offset: int, rows: int | None
) -> tuple[int | None, ...]:
    """Match an insert's new IDs to its rows. Quickbase lists them in the order of the rows that
    succeeded, so that only works when there's one for each of those rows."""
    if rows is None or len(result.row_ids) != offset or len(created) != rows - len(failed):
        return result.row_ids
    found = iter(created)
    return (*result.row_ids, *(None if row in failed else next(found) for row in range(rows)))


def merge_write_result(
    result: WriteResult, payload: Mapping[str, Any], offset: int, rows: int | None = None
) -> WriteResult:
    """Add one request's response. `offset` is the position of its first row, and `rows` is how
    many rows an insert sent, for matching new IDs to rows."""
    metadata = payload["metadata"]
    line_errors: Mapping[str, list[str]] = metadata.get("lineErrors") or {}
    errors = tuple(
        row_error(int(line) - 1 + offset, tuple(messages)) for line, messages in line_errors.items()
    )
    failed = {int(line) - 1 for line in line_errors}
    return replace(
        result,
        created_ids=(*result.created_ids, *metadata["createdRecordIds"]),
        updated_ids=(*result.updated_ids, *metadata["updatedRecordIds"]),
        unchanged_ids=(*result.unchanged_ids, *metadata["unchangedRecordIds"]),
        errors=(*result.errors, *errors),
        row_ids=_row_ids(result, metadata["createdRecordIds"], failed, offset, rows),
    )
