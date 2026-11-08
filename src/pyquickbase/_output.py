"""Turn results and errors into plain data for a model's tool call. No network access.

Any tool-calling framework can send a dictionary of JSON types, so that's all these return. Results
use the same shapes and value formats whatever the call, and errors carry a fixed `code`, so a model
can tell what went wrong, whether anything changed, and what to do next, without parsing a message.

Values come from records, so they're data, not instructions. Error messages are the SDK's own,
which don't repeat the values they were given, except for an `APIError`, which keeps Quickbase's
wording so a model can fix a request Quickbase refused.
"""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal, cast, overload

from ._confirm import expires_at
from ._decision import READ_VERBS
from ._models import (
    DeletePreviewPage,
    Field,
    FieldsLeftOutPage,
    FileAttachment,
    FileDownload,
    FileVersion,
    FormulaResult,
    Page,
    Record,
    Relationship,
    ReportInfo,
    TableInfo,
    TableSchema,
    User,
)
from ._preview import ConfirmedUpdatePreview, UpdatePreview
from ._schema_plan import (
    PlannedChange,
    SchemaChangeResult,
    SchemaPlan,
    change_action,
)
from ._summary import SummaryRow
from ._writes import RowError, WriteResult
from .errors import (
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    ConfirmationError,
    InvalidResponseError,
    InvalidValueError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    PolicyRefusedError,
    QueryError,
    QuickbaseError,
    RateLimitError,
    RequestTimeoutError,
    SchemaChangeError,
    SchemaMismatchError,
    ServerError,
    TooManyRecordsError,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    UnsupportedReportError,
    WriteError,
    WritePermissionError,
)

ToolErrorCode = Literal[
    "misconfigured",
    "needs_confirmation",
    "invalid_arguments",
    "too_many_records",
    "invalid_value",
    "read_refused",
    "write_refused",
    "unknown_table",
    "unknown_field",
    "unknown_report",
    "unsupported_report",
    "schema_mismatch",
    "partial_write",
    "write_failed",
    "write_permission_denied",
    "authentication",
    "permission_denied",
    "not_found",
    "bad_request",
    "rate_limited",
    "server_error",
    "api_error",
    "timeout",
    "network_error",
    "invalid_response",
    "quickbase_error",
    "internal",
]
"""The `code` of a `tool_error()`. See the table in the agents guide for what each one means."""

DataChanged = Literal["no", "partly", "unknown"]


class Count(int):
    """What a `count` operation returns: the number of matching records, which `tool_result()`
    describes as `count`."""

    __slots__ = ()


class DeletedCount(int):
    """What a `delete` operation returns: the number of records deleted, which `tool_result()`
    describes as `deleted_count`."""

    __slots__ = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupPage(Sequence[SummaryRow]):
    """What a `summarize` operation returns: some of a summary's groups, after sorting, with how
    many there are in all, which `tool_result()` describes as `groups` and `page`. It reads as a
    list of its rows, as the operation returned before. Private."""

    rows: tuple[SummaryRow, ...]
    total: int
    skip: int = 0

    def __len__(self) -> int:
        return len(self.rows)

    @overload
    def __getitem__(self, index: int) -> SummaryRow: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[SummaryRow]: ...
    def __getitem__(self, index: int | slice) -> SummaryRow | Sequence[SummaryRow]:
        return self.rows[index]


# The most specific class wins, so subclasses come before their bases.
_CODES: tuple[tuple[type[BaseException], ToolErrorCode], ...] = (
    (WritePermissionError, "write_permission_denied"),
    (WriteError, "write_failed"),
    (ConfigurationError, "misconfigured"),
    (ConfirmationError, "needs_confirmation"),
    (TooManyRecordsError, "too_many_records"),
    (QueryError, "invalid_arguments"),
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
    (NetworkError, "network_error"),
    (InvalidResponseError, "invalid_response"),
    (QuickbaseError, "quickbase_error"),
)
_HINTS: dict[ToolErrorCode, str] = {
    "misconfigured": "The tool is set up wrongly. Tell the user, as changing the call won't help.",
    "needs_confirmation": "Preview or plan the same change again, show the user the result, and "
    "send its confirmation once they approve.",
    "invalid_arguments": "Fix the filter or arguments as the message says, then try again.",
    "too_many_records": "This summary would read more records than it may. Counting alone "
    "works at any size. To total or average them, ask the user to narrow the question, such as to "
    "a shorter date range. Splitting it into several summaries takes a call for each part, and "
    "the parts must not overlap or miss records.",
    "invalid_value": "Fix the values as the message says, then try again.",
    "read_refused": "This tool may not read that. Ask for less, as the message says, or tell "
    "the user.",
    "write_refused": "This tool may not make that change. Don't try it another way. Tell the user.",
    "unknown_table": "Use a table name this tool lists. It may also be hidden from this tool.",
    "unknown_field": "Use a field this tool lists. It may also be hidden from this tool.",
    "unknown_report": "Use a report this tool lists, by ID if several share a name.",
    "unsupported_report": "This report can't be read this way. Use another report or a query.",
    "schema_mismatch": "The table has changed since the tool was built. Tell the user.",
    "partial_write": "Some records were changed. Fix the failed rows and send only those again.",
    "write_failed": "Nothing was changed. Fix the failed rows, then try again.",
    "write_permission_denied": "The user's role doesn't allow this change. Tell the user.",
    "authentication": "Quickbase didn't accept the token, or the app or table ID is wrong. Tell "
    "the user.",
    "permission_denied": "The user's role doesn't allow this. Tell the user.",
    "not_found": "That record, file, or version doesn't exist. Check the ID.",
    "bad_request": "Quickbase refused the request. Fix it as the message says, then try again.",
    "rate_limited": "Quickbase is limiting requests. Wait, then try again.",
    "server_error": "Quickbase failed. Check whether a change was made before trying again.",
    "api_error": "Quickbase refused the request. Tell the user if the message doesn't say why.",
    "timeout": "There was no answer in time. Check whether a change was made before trying again.",
    "network_error": "Quickbase couldn't be reached. Check whether a change was made before trying "
    "again.",
    "invalid_response": "Quickbase's answer couldn't be read. Check whether a change was made "
    "before trying again.",
    "quickbase_error": "The request failed. Tell the user.",
    "internal": "The tool failed. Tell the user, as trying again won't help.",
}
# A schema change that failed has no failed rows, and Quickbase can't undo the changes before it.
_SCHEMA_CHANGE_HINT = (
    "Changes made before this one stay made. Preview the same changes again, which shows only "
    "what's left, and show the user the result."
)
# A partial write whose last batch may or may not have landed, so its failed rows can't be trusted.
_UNKNOWN_WRITE_HINT = (
    "A batch may have been written before it failed. Check what was written, then send only what's "
    "missing."
)
_RETRYABLE = frozenset({"rate_limited", "server_error", "timeout", "network_error"})
_UNCERTAIN = (NetworkError, ServerError, InvalidResponseError)
_INTERNAL_MESSAGE = "An unexpected error occurred in the tool."
_MAX_MESSAGE = 2000


@dataclass(slots=True)
class _State:
    compact: bool
    max_text: int | None
    truncated: bool = False
    max_chars: int | None = None


# Room left in `max_chars` for a result's other keys, such as `ok`, `page`, and `text_truncated`.
_ENVELOPE_CHARS = 400
# How short text gets, in turn, to fit one record that's too large on its own.
_SHORTER_TEXT = (1000, 500, 200, 100, 50)


def _size(value: Any) -> int:
    """How many characters `value` takes as compact JSON, as a server sends it to a model."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def _fitting(sizes: Sequence[int], budget: int) -> int:
    """How many items, from the first, fit in `budget` characters. Always at least one."""
    used = count = 0
    for size in sizes:
        if count and used + size + 1 > budget:
            break
        used += size + 1
        count += 1
    return count


def _code(error: BaseException) -> ToolErrorCode:
    if isinstance(error, PolicyRefusedError):
        verb = getattr(error.decision, "verb", "")
        return "read_refused" if verb in READ_VERBS else "write_refused"
    for cls, code in _CODES:
        if isinstance(error, cls):
            return code
    return "internal"


def _write_changed(error: WriteError) -> DataChanged:
    result = error.result
    wrote = isinstance(result, WriteResult) and bool(result.created_ids or result.updated_ids)
    # A later batch that failed this way may have been written.
    if isinstance(error.__cause__, _UNCERTAIN):
        return "unknown"
    return "partly" if wrote else "no"


def _changed(error: BaseException) -> DataChanged:
    if isinstance(error, QuickbaseError) and error._changed:  # pyright: ignore[reportPrivateUsage]
        return "partly"
    if isinstance(error, WriteError):
        return _write_changed(error)
    if isinstance(error, _UNCERTAIN) or not isinstance(error, QuickbaseError):
        return "unknown"
    return "no"


def _row_error(item: RowError) -> dict[str, Any]:
    return {
        "index": item.index,
        "reason": item.reason,
        "field_id": item.field_id,
        "messages": list(item.messages),
    }


def _written(result: WriteResult) -> dict[str, list[int]]:
    return {
        "created_ids": list(result.created_ids),
        "updated_ids": list(result.updated_ids),
        "unchanged_ids": list(result.unchanged_ids),
    }


def _hint(error: BaseException, code: ToolErrorCode, changed: DataChanged) -> str:
    if isinstance(error, SchemaChangeError):
        return _SCHEMA_CHANGE_HINT
    if code == "partial_write" and changed == "unknown":
        return _UNKNOWN_WRITE_HINT
    return _HINTS[code]


def tool_error(error: BaseException) -> dict[str, Any]:
    """Describe an exception for a model, as the result of its tool call.

    ```python
    try:
        page = await tasks.query_page(
            where=where, limit=50
        )
        return tool_result(page)
    except QuickbaseError as error:
        return tool_error(error)
    ```

    The result is `{"ok": false, "error": {...}}`, which `json.dumps()` always accepts. `error`
    holds `code`, `message`, `hint`, `retryable`, and `data_changed`, and more for some codes:

    - `code` is one of `ToolErrorCode`, which doesn't change between 1.x versions, unlike the
      wording of `message`.
    - `hint` says what the model should do next, in fixed words for each code. A failed schema
      change has its own, as it has no failed rows, and so does a `partial_write` whose
      `data_changed` is `unknown`, as its failed rows may have been written.
    - `retryable` is true when the same call can succeed later, such as after a rate limit or a
      timeout. `retry_after` is added when Quickbase said how long to wait.
    - `data_changed` is `no` if nothing was changed, `partly` if part of a write was, and
      `unknown` if a write may have been applied, such as after a timeout. A model should check
      the data before repeating a call whose `data_changed` isn't `no`.
    - A `WriteError` adds `written`, with the IDs that were created, updated, and left unchanged,
      and `failed_rows`, with each row's `index`, `reason`, `field_id`, and Quickbase's `messages`.
    - An `APIError` adds `status`, the HTTP status.
    - A `TooManyRecordsError` adds `matched`, how many records match, and `limit`, the most the
      summary may read.

    An exception from outside the SDK gets the code `internal` and a message that says nothing
    about it, so a bug's details and any values in it don't reach the model. Log it yourself.

    Args:
        error: The exception, usually a `QuickbaseError`.

    Returns:
        The error, as plain data.
    """
    code = _code(error)
    changed = _changed(error)
    if code == "write_failed" and changed != "no":
        code = "partial_write"
    message = _INTERNAL_MESSAGE if code == "internal" else str(error)[:_MAX_MESSAGE]
    found: dict[str, Any] = {
        "code": code,
        "message": message,
        "hint": _hint(error, code, changed),
        "retryable": code in _RETRYABLE,
        "data_changed": changed,
    }
    if isinstance(error, RateLimitError) and error.retry_after is not None:
        found["retry_after"] = error.retry_after
    if isinstance(error, APIError) and error.status_code:
        found["status"] = error.status_code
    if isinstance(error, TooManyRecordsError):
        found["matched"] = error.matched
        found["limit"] = error.limit
    if isinstance(error, WriteError) and isinstance(error.result, WriteResult):
        found["written"] = _written(error.result)
        found["failed_rows"] = [_row_error(item) for item in error.result.errors]
    if isinstance(error, WriteError) and isinstance(error.result, SchemaChangeResult):
        found.update(_applied(error.result))
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
    state.truncated = True
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


def _user(value: User, state: _State) -> dict[str, Any]:
    return {"id": value.id, "name": _text(value.name, state), "email": value.email}


def _version(value: FileVersion, state: _State) -> dict[str, Any]:
    return {
        "number": value.number,
        "file_name": _text(value.file_name, state),
        "uploaded": _value(value.uploaded, state),
        "creator": None if value.creator is None else _user(value.creator, state),
    }


def _attachment(value: FileAttachment, state: _State) -> Any:
    if state.compact:
        return _text(value.latest.file_name, state)
    return {"url": value.url, "versions": [_version(item, state) for item in value.versions]}


def _value(value: Any, state: _State) -> Any:
    """Write one value the same way everywhere. Every kind of empty value becomes None."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, User):
        return _user(value, state)
    if isinstance(value, FileAttachment):
        return _attachment(value, state)
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
    """A record's values by label."""
    if isinstance(record, Mapping):
        found = cast(Mapping[Any, Any], record)
        return [(str(key), found[key]) for key in found]
    raise TypeError(f"tool_result() expected records, not {type(record).__name__}.")


def _record(record: Any, state: _State) -> dict[str, Any]:
    return {key: _value(item, state) for key, item in _items(record)}


Row = list[tuple[str, Any]]


def _shape(rows: Sequence[Row], compact: bool) -> dict[str, Any]:
    if not compact:
        return {"records": [dict(row) for row in rows]}
    columns = list(dict.fromkeys(key for row in rows for key, _ in row))
    return {
        "columns": columns,
        "rows": [[dict(row).get(column) for column in columns] for row in rows],
    }


def _shortened(record: Any, state: _State, budget: int) -> Row:
    """One record with its text cut shorter in turn until it fits in `budget`, or as short as
    text gets."""
    row: Row = list(_record(record, state).items())
    for limit in _SHORTER_TEXT:
        if state.max_text is not None and limit >= state.max_text:
            continue
        trial = _State(compact=state.compact, max_text=limit)
        row = list(_record(record, trial).items())
        state.truncated = state.truncated or trial.truncated
        if _size(dict(row)) <= budget:
            break
    return row


def _fit_rows(records: Sequence[Any], rows: list[Row], state: _State, limit: int) -> list[Row]:
    """The rows, from the first, that fit in `limit` characters, and at least one."""
    budget = limit - _ENVELOPE_CHARS
    if state.compact:
        columns = list(dict.fromkeys(key for row in rows for key, _ in row))
        budget -= _size(columns)
        sizes = [_size([dict(row).get(column) for column in columns]) for row in rows]
    else:
        sizes = [_size(dict(row)) for row in rows]
    kept = _fitting(sizes, budget)
    if kept == 1 and sizes[0] > budget:
        return [_shortened(records[0], state, budget)]
    return rows[:kept]


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
    rows: list[Row] = [list(_record(record, state).items()) for record in records]
    if state.max_chars is not None and rows:
        fitted = _fit_rows(records, rows, state, state.max_chars)
        has_more = has_more or len(fitted) < len(rows)
        rows = fitted
    next_skip = None if skip is None else skip + len(rows)
    page = {
        "returned": len(rows),
        "total": total,
        "skip": skip,
        "next_skip": next_skip,
        "has_more": has_more,
    }
    return {**_shape(rows, state.compact), "page": page}


def _groups(rows: Sequence[SummaryRow], state: _State, *, total: int, skip: int) -> dict[str, Any]:
    items = [_summary(row, state) for row in rows]
    if state.max_chars is not None and items:
        kept = _fitting([_size(item) for item in items], state.max_chars - _ENVELOPE_CHARS)
        items = items[:kept]
    page = {
        "returned": len(items),
        "total": total,
        "skip": skip,
        "next_skip": skip + len(items),
        "has_more": skip + len(items) < total,
    }
    return {"groups": items, "page": page}


def _summary(row: SummaryRow, state: _State) -> dict[str, Any]:
    found: dict[str, Any] = {"group": _value(dict(row.group), state) or {}, "count": row.count}
    for name in ("sum", "avg", "min", "max"):
        values: Mapping[str, Any] = getattr(row, name)
        if values:
            found[name] = {label: _value(item, state) for label, item in values.items()}
    return found


def _approval(
    confirmation: str | None, blockers: Sequence[str], warnings: Sequence[str]
) -> dict[str, Any]:
    """What every preview ends with: what stops the change, what else it does, and the
    confirmation that the change needs, with when it expires."""
    found: dict[str, Any] = {"blockers": list(blockers), "warnings": list(warnings)}
    if confirmation:
        found["confirmation"] = confirmation
        expires = expires_at(confirmation)
        if expires is not None:
            when = datetime.fromtimestamp(expires, UTC)
            found["expires_at"] = _value(when, _State(compact=False, max_text=None))
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
    if value.mode is not None:
        found["mode"] = value.mode
    if value.formula is not None:
        found["formula"] = value.formula
    return found


def _planned(item: PlannedChange) -> dict[str, Any]:
    found: dict[str, Any] = {
        "action": change_action(item.change),
        "status": item.status,
        "risk": item.risk,
        "summary": item.summary,
        "settings": {
            name: {"before": change.before, "after": change.after}
            for name, change in item.settings.items()
        },
        "warnings": list(item.warnings),
        "blockers": list(item.blockers),
    }
    if item.affected_records is not None:
        found["affected_records"] = item.affected_records
    if item.policy_effects:
        found["policy_effects"] = list(item.policy_effects)
    if item.link is not None:
        found["link"] = item.link
    return found


def _applied(result: SchemaChangeResult) -> dict[str, Any]:
    """What applying a plan did, by each change's summary."""
    found: dict[str, Any] = {
        "applied": [_summary_link(item) for item in result.applied],
        "already_done": [item.summary for item in result.already_done],
    }
    if result.failed is not None:
        found["failed"] = result.failed.summary
        found["not_attempted"] = [item.summary for item in result.not_attempted]
    return found


def _summary_link(item: PlannedChange) -> dict[str, Any]:
    found: dict[str, Any] = {"summary": item.summary}
    if item.link is not None:
        found["link"] = item.link
    return found


def _table_schema(value: TableSchema, state: _State, tables: Mapping[str, str]) -> dict[str, Any]:
    labels = {item.id: item.label for item in value.fields}
    return {
        **_metadata(value.info, state),
        "fields": [_metadata(item, state) for item in value.fields],
        "relationships": [_relationship(item, labels, tables) for item in value.relationships],
    }


def _relationship(
    value: Relationship, labels: Mapping[int, str], tables: Mapping[str, str]
) -> dict[str, Any]:
    """A relationship from its child's side, with its child's fields by label, and its parent by
    name when the parent is among the tables described."""
    return {
        "parent_table": tables.get(value.parent_table_id),
        "parent_table_id": value.parent_table_id,
        "foreign_key": labels.get(value.foreign_key_field_id or 0),
        "lookups": [labels[item] for item in value.lookup_field_ids if item in labels],
    }


def _download(value: FileDownload, state: _State) -> dict[str, Any]:
    """A file's name and size, and its text if it's UTF-8 text, as a model can't read bytes."""
    try:
        text: str | None = value.data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None and "\x00" in text:
        text = None
    found: dict[str, Any] = {
        "file_name": value.file_name,
        "content_type": value.content_type,
        "size": len(value.data),
        "text": None if text is None else (_text(text, state) or ""),
    }
    if text is None:
        found["binary"] = True
    return found


def _describe(value: Any, state: _State, max_records: int | None) -> dict[str, Any]:
    if isinstance(value, Page):
        page = value
        found = _paged(
            page.records,
            state,
            max_records,
            total=page.total,
            skip=page.skip,
            has_more=page.has_more,
        )
        if isinstance(page, DeletePreviewPage):
            blockers: list[str] = []
            if page.total > page.max_deletable:
                blockers.append(
                    f"{page.total} records match, and a delete may match at most "
                    f"{page.max_deletable}, so "
                    "deleting with this filter would be refused, and nothing would be deleted. "
                    "Narrow the filter."
                )
            found.update(_approval(page.confirmation, blockers, ()))
        if isinstance(page, FieldsLeftOutPage):
            found["fields_left_out"] = {
                "count": page.fields_left_out,
                "hint": "These are the table's first default fields. Name the fields you need "
                "in select to get others.",
            }
        return found
    if isinstance(value, GroupPage):
        return _groups(value.rows, state, total=value.total, skip=value.skip)
    if value is None or isinstance(value, Record):
        return {"record": None if value is None else _record(value, state)}
    if isinstance(value, WriteResult):
        return _written(value)
    if isinstance(value, UpdatePreview):
        found = _preview(value, state)
        if isinstance(value, ConfirmedUpdatePreview):
            warnings: list[str] = []
            if value.missing_ids:
                warnings.append(
                    f"Records {list(value.missing_ids)} don't exist, or this tool can't see them, "
                    "so the update would report them as failed rows."
                )
            found.update(_approval(value.confirmation, (), warnings))
        return found
    if isinstance(value, SchemaPlan):
        return {
            "changes": [_planned(item) for item in value.changes],
            "has_changes": value.has_changes,
            **_approval(value.confirmation, value.blockers, value.warnings),
        }
    if isinstance(value, SchemaChangeResult):
        return _applied(value)
    if isinstance(value, FormulaResult):
        return {"value": _value(value.value, state), "type": value.type}
    if isinstance(value, Count):
        return {"count": int(value)}
    if isinstance(value, DeletedCount):
        return {"deleted_count": int(value)}
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
            rows = cast(Sequence[SummaryRow], items)
            return _groups(rows, state, total=len(rows), skip=0)
        if items and all(isinstance(item, TableSchema) for item in items):
            schemas = cast(list[TableSchema], items)
            tables = {item.info.id: item.info.name for item in schemas}
            return {"tables": [_table_schema(item, state, tables) for item in schemas]}
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
    max_text_chars: int | None = None,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Describe a result for a model, as the result of its tool call.

    ```python
    page = await tasks.query_page(limit=50)
    return tool_result(page, compact=True)
    ```

    The result is `{"ok": true, ...}`, which `json.dumps()` always accepts, with a key for what it
    holds:

    | Value | Keys |
    |---|---|
    | `Page` | `records`, or `columns` and `rows` with `compact`, and `page` |
    | A list of records or dictionaries | The same. `page.total` is null, as it isn't known |
    | `Record` or None | `record`, which is null for None |
    | `WriteResult` | `created_ids`, `updated_ids`, and `unchanged_ids` |
    | A list of `SummaryRow` | `groups`, each with `group`, `count`, and its totals, and `page` |
    | A `summarize` operation's result | As for a list of `SummaryRow`, sorted and paged as asked |
    | `UpdatePreview` | `changes`, `unchanged_ids`, and `missing_ids` |
    | A `preview_update` operation's result | As for an `UpdatePreview`, with the approval keys |
    | A `preview_delete` operation's result | As for a `Page`, with the approval keys |
    | `SchemaPlan` | `changes` and `has_changes`, and the approval keys |
    | `SchemaChangeResult` | `applied` and `already_done`, and `failed` and `not_attempted` too |
    | A list of `TableSchema` | `tables`, each with `fields` and `relationships` |
    | `FormulaResult` | `value` and `type` |
    | A `count` operation's result | `count` |
    | A `delete` operation's result | `deleted_count` |
    | Text, a number, a date, or a bool | `value` |
    | `TableInfo`, `Field`, or `ReportInfo` | `table`, `field`, or `report`, without `raw` |
    | A list of one of those | `tables`, `fields`, or `reports` |
    | `FileDownload` | `file`, with `file_name`, `content_type`, `size`, and `text` or `binary` |

    A file's `text` is its contents if they're UTF-8 text, and otherwise it's null and `binary` is
    true, as a model can't read bytes.

    `page` holds `returned`, `total`, `skip`, `next_skip`, and `has_more`, so a model can tell
    whether it has every record, and ask for the next page. An empty list counts as no records.
    For a summary, `page` counts groups. A `query` operation that left out some of the table's
    default fields, because it has more than its `max_default_fields`, adds `fields_left_out` with
    how many and a `hint`.

    A preview's approval keys are `blockers`, what would stop the change, `warnings`, what else it
    would do, and, if nothing stops it, the `confirmation` the change needs and the UTC time it
    `expires_at`. A `preview_delete` operation's result has a blocker when more records match than
    the operation's `max_records`, as a delete with the same filter would be refused. Give both
    operations the same `max_records` for this to be right.

    Values are written the same way whatever their field: a date as `YYYY-MM-DD`, a timestamp in
    UTC as `2026-03-04T05:06:07Z`, a `Decimal` as a number when that's exact and as text otherwise,
    and every kind of empty value, such as `""` or an empty file, as null. Records are keyed by
    label.

    Args:
        value: The result to describe.
        compact: Send each label once, as `columns`, with a list of values for each record in
            `rows`, rather than repeating every label on every record. A file value becomes the
            latest file's name. Use it when a tool returns many records, to save tokens.
        max_records: The most records to include. Any more are left out, and `page.has_more` is
            true, so a model knows it doesn't have them all.
        max_text_chars: The most characters to include of each text value. A longer one is cut and
            ends by saying how much was left out, and `text_truncated` is true.
        max_chars: About the most characters the result may take as compact JSON, so that one
            call can't fill a model's context. Records and groups are included from the first
            until the next wouldn't fit, then `page.has_more` is true and `page.next_skip` says
            where to carry on, so paging still reaches every one. At least one is always included:
            if it's too large on its own, its text is cut shorter until it fits, down to 50
            characters a value. Other keys, such as a file's text, aren't limited by it.

    Returns:
        The result, as plain data.

    Raises:
        TypeError: The value isn't one of these, such as an `AppInfo`, or holds a value of another
            type.
        ValueError: `max_records`, `max_text_chars`, or `max_chars` isn't a positive whole number.
    """
    _limit("max_records", max_records)
    _limit("max_text_chars", max_text_chars)
    _limit("max_chars", max_chars)
    if not isinstance(compact, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("compact takes True or False.")
    state = _State(compact=compact, max_text=max_text_chars, max_chars=max_chars)
    found = _describe(value, state, max_records)
    result: dict[str, Any] = {"ok": True, **found}
    if state.truncated:
        result["text_truncated"] = True
    return result
