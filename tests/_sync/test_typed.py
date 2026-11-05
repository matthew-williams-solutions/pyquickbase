# Generated from the async code by scripts/generate_sync.py. Do not edit.
import json
import sys
import types
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from pyquickbase import (
    F,
    InvalidResponseError,
    InvalidValueError,
    QueryError,
    Quickbase,
    Record,
    Row,
    SchemaMismatchError,
    TypedPage,
    WriteError,
)
from pyquickbase._codegen import render
from pyquickbase._models import field_from_api, table_info_from_api
from pyquickbase._typed import schema_problems

APP = "bq1234567"
TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 7, "label": "Due Date", "fieldType": "date"},
    {"id": 8, "label": "Total", "fieldType": "numeric", "mode": "formula"},
]


def generated() -> Any:
    source = render(
        [(table_info_from_api({"id": TASKS, "name": "Tasks"}), [field_from_api(f) for f in FIELDS])]
    )
    module = types.ModuleType("generated_for_typed")
    sys.modules[module.__name__] = module
    exec(compile(source, module.__name__, "exec"), module.__dict__)
    return module.Tasks


Tasks = generated()


class Server:
    """Serves a table of `total` records, and records every request."""

    def __init__(self, total: int = 3) -> None:
        self.total = total
        self.fields = list(FIELDS)
        self.requests: list[httpx.Request] = []
        self.row_error = False

    def sent(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == f"/v1{path}"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks"}])
        if path == "/fields":
            return httpx.Response(200, json=self.fields)
        if path == "/records/query":
            return self.query(json.loads(request.content))
        if request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": 2})
        metadata: dict[str, Any] = {
            "createdRecordIds": [1],
            "unchangedRecordIds": [],
            "updatedRecordIds": [],
        }
        if self.row_error:
            metadata["lineErrors"] = {"1": ["Bad"]}
        return httpx.Response(200, json={"data": [], "metadata": metadata})

    def query(self, body: dict[str, Any]) -> httpx.Response:
        top, skip = body["options"]["top"], body["options"]["skip"]
        rows = range(skip + 1, min(skip + top, self.total) + 1)
        return httpx.Response(
            200,
            json={
                "fields": [
                    {"id": 3, "label": "Record ID#", "type": "recordid"},
                    {"id": 6, "label": "Title", "type": "text"},
                    {"id": 7, "label": "Due Date", "type": "date"},
                    {"id": 8, "label": "Total", "type": "numeric"},
                ],
                "data": [
                    {
                        "3": {"value": n},
                        "6": {"value": f"Item {n}"},
                        "7": {"value": "2027-01-02"},
                        "8": {"value": None},
                    }
                    for n in rows
                ],
                "metadata": {"totalRecords": self.total, "skip": skip},
            },
        )


def client(server: Any, **kwargs: Any) -> Quickbase:
    http_client = httpx.Client(transport=httpx.MockTransport(server))
    return Quickbase(realm="acme", token="fake-user-token", http_client=http_client, **kwargs)


def test_query_returns_instances_of_the_class() -> None:
    server = Server(total=3)
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        assert repr(tasks) == "TypedTable(Tasks)"
        found = [
            task
            for task in tasks.query(
                where=Tasks.fields.title == "Item",
                order_by=Tasks.fields.due_date.desc,
                page_size=2,
            )
        ]
    assert [task.title for task in found] == ["Item 1", "Item 2", "Item 3"]
    assert found[0] == Tasks(id=1, title="Item 1", due_date=date(2027, 1, 2), total=None)
    body = server.sent("/records/query")[0]
    assert body["select"] == [3, 6, 7, 8]
    assert body["where"] == "{6.EX.'Item'}"
    assert body["sortBy"][0] == {"fieldId": 7, "order": "DESC"}


def test_query_stops_at_the_limit() -> None:
    with client(Server(total=50)) as qb:
        found = [t for t in qb.app(APP).typed(Tasks).query(limit=2)]
    assert len(found) == 2


def test_query_checks_arguments_before_sending() -> None:
    server = Server()
    with client(server) as qb:
        with pytest.raises(QueryError):
            qb.app(APP).typed(Tasks).query(limit=0)
    assert server.requests == []


def test_insert_update_and_upsert_send_rows_by_id() -> None:
    server = Server()
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        assert (tasks.insert([Tasks.new(title="New")])).created_ids == (1,)
        tasks.update([Tasks.change(7, title=None)])
        tasks.upsert([Tasks.new(title="Up")], merge_on=Tasks.fields.title)
    insert, update, upsert = server.sent("/records")
    assert insert["data"] == [{"6": {"value": "New"}}]
    assert "mergeFieldId" not in insert
    assert update["data"] == [{"3": {"value": 7}, "6": {"value": ""}}]
    assert update["mergeFieldId"] == 3
    assert upsert["mergeFieldId"] == 6


def test_write_errors_still_reach_the_caller() -> None:
    server = Server()
    server.row_error = True
    with client(server) as qb:
        with pytest.raises(WriteError):
            qb.app(APP).typed(Tasks).insert([Tasks.new(title="New")])


def test_count_and_delete() -> None:
    server = Server(total=3)
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        assert tasks.count() == 3
        assert tasks.count(Tasks.fields.title == "x") == 3
        assert tasks.delete(Tasks.fields.title == "x") == 2
        assert tasks.table.count() == 3


def test_schema_is_checked_once_on_first_use() -> None:
    server = Server()
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        tasks.count()
        tasks.count()
        assert [task for task in tasks.query(limit=1)]
    assert [r.url.path for r in server.requests].count("/v1/fields") == 1


def test_first_use_trusts_the_cache_and_misses_a_newer_change() -> None:
    server = Server()
    with client(server) as qb:
        qb.app(APP).table("Tasks").fields()
        server.fields = FIELDS[:1]
        qb.app(APP).typed(Tasks).count()


def test_check_schema_always_looks_again() -> None:
    server = Server()
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        tasks.check_schema()
        tasks.check_schema()
        assert [r.url.path for r in server.requests].count("/v1/fields") == 2
        server.fields = FIELDS[:1]
        with pytest.raises(SchemaMismatchError, match="no longer exists"):
            tasks.check_schema()
        # A failed check isn't forgotten by later calls, which use the refreshed cache.
        with pytest.raises(SchemaMismatchError, match="no longer exists"):
            tasks.count()
        server.fields = list(FIELDS)
        tasks.check_schema()
        tasks.count()


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (FIELDS[:1] + FIELDS[2:], "title .field 6. no longer exists"),
        (
            [FIELDS[0], {**FIELDS[1], "fieldType": "numeric"}, FIELDS[2], FIELDS[3]],
            "title was text and is now numeric",
        ),
        (
            [FIELDS[0], {**FIELDS[1], "mode": "formula"}, FIELDS[2], FIELDS[3]],
            "title is now read-only",
        ),
    ],
    ids=["deleted", "type", "read-only"],
)
def test_a_changed_table_refuses_every_use(fields: list[Any], message: str) -> None:
    server = Server()
    server.fields = fields
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        with pytest.raises(SchemaMismatchError, match=message):
            tasks.count()
        with pytest.raises(SchemaMismatchError, match=message):
            tasks.insert([Tasks.new(title="x")])
        with pytest.raises(SchemaMismatchError, match=message):
            tasks.update([Tasks.change(1, title="x")])
        with pytest.raises(SchemaMismatchError, match=message):
            tasks.upsert([Tasks.new(title="x")], merge_on=Tasks.fields.title)
        with pytest.raises(SchemaMismatchError, match=message):
            tasks.delete(Tasks.fields.title == "x")
        with pytest.raises(SchemaMismatchError, match=message):
            for _ in tasks.query():
                pass  # pragma: no cover
    assert server.sent("/records") == []
    assert server.sent("/records/query") == []


def test_an_old_cache_doesnt_cause_a_false_alarm() -> None:
    server = Server()
    server.fields = FIELDS[:1]
    with client(server) as qb:
        qb.app(APP).table("Tasks").fields()
        server.fields = list(FIELDS)
        qb.app(APP).typed(Tasks).count()


def test_a_read_only_client_reads_but_cant_write() -> None:
    from pyquickbase import WriteRefusedError

    server = Server()
    with client(server, read_only=True) as qb:
        tasks = qb.app(APP).typed(Tasks)
        assert len([t for t in tasks.query()]) == 3
        with pytest.raises(WriteRefusedError):
            tasks.insert([Tasks.new(title="x")])


def test_passing_a_check_means_later_uses_dont_check_again() -> None:
    # With no cache, every check is visible as a request for the table's fields.
    server = Server()

    def fetches() -> int:
        return [r.url.path for r in server.requests].count("/v1/fields")

    with client(server, schema_ttl=0) as qb:
        tasks = qb.app(APP).typed(Tasks)
        tasks.count()
        first = fetches()
        tasks.count()
        second = fetches() - first
        assert second < first
        tasks.check_schema()
        after_check = fetches()
        tasks.count()
        assert fetches() - after_check == second


MONEY_FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Price", "fieldType": "currency"},
    {"id": 7, "label": "Cents", "fieldType": "numeric", "properties": {"decimalPlaces": 0}},
    {"id": 8, "label": "Parent", "fieldType": "numeric", "properties": {"foreignKey": True}},
    {"id": 9, "label": "Ratio", "fieldType": "numeric"},
]


def money_class() -> Any:
    source = render(
        [
            (
                table_info_from_api({"id": TASKS, "name": "Tasks"}),
                [field_from_api(f) for f in MONEY_FIELDS],
            )
        ]
    )
    module = types.ModuleType("generated_for_money")
    sys.modules[module.__name__] = module
    exec(compile(source, module.__name__, "exec"), module.__dict__)
    return module.Tasks


def money_record(**values: Any) -> Any:
    base = {"Record ID#": 1, "Price": 19.99, "Cents": 1999.0, "Parent": 4.0, "Ratio": 1.5, **values}
    labels = {"Record ID#": 3, "Price": 6, "Cents": 7, "Parent": 8, "Ratio": 9}
    return Record(
        {labels[label]: value for label, value in base.items()},
        {field_id: label for label, field_id in labels.items()},
    )


def test_money_reads_exactly_and_whole_numbers_read_as_int() -> None:
    record = money_class().from_record(money_record())
    assert record.price == Decimal("19.99")
    assert isinstance(record.price, Decimal)
    assert record.cents == 1999
    assert isinstance(record.cents, int)
    assert isinstance(record.parent, int)
    assert record.ratio == 1.5


def test_empty_numbers_stay_none() -> None:
    record = money_class().from_record(money_record(Price=None, Cents=None, Parent=None))
    assert (record.price, record.cents, record.parent) == (None, None, None)


def test_a_fraction_in_a_whole_number_field_raises_without_showing_it() -> None:
    with pytest.raises(InvalidResponseError) as caught:
        money_class().from_record(money_record(Cents=1999.5))
    assert "Cents" in str(caught.value)
    assert "1999.5" not in str(caught.value)


def test_a_class_notices_a_field_no_longer_whole() -> None:
    model = money_class()
    current = [field_from_api(f) for f in MONEY_FIELDS]
    assert schema_problems(model, current) == []
    changed = [
        field_from_api({**f, "properties": {"decimalPlaces": 2}} if f["id"] == 7 else f)
        for f in MONEY_FIELDS
    ]
    assert "cents is no longer read as int" in schema_problems(model, changed)[0]


def test_decimals_can_be_written() -> None:
    row = money_class().new(price=Decimal("19.99"), cents=1999, parent=4)
    assert row == {6: Decimal("19.99"), 7: 1999, 8: 4}


def single_record_client(rows: list[dict[str, Any]]) -> Quickbase:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks"}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        return httpx.Response(
            200,
            json={
                "data": rows,
                "fields": [
                    {"id": f["id"], "label": f["label"], "type": f["fieldType"]} for f in FIELDS
                ],
                "metadata": {"numFields": 4, "numRecords": len(rows), "skip": 0,
                             "totalRecords": len(rows), "top": 1},
            },
        )  # fmt: skip

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return Quickbase(realm="acme", token="t", http_client=http)


def test_first_and_get_return_an_instance_or_none() -> None:
    model = generated()
    row = {"3": {"value": 5}, "6": {"value": "A"}, "7": {"value": ""}, "8": {"value": 1.5}}
    with single_record_client([row]) as qb:
        tasks = qb.app(APP).typed(model)
        first = tasks.first(where=model.fields.title == "A", order_by=model.fields.title)
        got = tasks.get(5)
    assert isinstance(first, model)
    assert first.title == "A"
    assert isinstance(got, model)
    assert got.id == 5
    with single_record_client([]) as qb:
        tasks = qb.app(APP).typed(model)
        assert tasks.first() is None
        assert tasks.get(5) is None


def test_query_page_returns_instances_and_paging_details() -> None:
    server = Server(total=5)
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        page = tasks.query_page(
            where=Tasks.fields.title == "Item", order_by=Tasks.fields.due_date.desc, top=2, skip=1
        )
    assert isinstance(page, TypedPage)
    assert [task.id for task in page.records] == [2, 3]
    assert page.records[0] == Tasks(id=2, title="Item 2", due_date=date(2027, 1, 2), total=None)
    assert (page.total, page.skip, page.has_more, page.next_skip) == (5, 1, True, 3)
    body = server.sent("/records/query")[0]
    assert body["select"] == [3, 6, 7, 8]
    assert body["where"] == "{6.EX.'Item'}"
    assert body["sortBy"][0] == {"fieldId": 7, "order": "DESC"}
    assert body["options"] == {"top": 2, "skip": 1}


def test_query_page_defaults_and_last_page() -> None:
    server = Server(total=3)
    with client(server) as qb:
        page = qb.app(APP).typed(Tasks).query_page()
    assert server.sent("/records/query")[0]["options"] == {"top": 100, "skip": 0}
    assert (len(page.records), page.has_more, page.next_skip) == (3, False, 3)


@pytest.mark.parametrize(
    "order_by",
    [
        lambda: Tasks.fields.title,
        lambda: Tasks.fields.title.id,
        lambda: Tasks.fields.title.desc,
        lambda: [Tasks.fields.title.desc, Tasks.fields.due_date],
    ],
)
def test_query_page_takes_what_query_takes_for_order_by(order_by: Any) -> None:
    server = Server(total=1)
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        tasks.query_page(order_by=order_by())
        [_ for _ in tasks.query(order_by=order_by())]
    first, second = server.sent("/records/query")
    assert first["sortBy"] == second["sortBy"]


def test_query_page_checks_the_schema_first() -> None:
    fields = [field for field in FIELDS if field["label"] != "Title"]
    server = Server(total=1)
    server.fields = fields
    with client(server) as qb:
        with pytest.raises(SchemaMismatchError):
            qb.app(APP).typed(Tasks).query_page()
    assert not server.sent("/records/query")


@pytest.mark.parametrize(("top", "skip"), [(0, 0), (1, -1)])
def test_query_page_refuses_bad_paging_before_querying(top: int, skip: int) -> None:
    server = Server(total=1)
    with client(server) as qb:
        with pytest.raises(QueryError):
            qb.app(APP).typed(Tasks).query_page(top=top, skip=skip)
    assert not server.sent("/records/query")


def test_typed_fields_work_on_the_untyped_table() -> None:
    server = Server(total=1)
    title = Tasks.fields.title
    with client(server) as qb:
        table = qb.app(APP).typed(Tasks).table
        table.query_page(order_by=title, select=title)
        table.query_page(order_by=[title, Tasks.fields.due_date.desc], select=[title])
        table.first(order_by=title)
        [_ for _ in table.query(order_by=title)]
    single, several, first, iterated = server.sent("/records/query")
    assert single["sortBy"] == [{"fieldId": 6, "order": "ASC"}, {"fieldId": 3, "order": "ASC"}]
    assert single["select"] == [3, 6]
    assert several["sortBy"][:2] == [
        {"fieldId": 6, "order": "ASC"},
        {"fieldId": 7, "order": "DESC"},
    ]
    assert first["sortBy"] == iterated["sortBy"] == single["sortBy"]


@pytest.mark.parametrize(
    "order_by",
    ["Title", ["Title"], ["-Title"], F("Title"), [F("Title")], 1.5, [1.5], True, [None]],
    ids=repr,
)
def test_typed_order_by_takes_only_fields_of_the_class_and_ids(order_by: Any) -> None:
    server = Server(total=1)
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        with pytest.raises(QueryError, match=r"Tasks\.fields\.title"):
            tasks.query(order_by=order_by)
        with pytest.raises(QueryError, match=r"Tasks\.fields\.title"):
            tasks.query_page(order_by=order_by)
        with pytest.raises(QueryError, match=r"Tasks\.fields\.title"):
            tasks.first(order_by=order_by)
    assert not server.sent("/records/query")


def test_typed_order_by_takes_any_iterable_of_fields_and_ids() -> None:
    server = Server(total=1)
    title = Tasks.fields.title
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        tasks.query_page(order_by=(title, -7))
        tasks.query_page(order_by=iter([title, -7]))  # pyright: ignore[reportArgumentType]
    first, second = server.sent("/records/query")
    assert first["sortBy"] == second["sortBy"]
    assert first["sortBy"][:2] == [
        {"fieldId": 6, "order": "ASC"},
        {"fieldId": 7, "order": "DESC"},
    ]


class OtherTable(Tasks):
    """A class for another table, whose field IDs mean something else there."""

    __slots__ = ()
    __table_id__ = "bqother"


def test_a_row_for_another_table_is_refused_before_sending() -> None:
    server = Server()
    row = OtherTable.new(title="Wrong table")
    assert row.table_id == "bqother"
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        title = Tasks.fields.title
        for call in (
            lambda: tasks.insert([Tasks.new(title="Fine"), row]),
            lambda: tasks.update([row]),
            lambda: tasks.upsert([row], merge_on=title),
        ):
            with pytest.raises(InvalidValueError, match=r"Row [01] "):
                call()
    assert not server.sent("/records")
    assert not server.requests


def test_rows_without_a_table_and_from_the_same_table_are_accepted() -> None:
    server = Server()
    same_table = type("Regenerated", (Tasks,), {"__slots__": ()})
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        tasks.insert([same_table.new(title="A"), Row({6: "B"})])
    assert len(server.sent("/records")[0]["data"]) == 2


def test_batch_size_reaches_the_writes() -> None:
    server = Server()
    rows = [Tasks.new(title=f"T{n}") for n in range(3)]
    with client(server) as qb:
        tasks = qb.app(APP).typed(Tasks)
        tasks.insert(rows, batch_size=2)
        assert len(server.sent("/records")) == 2
        tasks.update([Tasks.change(n + 1, title="x") for n in range(3)], batch_size=1)
        assert len(server.sent("/records")) == 5
        tasks.upsert(rows, merge_on=Tasks.fields.title, batch_size=3)
        assert len(server.sent("/records")) == 6
        with pytest.raises(QueryError):
            tasks.insert(rows, batch_size=0)
