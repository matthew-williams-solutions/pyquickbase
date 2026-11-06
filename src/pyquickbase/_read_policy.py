"""Decide whether a read may be sent, and trim what comes back. No network access.

This follows `_policy.py`. `describe_read()` is the only code that knows how Quickbase's requests
look, and a policy only sees the `ReadRequest` it produces, which holds no values, so a policy can't
leak or log them. Anything that can't be understood is unknown, and `ReadRules` refuses it.

`ReadRules` also trims responses, which a policy function can't do, because that needs the values.
It keeps only what's listed, rather than removing what's known to be sensitive, so a key Quickbase
adds later is left out until someone decides it's safe. See docs/api-notes.md for what each
response holds.
"""

import inspect
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from ._formula import formula_parts, unreadable_fields, unsafe_functions
from ._models import RECORD_ID_FIELD
from ._policy import _access, is_schema_write  # pyright: ignore[reportPrivateUsage]
from ._query import where_field_ids
from .errors import ConfigurationError, InvalidResponseError, ReadRefusedError

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
    "other",
]
"""What a read does. See `ReadRequest`."""

DEFAULT_MAX_RECORDS = 100
DEFAULT_MAX_SUMMARY_RECORDS = 10_000

_TABLE_ID = r"([A-Za-z0-9]+)"
_REPORT_RUN = re.compile(r"/reports/([0-9]+)/run")
_FILE = re.compile(rf"/files/{_TABLE_ID}/[0-9]+/([0-9]+)/[0-9]+")
_APP = re.compile(rf"/apps/{_TABLE_ID}")
_ROLES = re.compile(rf"/apps/{_TABLE_ID}/roles")
_RELATIONSHIPS = re.compile(rf"/tables/{_TABLE_ID}/relationships")
_QUERY_KEYS = frozenset({"from", "select", "where", "sortBy", "options"})
_OPTION_KEYS = frozenset({"top", "skip", "compareWithAppLocalTime"})
_SORT_ORDERS = frozenset({"ASC", "DESC"})

# What each kind of metadata keeps. Everything else, such as a formula's text, a report's filter,
# and an app's variables, is left out of the response, so it isn't in `raw` either.
_TABLE_KEYS = ("id", "name", "description", "keyFieldId")
_REPORT_KEYS = ("id", "name", "type", "description", "incomplete")
_FIELD_KEYS = (
    "id",
    "label",
    "fieldType",
    "mode",
    "required",
    "unique",
    "appearsByDefault",
    "fieldHelp",
)
_FIELD_PROPERTIES = (
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
_COLUMN_KEYS = ("id", "label", "type")
_FILE_VERSION_KEYS = ("versionNumber", "fileName", "uploaded")
_USER_KEYS = ("id", "name")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadRequest:
    """What a read would do, for a read policy to decide on.

    It holds no values from the request, so a policy can log it safely. `None` always means that
    this couldn't be determined, so check for it rather than treating it as nothing. `Record ID#`
    appears like any other field.

    Attributes:
        action: `query` reads records, and `summarize` is a page that `summarize()` reads to
            count and total. `delete` deletes records by a filter, which a read policy checks
            because the number deleted shows how many matched. `report` runs a table report, and
            `summary_report` a summary or chart report. `file` downloads a file, and `formula`
            runs one. `tables`, `fields`, `relationships`, `reports`, `app`, and `roles` read
            metadata. `other` is anything else, including a report run through `request()`,
            which doesn't say what kind of report it is. A change to tables, fields, or
            relationships isn't a read.
        table_id: The table it reads, or None for `tables`, `app`, `roles`, and `other`.
        field_ids: The fields it returns. For `file`, the file field. None for a report, whose
            columns are chosen in Quickbase, and for metadata.
        filter_field_ids: The fields its filter uses, which is empty without one. None if the
            filter can't be read, and for a report, whose filter is its own.
        sort_field_ids: The fields it sorts by. None if it doesn't say, as the table's default
            sort would apply, and for a report.
        top: The most records it asks for. None if it doesn't say, so Quickbase returns them all.
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
    top: int | None = None
    skip: int | None = None
    report_id: str | None = None
    method: str
    path: str


ReadPolicy = Callable[[ReadRequest], bool]
"""A function that decides on a read. See `Quickbase`'s `read_policy` argument."""


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
        top=_plain_int(options.get("top")),  # pyright: ignore[reportUnknownMemberType]
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
    if (match := _FILE.fullmatch(path)) and not params:
        return ReadRequest(
            action="file",
            table_id=match[1],
            field_ids=frozenset({int(match[2])}),
            method="GET",
            path=path,
        )
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
        top=_plain_int(found.get("top")),
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
    if method == "DELETE" and _FILE.fullmatch(path):
        return None
    if is_schema_write(method, path, body, params):
        # A schema change reveals no values. Formulas, lookups, and summaries could, so the client
        # only sends one after checking that it reads nothing hidden.
        return None
    return ReadRequest(action="other", method=method, path=path)


def check_read(policy: ReadPolicy, request: ReadRequest) -> None:
    """Refuse the read unless the policy returns `True`. Anything else, even truthy, refuses."""
    try:
        allowed = policy(request)
    except ReadRefusedError:
        raise
    except Exception as exc:
        raise ReadRefusedError(
            f"The read policy raised {type(exc).__name__}, so this client won't send "
            f"{request.method} {request.path}."
        ) from exc
    if allowed is not True:
        raise ReadRefusedError(
            f"The read policy refused this {request.action}, so this client won't send "
            f"{request.method} {request.path}."
        )


def _optional_access(
    name: str, value: Any, default: bool | frozenset[int]
) -> bool | frozenset[int]:
    return default if value is None else _access(name, value)


def _id_list(name: str, value: Any) -> frozenset[int]:
    if isinstance(value, str | bytes) or not isinstance(value, Collection):
        raise ConfigurationError(f"{name} takes a list of field IDs.")
    if not value:
        return frozenset()
    found = _access(name, value)
    assert isinstance(found, frozenset)
    return found


def _report_ids(name: str, value: Any) -> frozenset[str]:
    if isinstance(value, str | bytes) or not isinstance(value, Collection):
        raise ConfigurationError(f"{name} takes a list of report IDs.")
    ids: set[str] = set()
    items: Collection[Any] = value  # pyright: ignore[reportUnknownVariableType]
    for item in items:
        text: Any = str(item) if type(item) is int else item
        if not isinstance(text, str) or not text.isdigit() or not text.isascii():
            raise ConfigurationError(
                f"{name} takes report IDs, not names, because a name can be repeated or renamed. "
                "Use report.info().id to look them up."
            )
        ids.add(str(int(text)))
    return frozenset(ids)


def _limit(name: str, value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ConfigurationError(f"{name} must be a positive whole number.")
    return value


def _allows(access: bool | frozenset[int], field_id: int) -> bool:
    if field_id == RECORD_ID_FIELD or access is True:
        return True
    return access is not False and field_id in access


@dataclass(frozen=True, slots=True, init=False)
class ReadAccess:
    """What a client may read from one table, for `ReadRules`. Anything not granted is refused.

    Field arguments take `True` for every field, including ones added later, or a list of field
    IDs. `Record ID#` is always allowed, as the SDK uses it to page through records.

    Args:
        fields: The fields it may return.
        filter: The fields it may filter on. Defaults to `fields`. A filter shows which records
            match, so a field here reveals something about its values even if it isn't returned.
        sort: The fields it may sort by. Defaults to `fields`. Sorting reveals order, in the same
            way.
        write_only: Fields it may write but never read, so that their labels can be used.
        files: File fields it may download from. Listing one in `fields` only returns its
            details, such as the file's name.
        reports: Table reports it may run, by ID. Columns outside `fields` are left out, but the
            report's own filter and sort can use any field.
        summary_reports: Summary and chart reports it may run, by ID. Their columns can't be
            matched to fields, so listing one allows all of them.
        max_records: The most records one query or report can reach, counting `skip`, so a query
            needs a `limit` of at most this. It's how far paging can go, not the size of a page,
            so give an operation its own, lower `max_records` for that, and set this as high as
            you want a caller to be able to page.
        max_summary_records: The most records `summarize()` may read for one summary.
        user_emails: Whether to keep email addresses in user values, and the uploader's details
            in file values. Without this, a user is only their `id` and `name`.

    Raises:
        ConfigurationError: A list holds anything but IDs, or a limit isn't a positive number.
    """

    fields: bool | frozenset[int]
    filter: bool | frozenset[int]
    sort: bool | frozenset[int]
    write_only: frozenset[int]
    files: frozenset[int]
    reports: frozenset[str]
    summary_reports: frozenset[str]
    max_records: int
    max_summary_records: int
    user_emails: bool

    def __init__(
        self,
        *,
        fields: bool | Collection[int],
        filter: bool | Collection[int] | None = None,
        sort: bool | Collection[int] | None = None,
        write_only: Collection[int] = (),
        files: Collection[int] = (),
        reports: Collection[str | int] = (),
        summary_reports: Collection[str | int] = (),
        max_records: int = DEFAULT_MAX_RECORDS,
        max_summary_records: int = DEFAULT_MAX_SUMMARY_RECORDS,
        user_emails: bool = False,
    ) -> None:
        if not isinstance(user_emails, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("user_emails takes True or False.")
        readable = _access("fields", fields)
        values = {
            "fields": readable,
            "filter": _optional_access("filter", filter, readable),
            "sort": _optional_access("sort", sort, readable),
            "write_only": _id_list("write_only", write_only),
            "files": _id_list("files", files),
            "reports": _report_ids("reports", reports),
            "summary_reports": _report_ids("summary_reports", summary_reports),
            "max_records": _limit("max_records", max_records),
            "max_summary_records": _limit("max_summary_records", max_summary_records),
            "user_emails": user_emails,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def _visible(self, field_id: int) -> bool:
        """Whether the field's metadata is shown, so that it can be named."""
        return (
            any(_allows(access, field_id) for access in (self.fields, self.filter, self.sort))
            or field_id in self.write_only
            or field_id in self.files
        )


def _refused(request: ReadRequest, reason: str) -> ReadRefusedError:
    return ReadRefusedError(
        f"This client's read rules don't allow this {request.action}: {reason} It wasn't sent."
    )


def _check_fields(
    request: ReadRequest, ids: frozenset[int] | None, access: bool | frozenset[int], word: str
) -> None:
    if ids is None:
        raise _refused(request, f"the fields it's {word} can't be determined.")
    extra = sorted(item for item in ids if not _allows(access, item))
    if extra:
        raise _refused(request, f"table {request.table_id} doesn't allow {word} fields {extra}.")


def _check_window(request: ReadRequest, limit: int, name: str) -> None:
    if request.top is None or request.skip is None:
        raise _refused(request, "it doesn't say how many records to return, so it would get all.")
    if request.top + request.skip > limit:
        raise _refused(
            request,
            f"table {request.table_id} allows reaching at most {limit} records, counting skip, "
            f"set by {name}, which is how far paging can go, not only the size of a page. Ask "
            "for fewer with limit or top, or narrow the filter.",
        )


class ReadRules:
    """A read policy that lists what a client may read from each table. Anything else is refused.

    ```python
    rules = ReadRules({"bqtasks123": ReadAccess(fields=[6, 7], max_records=50)})
    qb = Quickbase.from_env(read_policy=rules, read_only=True)
    ```

    Tables are named by ID, because a name can be renamed. A table or field that isn't listed acts
    as if the token couldn't see it: `app.tables()` and `table.fields()` leave it out, and naming it
    raises `UnknownTableError` or `UnknownFieldError`. Asking for more than is allowed, such as
    filtering on a field that's only returned, raises `ReadRefusedError`.

    It also refuses running formulas, app details and roles, reports through `request()`, and
    any request it doesn't recognize. Changes to tables and fields are left to the write limits,
    except that a formula, lookup, or summary field could show whatever this hides. So the SDK's
    own methods check that one only reads what this allows, and `request()` can't make one. A
    field's formula is only shown when everything it uses is readable.

    Args:
        tables: What's allowed, as `{table ID: ReadAccess(...)}`.
        new_tables: What it may read from the tables this client creates, which are added to
            `tables` as each is created. None hides them, even from the client that made them.

    Raises:
        ConfigurationError: A key isn't a table ID, or a value isn't a `ReadAccess`.
    """

    __slots__ = ("_new_tables", "_tables")

    def __init__(
        self, tables: Mapping[str, ReadAccess], *, new_tables: ReadAccess | None = None
    ) -> None:
        if not isinstance(tables, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("ReadRules takes a dictionary of {table ID: ReadAccess(...)}.")
        for table_id, access in tables.items():
            if not isinstance(table_id, str) or not table_id:  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError("ReadRules needs a table ID for each table.")
            if not isinstance(access, ReadAccess):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError(f"Table {table_id} needs a ReadAccess(...).")
        if new_tables is not None and not isinstance(new_tables, ReadAccess):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("new_tables takes a ReadAccess(...), or None.")
        self._tables = dict(tables)
        self._new_tables = new_tables

    def _adopt(self, table_id: str) -> None:
        """Give a table this client created the `new_tables` access."""
        if self._new_tables is not None:
            self._tables.setdefault(table_id, self._new_tables)

    def _access(self, table_id: str) -> ReadAccess | None:
        return self._tables.get(table_id)

    def __call__(self, request: ReadRequest) -> bool:
        """Return `True` if the read is allowed, or raise `ReadRefusedError` saying why not."""
        action = request.action
        if action == "tables":
            return True
        if action == "formula":
            raise _refused(request, "a formula can read other tables and fields, so none can run.")
        if action in ("app", "roles"):
            raise _refused(request, "only the listed tables can be read.")
        if action == "other":
            raise _refused(
                request,
                "only records, reports, files, and the listed tables' metadata can be read, and "
                "tables and fields can't be changed. A report can't be run through request().",
            )
        if request.table_id is None:
            raise _refused(request, "its table can't be determined.")
        access = self._tables.get(request.table_id)
        if access is None:
            raise _refused(request, f"table {request.table_id} isn't listed.")
        if action in ("query", "summarize"):
            _check_fields(request, request.field_ids, access.fields, "returning")
            _check_fields(request, request.filter_field_ids, access.filter, "filtering on")
            _check_fields(request, request.sort_field_ids, access.sort, "sorting by")
            if action == "summarize":
                _check_window(request, access.max_summary_records, "max_summary_records")
            else:
                _check_window(request, access.max_records, "max_records")
        elif action == "delete":
            _check_fields(request, request.filter_field_ids, access.filter, "filtering on")
        elif action in ("report", "summary_report"):
            listed = access.reports if action == "report" else access.summary_reports
            if request.report_id not in listed:
                kind = "table" if action == "report" else "summary"
                raise _refused(request, f"report {request.report_id} isn't a listed {kind} report.")
            _check_window(request, access.max_records, "max_records")
        elif action == "file":
            if request.field_ids is None or not request.field_ids <= access.files:
                raise _refused(request, f"table {request.table_id} doesn't allow downloading it.")
        return True

    def _record_limit(self, table_id: str) -> int | None:
        """The most records one query may reach in a table, or None if it isn't listed."""
        access = self._tables.get(table_id)
        return None if access is None else access.max_records

    def _summary_limit(self, table_id: str) -> int | None:
        """The most records `summarize()` may read from a table, or None if it isn't listed."""
        access = self._tables.get(table_id)
        return None if access is None else access.max_summary_records

    def _trim_response(
        self, request: ReadRequest | None, method: str, path: str, payload: Any
    ) -> Any:
        """Return the response with only what this allows. A response of the wrong shape raises
        `InvalidResponseError`, rather than passing through unchecked."""
        if request is None:
            # A deleted file version's details include the uploader, and the SDK doesn't use them.
            return None if method == "DELETE" and _FILE.fullmatch(path) else payload
        try:
            return self._trim(request, payload)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise InvalidResponseError(
                f"Quickbase returned an unexpected response to {method} {path}, so this client's "
                "read rules couldn't check it."
            ) from exc

    def _trim(self, request: ReadRequest, payload: Any) -> Any:
        action = request.action
        if action == "tables":
            return [_pick(item, _TABLE_KEYS) for item in payload if item["id"] in self._tables]
        access = self._tables.get(request.table_id or "")
        if access is None:
            # The check refuses these first, so this only guards against a change to it.
            raise _refused(request, "its table isn't listed.")
        if action == "fields":
            readable = [item["label"] for item in payload if _allows(access.fields, item["id"])]
            return [
                _trim_field(item, access, readable)
                for item in payload
                if access._visible(item["id"])  # pyright: ignore[reportPrivateUsage]
            ]
        if action == "reports":
            listed = access.reports | access.summary_reports
            return [_pick(item, _REPORT_KEYS) for item in payload if str(int(item["id"])) in listed]
        if action == "relationships":
            return {
                **payload,
                "relationships": [
                    self._trim_relationship(item, access) for item in payload["relationships"]
                ],
            }
        if action in ("query", "summarize", "report"):
            return _trim_records(payload, access, keep_all=False)
        if action == "summary_report":
            return _trim_records(payload, access, keep_all=True)
        return payload

    def _trim_relationship(self, item: Mapping[str, Any], child: ReadAccess) -> dict[str, Any]:
        """Keep each relationship, so paging by count still works, but only its visible fields."""
        parent = self._tables.get(item["parentTableId"])
        kept: dict[str, Any] = _pick(item, ("id", "parentTableId", "childTableId", "isCrossApp"))
        key = item.get("foreignKeyField")
        if key and child._visible(key["id"]):  # pyright: ignore[reportPrivateUsage]
            kept["foreignKeyField"] = _pick(key, ("id", "label"))
        kept["lookupFields"] = [
            _pick(field, ("id", "label"))
            for field in item.get("lookupFields") or ()
            if child._visible(field["id"])  # pyright: ignore[reportPrivateUsage]
        ]
        kept["summaryFields"] = [
            _pick(field, ("id", "label"))
            for field in item.get("summaryFields") or ()
            if parent is not None and parent._visible(field["id"])  # pyright: ignore[reportPrivateUsage]
        ]
        return kept

    def __repr__(self) -> str:
        return f"ReadRules(tables={sorted(self._tables)!r})"


def _pick(item: Mapping[str, Any], keys: Collection[str]) -> dict[str, Any]:
    return {key: item[key] for key in keys if key in item}


def _trim_field(
    item: Mapping[str, Any], access: ReadAccess, readable: Collection[str]
) -> dict[str, Any]:
    kept = _pick(item, _FIELD_KEYS)
    # A field that can't be returned mustn't be selected by default, or every default query fails.
    if not _allows(access.fields, item["id"]):
        kept["appearsByDefault"] = False
    properties = item.get("properties")
    if isinstance(properties, Mapping):
        found = cast(Mapping[str, Any], properties)
        kept["properties"] = _pick(found, _FIELD_PROPERTIES)
        formula = found.get("formula")
        if isinstance(formula, str) and _shows_only(formula, readable):
            kept["properties"]["formula"] = formula
    return kept


def _shows_only(formula: str, readable: Collection[str]) -> bool:
    """Whether a formula's text reveals nothing hidden: every field it names is readable, and it
    calls no function that reads other records."""
    try:
        parts = formula_parts(formula)
    except ReadRefusedError:
        return False
    return not unreadable_fields(parts, readable) and not unsafe_functions(parts)


def _trim_value(value: Any) -> Any:
    """Leave out emails and uploaders, recognizing users and files by their shape, as a formula or
    lookup can return either and reports its type as its own."""
    if isinstance(value, list):
        return [_trim_value(item) for item in cast(list[Any], value)]
    if not isinstance(value, Mapping):
        return value
    found = cast(Mapping[str, Any], value)
    if "versions" in found:
        versions: list[Mapping[str, Any]] = found["versions"]
        return {
            "url": found.get("url", ""),
            "versions": [_pick(version, _FILE_VERSION_KEYS) for version in versions],
        }
    if "email" in found or "userName" in found:
        return _pick(found, _USER_KEYS)
    return found


def _trim_records(payload: Mapping[str, Any], access: ReadAccess, *, keep_all: bool) -> Any:
    """Keep only the allowed columns. A summary report's IDs are column positions, not fields, so
    `keep_all` keeps every column of a report that was listed on purpose."""
    keep = {
        int(item["id"])
        for item in payload["fields"]
        if keep_all or _allows(access.fields, int(item["id"]))
    }
    emails = access.user_emails
    return {
        **payload,
        "fields": [_pick(item, _COLUMN_KEYS) for item in payload["fields"] if item["id"] in keep],
        "data": [
            {
                key: {"value": cell["value"] if emails else _trim_value(cell["value"])}
                for key, cell in row.items()
                if int(key) in keep
            }
            for row in payload["data"]
        ],
    }


def validate_read_policy(policy: ReadPolicy | None) -> ReadPolicy | None:
    if policy is None:
        return None
    if not callable(policy):
        raise ConfigurationError("read_policy must be a function, or ReadRules.")
    if inspect.iscoroutinefunction(policy):
        raise ConfigurationError(
            "read_policy can't be async, because it would never finish before the request is sent."
        )
    return policy
