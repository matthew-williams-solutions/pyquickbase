# Generated from the async code by scripts/generate_sync.py. Do not edit.
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
    Role,
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
    require_summary_report,
    require_table_report,
    role_from_api,
    summary_rows_from_api,
    table_info_from_api,
    unknown_report_message,
)
from .._operations import (
    DEFAULT_MAX_ROWS,
    OperationName,
    OperationSpec,
    allowed_for,
    copy_schema,
    make_spec,
    operation_names,
    refused_operation,
    scope_rows,
    unavailable,
)
from .._preview import UpdatePreview, build_update_preview, id_batches, merge_rows
from .._query import Expression, F, FieldRef, FieldRefs, build_query_body, compile_where
from .._schema import SchemaCache
from .._summary import Summary, SummaryGroups, SummaryRow
from .._typed import (
    OrderBy,
    R,
    Row,
    TypedField,
    TypedPage,
    column_ids,
    order_refs,
    raise_for_problems,
    rows_for,
    schema_problems,
)
from .._writes import (
    RowInput,
    WriteResult,
    build_rows,
    custom_key_refusal,
    field_properties,
    merge_write_result,
    refuse_blank_rows,
    refuse_field,
    refused_write,
    require_field,
    require_file_field,
    require_formula,
    require_id,
    split_batches,
    write_failure,
)
from ..errors import (
    ConfigurationError,
    InvalidResponseError,
    InvalidValueError,
    PermissionDeniedError,
    QueryError,
    QuickbaseError,
    ReadRefusedError,
    SchemaMismatchError,
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
        """The app's ID, as passed to `qb.app()`."""
        return self._id

    def info(self) -> AppInfo:
        """Get the app's metadata, from the client's cache if it's there.

        Returns:
            An `AppInfo`, with the app's `id`, `name`, `description`, `time_zone`, and
            `date_format`.

        Raises:
            AuthenticationError: Quickbase doesn't recognize the app ID. It answers with a 401
                here, rather than a 404.
        """
        key = f"app:{self._id}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = self._transport.request("GET", f"/apps/{self._id}")
        info = parse(app_info_from_api, payload, "app")
        self._cache.set(key, info)
        return info

    def tables(self, *, refresh: bool = False) -> list[TableInfo]:
        """List the app's tables, from the cache unless `refresh` is set.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `TableInfo` for each table the token can see.

        Raises:
            AuthenticationError: Quickbase doesn't recognize the app ID. It answers with a 401
                here, rather than a 404.
        """
        key = f"tables:{self._id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/tables", params={"appId": self._id})
        tables = parse(
            lambda rows: tuple(table_info_from_api(row) for row in rows), payload, "tables"
        )
        self._cache.set(key, tables)
        return list(tables)

    def roles(self, *, refresh: bool = False) -> list[Role]:
        """List the app's roles, from the cache unless `refresh` is set.

        Quickbase has no API for reading or changing what a role can do.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `Role` for each role in the app.

        Raises:
            PermissionDeniedError: The token isn't an administrator's.
        """
        key = f"roles:{self._id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", f"/apps/{self._id}/roles")
        roles = parse(lambda rows: tuple(role_from_api(row) for row in rows), payload, "roles")
        self._cache.set(key, roles)
        return list(roles)

    def ensure_table(
        self,
        name: str,
        *,
        description: str | None = None,
        single_record_name: str | None = None,
        plural_record_name: str | None = None,
    ) -> "Table":
        """Return the table with this name, creating it if it doesn't exist.

        This makes a setup script safe to run again. An existing table isn't changed.

        Args:
            name: The table's name, ignoring case when looking for an existing table.
            description: The new table's description.
            single_record_name: What the new table calls one record, such as `Project`.
            plural_record_name: What the new table calls several records, such as `Projects`.

        Returns:
            A `Table` for the existing or new table.
        """
        tables = self.tables(refresh=True)
        for item in tables:
            if item.name.casefold() == name.casefold():
                return self.table(item.id)
        return self.create_table(
            name,
            description=description,
            single_record_name=single_record_name,
            plural_record_name=plural_record_name,
        )

    def create_table(
        self,
        name: str,
        *,
        description: str | None = None,
        single_record_name: str | None = None,
        plural_record_name: str | None = None,
    ) -> "Table":
        """Create a table and return it.

        Args:
            name: The table's name.
            description: The table's description.
            single_record_name: What the table calls one record, such as `Project`.
            plural_record_name: What the table calls several records, such as `Projects`.

        Returns:
            A `Table` for the new table.

        Raises:
            BadRequestError: The app already has a table with this name.
        """
        optional = {
            "description": description,
            "singleRecordName": single_record_name,
            "pluralRecordName": plural_record_name,
        }
        body = {"name": name, **{k: v for k, v in optional.items() if v is not None}}
        payload = self._transport.request("POST", "/tables", params={"appId": self._id}, json=body)
        self._cache.delete(f"tables:{self._id}")
        return self.table(parse(lambda data: table_info_from_api(data).id, payload, "table"))

    def table(self, name_or_id: str) -> "Table":
        """Get a table in this app by name or ID. It's checked when first used.

        This doesn't contact Quickbase, so a table that doesn't exist raises `UnknownTableError`
        on first use.

        Args:
            name_or_id: The table's name, or its ID.

        Returns:
            A `Table`.
        """
        return Table(self._transport, self._cache, self, name_or_id)

    def typed(self, model: type[R]) -> "TypedTable[R]":
        """Use a class from `pyquickbase codegen` for typed access to its table.

        The class is compared with the table when first used, raising `SchemaMismatchError`
        if it's out of date.

        Args:
            model: A class from the module that `pyquickbase codegen` wrote.

        Returns:
            A `TypedTable`, whose records are instances of `model`.
        """
        return TypedTable(self.table(model.__table_id__), model)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._id!r})"


def _same_property(key: str, current: Any, wanted: Any) -> bool:
    """Compare a field's property with what's wanted. Quickbase can reorder a field's choices."""
    if key == "choices":
        return set(current or ()) == set(wanted)
    return current == wanted


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
        if page.skip != self._skip:
            # Carrying on would return the same records forever.
            raise InvalidResponseError(
                f"Quickbase returned records from {page.skip} when asked to skip {self._skip}."
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
        """Get the table's metadata, from the client's cache of the app's tables.

        Returns:
            A `TableInfo`, with the table's `id` and `name`.

        Raises:
            UnknownTableError: This app has no such table, even after checking again.
        """
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
        """List the table's fields, from the cache unless `refresh` is set.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `Field` for each of the table's fields, including built-in ones like `Record ID#`.

        Raises:
            UnknownTableError: This app has no such table.
        """
        table_id = (self.info()).id
        key = f"fields:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/fields", params={"tableId": table_id})
        fields = parse(lambda rows: tuple(field_from_api(row) for row in rows), payload, "fields")
        self._cache.set(key, fields)
        return list(fields)

    def relationships(self, *, refresh: bool = False) -> list[Relationship]:
        """List the relationships in which this table is the child. A parent table lists none.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `Relationship` for each parent this table links to, or an empty list.

        Raises:
            UnknownTableError: This app has no such table.
        """
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
        rows = [relationship_from_api(row) for row in payload["relationships"]]
        return rows, payload["metadata"]["totalRelationships"]

    def reports(self, *, refresh: bool = False) -> list[ReportInfo]:
        """List the table's reports, from the cache unless `refresh` is set.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `ReportInfo` for each report, including ones Quickbase can't run through the API,
            whose `incomplete` is true.

        Raises:
            UnknownTableError: This app has no such table.
        """
        table_id = (self.info()).id
        key = f"reports:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/reports", params={"tableId": table_id})
        reports = parse(
            lambda rows: tuple(report_info_from_api(row) for row in rows), payload, "reports"
        )
        self._cache.set(key, reports)
        return list(reports)

    def report(self, name_or_id: str) -> "Report":
        """Get a report on this table by name or ID. It's checked when first used.

        This doesn't contact Quickbase, so a report that doesn't exist raises
        `UnknownReportError` on first use.

        Args:
            name_or_id: The report's exact name, or its ID, which takes priority over a name.

        Returns:
            A `Report`, for running it or getting its details.
        """
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
        select: FieldRefs | None,
        order_by: FieldRefs | None,
    ) -> dict[str, Any]:
        table_id = (self.info()).id
        return self._with_fields(
            lambda fields: build_query_body(
                table_id, fields, where=where, select=select, order_by=order_by
            )
        )

    def _fetch_page(
        self, body: dict[str, Any], top: int, skip: int, *, summarizing: bool = False
    ) -> Page:
        payload = self._transport.request(
            "POST",
            "/records/query",
            json={**body, "options": {"top": top, "skip": skip}},
            summarizing=summarizing,
        )
        return parse(page_from_api, payload, "records query")

    def _fetch_summary_page(self, body: dict[str, Any], top: int, skip: int) -> Page:
        return self._fetch_page(body, top, skip, summarizing=True)

    def query_page(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRefs | None = None,
        order_by: FieldRefs | None = None,
        top: int = 100,
        skip: int = 0,
    ) -> Page:
        """Fetch one page of records, for controlling paging yourself.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
            select: The fields to return, by label, ID, or field object. Defaults to the table's
                default fields. Record ID# is always included.
            order_by: The fields to sort by, by label, ID, or field object. Prefix a label with `-`,
                or negate an ID, to sort descending. Without this, records come back in Record ID#
                order.
            top: The most records to return.
            skip: How many matching records to pass over first. Use the previous page's `next_skip`.

        Returns:
            A `Page` with `records`, `total`, `skip`, `has_more`, and `next_skip`. Skipping past the
            end returns no records rather than raising an error.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was queried.
            QueryError: An argument can't be sent as written. Nothing was queried.
        """
        _require_positive("top", top)
        if skip < 0:
            raise QueryError("skip can't be negative.")
        body = self._query_body(where, select, order_by)
        return self._fetch_page(body, top, skip)

    def query(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRefs | None = None,
        order_by: FieldRefs | None = None,
        page_size: int = 1000,
        limit: int | None = None,
    ) -> RecordIterator:
        """Iterate over matching records, fetching further pages as needed.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
            select: The fields to return, by label, ID, or field object. Defaults to the table's
                default fields. Record ID# is always included.
            order_by: The fields to sort by, by label, ID, or field object. Prefix a label with `-`,
                or negate an ID, to sort descending. Without this, records come back in Record ID#
                order.
            page_size: Records to request per call to Quickbase.
            limit: Stop after this many records.

        Returns:
            An iterator of `Record`, which only contacts Quickbase once you start iterating.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was queried.
            QueryError: An argument can't be sent as written. Nothing was queried. `page_size` and
                `limit` raise this right away, and the others on first use.
        """
        _require_positive("page_size", page_size)
        if limit is not None:
            _require_positive("limit", limit)
        return RecordIterator(
            lambda: self._query_body(where, select, order_by), self._fetch_page, page_size, limit
        )

    def first(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRefs | None = None,
        order_by: FieldRefs | None = None,
    ) -> Record | None:
        """Get the first matching record, or None if there isn't one.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
            select: The fields to return, by label, ID, or field object. Defaults to the table's
                default fields.
            order_by: The fields to sort by, which decide which record is first. See `query`.

        Returns:
            The first matching `Record`, or None if nothing matches.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was queried.
            QueryError: An argument can't be sent as written. Nothing was queried.
        """
        page = self.query_page(where=where, select=select, order_by=order_by, top=1)
        return page.records[0] if page.records else None

    def get(self, record_id: int, *, select: FieldRefs | None = None) -> Record | None:
        """Get a record by its Record ID#, or None if it doesn't exist.

        Args:
            record_id: The record's Record ID#.
            select: The fields to return, by label, ID, or field object. Defaults to the table's
                default fields.

        Returns:
            The `Record`, or None if no record has this ID.

        Raises:
            InvalidValueError: `record_id` isn't a positive integer. Nothing was sent.
            UnknownFieldError: The table has no such field. Nothing was queried.
        """
        require_id("record_id", record_id)
        return self.first(where=F(RECORD_ID_FIELD) == record_id, select=select)

    def count(self, where: Expression | str | None = None) -> int:
        """Count the matching records without fetching them.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
                Defaults to every record.

        Returns:
            The number of matching records.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was queried.
            QueryError: `where` can't be sent as written. Nothing was queried.
        """
        body = self._query_body(where, None, None)
        return (self._fetch_page({**body, "select": [RECORD_ID_FIELD]}, 1, 0)).total

    def _write(
        self, rows: list[dict[str, Any]], merge_field_id: int | None, batch_size: int
    ) -> WriteResult:
        info = self.info()
        table_id = info.id
        result = WriteResult()
        for start, end in split_batches(rows, batch_size):
            body: dict[str, Any] = {"to": table_id, "data": rows[start:end]}
            if merge_field_id is not None:
                body["mergeFieldId"] = merge_field_id
            try:
                payload = self._transport.request(
                    "POST", "/records", json=body, key_field_id=info.key_field_id
                )
                result = parse(partial(merge_write_result, result, offset=start), payload, "write")
            except PermissionDeniedError as exc:
                raise refused_write(exc, result, start) from exc
            except QuickbaseError as exc:
                if start == 0:
                    raise
                raise WriteError(
                    f"Writing stopped at row {start}, after earlier rows were written: {exc}",
                    result=result,
                ) from exc
        if result.errors:
            raise write_failure(result)
        return result

    def insert(
        self, rows: Iterable[RowInput], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Create records from `{label or ID: value}` rows. This never changes existing records.

        So it refuses a table whose key field isn't `Record ID#`, where Quickbase would change the
        record when a row has an existing value in that field. Use `upsert()` on those tables.

        Args:
            rows: The records to create, as `{label or ID: value}` dictionaries. A value of `None`
                leaves the field empty, but a row needs at least one real value, because Quickbase
                can silently skip a row without one.
            batch_size: The most rows to send in one request.

        Returns:
            A `WriteResult`, whose `created_ids` are in the same order as `rows`.

        Raises:
            InvalidValueError: A row can't be sent, such as one that sets `Record ID#` or has no
                real values, or the table's key field isn't `Record ID#`. Nothing was written.
            UnknownFieldError: A row names a field the table doesn't have. Nothing was written.
            WriteError: A row failed. Other rows are still written, and are listed in the error's
                `result`.
        """
        _require_positive("batch_size", batch_size)
        given = list(rows)
        built = self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        key_field_id = (self.info()).key_field_id
        if key_field_id not in (None, RECORD_ID_FIELD):
            raise custom_key_refusal(self._with_fields(FieldIndex), key_field_id)
        refuse_field(built, RECORD_ID_FIELD, "which insert() can't do. Use update() instead")
        refuse_blank_rows(built)
        return self._write(built, None, batch_size)

    def update(
        self, rows: Iterable[RowInput], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Change existing records. Each row must set `Record ID#`.

        A record that doesn't exist is an error rather than being created.

        Args:
            rows: The changes, as `{label or ID: value}` dictionaries that each set `Record ID#`.
                Fields left out of a row keep their values, and a value of `None` clears the field.
            batch_size: The most rows to send in one request.

        Returns:
            A `WriteResult`. A record whose values didn't change is in `unchanged_ids`, not
            `updated_ids`.

        Raises:
            InvalidValueError: A row can't be sent, such as one without `Record ID#`. Nothing was
                written.
            UnknownFieldError: A row names a field the table doesn't have. Nothing was written.
            WriteError: A row failed. Other rows are still written, and are listed in the error's
                `result`.
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

        Args:
            rows: The records, as `{label or ID: value}` dictionaries that each set `merge_on`.
                Fields left out of a row keep their values on a record that already exists.
            merge_on: The field that identifies a record, by label or ID. Quickbase requires it to
                be marked as unique.
            batch_size: The most rows to send in one request.

        Returns:
            A `WriteResult`, with new records in `created_ids`, changed ones in `updated_ids`, and
            ones that already matched in `unchanged_ids`.

        Raises:
            InvalidValueError: A row can't be sent, such as one without `merge_on`. Nothing was
                written.
            UnknownFieldError: `merge_on` or a row names a field the table doesn't have. Nothing was
                written.
            BadRequestError: `merge_on` isn't a unique field. Nothing was written.
            WriteError: A row failed. Other rows are still written, and are listed in the error's
                `result`.
        """
        _require_positive("batch_size", batch_size)
        given = list(rows)

        def build(fields: list[Field]) -> tuple[list[dict[str, Any]], int]:
            index = FieldIndex(fields)
            return build_rows(given, index), index.resolve(merge_on).id

        built, merge_field_id = self._with_fields(build)
        require_field(built, merge_field_id, "the merge_on field")
        refuse_blank_rows(built)
        return self._write(built, merge_field_id, batch_size)

    def delete(self, where: Expression | str) -> int:
        """Delete the matching records and return how many were deleted.

        Args:
            where: A condition built with `F`, or a hand-written string. It's required and is
                checked first, because Quickbase would otherwise treat a lowercase operator as
                "contains" and delete far more than you meant.

        Returns:
            The number of records deleted, which is 0 if none matched.

        Raises:
            QueryError: `where` can't be sent as written. Nothing was deleted.
            UnknownFieldError: `where` names a field the table doesn't have. Nothing was deleted.
        """
        table_id = (self.info()).id
        clause = self._with_fields(lambda fields: compile_where(where, FieldIndex(fields)))
        payload = self._transport.request(
            "DELETE", "/records", json={"from": table_id, "where": clause}
        )
        return parse(lambda data: int(data["numberDeleted"]), payload, "delete")

    def summarize(
        self,
        group_by: SummaryGroups | None = None,
        *,
        where: Expression | str | None = None,
        total: FieldRefs | None = None,
        average: FieldRefs | None = None,
        minimum: FieldRefs | None = None,
        maximum: FieldRefs | None = None,
        max_records: int | None = None,
        page_size: int = 1000,
    ) -> list[SummaryRow]:
        """Count and total records, overall or per group, without a saved report.

        This reads every matching record, so it makes one request per `page_size` records. Records
        can change between pages, so on a table in use, the result may not match any one moment. A
        field's values are added exactly, and returned as `float`.

        Args:
            group_by: The fields to group by, and `Period` for dates. Without this, the result is
                one row for all the matching records.
            where: A condition built with `F` or `where_from_dict()`, or a hand-written string.
            total: Numeric fields to add up. Empty values are skipped.
            average: Numeric fields to average. Empty values are skipped.
            minimum: Numeric, date, timestamp, or time of day fields to find the lowest of.
            maximum: Numeric, date, timestamp, or time of day fields to find the highest of.
            max_records: Raise `QueryError` if more records match than this, without reading them.
                It never returns a partial result. An agent's tool should always set this. A
                client's `ReadRules` can set a limit of their own, `max_summary_records`.
            page_size: Records to request per call to Quickbase.

        Returns:
            A `SummaryRow` for each group, sorted by the group's values with empty ones last. If
            nothing matches, that's an empty list, or without `group_by`, one row with a `count` of
            0.

        Raises:
            QueryError: An argument can't be used, such as a text field in `total`, or more records
                match than `max_records`. No records were read.
            UnknownFieldError: The table has no such field. No records were read.
            ReadRefusedError: More records match than the client's `ReadRules` allow summarizing,
                or it doesn't allow a field. No records were read.
            InvalidResponseError: A value isn't what its field's type says it should be.
        """
        _require_positive("page_size", page_size)
        if max_records is not None:
            _require_positive("max_records", max_records)
        summary = self._with_fields(
            lambda fields: Summary(
                fields,
                group_by=group_by,
                total=total,
                average=average,
                minimum=minimum,
                maximum=maximum,
            )
        )
        allowed = self._transport.summary_limit((self.info()).id)
        if max_records is not None or allowed is not None:
            found = self.count(where)
            if max_records is not None and found > max_records:
                raise QueryError(
                    f"{found} records match, which is more than max_records of {max_records}. "
                    "Narrow the filter, or raise the limit."
                )
            if allowed is not None and found > allowed:
                raise ReadRefusedError(
                    f"{found} records match, and this client's read rules allow summarizing at "
                    f"most {allowed}. Narrow the filter. Nothing was summarized."
                )
        records = RecordIterator(
            lambda: self._query_body(where, summary.select, None),
            self._fetch_summary_page,
            page_size,
            allowed,
        )
        for record in records:
            summary.add(record)
        return summary.rows()

    def preview_delete(
        self,
        where: Expression | str,
        *,
        select: FieldRefs | None = None,
        order_by: FieldRefs | None = None,
        top: int = 100,
    ) -> Page:
        """Show what `delete()` would delete with the same `where`, without deleting anything.

        This only reads, so it works on a read-only client. Records can change before a delete, so
        it can't promise what the delete will do, and it can't tell whether the token may delete.

        Args:
            where: A condition built with `F`, or a hand-written string, checked as `delete()`
                checks it. It's required, so that leaving it out can't look like a safe preview.
            select: The fields to return. Defaults to the table's default fields.
            order_by: Which matching records come first. See `query`.
            top: The most records to return. `total` still counts every match.

        Returns:
            A `Page`, whose `total` is how many records would be deleted.

        Raises:
            QueryError: `where` can't be sent as written, or is None. Nothing was queried.
            UnknownFieldError: The table has no such field. Nothing was queried.
        """
        if where is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise QueryError("preview_delete() needs where, as delete() does.")
        return self.query_page(where=where, select=select, order_by=order_by, top=top)

    def preview_update(self, rows: Iterable[RowInput]) -> UpdatePreview:
        """Show what `update()` would change with the same rows, without changing anything.

        This only reads, so it works on a read-only client. Records can change before an update, so
        it can't promise what the update will do. It also can't tell whether Quickbase will accept
        a value, such as a role that can't edit a field, or one of the table's data rules.

        Args:
            rows: The rows you'd pass to `update()`. If a record is in several rows, a later row
                wins. `Record ID#` must be a positive integer here, even as a string works in
                `update()`.

        Returns:
            An `UpdatePreview`, with `changes`, `unchanged_ids`, and `missing_ids`. A value counts
            as a change whenever the comparison isn't certain, so `update()` may report a listed
            record as unchanged. A file upload is always a change.

        Raises:
            InvalidValueError: A row can't be sent, such as one without `Record ID#`. Nothing was
                read.
            UnknownFieldError: A row names a field the table doesn't have. Nothing was read.
        """
        given = list(rows)
        built = self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        require_field(built, RECORD_ID_FIELD, "so preview_update() can't tell which record")
        merged = merge_rows(built)
        current: dict[int, Record] = {}
        limit = self._transport.record_limit((self.info()).id)
        for ids in id_batches(list(merged), limit):
            select = sorted({field_id for record_id in ids for field_id in merged[record_id]})
            page = self.query_page(where=F(RECORD_ID_FIELD).is_in(ids), select=select, top=len(ids))
            current.update({record.id: record for record in page.records})
        return build_update_preview(merged, current, FieldIndex(self.fields()))

    def run_formula(self, formula: str, *, record_id: int | None = None) -> FormulaResult:
        """Run a formula against this table, without saving it.

        Don't run formula text you don't control: a formula can read other tables, whichever
        table you run it on.

        Args:
            formula: The formula's text, in Quickbase's formula language. It can't be blank.
            record_id: The record whose values the formula reads. It's required if the formula
                uses fields.

        Returns:
            A `FormulaResult`. Its `value` is the result as a Python value where that's safe,
            and `text` is exactly what Quickbase sent.

        Raises:
            InvalidValueError: `formula` is blank, or `record_id` isn't a positive integer.
                Nothing was sent.
            BadRequestError: Quickbase can't run the formula, and its message says why.
            NotFoundError: The record doesn't exist.
        """
        require_formula(formula)
        if record_id is not None:
            require_id("record_id", record_id)
        body: dict[str, Any] = {"formula": formula, "from": (self.info()).id}
        if record_id is not None:
            body["rid"] = record_id
        payload = self._transport.request("POST", "/formula/run", json=body)
        return parse(formula_result_from_api, payload, "formula")

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

        Quickbase keeps a limited number of versions, so an older one may be gone.

        Args:
            record_id: The Record ID# of the record that holds the file.
            field: The file attachment field, by label or ID.
            version: The version to download, starting at 1. Defaults to the latest.

        Returns:
            A `FileDownload`, with the file's `name`, `data`, and `content_type`. Don't trust
            `name` as a path, or `content_type` as a description of what's in the file.

        Raises:
            InvalidValueError: `record_id` or `version` isn't a positive integer, or `field`
                isn't a file attachment field. Nothing was sent.
            UnknownFieldError: The table has no such field. Nothing was sent.
            NotFoundError: The record, version, or file doesn't exist.
        """
        if version is not None:
            require_id("version", version)
        # Version 0 means the latest to Quickbase.
        path = self._file_path(record_id, field, version or 0)
        response = self._transport.get_raw(path)
        return parse(file_download_from_response, response, "file")

    def delete_file_version(self, record_id: int, field: FieldRef, *, version: int) -> None:
        """Delete one version of a file. Deleting the only version leaves the field empty.

        The record stays, and the other versions keep their numbers.

        Args:
            record_id: The Record ID# of the record that holds the file.
            field: The file attachment field, by label or ID.
            version: The version to delete, starting at 1. It's required, because Quickbase
                treats version 0 as the latest and deletes it.

        Raises:
            InvalidValueError: `record_id` or `version` isn't a positive integer, or `field`
                isn't a file attachment field. Nothing was sent.
            UnknownFieldError: The table has no such field. Nothing was sent.
            NotFoundError: The record or version doesn't exist.
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
        """Create a field and return it.

        Quickbase sets `required` and `unique` in a second request, so if that one fails the field
        exists without them. Use `ensure_field()` to repair it.

        Args:
            label: The new field's label. Another field can't have the same label, ignoring case.
            field_type: One of `text`, `text-multiple-choice`, `text-multi-line`, `rich-text`,
                `numeric`, `currency`, `rating`, `percent`, `multitext`, `email`, `url`, `duration`,
                `date`, `datetime`, `timestamp`, `timeofday`, `checkbox`, `user`, `multiuser`,
                `address`, `phone`, or `file`. A `datetime` field reports its type as `timestamp`.
            choices: The options of a `text-multiple-choice` or `multitext` field. Quickbase still
                accepts other values unless `properties` sets `allowNewChoices` to False.
            required: Whether the field must have a value. Quickbase refuses this if the table has
                records, because the new field is empty on all of them.
            unique: Whether values must be different on every record.
            properties: Other settings, sent as they are, such as `{"decimalPlaces": 2}`. Quickbase
                refuses a key it doesn't know.
            formula: Makes this a formula field. `field_type` must match the formula's result, and
                can't be `timestamp` or `text-multi-line`. Use `datetime` for a timestamp formula.

        Returns:
            The new `Field`.

        Raises:
            BadRequestError: Quickbase refused the field, such as for an unknown type, a label
                that's already used, or `choices` on a type without them. If it refused `required`
                or `unique`, the field was still created, and the message says so.
            InvalidValueError: `formula` is blank. Nothing was sent.
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
        field = parse(field_from_api, payload, "field")
        if required is None and unique is None:
            return field
        try:
            return self.update_field(field.id, required=required, unique=unique)
        except QuickbaseError as exc:
            exc.args = (
                f"Field {field.id} was created, but setting required or unique failed, so it "
                f"exists without them. Use ensure_field() to repair it: {exc}",
            )
            exc._changed = True  # pyright: ignore[reportPrivateUsage]
            raise

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

        A field's type can't be changed here.

        Args:
            field: The field's label or ID.
            label: A new label.
            required: Whether the field must have a value. Quickbase refuses `True` if any record
                has this field empty.
            unique: Whether values must be different on every record. Quickbase refuses `True` if
                the field already holds duplicates.
            properties: Other settings, sent as they are. `{"choices": [...]}` replaces the whole
                list of choices.
            formula: New formula text, only for a formula field. It can't be blank, because
                Quickbase would accept that and empty the field's values.

        Returns:
            The `Field`, with its new settings.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was sent.
            InvalidValueError: There's nothing to change, or `formula` is blank. Nothing was sent.
            BadRequestError: Quickbase refused the change, and the field is as it was.
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
        return parse(field_from_api, payload, "field")

    def delete_fields(self, fields: FieldRef | Sequence[FieldRef]) -> None:
        """Delete fields and all their data.

        Args:
            fields: One field or a list of them, by label or ID.

        Raises:
            UnknownFieldError: A field doesn't exist. Nothing was deleted.
            InvalidValueError: The list is empty. Nothing was sent.
            WriteError: A field couldn't be deleted, such as a built-in field. The others in the
                list were still deleted.
        """
        table_id = (self.info()).id
        refs = [fields] if isinstance(fields, str | int) else list(fields)
        if not refs:
            raise InvalidValueError("delete_fields() needs at least one field.")
        field_ids = self.resolve_field_ids(refs)
        payload = self._transport.request(
            "DELETE", "/fields", params={"tableId": table_id}, json={"fieldIds": field_ids}
        )
        self._forget_fields(table_id)
        errors, deleted = parse(
            lambda data: (list(data.get("errors") or ()), list(data.get("deletedFieldIds") or ())),
            payload,
            "delete fields",
        )
        if errors:
            error = WriteError(
                f"Some fields weren't deleted, and fields {deleted} were: "
                f"{'; '.join(map(str, errors))}"
            )
            error._changed = bool(deleted)  # pyright: ignore[reportPrivateUsage]
            raise error

    def create_relationship(
        self,
        parent: "Table",
        *,
        foreign_key_label: str,
        lookup_fields: Sequence[FieldRef] = (),
    ) -> Relationship:
        """Make this table a child of `parent`, with a new foreign key field on this table.

        Quickbase also adds two fields to the parent: a list of the child's records, and a link to
        add one. Calling this again makes another relationship, even with the same label, so use
        `ensure_relationship()` in a script that may run twice.

        Args:
            parent: The `Table` to link to, which becomes the parent.
            foreign_key_label: The label of the new field on this table, which holds the Record
                ID# of each record's parent.
            lookup_fields: Fields of the parent to show on this table, by label or ID. Quickbase
                names each new field after the foreign key and the field, such as
                `Project - Name`.

        Returns:
            The `Relationship`. Its `lookup_field_ids` are the new fields on this table, so don't
            pass them back as `lookup_fields`.

        Raises:
            UnknownFieldError: The parent has no such field. Nothing was sent.
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
        return parse(relationship_from_api, payload, "relationship")

    def ensure_field(
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
        """Return the field with this label, creating it or changing it to match what you pass.

        This makes a setup script safe to run again, and repairs a field whose `required` or
        `unique` didn't get set, because Quickbase sets those in a second request. The arguments
        are the same as for `create_field()`, and anything left as None is ignored on an existing
        field. `choices` are compared ignoring order.

        Args:
            label: The field's label, ignoring case.
            field_type: The field's type. `datetime` matches a field that reports `timestamp`.
            choices: The options of a `text-multiple-choice` or `multitext` field.
            required: Whether the field must have a value. Quickbase refuses `True` if any record
                has this field empty, which includes a new field on a table with records.
            unique: Whether values must be different on every record.
            properties: Other settings, sent as they are.
            formula: The text of a formula field.

        Returns:
            The `Field`, whether it was found, created, or changed.

        Raises:
            SchemaMismatchError: The field exists with a different type, because Quickbase can't
                always convert one. It wasn't changed.
            BadRequestError: Quickbase refused the field or the change.
        """
        fields = self.fields(refresh=True)
        found = next((item for item in fields if item.label.casefold() == label.casefold()), None)
        if found is None:
            return self.create_field(
                label,
                field_type,
                choices=choices,
                required=required,
                unique=unique,
                properties=properties,
                formula=formula,
            )
        reported_type = "timestamp" if field_type == "datetime" else field_type
        if found.type != reported_type:
            raise SchemaMismatchError(
                f"Field {found.label!r} is {found.type}, not {field_type}, so it wasn't changed."
            )
        wanted = field_properties(properties, formula)
        if choices is not None:
            wanted["choices"] = list(choices)
        current: Mapping[str, Any] = found.raw.get("properties") or {}
        changed = {
            key: value
            for key, value in wanted.items()
            if not _same_property(key, current.get(key), value)
        }
        new_required = required if required not in (None, found.required) else None
        new_unique = unique if unique not in (None, found.unique) else None
        if not changed and new_required is None and new_unique is None:
            return found
        return self.update_field(
            found.id, required=new_required, unique=new_unique, properties=changed or None
        )

    def ensure_relationship(self, parent: "Table", *, foreign_key_label: str) -> Relationship:
        """Return this table's relationship with `parent`, creating it if it doesn't exist.

        Use the result with `ensure_lookup_field()` and `ensure_summary_field()`.

        Args:
            parent: The `Table` to link to, which becomes the parent.
            foreign_key_label: The label of the field on this table that holds the Record ID# of
                each record's parent, ignoring case when looking for an existing relationship.

        Returns:
            The existing or new `Relationship`.
        """
        parent_id = (parent.info()).id
        labels = {item.id: item.label.casefold() for item in self.fields(refresh=True)}
        for item in self.relationships(refresh=True):
            label = labels.get(item.foreign_key_field_id or 0)
            if item.parent_table_id == parent_id and label == foreign_key_label.casefold():
                return item
        return self.create_relationship(parent, foreign_key_label=foreign_key_label)

    def ensure_lookup_field(
        self, relationship: Relationship, parent_field: FieldRef, *, label: str | None = None
    ) -> Field:
        """Return this table's lookup of `parent_field`, creating it if it doesn't exist.

        Call this on the child table, and the new field is on the child. It's found by what it
        points at, so renaming it doesn't make a script create another.

        Args:
            relationship: The relationship, from `ensure_relationship()`.
            parent_field: The parent's field to look up, by label or ID.
            label: A label for the lookup, since Quickbase names one after the foreign key and the
                field, such as `Project - Name`. An existing lookup is renamed to match.

        Returns:
            The lookup `Field`, on this table.

        Raises:
            UnknownFieldError: The parent has no such field. Nothing was sent.
        """
        parent = self._app.table(relationship.parent_table_id)
        target_id = (parent.resolve_field_ids([parent_field]))[0]
        found = self._find_lookup(relationship, target_id)
        if found is None:
            table_id = (self.info()).id
            # This adds to the existing lookups, and takes the parent's field IDs, not the child's.
            self._transport.request(
                "POST",
                f"/tables/{table_id}/relationship/{relationship.id}",
                json={"lookupFieldIds": [target_id]},
            )
            self._forget_fields(table_id)
            self._forget_fields(relationship.parent_table_id)
            found = self._find_lookup(relationship, target_id)
            if found is None:
                raise InvalidResponseError(
                    f"Quickbase accepted a lookup of field {target_id}, but it can't be found."
                )
        if label is not None and found.label != label:
            return self.update_field(found.id, label=label)
        return found

    def _find_lookup(self, relationship: Relationship, target_id: int) -> Field | None:
        for item in self.fields(refresh=True):
            if (
                item.mode == "lookup"
                and item.lookup_reference_field_id == relationship.foreign_key_field_id
                and item.lookup_target_field_id == target_id
            ):
                return item
        return None

    def ensure_summary_field(
        self,
        relationship: Relationship,
        child_field: FieldRef,
        label: str,
        *,
        function: str = "SUM",
    ) -> Field:
        """Return the summary of this table's `child_field` on the parent, creating it if needed.

        Call this on the child table, and the new field is on the parent. **A summary with no child
        records is empty, not 0**, so wrap it in `Nz()` in a formula if your code expects a number.

        Args:
            relationship: The relationship, from `ensure_relationship()`.
            child_field: This table's field to summarize, by label or ID.
            label: The label of the summary field on the parent.
            function: Quickbase's name for the calculation, in capitals: `SUM`, `AVG`, `MAX`, `MIN`,
                `STD-DEV`, `DISTINCT-COUNT`, `COMBINED-TEXT`, or `COMBINED-USER`.

        Returns:
            The summary `Field`, on the parent.

        Raises:
            UnknownFieldError: This table has no such field. Nothing was sent.
            SchemaMismatchError: The label is already used on the parent by anything but this
                summary. It wasn't changed.
            BadRequestError: Quickbase can't apply `function` to this field, such as `SUM` of a
                checkbox.
        """
        parent = self._app.table(relationship.parent_table_id)
        target_id = (self.resolve_field_ids([child_field]))[0]
        fields = parent.fields(refresh=True)
        found = next((item for item in fields if item.label.casefold() == label.casefold()), None)
        if found is not None:
            if (
                found.mode != "summary"
                or found.summary_target_field_id != target_id
                or found.summary_reference_field_id != relationship.foreign_key_field_id
                or found.summary_function != function
            ):
                raise SchemaMismatchError(
                    f"Field {found.label!r} on the parent isn't this summary, so it wasn't changed."
                )
            return found
        table_id = (self.info()).id
        self._transport.request(
            "POST",
            f"/tables/{table_id}/relationship/{relationship.id}",
            json={
                "summaryFields": [
                    {"summaryFid": target_id, "label": label, "accumulationType": function}
                ]
            },
        )
        self._forget_fields(table_id)
        self._forget_fields(relationship.parent_table_id)
        fields = parent.fields(refresh=True)
        created = next((item for item in fields if item.label == label), None)
        if created is None:
            raise InvalidResponseError(
                f"Quickbase accepted summary {label!r}, but it can't be found."
            )
        return created

    def resolve_field_ids(self, fields: Sequence[FieldRef]) -> list[int]:
        """Turn field labels or IDs into IDs.

        Args:
            fields: The fields, by label or ID, in any mix.

        Returns:
            The field IDs, in the same order as `fields`.

        Raises:
            UnknownFieldError: The table has no such field.
        """
        return self._with_fields(
            lambda known: [FieldIndex(known).resolve(ref).id for ref in fields]
        )

    def operation(
        self,
        name: OperationName,
        *,
        fields: Sequence[FieldRef] | None = None,
        descriptions: Mapping[FieldRef, str] | None = None,
        reports: Sequence[str] | Mapping[str, str] | None = None,
        formula: str | None = None,
        max_records: int | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> "Operation":
        """Make one operation on this table into a tool for a model, with its JSON Schema.

        The schema describes the arguments a model may send, and `run()` checks them against the
        same lists before calling the table, so a model can't use anything it wasn't offered. The
        lists hold only what the client's `ReadRules` and `WriteRules` allow, narrowed by `fields`.

        ```python
        search = tasks.operation("query", fields=["Title", "Status"])
        tool = {"name": "search_tasks", "input_schema": search.schema}
        page = search.run(arguments, scope=F("Project") == project)
        ```

        See [Using pyquickbase from an AI agent](guides/agents.md#giving-a-model-operations) for
        what each operation takes.

        Args:
            name: What the operation does, such as `query` or `insert`. Each runs the method of the
                same name, and `run_report` and `summary_report` run a report.
            fields: The only fields it may use, by label or ID. Defaults to every field the client
                may use for the operation.
            descriptions: What fields mean, as `{label or ID: text}`, which the schema shows the
                model. Each must be for a field the operation offers.
            reports: The reports `run_report` or `summary_report` may run, by ID, or as `{ID:
                description}`. Defaults to every report of that kind the client may run.
            formula: For `run_formula`, the formula's text, which your code fixes. The model only
                chooses the record, as a formula can read any table.
            max_records: The most records one call may return, or for `delete`, may match. A
                delete that matches more is refused. Defaults to 100, or for `summarize`, 10,000
                records read. The client's `ReadRules` can lower it.
            max_rows: The most records one `insert` or `update` may send.

        Returns:
            An `Operation`, with its `schema` and `run()`.

        Raises:
            WriteRefusedError: The client's limits on writes don't allow this operation at all.
            ReadRefusedError: The client's `ReadRules` don't allow it at all, such as a formula.
            ConfigurationError: The table has nothing for it, such as no file fields, or
                `formula` is missing or given for another operation.
            UnknownFieldError: `fields` or `descriptions` names a field the client can't see, or
                one the operation doesn't offer.
            UnknownReportError: `reports` has an ID this table doesn't have.
        """
        if name not in operation_names():
            raise ConfigurationError(f"name must be one of: {', '.join(operation_names())}.")
        transport = self._transport
        if name == "run_formula" and transport.read_rules:
            raise ReadRefusedError(
                "This client's read rules refuse formulas, as a formula can read other tables "
                "and fields."
            )
        info = self.info()
        report_ids: Sequence[str] | None = None
        report_notes: dict[str, str] = {}
        if isinstance(reports, Mapping):
            report_notes = {str(key): str(text) for key, text in reports.items()}
            report_ids = list(report_notes)
        elif reports is not None:
            report_ids = [str(item) for item in reports]
        needs_reports = name in ("run_report", "summary_report")
        allowed = allowed_for(
            self.fields(),
            self.reports() if needs_reports else (),
            table_id=info.id,
            key_field_id=info.key_field_id,
            read_rules=transport.read_rules,
            write_rules=transport.write_rules,
            read_only=transport.read_only,
            wanted=fields,
            wanted_reports=report_ids if needs_reports else None,
        )
        reason = unavailable(name, allowed, formula)
        if reason is not None:
            raise refused_operation(
                name,
                reason,
                read_limited=bool(transport.read_rules),
                write_limited=transport.read_only or bool(transport.write_rules),
            )
        spec = make_spec(
            name,
            allowed,
            descriptions=descriptions,
            report_descriptions=report_notes,
            max_records=max_records,
            max_rows=max_rows,
            formula=formula,
        )
        return Operation(self, spec)

    def drop(self, *, confirm: str) -> None:
        """Delete this table and all its records.

        Args:
            confirm: The table's exact name, so a wrong variable can't drop the wrong table.

        Raises:
            InvalidValueError: `confirm` isn't the table's exact name. Nothing was sent.
        """
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

        Returns:
            A `ReportInfo`.

        Raises:
            UnknownReportError: The table has no such report, or several share the name. An ID takes
                priority over a name.
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
        return {"id": info.id, "tableId": (self._table.info()).id, "type": info.type}

    def _fetch_page(self, target: dict[str, Any], top: int, skip: int) -> Page:
        # Quickbase silently ignores top and skip in the body and returns every record.
        payload = self._transport.request(
            "POST",
            f"/reports/{target['id']}/run",
            params={"tableId": target["tableId"], "top": top, "skip": skip},
            report_type=target["type"],
        )
        return parse(page_from_api, payload, "report")

    def run_page(self, *, top: int = 100, skip: int = 0) -> Page:
        """Fetch one page of the report's records, for controlling paging yourself.

        Records hold only the report's columns, and each run adds to the report's usage count in
        Quickbase.

        Args:
            top: The most records to return.
            skip: How many records to pass over first. Use the previous page's `next_skip`.

        Returns:
            A `Page`, whose `total` counts every record in the report.

        Raises:
            UnknownReportError: The table has no such report, or several share the name.
            UnsupportedReportError: It isn't a complete table report, such as a summary, chart, or
                ask-the-user report.
        """
        _require_positive("top", top)
        if skip < 0:
            raise QueryError("skip can't be negative.")
        return self._fetch_page(self._load_target(), top, skip)

    def run(self, *, page_size: int = 1000, limit: int | None = None) -> RecordIterator:
        """Iterate over the report's records, fetching further pages as needed.

        Records hold only the report's columns, so `record.id` raises `KeyError` if Record ID#
        isn't one of them. Each run adds to the report's usage count in Quickbase.

        Args:
            page_size: Records to request per call to Quickbase.
            limit: Stop after this many records.

        Returns:
            An iterator of `Record`, which only contacts Quickbase once you start iterating.

        Raises:
            UnknownReportError: The table has no such report, or several share the name.
            UnsupportedReportError: It isn't a complete table report, such as a summary, chart, or
                ask-the-user report. Their raw results are available through `qb.request()`.
        """
        _require_positive("page_size", page_size)
        if limit is not None:
            _require_positive("limit", limit)
        return RecordIterator(self._load_target, self._fetch_page, page_size, limit)

    def summary_rows(
        self, *, page_size: int = 1000, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Get a summary or chart report's rows, as `{column label: value}`.

        Quickbase does the calculating, using the report as it was built in the UI. Values are what
        Quickbase returns, so a chart's dates are text in the app's format, such as `01-01-2026`.
        Each run adds to the report's usage count in Quickbase.

        Args:
            page_size: Rows to request per call to Quickbase.
            limit: Stop after this many rows.

        Returns:
            The rows. A summary report's total column is labeled like `Hours (tot)`.

        Raises:
            UnknownReportError: The table has no such report, or several share the name.
            UnsupportedReportError: It's a table report, which `run()` reads as records. Or
                Quickbase can't run it, such as a report that asks the user for a value, or two of
                its columns have the same label.
            InvalidResponseError: Quickbase stopped returning rows before the end.
        """
        _require_positive("page_size", page_size)
        if limit is not None:
            _require_positive("limit", limit)
        info = self.info()
        require_summary_report(info)
        table_id = (self._table.info()).id
        rows: list[dict[str, Any]] = []
        while True:
            top = page_size if limit is None else min(page_size, limit - len(rows))
            # Quickbase silently ignores top and skip in the body and returns every row.
            payload = self._transport.request(
                "POST",
                f"/reports/{info.id}/run",
                params={"tableId": table_id, "top": top, "skip": len(rows)},
                report_type=info.type,
            )
            page, total = parse(summary_rows_from_api, payload, "report")
            rows.extend(page)
            if len(rows) >= total or len(rows) == limit:
                return rows
            if not page:
                raise InvalidResponseError(
                    f"Quickbase returned an empty page at {len(rows)} of {total} rows."
                )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._ref!r}, table={self._table!r})"


class Operation:
    """One operation on a table, for a model's tool call. Create one with `table.operation()`."""

    def __init__(self, table: Table, spec: OperationSpec) -> None:
        self._table = table
        self._spec = spec

    @property
    def name(self) -> OperationName:
        """What the operation does, such as `query`."""
        return self._spec.name

    @property
    def schema(self) -> dict[str, Any]:
        """The JSON Schema of the arguments a model may send, for a tool's input.

        It's self-contained, with no `$ref`, and a new dictionary each time, so you can add to it.
        """
        return copy_schema(self._spec.schema)

    def run(self, arguments: Mapping[str, Any], *, scope: Expression | None = None) -> Any:
        """Check a model's arguments, and run the operation.

        Arguments the schema doesn't allow are refused before anything is sent, with a message that
        says where the mistake is, and doesn't repeat the values sent.

        Args:
            arguments: The arguments from the model's tool call.
            scope: A condition your code fixes, such as the user's project, that every record must
                match. It's added to every filter. An operation that names records, such as
                `update`, first checks that they all match it. A write can't set a field the scope
                uses, and `insert` fills those fields in from the scope, which must then be only
                `==` conditions joined with `&`. A report can't take one.

        Returns:
            What the table method returns, such as a `Page` for `query`, which `tool_result()`
            takes.

        Raises:
            QueryError: An argument can't be used, a record named isn't in `scope`, a write sets
                a field the scope uses, or a delete matches more than `max_records`. Nothing was
                sent, other than the query that checks the scope or counts a delete.
            InvalidValueError: A record or value can't be written. Nothing was sent.
        """
        call = self._spec.plan(arguments, scope)
        table = self._table
        if scope is not None and "rows" in call.arguments:
            call = scope_rows(call, scope, FieldIndex(table.fields()))
        if call.most is not None:
            matching = table.count(call.arguments["where"])
            if matching > call.most:
                raise QueryError(
                    f"{matching} records match, and this tool may delete at most {call.most} at "
                    "once, so nothing was deleted. Narrow the filter."
                )
        if call.record_ids and scope is not None:
            ids = list(call.record_ids)
            found = table.count(scope & F(RECORD_ID_FIELD).is_in(ids))
            if found != len(ids):
                raise QueryError(
                    "Some of these records don't exist, or aren't ones this tool may use, so "
                    "nothing was done."
                )
        if call.report_id is not None:
            report = table.report(call.report_id)
            return getattr(report, call.method)(**call.arguments)
        if call.method in ("insert", "update", "preview_update"):
            return getattr(table, call.method)(call.arguments["rows"])
        return getattr(table, call.method)(**call.arguments)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._spec.name!r}, table={self._table!r})"


class TypedTable(Generic[R]):
    """A table read and written through a generated class. Create one with `app.typed(Tasks)`."""

    def __init__(self, table: Table, model: type[R]) -> None:
        self._table = table
        self._model = model
        self._checked = False

    @property
    def table(self) -> Table:
        """The untyped table, for anything this class doesn't cover."""
        return self._table

    def check_schema(self) -> None:
        """Raise `SchemaMismatchError` if the class no longer matches the table.

        This always fetches the table's fields again. Other methods only check once, on first
        use, against cached metadata, so a long-running process can call this to look again.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
        """
        self._checked = False
        fields = self._table.fields(refresh=True)
        raise_for_problems(self._model, schema_problems(self._model, fields))
        self._checked = True

    def _check_once(self) -> None:
        if self._checked:
            return
        if schema_problems(self._model, self._table.fields()):
            # The cache may be older than the class, so look once more before giving up.
            self.check_schema()
        self._checked = True

    def query(
        self,
        *,
        where: Expression | str | None = None,
        order_by: OrderBy = None,
        page_size: int = 1000,
        limit: int | None = None,
    ) -> "TypedIterator[R]":
        """Iterate over matching records as instances of the class. See `Table.query`.

        Every field in the class is selected.

        Args:
            where: A condition built with `Tasks.fields` or `F`, or a hand-written string.
            order_by: A field or list of fields, such as `Tasks.fields.title`, or
                `Tasks.fields.title.desc` for descending. Without this, records come back in
                Record ID# order.
            page_size: Records to request per call to Quickbase.
            limit: Stop after this many records.

        Returns:
            A `TypedIterator`, which only contacts Quickbase once you start iterating.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            QueryError: `where`, `page_size`, or `limit` can't be sent as written. Nothing was sent.
            InvalidResponseError: A record has a fraction in a whole number field.
        """
        records = self._table.query(
            where=where,
            select=column_ids(self._model),
            order_by=order_refs(order_by),
            page_size=page_size,
            limit=limit,
        )
        return TypedIterator(self._check_once, self._model, records)

    def query_page(
        self,
        *,
        where: Expression | str | None = None,
        order_by: OrderBy = None,
        top: int = 100,
        skip: int = 0,
    ) -> TypedPage[R]:
        """Fetch one page of records as instances of the class, for controlling paging yourself.

        Every field in the class is selected.

        Args:
            where: A condition built with `Tasks.fields` or `F`, or a query string.
            order_by: A field or list of fields, such as `Tasks.fields.title`, or
                `Tasks.fields.title.desc` for descending. Without this, records come back in
                Record ID# order.
            top: The most records to return.
            skip: How many matching records to pass over first. Use the previous page's `next_skip`.

        Returns:
            The records, with `total`, `skip`, `has_more`, and `next_skip`.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            QueryError: `where`, `top`, or `skip` can't be sent as written. Nothing was sent.
            InvalidResponseError: A record has a fraction in a whole number field.
        """
        self._check_once()
        page = self._table.query_page(
            where=where,
            select=column_ids(self._model),
            order_by=order_refs(order_by),
            top=top,
            skip=skip,
        )
        records = tuple(self._model.from_record(record) for record in page.records)
        return TypedPage(records=records, total=page.total, skip=page.skip)

    def first(self, *, where: Expression | str | None = None, order_by: OrderBy = None) -> R | None:
        """Get the first matching record as an instance of the class, or None if there isn't one.

        Args:
            where: A condition built with `Tasks.fields` or `F`, or a hand-written string.
            order_by: A field or list of fields, such as `Tasks.fields.title`, or
                `Tasks.fields.title.desc` for descending. Without this, records come back in
                Record ID# order.

        Returns:
            An instance of the class, or None.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            QueryError: `where` can't be sent as written. Nothing was sent.
        """
        self._check_once()
        record = self._table.first(
            where=where, select=column_ids(self._model), order_by=order_refs(order_by)
        )
        return None if record is None else self._model.from_record(record)

    def get(self, record_id: int) -> R | None:
        """Get a record by its Record ID# as an instance of the class, or None if there's none.

        Args:
            record_id: The record's Record ID#.

        Returns:
            An instance of the class, or None.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            InvalidValueError: `record_id` isn't a positive integer. Nothing was sent.
        """
        self._check_once()
        record = self._table.get(record_id, select=column_ids(self._model))
        return None if record is None else self._model.from_record(record)

    def count(self, where: Expression | str | None = None) -> int:
        """Count the matching records without fetching them.

        Args:
            where: A condition built with `Tasks.fields` or `F`, or a hand-written string.
                Defaults to every record.

        Returns:
            The number of matching records.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            QueryError: `where` can't be sent as written. Nothing was sent.
        """
        self._check_once()
        return self._table.count(where)

    def insert(
        self, rows: Iterable[Row[R]], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Create records from rows made with `Tasks.new()`. See `Table.insert`.

        Args:
            rows: The rows, from `Tasks.new()`.
            batch_size: The most rows to send in one request.

        Returns:
            A `WriteResult`, whose `created_ids` are in the same order as `rows`.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            InvalidValueError: A row was made by a class for another table. Nothing was written.
            WriteError: A row failed. Other rows are still written, and are listed in the error's
                `result`.
        """
        given = rows_for(self._model, rows)
        self._check_once()
        return self._table.insert(cast(Iterable[RowInput], given), batch_size=batch_size)

    def update(
        self, rows: Iterable[Row[R]], *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Change records from rows made with `Tasks.change()`. See `Table.update`.

        Args:
            rows: The rows, from `Tasks.change()`. Fields you leave out keep their values.
            batch_size: The most rows to send in one request.

        Returns:
            A `WriteResult`. A record whose values didn't change is in `unchanged_ids`, not
            `updated_ids`.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            InvalidValueError: A row was made by a class for another table. Nothing was written.
            WriteError: A row failed. Other rows are still written, and are listed in the error's
                `result`.
        """
        given = rows_for(self._model, rows)
        self._check_once()
        return self._table.update(cast(Iterable[RowInput], given), batch_size=batch_size)

    def upsert(
        self, rows: Iterable[Row[R]], *, merge_on: TypedField, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> WriteResult:
        """Update the record whose unique `merge_on` field matches. See `Table.upsert`.

        Args:
            rows: The rows, from `Tasks.new()`, which must each set `merge_on`.
            merge_on: A field marked as unique, such as `Tasks.fields.code`.
            batch_size: The most rows to send in one request.

        Returns:
            A `WriteResult`, with new records in `created_ids`, changed ones in `updated_ids`, and
            ones that already matched in `unchanged_ids`.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            InvalidValueError: A row was made by a class for another table. Nothing was written.
            WriteError: A row failed. Other rows are still written, and are listed in the error's
                `result`.
        """
        given = rows_for(self._model, rows)
        self._check_once()
        return self._table.upsert(
            cast(Iterable[RowInput], given), merge_on=merge_on.id, batch_size=batch_size
        )

    def delete(self, where: Expression | str) -> int:
        """Delete the matching records and return how many were deleted. See `Table.delete`.

        Args:
            where: A condition built with `Tasks.fields` or `F`, or a hand-written string. It's
                required and is checked first.

        Returns:
            The number of records deleted, which is 0 if none matched.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            QueryError: `where` can't be sent as written. Nothing was deleted.
        """
        self._check_once()
        return self._table.delete(where)

    def summarize(
        self,
        group_by: SummaryGroups | None = None,
        *,
        where: Expression | str | None = None,
        total: FieldRefs | None = None,
        average: FieldRefs | None = None,
        minimum: FieldRefs | None = None,
        maximum: FieldRefs | None = None,
        max_records: int | None = None,
        page_size: int = 1000,
    ) -> list[SummaryRow]:
        """Count and total records, overall or per group. See `Table.summarize`.

        Results are keyed by field label, and aren't converted as the class's own values are, so
        whole numbers read as `float`.

        Args:
            group_by: The fields to group by, such as `Tasks.fields.status`, and `Period` for dates.
                Without this, the result is one row for all the matching records.
            where: A condition built with `Tasks.fields` or `F`, or a hand-written string.
            total: Numeric fields to add up, such as `Tasks.fields.hours`.
            average: Numeric fields to average.
            minimum: Numeric, date, timestamp, or time of day fields to find the lowest of.
            maximum: Numeric, date, timestamp, or time of day fields to find the highest of.
            max_records: Raise `QueryError` if more records match than this, without reading them.
            page_size: Records to request per call to Quickbase.

        Returns:
            A `SummaryRow` for each group.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            QueryError: An argument can't be used, or more records match than `max_records`.
        """
        self._check_once()
        return self._table.summarize(
            group_by,
            where=where,
            total=total,
            average=average,
            minimum=minimum,
            maximum=maximum,
            max_records=max_records,
            page_size=page_size,
        )

    def preview_delete(
        self, where: Expression | str, *, order_by: OrderBy = None, top: int = 100
    ) -> TypedPage[R]:
        """Show what `delete()` would delete, as instances of the class. See `Table.preview_delete`.

        Args:
            where: A condition built with `Tasks.fields` or `F`, or a hand-written string.
            order_by: Which matching records come first, as for `query()`.
            top: The most records to return. `total` still counts every match.

        Returns:
            A `TypedPage`, whose `total` is how many records would be deleted.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            QueryError: `where` can't be sent as written, or is None. Nothing was queried.
        """
        self._check_once()
        page = self._table.preview_delete(
            where, select=column_ids(self._model), order_by=order_refs(order_by), top=top
        )
        records = tuple(self._model.from_record(record) for record in page.records)
        return TypedPage(records=records, total=page.total, skip=page.skip)

    def preview_update(self, rows: Iterable[Row[R]]) -> UpdatePreview:
        """Show what `update()` would change. See `Table.preview_update`.

        Args:
            rows: The rows you'd pass to `update()`, from `Tasks.change()`.

        Returns:
            An `UpdatePreview`, with `changes`, `unchanged_ids`, and `missing_ids`.

        Raises:
            SchemaMismatchError: The class no longer matches the table.
            InvalidValueError: A row was made by a class for another table. Nothing was read.
        """
        given = rows_for(self._model, rows)
        self._check_once()
        return self._table.preview_update(cast(Iterable[RowInput], given))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._model.__name__})"


class TypedIterator(Generic[R]):
    """Yields records from a query as instances of a generated class."""

    def __init__(self, check: Callable[[], Any], model: type[R], records: RecordIterator) -> None:
        self._check = check
        self._model = model
        self._records = records

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> R:
        self._check()
        return self._model.from_record(self._records.__next__())
