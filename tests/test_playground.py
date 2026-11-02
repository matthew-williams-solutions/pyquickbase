"""The docs playground: its examples must run, and its simulator must act like Quickbase."""

import asyncio
import contextlib
import importlib
import io
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

import pyquickbase

APP = Path(__file__).resolve().parent.parent / "docs/playground-app"
sys.path.insert(0, str(APP / "python"))
runner = importlib.import_module("runner")
simulator = importlib.import_module("simulator")
EXAMPLES = sorted((APP / "examples").glob("*.py"))
TASKS = simulator.TASKS_ID


def run(code: str) -> tuple[str, str | None]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        error = asyncio.run(runner.run(code))
    return stdout.getvalue(), error


def run_example(name: str) -> str:
    output, error = run((APP / "examples" / name).read_text())
    assert error is None
    return output


@pytest.fixture
def api() -> httpx.Client:
    transport = httpx.MockTransport(simulator.SimulatedQuickbase())
    return httpx.Client(transport=transport, base_url="https://api.quickbase.com/v1")


def matches(api: httpx.Client, where: str) -> int:
    response = api.post("/records/query", json={"from": TASKS, "where": where})
    assert response.status_code == 200, response.text
    return response.json()["metadata"]["totalRecords"]


@pytest.mark.parametrize("path", EXAMPLES, ids=[path.stem for path in EXAMPLES])
def test_examples_run_and_print(path: Path) -> None:
    code = path.read_text()
    output, error = run(code)
    assert error is None
    assert output
    assert code.startswith("# ")


def test_there_are_examples() -> None:
    assert len(EXAMPLES) >= 5


def test_query_example_finds_open_tasks() -> None:
    assert run_example("01_find_open_tasks.py").splitlines()[0] == (
        "1 Review vendor contract 2026-04-10"
    )


def test_import_example_reports_the_lost_row() -> None:
    assert run_example("08_an_import_that_loses_a_row.py").splitlines() == [
        "Saved: (13, 14)",
        "Failed: Book venue - The field with ID '7' has an invalid choice.",
    ]


def test_home_page_shows_the_import_example_and_its_real_output() -> None:
    text = (APP.parent / "index.md").read_text()
    code = (APP / "examples/08_an_import_that_loses_a_row.py").read_text()
    assert code[code.index("rows = [") : code.index("]\n") + 1] in text
    assert code[code.index("    try:") :].replace("\n    ", "\n").removeprefix("    ") in text
    assert run_example("08_an_import_that_loses_a_row.py") in text
    home = text
    assert "playground-app/index.html#an-import-that-loses-a-row" in home


def test_a_failed_row_returns_a_success_status_and_saves_the_rest(api: httpx.Client) -> None:
    rows = [{"6": {"value": "Good"}, "7": {"value": "Open"}}, {"7": {"value": "Finished"}}]
    response = api.post("/records", json={"to": TASKS, "data": rows})
    response.raise_for_status()  # This is the point: the usual check passes.
    assert response.status_code == 207
    assert response.json()["metadata"]["createdRecordIds"] == [13]
    assert response.json()["metadata"]["lineErrors"] == {
        "2": ["The field with ID '7' has an invalid choice."]
    }
    assert matches(api, "{6.EX.'Good'}") == 1


def test_a_fully_valid_write_returns_200(api: httpx.Client) -> None:
    rows = [{"6": {"value": "Good"}, "8": {"value": 2}}]
    response = api.post("/records", json={"to": TASKS, "data": rows})
    assert response.status_code == 200
    assert "lineErrors" not in response.json()["metadata"]


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"999": {"value": "x"}}, 'Field with ID "999" not found.'),
        ({"8": {"value": "five"}}, 'Incompatible value for field with ID "8".'),
    ],
)
def test_bad_values_are_row_errors(api: httpx.Client, row: dict[str, Any], message: str) -> None:
    response = api.post("/records", json={"to": TASKS, "data": [row]})
    assert response.json()["metadata"]["lineErrors"] == {"1": [message]}


def test_empty_data_returns_400(api: httpx.Client) -> None:
    assert api.post("/records", json={"to": TASKS, "data": []}).status_code == 400


def test_writes_dont_change_the_next_runs_data() -> None:
    first, second = simulator.SimulatedQuickbase(), simulator.SimulatedQuickbase()
    body = {"to": TASKS, "data": [{"6": {"value": "Only in the first"}}]}
    first(httpx.Request("POST", "https://x/v1/records", json=body))
    query = httpx.Request("POST", "https://x/v1/records/query", json={"from": TASKS})
    assert json.loads(second(query).content)["metadata"]["totalRecords"] == 12


def test_clients_are_restored_after_a_run() -> None:
    real = pyquickbase.Quickbase
    run("from pyquickbase import Quickbase")
    assert pyquickbase.Quickbase is real


def test_a_run_starts_from_fresh_data() -> None:
    code = (
        "from pyquickbase import Quickbase\n"
        "with Quickbase(realm='demo', token='demo') as qb:\n"
        "    table = qb.app('bq1234567').table('Tasks')\n"
        "    list(table.report('Open tasks').run())\n"
        "    print(qb.request('GET', '/reports', params={'tableId': 'bqtasks'})[1]['usedCount'])\n"
    )
    assert run(code)[0] == "1\n"
    assert run(code)[0] == "1\n"


def test_errors_point_at_the_visitors_line_without_sdk_internals() -> None:
    code = (
        "from pyquickbase import Quickbase\n"
        "with Quickbase(realm='demo', token='demo') as qb:\n"
        "    table = qb.app('bq1234567').table('Tasks')\n"
        "    list(table.query(select=['Titel']))\n"
    )
    _, error = run(code)
    assert error is not None
    assert error.startswith("Line 4: list(table.query(select=['Titel']))\n")
    assert "Did you mean 'Title'?" in error
    assert "_resources" not in error


def test_syntax_errors_are_reported() -> None:
    _, error = run("x = = 1")
    assert error is not None
    assert "SyntaxError" in error


def test_exit_is_reported_rather_than_stopping_python() -> None:
    assert run("raise SystemExit(3)")[1] == "Line 1: raise SystemExit(3)\nSystemExit: 3"


def test_input_explains_itself() -> None:
    _, error = run("input('Name? ')")
    assert error is not None
    assert "can't ask for input" in error


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ("{7.EX.'Open'}", 7),
        ("{7.EX.'open'}", 7),  # EX ignores case
        ("{7.XEX.'Open'}", 5),
        ("{7.EX.Open}", 7),  # unquoted values work
        ("{6.EX.'review'}", 0),
        ("{6.ex.'review'}", 2),  # a lowercase operator acts like CT
        ("{6.NOPE.'review'}", 2),  # and so does an unknown one
        ("{999999.EX.'x'}", 0),  # an unknown field matches nothing
        ("{'Title'.EX.'Migrate reports'}", 0),  # and so does a label
        ("{10.EX.''}", 8),  # EX '' finds empty values
        ("{10.XEX.''}", 4),
        ("{8.EX.''}", 1),
        ("{8.GT.'7'}", 4),
        ("{9.BF.'2026-04-02'}", 2),
        ("{9.LT.'2026-04-02'}", 2),  # same as BF on a date
        ("{9.OAF.'2026-07-01'}", 1),
        ("{1.AF.'1775000000000'}", 0),  # a timestamp compared with milliseconds
        ("{11.EX.'true'}", 9),
        ("{11.EX.'1'}", 9),
        ("{11.TV.'true'}", 9),
        ("{6.SW.'review'}", 2),
        ("{6.XCT.'review'}", 10),
        ("{7.EX.'Open'}AND{8.GT.'7'}", 3),
        ("{7.EX.'Done'}OR{7.EX.'Blocked'}", 5),
        ("({7.EX.'Done'}OR{7.EX.'Blocked'})AND{8.LT.'3'}", 2),
        (" {7.EX.'Done'} OR {7.EX.'Blocked'} ", 5),  # whitespace around a join is fine
        ("{7.EX.'Open'}AND", 7),  # a trailing join raises no error
        ("{6.EX.'zz'}OR{3.GT.'0'}", 12),  # the breakout from the notes
    ],
)
def test_where_matches_recorded_behavior(api: httpx.Client, where: str, expected: int) -> None:
    assert matches(api, where) == expected


@pytest.mark.parametrize(
    "where",
    ["{7.EX.'Open'}and{8.GT.'7'}", "{7.EX.'Open'", "free text", "(({7.EX.'Open'})"],
)
def test_broken_where_returns_400(api: httpx.Client, where: str) -> None:
    response = api.post("/records/query", json={"from": TASKS, "where": where})
    assert response.status_code == 400
    assert response.json()["message"] == "Criteria is invalid."


def test_query_uses_default_fields_and_leaves_out_record_id(api: httpx.Client) -> None:
    body = api.post("/records/query", json={"from": TASKS}).json()
    assert [field["id"] for field in body["fields"]] == [6, 7, 8, 9]
    assert "3" not in body["data"][0]
    assert "top" not in body["metadata"]


def test_query_pages_and_sorts(api: httpx.Client) -> None:
    body = api.post(
        "/records/query",
        json={
            "from": TASKS,
            "select": [3],
            "sortBy": [{"fieldId": 8, "order": "DESC"}],
            "options": {"top": 2, "skip": 1},
        },
    ).json()
    assert [row["3"]["value"] for row in body["data"]] == [4, 6]
    assert body["metadata"] == {
        "numFields": 1,
        "numRecords": 2,
        "skip": 1,
        "top": 2,
        "totalRecords": 12,
    }


def test_top_zero_returns_everything(api: httpx.Client) -> None:
    body = api.post("/records/query", json={"from": TASKS, "options": {"top": 0}}).json()
    assert body["metadata"]["numRecords"] == 12


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "status"),
    [
        ("GET", "/apps/bqmissing", {}, 401),
        ("GET", "/tables", {"params": {"appId": "bqmissing"}}, 401),
        ("GET", "/fields", {"params": {"tableId": "bqmissing"}}, 401),
        ("GET", "/fields", {}, 400),
        ("POST", "/records/query", {"json": {"from": "bqmissing"}}, 401),
        ("POST", "/records/query", {"json": {}}, 400),
        ("POST", "/reports/2/run", {}, 400),
        ("POST", "/reports/2/run", {"params": {"tableId": "bqmissing"}}, 401),
        ("POST", "/reports/99/run", {"params": {"tableId": TASKS}}, 404),
        ("POST", "/reports/abc/run", {"params": {"tableId": TASKS}}, 404),
        ("POST", "/reports/2/run", {"params": {"tableId": TASKS, "top": 0}}, 400),
        ("POST", "/reports/2/run", {"params": {"tableId": TASKS, "skip": -1}}, 400),
        ("POST", "/reports/4/run", {"params": {"tableId": TASKS}}, 400),
    ],
)
def test_errors_match_recorded_status_codes(
    api: httpx.Client, method: str, path: str, kwargs: dict[str, Any], status: int
) -> None:
    assert api.request(method, path, **kwargs).status_code == status


def test_report_run_applies_its_filter_columns_and_sort(api: httpx.Client) -> None:
    body = api.post("/reports/2/run", params={"tableId": TASKS, "top": 2}).json()
    assert [field["id"] for field in body["fields"]] == [3, 6, 9]
    assert [row["9"]["value"] for row in body["data"]] == ["2026-04-10", "2026-04-22"]
    assert body["metadata"]["totalRecords"] == 7


def test_report_id_with_leading_zeros_runs_the_same_report(api: httpx.Client) -> None:
    body = api.post("/reports/02/run", params={"tableId": TASKS}).json()
    assert body["metadata"]["totalRecords"] == 7


def test_top_in_a_report_body_is_ignored(api: httpx.Client) -> None:
    body = api.post("/reports/2/run", params={"tableId": TASKS}, json={"top": 1}).json()
    assert body["metadata"]["numRecords"] == 7


def test_summary_columns_are_numbered_by_position(api: httpx.Client) -> None:
    body = api.post("/reports/3/run", params={"tableId": TASKS}).json()
    assert [field["id"] for field in body["fields"]] == [2, 3]


def test_a_failed_report_run_still_counts(api: httpx.Client) -> None:
    api.post("/reports/4/run", params={"tableId": TASKS})
    reports = api.get("/reports", params={"tableId": TASKS}).json()
    assert reports[3]["usedCount"] == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/records"),
        ("POST", "/formula/run"),
        ("GET", "/files/a/1/2/0"),
        ("POST", "/fields"),
    ],
)
def test_what_isnt_simulated_says_so(api: httpx.Client, method: str, path: str) -> None:
    response = api.request(method, path)
    assert response.status_code == 403
    assert f"doesn't simulate {method} {path}" in response.json()["message"]


def test_merging_rows_isnt_simulated_yet(api: httpx.Client) -> None:
    body = {"to": TASKS, "data": [{"3": {"value": 1}}], "mergeFieldId": 3}
    assert api.post("/records", json=body).status_code == 403


def test_sample_tables_line_up_with_their_columns() -> None:
    for table in simulator.sample_tables():
        assert all(len(row) == len(table["columns"]) for row in table["rows"])
