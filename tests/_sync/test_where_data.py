# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Filters from plain data, used through a client as a tool for a model would use them."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    F,
    QueryError,
    Quickbase,
    WriteRefusedError,
    where_from_dict,
    where_schema,
)

FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text", "appearsByDefault": True},
    {"id": 7, "label": "Status", "fieldType": "text-multiple-choice", "appearsByDefault": True},
    {"id": 8, "label": "Due", "fieldType": "date", "appearsByDefault": True},
]


class Fake:
    def __init__(self) -> None:
        self.bodies: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tables"):
            return httpx.Response(200, json=[{"id": "bqtasks", "name": "Tasks"}])
        if request.url.path.endswith("/fields"):
            return httpx.Response(200, json=FIELDS)
        body = json.loads(request.content)
        self.bodies.append((request.method, body))
        if request.method == "DELETE":
            return httpx.Response(200, json={"numberDeleted": 2})
        return httpx.Response(
            200,
            json={
                "data": [],
                "fields": [],
                "metadata": {"totalRecords": 0, "numRecords": 0, "skip": 0, "numFields": 0},
            },
        )


def client(fake: Fake, **kwargs: Any) -> Quickbase:
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return Quickbase(realm="acme", token="t", http_client=http, **kwargs)


def test_a_tool_call_becomes_a_query() -> None:
    fake = Fake()
    arguments = json.loads(
        """{"where": {"all": [
            {"field": "Status", "op": "==", "value": "Open"},
            {"field": "Due", "op": "<", "value": "2026-12-01"}
        ]}}"""
    )
    with client(fake, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        tasks.query_page(where=where_from_dict(arguments["where"]))
    assert fake.bodies[0][1]["where"] == "{7.EX.'Open'}AND{8.BF.'2026-12-01'}"


def test_code_can_fix_the_scope() -> None:
    fake = Fake()
    data = {"any": [{"field": "Status", "op": "==", "value": "Open"}]}
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        tasks.count((F("Title") == "Mine") & where_from_dict(data))
    assert fake.bodies[0][1]["where"] == "{6.EX.'Mine'}AND{7.EX.'Open'}"


def test_a_bad_value_for_the_field_sends_nothing() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(QueryError, match="YYYY-MM-DD"):
            tasks.delete(where_from_dict({"field": "Due", "op": "<", "value": "garbage"}))
    assert fake.bodies == []


def test_delete_uses_the_same_checked_filter() -> None:
    fake = Fake()
    with client(fake) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        where = where_from_dict({"field": "Status", "op": "==", "value": "Done"})
        assert tasks.delete(where) == 2
    assert fake.bodies == [("DELETE", {"from": "bqtasks", "where": "{7.EX.'Done'}"})]


def test_a_read_only_client_still_refuses_a_delete() -> None:
    fake = Fake()
    with client(fake, read_only=True) as qb:
        tasks = qb.app("bq1234567").table("Tasks")
        with pytest.raises(WriteRefusedError):
            tasks.delete(where_from_dict({"field": "Status", "op": "==", "value": "Done"}))


def test_the_schema_can_offer_a_tables_fields() -> None:
    with client(Fake()) as qb:
        fields = qb.app("bq1234567").table("Tasks").fields()
    schema = where_schema(fields=fields, max_depth=0)
    assert schema["properties"]["field"]["enum"] == ["Due", "Record ID#", "Status", "Title"]
