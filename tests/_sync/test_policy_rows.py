# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Row limits and created tables through a client: what reaches Quickbase."""

import json
import re
from typing import Any

import httpx
import pytest

from pyquickbase import (
    BoundPolicy,
    F,
    Policy,
    PolicyRefusedError,
    Quickbase,
    ReadRequest,
    allow,
    limit,
    tables,
)

from ..policy_helpers import bind, field, link

APP = "bq1234567"
TASKS = "bqtasks"
PROJECTS = "bqprojects"
TASK_FIELDS = [
    field(3, "Record ID#", "recordid"),
    field(6, "Title"),
    field(7, "Billable", "checkbox"),
    field(8, "Hours", "numeric"),
    field(9, "Project", "numeric", properties={"foreignKey": True}),
]
PROJECT_FIELDS = [field(3, "Record ID#", "recordid"), field(6, "Name")]
RELATIONSHIP = link(9, PROJECTS, TASKS, 9)


class Fake:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self.fields = {TASKS: list(TASK_FIELDS), PROJECTS: list(PROJECT_FIELDS)}
        self.matching = 2
        self.relationship = RELATIONSHIP
        # The records a row limit's check query finds, by Record ID#.
        self.reachable: set[int] = set()

    def bodies(self, method: str, path: str) -> list[Any]:
        return [body for m, p, body in self.sent if (m, p) == (method, path)]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        self.sent.append((request.method, path, body))
        table = request.url.params.get("tableId", "")
        if path == "/tables" and request.method == "POST":
            return httpx.Response(200, json={"id": "bqnew", "name": body["name"]})
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks", "keyFieldId": 3},
                                             {"id": PROJECTS, "name": "Projects"}])  # fmt: skip
        if path == "/fields":
            return httpx.Response(200, json=self.fields[table])
        if path == "/reports":
            found = [{"id": "1", "name": "Open", "type": "table"},
                     {"id": "2", "name": "Closed", "type": "table"}]  # fmt: skip
            return httpx.Response(200, json=found)
        if path.endswith("/relationships"):
            rows = [self.relationship] if path.split("/")[2] == TASKS else []
            metadata = {"totalRelationships": len(rows)}
            return httpx.Response(200, json={"relationships": rows, "metadata": metadata})
        if path == "/records/query" and "{3.EX." in body.get("where", ""):
            asked = {int(item) for item in re.findall(r"\{3\.EX\.(\d+)\}", body["where"])}
            rows = [{"3": {"value": item}} for item in sorted(asked & self.reachable)]
            return httpx.Response(200, json={"data": rows, "fields": [], "metadata": {
                "totalRecords": len(rows), "numRecords": len(rows), "skip": 0}})  # fmt: skip
        if path == "/records" and request.method == "POST":
            ids = [row["3"]["value"] for row in body["data"] if "3" in row]
            return httpx.Response(200, json={"data": [], "metadata": {
                "createdRecordIds": [], "updatedRecordIds": ids, "unchangedRecordIds": [],
                "totalNumberOfRecordsProcessed": len(body["data"])}})  # fmt: skip
        if path == "/records/query":
            return httpx.Response(200, json={"data": [], "fields": [],
                                             "metadata": {"totalRecords": self.matching,
                                                          "numRecords": 0,
                                                          "skip": 0}})  # fmt: skip
        if path == "/records" and request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": self.matching})
        if path.startswith(f"/tables/{TASKS}/relationship/"):
            for summary in body.get("summaryFields") or ():
                self.fields[PROJECTS].append(
                    {"id": 40, "label": summary["label"], "fieldType": "numeric",
                     "mode": "summary", "properties": {
                         "summaryReferenceFieldId": 9, "summaryTargetFieldId": 0,
                         "summaryFunction": "COUNT", "summaryQuery": summary.get("where", "")}}
                )  # fmt: skip
            return httpx.Response(200, json=RELATIONSHIP)
        if path == "/tables" and request.method == "POST":
            return httpx.Response(200, json={"id": "bqnew", "name": body["name"]})
        return httpx.Response(404, json={"message": "Not found"})


def policy(*rules: Any) -> BoundPolicy:
    return bind(
        allow.read(tables.all()),
        *rules,
        tables={TASKS: ("Tasks", TASK_FIELDS), PROJECTS: ("Projects", PROJECT_FIELDS)},
        relationships=[RELATIONSHIP],
        app_id=APP,
    )


def client(fake: Fake, bound: BoundPolicy) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, policy=bound)


BILLABLE = limit.rows(TASKS, F("Billable") == True)  # noqa: E712


def test_the_row_limit_is_anded_onto_every_query_and_delete() -> None:
    fake = Fake()
    with client(fake, policy(allow.delete(TASKS), BILLABLE)) as qb:
        tasks = qb.app(APP).table("Tasks")
        tasks.count()
        tasks.count(F("Hours") > 2)
        tasks.delete(F("Hours") > 2)
        qb.request(
            "POST",
            "/records/query",
            json={"from": TASKS, "select": [6], "sortBy": [], "options": {"top": 1}},
        )
    wheres = [body["where"] for body in fake.bodies("POST", "/records/query")]
    assert wheres == [
        "{7.EX.'true'}",
        "({7.EX.'true'})AND({8.GT.'2'})",
        "{7.EX.'true'}",
    ]
    (deleted,) = fake.bodies("DELETE", "/records")
    assert deleted["where"] == "({7.EX.'true'})AND({8.GT.'2'})"


def test_a_summary_of_a_row_limited_child_gets_the_limit_in_its_filter() -> None:
    fake = Fake()
    with client(fake, policy(allow.schema(PROJECTS, "summary"), BILLABLE)) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (tasks.relationships())[0]
        tasks.ensure_count_field(relationship, "Long Tasks", where=F("Hours") > 4)
        tasks.ensure_count_field(relationship, "Long Tasks", where=F("Hours") > 4)
    (sent,) = fake.bodies("POST", f"/tables/{TASKS}/relationship/9")
    assert sent["summaryFields"][0]["where"] == "({7.EX.'true'})AND({8.GT.'4'})"


def test_a_lookup_from_a_row_limited_parent_is_refused() -> None:
    fake = Fake()
    rows = limit.rows(PROJECTS, F("Name") == "Mine")
    with client(fake, policy(allow.schema(TASKS, "lookup"), rows)) as qb:
        tasks = qb.app(APP).table("Tasks")
        relationship = (tasks.relationships())[0]
        with pytest.raises(PolicyRefusedError, match="Refused by rows") as caught:
            tasks.ensure_lookup_field(relationship, "Name")
    assert caught.value.decision.rule == "limit.rows('bqprojects', F('Name') == 'Mine')"
    assert not fake.bodies("POST", f"/tables/{TASKS}/relationship/9")


class RefusesRelationships(Fake):
    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/relationships"):
            return httpx.Response(403, json={"message": "Insufficient permissions"})
        return super().__call__(request)


def test_a_role_that_cant_read_relationships_can_still_bind() -> None:
    fake = RefusesRelationships()
    properties = {"lookupReferenceFieldId": 9, "lookupTargetFieldId": 6}
    fake.fields[TASKS].append(field(20, "Project Name", mode="lookup", properties=properties))
    http = httpx.Client(transport=httpx.MockTransport(fake))
    reads = allow.read(tables.all())
    with Quickbase(realm="acme", token="t", http_client=http) as qb:
        app = qb.app(APP)
        everything = Policy(rules=[reads]).bind(app)
        limited = Policy(rules=[reads, limit.rows(PROJECTS, F("Name") == "Mine")]).bind(app)
    assert everything._visible(TASKS, 20)
    assert not limited._visible(TASKS, 20)
    assert limited._visible(TASKS, 6)


def test_creating_a_table_gives_the_client_a_new_policy() -> None:
    fake = Fake()
    bound = policy(allow.create_tables(), allow.read(tables.created()))
    with client(fake, bound) as qb:
        assert qb.policy is bound
        qb.app(APP).create_table("Visits")
        assert qb.policy is not bound
        assert qb.policy is not None
        assert qb.policy.created == ("bqnew",)
        saved = qb.policy.to_json()
    assert bound.created == ()
    assert BoundPolicy.from_json(saved).created == ("bqnew",)


@pytest.mark.parametrize(("most", "refused"), [(1, True), (2, False), (3, False)])
def test_a_delete_is_refused_when_more_records_match_than_its_limit(
    most: int, refused: bool
) -> None:
    fake = Fake()  # Two records match.
    rules = policy(allow.delete(TASKS, max_records_per_delete=most))
    with client(fake, rules) as qb:
        tasks = qb.app(APP).table("Tasks")
        if refused:
            with pytest.raises(PolicyRefusedError, match=r"2 records match.*at most 1 at once"):
                tasks.delete(F("Hours") > 2)
        else:
            assert tasks.delete(F("Hours") > 2) == 2
    (counted,) = fake.bodies("POST", "/records/query")
    assert counted["where"] == "{8.GT.'2'}"
    deletes = fake.bodies("DELETE", "/records")
    assert deletes == ([] if refused else [{"from": TASKS, "where": "{8.GT.'2'}"}])
    if not refused:
        order = [method for method, path, _ in fake.sent if path.startswith("/records")]
        assert order == ["POST", "DELETE"]


def test_a_delete_limit_counts_only_the_records_a_row_limit_reaches() -> None:
    fake = Fake()
    with client(fake, policy(allow.delete(TASKS, max_records_per_delete=5), BILLABLE)) as qb:
        qb.app(APP).table("Tasks").delete(F("Hours") > 2)
    (counted,) = fake.bodies("POST", "/records/query")
    assert counted["where"] == "({7.EX.'true'})AND({8.GT.'2'})"


@pytest.mark.parametrize(
    ("method", "path", "extra"),
    [
        ("POST", "/records/query", {"select": [6], "sortBy": [], "options": {"top": 1}}),
        ("DELETE", "/records", {}),
    ],
    ids=["query", "delete"],
)
def test_a_filter_that_would_escape_a_row_limit_is_refused(
    method: str, path: str, extra: dict[str, Any]
) -> None:
    fake = Fake()
    body = {"from": TASKS, "where": "{8.GT.'0'})OR({8.GT.'0'}", **extra}
    with client(fake, policy(allow.delete(TASKS), BILLABLE)) as qb:
        with pytest.raises(PolicyRefusedError, match="row limit can't be added") as caught:
            qb.request(method, path, json=body)
    assert caught.value.decision.layer == "rows"
    assert fake.bodies(method, path) == []


def test_a_real_or_stays_inside_the_row_limit() -> None:
    fake = Fake()
    body = {"from": TASKS, "where": "{8.GT.'9'}OR{8.LT.'1'}"}
    with client(fake, policy(allow.delete(TASKS), BILLABLE)) as qb:
        qb.request("DELETE", "/records", json=body)
    (sent,) = fake.bodies("DELETE", "/records")
    assert sent["where"] == "({7.EX.'true'})AND({8.GT.'9'}OR{8.LT.'1'})"


def test_binding_to_an_app_reads_the_reports_a_rule_names() -> None:
    fake = Fake()
    http = httpx.Client(transport=httpx.MockTransport(fake))
    with Quickbase(realm="acme", token="t", http_client=http) as qb:
        bound = Policy(rules=[allow.report("Tasks", ["Open"])]).bind(qb.app(APP))
    allowed, other = (
        bound.decide(
            ReadRequest(action="report", table_id=TASKS, report_id=item, limit=1, skip=0,
                        method="POST", path=f"/reports/{item}/run")
        )
        for item in ("1", "2")
    )  # fmt: skip
    assert allowed.allowed
    assert not other.allowed


def test_binding_to_an_app_learns_its_relationships() -> None:
    fake = Fake()
    properties = {"lookupReferenceFieldId": 9, "lookupTargetFieldId": 6}
    fake.fields[TASKS].append(field(20, "Project Name", mode="lookup", properties=properties))
    fake.relationship = link(9, PROJECTS, TASKS, 9, lookups=[20])
    http = httpx.Client(transport=httpx.MockTransport(fake))
    reads = allow.read(tables.all())
    with Quickbase(realm="acme", token="t", http_client=http) as qb:
        rules = [reads, limit.rows(PROJECTS, F("Name") == "Mine")]
        limited = Policy(rules=rules).bind(qb.app(APP))
    # Only the relationship says that this lookup shows Projects, which has the limit.
    assert not limited._visible(TASKS, 20)
    assert limited._visible(TASKS, 6)


UPDATE_TITLES = allow.update(TASKS, ["Title"])


def test_an_update_by_record_id_is_checked_against_the_row_limit_first() -> None:
    fake = Fake()
    fake.reachable = {1, 2}
    with client(fake, policy(UPDATE_TITLES, BILLABLE)) as qb:
        tasks = qb.app(APP).table("Tasks")
        tasks.update([{"Record ID#": 1, "Title": "a"}, {"Record ID#": 2, "Title": "b"}])
    (check,) = fake.bodies("POST", "/records/query")
    assert check["where"] == "({7.EX.'true'})AND({3.EX.1}OR{3.EX.2})"
    assert check["select"] == [3]
    assert len(fake.bodies("POST", "/records")) == 1


def test_an_update_naming_a_record_outside_the_row_limit_is_refused() -> None:
    fake = Fake()
    fake.reachable = {1}
    with client(fake, policy(UPDATE_TITLES, BILLABLE)) as qb:
        tasks = qb.app(APP).table("Tasks")
        with pytest.raises(PolicyRefusedError, match="Record ID# 5 isn't within") as caught:
            tasks.update([{"Record ID#": 1, "Title": "a"}, {"Record ID#": 5, "Title": "b"}])
    assert caught.value.decision.layer == "rows"
    assert fake.bodies("POST", "/records") == []


def test_a_raw_update_is_checked_and_many_records_take_several_queries() -> None:
    fake = Fake()
    fake.reachable = set(range(1, 151))
    rows = [{"3": {"value": item}, "6": {"value": "x"}} for item in range(1, 151)]
    with client(fake, policy(UPDATE_TITLES, BILLABLE)) as qb:
        qb.request("POST", "/records", json={"to": TASKS, "data": rows, "mergeFieldId": 3})
    assert len(fake.bodies("POST", "/records/query")) == 2
    assert len(fake.bodies("POST", "/records")) == 1


def test_more_than_five_records_outside_the_limit_are_counted() -> None:
    fake = Fake()
    rows = [{"3": {"value": item}, "6": {"value": "x"}} for item in range(1, 9)]
    with client(fake, policy(UPDATE_TITLES, BILLABLE)) as qb:
        with pytest.raises(PolicyRefusedError, match="1, 2, 3, 4, 5 and 3 more"):
            qb.request("POST", "/records", json={"to": TASKS, "data": rows})


@pytest.mark.parametrize("value", ["abc", None, True, 0, -2, {"x": 1}])
def test_an_update_whose_record_ids_cant_be_read_is_refused(value: Any) -> None:
    fake = Fake()
    rows = [{"3": {"value": value}, "6": {"value": "x"}}]
    with client(fake, policy(UPDATE_TITLES, BILLABLE)) as qb:
        with pytest.raises(PolicyRefusedError, match="Record ID# values can't be read"):
            qb.request("POST", "/records", json={"to": TASKS, "data": rows})
    assert fake.sent == []


def test_record_ids_written_as_digits_are_checked() -> None:
    fake = Fake()
    fake.reachable = {4}
    rows = [{"3": {"value": "4"}, "6": {"value": "x"}}]
    with client(fake, policy(UPDATE_TITLES, BILLABLE)) as qb:
        qb.request("POST", "/records", json={"to": TASKS, "data": rows})
    (check,) = fake.bodies("POST", "/records/query")
    assert check["where"].endswith("({3.EX.4})")


def test_an_upsert_on_a_row_limited_table_is_refused() -> None:
    fake = Fake()
    rows = [{"6": {"value": "x"}}]
    body = {"to": TASKS, "data": rows, "mergeFieldId": 6}
    bound = policy(allow.insert(TASKS, ["Title"]), UPDATE_TITLES, BILLABLE)
    with client(fake, bound) as qb:
        with pytest.raises(PolicyRefusedError, match="an upsert can change any record") as caught:
            qb.request("POST", "/records", json=body)
    assert caught.value.decision.layer == "rows"
    assert fake.sent == []


def test_inserts_and_tables_without_a_row_limit_send_no_check() -> None:
    fake = Fake()
    bound = policy(allow.insert(TASKS, ["Title"]), UPDATE_TITLES, allow.update(PROJECTS, ["Name"]),
                   allow.insert(PROJECTS, ["Name"]), BILLABLE)  # fmt: skip
    with client(fake, bound) as qb:
        app = qb.app(APP)
        app.table("Tasks").insert([{"Title": "new"}])
        app.table("Projects").update([{"Record ID#": 9, "Name": "Roof"}])
        app.table("Projects").upsert([{"Name": "Roof"}], merge_on="Name")
    assert fake.bodies("POST", "/records/query") == []
    assert len(fake.bodies("POST", "/records")) == 3
