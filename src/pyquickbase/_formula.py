"""Read what a Quickbase formula uses, and check it against what a client may read. No network
access.

This is a tokenizer, not a parser. It finds field references (`[Label]`) and function calls
(`Name(`), skipping strings and comments. Anything it can't read is refused, and so is any function
that isn't known to only work on the record's own values, such as `GetRecords()`, which reads other
tables. A function missing from the list is refused with its name, so add it once it's checked.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .errors import ReadRefusedError

# Functions that only work on the values they're given, the current user, or the date. Compared in
# lowercase, as Quickbase doesn't mind case. Size() only counts a list it's given. Anything that
# queries records, such as GetRecord(), GetRecords(), GetFieldValues(), and SumValues(), is left out
# on purpose, and so are the ones that turn a user into an email address, which read rules can hide.
SAFE_FUNCTIONS = frozenset(
    name.lower()
    for name in [
        "Abs",
        "AdjustMonth",
        "AdjustYear",
        "And",
        "Average",
        "Base64Decode",
        "Base64Encode",
        "Begins",
        "Case",
        "Ceil",
        "Contains",
        "Count",
        "Date",
        "Day",
        "DayOfWeek",
        "DayOfYear",
        "Days",
        "Duration",
        "Ends",
        "Exp",
        "Find",
        "FirstDayOfMonth",
        "FirstDayOfPeriod",
        "FirstDayOfWeek",
        "FirstDayOfYear",
        "Floor",
        "Frac",
        "Hour",
        "Hours",
        "If",
        "IfNull",
        "Includes",
        "Int",
        "IsDigit",
        "IsLetter",
        "IsNull",
        "IsUserEmail",
        "IsWeekday",
        "LastDayOfMonth",
        "LastDayOfPeriod",
        "LastDayOfWeek",
        "LastDayOfYear",
        "Left",
        "Length",
        "List",
        "Ln",
        "Log",
        "Lower",
        "Max",
        "Mid",
        "Min",
        "Minute",
        "Minutes",
        "Mod",
        "Month",
        "MSecs",
        "Not",
        "NotLeft",
        "NotRight",
        "Now",
        "Nz",
        "Or",
        "Part",
        "PrevDayOfWeek",
        "NextDayOfWeek",
        "RegexMatch",
        "RegexReplace",
        "Rem",
        "Right",
        "Round",
        "Second",
        "Seconds",
        "Size",
        "SearchAndReplace",
        "Split",
        "Sqrt",
        "Today",
        "ToDate",
        "ToDays",
        "ToFormattedText",
        "ToHours",
        "ToMinutes",
        "ToMSecs",
        "ToNumber",
        "ToSeconds",
        "ToText",
        "ToTimeOfDay",
        "ToTimestamp",
        "ToUser",
        "ToUserList",
        "ToWeeks",
        "Trim",
        "Truncate",
        "Upper",
        "URLDecode",
        "URLEncode",
        "User",
        "UserListToNames",
        "UserRoles",
        "UserToName",
        "WeekdayAdd",
        "WeekdaySub",
        "Weeks",
        "Year",
    ]
)

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FIELD_ID_REFERENCE = re.compile(r"_FID_([1-9][0-9]*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FormulaParts:
    """What a formula uses.

    Attributes:
        fields: The labels it references, as written. `[_FID_7]` names field 7 by its ID.
        functions: The functions it calls, as written.
    """

    fields: frozenset[str]
    functions: frozenset[str]


def formula_parts(formula: str) -> FormulaParts:
    """Find the fields and functions a formula uses.

    Raises:
        ReadRefusedError: A string, comment, or field reference isn't closed, so it can't be read.
    """
    fields: set[str] = set()
    functions: set[str] = set()
    position, end = 0, len(formula)
    while position < end:
        char = formula[position]
        if char == '"':
            position = _string_end(formula, position)
        elif formula.startswith("//", position):
            newline = formula.find("\n", position)
            position = end if newline < 0 else newline + 1
        elif formula.startswith("/*", position):
            close = formula.find("*/", position + 2)
            if close < 0:
                raise _unreadable("a comment isn't closed")
            position = close + 2
        elif char == "[":
            close = formula.find("]", position + 1)
            if close < 0:
                raise _unreadable("a field reference isn't closed")
            fields.add(formula[position + 1 : close])
            position = close + 1
        elif match := _IDENTIFIER.match(formula, position):
            after = match.end()
            while after < end and formula[after].isspace():
                after += 1
            if after < end and formula[after] == "(":
                functions.add(match[0])
            position = match.end()
        else:
            position += 1
    return FormulaParts(frozenset(fields), frozenset(functions))


def _string_end(formula: str, start: int) -> int:
    position = start + 1
    while position < len(formula):
        if formula[position] == "\\":
            position += 2
            continue
        if formula[position] == '"':
            return position + 1
        position += 1
    raise _unreadable("a string isn't closed")


def _unreadable(reason: str) -> ReadRefusedError:
    return ReadRefusedError(f"The formula can't be checked, as {reason}. Nothing was sent.")


def unsafe_functions(parts: FormulaParts) -> list[str]:
    """The functions a formula calls that aren't known to stay on the record's own values, each
    once, as first written."""
    found: dict[str, str] = {}
    for name in sorted(parts.functions):
        if name.lower() not in SAFE_FUNCTIONS:
            found.setdefault(name.lower(), name)
    return sorted(found.values(), key=str.lower)


def field_id_reference(reference: str) -> int | None:
    """The field ID a reference such as `_FID_7` names, or None for a label."""
    found = _FIELD_ID_REFERENCE.fullmatch(reference)
    return None if found is None else int(found[1])


def unreadable_fields(
    parts: FormulaParts, readable_labels: Iterable[str], readable_ids: Iterable[int] = ()
) -> list[str]:
    """The references a formula has that aren't readable, by label, which is unique ignoring
    case, or by `_FID_n`."""
    allowed = {label.casefold() for label in readable_labels}
    ids = set(readable_ids)

    def readable(reference: str) -> bool:
        field_id = field_id_reference(reference)
        return field_id in ids if field_id is not None else reference.casefold() in allowed

    return sorted(reference for reference in parts.fields if not readable(reference))


def check_formula(
    formula: str, readable_labels: Iterable[str], readable_ids: Iterable[int] = ()
) -> FormulaParts:
    """Refuse a formula unless it only reads these fields, with functions known to be safe.

    Raises:
        ReadRefusedError: It references another field, calls a function that isn't on the list,
            or can't be read. The message says which.
    """
    parts = formula_parts(formula)
    hidden = unreadable_fields(parts, readable_labels, readable_ids)
    if hidden:
        names = ", ".join(f"[{label}]" for label in hidden)
        raise ReadRefusedError(
            f"The formula uses {names}, which this client can't read, or which doesn't exist, so "
            "a field showing its result would reveal it. Nothing was sent."
        )
    unknown = unsafe_functions(parts)
    if unknown:
        raise ReadRefusedError(
            f"The formula calls {', '.join(name + '()' for name in unknown)}, which this client "
            "can't use in a formula, as it isn't known to only read the record's own values. "
            "Nothing was sent."
        )
    return parts
