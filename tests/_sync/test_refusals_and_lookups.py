# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Tests for how refused writes are reported, `first()`, `get()`, `roles()`, and `token_env`."""

import json
from typing import Any

import httpx
import pytest

from pyquickbase import (
    ConfigurationError,
    InvalidValueError,
    PermissionDeniedError,
    Quickbase,
    WriteError,
    WritePermissionDeniedError,
)
from pyquickbase._writes import row_error

APP = "bq1234567"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
]
FORBIDDEN = {
    "message": "Insufficient permissions",
    "description": "Not enough rights to execute this request.",
}


def client_for(handler: Any) -> Quickbase:
    def wrapped(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": "bqtasks", "name": "Tasks"}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        return handler(request, path)

    http = httpx.Client(transport=httpx.MockTransport(wrapped))
    return Quickbase(realm="acme", token="t", http_client=http)


def write_response(line_errors: dict[str, list[str]] | None = None) -> httpx.Response:
    metadata: dict[str, Any] = {
        "createdRecordIds": [],
        "updatedRecordIds": [],
        "unchangedRecordIds": [],
        "totalNumberOfRecordsProcessed": 1,
    }
    if line_errors:
        metadata["lineErrors"] = line_errors
    return httpx.Response(207 if line_errors else 200, json={"data": [], "metadata": metadata})


@pytest.mark.parametrize(
    ("message", "kind", "field_id"),
    [
        # Each of these is Quickbase's exact wording, seen against a live app.
        ('Adding non-unique value for field with ID "8".', "duplicate", 8),
        ("The field with ID '7' has an invalid choice.", "invalid_choice", 7),
        ("You don't have permission to add records to this table.", "permission", None),
        ("You don't have permission to update records in this table.", "permission", None),
        ("Insufficient permissions to update data in a restricted field.", "permission", None),
        ("Table rule validation: This invoice has been issued.", "rule", None),
        # A rule's text is free-form, so words from other kinds must not change what it counts as.
        ("Table rule validation: You don't have permission to do this.", "rule", None),
        ("Something Quickbase has never said before.", "other", None),
    ],
)
def test_row_errors_are_classified(message: str, kind: str, field_id: int | None) -> None:
    error = row_error(0, (message,))
    assert error.reason == kind
    assert error.field_id == field_id
    assert error.messages == (message,)


def test_a_403_raises_a_permission_error_that_is_also_a_write_error() -> None:
    def handler(request: httpx.Request, path: str) -> httpx.Response:
        return httpx.Response(403, json=FORBIDDEN, headers={"qb-api-ray": "ray-1"})

    with client_for(handler) as qb:
        with pytest.raises(WritePermissionDeniedError) as caught:
            qb.app(APP).table("Tasks").insert([{"Title": "x"}])
    error = caught.value
    assert isinstance(error, WriteError)
    assert isinstance(error, PermissionDeniedError)
    assert error.status_code == 403
    assert error.request_id == "ray-1"
    assert error.result.created_ids == ()


def test_permission_row_errors_raise_the_same_error() -> None:
    def handler(request: httpx.Request, path: str) -> httpx.Response:
        return write_response({"1": ["You don't have permission to add records to this table."]})

    with client_for(handler) as qb:
        with pytest.raises(WritePermissionDeniedError) as caught:
            qb.app(APP).table("Tasks").insert([{"Title": "x"}])
    assert caught.value.status_code == 207
    assert caught.value.result.failed_rows[0].reason == "permission"


def test_other_row_errors_stay_a_plain_write_error() -> None:
    def handler(request: httpx.Request, path: str) -> httpx.Response:
        return write_response({"1": ['Adding non-unique value for field with ID "8".']})

    with client_for(handler) as qb:
        with pytest.raises(WriteError) as caught:
            qb.app(APP).table("Tasks").insert([{"Title": "x"}])
    assert not isinstance(caught.value, PermissionDeniedError)
    assert caught.value.result.failed_rows[0].reason == "duplicate"
    assert caught.value.result.failed_rows[0].field_id == 8


def test_mixed_row_errors_are_a_plain_write_error() -> None:
    def handler(request: httpx.Request, path: str) -> httpx.Response:
        return write_response(
            {
                "1": ["You don't have permission to add records to this table."],
                "2": ['Adding non-unique value for field with ID "8".'],
            }
        )

    with client_for(handler) as qb:
        with pytest.raises(WriteError) as caught:
            qb.app(APP).table("Tasks").insert([{"Title": "x"}, {"Title": "y"}])
    assert not isinstance(caught.value, PermissionDeniedError)
    assert [e.reason for e in caught.value.result.failed_rows] == ["permission", "duplicate"]


def test_a_403_on_a_later_batch_keeps_the_earlier_results() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request, path: str) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                200,
                json={
                    "data": [],
                    "metadata": {
                        "createdRecordIds": [1],
                        "updatedRecordIds": [],
                        "unchangedRecordIds": [],
                        "totalNumberOfRecordsProcessed": 1,
                    },
                },
            )
        return httpx.Response(403, json=FORBIDDEN)

    with client_for(handler) as qb:
        with pytest.raises(WritePermissionDeniedError, match="stopped at row 1") as caught:
            qb.app(APP).table("Tasks").insert([{"Title": "a"}, {"Title": "b"}], batch_size=1)
    assert caught.value.result.created_ids == (1,)


def query_handler(bodies: list[Any], data: list[dict[str, Any]]) -> Any:
    def handler(request: httpx.Request, path: str) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": data,
                "fields": [{"id": 3, "label": "Record ID#", "type": "recordid"},
                           {"id": 6, "label": "Title", "type": "text"}],
                "metadata": {"numFields": 2, "numRecords": len(data), "skip": 0,
                             "totalRecords": len(data), "top": 1},
            },
        )  # fmt: skip

    return handler


def test_first_returns_a_record_or_none() -> None:
    bodies: list[Any] = []
    row = {"3": {"value": 5}, "6": {"value": "A"}}
    with client_for(query_handler(bodies, [row])) as qb:
        record = qb.app(APP).table("Tasks").first(select=["Title"], order_by=["Title"])
    assert record is not None
    assert record["Title"] == "A"
    assert bodies[0]["options"]["top"] == 1
    with client_for(query_handler([], [])) as qb:
        assert qb.app(APP).table("Tasks").first() is None


def test_get_filters_by_record_id() -> None:
    bodies: list[Any] = []
    row = {"3": {"value": 5}, "6": {"value": "A"}}
    with client_for(query_handler(bodies, [row])) as qb:
        record = qb.app(APP).table("Tasks").get(5, select=["Title"])
    assert record is not None
    assert record["Record ID#"] == 5
    assert bodies[0]["where"] == "{3.EX.'5'}"
    with client_for(query_handler([], [])) as qb:
        assert qb.app(APP).table("Tasks").get(99) is None


@pytest.mark.parametrize("bad", [0, -1, True, "5"])
def test_get_refuses_anything_but_a_record_id(bad: Any) -> None:
    with client_for(query_handler([], [])) as qb:
        with pytest.raises(InvalidValueError):
            qb.app(APP).table("Tasks").get(bad)


def test_roles_are_listed_and_cached() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request, path: str) -> httpx.Response:
        assert path == f"/apps/{APP}/roles"
        calls["count"] += 1
        return httpx.Response(
            200,
            json=[{"id": 13, "name": "Portal Service",
                   "access": {"id": 3, "type": "Basic Access"}}],
        )  # fmt: skip

    with client_for(handler) as qb:
        roles = qb.app(APP).roles()
        qb.app(APP).roles()
        assert calls["count"] == 1
        qb.app(APP).roles(refresh=True)
    assert calls["count"] == 2
    assert (roles[0].id, roles[0].name, roles[0].access) == (13, "Portal Service", "Basic Access")


def test_from_env_can_read_another_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QB_REALM", "acme")
    monkeypatch.delenv("QB_USER_TOKEN", raising=False)
    monkeypatch.setenv("QB_SERVICE_TOKEN", "secret")
    with Quickbase.from_env(token_env="QB_SERVICE_TOKEN"):
        pass
    with pytest.raises(ConfigurationError, match="QB_USER_TOKEN"):
        Quickbase.from_env()
    with pytest.raises(ConfigurationError, match="QB_OTHER"):
        Quickbase.from_env(token_env="QB_OTHER")
