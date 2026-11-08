"""Summaries and summary reports through a client, which page and only read."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    F,
    InvalidResponseError,
    Policy,
    PolicyRefusedError,
    QueryError,
    TooManyRecordsError,
    UnknownFieldError,
    UnknownReportError,
    UnsupportedReportError,
    allow,
    where_from_dict,
)

from ..policy_helpers import bind

pytestmark = pytest.mark.anyio

TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Status", "fieldType": "text", "appearsByDefault": True},
    {"id": 7, "label": "Hours", "fieldType": "numeric", "appearsByDefault": True},
    {"id": 8, "label": "Notes", "fieldType": "text", "appearsByDefault": True},
]
RESPONSE_FIELDS = [{"id": f["id"], "label": f["label"], "type": f["fieldType"]} for f in FIELDS]
REPORTS = [
    {"id": "1", "name": "List All", "type": "table", "properties": {}},
    {"id": "5", "name": "By Status", "type": "summary", "properties": {}},
    {"id": "6", "name": "Chart", "type": "chart", "properties": {}},
    {"id": "7", "name": "Asks", "type": "summary", "properties": {}, "incomplete": True},
    {"id": "8", "name": "Doubled", "type": "summary", "properties": {}},
    {"id": "9", "name": "Short", "type": "summary", "properties": {}},
]


class Fake:
    def __init__(self, total: int = 0) -> None:
        self.total = total
        self.queries: list[dict[str, Any]] = []
        self.runs: list[tuple[str, dict[str, str]]] = []
        self.writes: list[str] = []

    def _run(self, request: httpx.Request, report: str) -> httpx.Response:
        params = dict(request.url.params)
        self.runs.append((report, params))
        top, skip = int(params["top"]), int(params["skip"])
        fields = [{"id": 2, "label": "Status", "type": "text"}]
        total = self.total
        if report == "8":
            fields = [*fields, {"id": 3, "label": "Status", "type": "text"}]
        else:
            fields = [*fields, {"id": 3, "label": "Hours (tot)", "type": "numeric"}]
        rows = [
            {"2": {"value": f"s{n}"}, "3": {"value": n}}
            for n in range(skip, min(skip + top, total))
        ]
        if report == "9" and skip:
            rows = []
        return httpx.Response(
            200,
            json={
                "data": rows,
                "fields": fields,
                "metadata": {
                    "totalRecords": total,
                    "numRecords": len(rows),
                    "skip": skip,
                    "numFields": 2,
                },
            },
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": 3}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        if path == "/reports":
            return httpx.Response(200, json=REPORTS)
        if path.startswith("/reports/"):
            return self._run(request, path.split("/")[2])
        if path == "/records/query":
            body = json.loads(request.content)
            self.queries.append(body)
            top, skip = body["options"]["top"], body["options"]["skip"]
            rows = [
                {"3": {"value": n}, "6": {"value": "a" if n % 2 else "b"}, "7": {"value": n}}
                for n in range(skip, min(skip + top, self.total))
            ]
            return httpx.Response(
                200,
                json={
                    "data": rows,
                    "fields": RESPONSE_FIELDS,
                    "metadata": {
                        "totalRecords": self.total,
                        "numRecords": len(rows),
                        "skip": skip,
                        "numFields": 3,
                    },
                },
            )
        self.writes.append(f"{request.method} {path}")
        return httpx.Response(500)


def client(fake: Fake, **kwargs: Any) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, **kwargs)


async def test_it_reads_every_page_and_only_the_fields_it_needs() -> None:
    fake = Fake(total=25)
    async with client(fake, policy=Policy.read_only()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        rows = await tasks.summarize("Status", sum="Hours", page_size=10)
    assert [query["options"]["skip"] for query in fake.queries] == [0, 10, 20]
    assert fake.queries[0]["select"] == [3, 6, 7]
    assert [(row.group["Status"], row.count) for row in rows] == [("a", 12), ("b", 13)]
    assert sum(row.sum["Hours"] for row in rows) == sum(range(25))
    assert fake.writes == []


async def test_it_takes_any_kind_of_filter() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        await tasks.summarize(where=F("Status") == "x")
        await tasks.summarize(where=where_from_dict({"field": "Status", "op": "==", "value": "y"}))
        await tasks.summarize(where="{6.EX.'z'}")
    assert [query["where"] for query in fake.queries] == [
        "{6.EX.'x'}",
        "{6.EX.'y'}",
        "{6.EX.'z'}",
    ]


async def test_max_records_refuses_without_reading_records() -> None:
    fake = Fake(total=11)
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(QueryError, match=r"11 records match.*max_records_scanned of 10"):
            await tasks.summarize("Status", max_records_scanned=10, page_size=5)
    assert len(fake.queries) == 1
    assert fake.queries[0]["options"]["top"] == 1


async def test_max_records_allows_exactly_that_many() -> None:
    fake = Fake(total=10)
    async with client(fake) as qb:
        rows = await qb.app("bq1234567").table("Tasks").summarize("Status", max_records_scanned=10)
    assert sum(row.count for row in rows) == 10


async def test_too_many_records_is_a_query_error_that_says_how_many() -> None:
    fake = Fake(total=11)
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(TooManyRecordsError) as caught:
            await tasks.summarize(sum="Hours", max_records_scanned=10)
    assert isinstance(caught.value, QueryError)
    assert (caught.value.matched, caught.value.limit) == (11, 10)
    assert len(fake.queries) == 1


async def test_a_count_asks_how_many_match_and_reads_no_records() -> None:
    fake = Fake(total=250_000)
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        rows = await tasks.summarize(where=F("Status") == "a", max_records_scanned=10)
    assert [(row.group, row.count) for row in rows] == [({}, 250_000)]
    [query] = fake.queries
    assert (query["select"], query["options"]["top"], query["where"]) == ([3], 1, "{6.EX.'a'}")


async def test_a_count_isnt_limited_by_the_read_rules_limit() -> None:
    fake = Fake(total=50)
    rules = bind(allow.read(TASKS, max_records_scanned=10), tables={TASKS: ("Tasks", FIELDS)})
    async with client(fake, policy=rules) as qb:
        rows = await qb.app("bq1234567").table("Tasks").summarize()
    assert rows[0].count == 50
    assert len(fake.queries) == 1


async def test_a_count_matches_the_groups_it_would_add_up_to() -> None:
    fake = Fake(total=25)
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        [counted] = await tasks.summarize()
        grouped = await tasks.summarize("Status", page_size=4)
    assert counted.count == sum(row.count for row in grouped) == 25


async def test_pages_are_sized_by_the_fields_a_summary_reads() -> None:
    fake = Fake(total=40_000)
    async with client(fake) as qb:
        await qb.app("bq1234567").table("Tasks").summarize("Status", sum="Hours")
    assert [query["options"]["top"] for query in fake.queries] == [16_666] * 3
    assert [query["options"]["skip"] for query in fake.queries] == [0, 16_666, 33_332]


class Growing(Fake):
    """Gains records after the first query, the count, as a table in use can."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        if request.url.path.endswith("/records/query"):
            self.total = 12
        return response


async def test_records_added_past_the_read_rules_limit_refuse_rather_than_cut_short() -> None:
    fake = Growing(total=10)
    rules = bind(allow.read(TASKS, max_records_scanned=10), tables={TASKS: ("Tasks", FIELDS)})
    async with client(fake, policy=rules) as qb:
        with pytest.raises(PolicyRefusedError, match="no partial result"):
            await qb.app("bq1234567").table("Tasks").summarize("Status", page_size=5)
    assert [query["options"]["top"] for query in fake.queries] == [1, 5, 5]


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"max_records_scanned": 0}, QueryError),
        ({"page_size": 0}, QueryError),
        ({"sum": "Status"}, QueryError),
        ({"group_by": "Nope"}, UnknownFieldError),
        ({"where": "{6.ex.'x'}"}, QueryError),
    ],
)
async def test_bad_arguments_raise_without_reading_records(options: Any, error: Any) -> None:
    fake = Fake(total=5)
    async with client(fake) as qb:
        with pytest.raises(error):
            await qb.app("bq1234567").table("Tasks").summarize(**options)
    assert fake.queries == []


# Summary and chart reports


@pytest.mark.parametrize("report", ["By Status", "Chart", "5", "6"])
async def test_rows_are_keyed_by_column_label(report: str) -> None:
    fake = Fake(total=2)
    async with client(fake, policy=Policy.read_only()) as qb:
        rows = await qb.app("bq1234567").table("Tasks").report(report).summary_rows()
    assert rows == [{"Status": "s0", "Hours (tot)": 0}, {"Status": "s1", "Hours (tot)": 1}]
    assert fake.writes == []


async def test_rows_page_through_query_parameters() -> None:
    fake = Fake(total=25)
    async with client(fake) as qb:
        report = qb.app("bq1234567").table("Tasks").report("By Status")
        rows = await report.summary_rows(page_size=10)
    assert len(rows) == 25
    assert [params["skip"] for _, params in fake.runs] == ["0", "10", "20"]
    assert all(params["tableId"] == TASKS for _, params in fake.runs)


async def test_rows_can_stop_at_a_limit() -> None:
    fake = Fake(total=25)
    async with client(fake) as qb:
        report = qb.app("bq1234567").table("Tasks").report("By Status")
        rows = await report.summary_rows(page_size=10, max_total=15)
    assert len(rows) == 15
    assert [params["top"] for _, params in fake.runs] == ["10", "5"]


async def test_a_report_with_no_rows_is_empty() -> None:
    async with client(Fake()) as qb:
        assert await qb.app("bq1234567").table("Tasks").report("Chart").summary_rows() == []


@pytest.mark.parametrize(
    ("report", "error", "message"),
    [
        ("List All", UnsupportedReportError, r"Use run\(\)"),
        ("Asks", UnsupportedReportError, "incomplete"),
        ("Doubled", UnsupportedReportError, "same label"),
        ("Nope", UnknownReportError, "Nope"),
    ],
)
async def test_reports_it_cant_read(report: str, error: Any, message: str) -> None:
    fake = Fake(total=2)
    async with client(fake) as qb:
        with pytest.raises(error, match=message):
            await qb.app("bq1234567").table("Tasks").report(report).summary_rows()


async def test_a_short_response_raises_rather_than_looping() -> None:
    fake = Fake(total=25)
    async with client(fake) as qb:
        report = qb.app("bq1234567").table("Tasks").report("Short")
        with pytest.raises(InvalidResponseError, match="empty page"):
            await report.summary_rows(page_size=10)


@pytest.mark.parametrize("options", [{"page_size": 0}, {"max_total": 0}, {"page_size": -1}])
async def test_bad_paging_is_refused(options: Any) -> None:
    fake = Fake()
    async with client(fake) as qb:
        with pytest.raises(QueryError):
            await qb.app("bq1234567").table("Tasks").report("Chart").summary_rows(**options)
    assert fake.runs == []


async def test_run_points_to_summary_rows() -> None:
    async with client(Fake()) as qb:
        report = qb.app("bq1234567").table("Tasks").report("By Status")
        with pytest.raises(UnsupportedReportError, match=r"summary_rows\(\)"):
            await report.run_page()
