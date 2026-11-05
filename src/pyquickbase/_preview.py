"""Work out what a write would change. No network access.

A stored value and the value written for it can look different and mean the same, such as `5` and
`"5"`, or an email and a user. Each type is compared as Quickbase holds it, and whenever that can't
be certain, it counts as a change, so that a preview never hides one. See docs/api-notes.md for how
each type reads back.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ._models import RECORD_ID_FIELD, Field, FieldIndex, Record
from ._writes import FILE_FIELD_TYPE, require_id

# Records to read in one query. Quickbase's limit on a query's length isn't confirmed.
PREVIEW_BATCH_SIZE = 100
_NUMERIC_TYPES = frozenset({"numeric", "currency", "percent", "rating", "duration"})
_MISSING = object()


@dataclass(frozen=True, slots=True)
class FieldChange:
    """One field's value before and after a write, with an empty field as None.

    A file field shows the file's name, never its contents. `repr` hides both values, because they
    may be sensitive and reprs end up in logs.
    """

    before: Any = field(repr=False)
    after: Any = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordChange:
    """What a write would change on one record, as `{field label: FieldChange}`."""

    record_id: int
    fields: Mapping[str, FieldChange]

    def __repr__(self) -> str:
        return f"RecordChange(record_id={self.record_id!r}, fields={sorted(self.fields)!r})"


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdatePreview:
    """What `update()` would do if the records stayed as they are now.

    Attributes:
        changes: The records that would change, in the order of your rows.
        unchanged_ids: The records that already hold these values.
        missing_ids: The records that don't exist, or that the token can't see. `update()` would
            report these as failed rows.
    """

    changes: tuple[RecordChange, ...] = ()
    unchanged_ids: tuple[int, ...] = ()
    missing_ids: tuple[int, ...] = ()


def merge_rows(rows: Sequence[Mapping[str, Mapping[str, Any]]]) -> dict[int, dict[int, Any]]:
    """Group built rows by record, as `{record ID: {field ID: value}}`. A later row wins."""
    merged: dict[int, dict[int, Any]] = {}
    for row in rows:
        record_id = row[str(RECORD_ID_FIELD)]["value"]
        require_id("Record ID#", record_id)
        cells = merged.setdefault(record_id, {})
        for key, cell in row.items():
            if int(key) != RECORD_ID_FIELD:
                cells[int(key)] = cell["value"]
    return merged


def id_batches(ids: Sequence[int]) -> Iterator[Sequence[int]]:
    for start in range(0, len(ids), PREVIEW_BATCH_SIZE):
        yield ids[start : start + PREVIEW_BATCH_SIZE]


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _same_number(before: Any, after: Any) -> bool:
    if isinstance(before, bool) or isinstance(after, bool):
        return False
    try:
        return Decimal(str(before)) == Decimal(str(after))
    except InvalidOperation:
        return False


def _same_user(before: Any, after: Any) -> bool:
    if not isinstance(before, Mapping):
        return False
    wanted = after.get("id") if isinstance(after, Mapping) else after  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    if not isinstance(wanted, str):
        return False
    held: list[Any] = [before.get("email"), before.get("id")]  # pyright: ignore[reportUnknownMemberType]
    return wanted.casefold() in {item.casefold() for item in held if isinstance(item, str)}


def _same_users(before: Any, after: Any) -> bool:
    if not isinstance(before, list) or not isinstance(after, list) or len(before) != len(after):  # pyright: ignore[reportUnknownArgumentType]
        return False
    return all(any(_same_user(held, item) for held in before) for item in after)  # pyright: ignore[reportUnknownVariableType]


def _same_time(before: Any, after: Any) -> bool:
    """Compare to the second, because that's how a timestamp reads back."""
    if not isinstance(before, datetime) or not isinstance(after, str):
        return False
    try:
        written = datetime.fromisoformat(after)
    except ValueError:
        return False
    if written.tzinfo is None:
        return False
    return before.replace(microsecond=0) == written.replace(microsecond=0)


def _same_date(before: Any, after: Any) -> bool:
    if not isinstance(before, date) or not isinstance(after, str):
        return False
    try:
        return before == date.fromisoformat(after)
    except ValueError:
        return False


def is_same(item: Field, before: Any, after: Any) -> bool:
    """Whether writing `after` would leave a field that holds `before` as it is."""
    if item.type == FILE_FIELD_TYPE:
        return False  # An upload always adds a version.
    if item.type == "checkbox":
        # Clearing a checkbox unticks it. Anything else, such as "true", counts as a change.
        return (isinstance(after, bool) or after == "") and (before is True) == (after is True)
    if _is_empty(after):
        return _is_empty(before)
    if _is_empty(before):
        return False
    if item.type in _NUMERIC_TYPES:
        return _same_number(before, after)
    if item.type == "date":
        return _same_date(before, after)
    if item.type == "timestamp":
        return _same_time(before, after)
    if item.type == "user":
        return _same_user(before, after)
    if item.type == "multiuser":
        return _same_users(before, after)
    if item.type == "multitext":
        return (
            isinstance(before, list)
            and isinstance(after, list)
            and sorted(map(str, before)) == sorted(map(str, after))  # pyright: ignore[reportUnknownArgumentType]
        )
    if isinstance(after, bool | list | dict):
        return False
    return isinstance(before, str) and before == str(after)


def _shown(item: Field, value: Any, *, written: bool) -> Any:
    """A value as a preview shows it: a file's name, and None for anything empty."""
    if item.type == FILE_FIELD_TYPE:
        return _written_file_name(value) if written else _stored_file_name(value)
    if item.type == "checkbox" and value == "":
        return False
    return None if _is_empty(value) else value


def _written_file_name(value: Any) -> Any:
    return value.get("fileName") if isinstance(value, Mapping) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def _stored_file_name(value: Any) -> Any:
    """Only the name, as a stored file's value also holds who uploaded each version."""
    versions = value.get("versions") if isinstance(value, Mapping) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(versions, list) or not versions:
        return None
    usable: list[Mapping[str, Any]] = [item for item in versions if isinstance(item, Mapping)]  # pyright: ignore[reportUnknownVariableType]
    if not usable:
        return None
    newest = max(usable, key=lambda item: item.get("versionNumber") or 0)
    return newest.get("fileName")


def build_update_preview(
    rows: Mapping[int, Mapping[int, Any]], current: Mapping[int, Record], fields: FieldIndex
) -> UpdatePreview:
    """Compare what would be written with what the records hold."""
    changes: list[RecordChange] = []
    unchanged: list[int] = []
    missing: list[int] = []
    for record_id, cells in rows.items():
        record = current.get(record_id)
        if record is None:
            missing.append(record_id)
            continue
        changed: dict[str, FieldChange] = {}
        for field_id, after in cells.items():
            item = fields.resolve(field_id)
            before = record.get(field_id, _MISSING)
            if before is _MISSING or not is_same(item, before, after):
                changed[item.label] = FieldChange(
                    before=None if before is _MISSING else _shown(item, before, written=False),
                    after=_shown(item, after, written=True),
                )
        if changed:
            changes.append(RecordChange(record_id=record_id, fields=changed))
        else:
            unchanged.append(record_id)
    return UpdatePreview(
        changes=tuple(changes), unchanged_ids=tuple(unchanged), missing_ids=tuple(missing)
    )
