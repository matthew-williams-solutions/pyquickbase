"""Operations used through a client, so each one reaches Quickbase the way its method does."""

import base64
import json
import time
from typing import Any

import httpx
import pytest

from pyquickbase import (
    AsyncQuickbase,
    ConfigurationError,
    ConfirmationError,
    F,
    FileDownload,
    InvalidValueError,
    Page,
    Policy,
    PolicyRefusedError,
    QueryError,
    SummaryRow,
    UnknownReportError,
    UpdatePreview,
    WriteResult,
    allow,
    tool_result,
)

from ..policy_helpers import bind

pytestmark = pytest.mark.anyio

TASKS = "bqtasks"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid", "appearsByDefault": True},
    {"id": 6, "label": "Title", "fieldType": "text", "appearsByDefault": True},
    {"id": 7, "label": "Status", "fieldType": "text", "appearsByDefault": True},
    {"id": 8, "label": "Hours", "fieldType": "numeric", "appearsByDefault": True},
    {"id": 9, "label": "Project", "fieldType": "text"},
    {"id": 13, "label": "Attachment", "fieldType": "file"},
]
REPORTS = [
    {"id": "1", "name": "List All", "type": "table"},
    {"id": "5", "name": "Totals", "type": "summary"},
]


class Fake:
    """Answers like Quickbase, and records what was sent, with each request's body."""

    def __init__(self, matching: int = 1) -> None:
        self.matching = matching
        self.sent: list[tuple[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        self.sent.append((f"{request.method} {path}", body))
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": 3}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        if path == "/reports":
            return httpx.Response(200, json=REPORTS)
        if path == "/records/query":
            select = body["select"]
            row = {str(fid): {"value": 1 if fid in (3, 8) else "x"} for fid in select}
            return httpx.Response(
                200,
                json={
                    "fields": [{"id": fid, "label": f"F{fid}", "type": "text"} for fid in select],
                    "data": [row] if self.matching else [],
                    "metadata": {"totalRecords": self.matching, "numRecords": 1, "skip": 0},
                },
            )
        if path.startswith("/reports/"):
            return httpx.Response(
                200,
                json={
                    "fields": [{"id": 3, "label": "Record ID#", "type": "recordid"}],
                    "data": [{"3": {"value": 1}}],
                    "metadata": {"totalRecords": 1, "numRecords": 1, "skip": 0},
                },
            )
        if path == "/formula/run":
            return httpx.Response(200, json={"result": "8", "resultType": "number"})
        if path.startswith("/files/") and request.method == "GET":
            return httpx.Response(
                200,
                content=base64.b64encode(b"notes"),
                headers={"content-disposition": "attachment; filename*=utf-8''a.txt"},
            )
        if path.startswith("/files/"):
            return httpx.Response(200, json={})
        if path == "/records" and request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": 1})
        metadata = {"createdRecordIds": [2], "updatedRecordIds": [], "unchangedRecordIds": []}
        if body and any("3" in row for row in body["data"]):
            metadata = {"createdRecordIds": [], "updatedRecordIds": [1], "unchangedRecordIds": []}
        return httpx.Response(200, json={"metadata": metadata})

    def requests(self, name: str) -> list[Any]:
        return [body for sent, body in self.sent if sent == name]


def bound(*rules: Any) -> Any:
    return bind(*rules, tables={TASKS: ("Tasks", FIELDS)}, app_id="bq1234567")


def client(fake: Fake, **kwargs: Any) -> AsyncQuickbase:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return AsyncQuickbase(realm="acme", token="t", http_client=http, **kwargs)


OPEN = {"field": "Status", "op": "==", "value": "Open"}


async def test_query_runs_a_page_query_within_its_scope() -> None:
    fake = Fake()
    async with client(fake, policy=Policy.read_only()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        search = await tasks.operation("query", fields=["Title", "Status"])
        assert search.name == "query"
        page = await search.run(
            {"where": OPEN, "select": ["Title"], "order_by": [{"field": "Title"}], "limit": 5},
            scope=F("Project") == "Alpha",
        )
    assert isinstance(page, Page)
    (body,) = fake.requests("POST /records/query")
    assert body["select"] == [3, 6]
    assert body["where"] == "{9.EX.'Alpha'}AND{7.EX.'Open'}"
    assert body["sortBy"][0] == {"fieldId": 6, "order": "ASC"}
    assert body["options"] == {"top": 5, "skip": 0}
    assert tool_result(page)["page"]["total"] == 1


async def test_the_schema_is_a_new_dictionary_each_time() -> None:
    async with client(Fake()) as qb:
        search = await qb.app("bq1234567").table("Tasks").operation("query")
    search.schema["properties"].clear()
    assert search.schema["properties"]


async def test_count_summarize_and_delete() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert await (await tasks.operation("count")).run({"where": OPEN}) == 1
        rows = await (await tasks.operation("summarize")).run({"group_by": ["Status"]})
        assert all(isinstance(row, SummaryRow) for row in rows)
        delete = await tasks.operation("delete")
        assert await delete.run({"where": OPEN}, scope=F("Project") == "Alpha") == 1
    (deleted,) = fake.requests("DELETE /records")
    assert deleted["where"] == "{9.EX.'Alpha'}AND{7.EX.'Open'}"


async def test_insert_and_update() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        insert = await tasks.operation("insert", fields=["Title", "Hours"])
        result = await insert.run({"records": [{"Title": "Pour slab", "Hours": 8}]})
        assert isinstance(result, WriteResult)
        assert result.created_ids == (2,)
        update = await tasks.operation("update", fields=["Status"])
        assert (
            await update.run({"records": [{"Record ID#": 1, "Status": "Done"}]})
        ).updated_ids == (1,)
    first, second = fake.requests("POST /records")
    assert first["data"] == [{"6": {"value": "Pour slab"}, "8": {"value": 8}}]
    assert second["data"] == [{"3": {"value": 1}, "7": {"value": "Done"}}]


async def test_a_scope_is_checked_before_a_record_is_named() -> None:
    fake = Fake(matching=0)
    async with client(fake) as qb:
        update = await qb.app("bq1234567").table("Tasks").operation("update")
        with pytest.raises(QueryError, match="aren't ones this tool may use"):
            await update.run(
                {"records": [{"Record ID#": 1, "Status": "Done"}]}, scope=F("Project") == "Alpha"
            )
    assert fake.requests("POST /records") == []
    (check,) = fake.requests("POST /records/query")
    assert check["where"] == "{9.EX.'Alpha'}AND{3.EX.'1'}"


async def test_a_scope_that_matches_lets_the_call_run() -> None:
    fake = Fake(matching=1)
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = await (await tasks.operation("preview_update")).run(
            {"records": [{"Record ID#": 1, "Status": "Done"}]}, scope=F("Project") == "Alpha"
        )
        assert isinstance(preview, UpdatePreview)
        formula = await tasks.operation("run_formula", formula="[Hours] * 2")
        assert (await formula.run({"record_id": 1}, scope=F("Project") == "Alpha")).value == 8
        download = await tasks.operation("download_file")
        found = await download.run({"record_id": 1, "field": "Attachment"})
        assert isinstance(found, FileDownload)
        assert found.data == b"notes"
        delete = await tasks.operation("delete_file_version")
        await delete.run({"record_id": 1, "field": "Attachment", "version": 1})
    (formula_body,) = fake.requests("POST /formula/run")
    assert formula_body == {"formula": "[Hours] * 2", "from": TASKS, "rid": 1}
    assert [name for name, _ in fake.sent if name.startswith(("GET /files", "DELETE /files"))] == [
        "GET /files/bqtasks/1/13/0",
        "DELETE /files/bqtasks/1/13/1",
    ]


async def test_insert_fills_in_an_equality_scope() -> None:
    fake = Fake()
    async with client(fake) as qb:
        insert = await qb.app("bq1234567").table("Tasks").operation("insert", fields=["Title"])
        scope = (F("Project") == "Alpha") & (F("Status") == "Open")
        await insert.run({"records": [{"Title": "Pour slab"}]}, scope=scope)
    (body,) = fake.requests("POST /records")
    assert body["data"] == [
        {"6": {"value": "Pour slab"}, "9": {"value": "Alpha"}, "7": {"value": "Open"}}
    ]


@pytest.mark.parametrize(
    "scope",
    [
        F("Project") != "Alpha",
        (F("Project") == "Alpha") | (F("Project") == "Beta"),
        F("Project").contains("Al"),
        (F("Project") == "Alpha") & (F("Project") == "Beta"),
    ],
)
async def test_insert_refuses_a_scope_a_new_record_cant_match(scope: Any) -> None:
    fake = Fake()
    async with client(fake) as qb:
        insert = await qb.app("bq1234567").table("Tasks").operation("insert", fields=["Title"])
        with pytest.raises(QueryError, match="can't insert records"):
            await insert.run({"records": [{"Title": "Pour slab"}]}, scope=scope)
    assert fake.requests("POST /records") == []


@pytest.mark.parametrize("name", ["insert", "update", "preview_update"])
async def test_a_write_cant_set_a_field_the_scope_uses(name: str) -> None:
    fake = Fake()
    async with client(fake) as qb:
        operation = await qb.app("bq1234567").table("Tasks").operation(name)  # type: ignore[arg-type]
        row: dict[str, Any] = {"Project": "Beta"}
        if name != "insert":
            row["Record ID#"] = 1
        with pytest.raises(QueryError, match=r"records\[0\] sets 'Project'"):
            await operation.run({"records": [row]}, scope=F("Project") == "Alpha")
    assert fake.requests("POST /records") == []
    assert fake.requests("POST /records/query") == []


async def test_a_delete_that_matches_too_many_records_is_refused() -> None:
    fake = Fake(matching=11)
    async with client(fake) as qb:
        delete = (
            await qb.app("bq1234567").table("Tasks").operation("delete", max_records_per_call=10)
        )
        assert "more than 10 records" in delete.schema["properties"]["where"]["description"]
        with pytest.raises(QueryError, match="11 records match"):
            await delete.run({"where": OPEN}, scope=F("Project") == "Alpha")
    assert fake.requests("DELETE /records") == []
    (count,) = fake.requests("POST /records/query")
    assert count["where"] == "{9.EX.'Alpha'}AND{7.EX.'Open'}"


@pytest.mark.parametrize(("matching", "over"), [(10, False), (11, True)])
async def test_a_delete_preview_says_when_a_delete_would_be_refused(
    matching: int, over: bool
) -> None:
    async with client(Fake(matching=matching)) as qb:
        preview = (
            await qb.app("bq1234567")
            .table("Tasks")
            .operation("preview_delete", max_records_per_call=10)
        )
        page = await preview.run({"where": OPEN})
    assert isinstance(page, Page)
    assert page.total == matching
    result = tool_result(page)
    assert result["warnings"] == []
    if over:
        (blocker,) = result["blockers"]
        assert "11 records match, and a delete may match at most 10" in blocker
        assert "would be refused" in blocker
        assert "confirmation" not in result
    else:
        assert result["blockers"] == []


async def test_preview_update_offers_only_fields_it_can_read() -> None:
    policy = bound(allow.read(TASKS, [6, 7]), allow.update(TASKS, [7, 9]))
    async with client(Fake(), policy=policy) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = await tasks.operation("preview_update")
        record = preview.schema["properties"]["records"]["items"]
        assert list(record["properties"]) == ["Record ID#", "Status"]
        update = await tasks.operation("update")
        assert list(update.schema["properties"]["records"]["items"]["properties"]) == [
            "Record ID#",
            "Status",
            "Project",
        ]
        with pytest.raises(PolicyRefusedError, match="both read and change"):
            await tasks.operation("preview_update", fields=["Project"])


async def test_preview_update_reads_within_the_read_rules_limit() -> None:
    fake = Fake()
    async with client(fake, policy=bound(allow.read(TASKS, max_paging_depth=2))) as qb:
        rows = [{"Record ID#": record_id, "Status": "Done"} for record_id in (1, 2, 3)]
        await qb.app("bq1234567").table("Tasks").preview_update(rows)
    assert [body["options"]["top"] for body in fake.requests("POST /records/query")] == [2, 1]


async def test_reports_are_chosen_by_id_and_described_separately() -> None:
    async with client(Fake(), policy=Policy.read_only()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(ConfigurationError, match="report_descriptions"):
            await tasks.operation("run_report", reports={"1": "Every task."})  # pyright: ignore[reportArgumentType]
        with pytest.raises(UnknownReportError, match="doesn't offer"):
            await tasks.operation("run_report", report_descriptions={"99": "Missing."})
        run = await tasks.operation("run_report", report_descriptions={"1": "Every task."})
        assert "1: List All, Every task." in run.schema["properties"]["report"]["description"]


async def test_reports() -> None:
    fake = Fake()
    async with client(fake, policy=Policy.read_only()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        run = await tasks.operation(
            "run_report", reports=["1"], report_descriptions={"1": "Every task."}
        )
        assert "1: List All, Every task." in run.schema["properties"]["report"]["description"]
        page = await run.run({"report": "1", "limit": 2})
        assert isinstance(page, Page)
        summary = await tasks.operation("run_summary_report")
        assert await summary.run({"report": "5"}) == [{"Record ID#": 1}]
    assert [name for name, _ in fake.sent if "/run" in name] == [
        "POST /reports/1/run",
        "POST /reports/5/run",
    ]


async def test_reports_can_be_named_by_number_or_name() -> None:
    fake = Fake()
    async with client(fake, policy=Policy.read_only()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        run = await tasks.operation("run_report")
        assert isinstance(await run.run({"report": 1}), Page)
        assert isinstance(await run.run({"report": "list all"}), Page)
        summary = await tasks.operation("run_summary_report")
        assert await summary.run({"report": "Totals"}) == [{"Record ID#": 1}]
        with pytest.raises(QueryError, match="use the run_report operation"):
            await summary.run({"report": "List All"})
    assert [name for name, _ in fake.sent if "/run" in name] == [
        "POST /reports/1/run",
        "POST /reports/1/run",
        "POST /reports/5/run",
    ]


async def test_an_operation_the_client_cant_run_is_refused_when_made() -> None:
    async with client(Fake(), policy=Policy.read_only()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        for name in ("insert", "update", "delete", "delete_file_version"):
            with pytest.raises(PolicyRefusedError, match=f"can't offer {name}"):
                await tasks.operation(name)  # type: ignore[arg-type]
    rules = bound(allow.read(TASKS), allow.update(TASKS, [7]))
    async with client(Fake(), policy=rules) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(PolicyRefusedError, match="insert"):
            await tasks.operation("insert")
        update = await tasks.operation("update")
        record = update.schema["properties"]["records"]["items"]
        assert list(record["properties"]) == ["Record ID#", "Status"]


async def test_read_rules_shape_what_is_offered_and_refuse_formulas() -> None:
    rules = bound(allow.read(TASKS, [6, 7], filter=[7], max_paging_depth=10))
    async with client(Fake(), policy=rules) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        search = await tasks.operation("query")
        properties = search.schema["properties"]
        assert properties["select"]["items"]["enum"] == ["Record ID#", "Title", "Status"]
        assert properties["limit"]["maximum"] == 10
        with pytest.raises(PolicyRefusedError, match="formulas"):
            await tasks.operation("run_formula", formula="1")
        with pytest.raises(PolicyRefusedError, match="download"):
            await tasks.operation("download_file")
        with pytest.raises(PolicyRefusedError, match="report"):
            await tasks.operation("run_report")


async def test_bad_settings() -> None:
    async with client(Fake()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(ConfigurationError, match="name must be one of"):
            await tasks.operation("upsert")  # type: ignore[arg-type]
        with pytest.raises(ConfigurationError, match="needs formula"):
            await tasks.operation("run_formula")
        with pytest.raises(ConfigurationError, match="only for run_formula"):
            await tasks.operation("query", formula="1")


async def test_refused_arguments_send_nothing() -> None:
    fake = Fake()
    async with client(fake) as qb:
        insert = await qb.app("bq1234567").table("Tasks").operation("insert")
        before = len(fake.sent)
        with pytest.raises(QueryError):
            await insert.run({"rows": []})
    assert len(fake.sent) == before


async def test_a_scope_keeps_an_any_group_inside_it() -> None:
    fake = Fake()
    async with client(fake) as qb:
        count = await qb.app("bq1234567").table("Tasks").operation("count")
        where = {"any": [OPEN, {"field": "Status", "op": "==", "value": "Done"}]}
        await count.run({"where": where}, scope=F("Project") == "Alpha")
    (body,) = fake.requests("POST /records/query")
    assert body["where"] == "{9.EX.'Alpha'}AND({7.EX.'Open'}OR{7.EX.'Done'})"


# Scope given when the operation is made


async def test_a_scope_given_when_made_is_described_and_applied() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        count = await tasks.operation("count", scope=F("Project") == "Alpha")
        description = count.schema["properties"]["where"]["description"]
        assert 'Every record this tool reaches also matches: Project is "Alpha".' in description
        await count.run({"where": OPEN})
        await count.run({"where": OPEN}, scope=F("Hours") > 2)
    first, second = fake.requests("POST /records/query")
    assert first["where"] == "{9.EX.'Alpha'}AND{7.EX.'Open'}"
    assert second["where"] == "{9.EX.'Alpha'}AND{8.GT.'2'}AND{7.EX.'Open'}"


async def test_an_insert_scope_is_left_out_of_the_schema_and_filled_in() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        insert = await tasks.operation("insert", scope=F("Project") == "Alpha")
        records = insert.schema["properties"]["records"]
        assert "Project" not in records["items"]["properties"]
        assert 'made to match this tool\'s scope (Project is "Alpha")' in records["description"]
        await insert.run({"records": [{"Title": "New"}]})
        with pytest.raises(
            InvalidValueError, match=r"records\[0\]\.Project is fixed by this tool's"
        ):
            await insert.run({"records": [{"Title": "x", "Project": "Beta"}]})
    (sent,) = fake.requests("POST /records")
    assert sent["data"][0]["9"] == {"value": "Alpha"}


@pytest.mark.parametrize(
    ("name", "scope", "kwargs", "match"),
    [
        ("insert", F("Project") != "Alpha", {}, "only == conditions"),
        ("run_report", F("Project") == "Alpha", {}, "can't take a scope"),
        (
            "insert",
            F("Project") == "Alpha",
            {"policy": "insert Title"},
            r"Allow them with allow.insert\(\)",
        ),
    ],
)
async def test_a_scope_that_cant_work_is_refused_when_made(
    name: str, scope: Any, kwargs: dict[str, Any], match: str
) -> None:
    if kwargs.get("policy") == "insert Title":
        kwargs = {"policy": bound(allow.read(TASKS), allow.insert(TASKS, [6]))}
    async with client(Fake(), **kwargs) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(ConfigurationError, match=match):
            await tasks.operation(name, scope=scope)  # type: ignore[arg-type]


# Previews that an update or delete requires


CHANGE = {"records": [{"Record ID#": 1, "Status": "Done", "Title": "x"}]}


async def test_an_update_needs_the_confirmation_of_a_preview_of_the_same_records() -> None:
    fake = Fake()
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = await tasks.operation("preview_update")
        update = await tasks.operation("update", require_preview=True)
        assert "confirmation" in update.schema["required"]
        with pytest.raises(ConfirmationError, match="needs the confirmation that preview_update"):
            await update.run(CHANGE)
        shown = tool_result(await preview.run(CHANGE))
        confirmation = shown["confirmation"]
        # The same records in another order, and keys in another order, still match.
        reordered = {"records": [{"Title": "x", "Status": "Done", "Record ID#": 1}]}
        result = await update.run({**reordered, "confirmation": confirmation})
        assert isinstance(result, WriteResult)
        other = {"records": [{"Record ID#": 1, "Status": "Open", "Title": "x"}]}
        with pytest.raises(ConfirmationError, match="doesn't match"):
            await update.run({**other, "confirmation": confirmation})
        with pytest.raises(ConfirmationError, match="isn't one preview_update returned"):
            await update.run({**CHANGE, "confirmation": "nonsense"})
    assert len(fake.requests("POST /records")) == 1


async def test_a_confirmation_expires_and_only_works_with_its_own_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with client(Fake()) as qb, client(Fake()) as other_qb:
        preview = await qb.app("bq1234567").table("Tasks").operation("preview_update")
        confirmation = tool_result(await preview.run(CHANGE))["confirmation"]
        elsewhere = (
            await other_qb.app("bq1234567").table("Tasks").operation("update", require_preview=True)
        )
        with pytest.raises(ConfirmationError, match="another client or server process"):
            await elsewhere.run({**CHANGE, "confirmation": confirmation})
        update = await qb.app("bq1234567").table("Tasks").operation("update", require_preview=True)
        later = time.time() + 16 * 60
        monkeypatch.setattr(time, "time", lambda: later)
        with pytest.raises(ConfirmationError, match="has expired"):
            await update.run({**CHANGE, "confirmation": confirmation})


async def test_count_and_delete_say_what_their_number_is() -> None:
    async with client(Fake(matching=2)) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        count = await (await tasks.operation("count")).run({"where": OPEN})
        deleted = await (await tasks.operation("delete", max_records_per_call=10)).run(
            {"where": OPEN}
        )
    assert count == 2
    assert tool_result(count) == {"ok": True, "count": 2}
    assert tool_result(deleted) == {"ok": True, "deleted_count": 1}


async def test_a_delete_needs_a_preview_and_the_same_records_to_match() -> None:
    fake = Fake(matching=2)
    async with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = await tasks.operation("preview_delete", max_records_per_call=10)
        delete = await tasks.operation("delete", max_records_per_call=10, require_preview=True)
        confirmation = tool_result(await preview.run({"where": OPEN}))["confirmation"]
        fake.matching = 3  # Another record now matches.
        with pytest.raises(ConfirmationError, match="the records that match have changed"):
            await delete.run({"where": OPEN, "confirmation": confirmation})
        assert fake.requests("DELETE /records") == []
        fake.matching = 2
        assert await delete.run({"where": OPEN, "confirmation": confirmation}) == 1
        # A preview over the limit gives no confirmation, as the delete would be refused.
        fake.matching = 11
        shown = tool_result(await preview.run({"where": OPEN}))
        assert "confirmation" not in shown
        assert shown["blockers"]


async def test_a_preview_and_its_delete_share_a_confirmation_across_spellings() -> None:
    async with client(Fake(matching=2)) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = await tasks.operation("preview_delete", max_records_per_call=10)
        delete = await tasks.operation("delete", max_records_per_call=10, require_preview=True)
        where = {"and": [{"field": "Status", "op": "eq", "value": "Open"}]}
        confirmation = tool_result(await preview.run({"where": where}))["confirmation"]
        same = {"all": [{"field": "Status", "op": "==", "value": "Open"}]}
        assert await delete.run({"where": same, "confirmation": confirmation}) == 1


async def test_only_update_and_delete_can_require_a_preview() -> None:
    async with client(Fake()) as qb:
        with pytest.raises(ConfigurationError, match="only for update and delete"):
            await qb.app("bq1234567").table("Tasks").operation("insert", require_preview=True)
