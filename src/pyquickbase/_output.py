"""Turn results and errors into plain data for a model's tool call. No network access.

Any tool-calling framework can send a dictionary of JSON types, so that's all these return. Results
use the same shapes and value formats whatever the call, and errors carry a fixed `code`, so a model
can tell what went wrong, whether anything changed, and what to do next, without parsing a message.

Values come from records, so they're data, not instructions. Error messages are the SDK's own,
which don't repeat the values they were given, except for an `APIError`, which keeps Quickbase's
wording so a model can fix a request Quickbase refused.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal, cast

from ._models import Field, FileDownload, FormulaResult, Page, Record, ReportInfo, TableInfo
from ._preview import UpdatePreview
from ._summary import SummaryRow
from ._typed import TypedPage, TypedRecord
from ._writes import RowError, WriteResult
from .errors import (
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    InvalidResponseError,
    InvalidValueError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    QueryError,
    QuickbaseError,
    RateLimitError,
    ReadRefusedError,
    RequestRefusedError,
    RequestTimeoutError,
    SchemaMismatchError,
    ServerError,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    UnsupportedReportError,
    WriteError,
    WritePermissionError,
    WriteRefusedError,
)

ToolErrorCode = Literal[
    "configuration",
    "invalid_query",
    "invalid_value",
    "read_refused",
    "write_refused",
    "refused",
    "unknown_table",
    "unknown_field",
    "unknown_report",
    "unsupported_report",
    "schema_mismatch",
    "partial_write",
    "write_failed",
    "write_permission",
    "authentication",
    "permission_denied",
    "not_found",
    "bad_request",
    "rate_limited",
    "server_error",
    "api_error",
    "timeout",
    "network",
    "invalid_response",
    "error",
    "internal",
]
"""The `code` of a `tool_error()`. See the table in the agents guide for what each one means."""

Changed = Literal["none", "some", "unknown"]

# The most specific class wins, so subclasses come before their bases.
_CODES: tuple[tuple[type[BaseException], ToolErrorCode], ...] = (
    (WritePermissionError, "write_permission"),
    (WriteError, "write_failed"),
    (ReadRefusedError, "read_refused"),
    (WriteRefusedError, "write_refused"),
    (RequestRefusedError, "refused"),
    (ConfigurationError, "configuration"),
    (QueryError, "invalid_query"),
    (InvalidValueError, "invalid_value"),
    (UnknownTableError, "unknown_table"),
    (UnknownFieldError, "unknown_field"),
    (UnknownReportError, "unknown_report"),
    (UnsupportedReportError, "unsupported_report"),
    (SchemaMismatchError, "schema_mismatch"),
    (AuthenticationError, "authentication"),
    (PermissionDeniedError, "permission_denied"),
    (NotFoundError, "not_found"),
    (BadRequestError, "bad_request"),
    (RateLimitError, "rate_limited"),
    (ServerError, "server_error"),
    (APIError, "api_error"),
    (RequestTimeoutError, "timeout"),
    (NetworkError, "network"),
    (InvalidResponseError, "invalid_response"),
    (QuickbaseError, "error"),
)
_HINTS: dict[ToolErrorCode, str] = {
    "configuration": "The tool is set up wrongly. Tell the user, as changing the call won't help.",
    "invalid_query": "Fix the filter or arguments as the message says, then try again.",
    "invalid_value": "Fix the values as the message says, then try again.",
    "read_refused": "This tool may not read that. Ask for less, as the message says, or tell "
    "the user.",
    "write_refused": "This tool may not make that change. Don't try it another way. Tell the user.",
    "refused": "This tool may not do that. Tell the user.",
    "unknown_table": "Use a table name this tool lists. It may also be hidden from this tool.",
    "unknown_field": "Use a field this tool lists. It may also be hidden from this tool.",
    "unknown_report": "Use a report this tool lists, by ID if several share a name.",
    "unsupported_report": "This report can't be read this way. Use another report or a query.",
    "schema_mismatch": "The table has changed since the tool was built. Tell the user.",
    "partial_write": "Some records were changed. Fix the failed rows and send only those again.",
    "write_failed": "Nothing was changed. Fix the failed rows, then try again.",
    "write_permission": "The user's role doesn't allow this change. Tell the user.",
    "authentication": "Quickbase didn't accept the token, or the app or table ID is wrong. Tell "
    "the user.",
    "permission_denied": "The user's role doesn't allow this. Tell the user.",
    "not_found": "That record, file, or version doesn't exist. Check the ID.",
    "bad_request": "Quickbase refused the request. Fix it as the message says, then try again.",
    "rate_limited": "Quickbase is limiting requests. Wait, then try again.",
    "server_error": "Quickbase failed. Check whether a change was made before trying again.",
    "api_error": "Quickbase refused the request. Tell the user if the message doesn't say why.",
    "timeout": "There was no answer in time. Check whether a change was made before trying again.",
    "network": "Quickbase couldn't be reached. Check whether a change was made before trying "
    "again.",
    "invalid_response": "Quickbase's answer couldn't be read. Check whether a change was made "
    "before trying again.",
    "error": "The request failed. Tell the user.",
    "internal": "The tool failed. Tell the user, as trying again won't help.",
}
_RETRYABLE = frozenset({"rate_limited", "server_error", "timeout", "network"})
_UNCERTAIN = (NetworkError, ServerError, InvalidResponseError)
_INTERNAL_MESSAGE = "An unexpected error occurred in the tool."
_MAX_MESSAGE = 2000


@dataclass(slots=True)
class _State:
    compact: bool
    max_text: int | None
    shortened: bool = False


def _code(error: BaseException) -> ToolErrorCode:
    for cls, code in _CODES:
        if isinstance(error, cls):
            return code
    return "internal"


def _write_changed(error: WriteError) -> Changed:
    result = error.result
    wrote = isinstance(result, WriteResult) and bool(result.created_ids or result.updated_ids)
    # A later batch that failed this way may have been written.
    if isinstance(error.__cause__, _UNCERTAIN):
        return "unknown"
    return "some" if wrote else "none"


def _changed(error: BaseException) -> Changed:
    if isinstance(error, QuickbaseError) and error._changed:  # pyright: ignore[reportPrivateUsage]
        return "some"
    if isinstance(error, WriteError):
        return _write_changed(error)
    if isinstance(error, _UNCERTAIN) or not isinstance(error, QuickbaseError):
        return "unknown"
    return "none"


def _row_error(item: RowError) -> dict[str, Any]:
    return {
        "index": item.index,
        "kind": item.kind,
        "field_id": item.field_id,
        "messages": list(item.messages),
    }


def _written(result: WriteResult) -> dict[str, list[int]]:
    return {
        "created_ids": list(result.created_ids),
        "updated_ids": list(result.updated_ids),
        "unchanged_ids": list(result.unchanged_ids),
    }


def tool_error(error: BaseException) -> dict[str, Any]:
    """Describe an exception for a model, as the result of its tool call.

    ```python
    try:
        return tool_result(await tasks.query_page(where=where, top=50))
    except QuickbaseError as error:
        return tool_error(error)
    ```

    The result is `{"ok": false, "error": {...}}`, which `json.dumps()` always accepts. `error`
    holds `code`, `message`, `hint`, `retryable`, and `changed`, and more for some codes:

    - `code` is one of `ToolErrorCode`, which doesn't change between 1.x versions, unlike the
      wording of `message`.
    - `hint` says what the model should do next, in fixed words for each code.
    - `retryable` is true when the same call can succeed later, such as after a rate limit or a
      timeout. `retry_after` is added when Quickbase said how long to wait.
    - `changed` is `none` if nothing was changed, `some` if part of a write was, and `unknown` if
      a write may have been applied, such as after a timeout. A model should check the data
      before repeating a call whose `changed` isn't `none`.
    - A `WriteError` adds `written`, with the IDs that were created, updated, and left unchanged,
      and `failed_rows`, with each row's `index`, `kind`, `field_id`, and Quickbase's `messages`.
    - An `APIError` adds `status`, the HTTP status.

    An exception from outside the SDK gets the code `internal` and a message that says nothing
    about it, so a bug's details and any values in it don't reach the model. Log it yourself.

    Args:
        error: The exception, usually a `QuickbaseError`.

    Returns:
        The error, as plain data.
    """
    code = _code(error)
    if code == "write_failed" and _changed(error) != "none":
        code = "partial_write"
    message = _INTERNAL_MESSAGE if code == "internal" else str(error)[:_MAX_MESSAGE]
    found: dict[str, Any] = {
        "code": code,
        "message": message,
        "hint": _HINTS[code],
        "retryable": code in _RETRYABLE,
        "changed": _changed(error),
    }
    if isinstance(error, RateLimitError) and error.retry_after is not None:
        found["retry_after"] = error.retry_after
    if isinstance(error, APIError) and error.status_code:
        found["status"] = error.status_code
    if isinstance(error, WriteError) and isinstance(error.result, WriteResult):
        found["written"] = _written(error.result)
        found["failed_rows"] = [_row_error(item) for item in error.result.errors]
    return {"ok": False, "error": found}


def _decimal(value: Decimal) -> float | str | None:
    if not value.is_finite():
        return None
    number = float(value)
    return number if Decimal(repr(number)) == value else str(value)


def _text(value: str, state: _State) -> str | None:
    if value == "":
        return None
    limit = state.max_text
    if limit is None or len(value) <= limit:
        return value
    state.shortened = True
    return f"{value[:limit]}… ({len(value) - limit} more characters)"


def _file(value: Mapping[str, Any], state: _State) -> Any:
    versions: Sequence[Any] = value.get("versions") or ()
    if not versions:
        return None
    if state.compact:
        latest: Any = versions[-1]
        name = (
            cast(Mapping[str, Any], latest).get("fileName") if isinstance(latest, Mapping) else None
        )
        return _text(name, state) if isinstance(name, str) else None
    return {"url": value.get("url"), "versions": [_value(item, state) for item in versions]}


def _value(value: Any, state: _State) -> Any:
    """Write one value the same way everywhere. Every kind of empty value becomes None."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _text(value, state)
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, list | tuple):
        items = cast(Sequence[Any], value)
        return [_value(item, state) for item in items] if items else None
    if isinstance(value, Mapping):
        found = cast(Mapping[Any, Any], value)
        if "versions" in found and "url" in found:
            return _file(found, state)
        return {str(key): _value(item, state) for key, item in found.items()} or None
    raise TypeError(
        f"tool_result() can't describe a value of type {type(value).__name__}. Convert it first."
    )


def _items(record: Any) -> list[tuple[str, Any]]:
    """A record's values by label, or by attribute name for a generated class."""
    if isinstance(record, TypedRecord):
        # A record read with `select` leaves the other attributes unset.
        return [
            (column.name, getattr(record, column.name))
            for column in record.__columns__
            if hasattr(record, column.name)
        ]
    if isinstance(record, Mapping):
        found = cast(Mapping[Any, Any], record)
        return [(str(key), found[key]) for key in found]
    raise TypeError(f"tool_result() expected records, not {type(record).__name__}.")


def _record(record: Any, state: _State) -> dict[str, Any]:
    return {key: _value(item, state) for key, item in _items(record)}


def _records(records: Sequence[Any], state: _State) -> dict[str, Any]:
    rows = [list(_record(record, state).items()) for record in records]
    if not state.compact:
        return {"records": [dict(row) for row in rows]}
    columns = list(dict.fromkeys(key for row in rows for key, _ in row))
    return {
        "columns": columns,
        "rows": [[dict(row).get(column) for column in columns] for row in rows],
    }


def _paged(
    records: Sequence[Any],
    state: _State,
    max_records: int | None,
    *,
    total: int | None,
    skip: int | None,
    has_more: bool,
) -> dict[str, Any]:
    if max_records is not None and len(records) > max_records:
        records, has_more = records[:max_records], True
    next_skip = None if skip is None else skip + len(records)
    page = {
        "returned": len(records),
        "total": total,
        "skip": skip,
        "next_skip": next_skip,
        "has_more": has_more,
    }
    return {**_records(records, state), "page": page}


def _summary(row: SummaryRow, state: _State) -> dict[str, Any]:
    found: dict[str, Any] = {"group": _value(dict(row.group), state) or {}, "count": row.count}
    for name in ("total", "average", "minimum", "maximum"):
        values: Mapping[str, Any] = getattr(row, name)
        if values:
            found[name] = {label: _value(item, state) for label, item in values.items()}
    return found


def _preview(preview: UpdatePreview, state: _State) -> dict[str, Any]:
    return {
        "changes": [
            {
                "record_id": change.record_id,
                "fields": {
                    label: {
                        "before": _value(values.before, state),
                        "after": _value(values.after, state),
                    }
                    for label, values in change.fields.items()
                },
            }
            for change in preview.changes
        ],
        "unchanged_ids": list(preview.unchanged_ids),
        "missing_ids": list(preview.missing_ids),
    }


def _metadata(value: TableInfo | Field | ReportInfo, state: _State) -> dict[str, Any]:
    """Describe metadata by what a model needs, leaving out `raw`."""
    if isinstance(value, TableInfo):
        return {"id": value.id, "name": value.name, "description": _text(value.description, state)}
    if isinstance(value, ReportInfo):
        return {
            "id": value.id,
            "name": value.name,
            "type": value.type,
            "description": _text(value.description, state),
        }
    found: dict[str, Any] = {
        "id": value.id,
        "label": value.label,
        "type": value.type,
        "required": value.required,
        "unique": value.unique,
        "computed": value.mode is not None,
    }
    if value.choices:
        found["choices"] = list(value.choices)
    return found


def _download(value: FileDownload, state: _State) -> dict[str, Any]:
    """A file's name and size, and its text if it's UTF-8 text, as a model can't read bytes."""
    try:
        text: str | None = value.data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and "\x00" in text:
        text = None
    found: dict[str, Any] = {
        "name": value.name,
        "content_type": value.content_type,
        "size": len(value.data),
        "text": None if text is None else (_text(text, state) or ""),
    }
    if text is None:
        found["binary"] = True
    return found


def _describe(value: Any, state: _State, max_records: int | None) -> dict[str, Any]:
    if isinstance(value, Page | TypedPage):
        page = cast(Page | TypedPage[Any], value)
        return _paged(
            page.records,
            state,
            max_records,
            total=page.total,
            skip=page.skip,
            has_more=page.has_more,
        )
    if value is None or isinstance(value, Record | TypedRecord):
        return {"record": None if value is None else _record(value, state)}
    if isinstance(value, WriteResult):
        return _written(value)
    if isinstance(value, UpdatePreview):
        return _preview(value, state)
    if isinstance(value, FormulaResult):
        return {"value": _value(value.value, state), "type": value.type}
    if isinstance(value, str | int | float | bool | Decimal | date | time):
        return {"value": _value(value, state)}
    if isinstance(value, TableInfo | Field | ReportInfo):
        key = {TableInfo: "table", Field: "field", ReportInfo: "report"}[type(value)]
        return {key: _metadata(value, state)}
    if isinstance(value, FileDownload):
        return {"file": _download(value, state)}
    if isinstance(value, list | tuple):
        items = cast(Sequence[Any], value)
        if items and all(isinstance(item, SummaryRow) for item in items):
            return {"groups": [_summary(item, state) for item in items]}
        for cls, key in ((TableInfo, "tables"), (Field, "fields"), (ReportInfo, "reports")):
            if items and all(isinstance(item, cls) for item in items):
                return {key: [_metadata(item, state) for item in items]}
        return _paged(items, state, max_records, total=None, skip=None, has_more=False)
    raise TypeError(
        f"tool_result() can't describe a {type(value).__name__}. It takes records, pages, write "
        "results, summaries, previews, formula results, files, metadata, and plain values."
    )


def _limit(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError(f"{name} must be a positive whole number.")


def tool_result(
    value: Any,
    *,
    compact: bool = False,
    max_records: int | None = None,
    max_text: int | None = None,
) -> dict[str, Any]:
    """Describe a result for a model, as the result of its tool call.

    ```python
    page = await tasks.query_page(select=["Title", "Due Date"], top=50)
    return tool_result(page, compact=True)
    ```

    The result is `{"ok": true, ...}`, which `json.dumps()` always accepts, with a key for what it
    holds:

    | Value | Keys |
    |---|---|
    | `Page` or `TypedPage` | `records`, or `columns` and `rows` with `compact`, and `page` |
    | A list of records or dictionaries | The same. `page.total` is null, as it isn't known |
    | `Record`, `TypedRecord`, or None | `record`, which is null for None |
    | `WriteResult` | `created_ids`, `updated_ids`, and `unchanged_ids` |
    | A list of `SummaryRow` | `groups`, each with `group`, `count`, and the totals it has |
    | `UpdatePreview` | `changes`, `unchanged_ids`, and `missing_ids` |
    | `FormulaResult` | `value` and `type` |
    | Text, a number, a date, or a bool | `value` |
    | `TableInfo`, `Field`, or `ReportInfo` | `table`, `field`, or `report`, without `raw` |
    | A list of one of those | `tables`, `fields`, or `reports` |
    | `FileDownload` | `file`, with `name`, `content_type`, `size`, and `text` or `binary` |

    A file's `text` is its contents if they're UTF-8 text, and otherwise it's null and `binary` is
    true, as a model can't read bytes.

    `page` holds `returned`, `total`, `skip`, `next_skip`, and `has_more`, so a model can tell
    whether it has every record, and ask for the next page. An empty list counts as no records.

    Values are written the same way whatever their field: a date as `YYYY-MM-DD`, a timestamp in
    UTC as `2026-03-04T05:06:07Z`, a `Decimal` as a number when that's exact and as text otherwise,
    and every kind of empty value, such as `""` or an empty file, as null. Records are keyed by
    label, or by attribute name for a generated class.

    Args:
        value: The result to describe.
        compact: Send each label once, as `columns`, with a list of values for each record in
            `rows`, rather than repeating every label on every record. A file value becomes the
            latest file's name. Use it when a tool returns many records, to save tokens.
        max_records: The most records to include. Any more are left out, and `page.has_more` is
            true, so a model knows it doesn't have them all.
        max_text: The most characters to include of each text value. A longer one is cut and
            ends by saying how much was left out, and `text_shortened` is true.

    Returns:
        The result, as plain data.

    Raises:
        TypeError: The value isn't one of these, such as an `AppInfo`, or holds a value of another
            type.
        ValueError: `max_records` or `max_text` isn't a positive whole number.
    """
    _limit("max_records", max_records)
    _limit("max_text", max_text)
    if not isinstance(compact, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("compact takes True or False.")
    state = _State(compact=compact, max_text=max_text)
    found = _describe(value, state, max_records)
    result: dict[str, Any] = {"ok": True, **found}
    if state.shortened:
        result["text_shortened"] = True
    return result
