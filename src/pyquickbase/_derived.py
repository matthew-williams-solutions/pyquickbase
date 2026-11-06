"""Check that a formula, lookup, or summary field only reads what a client may read. No network
access.

A derived field shows values from other fields, and anyone who can read it sees them. So on a
client with `ReadRules`, a new one may only read fields the client can already return, and its
result reveals nothing new. Without `ReadRules` there's nothing to check, as the client can read
everything its token can.
"""

from collections.abc import Sequence

from ._formula import check_formula
from ._models import Field
from ._read_policy import ReadAccess, ReadRules, _allows  # pyright: ignore[reportPrivateUsage]
from .errors import ReadRefusedError

_USER_TYPES = frozenset({"user", "multiuser"})


def table_access(rules: Sequence[ReadRules], table_id: str) -> list[ReadAccess] | None:
    """What every `ReadRules` allows on a table, or None if there are none to check.

    Raises:
        ReadRefusedError: A `ReadRules` doesn't list the table, so nothing in it can be read.
    """
    if not rules:
        return None
    found: list[ReadAccess] = []
    for item in rules:
        access = item._access(table_id)  # pyright: ignore[reportPrivateUsage]
        if access is None:
            raise ReadRefusedError(
                f"This client can't read table {table_id}, so a field can't show anything from "
                "it. Nothing was sent."
            )
        found.append(access)
    return found


def readable(field: Field, access: Sequence[ReadAccess]) -> bool:
    return all(_allows(item.fields, field.id) for item in access)


def check_formula_sources(
    formula: str, fields: Sequence[Field], access: Sequence[ReadAccess] | None
) -> None:
    """Refuse a formula that reads a field the client can't return, or a user's details it hides.

    Raises:
        ReadRefusedError: The message says which field or function.
    """
    if access is None:
        return
    shown = [item for item in fields if readable(item, access)]
    if not all(item.user_emails for item in access):
        # A formula can turn a user into an email address, which these rules leave out.
        users = {item.label.casefold() for item in shown if item.type in _USER_TYPES}
        named = check_formula(formula, [item.label for item in shown])
        used = sorted(label for label in named.fields if label.casefold() in users)
        if used:
            raise ReadRefusedError(
                f"The formula uses {', '.join(f'[{label}]' for label in used)}, a user field, and "
                "this client hides users' email addresses, which a formula can show. Nothing was "
                "sent."
            )
        return
    check_formula(formula, [item.label for item in shown])


def check_readable(
    field: Field, access: Sequence[ReadAccess] | None, table_id: str, what: str
) -> None:
    """Refuse unless the client can return this field.

    Raises:
        ReadRefusedError: It can't, so a field showing it would reveal it.
    """
    if access is None or readable(field, access):
        return
    raise ReadRefusedError(
        f"{what} would show field {field.id} of table {table_id}, which this client can't read. "
        "Nothing was sent."
    )
