"""The schema operations a model can be given, and the JSON Schema each offers. No network access.

`describe_schema` shows the tables and fields the client can read, `preview_schema_changes` says
what a list of changes would do, and `apply_schema_changes` makes them, but only with the
confirmation `preview_schema_changes` returned for exactly those changes, while the schema is as it
was. Each schema offers only the changes, tables, and field types the client's policy allows, and
its descriptions say what each one does.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ._bound import BoundPolicy
from ._models import TableInfo
from ._schema_plan import CHANGE_TYPES, FIELD_TYPES, SUMMARY_FUNCTIONS
from ._write_request import UNSAFE_FORMULA_TYPES
from .errors import ConfigurationError

SchemaOperationName = Literal["describe_schema", "preview_schema_changes", "apply_schema_changes"]
"""What `App.operation()` can offer a model."""

DEFAULT_MAX_CHANGES = 20


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaAllowed:
    """What the client's policy allows a schema change to do, which the schemas offer.

    `tables` are the names of the tables it may change, and `create_tables` whether it may add
    one. A change that's offered can still be refused for a particular table or field, which the
    plan reports.
    """

    actions: frozenset[str]
    tables: tuple[str, ...]
    create_tables: bool
    field_types: tuple[str, ...]
    formulas: bool


_ACTION_VERBS = {
    "update_table": "change_table",
    "delete_table": "delete_table",
    "create_field": "add_field",
    "update_field": "change_field",
    "delete_field": "delete_field",
    "create_relationship": "link",
    "create_lookup_field": "lookup",
    "create_summary_field": "summary",
    "delete_relationship": "unlink",
}


def schema_allowed(
    tables: Sequence[TableInfo], policy: BoundPolicy | None, *, app_id: str
) -> SchemaAllowed:
    """Work out what schema changes the client's policy allows, from `BoundPolicy.offers()`, so
    what's offered is exactly what's allowed somewhere. A plan still checks each table and field."""
    if policy is None:
        return SchemaAllowed(
            actions=frozenset(CHANGE_TYPES),
            tables=tuple(item.name for item in tables),
            create_tables=True,
            field_types=FIELD_TYPES,
            formulas=True,
        )
    create_tables = policy.can_create_tables(app_id)
    grants: list[tuple[frozenset[str], frozenset[str] | None]] = []
    changeable: list[str] = []
    for item in tables:
        none: list[tuple[int, str]] = []
        offered = policy.offers_for(item.id, none, lambda found: found)
        if offered.schema:
            changeable.append(item.name)
            grants.append((offered.schema, offered.field_types))
    if create_tables:
        created = policy.with_created("\x00new", "new table")
        grants.append((policy.created_offers, created.field_types("\x00new")))
    verbs = frozenset(verb for found, _ in grants for verb in found)
    actions = {action for action, verb in _ACTION_VERBS.items() if verb in verbs}
    if create_tables:
        actions.add("create_table")
    types: set[str] = set()
    for found, allowed in grants:
        if "add_field" in found:
            types |= set(FIELD_TYPES) if allowed is None else set(allowed)
    return SchemaAllowed(
        actions=frozenset(actions),
        tables=tuple(changeable),
        create_tables=create_tables,
        field_types=tuple(name for name in FIELD_TYPES if name in types),
        formulas="formula" in verbs,
    )


def _text(description: str) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "description": description}


def _field_ref(description: str) -> dict[str, Any]:
    return {
        "anyOf": [{"type": "string", "minLength": 1}, {"type": "integer", "minimum": 1}],
        "description": f"{description}, by label, or by ID.",
    }


def _flag(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _choices() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string"},
        "description": "The options of a text-multiple-choice or multitext field.",
    }


def _action_schema(
    action: str, description: str, properties: dict[str, Any], required: Sequence[str]
) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {"action": {"const": action}, **properties},
        "required": ["action", *required],
        "additionalProperties": False,
    }


def _change_schemas(allowed: SchemaAllowed) -> list[dict[str, Any]]:
    tables = ", ".join(allowed.tables) or "none yet"
    new = " or one an earlier change creates" if allowed.create_tables else ""
    table = _text(f"The table, by name: one of {tables}{new}.")
    child = _text(f"The child table, by name: one of {tables}{new}.")
    foreign_key = _text("The label of the relationship's foreign key field on the child table.")
    field_type: dict[str, Any] = {
        "type": "string",
        "enum": list(allowed.field_types),
        "description": "The field's type. For a formula, the type of its result.",
    }
    create_field: dict[str, Any] = {
        "table": table,
        "label": _text("The new field's label, which no other field on the table may have."),
        "type": field_type,
        "choices": _choices(),
        "required": _flag("Whether every record must have a value. Not for a table with records."),
        "unique": _flag("Whether every record must have a different value."),
        "help_text": {"type": "string", "description": "Help shown beside the field on forms."},
    }
    update_field: dict[str, Any] = {
        "table": table,
        "field": _field_ref("The field to change"),
        "label": _text("A new label."),
        "choices": _choices(),
        "required": _flag("Whether every record must have a value."),
        "unique": _flag("Whether every record must have a different value."),
        "help_text": {"type": "string", "description": "New help text."},
    }
    if allowed.formulas:
        unsafe = " and ".join(sorted(UNSAFE_FORMULA_TYPES))
        formula = _text(
            "Makes it a formula field, calculated from the record's own fields, written as "
            f"[Field label]. It can't use other tables, and its type can't be {unsafe}."
        )
        create_field["formula"] = formula
        update_field["formula"] = _text("A new formula, for a formula field only.")
    table_names = {
        "single_record_name": _text("What the table calls one record, such as Project."),
        "plural_record_name": _text("What it calls several, such as Projects."),
    }
    schemas = {
        "create_table": _action_schema(
            "create_table",
            "Create a table.",
            {"name": _text("The table's name."), "description": {"type": "string"}, **table_names},
            ["name"],
        ),
        "update_table": _action_schema(
            "update_table",
            "Rename a table, or change its descriptions.",
            {
                "table": table,
                "name": _text("A new name."),
                "description": {"type": "string"},
                **table_names,
            },
            ["table"],
        ),
        "delete_table": _action_schema(
            "delete_table", "Delete a table and all its records.", {"table": table}, ["table"]
        ),
        "create_field": _action_schema(
            "create_field", "Add a field to a table.", create_field, ["table", "label", "type"]
        ),
        "update_field": _action_schema(
            "update_field",
            "Change a field's settings. Its type can't change.",
            update_field,
            ["table", "field"],
        ),
        "delete_field": _action_schema(
            "delete_field",
            "Delete a field and all its values.",
            {"table": table, "field": _field_ref("The field to delete")},
            ["table", "field"],
        ),
        "create_relationship": _action_schema(
            "create_relationship",
            "Link a child table to a parent, with a new field on the child that holds each "
            "record's parent.",
            {
                "child_table": child,
                "parent_table": _text(f"The parent table, by name: one of {tables}{new}."),
                "foreign_key": _text("The label of the new field on the child, such as Project."),
            },
            ["child_table", "parent_table", "foreign_key"],
        ),
        "create_lookup_field": _action_schema(
            "create_lookup_field",
            "Show a parent's field on each child record.",
            {
                "child_table": child,
                "foreign_key": foreign_key,
                "parent_field": _field_ref("The parent's field to show"),
                "label": _text("The new field's label. Defaults to '<foreign key> - <field>'."),
            },
            ["child_table", "foreign_key", "parent_field"],
        ),
        "create_summary_field": _action_schema(
            "create_summary_field",
            "Add a field to the parent that combines its child records, such as a total.",
            {
                "child_table": child,
                "foreign_key": foreign_key,
                "label": _text("The new field's label, on the parent."),
                "function": {
                    "type": "string",
                    "enum": list(SUMMARY_FUNCTIONS),
                    "description": "How to combine them. count counts the child records.",
                },
                "child_field": _field_ref("The child's field to combine, for anything but count"),
                "where": _text(
                    "Only combine the child records that match, in Quickbase's query language "
                    "with the child's field IDs, such as {7.EX.true}. Leave it out for all of them."
                ),
            },
            ["child_table", "foreign_key", "label", "function"],
        ),
        "delete_relationship": _action_schema(
            "delete_relationship",
            "Unlink a child table from its parent, deleting the relationship's lookups and "
            "summaries. The foreign key field stays, with its values.",
            {"child_table": child, "foreign_key": foreign_key},
            ["child_table", "foreign_key"],
        ),
    }
    return [schema for action, schema in schemas.items() if action in allowed.actions]


def _changes(allowed: SchemaAllowed, most: int) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": most,
        "items": {"oneOf": _change_schemas(allowed)},
        "description": (
            "The changes, in order. A later change can use a table or field an earlier one "
            "makes. A change the app already has is skipped."
        ),
    }


def schema_operation_schema(
    name: SchemaOperationName, allowed: SchemaAllowed, *, max_changes: int, tables: Sequence[str]
) -> dict[str, Any]:
    """The JSON Schema for a schema operation's arguments.

    Raises:
        ConfigurationError: The client's policy allows no schema change, for
            `preview_schema_changes` or `apply_schema_changes`.
    """
    if name == "describe_schema":
        return {
            "type": "object",
            "properties": {
                "tables": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(tables)},
                    "description": "Only these tables. Defaults to every table.",
                }
            },
            "additionalProperties": False,
        }
    if not allowed.actions:
        raise ConfigurationError(
            f"This client can't offer {name}: its policy allows no schema change."
        )
    properties: dict[str, Any] = {"changes": _changes(allowed, max_changes)}
    if name == "apply_schema_changes":
        properties["confirmation"] = {
            "type": "string",
            "description": (
                "The confirmation from preview_schema_changes, called with exactly these changes, "
                "after the person approved the plan. It lasts 15 minutes."
            ),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
