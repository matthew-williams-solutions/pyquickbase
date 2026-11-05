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
"""A field's label (`str`) or ID (`int`)."""
_CURRENT_USER = "_curuser_"
_USER_TYPES = frozenset({"user", "multiuser"})
# On a `user` field, EX never matches a user, and XEX matches everyone, including that user.
_USER_FIELD_OPERATORS = {"EX": "TV", "XEX": "XTV"}
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
_ESCAPE = re.compile(r"\\(.)")
_LIST_WORD = re.compile(r"\b(?:OR|AND)\b")
_FIELD_REFERENCE = re.compile(r"(?:^|\s)_fid_", re.IGNORECASE)
_WHERE_HELP = (
    "Each condition must be {fieldId.OPERATOR.'value'} in its own braces, joined outside the "
    "braces with AND or OR in uppercase. Write an apostrophe in a value as \\'."
)


def format_value(value: Any) -> str:
    """Render a Python value for use between the quotes of a condition.

    Backslashes and quotes are escaped. Quickbase reads some text inside quotes as query syntax,
    with no way to escape it, so a value from someone else could otherwise match other records:

    - Braces can end the condition, and Quickbase can't search for them, so they're rejected.
    - `_FID_6` compares with field 6, so it matches every record on field 6 itself. It's rejected.
    - An uppercase `OR` or `AND` standing alone splits the value, so `a OR b` matches `a` and `b`
      but not `a OR b`. Comparisons ignore case, so in lowercase they find the literal text.
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
        if _FIELD_REFERENCE.search(value):
            raise QueryError("Quickbase reads a value like _FID_6 as a field, not as text.")
        lowered = _LIST_WORD.sub(lambda word: word[0].lower(), value)
        return lowered.replace("\\", "\\\\").replace("'", "\\'")
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
        item = fields.resolve(self.field)
        if item.type in _USER_TYPES and self.value.strip().casefold() == _CURRENT_USER:
            raise QueryError(
                "On a user field, Quickbase reads _curuser_ as the token's owner, so a value from "
                "someone else could match the owner's records. To mean that on purpose, write a "
                "query string."
            )
        operator = self.operator
        if item.type == "user":
            operator = _USER_FIELD_OPERATORS.get(operator, operator)
        return f"{{{item.id}.{operator}.'{self.value}'}}"


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

    On a `user` or `multiuser` field, compare with an email or user ID.

    Args:
        field: The field's label or ID.
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
        """Match records whose value contains this text.

        Args:
            value: The text to look for.

        Returns:
            A condition to pass as `where`.
        """
        return self._condition("CT", value)

    def not_contains(self, value: Any) -> Condition:
        """Match records whose value doesn't contain this text.

        Args:
            value: The text to look for.

        Returns:
            A condition to pass as `where`.
        """
        return self._condition("XCT", value)

    def starts_with(self, value: Any) -> Condition:
        """Match records whose value starts with this text.

        Args:
            value: The text to look for.

        Returns:
            A condition to pass as `where`.
        """
        return self._condition("SW", value)

    def not_starts_with(self, value: Any) -> Condition:
        """Match records whose value doesn't start with this text.

        Args:
            value: The text to look for.

        Returns:
            A condition to pass as `where`.
        """
        return self._condition("XSW", value)

    def is_in(self, values: Iterable[Any]) -> Expression:
        """Match any of the values, as a series of `EX` conditions joined with `OR`.

        Args:
            values: The values to match. There must be at least one.

        Returns:
            A condition to pass as `where`.

        Raises:
            QueryError: There are no values.
        """
        conditions = [self._condition("EX", value) for value in values]
        if not conditions:
            raise QueryError("is_in() needs at least one value.")
        return conditions[0] if len(conditions) == 1 else Group.of("OR", *conditions)

    def op(self, operator: str, value: Any) -> Condition:
        """Use any Quickbase operator directly, such as `HAS` or `IR`.

        Args:
            operator: The operator, in capitals. Quickbase treats a lowercase one as "contains".
            value: The value to compare with.

        Returns:
            A condition to pass as `where`.

        Raises:
            QueryError: Quickbase has no such operator.
        """
        if operator not in OPERATORS:
            raise QueryError(f"Unknown operator {operator!r}. Operators must be uppercase.")
        return self._condition(operator, value)


def _where_error(where: str, position: int, problem: str) -> QueryError:
    return QueryError(
        f"Invalid where clause: {problem} near `{where[position : position + 40]}`. {_WHERE_HELP}"
    )


@dataclass(frozen=True, slots=True)
class WhereCondition:
    """One condition from a `where` string, with its value unquoted and unescaped.

    Attributes:
        field_id: The ID of the field it tests.
        operator: Quickbase's operator, such as `EX`.
        value: The value it compares with, as text.
    """

    field_id: int
    operator: str
    value: str


def _unquote(raw: str) -> str:
    return _ESCAPE.sub(r"\1", raw[1:-1]) if raw.startswith("'") else raw


def _scan_where(text: str, fields: FieldIndex) -> list[WhereCondition]:
    found: list[WhereCondition] = []
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
                raise _where_error(
                    text, position, f"field {match[1]} doesn't exist, or this token can't see it"
                ) from None
            if match[2] not in OPERATORS:
                raise _where_error(
                    text, position, f"unknown operator '{match[2]}', operators must be uppercase"
                )
            found.append(WhereCondition(int(match[1]), match[2], _unquote(match[3])))
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
    return found


def validate_where(where: str, fields: FieldIndex) -> str:
    """Check a hand-written `where` string and return it with outer whitespace removed.

    This catches mistakes, not attacks: a string can legitimately contain several conditions, so
    build queries with `F` when any of the text comes from someone else.
    """
    text = where.strip()
    _scan_where(text, fields)
    return text


def parse_where(where: str, fields: Iterable[Field]) -> list[WhereCondition]:
    """Check a hand-written `where` string and return its conditions.

    Useful for extra checks of your own, or for finding out which fields a string refers to.

    Args:
        where: The string, such as `{7.EX.'Open'}`.
        fields: The table's fields, from `Table.fields()`.

    Returns:
        A `WhereCondition` for each condition in the string.

    Raises:
        QueryError: It has the same mistakes that `table.query()` would refuse.
        UnknownFieldError: It names a field that isn't in `fields`.
    """
    return _scan_where(where.strip(), FieldIndex(fields))


def compile_where(where: Expression | str, fields: FieldIndex) -> str:
    if isinstance(where, str):
        return validate_where(where, fields)
    if isinstance(where, Expression):  # pyright: ignore[reportUnnecessaryIsInstance]
        return where.compile(fields)
    raise QueryError("`where` must be a condition built with F, or a string.")


FieldRefs = FieldRef | F | Sequence[FieldRef | F]
"""One field or a list of them, each by label, ID, or field object."""


def _as_list(refs: FieldRefs, argument: str) -> list[FieldRef | F]:
    if isinstance(refs, str | int | F):
        return [refs]
    try:
        items = list(refs)
    except TypeError:
        raise QueryError(f"{argument} takes fields or a list of them, not this value.") from None
    for item in items:
        if not isinstance(item, str | int | F):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise QueryError(f"{argument} takes field labels, IDs, or fields, not this value.")
    return items


def _named(field: F) -> FieldRef:
    return field._field  # pyright: ignore[reportPrivateUsage]


def _sort_field(ref: FieldRef | F, fields: FieldIndex) -> dict[str, Any]:
    if isinstance(ref, F):
        # A field object means exactly that field, so a leading `-` in its label isn't a prefix.
        return {"fieldId": fields.resolve(_named(ref)).id, "order": "ASC"}
    descending = False
    if isinstance(ref, int) and not isinstance(ref, bool) and ref < 0:
        ref, descending = -ref, True
    elif isinstance(ref, str) and ref.startswith("-") and not fields.has_label(ref):
        ref, descending = ref[1:], True
    return {"fieldId": fields.resolve(ref).id, "order": "DESC" if descending else "ASC"}


def _select_ids(
    select: FieldRefs | None, all_fields: Sequence[Field], fields: FieldIndex
) -> list[int]:
    if select is None:
        ids = [item.id for item in all_fields if item.appears_by_default]
    else:
        ids = [
            fields.resolve(_named(ref) if isinstance(ref, F) else ref).id
            for ref in _as_list(select, "select")
        ]
    return list(dict.fromkeys([RECORD_ID_FIELD, *ids]))


def build_query_body(
    table_id: str,
    all_fields: Sequence[Field],
    *,
    where: Expression | str | None,
    select: FieldRefs | None,
    order_by: FieldRefs | None,
) -> dict[str, Any]:
    """Build the body of a records query, without its paging options.

    Record ID# is always selected, and is always the last sort key so that pages stay stable
    when other sort values tie.
    """
    fields = FieldIndex(all_fields)
    refs = [] if order_by is None else _as_list(order_by, "order_by")
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
