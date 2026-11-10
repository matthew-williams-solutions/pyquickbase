"""Describe a read, for a policy to decide on, and the pieces that trim what comes back. No
network access.

This follows `_write_request.py`. `describe_read()` is the only code that knows how Quickbase's
requests look, and a policy only sees the `ReadRequest` it produces, which holds no values, so a
policy can't leak or log them. Anything that can't be understood is unknown, and a policy refuses
it.

Trimming keeps only what's listed, rather than removing what's known to be sensitive, so a key
Quickbase adds later is left out until someone decides it's safe. See docs/api-notes.md for what
each response holds.
"""

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from ._formula import formula_parts, unreadable_fields, unsafe_functions
from ._query import where_field_ids
from ._write_request import is_schema_write
from .errors import PolicyRefusedError

ReadAction = Literal[
    "query",
    "summarize",
    "delete",
    "report",
    "summary_report",
    "file",
    "formula",
    "tables",
    "fields",
    "relationships",
    "reports",
    "app",
    "roles",
    "field_usage",
    "app_events",
    "apps",
    "current_user",
    "find_user",
    "members",
    "member_roles",
    "code_pages",
    "other",
]
"""What a read does. See `ReadRequest`."""

_TABLE_ID = r"([A-Za-z0-9]+)"
_REPORT_RUN = re.compile(r"/reports/([0-9]+)/run")
FILE_PATH = re.compile(rf"/files/{_TABLE_ID}/[0-9]+/([0-9]+)/[0-9]+")
_APP = re.compile(rf"/apps/{_TABLE_ID}")
_ROLES = re.compile(rf"/apps/{_TABLE_ID}/roles")
_TRUSTEES = re.compile(rf"/app/{_TABLE_ID}/trustees")
_RELATIONSHIPS = re.compile(rf"/tables/{_TABLE_ID}/relationships")
_APP_EVENTS = re.compile(rf"/apps/{_TABLE_ID}/events")
_FIELD_USAGE = re.compile(r"/fields/usage(?:/([1-9][0-9]*))?")
_QUERY_KEYS = frozenset({"from", "select", "where", "sortBy", "options"})
_OPTION_KEYS = frozenset({"top", "skip", "compareWithAppLocalTime"})
_SORT_ORDERS = frozenset({"ASC", "DESC"})

# What each kind of metadata keeps. Everything else, such as a formula's text, a report's filter,
# and an app's variables, is left out of the response, so it isn't in `raw` either.
TABLE_KEYS = ("id", "name", "description", "keyFieldId")
REPORT_KEYS = ("id", "name", "type", "description", "incomplete")
FIELD_KEYS = (
    "id",
    "label",
    "fieldType",
    "mode",
    "required",
    "unique",
    "appearsByDefault",
    "fieldHelp",
)
FIELD_PROPERTIES = (
    "choices",
    "allowNewChoices",
    "decimalPlaces",
    "foreignKey",
    "lookupReferenceFieldId",
    "lookupTargetFieldId",
    "summaryReferenceFieldId",
    "summaryTargetFieldId",
    "summaryFunction",
)
COLUMN_KEYS = ("id", "label", "type")
FILE_VERSION_KEYS = ("versionNumber", "fileName", "uploaded")
USER_KEYS = ("id", "name")
USAGE_FIELD_KEYS = ("id", "name", "type")
EVENT_KEYS = ("type", "name", "isActive", "tableId", "url")

REFUSED_READS: dict[str, str] = {
    "apps": "A policy covers one app, so it can't list the apps the token can reach",
    "current_user": "A policy covers one app's tables, not who the token belongs to",
    "find_user": "A policy covers one app's tables, not the realm's users",
    "members": "Only tables can be read, not who has a role in the app",
    "member_roles": "Only tables can be read, not who has a role in the app",
}
"""The reads a policy always refuses, even `Policy.read_only()`, and why."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadRequest:
    """What a read would do, for a policy to decide on.

    It holds no values from the request, so a policy can log it safely. `None` always means that
    this couldn't be determined, so check for it rather than treating it as nothing. `Record ID#`
    appears like any other field.

    Attributes:
        action: `query` reads records, and `summarize` is a page that `summarize()` reads to
            count and total. `delete` deletes records by a filter, which a read policy checks
            because the number deleted shows how many matched. `report` runs a table report, and
            `summary_report` a summary or chart report. `file` downloads a file, and `formula`
            runs one. `tables`, `fields`, `relationships`, `reports`, `app`, and `roles` read
            metadata. `field_usage` reads where fields are used, and `app_events` the app's
            webhooks, notifications, and automations. `apps`, `current_user`, `find_user`,
            `members`, and `member_roles` read about people and apps, which a policy always
            refuses. `code_pages` lists or reads an app's code pages, which only
            `allow.code_pages()` permits. `other` is anything else, including a
            report run through `request()`, which doesn't say what kind of report it is. A
            change to tables, fields, or relationships isn't a read.
        table_id: The table it reads, or None for `tables`, `app`, `roles`, `app_events`, and
            `other`.
        app_id: For `members`, `member_roles`, and `code_pages`, the app.
        field_ids: The fields it returns. For `file`, the file field, and for `field_usage`, the
            one field it asks about. None for a report, whose columns are chosen in Quickbase,
            and for other metadata.
        filter_field_ids: The fields its filter uses, which is empty without one. None if the
            filter can't be read, and for a report, whose filter is its own.
        sort_field_ids: The fields it sorts by. None if it doesn't say, as the table's default
            sort would apply, and for a report.
        limit: The most records it asks for, which Quickbase calls `top`. None if it doesn't say,
            so Quickbase returns them all.
        skip: How many matching records it passes over first.
        report_id: The report it runs.
        method: The HTTP method.
        path: The request's path, without its query parameters.
    """

    action: ReadAction
    table_id: str | None = None
    field_ids: frozenset[int] | None = None
    filter_field_ids: frozenset[int] | None = None
    sort_field_ids: frozenset[int] | None = None
    limit: int | None = None
    skip: int | None = None
    report_id: str | None = None
    app_id: str | None = None
    method: str
    path: str


def _plain_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _id_set(value: Any) -> frozenset[int] | None:
    if not isinstance(value, list):
        return None
    ids = [_plain_int(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
    if any(item is None or item < 1 for item in ids):
        return None
    return frozenset(item for item in ids if item is not None)


def _where_ids(body: Mapping[str, Any]) -> frozenset[int] | None:
    if "where" not in body:
        return frozenset()
    where = body["where"]
    if not isinstance(where, str):
        return None
    return frozenset() if not where.strip() else where_field_ids(where)


def _sort_ids(sort: Any) -> frozenset[int] | None:
    """Read `sortBy`. Leaving it out, or `false`, means the table's default sort, which can't be
    known from the request, so it's unknown."""
    if not isinstance(sort, list):
        return None
    ids: set[int] = set()
    for item in sort:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, Mapping) or set(item) != {"fieldId", "order"}:  # pyright: ignore[reportUnknownArgumentType]
            return None
        field_id = _plain_int(item["fieldId"])
        if field_id is None or item["order"] not in _SORT_ORDERS:
            return None
        ids.add(field_id)
    return frozenset(ids)


def _params(params: Mapping[str, Any] | None, allowed: Collection[str]) -> dict[str, Any] | None:
    """Return the query parameters if they're all expected, or None."""
    found = dict(params or {})
    return found if set(found) <= set(allowed) else None


def _describe_query(body: Any, method: str, path: str, summarizing: bool) -> ReadRequest:
    if not isinstance(body, Mapping) or not set(body) <= _QUERY_KEYS:  # pyright: ignore[reportUnknownArgumentType]
        return ReadRequest(action="other", method=method, path=path)
    options = body.get("options", {})  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(options, Mapping) or not set(options) <= _OPTION_KEYS:  # pyright: ignore[reportUnknownArgumentType]
        return ReadRequest(action="other", method=method, path=path)
    source = body.get("from")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return ReadRequest(
        action="summarize" if summarizing else "query",
        table_id=source if isinstance(source, str) else None,
        field_ids=_id_set(body.get("select")),  # pyright: ignore[reportUnknownMemberType]
        filter_field_ids=_where_ids(body),  # pyright: ignore[reportUnknownArgumentType]
        sort_field_ids=_sort_ids(body.get("sortBy")),  # pyright: ignore[reportUnknownMemberType]
        limit=_plain_int(options.get("top")),  # pyright: ignore[reportUnknownMemberType]
        skip=_plain_int(options.get("skip", 0)),  # pyright: ignore[reportUnknownMemberType]
        method=method,
        path=path,
    )


def _describe_get(path: str, params: Mapping[str, Any] | None) -> ReadRequest:
    other = ReadRequest(action="other", method="GET", path=path)
    lists = {"/tables": ("tables", "appId"), "/reports": ("reports", "tableId")}
    if path in lists:
        action, key = lists[path]
        found = _params(params, (key,))
        if found is None or not isinstance(found.get(key), str):
            return other
        table_id = found.get(key) if action == "reports" else None
        return ReadRequest(action=action, table_id=table_id, method="GET", path=path)  # pyright: ignore[reportArgumentType]
    if path == "/fields":
        found = _params(params, ("tableId", "includeFieldPerms"))
        if found is None or not isinstance(found.get("tableId"), str):
            return other
        return ReadRequest(action="fields", table_id=found["tableId"], method="GET", path=path)
    if (match := _RELATIONSHIPS.fullmatch(path)) and _params(params, ("skip",)) is not None:
        return ReadRequest(action="relationships", table_id=match[1], method="GET", path=path)
    if (match := FILE_PATH.fullmatch(path)) and not params:
        return ReadRequest(
            action="file",
            table_id=match[1],
            field_ids=frozenset({int(match[2])}),
            method="GET",
            path=path,
        )
    if match := _FIELD_USAGE.fullmatch(path):
        found = _params(params, ("tableId",) if match[1] else ("tableId", "skip", "top"))
        if found is None or not isinstance(found.get("tableId"), str):
            return other
        return ReadRequest(
            action="field_usage",
            table_id=found["tableId"],
            field_ids=frozenset({int(match[1])}) if match[1] else None,
            method="GET",
            path=path,
        )
    if _APP_EVENTS.fullmatch(path) and not params:
        return ReadRequest(action="app_events", method="GET", path=path)
    if _TRUSTEES.fullmatch(path):
        return ReadRequest(action="members", method="GET", path=path)
    if _ROLES.fullmatch(path):
        return ReadRequest(action="roles", method="GET", path=path)
    if _APP.fullmatch(path):
        return ReadRequest(action="app", method="GET", path=path)
    return other


def _describe_report(
    path: str, report_id: str, params: Mapping[str, Any] | None, body: Any, report_type: str | None
) -> ReadRequest:
    found = _params(params, ("tableId", "top", "skip"))
    # Quickbase ignores top and skip in a body and returns every record, so a body is refused.
    if found is None or body is not None or report_type is None:
        return ReadRequest(action="other", method="POST", path=path)
    table_id = found.get("tableId")
    return ReadRequest(
        action="report" if report_type == "table" else "summary_report",
        table_id=table_id if isinstance(table_id, str) else None,
        limit=_plain_int(found.get("top")),
        skip=_plain_int(found.get("skip", 0)),
        # Quickbase runs the same report for an ID with leading zeros.
        report_id=str(int(report_id)),
        method="POST",
        path=path,
    )


def describe_read(
    method: str,
    path: str,
    params: Mapping[str, Any] | None,
    body: Any,
    *,
    report_type: str | None = None,
    summarizing: bool = False,
) -> ReadRequest | None:
    """Describe a request for a read policy, or return None for a write that reveals no data.

    `report_type` is the report's type, which a request to run one doesn't say, and without it a
    report run is `other`. `summarizing` marks the pages `summarize()` reads. Neither can be set
    through `request()`.
    """
    if method == "GET":
        if body is not None:
            return ReadRequest(action="other", method=method, path=path)
        return _describe_get(path, params)
    if method == "POST" and path == "/records/query":
        return _describe_query(body, method, path, summarizing)
    if method == "POST" and (match := _REPORT_RUN.fullmatch(path)):
        return _describe_report(path, match[1], params, body, report_type)
    if method == "POST" and path == "/formula/run":
        source = body.get("from") if isinstance(body, Mapping) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        table_id = source if isinstance(source, str) else None
        return ReadRequest(action="formula", table_id=table_id, method=method, path=path)
    if method == "POST" and path == "/records" and isinstance(body, Mapping):
        # A write only returns record values when it asks for them with fieldsToReturn.
        if "fieldsToReturn" in body:
            return ReadRequest(action="other", method=method, path=path)
        return None
    if method == "DELETE" and path == "/records" and isinstance(body, Mapping):
        source = body.get("from")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        return ReadRequest(
            action="delete",
            table_id=source if isinstance(source, str) else None,
            filter_field_ids=_where_ids(body),  # pyright: ignore[reportUnknownArgumentType]
            method=method,
            path=path,
        )
    if method == "DELETE" and FILE_PATH.fullmatch(path):
        return None
    if is_schema_write(method, path, body, params):
        # A schema change reveals no values. Formulas, lookups, and summaries could, so the client
        # only sends one after checking that it reads nothing hidden.
        return None
    return ReadRequest(action="other", method=method, path=path)


def pick(item: Mapping[str, Any], keys: Collection[str]) -> dict[str, Any]:
    return {key: item[key] for key in keys if key in item}


def shows_only(formula: str, readable: Collection[tuple[str, int]]) -> bool:
    """Whether a formula's text reveals nothing hidden: every field it names is readable, and it
    calls no function that reads other records."""
    try:
        parts = formula_parts(formula)
    except PolicyRefusedError:
        return False
    labels = [label for label, _ in readable]
    ids = [field_id for _, field_id in readable]
    return not unreadable_fields(parts, labels, ids) and not unsafe_functions(parts)


def trim_value(value: Any) -> Any:
    """Leave out emails and uploaders, recognizing users and files by their shape, as a formula or
    lookup can return either and reports its type as its own."""
    if isinstance(value, list):
        return [trim_value(item) for item in cast(list[Any], value)]
    if not isinstance(value, Mapping):
        return value
    found = cast(Mapping[str, Any], value)
    if "versions" in found:
        versions: list[Mapping[str, Any]] = found["versions"]
        return {
            "url": found.get("url", ""),
            "versions": [pick(version, FILE_VERSION_KEYS) for version in versions],
        }
    if "email" in found or "userName" in found:
        return pick(found, USER_KEYS)
    return found
