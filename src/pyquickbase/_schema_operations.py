"""The schema operations a model can be given, and the JSON Schema each offers. No network access.

`describe_schema` shows the tables and fields the client can read, `plan_schema` says what a list
of changes would do, and `apply_schema` makes them, but only with the confirmation `plan_schema`
returned for exactly those changes, while the schema is as it was. Each schema offers only the
kinds of change, tables, and field types the client's `WriteRules` could allow, and its
descriptions say what each one does.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

from ._models import TableInfo
from ._policy import UNSAFE_FORMULA_TYPES, SchemaAccess, WriteRules
from ._schema_plan import CHANGE_TYPES, FIELD_TYPES, SUMMARY_FUNCTIONS
from .errors import ConfigurationError

SchemaOperationName = Literal["describe_schema", "plan_schema", "apply_schema"]
"""What `App.operation()` can offer a model."""

DEFAULT_MAX_CHANGES = 20


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaAllowed:
    """What the client's limits could allow a schema change to do, which the schemas offer.

    `tables` are the names of the tables it may change, and `create_tables` whether it may add
    one. A change that's offered can still be refused for a particular table or field, which the
    plan reports.
    """

    kinds: frozenset[str]
    tables: tuple[str, ...]
    create_tables: bool
    field_types: tuple[str, ...]
    formulas: bool


def _grants(rules: WriteRules) -> list[SchemaAccess]:
    """Every `SchemaAccess` a `WriteRules` holds, including the one for new tables."""
    accesses = [*rules._tables.values()]  # pyright: ignore[reportPrivateUsage]
    if rules._new_tables is not None:  # pyright: ignore[reportPrivateUsage]
        accesses.append(rules._new_tables)  # pyright: ignore[reportPrivateUsage]
    return [access.schema for access in accesses if access.schema is not None]


def schema_allowed(
    tables: Sequence[TableInfo],
    write_rules: Sequence[WriteRules],
    *,
    app_id: str,
    read_only: bool,
) -> SchemaAllowed:
    """Work out what schema changes the client's limits could allow. Every `WriteRules` must allow
    one. A custom write policy can't be asked, so it may refuse more than this offers."""
    if read_only:
        return SchemaAllowed(
            kinds=frozenset(), tables=(), create_tables=False, field_types=(), formulas=False
        )
    if not write_rules:
        return SchemaAllowed(
            kinds=frozenset(CHANGE_TYPES),
            tables=tuple(item.name for item in tables),
            create_tables=True,
            field_types=FIELD_TYPES,
            formulas=True,
        )
    per_rules = [_grants(rules) for rules in write_rules]

    def granted(test: Callable[[SchemaAccess], bool]) -> bool:
        return all(any(test(item) for item in grants) for grants in per_rules)

    kinds = {
        "update_table": granted(lambda item: item.update_table),
        "delete_table": granted(lambda item: item.delete_table),
        "create_field": granted(lambda item: item.create_fields),
        "update_field": granted(lambda item: item.update_fields is not False),
        "delete_field": granted(lambda item: item.delete_fields is not False),
        "create_relationship": granted(lambda item: item.relationships),
        "create_lookup": granted(lambda item: item.relationships and item.lookups),
        "create_summary": granted(lambda item: item.relationships and item.summaries),
        "delete_relationship": granted(
            lambda item: item.relationships and item.delete_fields is True
        ),
    }
    create_tables = all(
        app_id in rules._create_tables_in  # pyright: ignore[reportPrivateUsage]
        for rules in write_rules
    )
    kinds["create_table"] = create_tables
    changeable = [
        item.name
        for item in tables
        if all(
            (access := rules._tables.get(item.id)) is not None  # pyright: ignore[reportPrivateUsage]
            and access.schema is not None
            for rules in write_rules
        )
    ]
    types = [name for name in FIELD_TYPES if granted(partial(_creates, name))]
    return SchemaAllowed(
        kinds=frozenset(kind for kind, offered in kinds.items() if offered),
        tables=tuple(changeable),
        create_tables=create_tables,
        field_types=tuple(types),
        formulas=granted(lambda item: item.formulas),
    )


def _creates(field_type: str, access: SchemaAccess) -> bool:
    return access.create_fields and (access.field_types is None or field_type in access.field_types)


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


def _kind_schema(
    kind: str, description: str, properties: dict[str, Any], required: Sequence[str]
) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {"kind": {"const": kind}, **properties},
        "required": ["kind", *required],
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
        "create_table": _kind_schema(
            "create_table",
            "Create a table.",
            {"name": _text("The table's name."), "description": {"type": "string"}, **table_names},
            ["name"],
        ),
        "update_table": _kind_schema(
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
        "delete_table": _kind_schema(
            "delete_table", "Delete a table and all its records.", {"table": table}, ["table"]
        ),
        "create_field": _kind_schema(
            "create_field", "Add a field to a table.", create_field, ["table", "label", "type"]
        ),
        "update_field": _kind_schema(
            "update_field",
            "Change a field's settings. Its type can't change.",
            update_field,
            ["table", "field"],
        ),
        "delete_field": _kind_schema(
            "delete_field",
            "Delete a field and all its values.",
            {"table": table, "field": _field_ref("The field to delete")},
            ["table", "field"],
        ),
        "create_relationship": _kind_schema(
            "create_relationship",
            "Link a child table to a parent, with a new field on the child that holds each "
            "record's parent.",
            {
                "child": child,
                "parent": _text(f"The parent table, by name: one of {tables}{new}."),
                "foreign_key": _text("The label of the new field on the child, such as Project."),
            },
            ["child", "parent", "foreign_key"],
        ),
        "create_lookup": _kind_schema(
            "create_lookup",
            "Show a parent's field on each child record.",
            {
                "child": child,
                "foreign_key": foreign_key,
                "parent_field": _field_ref("The parent's field to show"),
                "label": _text("The new field's label. Defaults to '<foreign key> - <field>'."),
            },
            ["child", "foreign_key", "parent_field"],
        ),
        "create_summary": _kind_schema(
            "create_summary",
            "Add a field to the parent that combines its child records, such as a total.",
            {
                "child": child,
                "foreign_key": foreign_key,
                "label": _text("The new field's label, on the parent."),
                "function": {
                    "type": "string",
                    "enum": list(SUMMARY_FUNCTIONS),
                    "description": "How to combine them. COUNT counts the child records.",
                },
                "child_field": _field_ref("The child's field to combine, for anything but COUNT"),
                "where": _text(
                    "Only combine the child records that match, in Quickbase's query language "
                    "with the child's field IDs, such as {7.EX.true}. Leave it out for all of them."
                ),
            },
            ["child", "foreign_key", "label", "function"],
        ),
        "delete_relationship": _kind_schema(
            "delete_relationship",
            "Unlink a child table from its parent, deleting the relationship's lookups and "
            "summaries. The foreign key field stays, with its values.",
            {"child": child, "foreign_key": foreign_key},
            ["child", "foreign_key"],
        ),
    }
    return [schema for kind, schema in schemas.items() if kind in allowed.kinds]


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
        ConfigurationError: The client's limits allow no schema change, for `plan_schema` or
            `apply_schema`.
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
    if not allowed.kinds:
        raise ConfigurationError(
            f"This client can't offer {name}: its limits on writes allow no schema change."
        )
    properties: dict[str, Any] = {"changes": _changes(allowed, max_changes)}
    if name == "apply_schema":
        properties["confirmation"] = {
            "type": "string",
            "description": (
                "The confirmation from plan_schema, called with exactly these changes, after the "
                "person approved the plan. It lasts 15 minutes."
            ),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
