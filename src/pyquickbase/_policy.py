"""What a client may do, written as rules over tables and fields. No network access.

A `Policy` is only a description, by label. `Policy.bind()` resolves it against an app once, and
the `BoundPolicy` it returns, in `_bound.py`, makes every decision. See docs/guides/agents.md.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, overload, runtime_checkable

from ._decision import SCHEMA_VERBS, VERBS, Decision, SchemaVerb
from ._query import Expression
from .errors import ConfigurationError

if TYPE_CHECKING:
    from ._async._resources import AsyncApp
    from ._bound import BoundPolicy
    from ._read_request import ReadRequest
    from ._sync._resources import App
    from ._write_request import WriteRequest

DEFAULT_MAX_RECORDS = 100
DEFAULT_MAX_SUMMARY_RECORDS = 10_000

NewFields = Literal["read", "hidden"] | str
"""What a field created after binding gets: `read`, `hidden`, or a class's name."""


@dataclass(frozen=True, slots=True)
class FieldSelector:
    """Which fields a rule or class covers. Make one with `fields`, or name fields by label or ID.

    Attributes:
        kind: `all`, `new`, `classed`, `labeled_like`, or `named`.
        values: The classes, the pattern, or the labels and IDs.
    """

    kind: Literal["all", "new", "classed", "labeled_like", "named"]
    values: tuple[str | int, ...] = ()

    def __repr__(self) -> str:
        if self.kind == "named":
            return repr(list(self.values))
        args = ", ".join(repr(item) for item in self.values)
        return f"fields.{self.kind}({args})"


@dataclass(frozen=True, slots=True)
class TableSelector:
    """Which tables a rule covers. Make one with `tables`, or name a table by name or ID.

    Attributes:
        kind: `all`, `created`, or `named`.
        values: For `named`, the table's name or ID.
    """

    kind: Literal["all", "created", "named"]
    values: tuple[str, ...] = ()

    def __repr__(self) -> str:
        if self.kind == "named":
            return repr(self.values[0])
        return f"tables.{self.kind}()"


class _Fields:
    """Field selectors, for rules and classes."""

    @staticmethod
    def all() -> FieldSelector:
        """Every field, including ones added after binding, which `new_fields` limits."""
        return FieldSelector("all")

    @staticmethod
    def new() -> FieldSelector:
        """Only the fields added after the policy was bound."""
        return FieldSelector("new")

    @staticmethod
    def classed(*names: str) -> FieldSelector:
        """The fields in any of these classes, including formulas, lookups, and summaries that read
        one, through any chain of them.

        Args:
            *names: The classes, from the policy's `classes`.
        """
        if not names or not all(isinstance(item, str) and item for item in names):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("fields.classed() takes one or more class names.")
        return FieldSelector("classed", tuple(names))

    @staticmethod
    def labeled_like(pattern: str) -> FieldSelector:
        """The fields whose labels match a pattern, ignoring case. Matched when the policy is bound.

        Args:
            pattern: The pattern, where `*` is any text and `?` is one character, such as `_*`.
        """
        if not isinstance(pattern, str) or not pattern:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("fields.labeled_like() takes a pattern, such as '_*'.")
        return FieldSelector("labeled_like", (pattern,))


class _Tables:
    """Table selectors, for rules."""

    @staticmethod
    def all() -> TableSelector:
        """Every table in the bound app, and any this client creates."""
        return TableSelector("all")

    @staticmethod
    def created() -> TableSelector:
        """Only the tables this client creates after the policy is bound."""
        return TableSelector("created")


fields = _Fields()
"""Field selectors: `fields.all()`, `fields.new()`, `fields.classed()`, and
`fields.labeled_like()`."""

tables = _Tables()
"""Table selectors: `tables.all()` and `tables.created()`."""

FieldTarget = str | int | FieldSelector
"""A field in a rule: a label, an ID, or a `fields` selector."""
FieldsArg = FieldTarget | Sequence[FieldTarget]
"""One field target or a list of them."""
TableTarget = str | TableSelector
"""A table in a rule: a name, an ID, or a `tables` selector."""
TablesArg = TableTarget | Sequence[TableTarget]
"""One table target or a list of them."""
ClassTarget = str | tuple[str, str | int] | FieldSelector
"""A field in a class: `"Table.Field"`, `("Table", "Field")`, or a `fields` selector."""


def _field_selectors(value: Any, argument: str) -> tuple[FieldSelector, ...]:
    items: list[Any] = (
        [value] if isinstance(value, str | int | FieldSelector) else list(value or ())
    )
    if not items:
        raise ConfigurationError(f"{argument} needs at least one field, or fields.all().")
    found: list[FieldSelector] = []
    named: list[str | int] = []
    for item in items:
        if isinstance(item, FieldSelector):
            found.append(item)
        elif isinstance(item, bool) or not isinstance(item, str | int) or item == "":
            raise ConfigurationError(f"{argument} takes labels, IDs, or fields.* selectors.")
        elif isinstance(item, int) and item < 1:
            raise ConfigurationError(f"{argument} has {item}, which isn't a field ID.")
        else:
            named.append(item)
    if named:
        found.append(FieldSelector("named", tuple(named)))
    return tuple(found)


def _table_selectors(value: Any, argument: str = "tables") -> tuple[TableSelector, ...]:
    items: list[Any] = [value] if isinstance(value, str | TableSelector) else list(value or ())
    if not items:
        raise ConfigurationError(f"{argument} needs at least one table, or tables.all().")
    found: list[TableSelector] = []
    for item in items:
        if isinstance(item, TableSelector):
            found.append(item)
        elif isinstance(item, str) and item:
            found.append(TableSelector("named", (item,)))
        else:
            raise ConfigurationError(f"{argument} takes table names, IDs, or tables.* selectors.")
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Grant:
    """Some verbs over some fields, within a `PolicyRule`. `fields` is None for verbs that apply to
    a whole table."""

    verbs: frozenset[str]
    fields: tuple[FieldSelector, ...] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyRule:
    """One rule of a `Policy`, made by `allow`, `deny`, or `limit`.

    Attributes:
        effect: `allow`, `deny`, or `limit`.
        tables: The tables it covers, or None for the app itself, as for `create_tables`.
        grants: The verbs it covers, and on which fields.
        options: Its limits, such as `max_records`, and for `limit.rows()`, its `where`.
        text: The rule as written, which a `Decision` names.
    """

    effect: Literal["allow", "deny", "limit"]
    tables: tuple[TableSelector, ...] | None
    grants: tuple[Grant, ...]
    options: Mapping[str, Any] = field(default_factory=dict[str, Any])
    text: str

    def __repr__(self) -> str:
        return self.text


@runtime_checkable
class Rule(Protocol):
    """Custom logic, added to a policy's `rules`. It can only refuse.

    `decide()` gets each request the built-in rules allowed, and returns None to leave it, or a
    `Decision` with `allowed=False` to refuse it. `BoundPolicy.offers()` asks about one field and
    one verb at a time, so a rule that decides each field on its own is offered exactly what it
    allows.
    """

    def decide(self, request: "ReadRequest | WriteRequest") -> Decision | None:
        """Refuse a request, or leave it to the other rules.

        Args:
            request: What the request would do, which holds no values.

        Returns:
            A `Decision` with `allowed=False` to refuse, or None.
        """
        ...


RuleLike = PolicyRule | Rule
"""A rule a `Policy` takes: one from `allow`, `deny`, or `limit`, or your own `Rule`."""


def _text(name: str, *args: Any, **options: Any) -> str:
    parts = [repr(_show(item)) for item in args]
    parts += [f"{key}={value!r}" for key, value in options.items()]
    return f"{name}({', '.join(parts)})"


def _limit(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ConfigurationError(f"{name} must be a positive whole number, or None for no limit.")
    return value


def _show(value: Any) -> Any:
    """A rule's argument as it was given, for its text."""
    if isinstance(value, list | tuple):
        return list(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return value


class _Allow:
    """The rules that grant. Anything no rule allows is refused."""

    def __init__(self, effect: Literal["allow", "deny"]) -> None:
        self._effect: Literal["allow", "deny"] = effect

    def _rule(
        self,
        name: str,
        tables_arg: Any,
        grants: tuple[Grant, ...],
        shown: tuple[Any, ...],
        options: Mapping[str, Any] | None = None,
        written: Mapping[str, Any] | None = None,
    ) -> PolicyRule:
        return PolicyRule(
            effect=self._effect,
            tables=None if tables_arg is None else _table_selectors(tables_arg),
            grants=grants,
            options=dict(options or {}),
            text=_text(
                f"{self._effect}.{name}", *(_show(item) for item in shown), **(written or {})
            ),
        )

    def _fields_rule(self, verb: str, tables_arg: TablesArg, fields_arg: FieldsArg) -> PolicyRule:
        selectors = _field_selectors(fields_arg, "fields")
        return self._rule(
            verb, tables_arg, (Grant(frozenset({verb}), selectors),), (tables_arg, fields_arg)
        )

    def filter(self, tables: TablesArg, fields: FieldsArg) -> PolicyRule:
        """Filtering on fields, which shows which records match, so it reveals something about
        their values. `read` already grants it, unless it's given its own `filter`.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            fields: The fields, by label or ID, or a `fields` selector.
        """
        return self._fields_rule("filter", tables, fields)

    def sort(self, tables: TablesArg, fields: FieldsArg) -> PolicyRule:
        """Sorting by fields, which reveals their order. `read` already grants it, unless it's given
        its own `sort`.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            fields: The fields, by label or ID, or a `fields` selector.
        """
        return self._fields_rule("sort", tables, fields)

    def insert(self, tables: TablesArg, fields: FieldsArg) -> PolicyRule:
        """Creating records that set these fields. An upsert needs `insert` and `update`.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            fields: The fields it may set, by label or ID, or a `fields` selector. Fields created
                after binding are never writable.
        """
        return self._fields_rule("insert", tables, fields)

    def update(self, tables: TablesArg, fields: FieldsArg) -> PolicyRule:
        """Changing these fields in existing records. Writing a field doesn't need read access to
        it.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            fields: The fields it may change, by label or ID, or a `fields` selector. Fields
                created after binding are never writable.
        """
        return self._fields_rule("update", tables, fields)

    def download(self, tables: TablesArg, fields: FieldsArg | None = None) -> PolicyRule:
        """Downloading files from file fields. `read` only returns a file's details.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            fields: The file fields, by label or ID, or a `fields` selector. Defaults to all.
        """
        return self._fields_rule(
            "download", tables, FieldSelector("all") if fields is None else fields
        )

    def delete_file(self, tables: TablesArg, fields: FieldsArg | None = None) -> PolicyRule:
        """Deleting versions of files.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            fields: The file fields, by label or ID, or a `fields` selector. Defaults to all.
        """
        return self._fields_rule(
            "delete_file", tables, FieldSelector("all") if fields is None else fields
        )

    def schema(
        self,
        tables: TablesArg,
        *verbs: SchemaVerb,
        fields: FieldsArg | None = None,
        field_types: Sequence[str] | None = None,
    ) -> PolicyRule:
        """Changing tables' schema, granted on the table where the change lands.

        `add_field`, `change_field`, and `delete_field` change fields, and `formula` makes or
        changes a formula, which also needs `add_field` or `change_field`. `lookup` adds a lookup
        to a child table, and also needs `read` on the parent's field. `summary` adds a summary to
        a parent table, and also needs `read` on the child's field, and `filter` on every field its
        `where` uses. `link` and `unlink` need a grant on both tables, as a relationship adds a
        link field and an "Add" record formula to the parent, and `unlink` also needs
        `delete_field` wherever its lookups and summaries are. `change_table` and `delete_table`
        change or delete the table itself.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            *verbs: What it grants, from those above.
            fields: Which fields `change_field` and `delete_field` may change. Defaults to all.
            field_types: The types `add_field` may create, such as `["text", "date"]`, or None
                for any. A formula counts by its result type.
        """
        if not verbs:
            raise ConfigurationError("schema() needs at least one verb, such as 'add_field'.")
        unknown = [item for item in verbs if item not in SCHEMA_VERBS]
        if unknown:
            raise ConfigurationError(
                f"schema() doesn't know {', '.join(map(repr, unknown))}. It takes "
                f"{', '.join(SCHEMA_VERBS)}."
            )
        types: frozenset[str] | None = None
        if field_types is not None:
            given: list[Any] = list(field_types)
            if isinstance(field_types, str) or not all(
                isinstance(item, str) and item for item in given
            ):
                raise ConfigurationError("field_types takes a list of field types, or None.")
            types = frozenset(field_types)
            if not types:
                raise ConfigurationError("field_types is empty. Leave out add_field instead.")
        selectors = _field_selectors(FieldSelector("all") if fields is None else fields, "fields")
        field_verbs = frozenset(verbs) & {"change_field", "delete_field"}
        table_verbs = frozenset(verbs) - field_verbs
        grants: list[Grant] = []
        if field_verbs:
            grants.append(Grant(field_verbs, selectors))
        if table_verbs:
            grants.append(Grant(table_verbs))
        written: dict[str, Any] = {}
        if fields is not None:
            written["fields"] = _show(fields)
        if field_types is not None:
            written["field_types"] = sorted(types or ())
        return self._rule(
            "schema",
            tables,
            tuple(grants),
            (tables, *verbs),
            {"field_types": types},
            written,
        )

    def create_tables(self) -> PolicyRule:
        """Creating tables in the bound app. A table this client creates is matched by
        `tables.created()` and `tables.all()`."""
        return self._rule("create_tables", None, (Grant(frozenset({"create_tables"})),), ())


class _AllowOnly(_Allow):
    """The rules that grant. Anything no rule allows is refused, and a deny always wins."""

    def read(
        self,
        tables: TablesArg,
        fields: FieldsArg | None = None,
        *,
        filter: FieldsArg | None = None,
        sort: FieldsArg | None = None,
        max_records: int | None = DEFAULT_MAX_RECORDS,
        max_summary_records: int | None = DEFAULT_MAX_SUMMARY_RECORDS,
        user_emails: bool = False,
    ) -> PolicyRule:
        """Reading fields' values and details, and filtering and sorting by them.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            fields: The fields it may return, by label or ID, or a `fields` selector. Defaults to
                all.
            filter: The fields it may filter on instead, or None for the same as `fields`. A
                filter shows which records match, so it reveals something about a field's values.
            sort: The fields it may sort by instead, or None for the same as `fields`.
            max_records: The most records one query or report can reach, counting `skip`, or None
                for no limit. It's how far paging can go, not the size of a page. Where several
                rules read a table, the highest applies.
            max_summary_records: The most records `summarize()` may read for one summary, or None
                for no limit.
            user_emails: Whether to keep email addresses in user values, and the uploader's
                details in file values. Without this, a user is only their `id` and `name`.
        """
        if not isinstance(user_emails, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("user_emails takes True or False.")
        shown = FieldSelector("all") if fields is None else fields
        main = _field_selectors(shown, "fields")
        grants = [Grant(frozenset({"read"}), main)]
        implied = {"filter": filter, "sort": sort}
        same = frozenset(verb for verb, value in implied.items() if value is None)
        if same:
            grants.append(Grant(same, main))
        for verb, value in implied.items():
            if value is not None:
                grants.append(Grant(frozenset({verb}), _field_selectors(value, verb)))
        options = {
            "max_records": _limit("max_records", max_records),
            "max_summary_records": _limit("max_summary_records", max_summary_records),
            "user_emails": user_emails,
        }
        written: dict[str, Any] = {}
        for key, value, default in (
            ("filter", filter, None),
            ("sort", sort, None),
            ("max_records", max_records, DEFAULT_MAX_RECORDS),
            ("max_summary_records", max_summary_records, DEFAULT_MAX_SUMMARY_RECORDS),
            ("user_emails", user_emails, False),
        ):
            if value != default:
                written[key] = _show(value)
        args = (tables,) if fields is None else (tables, fields)
        return self._rule("read", tables, tuple(grants), args, options, written)

    def delete(self, tables: TablesArg, *, max_records: int | None = None) -> PolicyRule:
        """Deleting records, which removes all of their fields.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            max_records: The most records one delete may remove, or None for no limit. With a
                limit, the client counts what matches before deleting, and refuses a delete
                through `request()`, which it can't count.
        """
        written = {} if max_records is None else {"max_records": max_records}
        return self._rule(
            "delete",
            tables,
            (Grant(frozenset({"delete"})),),
            (tables,),
            {"max_records": _limit("max_records", max_records)},
            written,
        )

    def report(self, table: TablesArg, reports: Sequence[str | int] | None = None) -> PolicyRule:
        """Running reports. A table report's columns are trimmed to what's readable, but its own
        filter and sort can use any field. A summary or chart report's columns can't be matched to
        fields, so it shows all of them.

        Args:
            table: The table, by name or ID, or a `tables` selector when `reports` is None.
            reports: The reports, by name or ID, or None for every report.
        """
        listed: tuple[str, ...] | None = None
        if reports is not None:
            if isinstance(reports, str) or not reports:
                raise ConfigurationError("reports takes a list of report names or IDs, or None.")
            items: list[Any] = list(reports)
            if not all(
                isinstance(item, str | int) and not isinstance(item, bool) for item in items
            ):
                raise ConfigurationError("reports takes a list of report names or IDs, or None.")
            listed = tuple(str(item) for item in items)
            selectors = _table_selectors(table, "table")
            if len(selectors) != 1 or selectors[0].kind != "named":
                raise ConfigurationError("report() lists reports for one table, by name or ID.")
        args = (table,) if reports is None else (table, reports)
        return self._rule(
            "report", table, (Grant(frozenset({"report"})),), args, {"reports": listed}
        )


class _Deny(_Allow):
    """The rules that refuse. A deny always wins over an allow."""

    def read(self, tables: TablesArg, fields: FieldsArg | None = None) -> PolicyRule:
        """Refuse reading, filtering, and sorting by fields, which hides them unless something
        else, such as a write, is allowed.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
            fields: The fields, by label or ID, or a `fields` selector. Defaults to all.
        """
        shown = FieldSelector("all") if fields is None else fields
        selectors = _field_selectors(shown, "fields")
        args = (tables,) if fields is None else (tables, fields)
        return self._rule(
            "read", tables, (Grant(frozenset({"read", "filter", "sort"}), selectors),), args
        )

    def delete(self, tables: TablesArg) -> PolicyRule:
        """Refuse deleting records.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
        """
        return self._rule("delete", tables, (Grant(frozenset({"delete"})),), (tables,))

    def report(self, tables: TablesArg) -> PolicyRule:
        """Refuse running reports.

        Args:
            tables: The tables, by name or ID, or a `tables` selector.
        """
        return self._rule("report", tables, (Grant(frozenset({"report"})),), (tables,))

    def all(self, selector: FieldSelector | TablesArg) -> PolicyRule:
        """Refuse everything on some fields, in every table, or on some tables.

        ```python
        deny.all(fields.classed("sensitive"))
        deny.all("Payroll")
        ```

        Args:
            selector: A `fields` selector, which hides those fields everywhere, or tables by name
                or ID, or a `tables` selector.
        """
        everything = frozenset(VERBS) - {"create_tables"}
        if isinstance(selector, FieldSelector):
            if selector.kind == "named":
                raise ConfigurationError(
                    "deny.all() takes a fields.* selector, or tables. To deny named fields, put "
                    "them in a class, or name their table in a deny rule."
                )
            return PolicyRule(
                effect="deny",
                tables=(TableSelector("all"),),
                grants=(Grant(everything, (selector,)),),
                text=_text("deny.all", selector),
            )
        return self._rule("all", selector, (Grant(everything),), (selector,))


class _Limit:
    """Limits on which records a client reaches."""

    def rows(self, tables: TablesArg, where: Expression | str) -> PolicyRule:
        """Only reach the records that match a condition. It's ANDed onto every query, summary,
        and delete the client sends, and into a summary field's filter.

        This is defense in depth: Quickbase's record rules for the token's role are still the real
        boundary. Reports, file downloads, and formulas on the table are refused, as the limit
        can't be added to them, and so is a lookup from it, which would show its records anywhere.
        The fields the condition uses may be filtered on.

        Args:
            tables: The tables, by name or ID. A `tables` selector works only if the condition
                names fields by ID.
            where: The condition, built with `F`, or a query string, by label or ID.
        """
        if not isinstance(where, Expression | str) or (  # pyright: ignore[reportUnnecessaryIsInstance]
            isinstance(where, str) and not where.strip()
        ):
            raise ConfigurationError("limit.rows() takes a condition built with F, or a string.")
        return PolicyRule(
            effect="limit",
            tables=_table_selectors(tables),
            grants=(),
            options={"where": where},
            text=_text("limit.rows", _show(tables), where),
        )


allow = _AllowOnly("allow")
"""The rules that grant: `allow.read()`, `allow.insert()`, `allow.schema()`, and the rest."""

deny = _Deny("deny")
"""The rules that refuse, which always win: `deny.all()`, `deny.update()`, and the rest."""

limit = _Limit()
"""Limits on which records a client reaches: `limit.rows()`."""


class Policy:
    """What a client may do, as rules over tables and fields. Bind it to an app, then give the
    `BoundPolicy` to a client.

    ```python
    policy = Policy(
        classes={"pay": ["Technicians.Hourly Rate"]},
        rules=[
            allow.read(tables.all()),
            allow.update("Job Tasks", ["Status", "Notes"]),
            deny.all(fields.classed("pay")),
        ],
    )
    bound = await policy.bind(qb.app("bq1234567"))
    ```

    Anything no rule allows is refused, and a deny always wins. A formula, lookup, or summary is
    in every class of the fields it reads, through any chain of them, so it's denied wherever they
    are.

    Args:
        app: The app's ID, which `bind()` checks, or None for any.
        classes: Names for groups of fields, as `{name: [fields]}`, for `fields.classed()`. Each
            field is `"Table.Field"`, `("Table", "Field")`, or a `fields` selector, such as
            `fields.labeled_like("_*")`.
        rules: What's allowed and refused, from `allow`, `deny`, and `limit`, and any `Rule`.
        new_fields: What a field added after binding gets: `read`, to be read, filtered, and
            sorted by where a rule allows it, `hidden`, or a class's name, which also leaves it
            read-only. It's never writable until the policy is bound again.

    Raises:
        ConfigurationError: A rule or class can't be used.
    """

    __slots__ = ("app_id", "classes", "new_fields", "rules")

    def __init__(
        self,
        *,
        app: str | None = None,
        classes: Mapping[str, Sequence[ClassTarget]] | None = None,
        rules: Sequence[RuleLike] = (),
        new_fields: NewFields = "read",
    ) -> None:
        if app is not None and (not isinstance(app, str) or not app.isalnum()):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError(
                "app takes the app's ID, which contains only letters and numbers."
            )
        found: dict[str, tuple[ClassTarget, ...]] = {}
        for name, targets in (classes or {}).items():
            if not isinstance(name, str) or not name:  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError("Each class needs a name.")
            items: list[Any] = [targets] if isinstance(targets, str | tuple) else list(targets)
            if not items:
                raise ConfigurationError(f"Class {name!r} has no fields.")
            for item in items:
                _check_class_target(name, item)
            found[name] = tuple(items)
        if isinstance(rules, PolicyRule) or not isinstance(rules, Sequence):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("rules takes a list of rules.")
        for item in rules:
            if not isinstance(item, PolicyRule) and not isinstance(item, Rule):  # pyright: ignore[reportUnnecessaryIsInstance]
                raise ConfigurationError(
                    f"{item!r} isn't a rule. Use allow, deny, or limit, or an object with "
                    "decide(request)."
                )
        if not isinstance(new_fields, str) or not new_fields:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ConfigurationError("new_fields takes 'read', 'hidden', or a class's name.")
        if new_fields not in ("read", "hidden") and new_fields not in found:
            raise ConfigurationError(
                f"new_fields is {new_fields!r}, which isn't 'read', 'hidden', or a class."
            )
        self.app_id: str | None = app
        self.classes: Mapping[str, tuple[ClassTarget, ...]] = found
        self.rules: tuple[RuleLike, ...] = tuple(rules)
        self.new_fields: str = new_fields

    @classmethod
    def read_only(cls) -> "Policy":
        """Read anything the token can, with no limit on records, and change nothing.

        It needs no binding, so a client takes it as it is.
        """
        return cls(
            rules=[
                allow.read(
                    tables.all(), max_records=None, max_summary_records=None, user_emails=True
                ),
                allow.download(tables.all()),
                allow.report(tables.all()),
                _RUN_FORMULAS,
            ]
        )

    @property
    def needs_app(self) -> bool:
        """Whether anything in it names a table or field, so it needs `bind()` before use."""
        if self.classes or self.new_fields not in ("read", "hidden"):
            return True
        for item in self.rules:
            if not isinstance(item, PolicyRule):
                continue
            if item.effect == "limit":
                return True
            if item.options.get("reports"):
                return True
            for selector in item.tables or ():
                if selector.kind == "named":
                    return True
            for grant in item.grants:
                for selector in grant.fields or ():
                    if selector.kind in ("named", "labeled_like", "classed"):
                        return True
        return False

    @overload
    def bind(self, app: "AsyncApp") -> "Any": ...
    @overload
    def bind(self, app: "App") -> "BoundPolicy": ...
    def bind(self, app: Any) -> Any:
        """Resolve every table, field, and class against an app, once.

        Every problem is reported at once. With an `AsyncApp`, this returns an awaitable. It reads
        the app's tables, each table's fields and relationships, and the reports of any table
        `allow.report()` lists by name, so use a client that can see them all.

        Args:
            app: The app, from a client without a policy.

        Returns:
            A `BoundPolicy`, which is frozen and keyed by ID.

        Raises:
            ConfigurationError: A table, field, report, or class can't be found, or is ambiguous.
                The message lists every one.
        """
        return app._bind_policy(self)  # pyright: ignore[reportPrivateUsage]

    def bind_offline(self) -> "BoundPolicy":
        """Bind a policy that names no table or field, without reading an app.

        Every field then counts as existing, so `new_fields` doesn't apply.

        Raises:
            ConfigurationError: It names a table, field, class, report, or row limit.
        """
        from ._bound import bind_offline

        return bind_offline(self)

    def __repr__(self) -> str:
        return f"Policy(app={self.app_id!r}, rules={list(self.rules)!r})"


_RUN_FORMULAS = PolicyRule(
    effect="allow",
    tables=(TableSelector("all"),),
    grants=(Grant(frozenset({"run_formula"})),),
    text="Policy.read_only()",
)


def _check_class_target(name: str, item: Any) -> None:
    if isinstance(item, FieldSelector):
        if item.kind not in ("all", "labeled_like"):
            raise ConfigurationError(
                f"Class {name!r} can use fields.all() and fields.labeled_like(), not {item!r}."
            )
        return
    if isinstance(item, tuple):
        pair: tuple[Any, ...] = item  # pyright: ignore[reportUnknownVariableType]
        if (
            len(pair) == 2
            and isinstance(pair[0], str)
            and isinstance(pair[1], str | int)
            and not isinstance(pair[1], bool)
        ):
            return
    elif isinstance(item, str) and "." in item.strip(".") and item.strip():
        return
    raise ConfigurationError(
        f"Class {name!r} has {item!r}. Name a field as 'Table.Field', ('Table', 'Field'), or with "
        "fields.labeled_like()."
    )
