# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Links to the Quickbase interface, through a client, so name lookups and read rules apply."""

from typing import Any

import httpx
import pytest

from pyquickbase import (
    BoundPolicy,
    F,
    InvalidQueryError,
    InvalidValueError,
    Quickbase,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    allow,
    where_from_dict,
)

from ..policy_helpers import bind

APP = "bq1234567"
TASKS = "bqtasks"
SECRET = "bqsecret"
BASE = f"https://acme.quickbase.com/nav/app/{APP}/table/{TASKS}/action"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 7, "label": "Status", "fieldType": "text"},
    {"id": 8, "label": "Salary", "fieldType": "numeric"},
]
REPORTS = [
    {"id": "1", "name": "List All", "type": "table"},
    {"id": "12", "name": "Open & Late", "type": "table"},
]


class FakeSchema:
    """Serves metadata only, and records every request, as a link never needs more."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def paths(self) -> list[str]:
        return [request.url.path.removeprefix("/v1") for request in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        payload: Any = {
            "/tables": [{"id": TASKS, "name": "Tasks"}, {"id": SECRET, "name": "Secret"}],
            "/fields": FIELDS,
            "/reports": REPORTS,
        }.get(path)
        if payload is None:
            raise AssertionError(f"A link shouldn't need {request.method} {path}.")
        return httpx.Response(200, json=payload)


def client(server: Any, **kwargs: Any) -> Quickbase:
    http_client = httpx.Client(transport=httpx.MockTransport(server))
    return Quickbase(realm="acme", token="fake-user-token", http_client=http_client, **kwargs)


def rules() -> BoundPolicy:
    return bind(
        allow.read(TASKS, [6, 7]),
        allow.report(TASKS, ["1"]),
        tables={TASKS: ("Tasks", FIELDS)},
        reports={TASKS: REPORTS},
        app_id=APP,
    )


def test_app_url_makes_no_request() -> None:
    server = FakeSchema()
    with client(server) as qb:
        assert qb.app(APP).url() == f"https://acme.quickbase.com/nav/app/{APP}/action/appoverview"
    assert server.requests == []


@pytest.mark.parametrize("ref", ["Tasks", TASKS])
def test_table_url_by_name_or_id(ref: str) -> None:
    with client(FakeSchema()) as qb:
        assert qb.app(APP).table(ref).url() == f"{BASE}/td"


def test_links_use_the_cache() -> None:
    server = FakeSchema()
    with client(server) as qb:
        tasks = qb.app(APP).table("Tasks")
        for _ in range(2):
            tasks.url()
            tasks.record_url(1)
            tasks.record_url_template()
            tasks.field_url("Title")
            tasks.report("List All").url()
    assert server.paths() == ["/tables", "/fields", "/reports"]


def test_links_use_the_client_realm() -> None:
    http_client = httpx.Client(transport=httpx.MockTransport(FakeSchema()))
    with Quickbase(
        realm="https://Builder.Example.com/db/main", token="t", http_client=http_client
    ) as qb:
        assert qb.app(APP).url().startswith("https://builder.example.com/nav/app/")


def test_record_urls() -> None:
    with client(FakeSchema()) as qb:
        tasks = qb.app(APP).table("Tasks")
        assert tasks.record_url(42) == f"{BASE}/dr?rid=42"
        assert tasks.record_url(42, edit=True) == f"{BASE}/er?rid=42"


@pytest.mark.parametrize("record_id", [0, -1, True, 1.0, "7", "7&a=b", None])
def test_record_url_refuses_anything_but_a_positive_int(record_id: Any) -> None:
    server = FakeSchema()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="record_id"):
            qb.app(APP).table("Tasks").record_url(record_id)
    assert server.requests == []


def test_record_url_refuses_edit_that_isnt_a_bool() -> None:
    with client(FakeSchema()) as qb:
        with pytest.raises(TypeError, match="edit"):
            qb.app(APP).table("Tasks").record_url(1, edit=1)  # pyright: ignore[reportArgumentType]


def test_record_url_template() -> None:
    with client(FakeSchema()) as qb:
        tasks = qb.app(APP).table("Tasks")
        pattern = tasks.record_url_template()
        assert pattern == f"{BASE}/dr?rid={{record_id}}"
        assert tasks.record_url_template(edit=True) == f"{BASE}/er?rid={{record_id}}"
        assert pattern.replace("{record_id}", "42") == tasks.record_url(42)
        assert pattern.format_map({"record_id": 42}) == tasks.record_url(42)


@pytest.mark.parametrize("field", ["Title", 6])
def test_field_url_by_label_or_id(field: str | int) -> None:
    with client(FakeSchema()) as qb:
        assert qb.app(APP).table("Tasks").field_url(field) == f"{BASE}/mf?fid=6"


@pytest.mark.parametrize("field", ["Nope", 99, True])
def test_field_url_refuses_an_unknown_field(field: Any) -> None:
    with client(FakeSchema()) as qb:
        with pytest.raises(UnknownFieldError):
            qb.app(APP).table("Tasks").field_url(field)


@pytest.mark.parametrize("ref", ["List All", "1"])
def test_report_url_by_name_or_id(ref: str) -> None:
    with client(FakeSchema()) as qb:
        assert qb.app(APP).table("Tasks").report(ref).url() == f"{BASE}/q?qid=1"


def test_report_url_with_a_name_that_needs_encoding_uses_the_id() -> None:
    with client(FakeSchema()) as qb:
        url = qb.app(APP).table(TASKS).report("Open & Late").url()
        assert url == f"{BASE}/q?qid=12"


def test_unknown_table_and_report_are_refused() -> None:
    with client(FakeSchema()) as qb:
        with pytest.raises(UnknownTableError):
            qb.app(APP).table("Nope").url()
        with pytest.raises(UnknownTableError):
            qb.app(APP).table("Nope").record_url(1)
        with pytest.raises(UnknownReportError):
            qb.app(APP).table("Tasks").report("Nope").url()


def test_read_rules_hide_tables_fields_and_reports() -> None:
    with client(FakeSchema(), policy=rules()) as qb:
        app = qb.app(APP)
        tasks = app.table(TASKS)
        assert tasks.url() == f"{BASE}/td"
        assert tasks.field_url("Status") == f"{BASE}/mf?fid=7"
        assert tasks.report("1").url() == f"{BASE}/q?qid=1"
        assert tasks.record_url(5) == f"{BASE}/dr?rid=5"
        for link in (
            lambda: app.table(SECRET).url(),
            lambda: app.table("Secret").record_url(1),
            lambda: app.table(SECRET).record_url_template(),
            lambda: app.table(SECRET).field_url(6),
        ):
            with pytest.raises(UnknownTableError):
                link()
        with pytest.raises(UnknownFieldError):
            tasks.field_url("Salary")
        with pytest.raises(UnknownFieldError):
            tasks.field_url(8)
        with pytest.raises(UnknownReportError):
            tasks.report("12").url()


def test_compile_where() -> None:
    with client(FakeSchema()) as qb:
        tasks = qb.app(APP).table("Tasks")
        assert tasks.compile_where(F("Status") == "Open") == "{7.EX.'Open'}"
        condition = where_from_dict(
            {
                "all": [
                    {"field": "Status", "op": "==", "value": "Open"},
                    {"field": "Title", "op": "contains", "value": "slab"},
                ]
            }
        )
        assert tasks.compile_where(condition) == "{7.EX.'Open'}AND{6.CT.'slab'}"
        assert tasks.compile_where("  {6.CT.'x'} ") == "{6.CT.'x'}"


def test_compile_where_refuses_what_a_query_would() -> None:
    with client(FakeSchema(), policy=rules()) as qb:
        tasks = qb.app(APP).table("Tasks")
        with pytest.raises(UnknownFieldError):
            tasks.compile_where(F("Salary") > 1)
        with pytest.raises(InvalidQueryError):
            tasks.compile_where("{6.CT.'x'")
        with pytest.raises(InvalidQueryError):
            tasks.compile_where(7)  # pyright: ignore[reportArgumentType]


def test_app_settings_url_makes_no_request() -> None:
    server = FakeSchema()
    with client(server) as qb:
        url = qb.app(APP).settings_url()
    assert url == f"https://acme.quickbase.com/nav/app/{APP}/action/appsettingshome"
    assert server.requests == []


def test_table_page_urls() -> None:
    server = FakeSchema()
    with client(server) as qb:
        tasks = qb.app(APP).table("Tasks")
        assert tasks.new_record_url() == f"{BASE}/nwr"
        assert tasks.fields_url() == f"{BASE}/listfields"
        assert tasks.relationships_url() == f"{BASE}/Relationships"
        assert tasks.settings_url() == f"{BASE}/KeyProps"
        assert tasks.form_url(10) == f"{BASE}/DFormProps?dfid=10"
    assert server.paths() == ["/tables"]


@pytest.mark.parametrize("form_id", [0, -3, False, 2.0, "10", "1&a=b", None])
def test_form_url_refuses_anything_but_a_positive_int(form_id: Any) -> None:
    server = FakeSchema()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="form_id"):
            qb.app(APP).table("Tasks").form_url(form_id)
    assert server.requests == []


def test_report_edit_url() -> None:
    with client(FakeSchema()) as qb:
        report = qb.app(APP).table("Tasks").report("List All")
        assert report.url(edit=True) == f"{BASE}/viewBuild?qid=1"
        with pytest.raises(TypeError, match="edit"):
            report.url(edit="yes")  # pyright: ignore[reportArgumentType]


def test_read_rules_hide_table_pages() -> None:
    with client(FakeSchema(), policy=rules()) as qb:
        app = qb.app(APP)
        assert app.table(TASKS).settings_url() == f"{BASE}/KeyProps"
        secret = app.table(SECRET)
        for link in (
            secret.new_record_url,
            secret.fields_url,
            secret.relationships_url,
            secret.settings_url,
            lambda: secret.form_url(10),
        ):
            with pytest.raises(UnknownTableError):
                link()
        with pytest.raises(UnknownReportError):
            app.table(TASKS).report("12").url(edit=True)
