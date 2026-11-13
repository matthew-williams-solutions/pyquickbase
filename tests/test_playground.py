"""The docs playground: its examples must run, and its simulator must act like Quickbase."""

import asyncio
import contextlib
import importlib
import io
import json
import re
import shutil
import subprocess
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
PROJECTS = simulator.PROJECTS_ID
TIME = simulator.TIME_ID


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
        "Saved: (25, 26)",
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


def test_the_playground_page_embeds_the_app_from_where_it_links_to_it() -> None:
    # Zensical rewrites relative URLs in raw HTML as it does Markdown links, relative to the
    # source file, so the iframe needs the same form as the link or it points above the site.
    text = (APP.parent / "playground.md").read_text()
    assert 'src="playground-app/"' in text
    assert "](playground-app/index.html)" in text


def test_a_failed_row_returns_a_success_status_and_saves_the_rest(api: httpx.Client) -> None:
    rows = [{"6": {"value": "Good"}, "7": {"value": "Open"}}, {"7": {"value": "Finished"}}]
    response = api.post("/records", json={"to": TASKS, "data": rows})
    response.raise_for_status()  # This is the point: the usual check passes.
    assert response.status_code == 207
    assert response.json()["metadata"]["createdRecordIds"] == [25]
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
    assert json.loads(second(query).content)["metadata"]["totalRecords"] == 24


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
        ("{7.EX.'Open'}", 16),
        ("{7.EX.'open'}", 16),  # EX ignores case
        ("{7.XEX.'Open'}", 8),
        ("{7.EX.Open}", 16),  # unquoted values work
        ("{6.EX.'review'}", 0),
        ("{6.ex.'review'}", 3),  # a lowercase operator acts like CT
        ("{6.NOPE.'review'}", 3),  # and so does an unknown one
        ("{999999.EX.'x'}", 0),  # an unknown field matches nothing
        ("{'Title'.EX.'Migrate reports'}", 0),  # and so does a label
        ("{10.EX.''}", 18),  # EX '' finds empty values
        ("{10.XEX.''}", 6),
        ("{8.EX.''}", 2),
        ("{8.GT.'7'}", 7),
        ("{9.BF.'2026-04-02'}", 3),
        ("{9.LT.'2026-04-02'}", 3),  # same as BF on a date
        ("{9.OAF.'2026-07-01'}", 9),
        ("{1.AF.'1775000000000'}", 0),  # a timestamp compared with milliseconds
        ("{11.EX.'true'}", 19),
        ("{11.EX.'1'}", 19),
        ("{11.TV.'true'}", 19),
        ("{6.SW.'review'}", 3),
        ("{6.XCT.'review'}", 21),
        ("{7.EX.'Open'}AND{8.GT.'7'}", 5),
        ("{7.EX.'Done'}OR{7.EX.'Blocked'}", 8),
        ("({7.EX.'Done'}OR{7.EX.'Blocked'})AND{8.LT.'3'}", 3),
        (" {7.EX.'Done'} OR {7.EX.'Blocked'} ", 8),  # whitespace around a join is fine
        ("{7.EX.'Open'}AND", 16),  # a trailing join raises no error
        ("{6.EX.'zz'}OR{3.GT.'0'}", 24),  # the breakout from the notes
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
    assert [row["3"]["value"] for row in body["data"]] == [8, 4]
    assert body["metadata"] == {
        "numFields": 1,
        "numRecords": 2,
        "skip": 1,
        "top": 2,
        "totalRecords": 24,
    }


def test_top_zero_returns_everything(api: httpx.Client) -> None:
    body = api.post("/records/query", json={"from": TASKS, "options": {"top": 0}}).json()
    assert body["metadata"]["numRecords"] == 24


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
    assert body["metadata"]["totalRecords"] == 16


def test_report_id_with_leading_zeros_runs_the_same_report(api: httpx.Client) -> None:
    body = api.post("/reports/02/run", params={"tableId": TASKS}).json()
    assert body["metadata"]["totalRecords"] == 16


def test_top_in_a_report_body_is_ignored(api: httpx.Client) -> None:
    body = api.post("/reports/2/run", params={"tableId": TASKS}, json={"top": 1}).json()
    assert body["metadata"]["numRecords"] == 16


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
        ("POST", "/formula/run"),
        ("GET", "/files/a/1/2/0"),
        ("POST", "/fields"),
    ],
)
def test_what_isnt_simulated_says_so(api: httpx.Client, method: str, path: str) -> None:
    response = api.request(method, path)
    assert response.status_code == 403
    assert f"doesn't simulate {method} {path}" in response.json()["message"]


def write(api: httpx.Client, *rows: dict[str, Any], **extra: Any) -> httpx.Response:
    data = [{key: {"value": value} for key, value in row.items()} for row in rows]
    return api.post("/records", json={"to": TASKS, "data": data, **extra})


def project_hours(api: httpx.Client) -> Any:
    body = {"from": simulator.PROJECTS_ID, "select": [13], "where": "{3.EX.'1'}"}
    return api.post("/records/query", json=body).json()["data"][0]["13"]["value"]


def test_updates_change_records_as_quickbase_reports_them(api: httpx.Client) -> None:
    before = project_hours(api)
    hours = api.post(
        "/records/query", json={"from": TASKS, "select": [8], "where": "{3.EX.'1'}"}
    ).json()["data"][0]["8"]["value"]
    changed = write(api, {"3": 1, "8": hours + 5, "10": None}, mergeFieldId=3)
    assert changed.status_code == 200
    assert changed.json()["metadata"]["updatedRecordIds"] == [1]
    assert project_hours(api) == before + 5  # Rollups follow the change.
    again = write(api, {"3": 1, "8": hours + 5})  # A Record ID# changes it without mergeFieldId.
    assert again.json()["metadata"]["unchangedRecordIds"] == [1]
    missing = write(api, {"3": 99999, "7": "Done"}, mergeFieldId=3)
    assert missing.status_code == 207
    assert missing.json()["metadata"]["lineErrors"] == {"1": ["Can not find record by ID"]}


def test_a_merge_needs_every_row_to_hold_the_merge_field(api: httpx.Client) -> None:
    assert write(api, {"7": "Done"}, mergeFieldId=3).status_code == 400


def test_upserts_arent_simulated_yet(api: httpx.Client) -> None:
    response = write(api, {"6": "New"}, mergeFieldId=6)
    assert response.status_code == 403
    assert "upserts" in response.json()["message"]


def delete(api: httpx.Client, **body: Any) -> httpx.Response:
    return api.request("DELETE", "/records", json={"from": TASKS, **body})


def test_deletes_follow_the_recorded_quirks(api: httpx.Client) -> None:
    total = matches(api, "{3.GT.'0'}")
    done = matches(api, "{7.EX.'Done'}")
    assert delete(api).status_code == 400
    assert delete(api, where="   ").json() == {"numberDeleted": 0}
    assert delete(api, where="{7.EX.'Done'").json() == {"numberDeleted": 0}
    assert delete(api, where="{7.ex.'Done'}").json() == {"numberDeleted": done}  # Acts like CT
    assert matches(api, "{3.GT.'0'}") == total - done


def test_sample_tables_line_up_with_their_columns() -> None:
    for table in simulator.sample_tables():
        assert all(len(row) == len(table["columns"]) for row in table["rows"])


def test_sample_tables_show_text_a_person_can_read() -> None:
    tasks, projects, time = simulator.sample_tables()
    first = dict(zip(tasks["columns"], tasks["rows"][0], strict=True))
    assert first["Assignee"] == "Ana Reyes"
    assert first["Tags"] == "legal, finance"
    assert first["Attachment"] == "contract-signed.pdf"
    assert first["Project Name"] == "Operations"
    assert tasks["columns"][:2] == ["Record ID#", "Title"]
    assert tasks["ids"][:2] == [3, 6]
    assert tasks["columns"][-1] == "Date Created"
    assert first["Date Created"] == "2026-03-01T09:00:00Z"
    assert [len(table["rows"]) for table in (tasks, projects, time)] == [24, 5, 28]
    assert all(table["description"] for table in (tasks, projects, time))
    for table in (tasks, projects, time):
        for row in table["rows"]:
            assert not any(isinstance(cell, dict | list) for cell in row)


def values(api: httpx.Client, table: str, record_id: int, *fields: int) -> list[Any]:
    body = {"from": table, "select": list(fields), "where": f"{{3.EX.'{record_id}'}}"}
    row = api.post("/records/query", json=body).json()["data"][0]
    return [row[str(field)]["value"] for field in fields]


def test_values_have_the_recorded_shapes(api: httpx.Client) -> None:
    user, tags, file = values(api, TASKS, 1, 14, 15, 16)
    assert set(user) == {"email", "id", "name"}
    assert tags == ["legal", "finance"]
    assert file["url"].startswith("/files/")
    assert [version["versionNumber"] for version in file["versions"]] == [1, 2]
    assert {"versionNumber", "fileName", "uploaded", "creator"} == set(file["versions"][0])


def test_empty_values_have_the_recorded_shapes(api: httpx.Client) -> None:
    assert values(api, TASKS, 9, 14, 15, 16, 8, 9, 10) == [
        None,
        [],
        {"url": "", "versions": []},
        None,
        "",
        "No date set yet",
    ]


def test_sample_people_use_a_reserved_domain() -> None:
    for person in (simulator.ANA, simulator.BEN, simulator.CHLOE, simulator.DEV, simulator.ELENA):
        assert person["email"].endswith("@example.com")


def test_computed_fields_are_marked_and_hold_their_values(api: httpx.Client) -> None:
    fields = {item["id"]: item for item in api.get("/fields", params={"tableId": TASKS}).json()}
    assert (fields[17]["mode"], fields[18]["mode"], fields[19]["mode"]) == (
        "lookup",
        "summary",
        "formula",
    )
    assert fields[19]["properties"]["formula"] == "[Hours Logged] > [Hours]"
    assert values(api, TASKS, 7, 17, 18, 19) == ["Platform", 4.5, True]
    assert values(api, TASKS, 9, 17, 18, 19) == ["People", 0.0, False]  # no estimate, so never over
    assert values(api, PROJECTS, 5, 12, 13) == [0, 0.0]  # a project with no tasks
    assert values(api, TIME, 1, 7) == ["Review vendor contract"]


def test_adding_records_updates_what_is_computed_from_them(api: httpx.Client) -> None:
    assert values(api, TASKS, 8, 18, 19) == [10.5, False]
    row = {"6": {"value": 8}, "10": {"value": 7.0}}
    assert api.post("/records", json={"to": TIME, "data": [row]}).status_code == 200
    assert values(api, TASKS, 8, 18, 19) == [17.5, True]

    before = values(api, PROJECTS, 4, 12, 13)
    row = {"6": {"value": "New"}, "8": {"value": 2.0}, "12": {"value": 4}}
    assert api.post("/records", json={"to": TASKS, "data": [row]}).status_code == 200
    assert values(api, PROJECTS, 4, 12, 13) == [before[0] + 1, before[1] + 2.0]
    assert values(api, TASKS, 25, 17, 14, 15, 16, 10, 7) == [
        "Customer Portal",
        None,
        [],
        {"url": "", "versions": []},
        "",
        "",
    ]


@pytest.mark.parametrize("field", [17, 18, 19])
def test_computed_fields_cant_be_written(api: httpx.Client, field: int) -> None:
    rows = [{"6": {"value": "Kept"}}, {str(field): {"value": "x"}}]
    response = api.post("/records", json={"to": TASKS, "data": rows})
    assert response.status_code == 207
    assert list(response.json()["metadata"]["lineErrors"]) == ["2"]
    assert response.json()["metadata"]["createdRecordIds"] == [25]


@pytest.mark.parametrize("field", [14, 16])
def test_writing_users_and_files_isnt_simulated(api: httpx.Client, field: int) -> None:
    rows = [{str(field): {"value": "x"}}]
    assert api.post("/records", json={"to": TASKS, "data": rows}).status_code == 403
    assert matches(api, "{3.GT.'0'}") == 24


def test_queries_on_files_say_they_arent_simulated(api: httpx.Client) -> None:
    response = api.post("/records/query", json={"from": TASKS, "where": "{16.EX.'a'}"})
    assert response.status_code == 400
    assert "doesn't simulate" in response.json()["message"]


def test_user_fields_match_as_recorded(api: httpx.Client) -> None:
    rows = api.post("/records/query", json={"from": TASKS, "select": [14]}).json()["data"]
    total = len(rows)
    ana = sum(1 for row in rows if (row["14"]["value"] or {}).get("id") == "60001.ana")
    empty = sum(1 for row in rows if row["14"]["value"] is None)
    assert 0 < ana < total
    for wanted in ("ana@example.com", "ANA@EXAMPLE.COM", "60001.ana", " _CURUSER_ "):
        assert matches(api, f"{{14.TV.'{wanted}'}}") == ana, wanted
    assert matches(api, "{14.XTV.'ana@example.com'}") == total - ana
    assert matches(api, "{14.EX.'ana@example.com'}") == 0  # EX never matches a user
    assert matches(api, "{14.XEX.'ana@example.com'}") == total  # So XEX matches everyone
    assert matches(api, "{14.EX.''}") == empty
    assert matches(api, "{14.TV.''}") == empty
    assert matches(api, "{14.CT.'Ana'}") == 0


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ("{15.HAS.'security'}", 5),
        ("{15.HAS.'SECURITY'}", 5),
        ("{15.XHAS.'security'}", 19),
        ("{15.EX.''}", 2),
        ("{15.HAS.'secur'}", 0),  # a whole value, unlike CT
        ("{15.CT.'secur'}", 5),
        ("{13.EX.'High'}", 7),
        ("{6.EX.'Renew O\\'Brien & Co retainer'}", 1),
    ],
)
def test_tags_and_new_choices_match(api: httpx.Client, where: str, expected: int) -> None:
    assert matches(api, where) == expected


def test_relationships_are_listed_only_on_the_child(api: httpx.Client) -> None:
    def parents(table: str) -> list[str]:
        response = api.get(f"/tables/{table}/relationships")
        return [link["parentTableId"] for link in response.json()["relationships"]]

    assert parents(TASKS) == [PROJECTS]
    assert parents(TIME) == [TASKS]
    assert parents(PROJECTS) == []


def test_every_table_report_on_every_table_runs(api: httpx.Client) -> None:
    for table in (TASKS, PROJECTS, TIME):
        for report in api.get("/reports", params={"tableId": table}).json():
            if not report["incomplete"]:
                response = api.post(f"/reports/{report['id']}/run", params={"tableId": table})
                assert response.status_code == 200
                assert response.json()["metadata"]["totalRecords"] > 0


def test_a_summary_report_adds_up_its_own_columns(api: httpx.Client) -> None:
    body = api.post("/reports/3/run", params={"tableId": PROJECTS}).json()
    assert [field["label"] for field in body["fields"]] == ["Status", "Total Budget"]
    assert {row["2"]["value"]: row["3"]["value"] for row in body["data"]} == {
        "Active": 137000.0,
        "On Hold": 35000.0,
    }


def test_search_example_finds_awkward_text_and_refuses_syntax() -> None:
    output = run_example("13_search_with_text_you_dont_control.py").splitlines()
    assert output[0] == '"Renew O\'Brien & Co retainer" matches 1 task'
    assert output[1] == "'Review Terms AND Conditions' matches 1 task"
    assert "refused" in output[2]
    assert "refused" in output[3]


def test_computed_fields_example_shows_the_change() -> None:
    output = run_example("12_watch_computed_fields_update.py")
    assert "Before: 10.5 hours logged, over estimate: False" in output
    assert "After: 17.5 hours logged, over estimate: True" in output


def test_policy_example_hides_a_field_refuses_a_write_and_allows_another() -> None:
    output = run_example("17_limit_what_an_agent_can_do.py").splitlines()
    assert "'Hours'" not in output[0]
    assert output[1].startswith("Hidden field: This table has no field labeled 'Hours'")
    assert output[2].startswith("Refused: Refused by policy: updating Title")
    assert output[3] == "Allowed: (1,)"


def test_schema_example_shows_each_status_and_changes_nothing() -> None:
    output = run_example("18_preview_a_schema_change.py")
    assert '[ready, additive] Add field "Reviewed"' in output
    assert "[blocked, modifying]" in output
    assert '[ready, destructive] Delete field "Hours"' in output
    assert '"Over Estimate"' in output
    assert output.endswith("Made yet: False\n")


def test_code_page_example_shows_the_diff_and_saves() -> None:
    output = run_example("20_change_a_code_page.py")
    assert "+<h1>Welcome (updated)</h1>" in output
    assert output.endswith("Saved page 2 - replaced\n")


PLAYGROUND_LINK = re.compile(r"playground-app/index\.html#([a-z0-9-]+)")


def example_id(path: Path) -> str:
    return path.stem.split("_", 1)[1].replace("_", "-")


def test_try_it_links_open_an_example_and_every_example_is_linked() -> None:
    docs = APP.parent
    linked = {
        found
        for page in docs.rglob("*.md")
        for found in PLAYGROUND_LINK.findall(page.read_text(encoding="utf-8"))
    }
    ids = {example_id(path) for path in EXAMPLES}
    assert linked <= ids, sorted(linked - ids)
    assert ids <= linked, f"No guide links to {sorted(ids - linked)}"


def test_the_xml_parser_works_in_the_vendored_pyodide(tmp_path: Path) -> None:
    pyodide = APP / "vendor" / "pyodide" / "pyodide.mjs"
    node = shutil.which("node")
    if node is None or not pyodide.exists():
        pytest.skip("Run scripts/build_playground.py, with Node installed, to check this.")
    source = Path(pyquickbase.__file__).parent / "_xml.py"
    script = tmp_path / "check.mjs"
    script.write_text(
        _PYODIDE_CHECK % {"pyodide": json.dumps(str(pyodide)), "source": json.dumps(str(source))}
    )
    done = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("xml parser ok"), done.stdout


def test_the_simulator_answers_the_xml_calls() -> None:
    async def check() -> None:
        transport = httpx.MockTransport(simulator.SimulatedQuickbase())
        http = httpx.AsyncClient(transport=transport)
        async with pyquickbase.AsyncQuickbase(realm="acme", token="t", http_client=http) as qb:
            assert [item.id for item in await qb.apps()] == [simulator.APP_ID]
            assert (await qb.current_user()).email == "sam@example.com"
            with pytest.raises(pyquickbase.NotFoundError):
                await qb.find_user("nobody@example.com")
            app = qb.app(simulator.APP_ID)
            assert [role.name for role in await app.roles()]
            assert (await app.info()).name
            assert (await app.members())[0].kind == "user"
            assert (await app.member_roles())[0].assigned_through == "user"
            assert {item.type for item in await app.code_pages()} == {"Text", "Home Page"}
            page = app.code_page("welcome.html")
            assert (await page.read()).text == "<h1>Welcome</h1>\n"
            written = await page.write("a\nb")
            assert not written.created
            assert (await page.read()).text == "a\nb"
            assert (await app.code_page("new.html").write("x")).created
            with pytest.raises(pyquickbase.BadRequestError):
                await app.code_page("Dashboard").read()
            with pytest.raises(pyquickbase.AuthenticationError):
                await qb.app("bqother").code_pages()

    asyncio.run(check())


_PYODIDE_CHECK = """
import { readFileSync } from "node:fs";
import { loadPyodide } from %(pyodide)s;
const pyodide = await loadPyodide();
pyodide.FS.writeFile("/tmp/quickbase_xml_source.py", readFileSync(%(source)s, "utf8"));
await pyodide.runPythonAsync(`
import importlib.util, sys, types
for name in ("httpx", "anyio", "pyquickbase", "pyquickbase._config", "pyquickbase._links",
             "pyquickbase._models", "pyquickbase._read_request", "pyquickbase._response",
             "pyquickbase._write_request", "pyquickbase.errors"):
    sys.modules[name] = types.ModuleType(name)
class Any_:
    def __getattr__(self, name):
        return Any_()
    def __call__(self, *a, **k):
        return Any_()
class Mod(types.ModuleType):
    __path__ = []
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (Exception,), {})
for name in list(sys.modules):
    if name == "httpx" or name.startswith("pyquickbase"):
        sys.modules[name] = Mod(name)
spec = importlib.util.spec_from_file_location("pyquickbase._xml", "/tmp/quickbase_xml_source.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["pyquickbase._xml"] = mod
spec.loader.exec_module(mod)
root = mod.parse_xml(b"<a x='1'>one<BR/>two</a>")
assert root.attributes == {"x": "1"} and root.text == "one" and root.children[0].tail == "two"
for bad in (b"<!DOCTYPE a><a/>", b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>'):
    try:
        mod.parse_xml(bad)
    except Exception as exc:
        assert type(exc).__name__ == "InvalidResponseError"
    else:
        raise AssertionError("a DTD was parsed")
print("xml parser ok")
`);
"""
