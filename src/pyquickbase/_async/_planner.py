"""Plan schema changes against an app as it is, and apply a plan. See `_schema_plan.py`.

A plan works on its own copy of the app's schema, so a later change can refer to a table or field
an earlier one makes. It reads only what the client may read, so it never mentions a field its
read rules hide, and it checks each change against the client's limits without sending it.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .._derived import (
    Source,
    check_filterable,
    check_formula_against,
    check_readable,
    emails_shown,
    readable,
    table_access,
)
from .._formula import field_id_reference, formula_parts
from .._models import RECORD_ID_FIELD, Field, FieldIndex, Relationship, TableInfo
from .._query import F, compile_where, where_field_ids
from .._schema_plan import (
    CreateField,
    CreateLookupField,
    CreateRelationship,
    CreateSummaryField,
    CreateTable,
    DeleteField,
    DeleteRelationship,
    DeleteTable,
    FieldRefValue,
    PlannedChange,
    SchemaChange,
    SchemaChangeResult,
    SchemaPlan,
    SettingChange,
    Status,
    UpdateField,
    UpdateTable,
    change_risk,
    fingerprint,
)
from ..errors import (
    InvalidValueError,
    QueryError,
    QuickbaseError,
    RequestRefusedError,
    SchemaChangeError,
    SchemaMismatchError,
    UnknownFieldError,
)

if TYPE_CHECKING:
    from ._resources import AsyncApp, AsyncTable

# The most records a plan reads to look for duplicates before making a field unique.
UNIQUE_CHECK_LIMIT = 10_000
# Stands for a field a plan makes, which has no ID yet, when asking the limits about changing it.
_NEW_FIELD_ID = 2**31 - 1
_BUILT_IN_FIELDS = frozenset({1, 2, 3, 4, 5})
_DERIVED_MODES = frozenset({"formula", "lookup", "summary"})


@dataclass
class _Field:
    """A field as a plan sees it. A field the plan makes has no ID yet."""

    id: int | None
    label: str
    type: str
    mode: str | None = None
    formula: str | None = None
    required: bool = False
    unique: bool = False
    choices: tuple[str, ...] = ()
    help_text: str = ""
    foreign_key: bool = False
    lookup: tuple[int | None, int | None] | None = None
    # The foreign key, the child's field (0 for a count), the function, and the filter.
    summary: tuple[int | None, int | None, str | None, str | None] | None = None

    @classmethod
    def of(cls, item: Field) -> "_Field":
        lookup = summary = None
        if item.mode == "lookup":
            lookup = (item.lookup_reference_field_id, item.lookup_target_field_id)
        if item.mode == "summary":
            summary = (
                item.summary_reference_field_id,
                item.summary_target_field_id or 0,
                item.summary_function,
                item.summary_query,
            )
        return cls(
            id=item.id,
            label=item.label,
            type=item.type,
            mode=item.mode,
            formula=item.formula,
            required=item.required,
            unique=item.unique,
            choices=item.choices,
            help_text=item.help_text,
            foreign_key=item.is_foreign_key,
            lookup=lookup,
            summary=summary,
        )

    def state(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "mode": self.mode,
            "formula": self.formula,
            "required": self.required,
            "unique": self.unique,
            "choices": sorted(self.choices),
        }


@dataclass
class _Link:
    """A relationship, from the child's side. One the plan makes has no ID yet."""

    id: int | None
    parent: "_Table"
    foreign_key: _Field


@dataclass
class _Table:
    """A table as a plan sees it. A table the plan makes has no `info` or `handle`."""

    name: str
    info: TableInfo | None
    handle: "AsyncTable | None"
    fields: list[_Field] = field(default_factory=list[_Field])
    links: list[_Link] = field(default_factory=list[_Link])
    loaded: bool = False
    dropped: bool = False
    touched: set[int] = field(default_factory=set[int])

    @property
    def id(self) -> str | None:
        return None if self.info is None else self.info.id

    def field(self, ref: FieldRefValue) -> _Field | None:
        if isinstance(ref, int):
            return next((item for item in self.fields if item.id == ref), None)
        wanted = ref.casefold()
        return next((item for item in self.fields if item.label.casefold() == wanted), None)

    def link(self, foreign_key: str) -> _Link | None:
        wanted = foreign_key.casefold()
        return next(
            (item for item in self.links if item.foreign_key.label.casefold() == wanted), None
        )


class _Blocked(Exception):
    """A change that can't be planned, with the reason."""


class AsyncPlanner:
    def __init__(self, app: "AsyncApp") -> None:
        self._app = app
        self._transport = app._transport  # pyright: ignore[reportPrivateUsage]
        self._tables: list[_Table] = []
        self._names_used = False

    async def plan(self, changes: Sequence[SchemaChange]) -> SchemaPlan:
        for info in await self._app.tables(refresh=True):
            self._tables.append(_Table(info.name, info, self._app.table(info.id)))
        planned = [await self._plan_one(change) for change in changes]
        return SchemaPlan(
            app_id=self._app.id, changes=tuple(planned), fingerprint=fingerprint(self._state())
        )

    def _state(self) -> dict[str, Any]:
        """What the plan depends on: the tables and fields it touched, and the names in use."""
        found: dict[str, Any] = {}
        if self._names_used:
            found["tables"] = sorted(item.info.name.casefold() for item in self._known())  # pyright: ignore[reportOptionalMemberAccess]
        for table in self._known():
            if not table.loaded:
                continue
            assert table.info is not None
            found[table.info.id] = {
                "name": table.info.name,
                "description": table.info.description,
                "labels": sorted(item.label.casefold() for item in table.fields if item.id),
                "fields": [item.state() for item in table.fields if item.id in table.touched],
                "links": sorted(
                    (item.id, item.parent.id, item.foreign_key.id)  # pyright: ignore[reportArgumentType]
                    for item in table.links
                    if item.id is not None
                ),
            }
        return found

    def _known(self) -> list[_Table]:
        """The tables that existed when planning began, even if the plan drops one."""
        return [item for item in self._tables if item.info is not None]

    # Finding tables and fields

    def _find(self, ref: str) -> _Table | None:
        live = [item for item in self._tables if not item.dropped]
        found = next((item for item in live if item.id == ref), None)
        wanted = _table_key(ref)
        return found or next((item for item in live if _table_key(item.name) == wanted), None)

    async def _table(self, ref: str) -> _Table:
        table = self._find(ref)
        if table is None:
            names = ", ".join(sorted(item.name for item in self._tables if not item.dropped))
            raise _Blocked(f"There's no table {ref!r}. The tables are: {names}.")
        await self._load(table)
        return table

    async def _load(self, table: _Table) -> None:
        if table.loaded or table.handle is None:
            table.loaded = True
            return
        table.fields = [_Field.of(item) for item in await table.handle.fields(refresh=True)]
        table.loaded = True
        for relationship in await table.handle.relationships(refresh=True):
            parent = self._find(relationship.parent_table_id)
            key = table.field(relationship.foreign_key_field_id or 0)
            if parent is not None and key is not None:
                table.links.append(_Link(relationship.id, parent, key))

    def _field(self, table: _Table, ref: FieldRefValue) -> _Field:
        found = table.field(ref)
        if found is None:
            labels = ", ".join(item.label for item in table.fields)
            raise _Blocked(f"{table.name} has no field {ref!r}. Its fields are: {labels}.")
        if found.id is not None:
            table.touched.add(found.id)
        return found

    # Planning each kind of change

    async def _plan_one(self, change: SchemaChange) -> PlannedChange:
        plans: dict[type[Any], Callable[[Any], Any]] = {
            CreateTable: self._create_table,
            UpdateTable: self._update_table,
            DeleteTable: self._delete_table,
            CreateField: self._create_field,
            UpdateField: self._update_field,
            DeleteField: self._delete_field,
            CreateRelationship: self._create_relationship,
            CreateLookupField: self._create_lookup,
            CreateSummaryField: self._create_summary,
            DeleteRelationship: self._delete_relationship,
        }
        try:
            return await plans[type(change)](change)
        except _Blocked as blocked:
            return self._result(change, "blocked", str(blocked), blockers=(str(blocked),))

    def _result(
        self,
        change: SchemaChange,
        status: Status,
        summary: str,
        *,
        settings: dict[str, SettingChange] | None = None,
        warnings: Sequence[str] = (),
        blockers: Sequence[str] = (),
        affected: int | None = None,
        link: str | None = None,
    ) -> PlannedChange:
        if blockers:
            status = "blocked"
        return PlannedChange(
            change=change,
            status=status,
            risk=change_risk(change),
            summary=summary,
            settings=settings or {},
            warnings=tuple(warnings),
            blockers=tuple(blockers),
            affected_records=affected,
            link=link,
        )

    def _allowed(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        parent: _Table | None = None,
    ) -> list[str]:
        """Why the client's limits would refuse this request, if they would."""
        try:
            self._transport.check_write(
                method,
                path,
                params=params,
                json=json,
                parent_table_id=None if parent is None else parent.id,
            )
        except RequestRefusedError as refused:
            message = self._named(str(refused).replace(" It wasn't sent.", ""))
            if parent is not None and parent.id is not None and parent.id in str(refused):
                message = (
                    f"This needs {parent.name} to allow linking and changing its fields too, and "
                    f"this client's write rules don't. {message}"
                )
            return [message]
        return []

    def _named(self, message: str) -> str:
        """A message with each table's ID replaced by its name, which is what a person knows."""
        for table in self._known():
            assert table.info is not None
            message = message.replace(f"table {table.info.id}", f"table {table.info.name}")
        return message

    def _sources(self, check: Callable[[], None]) -> list[str]:
        """Why a derived field would be refused for reading what the client can't, if it would."""
        try:
            check()
        except RequestRefusedError as refused:
            return [str(refused).replace(" Nothing was sent.", "")]
        return []

    def _access(self, table: _Table) -> Any:
        if table.id is None:
            return None  # Checked when applied, against the client's access for new tables.
        return table_access(self._transport.read_rules, table.id)

    def _formula_blockers(self, table: _Table, formula: str, *, own: _Field | None) -> list[str]:
        """Why a formula would be refused for reading what the client can't, if it would. It may
        use fields an earlier change makes, which are readable only if every field of the table
        is, as a new field isn't on a list of fields."""
        access = self._access(table)
        if access is None:
            return []
        every = all(item.fields is True for item in access)
        sources = [
            Source(
                item.label,
                item.type,
                item.id,
                every if item.id is None else readable(_as_field(item), access),
            )
            for item in table.fields
            if item is not own
        ]
        return self._sources(
            lambda: check_formula_against(formula, sources, emails_shown=emails_shown(access))
        )

    async def _count(self, table: _Table, where: Any) -> int | None:
        """How many records match, or None if the client can't count them."""
        if table.handle is None:
            return 0
        try:
            return await table.handle.count(where)
        except QuickbaseError:
            return None

    async def _link(self, table: _Table) -> str | None:
        if table.handle is None:
            return None
        try:
            return await table.handle.fields_url()
        except QuickbaseError:
            return None

    async def _create_table(self, change: CreateTable) -> PlannedChange:
        self._names_used = True
        existing = self._find(change.name)
        if existing is not None:
            matched = "" if existing.name == change.name else ", ignoring case and spaces,"
            return self._result(
                change,
                "no_change",
                f"Table {existing.name} already exists{matched} so it's kept as it is.",
                warnings=[
                    f"Later changes to {change.name} change the existing {existing.name}. If you "
                    "meant a new table, choose a name no table has."
                ],
            )
        settings = _settings(
            name=(None, change.name),
            description=(None, change.description),
            single_record_name=(None, change.single_record_name),
            plural_record_name=(None, change.plural_record_name),
        )
        blockers = self._allowed("POST", "/tables", params={"appId": self._app.id})
        table = _Table(change.name, None, None, loaded=True)
        table.fields = [_Field(RECORD_ID_FIELD, "Record ID#", "recordid")]
        self._tables.append(table)
        warnings: list[str] = []
        if self._transport.limited:
            warnings.append(
                f"Changes to {change.name} are checked against this client's limits for new "
                "tables when they're applied."
            )
        return self._result(
            change,
            "change",
            f"Create table {change.name}.",
            settings=settings,
            blockers=blockers,
            warnings=warnings,
        )

    async def _update_table(self, change: UpdateTable) -> PlannedChange:
        table = await self._table(change.table)

        info = table.info
        raw: Any = {} if info is None else info.raw
        settings = _settings(
            name=(table.name, change.name),
            description=(None if info is None else info.description, change.description),
            single_record_name=(raw.get("singleRecordName"), change.single_record_name),
            plural_record_name=(raw.get("pluralRecordName"), change.plural_record_name),
        )
        if not settings:
            return self._result(change, "no_change", f"Table {table.name} already has these.")
        blockers: list[str] = []
        if change.name is not None:
            self._names_used = True
            other = self._find(change.name)
            if other is not None and other is not table:
                blockers.append(f"Another table is already named {other.name}.")
        if table.id is not None:
            blockers += self._allowed(
                "POST", f"/tables/{table.id}", params={"appId": self._app.id}, json={}
            )
        summary = f"Change table {table.name}."
        if change.name is not None:
            table.name = change.name
        return self._result(
            change, "change", summary, settings=settings, blockers=blockers,
            link=await self._link(table),
        )  # fmt: skip

    async def _delete_table(self, change: DeleteTable) -> PlannedChange:
        table = await self._table(change.table)
        records = await self._count(table, None)
        if records is None:
            warnings = ["How many records it has wasn't checked."]
        else:
            held = "1 record is" if records == 1 else f"{records} records are"
            warnings = [f"Its {held} deleted with it."]
        parents = sorted({item.parent.name for item in table.links})
        children = sorted(
            {item.name for item in self._tables if any(link.parent is table for link in item.links)}
        )
        if parents:
            warnings.append(f"It's a child of {', '.join(parents)}, whose summaries of it go too.")
        if children:
            warnings.append(
                f"It's the parent of {', '.join(children)}, whose lookups of it go too."
            )
        blockers = []
        if table.id is not None:
            blockers = self._allowed(
                "DELETE", f"/tables/{table.id}", params={"appId": self._app.id}
            )
        table.dropped = True
        return self._result(
            change, "change", f"Delete table {table.name} and all its records.",
            warnings=warnings, blockers=blockers, affected=records,
        )  # fmt: skip

    async def _create_field(self, change: CreateField) -> PlannedChange:
        table = await self._table(change.table)
        kind = "formula field" if change.formula is not None else "field"
        _check_choices(change.type, change.choices)
        existing = table.field(change.label)
        if existing is not None:
            if existing.id is not None:
                table.touched.add(existing.id)
            same = existing.type == change.type or {existing.type, change.type} == {
                "datetime", "timestamp"
            }  # fmt: skip
            if same and (existing.formula or None) == (change.formula or None):
                matched = "" if existing.label == change.label else ", which matches ignoring case"
                return self._result(
                    change,
                    "no_change",
                    f'{table.name} already has {kind} "{existing.label}"{matched}.',
                )
            raise _Blocked(
                f'{table.name} already has a field labeled "{existing.label}" ({existing.type}), '
                "so it can't be added. Choose another label, or change that field."
            )
        settings = _settings(
            label=(None, change.label),
            type=(None, change.type),
            formula=(None, change.formula),
            choices=(None, None if change.choices is None else list(change.choices)),
            required=(None, change.required),
            unique=(None, change.unique),
            help_text=(None, change.help_text),
        )
        blockers: list[str] = []
        warnings = _lookalikes(table, change.label)
        if change.required and (await self._count(table, None) or 0) > 0:
            blockers.append(
                f"{table.name} has records, which would all be missing a value, so the field "
                "can't be required. Add it, fill it in, then make it required."
            )
        if change.formula is not None:
            blockers += self._formula_blockers(table, change.formula, own=None)
            warnings.append(_FORMULA_TYPE_WARNING.format(type=change.type))
        if change.type in _CHOICE_TYPES and not change.choices:
            warnings.append("It has no choices yet, so add them before anyone fills it in.")
        if self._hides_fields(table):
            warnings.append(
                f"This client can't see every field of {table.name}, so if one it can't see has "
                "this label, Quickbase refuses the field when it's applied."
            )
        if table.id is not None:
            body: dict[str, Any] = {"label": change.label, "fieldType": change.type}
            if change.formula is not None:
                body["properties"] = {"formula": change.formula}
            params = {"tableId": table.id}
            blockers += self._allowed("POST", "/fields", params=params, json=body)
            if change.required is not None or change.unique is not None:
                # Quickbase sets these in a second request, to the new field.
                blockers += self._allowed(
                    "POST", f"/fields/{_NEW_FIELD_ID}", params=params, json={"label": ""}
                )
        table.fields.append(
            _Field(
                None,
                change.label,
                change.type,
                mode="formula" if change.formula is not None else None,
                formula=change.formula,
                required=bool(change.required),
                unique=bool(change.unique),
                choices=change.choices or (),
            )
        )
        return self._result(
            change, "change", f'Add {kind} "{change.label}" ({change.type}) to {table.name}.',
            settings=settings, blockers=blockers, warnings=warnings, link=await self._link(table),
        )  # fmt: skip

    def _hides_fields(self, table: _Table) -> bool:
        """Whether the client's read rules may hide some of a table's fields."""
        access = self._access(table)
        return access is not None and not all(item.fields is True for item in access)

    async def _update_field(self, change: UpdateField) -> PlannedChange:
        table = await self._table(change.table)
        target = self._field(table, change.field)
        if change.choices is not None:
            _check_choices(target.type, change.choices)
        if target.id in _BUILT_IN_FIELDS:
            raise _Blocked(f'"{target.label}" is built into every table, so it can\'t be changed.')
        if change.formula is not None and target.mode != "formula":
            raise _Blocked(
                f"\"{target.label}\" isn't a formula field, and a field's type can't change. Add "
                "a new formula field instead."
            )
        settings = _settings(
            label=(target.label, change.label),
            formula=(target.formula, change.formula),
            choices=(
                list(target.choices),
                None if change.choices is None else list(change.choices),
            ),
            required=(target.required, change.required),
            unique=(target.unique, change.unique),
            help_text=(target.help_text, change.help_text),
        )
        name = f'"{target.label}" on {table.name}'
        if not settings:
            return self._result(change, "no_change", f"{name} already has these settings.")
        blockers: list[str] = []
        warnings: list[str] = []
        if change.label is not None:
            other = table.field(change.label)
            if other is not None and other is not target:
                blockers.append(f'{table.name} already has a field labeled "{other.label}".')
            warnings += _lookalikes(table, change.label, own=target)
            if target.mode == "formula" or any(
                item.formula and target.label.casefold() in _uses(item.formula)
                for item in table.fields
            ):
                warnings.append("Quickbase updates the formulas that use it to the new label.")
        if "formula" in settings:
            warnings.append("Every record's value is recalculated with the new formula.")
            warnings.append(_FORMULA_TYPE_WARNING.format(type=target.type))
            blockers += self._formula_blockers(table, change.formula or "", own=target)
        if change.required and not target.required and target.id is not None:
            empty = await self._count(table, _is_empty(target.id))
            if empty is None:
                warnings.append("Whether every record has a value wasn't checked.")
            elif empty:
                blockers.append(
                    f"{_records(empty)} no value in it, so it can't be required yet. Fill them in "
                    "first."
                )
        if change.unique and not target.unique and target.id is not None:
            blockers += await self._duplicates(table, target, warnings)
        if change.choices is not None and target.choices:
            removed = sorted(set(target.choices) - set(change.choices))
            if removed:
                warnings.append(await self._removed_choices(table, target, removed))
        if table.id is not None and target.id is not None:
            body: dict[str, Any] = {"label": change.label or target.label}
            if change.formula is not None:
                body["properties"] = {"formula": change.formula}
            blockers += self._allowed(
                "POST", f"/fields/{target.id}", params={"tableId": table.id}, json=body
            )
        if change.label is not None:
            target.label = change.label
        if change.formula is not None:
            target.formula = change.formula
        return self._result(
            change, "change", f"Change {name}.", settings=settings, warnings=warnings,
            blockers=blockers, link=await self._link(table),
        )  # fmt: skip

    async def _duplicates(self, table: _Table, target: _Field, warnings: list[str]) -> list[str]:
        """Refuse making a field unique when it already holds the same value twice."""
        if table.handle is None or target.id is None:
            return []
        total = await self._count(table, None)
        if total is None or total > UNIQUE_CHECK_LIMIT:
            warnings.append(
                "Whether every value is different wasn't checked. Quickbase refuses the change if "
                "not."
            )
            return []
        try:
            rows = await table.handle.summarize(
                [target.id],
                where=_has_value(target.id),
                max_records=UNIQUE_CHECK_LIMIT,
            )
        except QuickbaseError:
            warnings.append("Whether every value is different wasn't checked.")
            return []
        repeated = [row for row in rows if row.count > 1]
        if repeated:
            records = sum(row.count for row in repeated)
            values = "1 value is" if len(repeated) == 1 else f"{len(repeated)} values are"
            return [
                f"{values} each held by more than one record, {records} records in all, so it "
                "can't be unique yet."
            ]
        return []

    async def _removed_choices(self, table: _Table, target: _Field, removed: list[str]) -> str:
        """Which records hold the choices a change takes away, as Quickbase keeps their values."""
        held: list[str] = []
        for choice in removed:
            count = None if target.id is None else await self._count(table, F(target.id) == choice)
            held.append(
                repr(choice) if count is None else f"{choice!r} ({_records(count, 'hold')})"
            )
        return (
            f"These choices are taken away, but records that hold one keep it: {', '.join(held)}."
        )

    async def _delete_field(self, change: DeleteField) -> PlannedChange:
        table = await self._table(change.table)
        target = self._field(table, change.field)
        name = f'"{target.label}" from {table.name}'
        if target.id in _BUILT_IN_FIELDS:
            raise _Blocked(f'"{target.label}" is built into every table, so it can\'t be deleted.')
        if table.info is not None and target.id == table.info.key_field_id:
            raise _Blocked(f"\"{target.label}\" is the table's key field, so it can't be deleted.")
        warnings: list[str] = []
        records: int | None = None
        if target.mode in _DERIVED_MODES:
            warnings.append(f"It's a {target.mode} field, so no stored values are lost.")
        elif target.id is not None:
            records = await self._count(table, F(target.id) != None)  # noqa: E711
            warnings.append(_records_lost(records, "a value in it"))
        for link in table.links:
            if link.foreign_key is target:
                warnings.append(
                    f"It links {table.name} to {link.parent.name}. Delete that relationship first, "
                    "or its lookups and summaries stop working."
                )
        warnings += await self._dependents(table, target)
        blockers: list[str] = []
        if table.id is not None and target.id is not None:
            blockers = self._allowed(
                "DELETE", "/fields", params={"tableId": table.id}, json={"fieldIds": [target.id]}
            )
        table.fields.remove(target)
        return self._result(
            change, "change", f"Delete field {name}.", warnings=warnings, blockers=blockers,
            affected=records, link=await self._link(table),
        )  # fmt: skip

    async def _dependents(self, table: _Table, target: _Field) -> list[str]:
        """The fields that use one about to be deleted, which stop working without it, and the
        fields that use those formulas in turn."""
        found: list[str] = []
        formulas = [
            item for item in table.fields if item is not target and _formula_uses(item, target)
        ]
        if formulas:
            found.append(
                "Formulas that use it keep their text but become empty: "
                f"{_names(item.label for item in formulas)}."
            )
        for used in (target, *formulas):
            if used.id is None:
                continue
            via = "" if used is target else f', through "{used.label}"'
            for other, kind, labels in await self._reached_from(table, used):
                found.append(f"{kind} on {other.name} stop working{via}: {_names(labels)}.")
        return found

    async def _reached_from(
        self, table: _Table, field: _Field
    ) -> list[tuple[_Table, str, list[str]]]:
        """The lookups of a field on its children, and the summaries of it on its parents."""
        found: list[tuple[_Table, str, list[str]]] = []
        for other in self._tables:
            if other.dropped or other.handle is None:
                continue
            await self._load(other)
            lookups = [
                item.label
                for link in other.links
                if link.parent is table
                for item in other.fields
                if item.lookup == (link.foreign_key.id, field.id)
            ]
            if lookups:
                found.append((other, "Lookups", lookups))
            summaries = [
                item.label
                for link in table.links
                if link.parent is other
                for item in other.fields
                if item.summary is not None
                and item.summary[0] == link.foreign_key.id
                and (item.summary[1] == field.id or _filters_on(item.summary[3], field.id))
            ]
            if summaries:
                found.append((other, "Summaries", summaries))
        return found

    async def _create_relationship(self, change: CreateRelationship) -> PlannedChange:
        child = await self._table(change.child)
        parent = await self._table(change.parent)
        existing = child.link(change.foreign_key)
        if existing is not None and existing.parent is parent:
            return self._result(
                change, "no_change",
                f'{child.name} already links to {parent.name} by "{existing.foreign_key.label}".',
            )  # fmt: skip
        if child.field(change.foreign_key) is not None:
            raise _Blocked(
                f'{child.name} already has a field labeled "{change.foreign_key}", so it can\'t be '
                "the new link's foreign key."
            )
        blockers: list[str] = []
        if child.id is not None:
            body = {"parentTableId": parent.id, "foreignKeyField": {"label": change.foreign_key}}
            blockers = self._allowed("POST", f"/tables/{child.id}/relationship", json=body)
        if parent.id is None:
            blockers.append(f"{parent.name} must exist before a table can link to it.")
        key = _Field(None, change.foreign_key, "numeric", foreign_key=True)
        child.fields.append(key)
        child.links.append(_Link(None, parent, key))
        return self._result(
            change, "change",
            f'Link {child.name} to {parent.name} through a new field "{change.foreign_key}" on '
            f"{child.name}.",
            settings=_settings(parent=(None, parent.name), foreign_key=(None, change.foreign_key)),
            warnings=[
                *_lookalikes(child, change.foreign_key),
                f"{parent.name} also gets fields to list and add {child.name} records.",
            ],
            blockers=blockers, link=await self._link(child),
        )  # fmt: skip

    async def _relationship(self, child_ref: str, foreign_key: str) -> tuple[_Table, _Link]:
        child = await self._table(child_ref)
        link = child.link(foreign_key)
        if link is None:
            keys = ", ".join(f'"{item.foreign_key.label}"' for item in child.links) or "none"
            raise _Blocked(
                f'{child.name} has no relationship with the foreign key "{foreign_key}". Its '
                f"relationships' foreign keys are: {keys}."
            )
        if link.foreign_key.id is not None:
            child.touched.add(link.foreign_key.id)
        await self._load(link.parent)
        return child, link

    async def _create_lookup(self, change: CreateLookupField) -> PlannedChange:
        child, link = await self._relationship(change.child, change.foreign_key)
        parent = link.parent
        target = self._field(parent, change.parent_field)
        existing = next(
            (
                item
                for item in child.fields
                if item.lookup is not None
                and item.lookup == (link.foreign_key.id, target.id)
                and target.id is not None
            ),
            None,
        )
        if existing is not None and (change.label is None or existing.label == change.label):
            return self._result(
                change, "no_change", f'{child.name} already shows "{target.label}" as '
                f'"{existing.label}".',
            )  # fmt: skip
        label = change.label or f"{link.foreign_key.label} - {target.label}"
        if existing is None and child.field(label) is not None:
            raise _Blocked(f'{child.name} already has a field labeled "{label}".')
        blockers: list[str] = []
        if parent.id is not None and target.id is not None and parent.handle is not None:
            parent_fields = await parent.handle.fields()
            access = table_access(self._transport.read_rules, parent.id)
            found = FieldIndex(parent_fields)

            def readable() -> None:
                check_readable(found.resolve(target.id or 0), access, parent.id or "", "A lookup")

            blockers += self._sources(readable)
        if child.id is not None and link.id is not None:
            blockers += self._allowed(
                "POST", f"/tables/{child.id}/relationship/{link.id}",
                json={"lookupFieldIds": [target.id or 0]}, parent=parent,
            )  # fmt: skip
        child.fields.append(
            _Field(None, label, target.type, mode="lookup", lookup=(link.foreign_key.id, target.id))
        )
        return self._result(
            change, "change",
            f'Show "{target.label}" from {parent.name} on each {child.name} record, as "{label}".',
            settings=_settings(label=(None, label), parent_field=(None, target.label)),
            warnings=_lookalikes(child, label), blockers=blockers, link=await self._link(child),
        )  # fmt: skip

    async def _create_summary(self, change: CreateSummaryField) -> PlannedChange:
        child, link = await self._relationship(change.child, change.foreign_key)
        parent = link.parent
        counting = change.function == "COUNT"
        if counting and change.child_field is not None:
            raise _Blocked("A COUNT counts the child records, so it takes no child_field.")
        if not counting and change.child_field is None:
            raise _Blocked(f"A {change.function} needs the child_field to combine.")
        target = None if counting else self._field(child, change.child_field or "")
        blockers: list[str] = []
        clause = change.where.strip() if change.where is not None else None
        fields: FieldIndex | None = None
        if child.id is not None and child.handle is not None:
            fields = FieldIndex(await child.handle.fields())
            if clause is not None:
                try:
                    clause = compile_where(clause, fields)
                except (QueryError, UnknownFieldError) as error:
                    raise _Blocked(f"The summary's filter can't be used: {error}") from None
        wanted = (link.foreign_key.id, 0 if target is None else target.id, change.function, clause)
        existing = parent.field(change.label)
        if existing is not None:
            hidden_filter = bool(self._transport.read_rules)
            same = existing.summary is not None and (
                existing.summary[:3] == wanted[:3] if hidden_filter else existing.summary == wanted
            )
            if same and existing.id is not None:
                return self._result(
                    change, "no_change", f'{parent.name} already has summary "{existing.label}".',
                    warnings=(
                        ["This client can't read a summary's filter, so it may filter differently."]
                        if hidden_filter and clause is not None
                        else []
                    ),
                )  # fmt: skip
            raise _Blocked(f'{parent.name} already has a field labeled "{existing.label}".')
        if fields is not None:
            access = table_access(self._transport.read_rules, child.id or "")
            shown = [link.foreign_key.id] + ([] if target is None else [target.id])

            def readable() -> None:
                for field_id in shown:
                    if field_id is not None:
                        check_readable(
                            fields.resolve(field_id), access, child.id or "", "A summary"
                        )

            blockers += self._sources(readable)
            if clause is not None:
                found = clause
                blockers += self._sources(
                    lambda: check_filterable(found, access, child.id or "", "The summary")
                )
            if link.id is not None:
                blockers += self._allowed(
                    "POST", f"/tables/{child.id}/relationship/{link.id}",
                    json={"summaryFields": [{}]}, parent=parent,
                )  # fmt: skip
        parent.fields.append(_Field(None, change.label, "numeric", mode="summary", summary=wanted))
        what = (
            "the number of" if target is None else f'the {change.function} of "{target.label}" on'
        )
        matching = "" if clause is None else f" that match {clause}"
        return self._result(
            change, "change",
            f'Add "{change.label}" to {parent.name}: {what} its {child.name} records{matching}.',
            settings=_settings(
                label=(None, change.label), function=(None, change.function),
                child_field=(None, None if target is None else target.label),
                where=(None, clause),
            ),
            warnings=_lookalikes(parent, change.label, own=parent.fields[-1]),
            blockers=blockers, link=await self._link(parent),
        )  # fmt: skip

    async def _delete_relationship(self, change: DeleteRelationship) -> PlannedChange:
        child, link = await self._relationship(change.child, change.foreign_key)
        parent = link.parent
        key = link.foreign_key.id
        lookups = [
            item for item in child.fields if item.lookup is not None and item.lookup[0] == key
        ]
        summaries = [
            item for item in parent.fields if item.summary is not None and item.summary[0] == key
        ]
        warnings = [
            f'"{link.foreign_key.label}" stays on {child.name}, as a plain number, with its '
            "values.",
            f"{parent.name} loses the fields that list and add {child.name} records.",
        ]
        if lookups:
            warnings.append(
                f"These lookups on {child.name} are deleted: "
                f"{_names(item.label for item in lookups)}."
            )
        if summaries:
            warnings.append(
                f"These summaries on {parent.name} are deleted: "
                f"{_names(item.label for item in summaries)}."
            )
        blockers: list[str] = []
        if child.id is not None and link.id is not None:
            blockers = self._allowed(
                "DELETE", f"/tables/{child.id}/relationship/{link.id}", parent=parent
            )
        for item in lookups:
            child.fields.remove(item)
        for item in summaries:
            parent.fields.remove(item)
        link.foreign_key.foreign_key = False
        child.links.remove(link)
        return self._result(
            change, "change", f"Unlink {child.name} from {parent.name}.", warnings=warnings,
            blockers=blockers, link=await self._link(child),
        )  # fmt: skip


def _is_empty(field_id: int) -> Any:
    return F(field_id) == None  # noqa: E711


def _has_value(field_id: int) -> Any:
    return F(field_id) != None  # noqa: E711


_CHOICE_TYPES = frozenset({"text-multiple-choice", "multitext"})
_FORMULA_TYPE_WARNING = (
    "Quickbase only checks that the formula's result is a {type} when it's applied. If it isn't, "
    "this change fails, and the changes before it in the plan stay made."
)


def _table_key(name: str) -> str:
    """A table's name as Quickbase compares it, ignoring case and surrounding spaces."""
    return name.strip().casefold()


def _label_shape(label: str) -> str:
    """A label as a person reads it, ignoring case and how many spaces there are."""
    return " ".join(label.split()).casefold()


def _lookalikes(table: _Table, label: str, *, own: _Field | None = None) -> list[str]:
    """A warning for each field whose label reads the same as this one, but which Quickbase
    counts as another field, as it keeps a label's spaces exactly."""
    shape = _label_shape(label)
    return [
        f'"{label}" looks like "{item.label}", which {table.name} already has. Quickbase keeps '
        "spaces in a label, so they'd be two fields that look the same."
        for item in table.fields
        if item is not own
        and item.label.casefold() != label.casefold()
        and _label_shape(item.label) == shape
    ]


def _check_choices(field_type: str, choices: Sequence[str] | None) -> None:
    if choices is not None and field_type not in _CHOICE_TYPES:
        raise _Blocked(
            f"Only a text-multiple-choice or multitext field has choices, not a {field_type} field."
        )


def _as_field(item: _Field) -> Field:
    """Enough of a `Field` to check what the client may read of it."""
    return Field(
        id=item.id or 0, label=item.label, type=item.type, mode=item.mode, required=False,
        unique=False, appears_by_default=False, choices=(), allow_new_choices=False, raw={},
    )  # fmt: skip


def _formula_uses(item: _Field, target: _Field) -> bool:
    """Whether a field's formula uses `target`, by label or by `_FID_n`."""
    if not item.formula:
        return False
    for reference in _uses(item.formula):
        if reference == target.label.casefold() or (
            target.id is not None and field_id_reference(reference) == target.id
        ):
            return True
    return False


def _records(count: int, verb: str = "have") -> str:
    """`1 record has` or `3 records have`, for a count of records."""
    singular = {"have": "has", "hold": "holds", "lose": "loses"}[verb]
    return f"1 record {singular}" if count == 1 else f"{count} records {verb}"


def _settings(**pairs: tuple[Any, Any]) -> dict[str, SettingChange]:
    """The settings a change would set, leaving out those it leaves alone or already has."""
    return {
        name: SettingChange(before, after)
        for name, (before, after) in pairs.items()
        if after is not None and before != after
    }


def _records_lost(count: int | None, what: str) -> str:
    if count is None:
        return f"How many records hold {what} wasn't checked."
    return f"{_records(count, 'lose')} {what}."


def _filters_on(query: str | None, field_id: int | None) -> bool:
    """Whether a summary's filter uses a field, counting one that can't be read as using it."""
    if not query or field_id is None:
        return False
    used = where_field_ids(query)
    return used is None or field_id in used


def _names(labels: Any) -> str:
    return ", ".join(f'"{label}"' for label in labels)


def _uses(formula: str) -> set[str]:
    """The labels a formula uses, in lowercase, or none if it can't be read."""
    try:
        return {label.casefold() for label in formula_parts(formula).fields}
    except QuickbaseError:
        return set()


# Applying a plan


async def apply_plan(app: "AsyncApp", plan: SchemaPlan) -> SchemaChangeResult:
    if plan.app_id != app.id:
        raise InvalidValueError(f"This plan is for app {plan.app_id}, not {app.id}.")
    fresh = await AsyncPlanner(app).plan([item.change for item in plan.changes])
    if fresh.fingerprint != plan.fingerprint:
        raise SchemaMismatchError(
            "The app's schema has changed since this plan was made, so it wasn't applied. Plan "
            "the changes again, and check the new plan."
        )
    return await apply_checked(app, fresh)


async def apply_checked(app: "AsyncApp", fresh: SchemaPlan) -> SchemaChangeResult:
    """Apply a plan made just now, unless it has blockers."""
    if fresh.blockers:
        raise InvalidValueError(
            f"This plan can't be applied, so nothing was changed: {' '.join(fresh.blockers)}"
        )
    applied: list[PlannedChange] = []
    unchanged = [item for item in fresh.changes if item.status == "no_change"]
    pending = [item for item in fresh.changes if item.status == "change"]
    for position, item in enumerate(pending):
        try:
            await _apply(app, item.change)
        except QuickbaseError as error:
            result = SchemaChangeResult(
                applied=tuple(applied),
                unchanged=tuple(unchanged),
                failed=item,
                not_attempted=tuple(pending[position + 1 :]),
                error=str(error),
            )
            done = f"{len(applied)} changes before it were made, " if applied else ""
            failure = SchemaChangeError(
                f"{item.summary} failed: {error} {done}{len(result.not_attempted)} after it "
                "weren't attempted. Plan the same changes again to see what's left.",
                result=result,
            )
            failure._changed = bool(applied) or error._changed  # pyright: ignore[reportPrivateUsage]
            raise failure from error
        applied.append(item)
    return SchemaChangeResult(applied=tuple(applied), unchanged=tuple(unchanged))


async def _apply(app: "AsyncApp", change: SchemaChange) -> None:
    if isinstance(change, CreateTable):
        await app.create_table(
            change.name,
            description=change.description,
            single_record_name=change.single_record_name,
            plural_record_name=change.plural_record_name,
        )
    elif isinstance(change, UpdateTable):
        await app.table(change.table).update_info(
            name=change.name,
            description=change.description,
            single_record_name=change.single_record_name,
            plural_record_name=change.plural_record_name,
        )
    elif isinstance(change, DeleteTable):
        table = app.table(change.table)
        await table.drop(confirm=(await table.info()).name)
    elif isinstance(change, CreateField):
        await app.table(change.table).create_field(
            change.label,
            change.type,
            choices=change.choices,
            required=change.required,
            unique=change.unique,
            help_text=change.help_text,
            formula=change.formula,
        )
    elif isinstance(change, UpdateField):
        await app.table(change.table).update_field(
            change.field,
            label=change.label,
            required=change.required,
            unique=change.unique,
            help_text=change.help_text,
            formula=change.formula,
            properties=None if change.choices is None else {"choices": list(change.choices)},
        )
    elif isinstance(change, DeleteField):
        await app.table(change.table).delete_fields(change.field)
    elif isinstance(change, CreateRelationship):
        await app.table(change.child).create_relationship(
            app.table(change.parent), foreign_key_label=change.foreign_key
        )
    else:
        child = app.table(change.child)
        relationship = await _find_relationship(child, change.foreign_key)
        if isinstance(change, CreateLookupField):
            await child.ensure_lookup_field(relationship, change.parent_field, label=change.label)
        elif isinstance(change, CreateSummaryField):
            if change.function == "COUNT":
                await child.ensure_count_field(relationship, change.label, where=change.where)
            else:
                await child.ensure_summary_field(
                    relationship,
                    change.child_field or "",
                    change.label,
                    function=change.function,
                    where=change.where,
                )
        else:
            await child.delete_relationship(relationship)


async def _find_relationship(child: "AsyncTable", foreign_key: str) -> Relationship:
    labels = {item.id: item.label.casefold() for item in await child.fields(refresh=True)}
    for relationship in await child.relationships(refresh=True):
        if labels.get(relationship.foreign_key_field_id or 0) == foreign_key.casefold():
            return relationship
    raise UnknownFieldError(f"{(await child.info()).name} has no relationship by {foreign_key!r}.")
