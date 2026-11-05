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
from typing import Any, Literal

from ._models import RECORD_ID_FIELD
from .errors import ConfigurationError, ReadOnlyError

WriteAction = Literal["insert", "update", "upsert", "delete", "delete_file", "other"]
"""What a write does. See `WriteRequest`."""

_FILE_PATH = re.compile(r"/files/([A-Za-z0-9]+)/[0-9]+/([0-9]+)/[0-9]+")
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
            `delete` deletes records,
            `delete_file` deletes a version of a file, and `other` is any other write, including
            changes to tables and fields.
        table_id: The table it writes to.
        field_ids: The fields whose values it sets. For `update`, this leaves out `Record ID#`,
            which only says which record to change. `None` for `delete`, which affects whole
            records, and for `other`.
        row_count: How many rows are in this request, which is one batch of a larger write. `None`
            for anything but `insert`, `update`, and `upsert`, as a delete by filter can't know.
        method: The HTTP method.
        path: The request's path, without its query parameters.
    """

    action: WriteAction
    table_id: str | None
    field_ids: frozenset[int] | None
    row_count: int | None
    method: str
    path: str


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
    method: str, path: str, body: Any, *, key_field_id: int | None = None
) -> WriteRequest:
    """Describe a write from its request. Anything unrecognized is `other`.

    `key_field_id` is the table's key field, which a request doesn't say. Without it, a write that
    could be an insert is an `upsert`, as it could change an existing record.
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
    return _other(method, path)


def check_policy(policy: WritePolicy, request: WriteRequest) -> None:
    """Refuse the write unless the policy returns `True`. Anything else, even truthy, refuses."""
    try:
        allowed = policy(request)
    except ReadOnlyError:
        raise
    except Exception as exc:
        raise ReadOnlyError(
            f"The write policy raised {type(exc).__name__}, so this client won't send "
            f"{request.method} {request.path}."
        ) from exc
    if allowed is not True:
        raise ReadOnlyError(
            f"The write policy refused this {request.action}, so this client won't send "
            f"{request.method} {request.path}."
        )


def validate_write_policy(policy: WritePolicy | None, *, read_only: bool) -> WritePolicy | None:
    if policy is None:
        return None
    if read_only:
        raise ConfigurationError("Use read_only or write_policy, not both.")
    if not callable(policy):
        raise ConfigurationError("write_policy must be a function, or TableRules.")
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


@dataclass(frozen=True, slots=True, init=False)
class Allow:
    """What a client may do to one table, for `TableRules`. Anything not granted is refused.

    `insert` and `update` each take `True` for any field, or a list of the field IDs they may set.
    An `upsert` needs both, and its fields need to be in both, including the one it merges on.

    Args:
        insert: Whether it may create records, and with which fields.
        update: Whether it may change records, and which fields.
        delete: Whether it may delete records. Field lists don't limit this, as deleting a record
            removes all of its fields.
        delete_files: Whether it may delete file versions.

    Raises:
        ConfigurationError: A list is empty, or holds anything but field IDs.
    """

    insert: bool | frozenset[int]
    update: bool | frozenset[int]
    delete: bool
    delete_files: bool

    def __init__(
        self,
        *,
        insert: bool | Collection[int] = False,
        update: bool | Collection[int] = False,
        delete: bool = False,
        delete_files: bool = False,
    ) -> None:
        for name, value in (("delete", delete), ("delete_files", delete_files)):
            if not isinstance(value, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError(f"{name} takes True or False.")
        object.__setattr__(self, "insert", _access("insert", insert))
        object.__setattr__(self, "update", _access("update", update))
        object.__setattr__(self, "delete", delete)
        object.__setattr__(self, "delete_files", delete_files)


def _refused(request: WriteRequest, reason: str) -> ReadOnlyError:
    return ReadOnlyError(
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


class TableRules:
    """A write policy that lists what a client may do to each table. Anything else is refused.

    ```python
    rules = TableRules({"bqtasks123": Allow(insert=True, update=[7, 12])})
    qb = Quickbase.from_env(write_policy=rules)
    ```

    Tables are named by ID and must match exactly, because a name can be renamed. Only record and
    file writes can be allowed, so changes to tables, fields, and relationships are all refused.
    To allow those, write your own policy.

    Args:
        tables: What's allowed, as `{table ID: Allow(...)}`.

    Raises:
        ConfigurationError: A key isn't a table ID, or a value isn't an `Allow`.
    """

    __slots__ = ("_tables",)

    def __init__(self, tables: Mapping[str, Allow]) -> None:
        if not isinstance(tables, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("TableRules takes a dictionary of {table ID: Allow(...)}.")
        for table_id, access in tables.items():
            if not isinstance(table_id, str) or not table_id:  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError("TableRules needs a table ID for each table.")
            if not isinstance(access, Allow):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError(f"Table {table_id} needs an Allow(...).")
        self._tables = dict(tables)

    def __call__(self, request: WriteRequest) -> bool:
        """Return `True` if the write is allowed, and raise `ReadOnlyError` with why if it isn't."""
        if request.action == "other":
            raise _refused(request, "only record and file writes can be allowed.")
        if request.table_id is None:
            raise _refused(request, "its table can't be determined.")
        access = self._tables.get(request.table_id)
        if access is None:
            raise _refused(request, f"table {request.table_id} isn't listed.")
        if request.action in ("insert", "upsert"):
            _check_fields(request, access.insert, "inserting")
        if request.action in ("update", "upsert"):
            _check_fields(request, access.update, "updating")
        if request.action == "delete" and not access.delete:
            raise _refused(request, f"deleting isn't allowed on table {request.table_id}.")
        if request.action == "delete_file" and not access.delete_files:
            raise _refused(request, f"deleting files isn't allowed on table {request.table_id}.")
        return True


def all_of(*policies: WritePolicy) -> WritePolicy:
    """Combine write policies, so that a write must pass every one of them.

    Args:
        *policies: The policies, which are checked in order until one refuses.

    Returns:
        A write policy.

    Raises:
        ConfigurationError: No policies were given, or one can't be used.
    """
    if not policies:
        raise ConfigurationError("all_of() needs at least one policy.")
    for policy in policies:
        validate_write_policy(policy, read_only=False)
        if policy is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise ConfigurationError("all_of() takes policies, not None.")

    def combined(request: WriteRequest) -> bool:
        for policy in policies:
            check_policy(policy, request)
        return True

    return combined
