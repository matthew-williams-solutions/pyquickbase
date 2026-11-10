"""Schema changes, the plan that says what they'd do, and what applying one did. No network access.

A change says what someone wants, such as a new field. A plan is that list of changes compared
with the app as it is now: what each would change, what it would affect, what would stop it, and a
fingerprint of what it was checked against. `App.preview_schema_changes()` makes one, and
`App.apply_schema_changes()` applies it, refusing if the app has changed since.

Each change also has a JSON form, `{"action": "create_field", ...}`, which is what a model sends.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, Literal, cast, get_args

from .errors import InvalidValueError

SummaryFunction = Literal[
    "sum", "avg", "max", "min", "std_dev", "count", "distinct_count", "combined_text",
    "combined_user",
]  # fmt: skip
"""How a summary field combines a child's values. `count` counts the child records."""

SUMMARY_FUNCTIONS: tuple[str, ...] = get_args(SummaryFunction)

# Other names for a function, which models and people write. Quickbase's own, such as `STD-DEV`,
# are read by folding case and hyphens.
_FUNCTION_ALIASES = {
    "total": "sum",
    "average": "avg",
    "mean": "avg",
    "minimum": "min",
    "maximum": "max",
    "stddev": "std_dev",
    "standard_deviation": "std_dev",
    "count_distinct": "distinct_count",
}


def summary_function(value: Any) -> SummaryFunction | None:
    """The function a name means, such as `sum` for `SUM` or `total`, or None for no function."""
    if not isinstance(value, str):
        return None
    key = "_".join(value.strip().casefold().replace("-", " ").replace("_", " ").split())
    key = _FUNCTION_ALIASES.get(key, key)
    return cast(SummaryFunction, key) if key in SUMMARY_FUNCTIONS else None


def quickbase_function(function: SummaryFunction) -> str:
    """Quickbase's name for a function, such as `STD-DEV` for `std_dev`."""
    return function.upper().replace("_", "-")


FIELD_TYPES = (
    "text", "text-multiple-choice", "text-multi-line", "rich-text", "numeric", "currency",
    "rating", "percent", "multitext", "email", "url", "duration", "date", "datetime", "timestamp",
    "timeofday", "checkbox", "user", "multiuser", "address", "phone", "file",
)  # fmt: skip
"""The field types `create_field()` takes. `datetime` makes a `timestamp` field."""

FieldRefValue = str | int


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateTable:
    """Make a table. A plan treats a table with this name as already made.

    Args:
        name: The table's name.
        description: What it holds.
        single_record_name: What it calls one record, such as `Project`.
        plural_record_name: What it calls several, such as `Projects`.
    """

    name: str
    description: str | None = None
    single_record_name: str | None = None
    plural_record_name: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateTable:
    """Rename a table, or change its descriptions. Anything left as None stays as it is.

    Args:
        table: The table, by name or ID.
        name: A new name.
        description: A new description.
        single_record_name: A new name for one record.
        plural_record_name: A new name for several.
    """

    table: str
    name: str | None = None
    description: str | None = None
    single_record_name: str | None = None
    plural_record_name: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteTable:
    """Delete a table and all its records.

    Args:
        table: The table, by name or ID.
    """

    table: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateField:
    """Add a field, or a formula field when `formula` is set. A plan treats a field with this
    label, type, and formula as already made.

    Args:
        table: The table, by name or ID.
        label: The new field's label.
        type: Its type, such as `text` or `numeric`. For a formula, the type of its result.
        choices: The options of a `text-multiple-choice` or `multitext` field.
        required: Whether it must have a value. Quickbase refuses this on a table with records.
        unique: Whether each record must have a different value.
        help_text: The help shown beside it on forms.
        formula: Makes it a formula field, calculated from the record's other fields.
    """

    table: str
    label: str
    type: str
    choices: tuple[str, ...] | None = None
    required: bool | None = None
    unique: bool | None = None
    help_text: str | None = None
    formula: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateField:
    """Change a field's settings. Anything left as None stays as it is. Its type can't change.

    Args:
        table: The table, by name or ID.
        field: The field, by label or ID.
        label: A new label.
        choices: A new list of options, replacing the old one.
        required: Whether it must have a value.
        unique: Whether each record must have a different value.
        help_text: New help text.
        formula: A new formula, for a formula field only.
    """

    table: str
    field: FieldRefValue
    label: str | None = None
    choices: tuple[str, ...] | None = None
    required: bool | None = None
    unique: bool | None = None
    help_text: str | None = None
    formula: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteField:
    """Delete a field and all its values.

    Args:
        table: The table, by name or ID.
        field: The field, by label or ID.
    """

    table: str
    field: FieldRefValue


@dataclass(frozen=True, slots=True, kw_only=True)
class SetKeyField:
    """Change which field is a table's key. A plan treats a field that's already the key as done.

    The field must be unique, or be `Record ID#` to go back to the usual key. Once a table has
    a custom key, `insert()` refuses it, as a row with an existing key value updates that record.

    Args:
        table: The table, by name or ID.
        field: The field, by label or ID.
    """

    table: str
    field: FieldRefValue


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateRelationship:
    """Link a child table to a parent, with a new field on the child that holds the parent's
    Record ID#. A plan treats a link with this foreign key as already made.

    Args:
        child_table: The child table, by name or ID, such as `Tasks`.
        parent_table: The parent table, such as `Projects`.
        foreign_key: The label of the new field on the child, such as `Project`.
    """

    child_table: str
    parent_table: str
    foreign_key: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateLookupField:
    """Show a parent's field on each child record.

    Args:
        child_table: The child table, by name or ID.
        foreign_key: The label of the relationship's foreign key on the child.
        parent_field: The parent's field to show, by label or ID.
        label: The lookup's label. Quickbase names one `<foreign key> - <field>` without it.
    """

    child_table: str
    foreign_key: str
    parent_field: FieldRefValue
    label: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateSummaryField:
    """Total, average, or count a child's records on each parent record.

    Args:
        child_table: The child table, by name or ID.
        foreign_key: The label of the relationship's foreign key on the child.
        label: The label of the new field on the parent.
        function: How to combine the values, such as `sum`. `count` counts the child records,
            and takes no `child_field`.
        child_field: The child's field to combine, by label or ID.
        where: Only combine the child records that match, in Quickbase's query language with
            field IDs, such as `{7.EX.true}`. The plan checks it against the child's fields, as
            Quickbase saves one it can't use and the summary is then always empty.
    """

    child_table: str
    foreign_key: str
    label: str
    function: SummaryFunction = "sum"
    child_field: FieldRefValue | None = None
    where: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteRelationship:
    """Unlink a child table from its parent. Quickbase also deletes the relationship's lookups
    and summaries, and keeps the foreign key field, with its values, as a plain number.

    Args:
        child_table: The child table, by name or ID.
        foreign_key: The label of the relationship's foreign key on the child.
    """

    child_table: str
    foreign_key: str


SchemaChange = (
    CreateTable
    | UpdateTable
    | DeleteTable
    | CreateField
    | UpdateField
    | DeleteField
    | SetKeyField
    | CreateRelationship
    | CreateLookupField
    | CreateSummaryField
    | DeleteRelationship
)
"""One change to an app's schema."""

SchemaAction = Literal[
    "create_table", "update_table", "delete_table", "create_field", "update_field",
    "delete_field", "set_key_field", "create_relationship", "create_lookup_field",
    "create_summary_field", "delete_relationship",
]  # fmt: skip
"""The `action` of a change in its JSON form."""

CHANGE_TYPES: dict[str, type[Any]] = {
    "create_table": CreateTable,
    "update_table": UpdateTable,
    "delete_table": DeleteTable,
    "create_field": CreateField,
    "update_field": UpdateField,
    "delete_field": DeleteField,
    "set_key_field": SetKeyField,
    "create_relationship": CreateRelationship,
    "create_lookup_field": CreateLookupField,
    "create_summary_field": CreateSummaryField,
    "delete_relationship": DeleteRelationship,
}
_ACTIONS = {change_type: action for action, change_type in CHANGE_TYPES.items()}
# Values models write for a change's action, other than the schema's.
_ACTION_ALIASES = {"create_lookup": "create_lookup_field", "create_summary": "create_summary_field"}
# Keys models write, read as the ones the schema lists.
_KEY_ALIASES = {"kind": "action", "child": "child_table", "parent": "parent_table"}

Risk = Literal["additive", "modifying", "destructive"]
"""How much a change can lose: `additive` only adds, `modifying` changes what's there, and
`destructive` deletes data or fields."""

_RISKS: dict[str, Risk] = {
    "create_table": "additive",
    "create_field": "additive",
    "create_relationship": "additive",
    "create_lookup_field": "additive",
    "create_summary_field": "additive",
    "update_table": "modifying",
    "update_field": "modifying",
    "set_key_field": "modifying",
    "delete_table": "destructive",
    "delete_field": "destructive",
    "delete_relationship": "destructive",
}

Status = Literal["ready", "already_done", "blocked"]
"""Whether a planned change is `ready` to change the app, `already_done` because the app has it,
or `blocked`, which stops the whole plan."""


def change_action(change: SchemaChange) -> SchemaAction:
    """A change's `action`, as its JSON form names it."""
    return cast(SchemaAction, _ACTIONS[type(change)])


def change_risk(change: SchemaChange) -> Risk:
    return _RISKS[change_action(change)]


def change_to_json(change: SchemaChange) -> dict[str, Any]:
    """A change as JSON, leaving out what isn't set."""
    found: dict[str, Any] = {"action": change_action(change)}
    for item in fields(change):
        value = getattr(change, item.name)
        if value is not None:
            found[item.name] = (
                list(cast(tuple[Any, ...], value)) if isinstance(value, tuple) else value
            )
    return found


def _require(value: Any, what: str, *, allowed: tuple[type, ...]) -> Any:
    if isinstance(value, bool) and bool not in allowed:
        raise InvalidValueError(f"{what} can't be true or false.")
    if not isinstance(value, allowed):
        names = " or ".join(item.__name__ for item in allowed)
        raise InvalidValueError(f"{what} must be {names}.")
    if isinstance(value, str) and not value.strip():
        raise InvalidValueError(f"{what} can't be blank.")
    return value


def change_from_json(data: Any, position: int = 0) -> SchemaChange:
    """Read one change from its JSON form, checking every key and value.

    Raises:
        InvalidValueError: Something can't be used. The message says which change and key.
    """
    where = f"changes[{position}]"
    if not isinstance(data, Mapping):
        raise InvalidValueError(f"{where} must be an object with an action.")
    found = normalize_change(cast(Mapping[str, Any], data))
    action = found.get("action")
    change_type = CHANGE_TYPES.get(action) if isinstance(action, str) else None
    if change_type is None:
        raise InvalidValueError(f"{where}.action must be one of: {', '.join(CHANGE_TYPES)}.")
    names = {item.name: item for item in fields(change_type)}
    unknown = sorted(set(found) - set(names) - {"action"})
    if unknown:
        raise InvalidValueError(f"{where} has keys this action doesn't take: {', '.join(unknown)}.")
    values: dict[str, Any] = {}
    for name, item in names.items():
        value = found.get(name)
        if value is None:
            if item.default is MISSING and item.default_factory is MISSING:
                raise InvalidValueError(f"{where}.{name} is required.")
            continue
        values[name] = _read_value(change_type, name, value, f"{where}.{name}")
    return change_type(**values)


def normalize_change(data: Mapping[str, Any]) -> dict[str, Any]:
    """A change in the spelling the schema lists, from the ones models tend to write: `kind` for
    `action`, `create_lookup` for `create_lookup_field`, `child` for `child_table`, and a
    function such as `SUM` or `total` for `sum`. Each means exactly one thing. Anything else is
    left as it is, to be checked."""
    found = dict(data)
    for old, new in _KEY_ALIASES.items():
        if old in found and new not in found:
            found[new] = found.pop(old)
    action = found.get("action")
    if isinstance(action, str):
        key = "_".join(action.strip().casefold().replace("-", " ").split())
        key = _ACTION_ALIASES.get(key, key)
        found["action"] = key if key in CHANGE_TYPES else action
    if "function" in found:
        found["function"] = summary_function(found["function"]) or found["function"]
    return found


def _read_value(change_type: type[Any], name: str, value: Any, what: str) -> Any:
    if name in ("required", "unique"):
        return _require(value, what, allowed=(bool,))
    if name == "choices":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):  # pyright: ignore[reportUnknownVariableType]
            raise InvalidValueError(f"{what} must be a list of text.")
        return tuple(cast(list[str], value))
    if name in ("field", "parent_field", "child_field"):
        return _require(value, what, allowed=(str, int))
    if name == "function":
        if value not in SUMMARY_FUNCTIONS:
            raise InvalidValueError(f"{what} must be one of: {', '.join(SUMMARY_FUNCTIONS)}.")
        return value
    if name == "type" and value not in FIELD_TYPES:
        raise InvalidValueError(f"{what} must be one of: {', '.join(FIELD_TYPES)}.")
    if name in ("help_text", "description"):
        if not isinstance(value, str):
            raise InvalidValueError(f"{what} must be text.")
        return value
    return _require(value, what, allowed=(str,))


def changes_from_json(data: Any, *, most: int | None = None) -> list[SchemaChange]:
    """Read a list of changes from JSON.

    Raises:
        InvalidValueError: It isn't a list, it's empty or longer than `most`, or a change can't be
            used.
    """
    if not isinstance(data, list):
        raise InvalidValueError("changes must be a list.")
    items = cast(list[Any], data)
    if not items:
        raise InvalidValueError("changes can't be empty.")
    if most is not None and len(items) > most:
        raise InvalidValueError(
            f"A plan may hold at most {most} changes, and this has {len(items)}."
        )
    return [change_from_json(item, position) for position, item in enumerate(items)]


@dataclass(frozen=True, slots=True)
class SettingChange:
    """One setting before and after a change, with a setting that isn't there as None.

    Attributes:
        before: The setting now.
        after: What the change would set.
    """

    before: Any
    after: Any


@dataclass(frozen=True, slots=True, kw_only=True)
class PlannedChange:
    """What one change would do, checked against the app as it is now.

    Attributes:
        change: The change asked for.
        status: `ready` if it would change the app, `already_done` if the app already has it, and
            `blocked` if it can't be applied, which stops the whole plan.
        risk: `additive`, `modifying`, or `destructive`.
        summary: What it does, in a sentence, such as `Add field "Due" (date) to Tasks.`
        settings: What it would set, as `{setting: SettingChange}`.
        warnings: What it would also do or affect, such as formulas that use a deleted field.
        blockers: Why it can't be applied.
        affected_records: How many records it would affect, such as those that would lose a
            deleted field's values, if counted.
        link: A Quickbase page to check it on, if there is one yet.
        policy_effects: What the client's policy would do once the change is made: who can read
            and write a new field, and its classes, and which rules lose a deleted field or
            table. A rule it would leave matching nothing is also a blocker.
    """

    change: SchemaChange
    status: Status
    risk: Risk
    summary: str
    settings: Mapping[str, SettingChange] = field(default_factory=dict[str, SettingChange])
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    affected_records: int | None = None
    link: str | None = None
    policy_effects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaPlan:
    """What a list of changes would do to an app, checked against it as it is now.

    Pass it to `App.apply_schema_changes()`, which refuses it if the app has changed since.

    Attributes:
        app_id: The app it's for.
        changes: What each change would do, in order.
        fingerprint: A digest of everything the plan was checked against.
        confirmation: From a `preview_schema_changes` operation, what its `apply_schema_changes`
            operation needs. Empty otherwise.
    """

    app_id: str
    changes: tuple[PlannedChange, ...]
    fingerprint: str
    confirmation: str = field(default="", repr=False)

    @property
    def blockers(self) -> tuple[str, ...]:
        """Every reason the plan can't be applied, from all its changes."""
        return tuple(item for change in self.changes for item in change.blockers)

    @property
    def warnings(self) -> tuple[str, ...]:
        """Everything its changes would also do or affect."""
        return tuple(item for change in self.changes for item in change.warnings)

    @property
    def has_changes(self) -> bool:
        """Whether applying it would change anything."""
        return any(change.status == "ready" for change in self.changes)


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaChangeResult:
    """What applying a plan did.

    Attributes:
        applied: The changes made, in order.
        already_done: The changes the app already had.
        failed: The change that failed, if one did. The ones after it weren't attempted.
        not_attempted: The changes after one that failed.
        error: Why it failed, if it did.
    """

    applied: tuple[PlannedChange, ...] = ()
    already_done: tuple[PlannedChange, ...] = ()
    failed: PlannedChange | None = None
    not_attempted: tuple[PlannedChange, ...] = ()
    error: str | None = None


def fingerprint(state: Any) -> str:
    """A digest of what a plan was checked against, so a change since can be noticed."""
    text = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def canonical_changes(changes: Sequence[SchemaChange]) -> list[dict[str, Any]]:
    """Changes as JSON in a fixed form, for binding a confirmation to them."""
    return [change_to_json(change) for change in changes]
