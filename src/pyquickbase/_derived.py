"""Check that a formula, lookup, or summary field only reads what a client may read. No network
access.

A derived field shows values from other fields, and anyone who can read it sees them. So on a
client with `ReadRules`, a new one may only read fields the client can already return, and its
result reveals nothing new. Without `ReadRules` there's nothing to check, as the client can read
everything its token can.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from ._formula import check_formula, field_id_reference
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


@dataclass(frozen=True, slots=True)
class Source:
    """A field a formula may name, as a plan or the client sees it. A field a plan is still to
    make has no ID."""

    label: str
    type: str
    id: int | None
    readable: bool


def sources_of(fields: Sequence[Field], access: Sequence[ReadAccess]) -> list[Source]:
    return [Source(item.label, item.type, item.id, readable(item, access)) for item in fields]


def check_formula_against(formula: str, sources: Sequence[Source], *, emails_shown: bool) -> None:
    """Refuse a formula that names a field that isn't readable, or a user field when the client
    hides users' email addresses, which a formula can turn a user into.

    Raises:
        ReadRefusedError: The message says which field or function.
    """
    shown = [item for item in sources if item.readable]
    parts = check_formula(
        formula,
        [item.label for item in shown],
        [item.id for item in shown if item.id is not None],
    )
    if emails_shown:
        return
    users = [item for item in shown if item.type in _USER_TYPES]
    labels = {item.label.casefold() for item in users}
    ids = {item.id for item in users}
    used = sorted(
        reference
        for reference in parts.fields
        if reference.casefold() in labels or field_id_reference(reference) in ids
    )
    if used:
        raise ReadRefusedError(
            f"The formula uses {', '.join(f'[{label}]' for label in used)}, a user field, and "
            "this client hides users' email addresses, which a formula can show. Nothing was sent."
        )


def emails_shown(access: Sequence[ReadAccess]) -> bool:
    return all(item.user_emails for item in access)


def check_formula_sources(
    formula: str, fields: Sequence[Field], access: Sequence[ReadAccess] | None
) -> None:
    """Refuse a formula that reads a field the client can't return, or a user's details it hides.

    Raises:
        ReadRefusedError: The message says which field or function.
    """
    if access is None:
        return
    check_formula_against(formula, sources_of(fields, access), emails_shown=emails_shown(access))


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
