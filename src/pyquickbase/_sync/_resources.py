# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Apps and tables: labels instead of IDs, cached schema, and automatic paging."""

import difflib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from functools import partial
from typing import Any, Self, TypeVar

from .._models import (
    RECORD_ID_FIELD,
    AppInfo,
    Field,
    FieldIndex,
    FileDownload,
    FormulaResult,
    Page,
    Record,
    Relationship,
    ReportInfo,
    TableInfo,
    find_report,
    parse,
    require_table_report,
    unknown_report_message,
)
from .._query import Expression, FieldRef, build_query_body, compile_where
from .._schema import SchemaCache
from .._writes import (
    RowInput,
    WriteResult,
    build_rows,
    field_properties,
    refuse_field,
    require_field,
    require_file_field,
    require_formula,
    require_id,
    split_batches,
)
from ..errors import (
    InvalidResponseError,
    InvalidValueError,
    QueryError,
    QuickbaseError,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    WriteError,
)
from ._transport import Transport

T = TypeVar("T")
DEFAULT_BATCH_SIZE = 1000


def _require_positive(name: str, value: int) -> None:
    if value < 1:
        raise QueryError(f"{name} must be at least 1.")


class App:
    """A Quickbase app. Create one with `qb.app(app_id)`. This makes no network calls."""

    def __init__(self, transport: Transport, cache: SchemaCache, app_id: str) -> None:
        self._transport = transport
        self._cache = cache
        self._id = app_id

    @property
    def id(self) -> str:
        return self._id

    def info(self) -> AppInfo:
        """Get the app's metadata."""
        key = f"app:{self._id}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = self._transport.request("GET", f"/apps/{self._id}")
        info = parse(AppInfo.from_api, payload, "app")
        self._cache.set(key, info)
        return info

    def tables(self, *, refresh: bool = False) -> list[TableInfo]:
        """List the app's tables, from the cache unless `refresh` is set."""
        key = f"tables:{self._id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/tables", params={"appId": self._id})
        tables = parse(
            lambda rows: tuple(TableInfo.from_api(row) for row in rows), payload, "tables"
        )
        self._cache.set(key, tables)
        return list(tables)

    def create_table(
        self,
        name: str,
        *,
        description: str | None = None,
        single_record_name: str | None = None,
        plural_record_name: str | None = None,
    ) -> "Table":
        """Create a table and return it."""
        optional = {
            "description": description,
            "singleRecordName": single_record_name,
            "pluralRecordName": plural_record_name,
        }
        body = {"name": name, **{k: v for k, v in optional.items() if v is not None}}
        payload = self._transport.request("POST", "/tables", params={"appId": self._id}, json=body)
        self._cache.delete(f"tables:{self._id}")
        return self.table(parse(lambda data: TableInfo.from_api(data).id, payload, "table"))

    def table(self, name_or_id: str) -> "Table":
        """Get a table in this app by name or ID. It's checked when first used."""
        return Table(self._transport, self._cache, self, name_or_id)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._id!r})"


def _find_table(tables: Sequence[TableInfo], ref: str) -> TableInfo | None:
    return next((item for item in tables if item.id == ref), None) or next(
        (item for item in tables if item.name == ref), None
    )


class RecordIterator:
    """Yields records from a query, fetching further pages as needed.

    A class rather than an async generator, so stopping early with `break` needs no cleanup.
    """

    def __init__(
        self,
        load_body: Callable[[], Any],
        fetch_page: Callable[[dict[str, Any], int, int], Any],
        page_size: int,
        limit: int | None,
    ) -> None:
        self._load_body = load_body
        self._fetch_page = fetch_page
        self._page_size = page_size
        self._limit = limit
        self._body: dict[str, Any] | None = None
        self._buffer: Iterator[Record] = iter(())
        self._skip: int = 0
        self._yielded: int = 0
        self._done = False

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Record:
        while True:
            record = next(self._buffer, None)
            if record is not None:
                self._yielded += 1
                return record
            if self._done or self._yielded == self._limit:
                raise StopIteration
            self._load_next_page()

    def _load_next_page(self) -> None:
        body = self._body
        if body is None:
            body = self._body = self._load_body()
        remaining = None if self._limit is None else self._limit - self._yielded
        top = self._page_size if remaining is None else min(self._page_size, remaining)
        page: Page = self._fetch_page(body, top, self._skip)
        if page.has_more and not page.records:
            raise InvalidResponseError(
                f"Quickbase returned an empty page at {self._skip} of {page.total} records."
            )
        self._buffer = iter(page.records)
        self._skip = page.next_skip
        self._done = not page.has_more


class Table:
    """A table in an app. Create one with `app.table("Tasks")`."""

    def __init__(self, transport: Transport, cache: SchemaCache, app: App, ref: str) -> None:
        self._transport = transport
        self._cache = cache
        self._app = app
        self._ref = ref

    def info(self) -> TableInfo:
        """Get the table's metadata. Raises `UnknownTableError` if it isn't in this app."""
        tables = self._app.tables()
        found = _find_table(tables, self._ref)
        if found is None:
            # The table may be newer than the cache, so look once more before giving up.
            tables = self._app.tables(refresh=True)
            found = _find_table(tables, self._ref)
        if found is None:
            names = [item.name for item in tables]
            close = difflib.get_close_matches(self._ref, names, n=3)
            hint = f" Did you mean {' or '.join(repr(name) for name in close)}?" if close else ""
            raise UnknownTableError(f"App {self._app.id} has no table {self._ref!r}.{hint}")
        return found

    def fields(self, *, refresh: bool = False) -> list[Field]:
        """List the table's fields, from the cache unless `refresh` is set."""
        table_id = (self.info()).id
        key = f"fields:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/fields", params={"tableId": table_id})
        fields = parse(lambda rows: tuple(Field.from_api(row) for row in rows), payload, "fields")
        self._cache.set(key, fields)
        return list(fields)

    def relationships(self, *, refresh: bool = False) -> list[Relationship]:
        """List the relationships this table is part of, as either parent or child."""
        table_id = (self.info()).id
        key = f"relationships:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        found: list[Relationship] = []
        while True:
            payload = self._transport.request(
                "GET", f"/tables/{table_id}/relationships", params={"skip": len(found)}
            )
            page, total = parse(self._parse_relationships, payload, "relationships")
            found.extend(page)
            if not page or len(found) >= total:
                break
        self._cache.set(key, tuple(found))
        return found

    @staticmethod
    def _parse_relationships(payload: Any) -> tuple[list[Relationship], int]:
        rows = [Relationship.from_api(row) for row in payload["relationships"]]
        return rows, payload["metadata"]["totalRelationships"]

    def reports(self, *, refresh: bool = False) -> list[ReportInfo]:
        """List the table's reports, from the cache unless `refresh` is set."""
        table_id = (self.info()).id
        key = f"reports:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/reports", params={"tableId": table_id})
        reports = parse(
            lambda rows: tuple(ReportInfo.from_api(row) for row in rows), payload, "reports"
        )
        self._cache.set(key, reports)
        return list(reports)

    def report(self, name_or_id: str) -> "Report":
        """Get a report on this table by name or ID. It's checked when first used."""
        return Report(self._transport, self, name_or_id)

    def _with_fields(self, build: Callable[[list[Field]], T]) -> T:
        try:
            return build(self.fields())
        except UnknownFieldError:
            # The field may be newer than the cache, so look once more before giving up.
            return build(self.fields(refresh=True))

    def _query_body(
        self,
        where: Expression | str | None,
        select: FieldRef | Sequence[FieldRef] | None,
        order_by: FieldRef | Sequence[FieldRef] | None,
    ) -> dict[str, Any]:
        table_id = (self.info()).id
        return self._with_fields(
            lambda fields: build_query_body(
                table_id, fields, where=where, select=select, order_by=order_by
            )
        )

    def _fetch_page(self, body: dict[str, Any], top: int, skip: int) -> Page:
        payload = self._transport.request(
            "POST", "/records/query", json={**body, "options": {"top": top, "skip": skip}}
        )
        return parse(Page.from_api, payload, "records query")

    def query_page(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRef | Sequence[FieldRef] | None = None,
        order_by: FieldRef | Sequence[FieldRef] | None = None,
        top: int = 100,
        skip: int = 0,
    ) -> Page:
        """Fetch one page of records. See `query` for the arguments."""
        _require_positive("top", top)
        if skip < 0:
            raise QueryError("skip can't be negative.")
        body = self._query_body(where, select, order_by)
        return self._fetch_page(body, top, skip)

    def query(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRef | Sequence[FieldRef] | None = None,
        order_by: FieldRef | Sequence[FieldRef] | None = None,
        page_size: int = 1000,
        limit: int | None = None,
    ) -> RecordIterator:
        """Iterate over matching records, fetching further pages as needed.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
            select: Field labels or IDs. Defaults to the table's default fields. Record ID# is
                always included.
            order_by: Field labels or IDs. Prefix a label with `-`, or negate an ID, to sort
                descending. Without this, records come back in Record ID# order.
            page_size: Records to request per call to Quickbase.
            limit: Stop after this many records.
        """
        _require_positive("page_size", page_size)
        if limit is not None:
            _require_positive("limit", limit)
        return RecordIterator(
            lambda: self._query_body(where, select, order_by), self._fetch_page, page_size, limit
        )

    def count(self, where: Expression | str | None = None) -> int:
        """Count the matching records without fetching them."""
        body = self._query_body(where, None, None)
        return (self._fetch_page({**body, "select": [RECORD_ID_FIELD]}, 1, 0)).total

    def _write(
        self, rows: list[dict[str, Any]], merge_field_id: int | None, batch_size: int
    ) -> WriteResult:
        table_id = (self.info()).id
        result = WriteResult()
        for start, end in split_batches(rows, batch_size):
            body: dict[str, Any] = {"to": table_id, "data": rows[start:end]}
            if merge_field_id is not None:
                body["mergeFieldId"] = merge_field_id
            try:
                payload = self._transport.request("POST", "/records", json=body)
            except QuickbaseError as exc:
                if start == 0:
                    raise
                raise WriteError(
                    f"Writing stopped at row {start}, after earlier rows were written: {exc}",
                    result=result,
                ) from exc
            result = parse(partial(result.merged, offset=start), payload, "write")
        if result.errors:
            raise WriteError(result.summary(), result=result)
        return result

    def insert(
        self, rows: Iterable[RowInput], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Create records from `{label or ID: value}` rows. This never changes existing records.

        Raises `WriteError` if any row fails. Other rows are still written, and are listed in
        the error's `result`. A value of `None` leaves the field empty.
        """
        _require_positive("batch_size", batch_size)
        given = list(rows)
        built = self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        refuse_field(built, RECORD_ID_FIELD, "which insert() can't do. Use update() instead")
        return self._write(built, None, batch_size)

    def update(
        self, rows: Iterable[RowInput], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Change existing records. Each row must set `Record ID#`.

        A record that doesn't exist is an error rather than being created.
        Fields left out of a row keep their values, and a value of `None` clears the field.
        Raises `WriteError` if any row fails. Other rows are still written.
        """
        _require_positive("batch_size", batch_size)
        given = list(rows)
        built = self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        require_field(built, RECORD_ID_FIELD, "so update() can't tell which record to change")
        return self._write(built, RECORD_ID_FIELD, batch_size)

    def upsert(
        self,
        rows: Iterable[RowInput],
        *,
        merge_on: FieldRef,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> WriteResult:
        """Update the record whose unique `merge_on` field matches, or create one if none does.

        Raises `WriteError` if any row fails. Other rows are still written.
        """
        _require_positive("batch_size", batch_size)
        given = list(rows)

        def build(fields: list[Field]) -> tuple[list[dict[str, Any]], int]:
            index = FieldIndex(fields)
            return build_rows(given, index), index.resolve(merge_on).id

        built, merge_field_id = self._with_fields(build)
        require_field(built, merge_field_id, "the merge_on field")
        return self._write(built, merge_field_id, batch_size)

    def delete(self, where: Expression | str) -> int:
        """Delete the matching records and return how many were deleted.

        `where` is required and is checked first. Quickbase would otherwise treat a lowercase
        operator as "contains" and delete far more than you meant.
        """
        table_id = (self.info()).id
        clause = self._with_fields(lambda fields: compile_where(where, FieldIndex(fields)))
        payload = self._transport.request(
            "DELETE", "/records", json={"from": table_id, "where": clause}
        )
        return parse(lambda data: int(data["numberDeleted"]), payload, "delete")

    def run_formula(self, formula: str, *, record_id: int | None = None) -> FormulaResult:
        """Run a formula against this table, without saving it.

        `record_id` is required if the formula uses fields. Don't run formula text you don't
        control: a formula can read other tables, whichever table you run it on.
        """
        require_formula(formula)
        if record_id is not None:
            require_id("record_id", record_id)
        body: dict[str, Any] = {"formula": formula, "from": (self.info()).id}
        if record_id is not None:
            body["rid"] = record_id
        payload = self._transport.request("POST", "/formula/run", json=body)
        return parse(FormulaResult.from_api, payload, "formula")

    def _file_path(self, record_id: int, field: FieldRef, version: int) -> str:
        require_id("record_id", record_id)
        table_id = (self.info()).id

        def resolve(fields: list[Field]) -> int:
            item = FieldIndex(fields).resolve(field)
            require_file_field(item)
            return item.id

        field_id = self._with_fields(resolve)
        return f"/files/{table_id}/{record_id}/{field_id}/{version}"

    def download_file(
        self, record_id: int, field: FieldRef, *, version: int | None = None
    ) -> FileDownload:
        """Download a file from a record, by default its latest version.

        Quickbase keeps a limited number of versions, so an older one may be gone. Raises
        `NotFoundError` if the record, version, or file doesn't exist.
        """
        if version is not None:
            require_id("version", version)
        # Version 0 means the latest to Quickbase.
        path = self._file_path(record_id, field, version or 0)
        response = self._transport.get_raw(path)
        return parse(FileDownload.from_response, response, "file")

    def delete_file_version(self, record_id: int, field: FieldRef, *, version: int) -> None:
        """Delete one version of a file. Deleting the only version leaves the field empty.

        `version` is required, because Quickbase treats version 0 as the latest and deletes it.
        """
        require_id("version", version)
        self._transport.request("DELETE", self._file_path(record_id, field, version))

    def _forget_fields(self, table_id: str) -> None:
        self._cache.delete(f"fields:{table_id}", f"relationships:{table_id}")

    def create_field(
        self,
        label: str,
        field_type: str,
        *,
        choices: Sequence[str] | None = None,
        required: bool | None = None,
        unique: bool | None = None,
        properties: Mapping[str, Any] | None = None,
        formula: str | None = None,
    ) -> Field:
        """Create a field and return it. `field_type` is Quickbase's, such as `text` or `date`.

        `formula` makes it a formula field, and `field_type` must match the formula's result.
        Quickbase sets `required` and `unique` in a second request, so if that one fails the field
        exists without them.
        """
        merged = field_properties(properties, formula)
        table_id = (self.info()).id
        if choices is not None:
            merged["choices"] = list(choices)
        body: dict[str, Any] = {"label": label, "fieldType": field_type}
        if merged:
            body["properties"] = merged
        payload = self._transport.request(
            "POST", "/fields", params={"tableId": table_id}, json=body
        )
        self._forget_fields(table_id)
        field = parse(Field.from_api, payload, "field")
        if required is None and unique is None:
            return field
        return self.update_field(field.id, required=required, unique=unique)

    def update_field(
        self,
        field: FieldRef,
        *,
        label: str | None = None,
        required: bool | None = None,
        unique: bool | None = None,
        properties: Mapping[str, Any] | None = None,
        formula: str | None = None,
    ) -> Field:
        """Change a field's settings and return it. Anything left as None stays as it is.

        `formula` only works on a formula field, and can't be blank, because Quickbase would accept
        that and empty the field's values.
        """
        merged = field_properties(properties, formula)
        table_id = (self.info()).id
        field_id = self._with_fields(lambda fields: FieldIndex(fields).resolve(field).id)
        optional = {
            "label": label,
            "required": required,
            "unique": unique,
            "properties": merged or None,
        }
        body = {k: v for k, v in optional.items() if v is not None}
        if not body:
            raise InvalidValueError("update_field() needs at least one setting to change.")
        payload = self._transport.request(
            "POST", f"/fields/{field_id}", params={"tableId": table_id}, json=body
        )
        self._forget_fields(table_id)
        return parse(Field.from_api, payload, "field")

    def delete_fields(self, fields: FieldRef | Sequence[FieldRef]) -> None:
        """Delete fields and their data. Raises `WriteError` if any couldn't be deleted."""
        table_id = (self.info()).id
        refs = [fields] if isinstance(fields, str | int) else list(fields)
        if not refs:
            raise InvalidValueError("delete_fields() needs at least one field.")
        field_ids = self.resolve_field_ids(refs)
        payload = self._transport.request(
            "DELETE", "/fields", params={"tableId": table_id}, json={"fieldIds": field_ids}
        )
        self._forget_fields(table_id)
        errors = parse(lambda data: list(data.get("errors") or ()), payload, "delete fields")
        if errors:
            raise WriteError(f"Some fields weren't deleted: {'; '.join(map(str, errors))}")

    def create_relationship(
        self,
        parent: "Table",
        *,
        foreign_key_label: str,
        lookup_fields: Sequence[FieldRef] = (),
    ) -> Relationship:
        """Make this table a child of `parent`, with a new foreign key field on this table.

        `lookup_fields` are fields of the parent to show on this table.
        """
        table_id = (self.info()).id
        parent_id = (parent.info()).id
        lookup_ids = parent.resolve_field_ids(lookup_fields)
        payload = self._transport.request(
            "POST",
            f"/tables/{table_id}/relationship",
            json={
                "parentTableId": parent_id,
                "foreignKeyField": {"label": foreign_key_label},
                "lookupFieldIds": lookup_ids,
            },
        )
        self._forget_fields(table_id)
        self._forget_fields(parent_id)
        return parse(Relationship.from_api, payload, "relationship")

    def resolve_field_ids(self, fields: Sequence[FieldRef]) -> list[int]:
        """Turn field labels or IDs into IDs. Raises `UnknownFieldError` if one doesn't exist."""
        return self._with_fields(
            lambda known: [FieldIndex(known).resolve(ref).id for ref in fields]
        )

    def drop(self, *, confirm: str) -> None:
        """Delete this table and all its records. `confirm` must be the table's exact name."""
        info = self.info()
        if confirm != info.name:
            raise InvalidValueError(
                f"Not dropping table {info.name!r}: confirm must be its exact name."
            )
        self._transport.request("DELETE", f"/tables/{info.id}", params={"appId": self._app.id})
        self._forget_fields(info.id)
        self._cache.delete(f"tables:{self._app.id}", f"reports:{info.id}")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._ref!r}, app={self._app.id!r})"


class Report:
    """A report on a table. Create one with `table.report("Open tasks")`."""

    def __init__(self, transport: Transport, table: Table, ref: str) -> None:
        self._transport = transport
        self._table = table
        self._ref = ref

    def info(self) -> ReportInfo:
        """Get the report's metadata, from the table's cached list of reports.

        Raises `UnknownReportError` if the table has no such report, or if several share the name.
        An ID takes priority over a name.
        """
        reports = self._table.reports()
        found = find_report(reports, self._ref)
        if found is None:
            # The report may be newer than the cache, so look once more before giving up.
            reports = self._table.reports(refresh=True)
            found = find_report(reports, self._ref)
        if found is None:
            raise UnknownReportError(unknown_report_message(reports, self._ref))
        return found

    def _load_target(self) -> dict[str, Any]:
        info = self.info()
        require_table_report(info)
        return {"id": info.id, "tableId": (self._table.info()).id}

    def _fetch_page(self, target: dict[str, Any], top: int, skip: int) -> Page:
        # Quickbase silently ignores top and skip in the body and returns every record.
        payload = self._transport.request(
            "POST",
            f"/reports/{target['id']}/run",
            params={"tableId": target["tableId"], "top": top, "skip": skip},
        )
        return parse(Page.from_api, payload, "report")

    def run_page(self, *, top: int = 100, skip: int = 0) -> Page:
        """Fetch one page of the report's records. See `run`."""
        _require_positive("top", top)
        if skip < 0:
            raise QueryError("skip can't be negative.")
        return self._fetch_page(self._load_target(), top, skip)

    def run(self, *, page_size: int = 1000, limit: int | None = None) -> RecordIterator:
        """Iterate over the report's records, fetching further pages as needed.

        Records hold only the report's columns, so `record.id` raises `KeyError` if Record ID#
        isn't one of them. Each run adds to the report's usage count in Quickbase.

        Raises `UnsupportedReportError` for anything but a complete table report, including summary,
        chart, and ask-the-user reports. Their raw results are available through `qb.request()`.

        Args:
            page_size: Records to request per call to Quickbase.
            limit: Stop after this many records.
        """
        _require_positive("page_size", page_size)
        if limit is not None:
            _require_positive("limit", limit)
        return RecordIterator(self._load_target, self._fetch_page, page_size, limit)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._ref!r}, table={self._table!r})"
