"""Apps and tables: labels instead of IDs, cached schema, and automatic paging."""

import difflib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from functools import partial
from typing import Any, Generic, Self, TypeVar, cast

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
    app_info_from_api,
    field_from_api,
    file_download_from_response,
    find_report,
    formula_result_from_api,
    page_from_api,
    parse,
    relationship_from_api,
    report_info_from_api,
    require_table_report,
    table_info_from_api,
    unknown_report_message,
)
from .._query import Expression, FieldRef, build_query_body, compile_where
from .._schema import SchemaCache
from .._typed import (
    OrderBy,
    R,
    Row,
    TypedField,
    column_ids,
    order_refs,
    raise_for_problems,
    schema_problems,
)
from .._writes import (
    RowInput,
    WriteResult,
    build_rows,
    field_properties,
    merge_write_result,
    refuse_blank_rows,
    refuse_field,
    require_field,
    require_file_field,
    require_formula,
    require_id,
    split_batches,
    write_result_summary,
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
from ._transport import AsyncTransport

T = TypeVar("T")
DEFAULT_BATCH_SIZE = 1000


def _require_positive(name: str, value: int) -> None:
    if value < 1:
        raise QueryError(f"{name} must be at least 1.")


class AsyncApp:
    """A Quickbase app. Create one with `qb.app(app_id)`. This makes no network calls."""

    def __init__(self, transport: AsyncTransport, cache: SchemaCache, app_id: str) -> None:
        self._transport = transport
        self._cache = cache
        self._id = app_id

    @property
    def id(self) -> str:
        return self._id

    async def info(self) -> AppInfo:
        """Get the app's metadata."""
        key = f"app:{self._id}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = await self._transport.request("GET", f"/apps/{self._id}")
        info = parse(app_info_from_api, payload, "app")
        self._cache.set(key, info)
        return info

    async def tables(self, *, refresh: bool = False) -> list[TableInfo]:
        """List the app's tables, from the cache unless `refresh` is set."""
        key = f"tables:{self._id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = await self._transport.request("GET", "/tables", params={"appId": self._id})
        tables = parse(
            lambda rows: tuple(table_info_from_api(row) for row in rows), payload, "tables"
        )
        self._cache.set(key, tables)
        return list(tables)

    async def create_table(
        self,
        name: str,
        *,
        description: str | None = None,
        single_record_name: str | None = None,
        plural_record_name: str | None = None,
    ) -> "AsyncTable":
        """Create a table and return it."""
        optional = {
            "description": description,
            "singleRecordName": single_record_name,
            "pluralRecordName": plural_record_name,
        }
        body = {"name": name, **{k: v for k, v in optional.items() if v is not None}}
        payload = await self._transport.request(
            "POST", "/tables", params={"appId": self._id}, json=body
        )
        self._cache.delete(f"tables:{self._id}")
        return self.table(parse(lambda data: table_info_from_api(data).id, payload, "table"))

    def table(self, name_or_id: str) -> "AsyncTable":
        """Get a table in this app by name or ID. It's checked when first used."""
        return AsyncTable(self._transport, self._cache, self, name_or_id)

    def typed(self, model: type[R]) -> "AsyncTypedTable[R]":
        """Use a class from `pyquickbase codegen` for typed access to its table.

        The class is compared with the table when first used, raising `SchemaMismatchError`
        if it's out of date.
        """
        return AsyncTypedTable(self.table(model.__table_id__), model)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._id!r})"


def _find_table(tables: Sequence[TableInfo], ref: str) -> TableInfo | None:
    return next((item for item in tables if item.id == ref), None) or next(
        (item for item in tables if item.name == ref), None
    )


class AsyncRecordIterator:
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

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Record:
        while True:
            record = next(self._buffer, None)
            if record is not None:
                self._yielded += 1
                return record
            if self._done or self._yielded == self._limit:
                raise StopAsyncIteration
            await self._load_next_page()

    async def _load_next_page(self) -> None:
        body = self._body
        if body is None:
            body = self._body = await self._load_body()
        remaining = None if self._limit is None else self._limit - self._yielded
        top = self._page_size if remaining is None else min(self._page_size, remaining)
        page: Page = await self._fetch_page(body, top, self._skip)
        if page.has_more and not page.records:
            raise InvalidResponseError(
                f"Quickbase returned an empty page at {self._skip} of {page.total} records."
            )
        self._buffer = iter(page.records)
        self._skip = page.next_skip
        self._done = not page.has_more


class AsyncTable:
    """A table in an app. Create one with `app.table("Tasks")`."""

    def __init__(
        self, transport: AsyncTransport, cache: SchemaCache, app: AsyncApp, ref: str
    ) -> None:
        self._transport = transport
        self._cache = cache
        self._app = app
        self._ref = ref

    async def info(self) -> TableInfo:
        """Get the table's metadata. Raises `UnknownTableError` if it isn't in this app."""
        tables = await self._app.tables()
        found = _find_table(tables, self._ref)
        if found is None:
            # The table may be newer than the cache, so look once more before giving up.
            tables = await self._app.tables(refresh=True)
            found = _find_table(tables, self._ref)
        if found is None:
            names = [item.name for item in tables]
            close = difflib.get_close_matches(self._ref, names, n=3)
            hint = f" Did you mean {' or '.join(repr(name) for name in close)}?" if close else ""
            raise UnknownTableError(f"App {self._app.id} has no table {self._ref!r}.{hint}")
        return found

    async def fields(self, *, refresh: bool = False) -> list[Field]:
        """List the table's fields, from the cache unless `refresh` is set."""
        table_id = (await self.info()).id
        key = f"fields:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = await self._transport.request("GET", "/fields", params={"tableId": table_id})
        fields = parse(lambda rows: tuple(field_from_api(row) for row in rows), payload, "fields")
        self._cache.set(key, fields)
        return list(fields)

    async def relationships(self, *, refresh: bool = False) -> list[Relationship]:
        """List the relationships in which this table is the child. A parent table lists none."""
        table_id = (await self.info()).id
        key = f"relationships:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        found: list[Relationship] = []
        while True:
            payload = await self._transport.request(
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
        rows = [relationship_from_api(row) for row in payload["relationships"]]
        return rows, payload["metadata"]["totalRelationships"]

    async def reports(self, *, refresh: bool = False) -> list[ReportInfo]:
        """List the table's reports, from the cache unless `refresh` is set."""
        table_id = (await self.info()).id
        key = f"reports:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = await self._transport.request("GET", "/reports", params={"tableId": table_id})
        reports = parse(
            lambda rows: tuple(report_info_from_api(row) for row in rows), payload, "reports"
        )
        self._cache.set(key, reports)
        return list(reports)

    def report(self, name_or_id: str) -> "AsyncReport":
        """Get a report on this table by name or ID. It's checked when first used."""
        return AsyncReport(self._transport, self, name_or_id)

    async def _with_fields(self, build: Callable[[list[Field]], T]) -> T:
        try:
            return build(await self.fields())
        except UnknownFieldError:
            # The field may be newer than the cache, so look once more before giving up.
            return build(await self.fields(refresh=True))

    async def _query_body(
        self,
        where: Expression | str | None,
        select: FieldRef | Sequence[FieldRef] | None,
        order_by: FieldRef | Sequence[FieldRef] | None,
    ) -> dict[str, Any]:
        table_id = (await self.info()).id
        return await self._with_fields(
            lambda fields: build_query_body(
                table_id, fields, where=where, select=select, order_by=order_by
            )
        )

    async def _fetch_page(self, body: dict[str, Any], top: int, skip: int) -> Page:
        payload = await self._transport.request(
            "POST", "/records/query", json={**body, "options": {"top": top, "skip": skip}}
        )
        return parse(page_from_api, payload, "records query")

    async def query_page(
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
        body = await self._query_body(where, select, order_by)
        return await self._fetch_page(body, top, skip)

    def query(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRef | Sequence[FieldRef] | None = None,
        order_by: FieldRef | Sequence[FieldRef] | None = None,
        page_size: int = 1000,
        limit: int | None = None,
    ) -> AsyncRecordIterator:
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
        return AsyncRecordIterator(
            lambda: self._query_body(where, select, order_by), self._fetch_page, page_size, limit
        )

    async def count(self, where: Expression | str | None = None) -> int:
        """Count the matching records without fetching them."""
        body = await self._query_body(where, None, None)
        return (await self._fetch_page({**body, "select": [RECORD_ID_FIELD]}, 1, 0)).total

    async def _write(
        self, rows: list[dict[str, Any]], merge_field_id: int | None, batch_size: int
    ) -> WriteResult:
        table_id = (await self.info()).id
        result = WriteResult()
        for start, end in split_batches(rows, batch_size):
            body: dict[str, Any] = {"to": table_id, "data": rows[start:end]}
            if merge_field_id is not None:
                body["mergeFieldId"] = merge_field_id
            try:
                payload = await self._transport.request("POST", "/records", json=body)
            except QuickbaseError as exc:
                if start == 0:
                    raise
                raise WriteError(
                    f"Writing stopped at row {start}, after earlier rows were written: {exc}",
                    result=result,
                ) from exc
            result = parse(partial(merge_write_result, result, offset=start), payload, "write")
        if result.errors:
            raise WriteError(write_result_summary(result), result=result)
        return result

    async def insert(
        self, rows: Iterable[RowInput], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Create records from `{label or ID: value}` rows. This never changes existing records.

        Raises `WriteError` if any row fails. Other rows are still written, and are listed in
        the error's `result`. A value of `None` leaves the field empty, but a row needs at least
        one real value, because Quickbase can silently skip a row without one.
        """
        _require_positive("batch_size", batch_size)
        given = list(rows)
        built = await self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        refuse_field(built, RECORD_ID_FIELD, "which insert() can't do. Use update() instead")
        refuse_blank_rows(built)
        return await self._write(built, None, batch_size)

    async def update(
        self, rows: Iterable[RowInput], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Change existing records. Each row must set `Record ID#`.

        A record that doesn't exist is an error rather than being created.
        Fields left out of a row keep their values, and a value of `None` clears the field.
        Raises `WriteError` if any row fails. Other rows are still written.
        """
        _require_positive("batch_size", batch_size)
        given = list(rows)
        built = await self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        require_field(built, RECORD_ID_FIELD, "so update() can't tell which record to change")
        return await self._write(built, RECORD_ID_FIELD, batch_size)

    async def upsert(
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

        built, merge_field_id = await self._with_fields(build)
        require_field(built, merge_field_id, "the merge_on field")
        refuse_blank_rows(built)
        return await self._write(built, merge_field_id, batch_size)

    async def delete(self, where: Expression | str) -> int:
        """Delete the matching records and return how many were deleted.

        `where` is required and is checked first. Quickbase would otherwise treat a lowercase
        operator as "contains" and delete far more than you meant.
        """
        table_id = (await self.info()).id
        clause = await self._with_fields(lambda fields: compile_where(where, FieldIndex(fields)))
        payload = await self._transport.request(
            "DELETE", "/records", json={"from": table_id, "where": clause}
        )
        return parse(lambda data: int(data["numberDeleted"]), payload, "delete")

    async def run_formula(self, formula: str, *, record_id: int | None = None) -> FormulaResult:
        """Run a formula against this table, without saving it.

        `record_id` is required if the formula uses fields. Don't run formula text you don't
        control: a formula can read other tables, whichever table you run it on.
        """
        require_formula(formula)
        if record_id is not None:
            require_id("record_id", record_id)
        body: dict[str, Any] = {"formula": formula, "from": (await self.info()).id}
        if record_id is not None:
            body["rid"] = record_id
        payload = await self._transport.request("POST", "/formula/run", json=body)
        return parse(formula_result_from_api, payload, "formula")

    async def _file_path(self, record_id: int, field: FieldRef, version: int) -> str:
        require_id("record_id", record_id)
        table_id = (await self.info()).id

        def resolve(fields: list[Field]) -> int:
            item = FieldIndex(fields).resolve(field)
            require_file_field(item)
            return item.id

        field_id = await self._with_fields(resolve)
        return f"/files/{table_id}/{record_id}/{field_id}/{version}"

    async def download_file(
        self, record_id: int, field: FieldRef, *, version: int | None = None
    ) -> FileDownload:
        """Download a file from a record, by default its latest version.

        Quickbase keeps a limited number of versions, so an older one may be gone. Raises
        `NotFoundError` if the record, version, or file doesn't exist.
        """
        if version is not None:
            require_id("version", version)
        # Version 0 means the latest to Quickbase.
        path = await self._file_path(record_id, field, version or 0)
        response = await self._transport.get_raw(path)
        return parse(file_download_from_response, response, "file")

    async def delete_file_version(self, record_id: int, field: FieldRef, *, version: int) -> None:
        """Delete one version of a file. Deleting the only version leaves the field empty.

        `version` is required, because Quickbase treats version 0 as the latest and deletes it.
        """
        require_id("version", version)
        await self._transport.request("DELETE", await self._file_path(record_id, field, version))

    def _forget_fields(self, table_id: str) -> None:
        self._cache.delete(f"fields:{table_id}", f"relationships:{table_id}")

    async def create_field(
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
        table_id = (await self.info()).id
        if choices is not None:
            merged["choices"] = list(choices)
        body: dict[str, Any] = {"label": label, "fieldType": field_type}
        if merged:
            body["properties"] = merged
        payload = await self._transport.request(
            "POST", "/fields", params={"tableId": table_id}, json=body
        )
        self._forget_fields(table_id)
        field = parse(field_from_api, payload, "field")
        if required is None and unique is None:
            return field
        return await self.update_field(field.id, required=required, unique=unique)

    async def update_field(
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
        table_id = (await self.info()).id
        field_id = await self._with_fields(lambda fields: FieldIndex(fields).resolve(field).id)
        optional = {
            "label": label,
            "required": required,
            "unique": unique,
            "properties": merged or None,
        }
        body = {k: v for k, v in optional.items() if v is not None}
        if not body:
            raise InvalidValueError("update_field() needs at least one setting to change.")
        payload = await self._transport.request(
            "POST", f"/fields/{field_id}", params={"tableId": table_id}, json=body
        )
        self._forget_fields(table_id)
        return parse(field_from_api, payload, "field")

    async def delete_fields(self, fields: FieldRef | Sequence[FieldRef]) -> None:
        """Delete fields and their data. Raises `WriteError` if any couldn't be deleted."""
        table_id = (await self.info()).id
        refs = [fields] if isinstance(fields, str | int) else list(fields)
        if not refs:
            raise InvalidValueError("delete_fields() needs at least one field.")
        field_ids = await self.resolve_field_ids(refs)
        payload = await self._transport.request(
            "DELETE", "/fields", params={"tableId": table_id}, json={"fieldIds": field_ids}
        )
        self._forget_fields(table_id)
        errors = parse(lambda data: list(data.get("errors") or ()), payload, "delete fields")
        if errors:
            raise WriteError(f"Some fields weren't deleted: {'; '.join(map(str, errors))}")

    async def create_relationship(
        self,
        parent: "AsyncTable",
        *,
        foreign_key_label: str,
        lookup_fields: Sequence[FieldRef] = (),
    ) -> Relationship:
        """Make this table a child of `parent`, with a new foreign key field on this table.

        `lookup_fields` are fields of the parent to show on this table.
        """
        table_id = (await self.info()).id
        parent_id = (await parent.info()).id
        lookup_ids = await parent.resolve_field_ids(lookup_fields)
        payload = await self._transport.request(
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
        return parse(relationship_from_api, payload, "relationship")

    async def resolve_field_ids(self, fields: Sequence[FieldRef]) -> list[int]:
        """Turn field labels or IDs into IDs. Raises `UnknownFieldError` if one doesn't exist."""
        return await self._with_fields(
            lambda known: [FieldIndex(known).resolve(ref).id for ref in fields]
        )

    async def drop(self, *, confirm: str) -> None:
        """Delete this table and all its records. `confirm` must be the table's exact name."""
        info = await self.info()
        if confirm != info.name:
            raise InvalidValueError(
                f"Not dropping table {info.name!r}: confirm must be its exact name."
            )
        await self._transport.request(
            "DELETE", f"/tables/{info.id}", params={"appId": self._app.id}
        )
        self._forget_fields(info.id)
        self._cache.delete(f"tables:{self._app.id}", f"reports:{info.id}")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._ref!r}, app={self._app.id!r})"


class AsyncReport:
    """A report on a table. Create one with `table.report("Open tasks")`."""

    def __init__(self, transport: AsyncTransport, table: AsyncTable, ref: str) -> None:
        self._transport = transport
        self._table = table
        self._ref = ref

    async def info(self) -> ReportInfo:
        """Get the report's metadata, from the table's cached list of reports.

        Raises `UnknownReportError` if the table has no such report, or if several share the name.
        An ID takes priority over a name.
        """
        reports = await self._table.reports()
        found = find_report(reports, self._ref)
        if found is None:
            # The report may be newer than the cache, so look once more before giving up.
            reports = await self._table.reports(refresh=True)
            found = find_report(reports, self._ref)
        if found is None:
            raise UnknownReportError(unknown_report_message(reports, self._ref))
        return found

    async def _load_target(self) -> dict[str, Any]:
        info = await self.info()
        require_table_report(info)
        return {"id": info.id, "tableId": (await self._table.info()).id}

    async def _fetch_page(self, target: dict[str, Any], top: int, skip: int) -> Page:
        # Quickbase silently ignores top and skip in the body and returns every record.
        payload = await self._transport.request(
            "POST",
            f"/reports/{target['id']}/run",
            params={"tableId": target["tableId"], "top": top, "skip": skip},
        )
        return parse(page_from_api, payload, "report")

    async def run_page(self, *, top: int = 100, skip: int = 0) -> Page:
        """Fetch one page of the report's records. See `run`."""
        _require_positive("top", top)
        if skip < 0:
            raise QueryError("skip can't be negative.")
        return await self._fetch_page(await self._load_target(), top, skip)

    def run(self, *, page_size: int = 1000, limit: int | None = None) -> AsyncRecordIterator:
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
        return AsyncRecordIterator(self._load_target, self._fetch_page, page_size, limit)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._ref!r}, table={self._table!r})"


class AsyncTypedTable(Generic[R]):
    """A table read and written through a generated class. Create one with `app.typed(Tasks)`."""

    def __init__(self, table: AsyncTable, model: type[R]) -> None:
        self._table = table
        self._model = model
        self._checked = False

    @property
    def table(self) -> AsyncTable:
        """The untyped table, for anything this class doesn't cover."""
        return self._table

    async def check_schema(self) -> None:
        """Raise `SchemaMismatchError` if the class no longer matches the table.

        This always fetches the table's fields again. Other methods only check once, on first
        use, against cached metadata, so a long-running process can call this to look again.
        """
        self._checked = False
        fields = await self._table.fields(refresh=True)
        raise_for_problems(self._model, schema_problems(self._model, fields))
        self._checked = True

    async def _check_once(self) -> None:
        if self._checked:
            return
        if schema_problems(self._model, await self._table.fields()):
            # The cache may be older than the class, so look once more before giving up.
            await self.check_schema()
        self._checked = True

    def query(
        self,
        *,
        where: Expression | str | None = None,
        order_by: OrderBy = None,
        page_size: int = 1000,
        limit: int | None = None,
    ) -> "AsyncTypedIterator[R]":
        """Iterate over matching records as instances of the class. See `Table.query`.

        Every field in the class is selected. Sort with `Tasks.fields.title`, or
        `Tasks.fields.title.desc` for descending.
        """
        records = self._table.query(
            where=where,
            select=column_ids(self._model),
            order_by=order_refs(order_by),
            page_size=page_size,
            limit=limit,
        )
        return AsyncTypedIterator(self._check_once, self._model, records)

    async def count(self, where: Expression | str | None = None) -> int:
        """Count the matching records without fetching them."""
        await self._check_once()
        return await self._table.count(where)

    async def insert(self, rows: Iterable[Row[R]]) -> WriteResult:
        """Create records from rows made with `Tasks.new()`. See `Table.insert`."""
        await self._check_once()
        return await self._table.insert(cast(Iterable[RowInput], rows))

    async def update(self, rows: Iterable[Row[R]]) -> WriteResult:
        """Change records from rows made with `Tasks.change()`. See `Table.update`."""
        await self._check_once()
        return await self._table.update(cast(Iterable[RowInput], rows))

    async def upsert(self, rows: Iterable[Row[R]], *, merge_on: TypedField) -> WriteResult:
        """Update the record whose unique `merge_on` field matches. See `Table.upsert`."""
        await self._check_once()
        return await self._table.upsert(cast(Iterable[RowInput], rows), merge_on=merge_on.id)

    async def delete(self, where: Expression | str) -> int:
        """Delete the matching records and return how many were deleted. See `Table.delete`."""
        await self._check_once()
        return await self._table.delete(where)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._model.__name__})"


class AsyncTypedIterator(Generic[R]):
    """Yields records from a query as instances of a generated class."""

    def __init__(
        self, check: Callable[[], Any], model: type[R], records: AsyncRecordIterator
    ) -> None:
        self._check = check
        self._model = model
        self._records = records

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> R:
        await self._check()
        return self._model.from_record(await self._records.__anext__())
