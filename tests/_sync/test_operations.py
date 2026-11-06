# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Operations used through a client, so each one reaches Quickbase the way its method does."""

import base64
import json
import time
from typing import Any

import httpx
import pytest

from pyquickbase import (
    ConfigurationError,
    F,
    FileDownload,
    InvalidValueError,
    Page,
    QueryError,
    Quickbase,
    ReadAccess,
    ReadRefusedError,
    ReadRules,
    SummaryRow,
    UpdatePreview,
    WriteAccess,
    WriteRefusedError,
    WriteResult,
    WriteRules,
    tool_result,
)

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


def client(fake: Fake, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, **kwargs)


OPEN = {"field": "Status", "op": "==", "value": "Open"}


def test_query_runs_a_page_query_within_its_scope() -> None:
    fake = Fake()
    with client(fake, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        search = tasks.operation("query", fields=["Title", "Status"])
        assert search.name == "query"
        page = search.run(
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


def test_the_schema_is_a_new_dictionary_each_time() -> None:
    with client(Fake()) as qb:
        search = qb.app("bq1234567").table("Tasks").operation("query")
    search.schema["properties"].clear()
    assert search.schema["properties"]


def test_count_summarize_and_delete() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert (tasks.operation("count")).run({"where": OPEN}) == 1
        rows = (tasks.operation("summarize")).run({"group_by": ["Status"]})
        assert all(isinstance(row, SummaryRow) for row in rows)
        delete = tasks.operation("delete")
        assert delete.run({"where": OPEN}, scope=F("Project") == "Alpha") == 1
    (deleted,) = fake.requests("DELETE /records")
    assert deleted["where"] == "{9.EX.'Alpha'}AND{7.EX.'Open'}"


def test_insert_and_update() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        insert = tasks.operation("insert", fields=["Title", "Hours"])
        result = insert.run({"records": [{"Title": "Pour slab", "Hours": 8}]})
        assert isinstance(result, WriteResult)
        assert result.created_ids == (2,)
        update = tasks.operation("update", fields=["Status"])
        assert (update.run({"records": [{"Record ID#": 1, "Status": "Done"}]})).updated_ids == (1,)
    first, second = fake.requests("POST /records")
    assert first["data"] == [{"6": {"value": "Pour slab"}, "8": {"value": 8}}]
    assert second["data"] == [{"3": {"value": 1}, "7": {"value": "Done"}}]


def test_a_scope_is_checked_before_a_record_is_named() -> None:
    fake = Fake(matching=0)
    with client(fake) as qb:
        update = qb.app("bq1234567").table("Tasks").operation("update")
        with pytest.raises(QueryError, match="aren't ones this tool may use"):
            update.run(
                {"records": [{"Record ID#": 1, "Status": "Done"}]}, scope=F("Project") == "Alpha"
            )
    assert fake.requests("POST /records") == []
    (check,) = fake.requests("POST /records/query")
    assert check["where"] == "{9.EX.'Alpha'}AND{3.EX.'1'}"


def test_a_scope_that_matches_lets_the_call_run() -> None:
    fake = Fake(matching=1)
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = (tasks.operation("preview_update")).run(
            {"records": [{"Record ID#": 1, "Status": "Done"}]}, scope=F("Project") == "Alpha"
        )
        assert isinstance(preview, UpdatePreview)
        formula = tasks.operation("run_formula", formula="[Hours] * 2")
        assert (formula.run({"record_id": 1}, scope=F("Project") == "Alpha")).value == 8
        download = tasks.operation("download_file")
        found = download.run({"record_id": 1, "field": "Attachment"})
        assert isinstance(found, FileDownload)
        assert found.data == b"notes"
        delete = tasks.operation("delete_file_version")
        delete.run({"record_id": 1, "field": "Attachment", "version": 1})
    (formula_body,) = fake.requests("POST /formula/run")
    assert formula_body == {"formula": "[Hours] * 2", "from": TASKS, "rid": 1}
    assert [name for name, _ in fake.sent if name.startswith(("GET /files", "DELETE /files"))] == [
        "GET /files/bqtasks/1/13/0",
        "DELETE /files/bqtasks/1/13/1",
    ]


def test_insert_fills_in_an_equality_scope() -> None:
    fake = Fake()
    with client(fake) as qb:
        insert = qb.app("bq1234567").table("Tasks").operation("insert", fields=["Title"])
        scope = (F("Project") == "Alpha") & (F("Status") == "Open")
        insert.run({"records": [{"Title": "Pour slab"}]}, scope=scope)
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
def test_insert_refuses_a_scope_a_new_record_cant_match(scope: Any) -> None:
    fake = Fake()
    with client(fake) as qb:
        insert = qb.app("bq1234567").table("Tasks").operation("insert", fields=["Title"])
        with pytest.raises(QueryError, match="can't insert records"):
            insert.run({"records": [{"Title": "Pour slab"}]}, scope=scope)
    assert fake.requests("POST /records") == []


@pytest.mark.parametrize("name", ["insert", "update", "preview_update"])
def test_a_write_cant_set_a_field_the_scope_uses(name: str) -> None:
    fake = Fake()
    with client(fake) as qb:
        operation = qb.app("bq1234567").table("Tasks").operation(name)  # type: ignore[arg-type]
        row: dict[str, Any] = {"Project": "Beta"}
        if name != "insert":
            row["Record ID#"] = 1
        with pytest.raises(QueryError, match=r"records\[0\] sets 'Project'"):
            operation.run({"records": [row]}, scope=F("Project") == "Alpha")
    assert fake.requests("POST /records") == []
    assert fake.requests("POST /records/query") == []


def test_a_delete_that_matches_too_many_records_is_refused() -> None:
    fake = Fake(matching=11)
    with client(fake) as qb:
        delete = qb.app("bq1234567").table("Tasks").operation("delete", max_records=10)
        assert "more than 10 records" in delete.schema["properties"]["where"]["description"]
        with pytest.raises(QueryError, match="11 records match"):
            delete.run({"where": OPEN}, scope=F("Project") == "Alpha")
    assert fake.requests("DELETE /records") == []
    (count,) = fake.requests("POST /records/query")
    assert count["where"] == "{9.EX.'Alpha'}AND{7.EX.'Open'}"


@pytest.mark.parametrize(("matching", "over"), [(10, False), (11, True)])
def test_a_delete_preview_says_when_a_delete_would_be_refused(matching: int, over: bool) -> None:
    with client(Fake(matching=matching)) as qb:
        preview = qb.app("bq1234567").table("Tasks").operation("preview_delete", max_records=10)
        page = preview.run({"where": OPEN})
    assert isinstance(page, Page)
    assert page.total == matching
    result = tool_result(page)
    if over:
        assert result["over_limit"]["max_records"] == 10
        assert "11 records match" in result["over_limit"]["message"]
        assert "would be refused" in result["over_limit"]["message"]
    else:
        assert "over_limit" not in result


def test_preview_update_offers_only_fields_it_can_read() -> None:
    reads = ReadRules({TASKS: ReadAccess(fields=[6, 7], write_only=[9])})
    writes = WriteRules({TASKS: WriteAccess(update=[7, 9])})
    with client(Fake(), read_policy=reads, write_policy=writes) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = tasks.operation("preview_update")
        record = preview.schema["properties"]["records"]["items"]
        assert list(record["properties"]) == ["Record ID#", "Status"]
        update = tasks.operation("update")
        assert list(update.schema["properties"]["records"]["items"]["properties"]) == [
            "Record ID#",
            "Status",
            "Project",
        ]
        with pytest.raises(WriteRefusedError, match="both read and change"):
            tasks.operation("preview_update", fields=["Project"])


def test_preview_update_reads_within_the_read_rules_limit() -> None:
    fake = Fake()
    reads = ReadRules({TASKS: ReadAccess(fields=True, max_records=2)})
    with client(fake, read_policy=reads) as qb:
        rows = [{"Record ID#": record_id, "Status": "Done"} for record_id in (1, 2, 3)]
        qb.app("bq1234567").table("Tasks").preview_update(rows)
    assert [body["options"]["top"] for body in fake.requests("POST /records/query")] == [2, 1]


def test_reports() -> None:
    fake = Fake()
    with client(fake, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        run = tasks.operation("run_report", reports={"1": "Every task."})
        assert "1: List All, Every task." in run.schema["properties"]["report"]["description"]
        page = run.run({"report": "1", "limit": 2})
        assert isinstance(page, Page)
        summary = tasks.operation("summary_report")
        assert summary.run({"report": "5"}) == [{"Record ID#": 1}]
    assert [name for name, _ in fake.sent if "/run" in name] == [
        "POST /reports/1/run",
        "POST /reports/5/run",
    ]


def test_reports_can_be_named_by_number_or_name() -> None:
    fake = Fake()
    with client(fake, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        run = tasks.operation("run_report")
        assert isinstance(run.run({"report": 1}), Page)
        assert isinstance(run.run({"report": "list all"}), Page)
        summary = tasks.operation("summary_report")
        assert summary.run({"report": "Totals"}) == [{"Record ID#": 1}]
        with pytest.raises(QueryError, match="use the run_report operation"):
            summary.run({"report": "List All"})
    assert [name for name, _ in fake.sent if "/run" in name] == [
        "POST /reports/1/run",
        "POST /reports/1/run",
        "POST /reports/5/run",
    ]


def test_an_operation_the_client_cant_run_is_refused_when_made() -> None:
    with client(Fake(), read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        for name in ("insert", "update", "delete", "delete_file_version"):
            with pytest.raises(WriteRefusedError, match=f"can't offer {name}"):
                tasks.operation(name)  # type: ignore[arg-type]
    rules = WriteRules({TASKS: WriteAccess(update=[7])})
    with client(Fake(), write_policy=rules) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(WriteRefusedError, match="insert"):
            tasks.operation("insert")
        update = tasks.operation("update")
        record = update.schema["properties"]["records"]["items"]
        assert list(record["properties"]) == ["Record ID#", "Status"]


def test_read_rules_shape_what_is_offered_and_refuse_formulas() -> None:
    rules = ReadRules({TASKS: ReadAccess(fields=[6, 7], filter=[7], max_records=10)})
    with client(Fake(), read_policy=rules, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        search = tasks.operation("query")
        properties = search.schema["properties"]
        assert properties["select"]["items"]["enum"] == ["Record ID#", "Title", "Status"]
        assert properties["limit"]["maximum"] == 10
        with pytest.raises(ReadRefusedError, match="formulas"):
            tasks.operation("run_formula", formula="1")
        with pytest.raises(ReadRefusedError, match="download"):
            tasks.operation("download_file")
        with pytest.raises(ReadRefusedError, match="report"):
            tasks.operation("run_report")


def test_bad_settings() -> None:
    with client(Fake()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(ConfigurationError, match="name must be one of"):
            tasks.operation("upsert")  # type: ignore[arg-type]
        with pytest.raises(ConfigurationError, match="needs formula"):
            tasks.operation("run_formula")
        with pytest.raises(ConfigurationError, match="only for run_formula"):
            tasks.operation("query", formula="1")


def test_refused_arguments_send_nothing() -> None:
    fake = Fake()
    with client(fake) as qb:
        insert = qb.app("bq1234567").table("Tasks").operation("insert")
        before = len(fake.sent)
        with pytest.raises(QueryError):
            insert.run({"rows": []})
    assert len(fake.sent) == before


def test_a_scope_keeps_an_any_group_inside_it() -> None:
    fake = Fake()
    with client(fake) as qb:
        count = qb.app("bq1234567").table("Tasks").operation("count")
        where = {"any": [OPEN, {"field": "Status", "op": "==", "value": "Done"}]}
        count.run({"where": where}, scope=F("Project") == "Alpha")
    (body,) = fake.requests("POST /records/query")
    assert body["where"] == "{9.EX.'Alpha'}AND({7.EX.'Open'}OR{7.EX.'Done'})"


# Scope given when the operation is made


def test_a_scope_given_when_made_is_described_and_applied() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        count = tasks.operation("count", scope=F("Project") == "Alpha")
        description = count.schema["properties"]["where"]["description"]
        assert 'Every record this tool reaches also matches: Project is "Alpha".' in description
        count.run({"where": OPEN})
        count.run({"where": OPEN}, scope=F("Hours") > 2)
    first, second = fake.requests("POST /records/query")
    assert first["where"] == "{9.EX.'Alpha'}AND{7.EX.'Open'}"
    assert second["where"] == "{9.EX.'Alpha'}AND{8.GT.'2'}AND{7.EX.'Open'}"


def test_an_insert_scope_is_left_out_of_the_schema_and_filled_in() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        insert = tasks.operation("insert", scope=F("Project") == "Alpha")
        records = insert.schema["properties"]["records"]
        assert "Project" not in records["items"]["properties"]
        assert 'made to match this tool\'s scope (Project is "Alpha")' in records["description"]
        insert.run({"records": [{"Title": "New"}]})
        with pytest.raises(
            InvalidValueError, match=r"records\[0\]\.Project is fixed by this tool's"
        ):
            insert.run({"records": [{"Title": "x", "Project": "Beta"}]})
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
            {"write_policy": WriteRules({TASKS: WriteAccess(insert=[6])})},
            "Allow them in WriteRules",
        ),
    ],
)
def test_a_scope_that_cant_work_is_refused_when_made(
    name: str, scope: Any, kwargs: dict[str, Any], match: str
) -> None:
    with client(Fake(), **kwargs) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(ConfigurationError, match=match):
            tasks.operation(name, scope=scope)  # type: ignore[arg-type]


# Previews that an update or delete requires


CHANGE = {"records": [{"Record ID#": 1, "Status": "Done", "Title": "x"}]}


def test_an_update_needs_the_confirmation_of_a_preview_of_the_same_records() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = tasks.operation("preview_update")
        update = tasks.operation("update", require_preview=True)
        assert "confirmation" in update.schema["required"]
        with pytest.raises(QueryError, match="needs the confirmation that preview_update"):
            update.run(CHANGE)
        shown = tool_result(preview.run(CHANGE))
        confirmation = shown["confirmation"]
        # The same records in another order, and keys in another order, still match.
        reordered = {"records": [{"Title": "x", "Status": "Done", "Record ID#": 1}]}
        result = update.run({**reordered, "confirmation": confirmation})
        assert isinstance(result, WriteResult)
        other = {"records": [{"Record ID#": 1, "Status": "Open", "Title": "x"}]}
        with pytest.raises(QueryError, match="doesn't match"):
            update.run({**other, "confirmation": confirmation})
        with pytest.raises(QueryError, match="isn't one preview_update returned"):
            update.run({**CHANGE, "confirmation": "nonsense"})
    assert len(fake.requests("POST /records")) == 1


def test_a_confirmation_expires_and_only_works_with_its_own_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with client(Fake()) as qb, client(Fake()) as other_qb:
        preview = qb.app("bq1234567").table("Tasks").operation("preview_update")
        confirmation = tool_result(preview.run(CHANGE))["confirmation"]
        elsewhere = (
            other_qb.app("bq1234567").table("Tasks").operation("update", require_preview=True)
        )
        with pytest.raises(QueryError, match="doesn't match"):
            elsewhere.run({**CHANGE, "confirmation": confirmation})
        update = qb.app("bq1234567").table("Tasks").operation("update", require_preview=True)
        later = time.time() + 16 * 60
        monkeypatch.setattr(time, "time", lambda: later)
        with pytest.raises(QueryError, match="has expired"):
            update.run({**CHANGE, "confirmation": confirmation})


def test_a_delete_needs_a_preview_and_the_same_records_to_match() -> None:
    fake = Fake(matching=2)
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        preview = tasks.operation("preview_delete", max_records=10)
        delete = tasks.operation("delete", max_records=10, require_preview=True)
        confirmation = tool_result(preview.run({"where": OPEN}))["confirmation"]
        fake.matching = 3  # Another record now matches.
        with pytest.raises(QueryError, match="the records that match have changed"):
            delete.run({"where": OPEN, "confirmation": confirmation})
        assert fake.requests("DELETE /records") == []
        fake.matching = 2
        assert delete.run({"where": OPEN, "confirmation": confirmation}) == 1
        # A preview over the limit gives no confirmation, as the delete would be refused.
        fake.matching = 11
        shown = tool_result(preview.run({"where": OPEN}))
        assert "confirmation" not in shown
        assert "over_limit" in shown


def test_only_update_and_delete_can_require_a_preview() -> None:
    with client(Fake()) as qb:
        with pytest.raises(ConfigurationError, match="only for update and delete"):
            qb.app("bq1234567").table("Tasks").operation("insert", require_preview=True)
