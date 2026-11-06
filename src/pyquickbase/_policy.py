"""Decide whether a write may be sent. No network access.

There are two separate steps, so a new rule needs no new parsing and a new endpoint needs no new
rules. `describe_write()` is the only code that knows how Quickbase's requests look, and a policy
only sees the `WriteRequest` it produces. That holds no values, so a policy can't leak or log them.

Anything that can't be understood is described as unknown, and the built-in rules refuse what's
unknown wherever it matters. See docs/api-notes.md for how Quickbase reads these requests.
"""

import inspect
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from ._models import RECORD_ID_FIELD
from .errors import ConfigurationError, WriteRefusedError

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
    "update_relationship",
    "delete_relationship",
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
_FIELD_PATH = re.compile(r"/fields/([1-9][0-9]*)")
_RELATIONSHIP_PATH = re.compile(r"/tables/([A-Za-z0-9]+)/relationship(?:/([1-9][0-9]*))?")
_FIELD_KEY = re.compile(r"[1-9][0-9]*")
_RECORD_ID_KEY = str(RECORD_ID_FIELD)


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteRequest:
    """What a write would do, for a write policy to decide on.

    It holds no values from the request, so a policy can log it safely. `None` always means that
    this couldn't be determined, so check for it rather than treating it as nothing.

    Attributes:
        action: `insert` only creates records, and `update` only changes them. `upsert` may do
            either. That includes a write that merges on any field other than `Record ID#`, a row
            whose field IDs aren't written in the usual way, and any write without a merge field to
            a table whose key field isn't `Record ID#`, or through `request()`, which can't tell.
            `delete` deletes records, and `delete_file` deletes a version of a file.
            `create_table`, `update_table`, `delete_table`, `create_field`, `update_field`,
            `delete_field`, `create_relationship`, `update_relationship`, and
            `delete_relationship` change the schema. `update_relationship` adds lookup or summary
            fields. `other` is any other write.
        table_id: The table it writes to. For a relationship, the child table.
        field_ids: The fields whose values it sets, or for `update_field` and `delete_field`, the
            fields it changes or deletes. For `update`, this leaves out `Record ID#`, which only
            says which record to change. `None` for `delete`, which affects whole records, and for
            other schema changes.
        row_count: How many rows are in this request, which is one batch of a larger write. `None`
            for anything but `insert`, `update`, and `upsert`, as a delete by filter can't know.
        method: The HTTP method.
        path: The request's path, without its query parameters.
        app_id: For `create_table` and `update_table`, the app.
        parent_table_id: For a relationship, the parent table. Only `create_relationship` says
            which it is, so through `request()` it's `None` for the others.
        field_type: For `create_field`, the new field's type, such as `text`.
        derived: The formula, lookup, and summary fields it makes or changes, which read other
            fields. A summary is made on the parent table.
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


WritePolicy = Callable[[WriteRequest], bool]
"""A function that decides on a write. See `Quickbase`'s `write_policy` argument."""


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
    schema = _describe_schema(method, path, body, params or {}, parent_table_id)
    return _other(method, path) if schema is None else schema


def is_schema_write(method: str, path: str, body: Any, params: Mapping[str, Any] | None) -> bool:
    """Whether a request changes a table, field, or relationship."""
    return _describe_schema(method, path, body, params or {}, None) is not None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


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
        )
    if (found := _FIELD_PATH.fullmatch(path)) and method == "POST" and data is not None:
        return schema(
            "update_field",
            table_id=table_param,
            field_ids=frozenset({int(found[1])}),
            derived=_formula_in(data),
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
        return schema(
            "update_relationship", table_id=child, parent_table_id=parent_table_id, derived=derived
        )
    return schema(
        "create_relationship",
        table_id=child,
        parent_table_id=_text(data.get("parentTableId")),
        derived=derived,
    )


def check_policy(policy: WritePolicy, request: WriteRequest) -> None:
    """Refuse the write unless the policy returns `True`. Anything else, even truthy, refuses."""
    try:
        allowed = policy(request)
    except WriteRefusedError:
        raise
    except Exception as exc:
        raise WriteRefusedError(
            f"The write policy raised {type(exc).__name__}, so this client won't send "
            f"{request.method} {request.path}."
        ) from exc
    if allowed is not True:
        raise WriteRefusedError(
            f"The write policy refused this {request.action}, so this client won't send "
            f"{request.method} {request.path}."
        )


def validate_write_policy(policy: WritePolicy | None, *, read_only: bool) -> WritePolicy | None:
    if policy is None:
        return None
    if read_only:
        raise ConfigurationError("Use read_only or write_policy, not both.")
    if not callable(policy):
        raise ConfigurationError("write_policy must be a function, or WriteRules.")
    if inspect.iscoroutinefunction(policy):
        raise ConfigurationError(
            "write_policy can't be async, because it would never finish before the request is sent."
        )
    return policy


def _access(name: str, value: Any) -> bool | frozenset[int]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str | bytes) or not isinstance(value, Collection):
        raise ConfigurationError(f"{name} takes True, False, or a list of field IDs.")
    ids = frozenset(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    if not ids:
        raise ConfigurationError(f"{name} has an empty list of fields. Use False to refuse it.")
    for item in ids:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise ConfigurationError(
                f"{name} takes field IDs, not labels, because a label can be renamed. Use "
                "table.resolve_field_ids() to look them up."
            )
    return ids  # pyright: ignore[reportUnknownVariableType]


def _flag(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} takes True or False.")
    return value


@dataclass(frozen=True, slots=True, init=False)
class SchemaAccess:
    """What a client may change in one table's schema, for `WriteAccess`. Anything not granted is
    refused.

    ```python
    WriteAccess(insert=True, schema=SchemaAccess(create_fields=True, field_types=["text", "date"]))
    ```

    Formula, lookup, and summary fields each need their own grant, as they read other fields. On a
    client with `ReadRules`, the SDK also checks that they only read what the client may read, so
    only its own methods can make them, not `request()`. A formula can't make a link or rich text
    (`url` or `rich-text`), as that acts as whoever clicks it.

    Args:
        update_table: Whether it may rename the table or change its descriptions.
        delete_table: Whether it may delete the table and all its records.
        create_fields: Whether it may add fields.
        update_fields: Whether it may change fields' settings, and which, as `True` or a list of
            field IDs.
        delete_fields: Whether it may delete fields and their data, and which.
        field_types: The types it may create, such as `["text", "numeric"]`, or None for any.
            Formula fields count by their result type.
        formulas: Whether it may create formula fields or change formulas. Needs `create_fields` or
            `update_fields` too.
        lookups: Whether it may add lookup fields to this table, as the child.
        summaries: Whether it may add summary fields to this table, as the parent.
        relationships: Whether it may link this table to another, as the child or the parent. Both
            tables need it. Deleting a relationship deletes its lookups and summaries, so it needs
            `delete_fields=True` on both tables too.

    Raises:
        ConfigurationError: A setting has the wrong type, or a list is empty.
    """

    update_table: bool
    delete_table: bool
    create_fields: bool
    update_fields: bool | frozenset[int]
    delete_fields: bool | frozenset[int]
    field_types: frozenset[str] | None
    formulas: bool
    lookups: bool
    summaries: bool
    relationships: bool

    def __init__(
        self,
        *,
        update_table: bool = False,
        delete_table: bool = False,
        create_fields: bool = False,
        update_fields: bool | Collection[int] = False,
        delete_fields: bool | Collection[int] = False,
        field_types: Collection[str] | None = None,
        formulas: bool = False,
        lookups: bool = False,
        summaries: bool = False,
        relationships: bool = False,
    ) -> None:
        flags = {
            "update_table": update_table,
            "delete_table": delete_table,
            "create_fields": create_fields,
            "formulas": formulas,
            "lookups": lookups,
            "summaries": summaries,
            "relationships": relationships,
        }
        for name, value in flags.items():
            object.__setattr__(self, name, _flag(name, value))
        object.__setattr__(self, "update_fields", _access("update_fields", update_fields))
        object.__setattr__(self, "delete_fields", _access("delete_fields", delete_fields))
        types: frozenset[str] | None = None
        if field_types is not None:
            items: Collection[Any] = field_types
            if isinstance(field_types, str) or not all(
                isinstance(item, str) and item for item in items
            ):
                raise ConfigurationError("field_types takes a list of field types, or None.")
            types = frozenset(field_types)
            if not types:
                raise ConfigurationError("field_types is empty. Use create_fields=False instead.")
        object.__setattr__(self, "field_types", types)


@dataclass(frozen=True, slots=True, init=False)
class WriteAccess:
    """What a client may do to one table, for `WriteRules`. Anything not granted is refused.

    `insert` and `update` each take `True` for any field, or a list of the field IDs they may set.
    An `upsert` needs both, and its fields need to be in both, including the one it merges on.

    Args:
        insert: Whether it may create records, and with which fields.
        update: Whether it may change records, and which fields.
        delete: Whether it may delete records. Field lists don't limit this, as deleting a record
            removes all of its fields.
        delete_files: Whether it may delete file versions.
        schema: What it may change in the table's schema, or None for nothing.

    Raises:
        ConfigurationError: A list is empty, or holds anything but field IDs.
    """

    insert: bool | frozenset[int]
    update: bool | frozenset[int]
    delete: bool
    delete_files: bool
    schema: SchemaAccess | None

    def __init__(
        self,
        *,
        insert: bool | Collection[int] = False,
        update: bool | Collection[int] = False,
        delete: bool = False,
        delete_files: bool = False,
        schema: SchemaAccess | None = None,
    ) -> None:
        for name, value in (("delete", delete), ("delete_files", delete_files)):
            _flag(name, value)
        if schema is not None and not isinstance(schema, SchemaAccess):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("schema takes a SchemaAccess(...), or None.")
        object.__setattr__(self, "insert", _access("insert", insert))
        object.__setattr__(self, "update", _access("update", update))
        object.__setattr__(self, "delete", delete)
        object.__setattr__(self, "delete_files", delete_files)
        object.__setattr__(self, "schema", schema)


def _refused(request: WriteRequest, reason: str) -> WriteRefusedError:
    return WriteRefusedError(
        f"This client's write rules don't allow this {request.action}: {reason} It wasn't sent."
    )


def _check_fields(request: WriteRequest, access: bool | frozenset[int], word: str) -> None:
    if access is False:
        raise _refused(request, f"{word} isn't allowed on table {request.table_id}.")
    if access is True:
        return
    if request.field_ids is None:
        raise _refused(request, "its fields can't be determined, and this table limits them.")
    extra = request.field_ids - access
    if extra:
        raise _refused(request, f"table {request.table_id} doesn't allow fields {sorted(extra)}.")


class WriteRules:
    """A write policy that lists what a client may do to each table. Anything else is refused.

    ```python
    rules = WriteRules({"bqtasks123": WriteAccess(insert=True, update=[7, 12])})
    qb = Quickbase.from_env(write_policy=rules)
    ```

    Tables are named by ID and must match exactly, because a name can be renamed. Changes to a
    table's schema need its `WriteAccess(schema=...)`. Any other write is refused, so to allow it,
    write your own policy.

    Args:
        tables: What's allowed, as `{table ID: WriteAccess(...)}`.
        create_tables_in: The apps it may create tables in, by ID.
        new_tables: What it may do to the tables this client creates, which are added to `tables`
            as each is created. None allows nothing, not even adding fields.

    Raises:
        ConfigurationError: A key isn't a table ID, a value isn't a `WriteAccess`, or an app ID
            isn't text.
    """

    __slots__ = ("_create_tables_in", "_new_tables", "_tables")

    def __init__(
        self,
        tables: Mapping[str, WriteAccess],
        *,
        create_tables_in: Collection[str] = (),
        new_tables: WriteAccess | None = None,
    ) -> None:
        if not isinstance(tables, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError(
                "WriteRules takes a dictionary of {table ID: WriteAccess(...)}."
            )
        for table_id, access in tables.items():
            if not isinstance(table_id, str) or not table_id:  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError("WriteRules needs a table ID for each table.")
            if not isinstance(access, WriteAccess):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError(f"Table {table_id} needs an WriteAccess(...).")
        apps: Collection[Any] = create_tables_in
        if isinstance(create_tables_in, str) or not all(
            isinstance(item, str) and item for item in apps
        ):
            raise ConfigurationError("create_tables_in takes a list of app IDs.")
        if new_tables is not None and not isinstance(new_tables, WriteAccess):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("new_tables takes a WriteAccess(...), or None.")
        self._tables = dict(tables)
        self._create_tables_in = frozenset(create_tables_in)
        self._new_tables = new_tables

    def __call__(self, request: WriteRequest) -> bool:
        """Return `True` if the write is allowed, or raise `WriteRefusedError` saying why not."""
        action = request.action
        if action == "other":
            raise _refused(request, "only record, file, and schema writes can be allowed.")
        if action == "create_table":
            if request.app_id not in self._create_tables_in:
                raise _refused(request, f"creating tables isn't allowed in app {request.app_id}.")
            return True
        if request.table_id is None:
            raise _refused(request, "its table can't be determined.")
        access = self._tables.get(request.table_id)
        if access is None:
            raise _refused(request, f"table {request.table_id} isn't listed.")
        if action in SCHEMA_ACTIONS:
            self._check_schema(request, self._schema(request, request.table_id))
            return True
        if action in ("insert", "upsert"):
            _check_fields(request, access.insert, "inserting")
        if action in ("update", "upsert"):
            _check_fields(request, access.update, "updating")
        if action == "delete" and not access.delete:
            raise _refused(request, f"deleting isn't allowed on table {request.table_id}.")
        if action == "delete_file" and not access.delete_files:
            raise _refused(request, f"deleting files isn't allowed on table {request.table_id}.")
        return True

    def _schema(self, request: WriteRequest, table_id: str | None) -> SchemaAccess:
        if table_id is None:
            raise _refused(request, "its parent table can't be determined.")
        access = self._tables.get(table_id)
        if access is None:
            raise _refused(request, f"table {table_id} isn't listed.")
        if access.schema is None:
            raise _refused(request, f"table {table_id} doesn't allow schema changes.")
        return access.schema

    def _check_schema(self, request: WriteRequest, schema: SchemaAccess) -> None:
        action, table = request.action, request.table_id

        def need(granted: bool, what: str, on: str | None = table) -> None:
            if not granted:
                raise _refused(request, f"{what} isn't allowed on table {on}.")

        if action == "update_table":
            need(schema.update_table, "changing the table")
        elif action == "delete_table":
            need(schema.delete_table, "deleting the table")
        elif action == "create_field":
            need(schema.create_fields, "adding fields")
            self._check_type(request, schema)
        elif action == "update_field":
            _check_fields(request, schema.update_fields, "changing fields")
        elif action == "delete_field":
            _check_fields(request, schema.delete_fields, "deleting fields")
        else:
            parent = self._schema(request, request.parent_table_id)
            need(schema.relationships, "linking tables")
            need(parent.relationships, "linking tables", request.parent_table_id)
            if action == "delete_relationship":
                need(schema.delete_fields is True, "deleting every lookup")
                need(
                    parent.delete_fields is True, "deleting every summary", request.parent_table_id
                )
        if "formula" in request.derived:
            need(schema.formulas, "making formulas")
        if "lookup" in request.derived:
            need(schema.lookups, "adding lookups")
        if "summary" in request.derived:
            parent = self._schema(request, request.parent_table_id)
            need(parent.summaries, "adding summaries", request.parent_table_id)

    @staticmethod
    def _check_type(request: WriteRequest, schema: SchemaAccess) -> None:
        field_type = request.field_type
        if field_type is None:
            raise _refused(request, "its field type can't be determined.")
        if schema.field_types is not None and field_type not in schema.field_types:
            allowed = ", ".join(sorted(schema.field_types))
            raise _refused(
                request,
                f"table {request.table_id} only allows fields of type {allowed}.",
            )
        if "formula" in request.derived and field_type in UNSAFE_FORMULA_TYPES:
            raise _refused(
                request,
                f"a {field_type} formula makes something people click, which acts as them.",
            )

    def _adopt(self, table_id: str) -> None:
        """Give a table this client created the `new_tables` access."""
        if self._new_tables is not None:
            self._tables.setdefault(table_id, self._new_tables)

    def __repr__(self) -> str:
        return f"WriteRules(tables={sorted(self._tables)!r})"
