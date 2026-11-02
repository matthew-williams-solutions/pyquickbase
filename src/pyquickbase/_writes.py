"""Prepare records for writing and interpret the results. No network access.

Quickbase silently ignores a null value, reports failed rows inside a successful response, and
creates a record when a merge value doesn't match. See docs/api-notes.md for the tested behavior.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ._models import FieldIndex
from .errors import InvalidValueError

RowInput = Mapping[str | int, Any]


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


def build_rows(rows: Iterable[RowInput], fields: FieldIndex) -> list[dict[str, dict[str, Any]]]:
    """Turn `{label or ID: value}` rows into Quickbase's `{"6": {"value": ...}}` form."""
    built: list[dict[str, dict[str, Any]]] = []
    for index, row in enumerate(rows):
        if not row:
            raise InvalidValueError(f"Row {index} has no fields.")
        cells: dict[str, dict[str, Any]] = {}
        for ref, value in row.items():
            key = str(fields.resolve(ref).id)
            if key in cells:
                raise InvalidValueError(f"Row {index} sets field {key} more than once.")
            cells[key] = {"value": to_api_value(value)}
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
