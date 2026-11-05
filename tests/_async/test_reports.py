import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    InvalidResponseError,
    QueryError,
    ReportInfo,
    UnknownReportError,
    UnsupportedReportError,
    WriteRefusedError,
)

pytestmark = pytest.mark.anyio

APP = "bq1234567"
TASKS = "bqtasks"
RUN = "POST /v1/reports/1/run"


def report(id: str, name: str, type: str = "table", **extra: object) -> dict[str, object]:
    return {"id": id, "name": name, "type": type, "incomplete": False, **extra}


REPORTS = [
    report("1", "List All", description="Everything"),
    report("5", "Filtered"),
    report("9", "Filtered"),
    report("6", "Ask", incomplete=True),
    report("7", "Summary", "summary"),
    report("8", "Chart", "chart"),
    report("20", "7"),
]


class FakeReports:
    """Serves one table's reports, and rows that leave out Record ID# unless told otherwise."""

    def __init__(self, total: int = 5, with_record_id: bool = False) -> None:
        self.total = total
        self.with_record_id = with_record_id
        self.reports = list(REPORTS)
        self.requests: list[httpx.Request] = []

    def sent(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def run_params(self) -> list[dict[str, str]]:
        return [dict(r.url.params) for r in self.requests if r.url.path.endswith("/run")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks"}])
        if request.method == "DELETE":
            return httpx.Response(200, json={"deletedTableId": TASKS})
        if path == "/reports":
            return httpx.Response(200, json=self.reports)
        return self.run(request)

    def run(self, request: httpx.Request) -> httpx.Response:
        assert request.content == b""
        top = int(request.url.params["top"])
        skip = int(request.url.params["skip"])
        rows = range(skip, min(skip + top, self.total))
        fields = [{"id": 6, "label": "Title", "type": "text"}]
        if self.with_record_id:
            fields.append({"id": 3, "label": "Record ID#", "type": "recordid"})
        return httpx.Response(
            200,
            json={
                "fields": fields,
                "data": [
                    {"6": {"value": f"Item {n}"}}
                    | ({"3": {"value": n + 1}} if self.with_record_id else {})
                    for n in rows
                ],
                "metadata": {
                    "numFields": len(fields),
                    "numRecords": len(rows),
                    "skip": skip,
                    "top": top,
                    "totalRecords": self.total,
                },
            },
        )


def client(server: FakeReports, **kwargs: object) -> AsyncQuickbase:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return AsyncQuickbase(
        realm="acme",
        token="fake-user-token",
        http_client=http_client,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_reports_are_listed_and_cached() -> None:
    server = FakeReports()
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        reports = await table.reports()
        await table.reports()
        await table.reports(refresh=True)
    assert reports[0] == ReportInfo(
        id="1", name="List All", type="table", description="Everything", incomplete=False, raw={}
    )
    assert reports[0].raw["name"] == "List All"
    assert reports[3].incomplete
    assert server.sent().count("GET /v1/reports") == 2
    assert server.requests[1].url.params["tableId"] == TASKS


async def test_malformed_report_list_is_reported() -> None:
    server = FakeReports()
    server.reports = [{"name": "No ID"}]  # type: ignore[list-item]
    async with client(server) as qb:
        with pytest.raises(InvalidResponseError, match="reports"):
            await qb.app(APP).table("Tasks").reports()


async def test_report_is_found_by_name_or_id_without_a_call_until_used() -> None:
    server = FakeReports()
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        by_name = table.report("List All")
        by_id = table.report("1")
        assert server.requests == []
        assert await by_name.info() == await by_id.info()
        assert (await by_name.info()).id == "1"
        assert repr(by_name).startswith("AsyncReport('List All', table=AsyncTable('Tasks'")


async def test_an_id_takes_priority_over_a_name() -> None:
    async with client(FakeReports()) as qb:
        assert (await qb.app(APP).table("Tasks").report("7").info()).name == "Summary"


async def test_repeated_name_is_an_error_that_lists_the_ids() -> None:
    server = FakeReports()
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        with pytest.raises(UnknownReportError, match=r"Several reports.*\['5', '9'\]"):
            await table.report("Filtered").info()
        assert (await table.report("9").info()).id == "9"
    assert not server.run_params()


async def test_unknown_report_refreshes_once_then_suggests() -> None:
    server = FakeReports()
    async with client(server) as qb:
        with pytest.raises(UnknownReportError, match="Did you mean 'Filtered'"):
            await qb.app(APP).table("Tasks").report("Filterd").info()
    assert server.sent().count("GET /v1/reports") == 2


async def test_unknown_report_with_nothing_close_has_no_hint() -> None:
    async with client(FakeReports()) as qb:
        with pytest.raises(UnknownReportError, match=r"no report 'zzzz'\.$"):
            await qb.app(APP).table("Tasks").report("zzzz").info()


async def test_report_newer_than_the_cache_is_found() -> None:
    server = FakeReports()
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        await table.reports()
        server.reports = [*server.reports, report("30", "New")]
        assert (await table.report("New").info()).id == "30"


async def test_run_sends_paging_as_query_parameters_and_pages_through() -> None:
    server = FakeReports(total=5)
    async with client(server) as qb:
        records = [r async for r in qb.app(APP).table("Tasks").report("List All").run(page_size=2)]
    assert [r["Title"] for r in records] == [f"Item {n}" for n in range(5)]
    assert server.run_params() == [
        {"tableId": TASKS, "top": "2", "skip": "0"},
        {"tableId": TASKS, "top": "2", "skip": "2"},
        {"tableId": TASKS, "top": "2", "skip": "4"},
    ]
    assert server.sent().count(RUN) == 3


async def test_run_stops_at_the_limit() -> None:
    server = FakeReports(total=50)
    async with client(server) as qb:
        records = [
            r async for r in qb.app(APP).table("Tasks").report("1").run(page_size=2, limit=3)
        ]
    assert len(records) == 3
    assert server.run_params()[-1]["top"] == "1"


async def test_run_page() -> None:
    server = FakeReports(total=5)
    async with client(server) as qb:
        page = await qb.app(APP).table("Tasks").report("1").run_page(top=2, skip=1)
    assert [r["Title"] for r in page.records] == ["Item 1", "Item 2"]
    assert (page.total, page.next_skip, page.has_more) == (5, 3, True)
    assert server.run_params() == [{"tableId": TASKS, "top": "2", "skip": "1"}]


async def test_record_id_is_available_only_when_the_report_includes_it() -> None:
    async with client(FakeReports(with_record_id=True)) as qb:
        page = await qb.app(APP).table("Tasks").report("1").run_page(top=1)
        assert page.records[0].id == 1

    async with client(FakeReports()) as qb:
        page = await qb.app(APP).table("Tasks").report("1").run_page(top=1)
        assert repr(page.records[0]) == "Record(id=None, fields=1)"
        with pytest.raises(KeyError, match="no Record ID#"):
            _ = page.records[0].id


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("Summary", "summary report"),
        ("Chart", "chart report"),
        ("Ask", "incomplete"),
    ],
)
async def test_reports_that_arent_records_are_refused_without_running(
    ref: str, message: str
) -> None:
    server = FakeReports()
    async with client(server) as qb:
        report_ = qb.app(APP).table("Tasks").report(ref)
        with pytest.raises(UnsupportedReportError, match=message):
            await report_.run_page()
        with pytest.raises(UnsupportedReportError, match=message):
            async for _ in report_.run():
                pass  # pragma: no cover
        assert (await report_.info()).name == ref
    assert server.run_params() == []


@pytest.mark.parametrize(
    "kwargs", [{"top": 0}, {"top": -1}, {"skip": -1}], ids=["top0", "top-1", "skip-1"]
)
async def test_run_page_checks_arguments_before_sending(kwargs: dict[str, int]) -> None:
    server = FakeReports()
    async with client(server) as qb:
        with pytest.raises(QueryError):
            await qb.app(APP).table("Tasks").report("1").run_page(**kwargs)
    assert server.requests == []


@pytest.mark.parametrize("kwargs", [{"page_size": 0}, {"limit": 0}], ids=["page_size", "limit"])
async def test_run_checks_arguments_before_sending(kwargs: dict[str, int]) -> None:
    server = FakeReports()
    async with client(server) as qb:
        with pytest.raises(QueryError):
            qb.app(APP).table("Tasks").report("1").run(**kwargs)
    assert server.requests == []


async def test_malformed_run_response_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tables"):
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks"}])
        if request.url.path.endswith("/reports"):
            return httpx.Response(200, json=REPORTS)
        return httpx.Response(200, json={"data": []})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with AsyncQuickbase(realm="acme", token="fake-user-token", http_client=http_client) as qb:
        with pytest.raises(InvalidResponseError, match="report"):
            await qb.app(APP).table("Tasks").report("1").run_page()


async def test_read_only_client_can_run_reports_but_not_write() -> None:
    server = FakeReports()
    async with client(server, read_only=True) as qb:
        page = await qb.app(APP).table("Tasks").report("1").run_page()
        assert len(page.records) == 5
        with pytest.raises(WriteRefusedError):
            await qb.request("POST", "/records", json={})


async def test_dropping_a_table_forgets_its_reports() -> None:
    server = FakeReports()
    async with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        await table.reports()
        await table.drop(confirm="Tasks")
        await table.reports()
    assert server.sent().count("GET /v1/reports") == 2
