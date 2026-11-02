# Generated from the async code by scripts/generate_sync.py. Do not edit.
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from pyquickbase import (
    BadRequestError,
    InvalidResponseError,
    InvalidValueError,
    Quickbase,
    ReadOnlyError,
)

APP = "bq1234567"
TASKS = "bqtasks"
FIELDS = [
    {"id": 6, "label": "Title", "fieldType": "text"},
    {
        "id": 12,
        "label": "Total",
        "fieldType": "numeric",
        "mode": "formula",
        "properties": {"formula": "[Amount] * 2"},
    },
]


class FakeFormulas:
    """Serves a table with a formula field, and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.run = httpx.Response(
            200, json={"fieldTypeApplied": False, "result": "1", "resultType": "bool"}
        )

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == f"/v1{path}"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": TASKS, "name": "Tasks"}])
        if path == "/formula/run":
            return self.run
        if request.method == "POST" and path == "/fields":
            return httpx.Response(200, json=FIELDS[1])
        if path.startswith("/fields/"):
            return httpx.Response(200, json=FIELDS[1])
        return httpx.Response(200, json=FIELDS)


def client(server: Any, **kwargs: Any) -> Quickbase:
    http_client = httpx.Client(transport=httpx.MockTransport(server))
    return Quickbase(realm="acme", token="fake-user-token", http_client=http_client, **kwargs)


def test_run_formula_on_a_record() -> None:
    server = FakeFormulas()
    with client(server) as qb:
        result = qb.app(APP).table("Tasks").run_formula("[Amount] > 1", record_id=7)
    assert (result.text, result.type, result.value) == ("1", "bool", True)
    assert server.bodies("/formula/run") == [{"formula": "[Amount] > 1", "from": TASKS, "rid": 7}]


def test_run_formula_without_a_record_leaves_out_rid() -> None:
    server = FakeFormulas()
    server.run = httpx.Response(200, json={"result": "2", "resultType": "number"})
    with client(server) as qb:
        result = qb.app(APP).table("Tasks").run_formula("1 + 1")
    assert result.value == Decimal(2)
    assert server.bodies("/formula/run") == [{"formula": "1 + 1", "from": TASKS}]


@pytest.mark.parametrize("formula", ["", "   "])
def test_blank_formula_is_refused_before_sending(formula: str) -> None:
    server = FakeFormulas()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="formula"):
            qb.app(APP).table("Tasks").run_formula(formula)
    assert server.requests == []


@pytest.mark.parametrize("record_id", [0, -1, True, "7"])
def test_bad_record_id_is_refused_before_sending(record_id: Any) -> None:
    server = FakeFormulas()
    with client(server) as qb:
        with pytest.raises(InvalidValueError, match="record_id"):
            qb.app(APP).table("Tasks").run_formula("1 + 1", record_id=record_id)
    assert server.requests == []


def test_formula_errors_are_reported() -> None:
    server = FakeFormulas()
    server.run = httpx.Response(
        400, json={"message": "Invalid input", "description": "Formula syntax error"}
    )
    with client(server) as qb:
        with pytest.raises(BadRequestError, match="Formula syntax error"):
            qb.app(APP).table("Tasks").run_formula("1+")


def test_malformed_formula_response_is_reported() -> None:
    server = FakeFormulas()
    server.run = httpx.Response(200, json={"result": "2"})
    with client(server) as qb:
        with pytest.raises(InvalidResponseError, match="formula"):
            qb.app(APP).table("Tasks").run_formula("1 + 1")


def test_read_only_client_can_run_formulas() -> None:
    server = FakeFormulas()
    with client(server, read_only=True) as qb:
        assert (qb.app(APP).table("Tasks").run_formula("1 + 1")).value is True
        with pytest.raises(ReadOnlyError):
            qb.app(APP).table("Tasks").create_field("X", "numeric", formula="1 + 1")


def test_allowed_writes_client_can_run_formulas() -> None:
    server = FakeFormulas()
    with client(server, allowed_writes={("POST", "/records")}) as qb:
        assert (qb.app(APP).table("Tasks").run_formula("1 + 1")).value is True


def test_create_field_with_a_formula() -> None:
    server = FakeFormulas()
    with client(server) as qb:
        field = (
            qb.app(APP)
            .table("Tasks")
            .create_field(
                "Total", "numeric", formula="[Amount] * 2", properties={"decimalPlaces": 2}
            )
        )
    assert field.formula == "[Amount] * 2"
    assert server.bodies("/fields")[0]["properties"] == {
        "decimalPlaces": 2,
        "formula": "[Amount] * 2",
    }


def test_update_field_formula_keeps_it_inside_properties() -> None:
    server = FakeFormulas()
    with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        assert [f.formula for f in table.fields()] == [None, "[Amount] * 2"]
        table.update_field("Total", formula="[Amount] * 3")
    assert server.bodies("/fields/12") == [{"properties": {"formula": "[Amount] * 3"}}]


@pytest.mark.parametrize(
    "kwargs", [{"formula": ""}, {"formula": "  "}, {"properties": {"formula": ""}}]
)
def test_a_blank_formula_never_reaches_a_field(kwargs: dict[str, Any]) -> None:
    server = FakeFormulas()
    with client(server) as qb:
        table = qb.app(APP).table("Tasks")
        with pytest.raises(InvalidValueError, match="formula"):
            table.update_field("Total", **kwargs)
        with pytest.raises(InvalidValueError, match="formula"):
            table.create_field("New", "numeric", **kwargs)
    assert [r for r in server.requests if r.method == "POST"] == []
