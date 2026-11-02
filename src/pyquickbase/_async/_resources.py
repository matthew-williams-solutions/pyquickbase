"""Apps and tables: labels instead of IDs, cached schema, and automatic paging."""

import difflib
from collections.abc import Callable, Iterator, Sequence
from typing import Any, Self

from .._models import RECORD_ID_FIELD, AppInfo, Field, Page, Record, Relationship, TableInfo, parse
from .._query import Expression, FieldRef, build_query_body
from .._schema import SchemaCache
from ..errors import InvalidResponseError, QueryError, UnknownFieldError, UnknownTableError
from ._transport import AsyncTransport


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
        info = parse(AppInfo.from_api, payload, "app")
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
            lambda rows: tuple(TableInfo.from_api(row) for row in rows), payload, "tables"
        )
        self._cache.set(key, tables)
        return list(tables)

    def table(self, name_or_id: str) -> "AsyncTable":
        """Get a table in this app by name or ID. It's checked when first used."""
        return AsyncTable(self._transport, self._cache, self, name_or_id)

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
        fields = parse(lambda rows: tuple(Field.from_api(row) for row in rows), payload, "fields")
        self._cache.set(key, fields)
        return list(fields)

    async def relationships(self, *, refresh: bool = False) -> list[Relationship]:
        """List the relationships this table is part of, as either parent or child."""
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
        rows = [Relationship.from_api(row) for row in payload["relationships"]]
        return rows, payload["metadata"]["totalRelationships"]

    async def _query_body(
        self,
        where: Expression | str | None,
        select: FieldRef | Sequence[FieldRef] | None,
        order_by: FieldRef | Sequence[FieldRef] | None,
    ) -> dict[str, Any]:
        table_id = (await self.info()).id
        try:
            fields = await self.fields()
            return build_query_body(table_id, fields, where=where, select=select, order_by=order_by)
        except UnknownFieldError:
            # The field may be newer than the cache, so look once more before giving up.
            fields = await self.fields(refresh=True)
            return build_query_body(table_id, fields, where=where, select=select, order_by=order_by)

    async def _fetch_page(self, body: dict[str, Any], top: int, skip: int) -> Page:
        payload = await self._transport.request(
            "POST", "/records/query", json={**body, "options": {"top": top, "skip": skip}}
        )
        return parse(Page.from_api, payload, "records query")

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

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._ref!r}, app={self._app.id!r})"
