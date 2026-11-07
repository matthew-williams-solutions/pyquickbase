"""Describe a table operation as JSON Schema, and check a model's arguments. No network access.

Each operation is one entry in `_KINDS`: the fields it uses, its schema, and the check that turns a
model's arguments into a table call. Both come from the same lists of fields, so a model is never
offered something the check would refuse, or allowed something it wasn't offered. Adding an
operation means adding an entry, without touching the others.

The lists come from `BoundPolicy.offers()`, narrowed by the tool's own `fields`, so what's offered
is exactly what the policy allows. It's still checked on every request.
Error messages follow `_where_data.py`: they say where the mistake is and what's allowed, and don't
repeat the values they were given.
"""

import json
import math
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, cast, get_args

from ._bound import BoundPolicy
from ._codegen import _WRITE_TYPES  # pyright: ignore[reportPrivateUsage]
from ._decision import Decision, Named
from ._models import RECORD_ID_FIELD, Field, FieldIndex, ReportInfo
from ._query import NO_VALUE, Condition, Expression, FieldRef, Group
from ._read_request import ReadRequest
from ._summary import Period, PeriodUnit
from ._typed import is_writable
from ._where_data import case_hint, normalize_where, where_from_dict, where_schema
from ._writes import FileUpload
from .errors import (
    ConfigurationError,
    InvalidValueError,
    PolicyRefusedError,
    QueryError,
    QuickbaseError,
    UnknownFieldError,
    UnknownReportError,
)

OperationName = Literal[
    "query",
    "count",
    "summarize",
    "insert",
    "update",
    "delete",
    "preview_update",
    "preview_delete",
    "run_report",
    "run_summary_report",
    "run_formula",
    "download_file",
    "delete_file_version",
]
"""What an `Operation` does. Each runs the table or report method of the same name."""

DEFAULT_LIMIT = 20
DEFAULT_MAX_RECORDS = 100
DEFAULT_MAX_SUMMARY_RECORDS = 10_000
DEFAULT_MAX_ROWS = 100
_MAX_SORT_FIELDS = 5

_NUMERIC_TYPES = frozenset({"numeric", "currency", "percent", "rating", "duration"})
_DATE_TYPES = frozenset({"date", "timestamp"})
_RANGE_TYPES = _NUMERIC_TYPES | _DATE_TYPES | {"timeofday", "recordid"}
_UNGROUPABLE_TYPES = frozenset({"multitext", "multiuser", "file"})
# The types a generated class writes, and a file sent as text, but not users, as a model can't be
# offered them as choices.
_JSON_WRITE_TYPES = frozenset(_WRITE_TYPES) - {"user", "multiuser"}
_TEXT_TYPES = frozenset(
    {
        "text",
        "text-multiple-choice",
        "text-multi-line",
        "rich-text",
        "email",
        "url",
        "phone",
        "predecessor",
    }
)
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIME_OF_DAY = re.compile(r"[0-9]{2}:[0-9]{2}(:[0-9]{2})?")
_RECORD_ID_LABEL = "Record ID#"
_RECORD_ID_SCHEMA = {"type": "integer", "minimum": 1}


@dataclass(frozen=True, slots=True, kw_only=True)
class Allowed:
    """What an operation may use, after every limit on the client and the tool."""

    readable: tuple[Field, ...] = ()
    filterable: tuple[Field, ...] = ()
    sortable: tuple[Field, ...] = ()
    insertable: tuple[Field, ...] = ()
    updatable: tuple[Field, ...] = ()
    previewable: tuple[Field, ...] = ()
    downloadable: tuple[Field, ...] = ()
    file_deletable: tuple[Field, ...] = ()
    can_delete: bool = False
    reports: tuple[ReportInfo, ...] = ()
    summary_reports: tuple[ReportInfo, ...] = ()
    max_records: int | None = None
    can_run_formula: bool = True


def _labels(fields: Sequence[Field]) -> list[str]:
    return [item.label for item in fields]


def _glossary(fields: Sequence[Field], descriptions: Mapping[int, str]) -> str:
    entries = [
        f"{item.label}: {descriptions[item.id]}" for item in fields if item.id in descriptions
    ]
    return f" Fields: {' '.join(entries)}" if entries else ""


def _check_names(data: Mapping[str, Any], allowed: Sequence[str], required: Sequence[str]) -> None:
    unknown = set(data) - set(allowed)
    if unknown:
        raise QueryError(
            f"The arguments have a key this tool doesn't take. Use only: {', '.join(allowed)}."
        )
    missing = [name for name in required if name not in data]
    if missing:
        raise QueryError(f"The arguments need {', '.join(missing)}.")


def _whole(value: Any, name: str, *, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        top = "" if maximum is None else f" to {maximum}"
        raise QueryError(f"{name} must be a whole number from {minimum}{top}.")
    return value


def _field_names(value: Any, name: str, offered: Sequence[Field]) -> list[str]:
    labels = _labels(offered)
    if not isinstance(value, list) or not value:
        raise QueryError(f"{name} must be a list with at least one field.")
    items = cast(list[Any], value)
    for index, item in enumerate(items):
        if item not in labels:
            raise QueryError(
                f"{name}[{index}] isn't a field this tool may use there.{case_hint(item, labels)}"
            )
    if len(set(items)) != len(items):
        raise QueryError(f"{name} names a field more than once.")
    return cast(list[str], items)


_DIRECTIONS = {"asc": "asc", "ascending": "asc", "desc": "desc", "descending": "desc"}
# Keys a model writes for an order_by item's direction, other than direction itself.
_DIRECTION_KEYS = ("order", "dir", "sort_order")


def _sort_item(item: Any) -> Any:
    """An order_by item as `{"field": ..., "direction": ...}`, from the shapes models write: a
    bare label, `"order": "DESC"`, or `"descending": true`. Anything else is left to be refused."""
    if isinstance(item, str):
        return {"field": item, "direction": "asc"}
    if not isinstance(item, Mapping):
        return item
    entry = dict(cast(Mapping[str, Any], item))
    if "direction" not in entry:
        for key in _DIRECTION_KEYS:
            if key in entry:
                entry["direction"] = entry.pop(key)
                break
    for key, value in (("descending", "desc"), ("ascending", "asc")):
        if "direction" not in entry and isinstance(entry.get(key), bool):
            flag = entry.pop(key)
            entry["direction"] = value if flag else ("asc" if value == "desc" else "desc")
    direction = entry.get("direction")
    if isinstance(direction, str):
        entry["direction"] = _DIRECTIONS.get(direction.strip().casefold(), direction)
    return entry


def normalize_arguments(data: Mapping[str, Any], offered: Collection[str]) -> dict[str, Any]:
    """Arguments in the spelling the schema lists, from the ones models tend to write: `sort` for
    `order_by`, one item for a list, and the aliases of `normalize_where()`. Each alias means
    exactly one thing, and labels are never guessed, so nothing here changes what a call does."""
    found = dict(data)
    if "sort" in found and "order_by" not in found and "order_by" in offered:
        found["order_by"] = found.pop("sort")
    if "order_by" in found:
        order: Any = found["order_by"]
        if isinstance(order, str | Mapping):
            order = [order]
        if isinstance(order, list | tuple):
            found["order_by"] = [_sort_item(item) for item in cast(list[Any], order)]
    if "where" in found:
        found["where"] = normalize_where(found["where"])
    return found


def _sort(value: Any, offered: Sequence[Field]) -> list[int]:
    """Read `order_by` as field IDs, negative for descending, which no label can make ambiguous."""
    ids = {item.label: item.id for item in offered}
    if not isinstance(value, list) or not value or len(value) > _MAX_SORT_FIELDS:  # pyright: ignore[reportUnknownArgumentType]
        raise QueryError(f"order_by must be a list of 1 to {_MAX_SORT_FIELDS} fields.")
    found: list[int] = []
    for index, item in enumerate(cast(list[Any], value)):
        path = f"order_by[{index}]"
        if not isinstance(item, Mapping) or not set(item) <= {"field", "direction"}:  # pyright: ignore[reportUnknownArgumentType]
            raise QueryError(f'{path} must be {{"field": ..., "direction": "asc" or "desc"}}.')
        entry = cast(Mapping[str, Any], item)
        if entry.get("field") not in ids:
            hint = case_hint(entry.get("field"), ids)
            raise QueryError(f"{path}.field isn't a field this tool may sort by.{hint}")
        direction = entry.get("direction", "asc")
        if direction not in ("asc", "desc"):
            raise QueryError(f"{path}.direction must be asc or desc.")
        field_id = ids[entry["field"]]
        found.append(-field_id if direction == "desc" else field_id)
    return found


def _period(item: Mapping[str, Any], path: str, dates: Sequence[Field]) -> Period:
    if set(item) != {"field", "unit"}:
        raise QueryError(f'{path} must be a label, or {{"field": ..., "unit": ...}}.')
    if item["field"] not in _labels(dates):
        raise QueryError(f"{path}.field isn't a date field this tool may group by.")
    if item["unit"] not in get_args(PeriodUnit):
        raise QueryError(f"{path}.unit must be one of: {', '.join(get_args(PeriodUnit))}.")
    return Period(item["field"], item["unit"])


@dataclass(frozen=True, slots=True)
class _Converter:
    field: Field

    def __call__(self, value: Any, path: str) -> Any:
        """Turn a JSON value into what a write takes, or raise. None clears the field."""
        item = self.field
        kind = item.type
        if kind == "file":
            # Quickbase ignores null on a file field, so it can't clear one.
            return _file_upload(value, path)
        if value is None:
            return None
        if kind in _TEXT_TYPES:
            if not isinstance(value, str):
                raise InvalidValueError(f"{path} must be text.")
            if _closed_choices(item) and value not in item.choices:
                hint = case_hint(value, item.choices).replace("Labels", "Choices")
                raise InvalidValueError(f"{path} must be one of the field's choices.{hint}")
            return value
        if kind == "duration":
            whole = isinstance(value, int) or (isinstance(value, float) and value.is_integer())
            if isinstance(value, bool) or not whole:
                raise InvalidValueError(
                    f"{path} must be whole milliseconds, such as 3600000 for an hour."
                )
            return int(value)
        if kind in _NUMERIC_TYPES:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise InvalidValueError(f"{path} must be a number.")
            if isinstance(value, float) and not math.isfinite(value):
                raise InvalidValueError(f"{path} must be a finite number.")
            return value
        if kind == "checkbox":
            if not isinstance(value, bool):
                raise InvalidValueError(f"{path} must be true or false.")
            return value
        if kind == "date":
            return _date(value, path)
        if kind == "timestamp":
            return _timestamp(value, path)
        if kind == "timeofday":
            if not isinstance(value, str) or not _TIME_OF_DAY.fullmatch(value):
                raise InvalidValueError(f"{path} must be a time of day as HH:MM or HH:MM:SS.")
            return value
        if kind == "multitext":
            items = cast(list[Any], value) if isinstance(value, list) else None
            if items is None or not all(isinstance(entry, str) for entry in items):
                raise InvalidValueError(f"{path} must be a list of text.")
            return items
        raise InvalidValueError(f"{path} can't be written by this tool.")  # pragma: no cover


def _closed_choices(item: Field) -> bool:
    """Whether a field only takes its listed choices."""
    return item.type == "text-multiple-choice" and bool(item.choices) and not item.allow_new_choices


def _date(value: Any, path: str) -> date:
    try:
        if isinstance(value, str) and _ISO_DATE.fullmatch(value):
            return date.fromisoformat(value)
    except ValueError:
        pass
    raise InvalidValueError(f"{path} must be a date as YYYY-MM-DD.")


def _timestamp(value: Any, path: str) -> datetime:
    problem = f"{path} must be an ISO 8601 time with a time zone, like 2026-12-01T09:30:00Z."
    if not isinstance(value, str) or "T" not in value:
        raise InvalidValueError(problem)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise InvalidValueError(problem) from None
    if parsed.tzinfo is None:
        raise InvalidValueError(problem)
    return parsed


def _value_schema(item: Field, description: str | None) -> dict[str, Any]:
    kind = item.type
    schema: dict[str, Any]
    if kind in _TEXT_TYPES:
        schema = {"type": ["string", "null"]}
        if _closed_choices(item):
            schema = {"enum": [*item.choices, None]}
    elif kind == "duration":
        schema = {
            "type": ["integer", "null"],
            "description": "A length of time in whole milliseconds, so an hour is 3600000.",
        }
    elif kind == "percent":
        schema = {"type": ["number", "null"], "description": "A fraction, so 50% is 0.5."}
    elif kind in _NUMERIC_TYPES:
        schema = {"type": ["number", "null"]}
    elif kind == "checkbox":
        schema = {"type": ["boolean", "null"]}
    elif kind == "date":
        schema = {"type": ["string", "null"], "description": "A date, as YYYY-MM-DD."}
    elif kind == "timestamp":
        schema = {
            "type": ["string", "null"],
            "description": "A time, as ISO 8601 with a time zone, like 2026-12-01T09:30:00Z.",
        }
    elif kind == "timeofday":
        schema = {"type": ["string", "null"], "description": "A time of day, as HH:MM:SS."}
    elif kind == "file":
        schema = {
            "type": "object",
            "properties": {
                "file_name": {"type": "string", "minLength": 1},
                "text": {"type": "string"},
            },
            "required": ["file_name", "text"],
            "additionalProperties": False,
            "description": "A new version of the file, from its text.",
        }
    else:
        schema = {"type": ["array", "null"], "items": {"type": "string"}}
    if description:
        found = schema.get("description")
        schema["description"] = f"{description} {found}" if found else description
    return schema


def _file_upload(value: Any, path: str) -> FileUpload:
    shape = f'{path} must be {{"file_name": ..., "text": ...}}, with the file\'s text.'
    if not isinstance(value, Mapping) or set(value) != {"file_name", "text"}:  # pyright: ignore[reportUnknownArgumentType]
        raise InvalidValueError(shape)
    upload = cast(Mapping[str, Any], value)
    name, text = upload["file_name"], upload["text"]
    if not isinstance(name, str) or not name.strip() or not isinstance(text, str):
        raise InvalidValueError(shape)
    return FileUpload(name, text.encode("utf-8"))


def _whole_id(value: Any, path: str) -> int:
    if type(value) is not int or value < 1:
        raise InvalidValueError(f"{path} must be a positive whole number.")
    return value


def _array_of(labels: list[str], description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"enum": labels},
        "minItems": 1,
        "uniqueItems": True,
        "description": description,
    }


def _object(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    # Separates every part, so that changing one in a tool's schema can't change another.
    return json.loads(json.dumps(schema))


@dataclass(frozen=True, slots=True, kw_only=True)
class Context:
    """Everything an operation's schema and checks are built from."""

    allowed: Allowed
    descriptions: Mapping[int, str]
    report_descriptions: Mapping[str, str]
    limit: int
    max_rows: int
    formula: str | None
    scope: Expression | None = None
    scope_text: str = ""
    """The scope as a model reads it, such as `Billable is true`."""
    scope_labels: frozenset[str] = frozenset()
    """The labels of the fields the scope uses, which a write can't set."""
    require_preview: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class Call:
    """What to call, with checked arguments.

    `method` is a table method, or a report method when `report_id` is set. `record_ids` are the
    records a call names directly, which a scope has to be checked against before it runs.
    `most` is how many records its `where` may match, which is counted before it runs.
    """

    method: str
    arguments: dict[str, Any] = field(default_factory=dict[str, Any])
    report_id: str | None = None
    record_ids: tuple[int, ...] = ()
    most: int | None = None


# Parts of a schema, each with the check that reads it back.


def _where(ctx: Context, *, required: bool) -> dict[str, Any]:
    filterable = ctx.allowed.filterable
    if not filterable:
        return {}
    schema = where_schema(fields=filterable)
    limited = (
        f" Every record this tool reaches also matches: {ctx.scope_text}." if ctx.scope_text else ""
    )
    schema["description"] = (
        f"{schema['description']}{' Required.' if required else ''}{limited}"
        f"{_glossary(filterable, ctx.descriptions)}"
    )
    return {"where": schema}


def _read_where(
    ctx: Context, data: Any, scope: Expression | None, *, required: bool
) -> Expression | None:
    if data is None:
        if required:
            raise QueryError("where is required, and can't be empty.")
        return scope
    if not isinstance(data, Mapping):
        raise QueryError("where must be an object.")
    condition = where_from_dict(cast(Mapping[str, Any], data), fields=ctx.allowed.filterable)
    return condition if scope is None else scope & condition


def _select(ctx: Context) -> dict[str, Any]:
    readable = ctx.allowed.readable
    if not readable:
        return {}
    description = "The fields to return. Defaults to the table's usual fields."
    return {
        "select": _array_of(_labels(readable), description + _glossary(readable, ctx.descriptions))
    }


def _order_by(ctx: Context) -> dict[str, Any]:
    sortable = ctx.allowed.sortable
    if not sortable:
        return {}
    return {
        "order_by": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"enum": _labels(sortable)},
                    "direction": {"enum": ["asc", "desc"]},
                },
                "required": ["field"],
                "additionalProperties": False,
            },
            "minItems": 1,
            "maxItems": _MAX_SORT_FIELDS,
            "description": "The fields to sort by, first to last. Direction defaults to asc."
            + _glossary(sortable, ctx.descriptions),
        }
    }


def _delete_where(ctx: Context) -> dict[str, Any]:
    found = _where(ctx, required=True)
    if found:
        found["where"]["description"] += (
            f" A delete that matches more than {ctx.limit} records is refused, and deletes nothing."
        )
    return found


def _limit(ctx: Context, what: str = "records") -> dict[str, Any]:
    default = min(DEFAULT_LIMIT, ctx.limit)
    return {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": ctx.limit,
            "description": f"The most {what} to return. Defaults to {default}.",
        }
    }


def _read_limit(ctx: Context, arguments: Mapping[str, Any]) -> int:
    default = min(DEFAULT_LIMIT, ctx.limit)
    return _whole(arguments.get("limit", default), "limit", minimum=1, maximum=ctx.limit)


_SKIP = {
    "skip": {
        "type": "integer",
        "minimum": 0,
        "description": "How many to pass over first. Use the previous result's page.next_skip.",
    }
}


def _record_id(description: str) -> dict[str, Any]:
    return {"record_id": {**_RECORD_ID_SCHEMA, "description": description}}


def _file_field(fields: Sequence[Field], ctx: Context) -> dict[str, Any]:
    return {
        "field": {
            "enum": _labels(fields),
            "description": "The file field." + _glossary(fields, ctx.descriptions),
        }
    }


def _read_file_field(value: Any, fields: Sequence[Field]) -> str:
    if value not in _labels(fields):
        raise QueryError("field isn't a file field this tool may use.")
    return cast(str, value)


def _report(reports: Sequence[ReportInfo], ctx: Context) -> dict[str, Any]:
    entries = [
        f"{item.id}: {item.name}"
        + (f", {ctx.report_descriptions[item.id]}" if item.id in ctx.report_descriptions else "")
        for item in reports
    ]
    return {
        "report": {
            "enum": [item.id for item in reports],
            "description": f"The report's ID, or its name. Reports: {'; '.join(entries)}.",
        }
    }


def _find_report(value: Any, reports: Sequence[ReportInfo]) -> str | None:
    """The ID of the listed report that `value` names, as its ID, its ID as a number, or its exact
    name ignoring case, as models send all three. None if it names none of them."""
    ids = [item.id for item in reports]
    if isinstance(value, str) and value in ids:
        return value
    if isinstance(value, int) and not isinstance(value, bool) and str(value) in ids:
        return str(value)
    if isinstance(value, str):
        named = [item.id for item in reports if item.name.casefold() == value.casefold()]
        if len(named) > 1:
            raise QueryError(
                "Several reports this tool may run have that name. Use the report's ID instead."
            )
        if named:
            return named[0]
    return None


def _read_report(
    value: Any, reports: Sequence[ReportInfo], other: Sequence[ReportInfo], *, summary: bool
) -> str:
    found = _find_report(value, reports)
    if found is not None:
        return found
    try:
        elsewhere = _find_report(value, other)
    except QueryError:
        elsewhere = None
    if elsewhere is not None:
        # The ID is one the client may run anyway, so naming it reveals nothing.
        kind, use = (
            ("a table", "run_report") if summary else ("a summary or chart", "run_summary_report")
        )
        raise QueryError(
            f"Report {elsewhere} isn't a report this tool may run: it's {kind} report, so use the "
            f"{use} operation."
        )
    raise QueryError("report isn't a report this tool may run. Use an ID or name its schema lists.")


def _records(ctx: Context, *, update: bool, group: str) -> dict[str, Any]:
    offered: Sequence[Field] = getattr(ctx.allowed, group)
    properties: dict[str, Any] = {}
    if update:
        properties[_RECORD_ID_LABEL] = {**_RECORD_ID_SCHEMA, "description": "The record to change."}
    offered = [item for item in offered if item.label not in ctx.scope_labels]
    for item in offered:
        properties[item.label] = _value_schema(item, ctx.descriptions.get(item.id))
    record: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "minProperties": 2 if update else 1,
    }
    required = [_RECORD_ID_LABEL] if update else [item.label for item in offered if item.required]
    if required:
        record["required"] = required
    return {
        "records": {
            "type": "array",
            "items": record,
            "minItems": 1,
            "maxItems": ctx.max_rows,
            "description": "The records. A field set to null is cleared, except a file field."
            + _scope_note(ctx, update=update),
        }
    }


def _scope_note(ctx: Context, *, update: bool) -> str:
    if not ctx.scope_text:
        return ""
    if update:
        return (
            f" This tool only changes records that match its scope ({ctx.scope_text}), and can't "
            "change the fields the scope uses."
        )
    return (
        f" Every new record is made to match this tool's scope ({ctx.scope_text}), so the "
        "fields it uses are filled in and can't be set."
    )


def _read_records(ctx: Context, value: Any, *, update: bool, group: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise InvalidValueError("records must be a list with at least one record.")
    rows = cast(list[Any], value)
    if len(rows) > ctx.max_rows:
        raise InvalidValueError(
            f"records can have at most {ctx.max_rows} records. Send the rest separately."
        )
    offered: Sequence[Field] = getattr(ctx.allowed, group)
    converters = {item.label: _Converter(item) for item in offered}
    readable = {item.label for item in ctx.allowed.readable}
    built: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        path = f"records[{index}]"
        if not isinstance(row, Mapping) or not row:
            raise InvalidValueError(f"{path} must be an object with at least one field.")
        cells: dict[str, Any] = {}
        for key, item in cast(Mapping[str, Any], row).items():
            if update and key == _RECORD_ID_LABEL:
                cells[key] = _whole_id(item, f"{path}.{_RECORD_ID_LABEL}")
                continue
            converter = converters.get(key)
            if key in ctx.scope_labels:
                does = "no change may alter it" if update else "it's filled in on every new record"
                raise InvalidValueError(
                    f"{path}.{key} is fixed by this tool's scope ({ctx.scope_text}), so {does}. "
                    "Leave it out."
                )
            if converter is None and key in readable:
                # The label is one the tool offers to read, so naming it reveals nothing.
                raise InvalidValueError(
                    f"{path}.{key} can be read, but this tool may not write it. Leave it out."
                )
            if converter is None:
                raise InvalidValueError(
                    f"{path} has a field this tool may not write. Use only the fields its schema "
                    f"lists.{case_hint(key, [*converters, *readable])}"
                )
            cells[key] = converter(item, f"{path}.{key}")
        if update and _RECORD_ID_LABEL not in cells:
            raise InvalidValueError(f"{path} needs {_RECORD_ID_LABEL}, to say which record.")
        if update and len(cells) == 1:
            raise InvalidValueError(f"{path} doesn't change any field.")
        built.append(cells)
    return built


def _summary_fields(ctx: Context) -> dict[str, Sequence[Field]]:
    readable = ctx.allowed.readable
    numeric = [item for item in readable if item.type in _NUMERIC_TYPES]
    ranged = [item for item in readable if item.type in _RANGE_TYPES]
    return {
        "group_by": [item for item in readable if item.type not in _UNGROUPABLE_TYPES],
        "dates": [item for item in readable if item.type in _DATE_TYPES],
        "total": numeric,
        "average": numeric,
        "minimum": ranged,
        "maximum": ranged,
    }


def _summary(ctx: Context) -> dict[str, Any]:
    offered = _summary_fields(ctx)
    parts: dict[str, Any] = {}
    groupable, dates = offered["group_by"], offered["dates"]
    if groupable:
        choices: list[Any] = [{"enum": _labels(groupable)}]
        if dates:
            choices.append(
                {
                    "type": "object",
                    "properties": {
                        "field": {"enum": _labels(dates)},
                        "unit": {"enum": list(get_args(PeriodUnit))},
                    },
                    "required": ["field", "unit"],
                    "additionalProperties": False,
                }
            )
        parts["group_by"] = {
            "type": "array",
            "items": {"anyOf": choices} if len(choices) > 1 else choices[0],
            "minItems": 1,
            "description": "The fields to group by. Group a date by day, week, month, quarter, "
            'or year with {"field": ..., "unit": ...}. Without this, there\'s one group.'
            + _glossary(groupable, ctx.descriptions),
        }
    for name, what in (
        ("total", "add up"),
        ("average", "average"),
        ("minimum", "find the lowest of"),
        ("maximum", "find the highest of"),
    ):
        if offered[name]:
            parts[name] = _array_of(_labels(offered[name]), f"Fields to {what}.")
    return parts


def _read_summary(ctx: Context, arguments: Mapping[str, Any]) -> dict[str, Any]:
    offered = _summary_fields(ctx)
    found: dict[str, Any] = {"max_records": ctx.limit}
    if "group_by" in arguments:
        value = arguments["group_by"]
        if not isinstance(value, list) or not value:
            raise QueryError("group_by must be a list with at least one field.")
        groups: list[Any] = []
        for index, item in enumerate(cast(list[Any], value)):
            path = f"group_by[{index}]"
            if isinstance(item, Mapping):
                groups.append(_period(cast(Mapping[str, Any], item), path, offered["dates"]))
            elif item in _labels(offered["group_by"]):
                groups.append(item)
            else:
                hint = case_hint(item, _labels(offered["group_by"]))
                raise QueryError(f"{path} isn't a field this tool may group by.{hint}")
        found["group_by"] = groups
    for key in ("total", "average", "minimum", "maximum"):
        if key in arguments:
            found[key] = _field_names(arguments[key], key, offered[key])
    return found


# The operations.

Plan = Callable[[Context, Mapping[str, Any], Expression | None], Call]


@dataclass(frozen=True, slots=True, kw_only=True)
class Kind:
    """One operation: what it needs, its schema, and how its arguments become a call.

    `fields` names the groups of `Allowed` it offers, which descriptions must be for. `available`
    says why it can't run, or returns None if it can.
    """

    fields: tuple[str, ...]
    properties: Callable[[Context], dict[str, Any]]
    required: tuple[str, ...] = ()
    plan: Plan
    available: Callable[[Allowed, str | None], str | None]
    scoped: bool = True


def _always(allowed: Allowed, formula: str | None) -> str | None:
    return None


def _needs(group: str, reason: str) -> Callable[[Allowed, str | None], str | None]:
    def check(allowed: Allowed, formula: str | None) -> str | None:
        return None if getattr(allowed, group) else reason

    return check


def _plan_query(ctx: Context, arguments: Mapping[str, Any], scope: Expression | None) -> Call:
    found: dict[str, Any] = {
        "where": _read_where(ctx, arguments.get("where"), scope, required=False),
        "top": _read_limit(ctx, arguments),
        "skip": _whole(arguments.get("skip", 0), "skip", minimum=0),
    }
    if "select" in arguments:
        found["select"] = _field_names(arguments["select"], "select", ctx.allowed.readable)
    if "order_by" in arguments:
        found["order_by"] = _sort(arguments["order_by"], ctx.allowed.sortable)
    return Call(method="query_page", arguments=found)


def _plan_where(method: str, *, required: bool, capped: bool = False) -> Plan:
    def build(ctx: Context, arguments: Mapping[str, Any], scope: Expression | None) -> Call:
        where = _read_where(ctx, arguments.get("where"), scope, required=required)
        return Call(method=method, arguments={"where": where}, most=ctx.limit if capped else None)

    return build


def _plan_summarize(ctx: Context, arguments: Mapping[str, Any], scope: Expression | None) -> Call:
    found = _read_summary(ctx, arguments)
    found["where"] = _read_where(ctx, arguments.get("where"), scope, required=False)
    return Call(method="summarize", arguments=found)


def _plan_records(method: str, *, update: bool, group: str) -> Plan:
    def build(ctx: Context, arguments: Mapping[str, Any], scope: Expression | None) -> Call:
        rows = _read_records(ctx, arguments["records"], update=update, group=group)
        ids = tuple(sorted({row[_RECORD_ID_LABEL] for row in rows})) if update else ()
        return Call(method=method, arguments={"rows": rows}, record_ids=ids)

    return build


def _plan_preview_delete(
    ctx: Context, arguments: Mapping[str, Any], scope: Expression | None
) -> Call:
    found: dict[str, Any] = {
        "where": _read_where(ctx, arguments.get("where"), scope, required=True),
        "top": _read_limit(ctx, arguments),
    }
    if "select" in arguments:
        found["select"] = _field_names(arguments["select"], "select", ctx.allowed.readable)
    return Call(method="preview_delete", arguments=found)


def _no_scope(scope: Expression | None) -> None:
    if scope is not None:
        raise QueryError("A report runs with its own filter, so it can't take a scope.")


def _plan_report(ctx: Context, arguments: Mapping[str, Any], scope: Expression | None) -> Call:
    _no_scope(scope)
    report_id = _read_report(
        arguments["report"], ctx.allowed.reports, ctx.allowed.summary_reports, summary=False
    )
    found = {
        "top": _read_limit(ctx, arguments),
        "skip": _whole(arguments.get("skip", 0), "skip", minimum=0),
    }
    return Call(method="run_page", arguments=found, report_id=report_id)


def _plan_summary_report(
    ctx: Context, arguments: Mapping[str, Any], scope: Expression | None
) -> Call:
    _no_scope(scope)
    report_id = _read_report(
        arguments["report"], ctx.allowed.summary_reports, ctx.allowed.reports, summary=True
    )
    found = {"limit": _read_limit(ctx, arguments)}
    return Call(method="summary_rows", arguments=found, report_id=report_id)


def _plan_formula(ctx: Context, arguments: Mapping[str, Any], scope: Expression | None) -> Call:
    found: dict[str, Any] = {"formula": ctx.formula}
    ids: tuple[int, ...] = ()
    if "record_id" in arguments:
        found["record_id"] = _whole(arguments["record_id"], "record_id", minimum=1)
        ids = (found["record_id"],)
    elif scope is not None:
        raise QueryError("record_id is required, as this tool is limited to some records.")
    return Call(method="run_formula", arguments=found, record_ids=ids)


def _plan_download(ctx: Context, arguments: Mapping[str, Any], scope: Expression | None) -> Call:
    record_id = _whole(arguments["record_id"], "record_id", minimum=1)
    found: dict[str, Any] = {
        "record_id": record_id,
        "field": _read_file_field(arguments["field"], ctx.allowed.downloadable),
    }
    if "version" in arguments:
        found["version"] = _whole(arguments["version"], "version", minimum=1)
    return Call(method="download_file", arguments=found, record_ids=(record_id,))


def _plan_delete_file(ctx: Context, arguments: Mapping[str, Any], scope: Expression | None) -> Call:
    record_id = _whole(arguments["record_id"], "record_id", minimum=1)
    found = {
        "record_id": record_id,
        "field": _read_file_field(arguments["field"], ctx.allowed.file_deletable),
        "version": _whole(arguments["version"], "version", minimum=1),
    }
    return Call(method="delete_file_version", arguments=found, record_ids=(record_id,))


def _version(description: str) -> dict[str, Any]:
    return {"version": {"type": "integer", "minimum": 1, "description": description}}


_KINDS: dict[OperationName, Kind] = {
    "query": Kind(
        fields=("readable", "filterable", "sortable"),
        properties=lambda ctx: {
            **_where(ctx, required=False),
            **_select(ctx),
            **_order_by(ctx),
            **_limit(ctx),
            **_SKIP,
        },
        plan=_plan_query,
        available=_always,
    ),
    "count": Kind(
        fields=("filterable",),
        properties=lambda ctx: _where(ctx, required=False),
        plan=_plan_where("count", required=False),
        available=_always,
    ),
    "summarize": Kind(
        fields=("readable", "filterable"),
        properties=lambda ctx: {**_where(ctx, required=False), **_summary(ctx)},
        plan=_plan_summarize,
        available=_always,
    ),
    "insert": Kind(
        fields=("insertable",),
        properties=lambda ctx: _records(ctx, update=False, group="insertable"),
        required=("records",),
        plan=_plan_records("insert", update=False, group="insertable"),
        available=_needs("insertable", "it may not insert records into this table"),
    ),
    "update": Kind(
        fields=("updatable",),
        properties=lambda ctx: _records(ctx, update=True, group="updatable"),
        required=("records",),
        plan=_plan_records("update", update=True, group="updatable"),
        available=_needs("updatable", "it may not change any field of this table"),
    ),
    "preview_update": Kind(
        fields=("previewable",),
        properties=lambda ctx: _records(ctx, update=True, group="previewable"),
        required=("records",),
        plan=_plan_records("preview_update", update=True, group="previewable"),
        available=_needs("previewable", "it may not both read and change any field of this table"),
    ),
    "delete": Kind(
        fields=("filterable",),
        properties=_delete_where,
        required=("where",),
        plan=_plan_where("delete", required=True, capped=True),
        available=lambda allowed, formula: (
            None
            if allowed.can_delete and allowed.filterable
            else "it may not delete records from this table, or filter on any field"
        ),
    ),
    "preview_delete": Kind(
        fields=("readable", "filterable"),
        properties=lambda ctx: {**_where(ctx, required=True), **_select(ctx), **_limit(ctx)},
        required=("where",),
        plan=_plan_preview_delete,
        available=_needs("filterable", "it may not filter on any field of this table"),
    ),
    "run_report": Kind(
        fields=(),
        properties=lambda ctx: {
            **_report(ctx.allowed.reports, ctx),
            **_limit(ctx),
            **_SKIP,
        },
        required=("report",),
        plan=_plan_report,
        available=_needs("reports", "it may not run any table report on this table"),
        scoped=False,
    ),
    "run_summary_report": Kind(
        fields=(),
        properties=lambda ctx: {
            **_report(ctx.allowed.summary_reports, ctx),
            **_limit(ctx, "rows"),
        },
        required=("report",),
        plan=_plan_summary_report,
        available=_needs("summary_reports", "it may not run any summary report on this table"),
        scoped=False,
    ),
    "run_formula": Kind(
        fields=(),
        properties=lambda ctx: _record_id(
            "The record the formula reads. Needed if the formula uses fields."
        ),
        plan=_plan_formula,
        available=lambda allowed, formula: (
            "the client's policy doesn't allow running formulas, as one can read any table"
            if not allowed.can_run_formula
            else None
            if formula
            else "run_formula needs formula, the text your code chooses"
        ),
    ),
    "download_file": Kind(
        fields=("downloadable",),
        properties=lambda ctx: {
            **_record_id("The record that holds the file."),
            **_file_field(ctx.allowed.downloadable, ctx),
            **_version("The version to download, starting at 1. Defaults to the latest."),
        },
        required=("record_id", "field"),
        plan=_plan_download,
        available=_needs("downloadable", "it may not download from any file field of this table"),
    ),
    "delete_file_version": Kind(
        fields=("file_deletable",),
        properties=lambda ctx: {
            **_record_id("The record that holds the file."),
            **_file_field(ctx.allowed.file_deletable, ctx),
            **_version("The version to delete, starting at 1."),
        },
        required=("record_id", "field", "version"),
        plan=_plan_delete_file,
        available=_needs("file_deletable", "it may not delete files from this table"),
    ),
}


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """One operation's schema and the checks that match it, for one table."""

    name: OperationName
    kind: Kind
    context: Context
    schema: dict[str, Any]

    def normalize(self, data: Any) -> dict[str, Any]:
        """The arguments in the spelling the schema lists. See `normalize_arguments()`."""
        if not isinstance(data, Mapping):
            raise QueryError("The arguments must be an object.")
        return normalize_arguments(cast(Mapping[str, Any], data), self.schema["properties"])

    def plan(self, data: Any, scope: Expression | None) -> Call:
        """Check a model's arguments, and return the call to make. Nothing is sent."""
        if not isinstance(data, Mapping):
            raise QueryError("The arguments must be an object.")
        arguments = self.normalize(data)
        _check_names(arguments, list(self.schema["properties"]), self.kind.required)
        return self.kind.plan(self.context, arguments, scope)


def resolve_descriptions(
    descriptions: Mapping[FieldRef, str] | None, offered: Sequence[Field]
) -> dict[int, str]:
    """Key descriptions by field ID, refusing one that isn't for a field the operation uses."""
    if descriptions is None:
        return {}
    if not isinstance(descriptions, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise QueryError("descriptions takes a dictionary of {label or ID: text}.")
    index = FieldIndex(offered)
    found: dict[int, str] = {}
    for key, text in descriptions.items():
        if not isinstance(text, str) or not text.strip():  # pyright: ignore[reportUnnecessaryIsInstance]
            raise QueryError("Each description must be text that isn't empty.")
        try:
            item = index.resolve(key)
        except UnknownFieldError:
            raise UnknownFieldError(
                f"descriptions has {key!r}, which isn't one of this operation's fields, or this "
                "client can't see it."
            ) from None
        found[item.id] = text.strip()
    return found


def allowed_for(
    fields: Sequence[Field],
    reports: Sequence[ReportInfo],
    *,
    table_id: str,
    key_field_id: int | None,
    policy: BoundPolicy | None,
    wanted: Sequence[FieldRef] | None,
    wanted_reports: Sequence[str] | None,
) -> Allowed:
    """Work out what an operation may use, from the client's policy and the tool's own fields.

    Every list comes from `BoundPolicy.offers()`, which asks the policy's one evaluator about each
    field and verb, so a field is offered exactly when a request with it would be allowed.
    """
    if wanted is not None:
        if isinstance(wanted, str):
            raise QueryError("fields takes a list of labels or IDs, not a single string.")
        index = FieldIndex(fields)
        ids = {index.resolve(ref).id for ref in wanted}
        if not ids:
            raise QueryError("fields can't be empty.")
        fields = [item for item in fields if item.id in ids]
    offered = _offered_reports(reports, wanted_reports)
    runnable = [item for item in offered if not item.incomplete]
    writable = {item.id for item in writable_fields(fields)}
    if policy is None:
        readable = tuple(fields)
        result = Allowed(
            readable=readable,
            filterable=readable,
            sortable=readable,
            insertable=tuple(item for item in fields if item.id in writable),
            updatable=tuple(item for item in fields if item.id in writable),
            downloadable=tuple(item for item in fields if item.type == "file"),
            file_deletable=tuple(item for item in fields if item.type == "file"),
            can_delete=True,
            reports=tuple(runnable),
        )
    else:
        found = policy.offers_for(
            table_id,
            [(item, item.type) for item in fields],
            lambda item: item.id,
            runnable,
            lambda item: (item.id, "table" if item.type == "table" else "summary"),
        )
        result = Allowed(
            readable=found.readable,
            filterable=found.filterable,
            sortable=found.sortable,
            insertable=tuple(item for item in found.insertable if item.id in writable),
            updatable=tuple(item for item in found.updatable if item.id in writable),
            downloadable=found.downloadable,
            file_deletable=found.file_deletable,
            can_delete=found.can_delete,
            reports=found.reports,
            max_records=found.max_records,
            can_run_formula=policy.decide(
                ReadRequest(action="formula", table_id=table_id, method="POST", path="/formula/run")
            ).allowed,
        )
    insertable = result.insertable
    if key_field_id not in (None, RECORD_ID_FIELD):
        insertable = ()  # insert() refuses a table whose key field isn't Record ID#.
    shown = {item.id for item in result.readable}
    return Allowed(
        readable=result.readable,
        filterable=result.filterable,
        sortable=result.sortable,
        insertable=insertable,
        updatable=result.updatable,
        previewable=tuple(item for item in result.updatable if item.id in shown),
        downloadable=result.downloadable,
        file_deletable=result.file_deletable,
        can_delete=result.can_delete,
        reports=tuple(item for item in result.reports if item.type == "table"),
        summary_reports=tuple(item for item in result.reports if item.type in ("summary", "chart")),
        max_records=result.max_records,
        can_run_formula=result.can_run_formula,
    )


def _offered_reports(
    reports: Sequence[ReportInfo], wanted: Sequence[str] | None
) -> list[ReportInfo]:
    if wanted is None:
        return list(reports)
    if isinstance(wanted, str):
        raise QueryError("reports takes a list of report IDs, not a single string.")
    by_id = {item.id: item for item in reports}
    missing = [ref for ref in wanted if ref not in by_id]
    if missing:
        raise UnknownReportError(
            f"This table has no report with ID {missing[0]!r}, or this client can't see it. Use "
            "report IDs, as names can repeat."
        )
    return [by_id[ref] for ref in wanted]


def writable_fields(fields: Sequence[Field]) -> list[Field]:
    """Fields a tool can write from JSON: writable, and of a type JSON can carry."""
    return [item for item in fields if is_writable(item) and item.type in _JSON_WRITE_TYPES]


def unavailable(name: OperationName, allowed: Allowed, formula: str | None) -> str | None:
    """Why the operation can't run with these limits, or None if it can."""
    return _KINDS[name].available(allowed, formula)


def make_spec(
    name: OperationName,
    allowed: Allowed,
    *,
    descriptions: Mapping[FieldRef, str] | None,
    report_descriptions: Mapping[str, str],
    max_records: int | None,
    max_rows: int,
    formula: str | None,
    scope: Expression | None = None,
    scope_text: str = "",
    scope_labels: frozenset[str] = frozenset(),
    require_preview: bool = False,
) -> OperationSpec:
    kind = _KINDS[name]
    if require_preview and name not in ("update", "delete"):
        raise ConfigurationError("require_preview is only for update and delete.")
    if formula is not None and name != "run_formula":
        raise ConfigurationError("formula is only for run_formula.")
    if formula is not None and (not isinstance(formula, str) or not formula.strip()):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ConfigurationError("formula must be the formula's text.")
    default = DEFAULT_MAX_SUMMARY_RECORDS if name == "summarize" else DEFAULT_MAX_RECORDS
    limit = _whole(default if max_records is None else max_records, "max_records", minimum=1)
    if allowed.max_records is not None and name != "summarize":
        limit = min(limit, allowed.max_records)
    _whole(max_rows, "max_rows", minimum=1)
    offered = {item.id: item for group in kind.fields for item in getattr(allowed, group)}
    context = Context(
        allowed=allowed,
        descriptions=resolve_descriptions(descriptions, list(offered.values())),
        report_descriptions=report_descriptions,
        limit=limit,
        max_rows=max_rows,
        formula=formula,
        scope=scope,
        scope_text=scope_text,
        scope_labels=scope_labels,
        require_preview=require_preview,
    )
    properties, required = kind.properties(context), kind.required
    if require_preview:
        properties = {**properties, "confirmation": _confirmation_schema(name)}
        required = (*required, "confirmation")
    schema = _object(properties, required)
    return OperationSpec(name, kind, context, schema)


def _confirmation_schema(name: str) -> dict[str, Any]:
    return {
        "type": "string",
        "description": (
            f"The confirmation from preview_{name}, called with exactly these arguments, after the "
            "person approved what it showed. It lasts 15 minutes."
        ),
    }


_SCOPE_WORDS = {
    "EX": "is",
    "XEX": "is not",
    "LT": "is less than",
    "LTE": "is at most",
    "GT": "is more than",
    "GTE": "is at least",
    "BF": "is before",
    "OBF": "is on or before",
    "AF": "is after",
    "OAF": "is on or after",
    "CT": "contains",
    "XCT": "doesn't contain",
    "SW": "starts with",
    "XSW": "doesn't start with",
    "HAS": "has",
    "XHAS": "doesn't have",
    "TV": "is",
    "XTV": "is not",
}


def describe_scope(scope: Expression, index: FieldIndex) -> str:
    """A scope in words a model reads, such as `Billable is true and Team is "Platform"`."""
    if isinstance(scope, Group):
        joiner = " and " if scope.joiner == "AND" else " or "
        return joiner.join(
            f"({describe_scope(part, index)})"
            if isinstance(part, Group)
            else describe_scope(part, index)
            for part in scope.parts
        )
    if not isinstance(scope, Condition):
        raise QueryError("scope must be a condition built with F.")
    label = index.resolve(scope.field).label
    words = _SCOPE_WORDS.get(scope.operator, scope.operator)
    value = scope.original
    if value is NO_VALUE:
        shown = json.dumps(scope.value)
    elif value is None:
        return f"{label} {'is empty' if scope.operator in ('EX', 'TV') else 'is not empty'}"
    elif isinstance(value, bool):
        shown = "true" if value else "false"
    elif isinstance(value, int | float | Decimal):
        shown = str(value)
    else:
        shown = json.dumps(value if isinstance(value, str) else str(value), ensure_ascii=False)
    return f"{label} {words} {shown}"


def check_scope(
    name: OperationName, scope: Expression, index: FieldIndex, allowed: Allowed
) -> frozenset[str]:
    """Check a scope when an operation is made, and return the labels of its fields."""
    if not is_scoped(name):
        raise ConfigurationError(f"{name} runs with its own filter, so it can't take a scope.")
    labels = frozenset(index.resolve(item.field).label for item in _scope_conditions(scope))
    if name == "insert":
        if _scope_values(scope, index) is None:
            raise ConfigurationError(
                "An insert fills the scope's fields into each new record, so its scope must be "
                "only == conditions joined with &."
            )
        missing = sorted(labels - {item.label for item in allowed.insertable})
        if missing:
            raise ConfigurationError(
                f"An insert fills in {', '.join(missing)} from the scope, but the client's policy "
                "doesn't let it set them. Allow them with allow.insert()."
            )
    return labels


def is_scoped(name: OperationName) -> bool:
    """Whether the operation can be limited to a scope."""
    return _KINDS[name].scoped


def operation_names() -> tuple[str, ...]:
    return get_args(OperationName)


def copy_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(schema))


_WRITE_OPERATIONS = frozenset({"insert", "update", "delete", "delete_file_version"})


def refused_operation(
    name: OperationName, reason: str, *, limited: bool, table: Named | None = None
) -> QuickbaseError:
    """The error for an operation that can't run at all: the policy's refusal when there is one."""
    if not limited or (name == "run_formula" and "policy" not in reason):
        return ConfigurationError(f"This client can't offer {name}: {reason}.")
    verb = {
        "insert": "insert",
        "update": "update",
        "preview_update": "update",
        "delete": "delete",
        "delete_file_version": "delete_file",
        "download_file": "download",
        "run_report": "report",
        "run_summary_report": "report",
        "run_formula": "run_formula",
    }.get(name, "read")
    return PolicyRefusedError(
        Decision(
            allowed=False,
            verb=verb,  # pyright: ignore[reportArgumentType]
            reason=f"This client can't offer {name}: {reason}",
            table=table,
        )
    )


_SCOPED_WRITES = frozenset({"insert", "update", "preview_update"})


def _scope_conditions(scope: Expression) -> list[Condition]:
    if isinstance(scope, Condition):
        return [scope]
    if isinstance(scope, Group):
        return [item for part in scope.parts for item in _scope_conditions(part)]
    raise QueryError("scope must be a condition built with F.")


def _scope_values(scope: Expression, index: FieldIndex) -> dict[int, Any] | None:
    """What a new record must hold to match the scope, or None if the scope isn't only `==`
    conditions joined with `&`, which a record can't be made to match by filling in fields."""
    if isinstance(scope, Group) and scope.joiner != "AND":
        return None
    values: dict[int, Any] = {}
    for item in _scope_conditions(scope):
        if item.operator != "EX" or item.original is NO_VALUE:
            return None
        field_id = index.resolve(item.field).id
        if field_id in values and values[field_id] != item.original:
            return None
        values[field_id] = item.original
    return values


def scope_rows(call: Call, scope: Expression | None, index: FieldIndex) -> Call:
    """Keep a write inside its scope: refuse rows that set a field the scope uses, as that could
    move a record out of it, and fill those fields into new records from an `==` scope."""
    if scope is None or call.method not in _SCOPED_WRITES:
        return call
    fixed = {index.resolve(item.field).id for item in _scope_conditions(scope)}
    rows: list[dict[str | int, Any]] = call.arguments["rows"]
    for position, row in enumerate(rows):
        for key in row:
            if key != _RECORD_ID_LABEL and index.resolve(key).id in fixed:
                raise QueryError(
                    f"records[{position}] sets {key!r}, which this tool's scope fixes, so it can't "
                    "be written. Leave it out."
                )
    if call.method != "insert":
        return call
    values = _scope_values(scope, index)
    if values is None:
        raise QueryError(
            "This tool is limited to a scope that a new record can't be made to match, so it "
            "can't insert records."
        )
    filled = [{**row, **values} for row in rows]
    return Call(method=call.method, arguments={**call.arguments, "rows": filled})
