"""Describe a write, for a policy to decide on. No network access.

There are two separate steps, so a new rule needs no new parsing and a new endpoint needs no new
rules. `describe_write()` is the only code that knows how Quickbase's requests look, and a policy
only sees the `WriteRequest` it produces. That holds no values, so a policy can't leak or log them.

Anything that can't be understood is described as unknown, and the built-in rules refuse what's
unknown wherever it matters. See docs/api-notes.md for how Quickbase reads these requests.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from ._models import RECORD_ID_FIELD

WriteAction = Literal[
    "insert",
    "update",
    "upsert",
    "delete",
    "delete_file",
    "create_table",
    "update_table",
    "delete_table",
    "create_field",
    "update_field",
    "delete_field",
    "create_relationship",
    "create_lookup_field",
    "create_summary_field",
    "update_relationship",
    "delete_relationship",
    "create_app",
    "copy_app",
    "delete_app",
    "write_code_page",
    "write_role_assignments",
    "set_key_field",
    "other",
]
"""What a write does. See `WriteRequest`."""

DerivedKind = Literal["formula", "lookup", "summary"]
"""A field whose values come from other fields. See `WriteRequest.derived`."""

SCHEMA_ACTIONS: frozenset[str] = frozenset(
    action
    for action in get_args(WriteAction)
    if action.endswith(("_table", "_field", "_relationship"))
)
"""The actions that change tables, fields, and relationships, rather than records."""

UNSAFE_FORMULA_TYPES = frozenset({"url", "rich-text"})
"""Formula types that make links or markup someone may click, which act as whoever clicks them."""

_FILE_PATH = re.compile(r"/files/([A-Za-z0-9]+)/[0-9]+/([0-9]+)/[0-9]+")
_TABLE_PATH = re.compile(r"/tables/([A-Za-z0-9]+)")
_APP_PATH = re.compile(r"/apps/([A-Za-z0-9]+)")
_APP_COPY_PATH = re.compile(r"/apps/([A-Za-z0-9]+)/copy")
_FIELD_PATH = re.compile(r"/fields/([1-9][0-9]*)")
_TRUSTEES_PATH = re.compile(r"/app/([A-Za-z0-9]+)/trustees")
_RELATIONSHIP_PATH = re.compile(r"/tables/([A-Za-z0-9]+)/relationship(?:/([1-9][0-9]*))?")
_FIELD_KEY = re.compile(r"[1-9][0-9]*")
_RECORD_ID_KEY = str(RECORD_ID_FIELD)


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteRequest:
    """What a write would do, for a policy to decide on.

    It holds no values from the request, so a policy can log it safely. `None` always means that
    this couldn't be determined, so check for it rather than treating it as nothing.

    Attributes:
        action: `insert` only creates records, and `update` only changes them. `upsert` may do
            either. That includes a write that merges on any field other than `Record ID#`, a row
            whose field IDs aren't written in the usual way, and any write without a merge field to
            a table whose key field isn't `Record ID#`, or through `request()`, which can't tell.
            `delete` deletes records, and `delete_file` deletes a version of a file.
            `create_table`, `update_table`, `delete_table`, `create_field`, `update_field`,
            `delete_field`, `create_relationship`, `create_lookup_field`, `create_summary_field`,
            `update_relationship`, and `delete_relationship` change the schema.
            `create_lookup_field` and `create_summary_field` add fields to an existing
            relationship, as schema plans call them. `update_relationship` is a request to an
            existing relationship that adds both kinds at once, or neither, which only `request()`
            can send. `request.derived` says which kinds any of them adds. `write_code_page`
            creates or replaces a code page, which only `allow.code_pages(write=True)` permits.
            `write_role_assignments` gives or takes away a role, which no policy can allow.
            `set_key_field` changes a table's key field. `other` is any other write.
        table_id: The table it writes to. For a relationship, the child table.
        field_ids: The fields whose values it sets, or for `update_field` and `delete_field`, the
            fields it changes or deletes. For `update`, this leaves out `Record ID#`, which only
            says which record to change. `None` for `delete`, which affects whole records, and for
            other schema changes.
        row_count: How many rows are in this request, which is one batch of a larger write. `None`
            for anything but `insert`, `update`, and `upsert`, as a delete by filter can't know.
        method: The HTTP method.
        path: The request's path, without its query parameters.
        app_id: For `create_table` and `update_table`, the app, for `copy_app` and
            `delete_app`, the app copied or deleted, and for `write_code_page`, the app.
        parent_table_id: For a relationship, the parent table. Only `create_relationship` says
            which it is, so through `request()` it's `None` for the others.
        field_type: For `create_field`, the new field's type, such as `text`.
        derived: The formula, lookup, and summary fields it makes or changes, which read other
            fields. A summary is made on the parent table.
        allows_html: For `create_field` and `update_field`, whether it turns on the field's
            `allowHTML` setting, so that its values show as markup.
    """

    action: WriteAction
    table_id: str | None
    field_ids: frozenset[int] | None
    row_count: int | None
    method: str
    path: str
    app_id: str | None = None
    parent_table_id: str | None = None
    field_type: str | None = None
    derived: frozenset[DerivedKind] = frozenset()
    allows_html: bool = False


def _describe_rows(path: str, body: Mapping[str, Any], key_field_id: int | None) -> WriteRequest:
    to = body.get("to")
    table_id = to if isinstance(to, str) else None
    rows = body.get("data")
    if not isinstance(rows, list):
        return _other("POST", path)
    field_ids: set[int] | None = set()
    with_record_id = 0
    # Without a merge field, a row with an existing value in the table's key field changes that
    # record. Only a key field of Record ID# can't do that by accident, as insert() refuses it.
    insert_certain = key_field_id == RECORD_ID_FIELD
    for row in rows:  # pyright: ignore[reportUnknownVariableType]
        # Quickbase also accepts keys like "07" and " 7" as field 7, so only the usual form counts.
        if not isinstance(row, Mapping) or not all(
            isinstance(key, str) and _FIELD_KEY.fullmatch(key)
            for key in row  # pyright: ignore[reportUnknownVariableType]
        ):
            field_ids = None
            break
        with_record_id += _RECORD_ID_KEY in row
        field_ids.update(int(key) for key in row)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    if field_ids is None:
        action: WriteAction = "upsert"
    elif "mergeFieldId" in body:
        merge = body["mergeFieldId"]
        action = "update" if type(merge) is int and merge == RECORD_ID_FIELD else "upsert"
    elif with_record_id == 0:
        action = "insert" if insert_certain else "upsert"
    else:
        # A row with a Record ID# changes that record, or fails if it doesn't exist.
        action = "update" if with_record_id == len(rows) else "upsert"  # pyright: ignore[reportUnknownArgumentType]
    if field_ids is not None and action == "update":
        field_ids.discard(RECORD_ID_FIELD)
    return WriteRequest(
        action=action,
        table_id=table_id,
        field_ids=None if field_ids is None else frozenset(field_ids),
        row_count=len(rows),  # pyright: ignore[reportUnknownArgumentType]
        method="POST",
        path=path,
    )


def _other(method: str, path: str) -> WriteRequest:
    return WriteRequest(
        action="other", table_id=None, field_ids=None, row_count=None, method=method, path=path
    )


def describe_write(
    method: str,
    path: str,
    body: Any,
    *,
    params: Mapping[str, Any] | None = None,
    key_field_id: int | None = None,
    parent_table_id: str | None = None,
) -> WriteRequest:
    """Describe a write from its request. Anything unrecognized is `other`.

    `key_field_id` is the table's key field, which a request doesn't say. Without it, a write that
    could be an insert is an `upsert`, as it could change an existing record. `parent_table_id` is
    a relationship's parent, which only a request to create one says.
    """
    if path == "/records" and isinstance(body, Mapping):
        if method == "POST":
            return _describe_rows(path, body, key_field_id)  # pyright: ignore[reportUnknownArgumentType]
        if method == "DELETE":
            source = body.get("from")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            return WriteRequest(
                action="delete",
                table_id=source if isinstance(source, str) else None,
                field_ids=None,
                row_count=None,
                method=method,
                path=path,
            )
    found = _FILE_PATH.fullmatch(path)
    if method == "DELETE" and found:
        return WriteRequest(
            action="delete_file",
            table_id=found[1],
            field_ids=frozenset({int(found[2])}),
            row_count=None,
            method=method,
            path=path,
        )
    app = _describe_app(method, path)
    if app is not None:
        return app
    trustees = _TRUSTEES_PATH.fullmatch(path)
    if trustees and method in ("POST", "PATCH", "DELETE"):
        return WriteRequest(
            action="write_role_assignments",
            table_id=None,
            field_ids=None,
            row_count=None,
            method=method,
            path=path,
            app_id=trustees[1],
        )
    schema = _describe_schema(method, path, body, params or {}, parent_table_id)
    return _other(method, path) if schema is None else schema


def _describe_app(method: str, path: str) -> WriteRequest | None:
    """Describe creating, copying, or deleting an app, or return None if it isn't one."""
    action: WriteAction
    app_id = None
    if method == "POST" and path == "/apps":
        action = "create_app"
    elif method == "POST" and (found := _APP_COPY_PATH.fullmatch(path)):
        action, app_id = "copy_app", found[1]
    elif method == "DELETE" and (found := _APP_PATH.fullmatch(path)):
        action, app_id = "delete_app", found[1]
    else:
        return None
    return WriteRequest(
        action=action,
        table_id=None,
        field_ids=None,
        row_count=None,
        method=method,
        path=path,
        app_id=app_id,
    )


def is_schema_write(method: str, path: str, body: Any, params: Mapping[str, Any] | None) -> bool:
    """Whether a request changes a table, field, or relationship."""
    return _describe_schema(method, path, body, params or {}, None) is not None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _html_in(body: Mapping[str, Any]) -> bool:
    """Whether a field's body turns on `allowHTML`. Anything but an explicit false counts."""
    properties = body.get("properties")
    if not isinstance(properties, Mapping) or "allowHTML" not in properties:
        return False
    return cast(Mapping[str, Any], properties)["allowHTML"] is not False


def _formula_in(body: Mapping[str, Any]) -> frozenset[DerivedKind]:
    """A formula, if a field's body sets one, which even an empty one does."""
    properties = body.get("properties")
    if isinstance(properties, Mapping) and "formula" in properties:
        return frozenset({"formula"})
    return frozenset()


def _id_set(value: Any) -> frozenset[int] | None:
    """Field IDs from a request, or None unless they're all written in the usual way."""
    if not isinstance(value, list):
        return None
    ids = cast(list[Any], value)
    if not all(type(item) is int and item > 0 for item in ids):
        return None
    return frozenset(cast(list[int], ids))


def _relationship_fields(body: Mapping[str, Any]) -> frozenset[DerivedKind] | None:
    """The derived fields a relationship request adds, or None if they can't be read."""
    lookups, summaries = body.get("lookupFieldIds"), body.get("summaryFields")
    if not isinstance(lookups, list | None) or not isinstance(summaries, list | None):
        return None
    kinds: set[DerivedKind] = set()
    if lookups:
        kinds.add("lookup")
    if summaries:
        kinds.add("summary")
    return frozenset(kinds)


def _describe_schema(
    method: str,
    path: str,
    body: Any,
    params: Mapping[str, Any],
    parent_table_id: str | None,
) -> WriteRequest | None:
    """Describe a change to a table, field, or relationship, or return None if it isn't one."""

    def schema(
        action: WriteAction,
        *,
        table_id: str | None,
        field_ids: frozenset[int] | None = None,
        app_id: str | None = None,
        parent_table_id: str | None = None,
        field_type: str | None = None,
        derived: frozenset[DerivedKind] = frozenset(),
        allows_html: bool = False,
    ) -> WriteRequest:
        return WriteRequest(
            action=action,
            table_id=table_id,
            field_ids=field_ids,
            row_count=None,
            method=method,
            path=path,
            app_id=app_id,
            parent_table_id=parent_table_id,
            field_type=field_type,
            derived=derived,
            allows_html=allows_html,
        )

    data = cast(Mapping[str, Any], body) if isinstance(body, Mapping) else None
    table_param = _text(params.get("tableId"))
    if path == "/tables" and method == "POST":
        return schema("create_table", table_id=None, app_id=_text(params.get("appId")))
    if (found := _TABLE_PATH.fullmatch(path)) and method in ("POST", "DELETE"):
        action: WriteAction = "update_table" if method == "POST" else "delete_table"
        return schema(action, table_id=found[1], app_id=_text(params.get("appId")))
    if path == "/fields" and method == "POST" and data is not None:
        return schema(
            "create_field",
            table_id=table_param,
            field_type=_text(data.get("fieldType")),
            derived=_formula_in(data),
            allows_html=_html_in(data),
        )
    if (found := _FIELD_PATH.fullmatch(path)) and method == "POST" and data is not None:
        return schema(
            "update_field",
            table_id=table_param,
            field_ids=frozenset({int(found[1])}),
            derived=_formula_in(data),
            allows_html=_html_in(data),
        )
    if path == "/fields" and method == "DELETE" and data is not None:
        return schema("delete_field", table_id=table_param, field_ids=_id_set(data.get("fieldIds")))
    found = _RELATIONSHIP_PATH.fullmatch(path)
    if found is None:
        return None
    child, relationship_id = found[1], found[2]
    if method == "DELETE" and relationship_id:
        return schema("delete_relationship", table_id=child, parent_table_id=parent_table_id)
    if method != "POST" or data is None:
        return None
    derived = _relationship_fields(data)
    if derived is None:
        return None
    if relationship_id:
        action = "update_relationship"
        if derived == frozenset[DerivedKind]({"lookup"}):
            action = "create_lookup_field"
        elif derived == frozenset[DerivedKind]({"summary"}):
            action = "create_summary_field"
        return schema(action, table_id=child, parent_table_id=parent_table_id, derived=derived)
    return schema(
        "create_relationship",
        table_id=child,
        parent_table_id=_text(data.get("parentTableId")),
        derived=derived,
    )
