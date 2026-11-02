"""Build and check `where` clauses and query bodies. No network access.

Quickbase accepts many broken queries and answers with plausible results: a lowercase or unknown
operator acts like "contains", and an unknown field matches nothing. Everything here exists so
that those mistakes raise an error instead. See docs/api-notes.md for the tested behavior.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from ._models import RECORD_ID_FIELD, Field, FieldIndex
from .errors import QueryError, UnknownFieldError

FieldRef = str | int
OPERATORS = frozenset(
    {
        "EX", "XEX", "CT", "XCT", "SW", "XSW", "LT", "LTE", "GT", "GTE",
        "BF", "OBF", "AF", "OAF", "IR", "XIR", "HAS", "XHAS", "TV", "XTV",
    }
)  # fmt: skip
_CONDITION = re.compile(
    r"\{\s*(\d+)\s*\.\s*([A-Za-z]+)\s*\.\s*('(?:[^'{}\\]|\\[^{}])*'|[^'{}\\]*?)\s*\}"
)
_JOIN = re.compile(r"(?:AND|OR)\b")
_WHERE_HELP = (
    "Each condition must be {fieldId.OPERATOR.'value'} in its own braces, joined outside the "
    "braces with AND or OR in uppercase. Write an apostrophe in a value as \\'."
)


def format_value(value: Any) -> str:
    """Render a Python value for use between the quotes of a condition.

    Backslashes and quotes are escaped. Braces are rejected because Quickbase can't search for
    them, and because every way we've found to break out of a value relies on one.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise QueryError("A datetime in a query needs a timezone, so its meaning is clear.")
        return str(int(value.timestamp() * 1000))
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        if "{" in value or "}" in value:
            raise QueryError("Quickbase can't search for values containing { or }.")
        return value.replace("\\", "\\\\").replace("'", "\\'")
    raise QueryError(f"Can't use a value of type {type(value).__name__} in a query.")


class Expression:
    """A query condition. Combine with `&` and `|`, not `and` and `or`."""

    __slots__ = ()

    def __and__(self, other: "Expression") -> "Group":
        return Group.of("AND", self, other)

    def __or__(self, other: "Expression") -> "Group":
        return Group.of("OR", self, other)

    def __bool__(self) -> bool:
        # `a and b` or `1 < F("x") < 5` would otherwise silently drop a condition.
        raise QueryError(
            "Combine conditions with & and |, not `and`, `or`, or chained comparisons."
        )

    def compile(self, fields: FieldIndex) -> str:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Condition(Expression):
    field: FieldRef
    operator: str
    value: str

    def compile(self, fields: FieldIndex) -> str:
        return f"{{{fields.resolve(self.field).id}.{self.operator}.'{self.value}'}}"


@dataclass(frozen=True, slots=True)
class Group(Expression):
    joiner: str
    parts: tuple[Expression, ...]

    @classmethod
    def of(cls, joiner: str, *parts: Expression) -> "Group":
        # Callers without a type checker can pass anything here.
        if not all(isinstance(part, Expression) for part in parts):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise QueryError("Conditions can only be combined with other conditions.")
        flat: list[Expression] = []
        for part in parts:
            if isinstance(part, Group) and part.joiner == joiner:
                flat.extend(part.parts)
            else:
                flat.append(part)
        return cls(joiner, tuple(flat))

    def compile(self, fields: FieldIndex) -> str:
        return self.joiner.join(
            f"({part.compile(fields)})" if isinstance(part, Group) else part.compile(fields)
            for part in self.parts
        )


class F:
    """A field in a query, named by label (`str`) or ID (`int`).

    `(F("Status") == "Done") & (F("Due Date") < date.today())`. Values are escaped for you, so
    this is the safe way to use text you don't control. `== None` matches empty fields.
    """

    __slots__ = ("_field",)
    __hash__: ClassVar[None] = None  # pyright: ignore[reportIncompatibleMethodOverride]

    def __init__(self, field: FieldRef) -> None:
        if isinstance(field, bool) or not isinstance(field, str | int):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise QueryError("F() takes a field label (str) or ID (int).")
        self._field = field

    def _condition(self, operator: str, value: Any) -> Condition:
        return Condition(self._field, operator, format_value(value))

    def _compare(self, operator: str, date_operator: str, value: Any) -> Condition:
        return self._condition(date_operator if isinstance(value, date) else operator, value)

    def __eq__(self, value: object) -> Condition:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._condition("EX", value)

    def __ne__(self, value: object) -> Condition:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._condition("XEX", value)

    def __lt__(self, value: Any) -> Condition:
        return self._compare("LT", "BF", value)

    def __le__(self, value: Any) -> Condition:
        return self._compare("LTE", "OBF", value)

    def __gt__(self, value: Any) -> Condition:
        return self._compare("GT", "AF", value)

    def __ge__(self, value: Any) -> Condition:
        return self._compare("GTE", "OAF", value)

    def contains(self, value: Any) -> Condition:
        return self._condition("CT", value)

    def not_contains(self, value: Any) -> Condition:
        return self._condition("XCT", value)

    def starts_with(self, value: Any) -> Condition:
        return self._condition("SW", value)

    def not_starts_with(self, value: Any) -> Condition:
        return self._condition("XSW", value)

    def is_in(self, values: Iterable[Any]) -> Expression:
        """Match any of the values, as a series of `EX` conditions joined with `OR`."""
        conditions = [self._condition("EX", value) for value in values]
        if not conditions:
            raise QueryError("is_in() needs at least one value.")
        return conditions[0] if len(conditions) == 1 else Group.of("OR", *conditions)

    def op(self, operator: str, value: Any) -> Condition:
        """Use any Quickbase operator directly, such as `HAS` or `IR`."""
        if operator not in OPERATORS:
            raise QueryError(f"Unknown operator {operator!r}. Operators must be uppercase.")
        return self._condition(operator, value)


def _where_error(where: str, position: int, problem: str) -> QueryError:
    return QueryError(
        f"Invalid where clause: {problem} near `{where[position : position + 40]}`. {_WHERE_HELP}"
    )


def validate_where(where: str, fields: FieldIndex) -> str:
    """Check a hand-written `where` string and return it with outer whitespace removed.

    This catches mistakes, not attacks: a string can legitimately contain several conditions, so
    build queries with `F` when any of the text comes from someone else.
    """
    text = where.strip()
    position, depth, expect_condition = 0, 0, True
    while position < len(text):
        if text[position].isspace():
            position += 1
        elif expect_condition and text[position] == "(":
            position, depth = position + 1, depth + 1
        elif expect_condition:
            match = _CONDITION.match(text, position)
            if match is None:
                raise _where_error(text, position, "expected a condition")
            try:
                fields.resolve(int(match[1]))
            except UnknownFieldError:
                raise _where_error(text, position, f"field {match[1]} doesn't exist") from None
            if match[2] not in OPERATORS:
                raise _where_error(
                    text, position, f"unknown operator '{match[2]}', operators must be uppercase"
                )
            position, expect_condition = match.end(), False
        elif text[position] == ")":
            if depth == 0:
                raise _where_error(text, position, "unbalanced ')'")
            position, depth = position + 1, depth - 1
        else:
            match = _JOIN.match(text, position)
            if match is None:
                raise _where_error(text, position, "expected AND or OR between conditions")
            position, expect_condition = match.end(), True
    if expect_condition:
        raise _where_error(text, max(0, len(text) - 20), "clause is empty or ends with AND/OR")
    if depth:
        raise _where_error(text, 0, "unbalanced '('")
    return text


def compile_where(where: Expression | str, fields: FieldIndex) -> str:
    if isinstance(where, str):
        return validate_where(where, fields)
    if isinstance(where, Expression):  # pyright: ignore[reportUnnecessaryIsInstance]
        return where.compile(fields)
    raise QueryError("`where` must be a condition built with F, or a string.")


def _as_list(refs: FieldRef | Sequence[FieldRef]) -> list[FieldRef]:
    return [refs] if isinstance(refs, str | int) else list(refs)


def _sort_field(ref: FieldRef, fields: FieldIndex) -> dict[str, Any]:
    descending = False
    if isinstance(ref, int) and not isinstance(ref, bool) and ref < 0:
        ref, descending = -ref, True
    elif isinstance(ref, str) and ref.startswith("-") and not fields.has_label(ref):
        ref, descending = ref[1:], True
    return {"fieldId": fields.resolve(ref).id, "order": "DESC" if descending else "ASC"}


def _select_ids(
    select: FieldRef | Sequence[FieldRef] | None, all_fields: Sequence[Field], fields: FieldIndex
) -> list[int]:
    if select is None:
        ids = [item.id for item in all_fields if item.appears_by_default]
    else:
        ids = [fields.resolve(ref).id for ref in _as_list(select)]
    return list(dict.fromkeys([RECORD_ID_FIELD, *ids]))


def build_query_body(
    table_id: str,
    all_fields: Sequence[Field],
    *,
    where: Expression | str | None,
    select: FieldRef | Sequence[FieldRef] | None,
    order_by: FieldRef | Sequence[FieldRef] | None,
) -> dict[str, Any]:
    """Build the body of a records query, without its paging options.

    Record ID# is always selected, and is always the last sort key so that pages stay stable
    when other sort values tie.
    """
    fields = FieldIndex(all_fields)
    refs = [] if order_by is None else _as_list(order_by)
    sort = [_sort_field(ref, fields) for ref in refs]
    if all(item["fieldId"] != RECORD_ID_FIELD for item in sort):
        sort.append({"fieldId": RECORD_ID_FIELD, "order": "ASC"})
    body: dict[str, Any] = {
        "from": table_id,
        "select": _select_ids(select, all_fields, fields),
        "sortBy": sort,
    }
    if where is not None:
        body["where"] = compile_where(where, fields)
    return body
