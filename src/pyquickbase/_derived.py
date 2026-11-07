"""Check that a formula, lookup, or summary field only reads what a client may read. No network
access.

A derived field shows values from other fields, and anyone who can read it sees them. So on a
client with a policy, a new one may only read fields the client can already return, and its result
reveals nothing new. Without a policy there's nothing to check, as the client can read everything
its token can.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from ._bound import BoundPolicy, derived_refusal
from ._decision import Named, Verb
from ._formula import check_formula, field_id_reference
from ._models import Field
from ._query import where_field_ids

_USER_TYPES = frozenset({"user", "multiuser"})


@dataclass(frozen=True, slots=True)
class Source:
    """A field a formula may name, as a plan or the client sees it. A field a plan is still to
    make has no ID."""

    label: str
    type: str
    id: int | None
    readable: bool


def sources_of(fields: Sequence[Field], policy: BoundPolicy, table_id: str) -> list[Source]:
    return [
        Source(item.label, item.type, item.id, policy.readable(table_id, item.id))
        for item in fields
    ]


def _table(policy: BoundPolicy, table_id: str) -> Named:
    return policy._named_table(table_id) or Named(table_id, table_id)  # pyright: ignore[reportPrivateUsage]


def check_formula_against(
    formula: str,
    sources: Sequence[Source],
    *,
    emails_shown: bool,
    table: Named | None = None,
) -> None:
    """Refuse a formula that names a field that isn't readable, or a user field when the client
    hides users' email addresses, which a formula can turn a user into.

    Raises:
        PolicyRefusedError: The message says which field or function.
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
        raise derived_refusal(
            "formula",
            f"The formula uses {', '.join(f'[{label}]' for label in used)}, a user field, and "
            "this client hides users' email addresses, which a formula can show",
            table=table,
            hint="allow.read(..., user_emails=True)",
        )


def check_formula_sources(
    formula: str, fields: Sequence[Field], policy: BoundPolicy | None, table_id: str
) -> None:
    """Refuse a formula that reads a field the client can't return, or a user's details it hides.

    Raises:
        PolicyRefusedError: The message says which field or function.
    """
    if policy is None:
        return
    check_formula_against(
        formula,
        sources_of(fields, policy, table_id),
        emails_shown=policy.emails_shown(table_id),
        table=_table(policy, table_id),
    )


def check_readable(
    field: Field, policy: BoundPolicy | None, table_id: str, what: str, verb: Verb = "lookup"
) -> None:
    """Refuse unless the client can return this field.

    Raises:
        PolicyRefusedError: It can't, so a field showing it would reveal it.
    """
    if policy is None or policy.readable(table_id, field.id):
        return
    table = _table(policy, table_id)
    raise derived_refusal(
        verb,
        f"{what} would show {field.label} ({field.id}) of {table}, which this client can't read",
        table=table,
        hint=f"allow reading {field.label!r} in {table.name!r}",
    )


def check_lookup_parent(policy: BoundPolicy | None, parent_id: str, what: str) -> None:
    """Refuse a lookup from a parent with a row limit, which would show its records anywhere.

    Raises:
        PolicyRefusedError: The parent has a row limit.
    """
    if policy is None or policy.row_limit(parent_id) is None:
        return
    table = _table(policy, parent_id)
    raise derived_refusal(
        "lookup",
        f"{what} would show {table}'s records, which has a row limit, on records outside it",
        table=table,
        rule=policy.row_limit_rule(parent_id),
        layer="rows",
    )


def check_filterable(where: str, policy: BoundPolicy | None, table_id: str, what: str) -> None:
    """Refuse a summary's filter unless the client may filter on every field it uses, as the
    summary's value shows which records match, as a query's filter would.

    Raises:
        PolicyRefusedError: It uses a field the client can't filter on, or compares with a field
            reference such as `_FID_7`, so the fields it uses can't be told.
    """
    if policy is None:
        return
    table = _table(policy, table_id)
    used = where_field_ids(where)
    if used is None:
        raise derived_refusal(
            "summary",
            f"{what}'s filter compares with a field reference, such as _FID_7, so the fields it "
            "uses can't be checked. Compare with values instead",
            table=table,
        )
    hidden = sorted(item for item in used if not policy.filterable(table_id, item))
    if hidden:
        listed = ", ".join(str(item) for item in hidden)
        raise derived_refusal(
            "summary",
            f"{what}'s filter uses field {listed} of {table}, which this client can't filter "
            "on, so its value would show which records match",
            table=table,
        )


def limited_where(policy: BoundPolicy | None, child_id: str, where: str | None) -> str | None:
    """A summary's filter with the child's row limit ANDed in, so it only counts what the client
    may reach."""
    limit = None if policy is None else policy.row_limit(child_id)
    if limit is None:
        return where
    return limit if not where or not where.strip() else f"({limit})AND({where})"
