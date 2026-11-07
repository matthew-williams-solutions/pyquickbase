# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""A policy's reads, used through a client, so every kind of read reaches the check."""

import json
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx
import pytest

from pyquickbase import (
    BoundPolicy,
    Column,
    Decision,
    F,
    Policy,
    PolicyRefusedError,
    QueryError,
    Quickbase,
    ReadRequest,
    SchemaMismatchError,
    TypedRecord,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    User,
    WriteRequest,
    allow,
    fields,
)

from ..policy_helpers import bind

TASKS = "bqtasks"
SECRET = "bqsecret"
USER = {"email": "ann@example.com", "id": "1.ab", "name": "Ann"}
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid", "appearsByDefault": True},
    {"id": 6, "label": "Title", "fieldType": "text", "appearsByDefault": True},
    {"id": 7, "label": "Status", "fieldType": "text", "appearsByDefault": True},
    {"id": 8, "label": "Hours", "fieldType": "numeric", "appearsByDefault": True},
    {
        "id": 9,
        "label": "Notes",
        "fieldType": "text",
        "appearsByDefault": True,
        "properties": {"formula": "[Salary]"},
    },
    {"id": 10, "label": "Owner", "fieldType": "user", "appearsByDefault": True},
    {"id": 13, "label": "Attachment", "fieldType": "file"},
]
VALUES: dict[int, Any] = {
    3: 1,
    6: "Pour slab",
    7: "Open",
    8: 4,
    9: "s3cret",
    10: USER,
    13: {"url": "/f", "versions": [{"versionNumber": 1, "fileName": "a", "creator": USER}]},
}
TYPES = {item["id"]: item["fieldType"] for item in FIELDS}


def page(field_ids: list[int], total: int = 1) -> dict[str, Any]:
    return {
        "fields": [{"id": fid, "label": f"F{fid}", "type": TYPES[fid]} for fid in field_ids],
        "data": [{str(fid): {"value": VALUES[fid]} for fid in field_ids}],
        "metadata": {"totalRecords": total, "numRecords": 1, "skip": 0},
    }


class Fake:
    """Answers like Quickbase, and records every request, so a test can check what was sent."""

    def __init__(self, total: int = 1) -> None:
        self.total = total
        self.sent: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        self.sent.append(f"{request.method} {path}")
        body = json.loads(request.content) if request.content else None
        if path == "/tables":
            tables = [{"id": TASKS, "name": "Tasks", "keyFieldId": 3}]
            return httpx.Response(200, json=[*tables, {"id": SECRET, "name": "Salaries"}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        if path == "/records/query":
            return httpx.Response(200, json=page(body["select"], self.total))
        if path == "/reports":
            reports = [
                {"id": "7", "name": "Open", "type": "table", "query": {"filter": "x"}},
                {"id": "8", "name": "Totals", "type": "summary"},
            ]
            return httpx.Response(200, json=reports)
        if path == "/reports/7/run":
            return httpx.Response(200, json=page([3, 6, 9]))
        if path == "/reports/8/run":
            return httpx.Response(200, json=page([3, 8]))
        if path.startswith("/files/"):
            return httpx.Response(
                200,
                content=b"aGk=",
                headers={"content-disposition": "attachment; filename*=utf-8''a.txt"},
            )
        if path == f"/tables/{TASKS}/relationships":
            return httpx.Response(
                200, json={"relationships": [], "metadata": {"totalRelationships": 0}}
            )
        if path == "/records" and request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": 1})
        if path == "/records":
            ids = {"createdRecordIds": [2], "updatedRecordIds": [], "unchangedRecordIds": []}
            return httpx.Response(200, json={"metadata": ids})
        return httpx.Response(200, json={"id": "bqapp", "name": "App", "variables": [{"v": 1}]})


def client(fake: Fake, policy: Any, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, policy=policy, **kwargs)


REPORTS = [
    {"id": "7", "name": "Open", "type": "table"},
    {"id": "8", "name": "Totals", "type": "summary"},
]


def rules(*extra: Any, **access: Any) -> BoundPolicy:
    """What `ReadAccess(fields=[6, 7, 10], filter=[7])` allowed, with the same options."""
    options = {"filter": [7], **access}
    readable = options.pop("fields", [6, 7, 10])
    if "sort" not in options:
        options["sort"] = readable
    return bind(
        allow.read(TASKS, readable, **options),
        *extra,
        tables={TASKS: ("Tasks", FIELDS)},
        reports={TASKS: REPORTS},
    )


def queries(fake: Fake) -> list[str]:
    return [item for item in fake.sent if item == "POST /records/query"]


def test_unlisted_tables_and_fields_look_as_if_they_dont_exist() -> None:
    fake = Fake()
    with client(fake, rules()) as qb:
        app = qb.app("bq1234567")
        assert [table.name for table in app.tables()] == ["Tasks"]
        with pytest.raises(UnknownTableError):
            app.table("Salaries").fields()
        tasks = app.table("Tasks")
        fields = tasks.fields()
        assert [field.label for field in fields] == ["Record ID#", "Title", "Status", "Owner"]
        assert all("formula" not in json.dumps(dict(field.raw)) for field in fields)
        # The hint mustn't suggest a hidden label.
        with pytest.raises(UnknownFieldError) as caught:
            tasks.first(select=["Note"])
        assert "Notes" not in str(caught.value)


def test_default_select_only_holds_returned_fields() -> None:
    fake = Fake()
    with client(fake, rules()) as qb:
        record = qb.app("bq1234567").table("Tasks").first()
    assert record is not None
    assert dict(record) == {
        "F3": 1,
        "F6": "Pour slab",
        "F7": "Open",
        "F10": User(id="1.ab", name="Ann"),
    }
    assert record["F10"].email is None  # Left out without user_emails


def test_filtering_and_sorting_outside_the_lists_are_refused_without_sending() -> None:
    fake = Fake()
    with client(fake, rules(sort=[7])) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(PolicyRefusedError, match=r"filtering on Title \(6\)"):
            tasks.count(F("Title") == "x")
        with pytest.raises(PolicyRefusedError, match=r"filtering on Title \(6\)"):
            tasks.preview_delete(F("Title") == "x")
        with pytest.raises(PolicyRefusedError, match="sorting"):
            tasks.first(order_by="Title")
        assert tasks.count(F("Status") == "Open") == 1
    assert len(queries(fake)) == 1


def test_hand_written_filters_are_checked_too() -> None:
    fake = Fake()
    with client(fake, rules()) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(PolicyRefusedError):
            tasks.count("{7.EX.'_FID_6'}")
        # The SDK's validator already refuses a field it can't see.
        with pytest.raises(QueryError):
            tasks.count("{9.EX.'s3cret'}")
    assert queries(fake) == []


def test_a_query_needs_a_limit_within_max_records() -> None:
    fake = Fake()
    with client(fake, rules(max_records=50)) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(PolicyRefusedError, match="at most 50"):
            [record for record in tasks.query()]
        assert len([record for record in tasks.query(limit=50)]) == 1
        with pytest.raises(PolicyRefusedError, match="at most 50"):
            tasks.query_page(top=10, skip=41)


def test_summarize_counts_first_and_has_its_own_limit() -> None:
    fake = Fake(total=11)
    with client(fake, rules(max_records=1, max_summary_records=10)) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(PolicyRefusedError, match="allows summarizing at most 10"):
            tasks.summarize(group_by="Status")
        assert len(queries(fake)) == 1  # Only the count.
        fake.total = 1
        rows = tasks.summarize(group_by="Status")
        assert [row.count for row in rows] == [1]
        with pytest.raises(UnknownFieldError):
            tasks.summarize(total="Hours")


def test_summarize_groups_users_by_id_without_emails() -> None:
    fake = Fake()
    with client(fake, rules()) as qb:
        rows = qb.app("bq1234567").table("Tasks").summarize(group_by="Owner")
    assert rows[0].group == {"Owner": "1.ab"}


def test_reports_must_be_listed_and_lose_hidden_columns() -> None:
    fake = Fake()
    with client(fake, rules(allow.report(TASKS, ["7", "Totals"]))) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert [item.name for item in tasks.reports()] == ["Open", "Totals"]
        assert all("query" not in item.raw for item in tasks.reports())
        record = (tasks.report("Open").run_page(top=5)).records[0]
        assert dict(record) == {"F3": 1, "F6": "Pour slab"}
        assert tasks.report("Totals").summary_rows(page_size=5) == [{"F3": 1, "F8": 4}]
    with client(Fake(), rules()) as qb:
        with pytest.raises(UnknownReportError):  # An unlisted report looks like none.
            qb.app("bq1234567").table("Tasks").report("Open").run_page()


def test_reports_through_request_are_refused() -> None:
    fake = Fake()
    with client(fake, rules(allow.report(TASKS, ["7"]))) as qb:
        with pytest.raises(PolicyRefusedError, match="request"):
            qb.request("POST", "/reports/7/run", params={"tableId": TASKS, "top": 1})
    assert "POST /reports/7/run" not in fake.sent


def test_downloads_need_their_own_grant() -> None:
    fake = Fake()
    with client(fake, rules(allow.download(TASKS, ["Attachment"]))) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert (tasks.download_file(1, "Attachment")).data == b"hi"
    with client(fake, rules(fields=[6, 13])) as qb:
        with pytest.raises(PolicyRefusedError, match="downloading"):
            qb.app("bq1234567").table("Tasks").download_file(1, "Attachment")


def test_formulas_app_details_and_roles_are_refused() -> None:
    fake = Fake()
    with client(fake, rules(fields=fields.all())) as qb:
        app = qb.app("bq1234567")
        with pytest.raises(PolicyRefusedError, match="formula"):
            app.table("Tasks").run_formula("1")
        with pytest.raises(PolicyRefusedError):
            app.info()
        with pytest.raises(PolicyRefusedError):
            app.roles()
    assert not [item for item in fake.sent if "formula" in item or "/apps" in item]


def test_a_formula_that_reads_a_hidden_field_is_refused() -> None:
    fake = Fake()
    with client(fake, rules(allow.schema(TASKS, "add_field", "formula"))) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(PolicyRefusedError, match=r"uses \[Notes\], which this client can't"):
            tasks.create_field("Copy", "text", formula="[Notes]")
        with pytest.raises(PolicyRefusedError, match="check what they read"):
            qb.request(
                "POST",
                "/fields",
                params={"tableId": TASKS},
                json={"label": "Copy", "fieldType": "text", "properties": {"formula": "1"}},
            )
    assert not [item for item in fake.sent if item.startswith("POST /fields")]


def test_record_writes_still_work_and_deletes_are_held_to_the_filter() -> None:
    fake = Fake()
    writes = [allow.insert(TASKS, fields.all()), allow.delete(TASKS)]
    with client(fake, rules(*writes)) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        assert (tasks.insert([{"Title": "x", "Notes": "private"}])).created_ids == (2,)
        with pytest.raises(PolicyRefusedError, match="filtering"):
            tasks.delete(F("Title") == "x")
        assert tasks.delete(F("Status") == "x") == 1
        with pytest.raises(PolicyRefusedError, match="reading Notes"):
            tasks.first(select=["Notes"])


def test_write_refusals_come_before_reads() -> None:
    with client(Fake(), Policy.read_only()) as qb:
        with pytest.raises(PolicyRefusedError):
            qb.app("bq1234567").table("Tasks").delete(F("Title") == "x")


def test_request_is_checked_and_trimmed() -> None:
    fake = Fake()
    with client(fake, rules()) as qb:
        fields = qb.request("GET", "/fields", params={"tableId": TASKS})
        assert [item["id"] for item in fields] == [3, 6, 7, 10]
        found = qb.request(
            "POST",
            "/records/query",
            json={"from": TASKS, "select": [6], "sortBy": [], "options": {"top": 1}},
        )
        assert set(found["data"][0]) == {"6"}
        for body in (
            {"from": TASKS, "select": [9], "sortBy": [], "options": {"top": 1}},
            {"from": TASKS, "select": [6], "options": {"top": 1}},
            {"from": TASKS, "select": [6], "sortBy": []},
            {"from": SECRET, "select": [6], "sortBy": [], "options": {"top": 1}},
        ):
            with pytest.raises(PolicyRefusedError):
                qb.request("POST", "/records/query", json=body)
        with pytest.raises(PolicyRefusedError):
            qb.request("GET", "/fields", params={"tableId": SECRET})
    assert len(queries(fake)) == 1


def test_preview_update_of_a_hidden_field_is_refused() -> None:
    fake = Fake()
    with client(fake, rules(allow.update(TASKS, ["Notes", "Status"]))) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(PolicyRefusedError, match="reading Notes"):
            tasks.preview_update([{"Record ID#": 1, "Notes": "x"}])
        preview = tasks.preview_update([{"Record ID#": 1, "Status": "Done"}])
        assert [change.record_id for change in preview.changes] == [1]


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class Tasks(TypedRecord):
    __table_id__: ClassVar[str] = TASKS
    __columns__: ClassVar[tuple[Column, ...]] = (
        Column("id", 3, "recordid", False, "Record ID#"),
        Column("notes", 9, "text", True, "Notes"),
    )


def test_a_generated_class_with_a_hidden_field_doesnt_match() -> None:
    with client(Fake(), rules()) as qb:
        with pytest.raises(SchemaMismatchError, match="notes"):
            qb.app("bq1234567").typed(Tasks).first()


class Log:
    def __init__(self) -> None:
        self.seen: list[ReadRequest | WriteRequest] = []

    def decide(self, request: ReadRequest | WriteRequest) -> Decision | None:
        self.seen.append(request)
        return None


def test_a_rule_of_your_own_sees_every_read_the_rules_allow() -> None:
    log = Log()
    policy = bind(allow.read(TASKS), log, tables={TASKS: ("Tasks", FIELDS)})
    with client(Fake(), policy) as qb:
        record = qb.app("bq1234567").table("Tasks").first(select=["Notes"])
    assert record is not None
    assert record[9] == "s3cret"
    assert [item.action for item in log.seen] == ["tables", "fields", "query"]
    assert log.seen[-1].field_ids == {3, 9}  # pyright: ignore[reportAttributeAccessIssue]
