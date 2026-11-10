"""Build query conditions from plain data, such as a model's tool call. No network access.

An LLM or web form can't send a Python `F` expression, only JSON. `where_from_dict()` accepts JSON's
shapes and refuses anything it doesn't recognize, rather than guessing, because a query that means
something else still runs, and may be a delete. `where_schema()` describes the same format to a
model.

Error messages are written for a model to act on: they say where the mistake is and what's allowed.
They don't repeat the values they were given, because errors end up in logs, and because a model
shouldn't be shown text from a tool call as if the SDK had said it.
"""

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from ._models import Field, FieldIndex
from ._query import OPERATORS, Condition, FieldRef, Group, WhereExpression, format_value
from .errors import InvalidQueryError

MAX_WHERE_DEPTH = 20
MAX_WHERE_CONDITIONS = 100
# A tool's schema goes to the model with every request, and each level adds two more conditions.
_MAX_SCHEMA_DEPTH = 5
_MAX_EXPONENT = 308  # The largest a JSON number can be in most parsers.

_NAMED_OPERATORS = {
    "==": "EX",
    "!=": "XEX",
    "<": "LT",
    "<=": "LTE",
    ">": "GT",
    ">=": "GTE",
    "contains": "CT",
    "not_contains": "XCT",
    "starts_with": "SW",
    "not_starts_with": "XSW",
    "has": "HAS",
    "not_has": "XHAS",
}
_LIST_OPERATORS = {"in": ("EX", "OR"), "not_in": ("XEX", "AND")}
# IR and XIR take date ranges as text, such as `last 7 days`. Text Quickbase can't read as a range
# matches nothing, with no error, and a model can compare with dates instead.
_RAW_OPERATORS = OPERATORS - {"IR", "XIR"}
_SCHEMA_OPERATORS = (*_NAMED_OPERATORS, *_LIST_OPERATORS)
# What models write for an operator, other than its own spelling. Each is closed, and means exactly
# one operator, so reading one can't change a query's meaning. They aren't in the schema, which
# lists one spelling of each. Words are matched after `normalize_operator()` folds case and turns
# spaces, hyphens, and camelCase into underscores.
_SYMBOL_ALIASES = {"=": "==", "===": "==", "<>": "!=", "!==": "!=", "≠": "!=", "≤": "<=", "≥": ">="}
_WORD_ALIASES = {
    "eq": "==",
    "equal": "==",
    "equals": "==",
    "ne": "!=",
    "neq": "!=",
    "not_equal": "!=",
    "not_equals": "!=",
    "lt": "<",
    "less_than": "<",
    "lte": "<=",
    "le": "<=",
    "less_than_or_equal": "<=",
    "gt": ">",
    "greater_than": ">",
    "gte": ">=",
    "ge": ">=",
    "greater_than_or_equal": ">=",
    "contain": "contains",
    "does_not_contain": "not_contains",
    "doesnt_contain": "not_contains",
    "startswith": "starts_with",
    "start_with": "starts_with",
    "begins_with": "starts_with",
    "not_startswith": "not_starts_with",
    "does_not_start_with": "not_starts_with",
    "doesnt_start_with": "not_starts_with",
    "notin": "not_in",
    "nin": "not_in",
    "does_not_have": "not_has",
    "doesnt_have": "not_has",
}
_GROUP_ALIASES = {"and": "all", "or": "any"}
_EMPTY_ALLOWED = frozenset({"EX", "XEX"})

_COMPARISONS = frozenset({"EX", "XEX", "LT", "LTE", "GT", "GTE", "BF", "OBF", "AF", "OAF"})
# On a timestamp, only these are confirmed to compare with the milliseconds that are sent.
_DATE_OPERATORS = {"LT": "BF", "LTE": "OBF", "GT": "AF", "GTE": "OAF"}
_DATE_TYPES = frozenset({"date", "timestamp"})
_NUMERIC_TYPES = frozenset({"numeric", "currency", "percent", "rating", "duration", "recordid"})
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CONDITION_KEYS = frozenset({"field", "op", "value"})
_SCHEMA_DESCRIPTION = (
    "Field is the field's label. Value is a date as YYYY-MM-DD, a time as ISO 8601 with a time "
    "zone, a list for in and not_in, or null with == or != for an empty field."
)
_HELP = (
    'Use {"field": ..., "op": ..., "value": ...}, or {"all": [...]} or {"any": [...]} '
    "to combine conditions."
)


def _iso_date(value: str, label: str) -> str:
    try:
        if _ISO_DATE.fullmatch(value):
            date.fromisoformat(value)
            return value
    except ValueError:
        pass
    raise InvalidQueryError(f"Field {label!r} is a date. Send its value as YYYY-MM-DD.")


def _timestamp(value: str, label: str) -> str:
    """Accept a date, which Quickbase reads as a date, or a time, which becomes milliseconds."""
    if _ISO_DATE.fullmatch(value):
        return _iso_date(value, label)
    problem = (
        f"Field {label!r} is a timestamp. Send its value as YYYY-MM-DD, or as an ISO 8601 time "
        "with a time zone, like 2026-12-01T09:30:00Z."
    )
    try:
        parsed = datetime.fromisoformat(value) if "T" in value else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise InvalidQueryError(problem)
    return str(int(parsed.timestamp() * 1000))


def _number(value: str, label: str) -> str:
    try:
        number = Decimal(value)
    except InvalidOperation:
        number = None
    # The exponent is limited because writing out 1e999999999 would take all of the memory.
    if number is None or not number.is_finite() or abs(number.adjusted()) > _MAX_EXPONENT:
        raise InvalidQueryError(f"Field {label!r} is a number. Send its value as a number.")
    return format(number, "f")  # Without an exponent, and in ASCII digits.


def _checkbox(value: str, label: str) -> str:
    lowered = value.casefold()
    if lowered not in ("true", "false"):
        raise InvalidQueryError(f"Field {label!r} is a checkbox. Send its value as true or false.")
    return lowered


@dataclass(frozen=True, slots=True)
class _CheckedCondition(Condition):
    """A condition that also checks its value against the field's type.

    `F` leaves a value's meaning to the caller, who can use a `date` object. Plain data can only
    hold text, and Quickbase matches nothing, with no error, for text it can't read as a date.
    """

    def compile(self, fields: FieldIndex) -> str:
        item = fields.resolve(self.field)
        operator, value = self.operator, self.value
        if item.type in _DATE_TYPES or item.type in _NUMERIC_TYPES or item.type == "checkbox":
            allowed = _COMPARISONS if item.type != "checkbox" else _EMPTY_ALLOWED
            if operator not in allowed:
                raise InvalidQueryError(
                    f"Field {item.label!r} has the type {item.type}, so it can only be compared, "
                    "with ==, !=, in, or not_in"
                    + (", or <, <=, >, or >=." if item.type != "checkbox" else ".")
                )
        if value:
            if item.type == "date":
                value = _iso_date(value, item.label)
            elif item.type == "timestamp":
                value = _timestamp(value, item.label)
            elif item.type in _NUMERIC_TYPES:
                value = _number(value, item.label)
                # A model's numbers mean what they mean when writing: a fraction for a percent,
                # and milliseconds for a duration.
                return Condition(self.field, operator, value, Decimal(value)).compile(fields)
            elif item.type == "checkbox":
                value = _checkbox(value, item.label)
        if item.type in _DATE_TYPES:
            operator = _DATE_OPERATORS.get(operator, operator)
        return Condition(self.field, operator, value).compile(fields)


class _Builder:
    def __init__(self, fields: frozenset[FieldRef] | None) -> None:
        self._fields = fields
        self._conditions = 0

    def _count(self, path: str, added: int = 1) -> None:
        self._conditions += added
        if self._conditions > MAX_WHERE_CONDITIONS:
            raise InvalidQueryError(
                f"{path}: a filter can have at most {MAX_WHERE_CONDITIONS} conditions, counting "
                "each value of in and not_in."
            )

    def build(self, data: Any, path: str, depth: int) -> WhereExpression:
        if not isinstance(data, Mapping):
            raise InvalidQueryError(f"{path} must be an object, not {type(data).__name__}. {_HELP}")
        if "all" in data or "any" in data:
            return self._group(data, path, depth)  # pyright: ignore[reportUnknownArgumentType]
        return self._condition(data, path)  # pyright: ignore[reportUnknownArgumentType]

    def _group(self, data: Mapping[Any, Any], path: str, depth: int) -> WhereExpression:
        if len(data) != 1:
            raise InvalidQueryError(f"{path} must have only one key, all or any. {_HELP}")
        key = next(iter(data))
        parts = data[key]
        if not isinstance(parts, list | tuple) or not parts:
            raise InvalidQueryError(f"{path}.{key} must be a list with at least one condition.")
        if depth >= MAX_WHERE_DEPTH:
            raise InvalidQueryError(
                f"{path}: groups can only be nested {MAX_WHERE_DEPTH} levels deep."
            )
        built = [
            self.build(part, f"{path}.{key}[{index}]", depth + 1)
            for index, part in enumerate(parts)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        ]
        return built[0] if len(built) == 1 else Group.of("AND" if key == "all" else "OR", *built)

    def _condition(self, data: Mapping[Any, Any], path: str) -> WhereExpression:
        if _CONDITION_KEYS.symmetric_difference(data):
            missing = sorted(_CONDITION_KEYS - set(data))
            problem = f"is missing {', '.join(missing)}" if missing else "has an unknown key"
            raise InvalidQueryError(
                f"{path} {problem}. {_HELP} Use a value of null to match empty."
            )
        field, op, value = data["field"], data["op"], data["value"]
        if isinstance(field, bool) or not isinstance(field, str | int):
            raise InvalidQueryError(f"{path}.field must be a field's label or ID.")
        if self._fields is not None and field not in self._fields:
            raise InvalidQueryError(
                f"{path}.field isn't a field this filter may use.{case_hint(field, self._fields)}"
            )
        if not isinstance(op, str):
            raise InvalidQueryError(f"{path}.op must be text. {_operators_help()}")
        op = normalize_operator(op)
        try:
            if op in _LIST_OPERATORS:
                return self._list_condition(field, op, value, path)
            operator = _NAMED_OPERATORS.get(op, op)
            if operator not in _RAW_OPERATORS:
                raise InvalidQueryError(f"{path}.op isn't an operator. {_operators_help()}")
            self._count(path)
            return _CheckedCondition(field, operator, _text(value, operator))
        except InvalidQueryError as exc:
            if str(exc).startswith(path):
                raise
            raise InvalidQueryError(f"{path}: {exc}") from None

    def _list_condition(self, field: FieldRef, op: str, values: Any, path: str) -> WhereExpression:
        operator, joiner = _LIST_OPERATORS[op]
        if not isinstance(values, list | tuple) or not values:
            raise InvalidQueryError(f"{op} needs a list with at least one value.")
        self._count(path, len(values))  # pyright: ignore[reportUnknownArgumentType]
        built: list[WhereExpression] = [
            _CheckedCondition(field, operator, _text(item, operator))
            for item in values  # pyright: ignore[reportUnknownVariableType]
        ]
        return built[0] if len(built) == 1 else Group.of(joiner, *built)


def normalize_operator(op: str) -> str:
    """The operator a model meant, such as `starts_with` for `starts with` or `startsWith`, or
    `==` for `eq`. Quickbase's own codes, such as `CT`, are kept, in capitals. Anything else is
    returned unchanged, to be refused."""
    if op in _NAMED_OPERATORS or op in _LIST_OPERATORS or op in _RAW_OPERATORS:
        return op
    key = op.strip()
    if key in _SYMBOL_ALIASES:
        return _SYMBOL_ALIASES[key]
    key = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", key).casefold().replace("'", "")
    key = re.sub(r"[\s_-]+", "_", key)
    if key in _NAMED_OPERATORS or key in _LIST_OPERATORS:
        return key
    if key in _WORD_ALIASES:
        return _WORD_ALIASES[key]
    return key.upper() if key.upper() in _RAW_OPERATORS else op


def normalize_where(data: Any, depth: int = 0) -> Any:
    """A filter with each operator, and `and` or `or` for a group, in the spelling the schema
    lists. Anything else is left as it is, for `where_from_dict()` to check, including groups
    nested deeper than it allows."""
    if not isinstance(data, Mapping) or depth > MAX_WHERE_DEPTH:
        return cast(Any, data)
    items = cast(Mapping[Any, Any], data)
    if len(items) == 1:
        key = next(iter(items))
        group = _GROUP_ALIASES.get(key.casefold(), key.casefold()) if isinstance(key, str) else key
        if group in ("all", "any") and isinstance(items[key], list | tuple):
            parts = cast(list[Any], items[key])
            return {group: [normalize_where(part, depth + 1) for part in parts]}
    if isinstance(items.get("op"), str):
        return {**items, "op": normalize_operator(items["op"])}
    return items


def _text(value: Any, operator: str) -> str:
    """Render one value, refusing the ones whose meaning would be different from what's written."""
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidQueryError("A value must be a finite number.")
    if isinstance(value, int) and abs(value) >= 10**_MAX_EXPONENT:
        raise InvalidQueryError("A number can't be this large.")
    if isinstance(value, Decimal) and (
        not value.is_finite() or abs(value.adjusted()) > _MAX_EXPONENT
    ):
        raise InvalidQueryError("A value must be a finite number that isn't this large.")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise InvalidQueryError(
                "A datetime in a query needs a timezone, so its meaning is clear."
            )
        value = value.isoformat()
    if not (value is None or isinstance(value, str | int | float | bool | Decimal | date)):
        raise InvalidQueryError("A value must be text, a number, true, false, or null.")
    text = format_value(value)
    if text != "" and not text.strip():
        raise InvalidQueryError("A value can't be only whitespace.")
    if text == "" and operator not in _EMPTY_ALLOWED:
        raise InvalidQueryError("An empty value can only be used with ==, !=, in, or not_in.")
    return text


def case_hint(value: Any, labels: Iterable[Any]) -> str:
    """A hint for a label sent in another case. It names the offered label, which the schema
    already shows, and never repeats the text that was sent."""
    if not isinstance(value, str):
        return ""
    found = {
        item for item in labels if isinstance(item, str) and item.casefold() == value.casefold()
    }
    return f" Labels must match exactly, as in {found.pop()!r}." if len(found) == 1 else ""


def _units(offered: Iterable[FieldRef | Field] | None) -> str:
    """Say what a number means on a field whose queries use other units, if one is offered."""
    types = {item.type for item in offered or () if isinstance(item, Field)}
    notes: list[str] = []
    if "percent" in types:
        notes.append("A percent is a fraction, so 50% is 0.5.")
    if "duration" in types:
        notes.append("A duration is whole milliseconds, so an hour is 3600000.")
    return "".join(f" {note}" for note in notes)


def _operators_help() -> str:
    return f"Use one of: {', '.join(_SCHEMA_OPERATORS)}."


def _field_set(fields: Iterable[FieldRef | Field]) -> frozenset[FieldRef]:
    """The fields a filter may use, with a `Field` counted by its label, as a model names it."""
    if isinstance(fields, str):
        raise InvalidQueryError("fields takes a list of labels or IDs, not a single string.")
    allowed = frozenset(item.label if isinstance(item, Field) else item for item in fields)
    if not allowed:
        raise InvalidQueryError("fields can't be empty, because no filter could use it.")
    return allowed


def _descriptions(
    descriptions: Mapping[FieldRef, str], fields: Iterable[FieldRef | Field] | None
) -> str:
    """Describe the fields in one sentence, refusing a description for a field not offered."""
    if fields is None:
        raise InvalidQueryError(
            "descriptions needs fields, so that each one is for a field offered."
        )
    names: dict[FieldRef, str] = {}
    for item in fields:
        if isinstance(item, Field):
            names[item.label] = names[item.id] = item.label
        else:
            names[item] = str(item)
    entries: list[str] = []
    for key, text in descriptions.items():
        if key not in names:
            raise InvalidQueryError(
                f"descriptions has {key!r}, which isn't one of the fields offered."
            )
        if not isinstance(text, str) or not text.strip():  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidQueryError("Each description must be text that isn't empty.")
        entries.append(f"{names[key]}: {text.strip()}")
    return f" Fields: {' '.join(entries)}" if entries else ""


def where_from_dict(
    data: Mapping[str, Any], *, fields: Iterable[FieldRef] | Iterable[Field] | None = None
) -> WhereExpression:
    """Build a condition from plain data, such as the JSON from a model's tool call.

    The result works anywhere a condition built with `F` does, including combined with one, which
    is how to keep a model inside a scope your code decides:

    ```python
    scope = F("Project") == project
    allowed = ["Status", "Due Date"]
    where = scope & where_from_dict(
        data, fields=allowed
    )
    ```

    A condition is `{"field": "Status", "op": "==", "value": "Open"}`, and `{"all": [...]}` and
    `{"any": [...]}` combine them, nested if needed. `field` is a label or ID. `op` is `==`, `!=`,
    `<`, `<=`, `>`, `>=`, `contains`, `not_contains`, `starts_with`, `not_starts_with`, `has`,
    `not_has`, `in`, `not_in`, or a Quickbase operator such as `TV`, other than `IR` and `XIR`.
    `has` matches one of a multi-select field's values exactly.

    Spellings a model tends to write are read as the operator they mean: case, spaces, hyphens,
    and camelCase don't matter, so `starts with` and `startsWith` are `starts_with`, and `eq`,
    `=`, `ne`, `<>`, `lt`, `gte`, and the like are the matching symbol. `and` and `or` are read as
    `all` and `any`. Each of these means exactly one operator, so none can change what a filter
    means. Labels are never guessed: a label must match exactly.

    Values are escaped as they are with `F`. Unlike `F`, a value is also checked against the type of
    its field when the query is built: dates must be `YYYY-MM-DD`, timestamps need a time zone or to
    be dates, and numbers and checkboxes must be what they say. So `today` is refused, as Quickbase
    matches nothing, with no error, for text it can't read as a date.

    This protects a query's shape, not its meaning. A model that chooses the field and operator
    chooses the result, so a condition can still match every record. See
    [Using pyquickbase from an AI agent](agents/untrusted-input.md#filters-from-a-model).

    Args:
        data: The condition or group. `value` is required on every condition, so that a dropped key
            can't turn into a search for empty fields. Send `null` to mean that on purpose, which
            works with `==`, `!=`, `in`, and `not_in`.
        fields: The only fields this filter may use, as labels, IDs, or `Field` objects, which
            count by label. Pass the same list as to `where_schema()`. Without this, it may use any
            field the token can see, and a model could learn a value it can't read by filtering on
            it. A field listed by label can't be named by its ID.

    Returns:
        A condition to pass as `where`.

    Raises:
        InvalidQueryError: The data can't be used, and the message says where. Nothing was sent.
            One with over 100 conditions, counting each value of `in` and `not_in`, or groups over
            20 levels deep, is refused too. Type checks raise this when the query is built, not
            here.
        UnknownFieldError: The table has no such field, raised when the query is built.
    """
    allowed = None if fields is None else _field_set(fields)
    return _Builder(allowed).build(normalize_where(data), "where", 0)


def _condition_schema(fields: frozenset[FieldRef] | None) -> dict[str, Any]:
    field: dict[str, Any] = {"type": ["string", "integer"]} if fields is None else {}
    if fields is not None:
        field["enum"] = sorted(fields, key=str)
    return {
        "type": "object",
        "properties": {
            "field": field,
            "op": {"enum": list(_SCHEMA_OPERATORS)},
            "value": {
                "type": ["string", "number", "boolean", "array", "null"],
                "items": {"type": ["string", "number", "boolean", "null"]},
            },
        },
        "required": ["field", "op", "value"],
        "additionalProperties": False,
    }


def _group_schema(key: str, depth: int, condition: dict[str, Any]) -> dict[str, Any]:
    """Describe one group. It only holds the other kind, as `all` inside `all` would add nothing."""
    other = "any" if key == "all" else "all"
    items = condition
    if depth > 1:
        items = {"anyOf": [condition, _group_schema(other, depth - 1, condition)]}
    return {
        "type": "object",
        "properties": {key: {"type": "array", "items": items, "minItems": 1}},
        "required": [key],
        "additionalProperties": False,
    }


def where_schema(
    *,
    fields: Iterable[FieldRef] | Iterable[Field] | None = None,
    field_descriptions: Mapping[FieldRef, str] | None = None,
    max_depth: int = 2,
) -> dict[str, Any]:
    """Describe the format of `where_from_dict()` as JSON Schema, for a tool's input.

    The schema is self-contained, with no `$ref`, so it can go anywhere in a tool's input schema.
    It's a new dictionary each time, so you can add to it, such as a `description`.

    Args:
        fields: The fields to offer, as labels, IDs, or `Field` objects, so a model can't name one
            that doesn't exist. Pass the same fields to `where_from_dict()`, by label or ID, to
            enforce the list, as this only describes it. Field labels can contain any text, and
            this shows them to the model, so only offer fields from an app you trust. Given
            `Field` objects, the description also says what a number means on a percent or
            duration field.
        field_descriptions: What each field means, as `{label or ID: text}`, added to the schema's
            description so the model can choose the right field. Each must be for a field in
            `fields`, so a renamed field can't silently keep a description.
        max_depth: How many levels of `all` and `any` to describe, from 0 to 5. The default allows
            `A and (B or C)`. A tool's schema is sent to the model with every request, so each level
            has a cost. `where_from_dict()` accepts deeper filters regardless.

    Returns:
        The JSON Schema, as a dictionary.

    Raises:
        InvalidQueryError: `fields` is empty, `max_depth` is out of range, or a description isn't
            for a field in `fields`.
    """
    if isinstance(max_depth, bool) or not 0 <= max_depth <= _MAX_SCHEMA_DEPTH:
        raise InvalidQueryError(f"max_depth must be from 0 to {_MAX_SCHEMA_DEPTH}.")
    offered: list[FieldRef | Field] | None = None
    if fields is not None:
        if isinstance(fields, str):
            raise InvalidQueryError("fields takes a list of labels or IDs, not a single string.")
        offered = list(fields)
    allowed = None if offered is None else _field_set(offered)
    glossary = "" if field_descriptions is None else _descriptions(field_descriptions, offered)
    condition = _condition_schema(allowed)
    schema: dict[str, Any] = {"description": f"One filter condition. {_SCHEMA_DESCRIPTION}"}
    if max_depth == 0:
        schema.update(condition)
    else:
        schema["description"] = f"A condition, or all or any of a list. {_SCHEMA_DESCRIPTION}"
        groups = [_group_schema(key, max_depth, condition) for key in ("all", "any")]
        schema["anyOf"] = [condition, *groups]
    schema["description"] += _units(offered) + glossary
    # Separates the copies of a condition, so that changing one in a tool's schema can't change all.
    return json.loads(json.dumps(schema))
