"""Check what docs/api-notes.md says. A failure means the notes, or Quickbase, have changed.

These use plain HTTP rather than the SDK, so that they test Quickbase and not our own code.
"""

import base64
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

API = "https://api.quickbase.com/v1"
MISSING_TABLE = "bqzzzzzzz"


class Api:
    def __init__(self, scratch: Any) -> None:
        self.scratch = scratch
        self.http = httpx.Client(
            base_url=API,
            timeout=60,
            headers={
                "QB-Realm-Hostname": scratch.client.realm,
                "Authorization": f"QB-USER-TOKEN {scratch.token}",
            },
        )

    def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.http.request(method, path, **kwargs)

    def post(self, path: str, body: Any) -> httpx.Response:
        return self.call("POST", path, json=body)

    def where(self, table: str, where: str) -> httpx.Response:
        return self.post("/records/query", {"from": table, "select": [3], "where": where})

    def ids(self, table: str, where: str) -> list[int]:
        response = self.where(table, where)
        assert response.status_code == 200, response.text
        return sorted(row["3"]["value"] for row in response.json()["data"])

    def write(self, table: str, rows: list[dict[str, Any]], **extra: Any) -> httpx.Response:
        data = [{str(k): {"value": v} for k, v in row.items()} for row in rows]
        return self.post("/records", {"to": table, "data": data, **extra})

    def add(self, table: str, row: dict[int, Any]) -> int:
        response = self.write(table, [row])
        assert response.status_code == 200, response.text
        return response.json()["metadata"]["createdRecordIds"][0]

    def value(self, table: str, record: int, field: int) -> Any:
        body = {"from": table, "select": [field], "where": f"{{3.EX.{record}}}"}
        return self.post("/records/query", body).json()["data"][0][str(field)]["value"]

    def clear(self, table: str) -> None:
        self.call("DELETE", "/records", json={"from": table, "where": "{3.GT.'0'}"})


@pytest.fixture(scope="module")
def api(scratch: Any) -> Iterator[Api]:
    helper = Api(scratch)
    yield helper
    helper.http.close()


TEXT, DATE, STAMP, NUM, BOX, UNIQUE, CHOICE, FILE, FORMULA = 6, 7, 8, 9, 10, 11, 12, 13, 14


@pytest.fixture(scope="module")
def table(scratch: Any) -> str:
    made = scratch.table(
        ("Text", "text"),
        ("Date", "date"),
        ("Stamp", "timestamp"),
        ("Num", "numeric"),
        ("Box", "checkbox"),
        ("Unique", "text", {"unique": True}),
        ("Choice", "text-multiple-choice", {"choices": ["a", "b"]}),
        ("File", "file"),
        ("Formula", "numeric", {"formula": "[Num] / 3"}),
    )
    ids = {item.label: item.id for item in made.fields()}
    assert [ids[n] for n in ("Text", "Date", "Stamp", "Num", "Box")] == [
        TEXT,
        DATE,
        STAMP,
        NUM,
        BOX,
    ]
    assert ids["Formula"] == FORMULA
    return made.info().id


@pytest.fixture
def rows(api: Api, table: str) -> dict[str, int]:
    api.clear(table)
    response = api.write(
        table,
        [
            {TEXT: "alpha", DATE: "2026-03-01", NUM: 1, BOX: True},
            {TEXT: "Beta", DATE: "2026-03-02", NUM: 2},
            {TEXT: "gamma alpha", DATE: "2026-03-03", NUM: 3},
            {NUM: 4},
        ],
    )
    created = response.json()["metadata"]["createdRecordIds"]
    return dict(zip(("alpha", "beta", "gamma", "empty"), created, strict=True))


# Confirmed


def test_error_body_and_request_id(api: Api) -> None:
    response = api.call("GET", "/fields", params={"tableId": MISSING_TABLE})
    assert response.status_code == 401
    assert {"message", "description"} <= set(response.json())
    assert response.headers["qb-api-ray"]


def test_query_metadata(api: Api, table: str, rows: dict[str, int]) -> None:
    body = {"from": table, "select": [3], "options": {"top": 2, "skip": 1}}
    metadata = api.post("/records/query", body).json()["metadata"]
    assert metadata == {"numFields": 1, "numRecords": 2, "skip": 1, "top": 2, "totalRecords": 4}


def test_top_and_skip_on_a_query(api: Api, table: str, rows: dict[str, int]) -> None:
    def count(options: dict[str, int] | None) -> int:
        body: dict[str, Any] = {"from": table, "select": [3]}
        if options is not None:
            body["options"] = options
        return len(api.post("/records/query", body).json()["data"])

    assert count(None) == 4
    assert count({"top": 0}) == 4
    assert count({"skip": 100}) == 0


def test_ex_ignores_case(api: Api, table: str, rows: dict[str, int]) -> None:
    assert api.ids(table, f"{{{TEXT}.EX.'ALPHA'}}") == [rows["alpha"]]


def test_joins(api: Api, table: str, rows: dict[str, int]) -> None:
    both = sorted([rows["alpha"], rows["beta"]])
    clause = f"{{{TEXT}.EX.'alpha'}}OR{{{TEXT}.EX.'beta'}}"
    assert api.ids(table, clause) == both
    assert api.ids(table, clause.replace("OR", " OR ")) == both
    assert api.where(table, clause.replace("OR", "or")).status_code == 400


def test_unquoted_values_work_for_numbers(api: Api, table: str, rows: dict[str, int]) -> None:
    assert (
        api.ids(table, f"{{{NUM}.EX.2}}") == api.ids(table, f"{{{NUM}.EX.'2'}}") == [rows["beta"]]
    )


def test_empty_comparisons(api: Api, table: str, rows: dict[str, int]) -> None:
    assert api.ids(table, f"{{{TEXT}.EX.''}}") == [rows["empty"]]
    assert rows["empty"] not in api.ids(table, f"{{{TEXT}.XEX.''}}")


def test_date_comparisons_match(api: Api, table: str, rows: dict[str, int]) -> None:
    day = "2026-03-02"
    assert api.ids(table, f"{{{DATE}.LT.'{day}'}}") == api.ids(table, f"{{{DATE}.BF.'{day}'}}")
    assert api.ids(table, f"{{{DATE}.LTE.'{day}'}}") == api.ids(table, f"{{{DATE}.OBF.'{day}'}}")
    assert api.ids(table, f"{{{DATE}.GT.'{day}'}}") == api.ids(table, f"{{{DATE}.AF.'{day}'}}")
    assert api.ids(table, f"{{{DATE}.GTE.'{day}'}}") == api.ids(table, f"{{{DATE}.OAF.'{day}'}}")


def test_timestamps(api: Api, table: str) -> None:
    api.clear(table)
    record = api.add(table, {STAMP: "2026-03-04T05:06:07Z"})
    assert api.value(table, record, STAMP) == "2026-03-04T05:06:07Z"
    moment = 1772600767000  # 2026-03-04T05:06:07Z
    assert api.ids(table, f"{{{STAMP}.AF.{moment - 1000}}}") == [record]
    assert api.ids(table, f"{{{STAMP}.BF.{moment - 1000}}}") == []


def test_a_missing_table_is_401_and_a_malformed_one_is_400(api: Api) -> None:
    assert api.post("/records/query", {"from": MISSING_TABLE, "select": [3]}).status_code == 401
    for malformed in ("abc", "bqmuchtoolongtobeatable"):
        response = api.post("/records/query", {"from": malformed, "select": [3]})
        assert response.status_code == 400
        assert response.json()["message"] == "Invalid request"


def test_a_parent_table_lists_no_relationships(api: Api, scratch: Any) -> None:
    parent, child = scratch.table(("A", "text")), scratch.table(("B", "text"))
    child.create_relationship(parent, foreign_key_label="P")
    for made, expected in ((parent, 0), (child, 1)):
        path = f"/tables/{made.info().id}/relationships"
        assert api.call("GET", path).json()["metadata"]["totalRelationships"] == expected


def test_table_names_must_be_unique(api: Api, scratch: Any) -> None:
    made = scratch.table(("A", "text"))
    name = made.info().name
    params = {"appId": scratch.app_id}
    for attempt in (name, name.upper()):
        response = api.call("POST", "/tables", params=params, json={"name": attempt})
        assert response.status_code == 400
        assert response.json()["message"] == "Invalid input"


# Query pitfalls


@pytest.mark.parametrize("operator", ["ex", "NOPE"])
def test_a_bad_operator_acts_like_contains(
    api: Api, table: str, rows: dict[str, int], operator: str
) -> None:
    found = api.ids(table, f"{{{TEXT}.{operator}.'alpha'}}")
    assert found == sorted([rows["alpha"], rows["gamma"]])


def test_an_unknown_field_id_matches_nothing(api: Api, table: str, rows: dict[str, int]) -> None:
    assert api.ids(table, "{999999.EX.'alpha'}") == []


def test_unquoted_text_works_like_quoted_text(api: Api, table: str, rows: dict[str, int]) -> None:
    assert api.ids(table, f"{{{TEXT}.EX.alpha}}") == [rows["alpha"]]
    assert api.ids(table, f"{{{TEXT}.EX.nomatch}}") == []


@pytest.mark.parametrize("name", ["'Text'", "Text", "'text'", "'No Such Field'"])
def test_a_name_as_the_field_still_matches(
    api: Api, table: str, rows: dict[str, int], name: str
) -> None:
    assert rows["alpha"] in api.ids(table, f"{{{name}.EX.'alpha'}}")


def test_a_trailing_join_is_accepted(api: Api, table: str, rows: dict[str, int]) -> None:
    assert api.where(table, f"{{{TEXT}.EX.'alpha'}}AND").status_code == 200


@pytest.mark.parametrize("clause", ["{6.EX.'a'}or{6.EX.'b'}", "{6.EX.'a'", "free text"])
def test_rejected_where_clauses(api: Api, table: str, clause: str) -> None:
    response = api.where(table, clause)
    assert response.status_code == 400
    assert "Criteria is invalid" in response.text


def test_quoting(api: Api, table: str) -> None:
    api.clear(table)
    quote = api.add(table, {TEXT: "O'Brien"})
    slash = api.add(table, {TEXT: "back\\slash"})
    assert api.ids(table, f"{{{TEXT}.EX.'O\\'Brien'}}") == [quote]
    assert api.ids(table, f"{{{TEXT}.EX.'O'Brien'}}") == [quote]
    assert api.ids(table, f"{{{TEXT}.EX.'O''Brien'}}") == []
    assert api.ids(table, f"{{{TEXT}.EX.'back\\\\slash'}}") == [slash]
    assert api.ids(table, f"{{{TEXT}.EX.'back\\slash'}}") == []
    assert api.where(table, f"{{{TEXT}.EX.'a{{b'}}").status_code == 400


def test_a_value_can_break_out_of_its_clause(api: Api, table: str, rows: dict[str, int]) -> None:
    assert len(api.ids(table, f"{{{TEXT}.EX.'zz'}}OR{{3.GT.'0'}}")) == 4


@pytest.mark.parametrize("operator", ["EX", "TV"])
def test_or_splits_a_quoted_value(api: Api, table: str, operator: str) -> None:
    api.clear(table)
    a, b = api.add(table, {TEXT: "a"}), api.add(table, {TEXT: "b"})
    literal = api.add(table, {TEXT: "a OR b"})
    assert api.ids(table, f"{{{TEXT}.{operator}.'a OR b'}}") == sorted([a, b])
    assert api.ids(table, f"{{{TEXT}.{operator}.'a or b'}}") == [literal]


def test_and_and_lone_words_in_a_value(api: Api, table: str) -> None:
    api.clear(table)
    a = api.add(table, {TEXT: "a"})
    literal = api.add(table, {TEXT: "a AND b"})
    word = api.add(table, {TEXT: "OR"})
    assert api.ids(table, f"{{{TEXT}.EX.'a AND b'}}") == []
    assert api.ids(table, f"{{{TEXT}.EX.'a and b'}}") == [literal]
    assert api.ids(table, f"{{{TEXT}.EX.'OR'}}") == []
    assert api.ids(table, f"{{{TEXT}.EX.'or'}}") == [word]
    assert api.ids(table, f"{{{TEXT}.EX.'a OR'}}") == [a]


@pytest.mark.parametrize("value", ["x Or y", "x,OR,y", "x-OR-y", "xOR y", "x\u00a0OR\u00a0y"])
def test_what_doesnt_split_a_value(api: Api, table: str, value: str) -> None:
    api.clear(table)
    record = api.add(table, {TEXT: value})
    assert record in api.ids(table, f"{{{TEXT}.EX.'{value}'}}")


@pytest.mark.parametrize(
    "value", ["_FID_6", "_fid_6", "_FID_6x", "_FID_06", "_FID_ 6", "_FID_6 x", "\\_FID_6"]
)
def test_a_field_reference_in_a_value(api: Api, table: str, value: str) -> None:
    api.clear(table)
    api.write(table, [{TEXT: "a"}, {TEXT: "b"}])
    assert len(api.ids(table, f"{{{TEXT}.EX.'{value}'}}")) == 2
    assert len(api.ids(table, f"{{{TEXT}.XEX.'{value}'}}")) == 0


@pytest.mark.parametrize(
    "value",
    [" _FID_6", "x _FID_6", "x_FID_6", "\\\\_FID_6", "\\'_FID_6", ",_FID_6", "\t_FID_6", "_FID_"],
)
def test_what_isnt_a_field_reference(api: Api, table: str, value: str) -> None:
    api.clear(table)
    api.write(table, [{TEXT: "a"}, {TEXT: "b"}])
    assert api.ids(table, f"{{{TEXT}.EX.'{value}'}}") == []


def test_ask_values(api: Api, table: str) -> None:
    api.clear(table)
    record = api.add(table, {TEXT: "_ask1_"})
    response = api.where(table, f"{{{TEXT}.EX.'_ask1_'}}")
    assert response.status_code == 400
    assert "Incomplete reports not supported" in response.text
    assert api.ids(table, f"{{{TEXT}.EX.'_ASK1_'}}") == [record]


def test_delete_through_a_field_reference(api: Api, scratch: Any) -> None:
    made = scratch.table(("Code", "text"))
    table = made.info().id
    api.write(table, [{TEXT: "a"}, {TEXT: "b"}, {TEXT: "c"}])
    body = {"from": table, "where": f"{{{TEXT}.EX.'_FID_{TEXT}'}}"}
    assert api.call("DELETE", "/records", json=body).json()["numberDeleted"] == 3


# Writes


def test_a_failed_row_returns_207(api: Api, table: str) -> None:
    api.clear(table)
    response = api.write(table, [{TEXT: "ok"}, {3: 99999999, TEXT: "bad"}])
    assert response.status_code == 207
    metadata = response.json()["metadata"]
    assert list(metadata["lineErrors"]) == ["2"]
    assert len(metadata["createdRecordIds"]) == 1


def test_null_is_ignored_and_empty_clears(api: Api, table: str) -> None:
    api.clear(table)
    record = api.add(table, {TEXT: "a", DATE: "2026-01-01", STAMP: "2026-01-01T00:00:00Z", NUM: 1})
    row = {3: record, TEXT: None, NUM: None}
    api.write(table, [row])
    assert api.value(table, record, TEXT) == "a"
    assert api.value(table, record, NUM) == 1
    api.write(table, [{3: record, TEXT: "", DATE: "", STAMP: "", NUM: "", BOX: "", CHOICE: ""}])
    assert api.value(table, record, TEXT) == ""
    assert api.value(table, record, DATE) == ""
    assert api.value(table, record, STAMP) == ""
    assert api.value(table, record, NUM) is None
    assert api.value(table, record, BOX) is False


def test_blank_rows_are_skipped_only_in_single_field_batches(api: Api, table: str) -> None:
    def created(rows: list[dict[str, Any]]) -> int:
        api.clear(table)
        return len(api.write(table, rows).json()["metadata"]["createdRecordIds"])

    assert created([{TEXT: ""}, {TEXT: "x"}]) == 1
    assert created([{TEXT: "x"}, {TEXT: ""}, {TEXT: "y"}]) == 2
    assert created([{TEXT: ""}]) == 1
    assert created([{TEXT: ""}, {NUM: 1}]) == 2
    assert created([{TEXT: "", NUM: ""}, {TEXT: "x", NUM: 1}]) == 2


def test_writing_with_a_record_id(api: Api, table: str) -> None:
    api.clear(table)
    record = api.add(table, {TEXT: "a"})
    changed = api.write(table, [{3: record, TEXT: "b"}]).json()["metadata"]
    assert changed["updatedRecordIds"] == [record]
    same = api.write(table, [{3: record, TEXT: "b"}]).json()["metadata"]
    assert same["unchangedRecordIds"] == [record]
    for extra in ({}, {"mergeFieldId": 3}):
        missing = api.write(table, [{3: 99999999, TEXT: "c"}], **extra)
        assert missing.status_code == 207
        assert missing.json()["metadata"]["createdRecordIds"] == []


def test_merging(api: Api, scratch: Any) -> None:
    made = scratch.table(("Key", "text", {"unique": True}), ("Plain", "text"))
    table = made.info().id
    key, plain = 6, 7
    first = api.write(table, [{key: "k", plain: "a"}], mergeFieldId=key)
    assert len(first.json()["metadata"]["createdRecordIds"]) == 1
    second = api.write(table, [{key: "k", plain: "b"}], mergeFieldId=key).json()["metadata"]
    assert len(second["updatedRecordIds"]) == 1
    assert api.write(table, [{key: "x", plain: "b"}], mergeFieldId=plain).status_code == 400
    assert api.write(table, [{plain: "b"}], mergeFieldId=key).status_code == 400


@pytest.mark.parametrize("field", [999999, FORMULA, 1])
def test_fields_that_cant_be_written(api: Api, table: str, field: int) -> None:
    api.clear(table)
    assert api.write(table, [{TEXT: "a", field: 1}]).status_code == 207


def test_a_value_outside_the_choices(api: Api, scratch: Any) -> None:
    made = scratch.table(
        ("Open", "text-multiple-choice", {"choices": ["a"]}),
        (
            "Closed",
            "text-multiple-choice",
            {"properties": {"allowNewChoices": False, "choices": ["a"]}},
        ),
    )
    table = made.info().id
    assert api.write(table, [{7: "zz"}]).status_code == 207
    assert api.write(table, [{6: "a"}]).status_code == 200


def test_empty_writes_and_deletes(api: Api, table: str, rows: dict[str, int]) -> None:
    assert api.post("/records", {"to": table, "data": []}).status_code == 400
    for body in ({"from": table}, {"from": table, "where": ""}):
        assert api.call("DELETE", "/records", json=body).status_code == 400
    blank = api.call("DELETE", "/records", json={"from": table, "where": "  "})
    assert (blank.status_code, blank.json()["numberDeleted"]) == (200, 0)
    assert len(api.ids(table, "{3.GT.'0'}")) == 4


def test_deletes_with_bad_clauses(api: Api, table: str, rows: dict[str, int]) -> None:
    body = {"from": table, "where": "not a clause"}
    assert api.call("DELETE", "/records", json=body).json()["numberDeleted"] == 0
    body = {"from": table, "where": f"{{{TEXT}.ex.'alpha'}}"}
    assert api.call("DELETE", "/records", json=body).json()["numberDeleted"] == 2


def test_deleting_fields_reports_failures(api: Api, scratch: Any) -> None:
    made = scratch.table(("A", "text"))
    params = {"tableId": made.info().id}
    body = api.call("DELETE", "/fields", params=params, json={"fieldIds": [6, 999999]}).json()
    assert body["deletedFieldIds"] == [6]
    assert body["errors"]


@pytest.mark.parametrize("setting", ["unique", "required"])
def test_create_field_refuses_some_settings(api: Api, table: str, setting: str) -> None:
    body = {"label": f"No {setting}", "fieldType": "text", setting: True}
    assert api.call("POST", "/fields", params={"tableId": table}, json=body).status_code == 400


def test_checkbox_queries(api: Api, table: str, rows: dict[str, int]) -> None:
    expected = [rows["alpha"]]
    for clause in (f"{{{BOX}.EX.'true'}}", f"{{{BOX}.EX.'1'}}", f"{{{BOX}.TV.'true'}}"):
        assert api.ids(table, clause) == expected


# Reports


def test_report_listing_and_running(api: Api, scratch: Any) -> None:
    made = scratch.table(("A", "text"))
    table = made.info().id
    api.write(table, [{6: f"r{n}"} for n in range(5)])
    params = {"tableId": table}
    listed = api.call("GET", "/reports", params=params).json()
    assert [(r["id"], r["name"]) for r in listed][:2] == [("1", "List All"), ("2", "List Changes")]
    assert {"id", "name", "type", "description", "query", "properties", "incomplete"} <= set(
        listed[0]
    )

    def run(report: str = "1", body: Any = None, **query: Any) -> httpx.Response:
        return api.call("POST", f"/reports/{report}/run", params={**params, **query}, json=body)

    ran = run().json()
    assert {"fields", "data", "metadata"} <= set(ran)
    assert len(ran["data"]) == 5
    assert "3" not in ran["data"][0]
    assert len(run(top=2).json()["data"]) == 2
    assert len(run(body={"top": 2, "skip": 1}).json()["data"]) == 5
    assert run(top=0).status_code == 400
    assert run(skip=-1).status_code == 400
    assert run(skip=100).json()["data"] == []
    assert len(run("01").json()["data"]) == 5
    assert run("999").status_code == 404
    assert run("abc").status_code == 404
    assert api.call("POST", "/reports/1/run").status_code == 400
    missing = api.call("POST", "/reports/1/run", params={"tableId": MISSING_TABLE})
    assert missing.status_code == 401


def test_running_a_report_adds_to_its_count(api: Api, scratch: Any) -> None:
    made = scratch.table(("A", "text"))
    params = {"tableId": made.info().id}

    def used() -> int:
        return api.call("GET", "/reports/1", params=params).json()["usedCount"]

    before = used()
    assert used() == before
    api.call("POST", "/reports/1/run", params=params)
    assert used() == before + 1
    assert api.call("POST", "/reports/1/run", params={**params, "top": 0}).status_code == 400
    assert used() == before + 1


def test_a_dropped_table_is_404_for_reports(api: Api, scratch: Any) -> None:
    made = scratch.table(("A", "text"))
    table = made.info().id
    scratch.created.remove(made)
    made.drop(confirm=made.info().name)
    response = api.call("POST", "/reports/1/run", params={"tableId": table})
    assert response.status_code == 404


# File attachments


def file_value(name: str, data: bytes) -> dict[str, str]:
    return {"fileName": name, "data": base64.b64encode(data).decode()}


@pytest.fixture
def filed(api: Api, table: str) -> int:
    api.clear(table)
    return api.add(table, {TEXT: "file", FILE: file_value("a.html", bytes(range(256)))})


def get_file(
    api: Api, table: str, record: int, version: int = 0, field: int = FILE
) -> httpx.Response:
    return api.call("GET", f"/files/{table}/{record}/{field}/{version}")


def test_downloads(api: Api, table: str, filed: int) -> None:
    response = get_file(api, table, filed)
    assert response.status_code == 200
    assert base64.b64decode(response.content) == bytes(range(256))
    assert "filename*=utf-8''a.html" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/html")


def test_file_versions(api: Api, table: str, filed: int) -> None:
    def upload(data: bytes) -> dict[str, Any]:
        rows = [{3: filed, FILE: file_value("a.html", data)}]
        return api.write(table, rows).json()["metadata"]

    assert upload(bytes(range(256)))["updatedRecordIds"] == [filed]  # identical contents
    upload(b"three")
    upload(b"four")
    value = api.value(table, filed, FILE)
    assert [v["versionNumber"] for v in value["versions"]] == [2, 3, 4]
    assert get_file(api, table, filed, 1).status_code == 404
    assert base64.b64decode(get_file(api, table, filed, 0).content) == b"four"
    assert api.call("DELETE", f"/files/{table}/{filed}/{FILE}/0").status_code == 200
    assert [v["versionNumber"] for v in api.value(table, filed, FILE)["versions"]] == [2, 3]
    upload(b"five")
    assert [v["versionNumber"] for v in api.value(table, filed, FILE)["versions"]] == [2, 3, 5]


def test_deleting_the_last_version_keeps_the_record(api: Api, table: str, filed: int) -> None:
    response = api.call("DELETE", f"/files/{table}/{filed}/{FILE}/1")
    assert response.status_code == 200
    assert "fileName" in response.json()
    assert api.value(table, filed, FILE) == {"url": "", "versions": []}


def test_reading_a_file_field(api: Api, table: str, filed: int) -> None:
    value = api.value(table, filed, FILE)
    assert value["url"].startswith("/files/")
    assert {"versionNumber", "fileName", "uploaded", "creator"} == set(value["versions"][0])
    assert "email" in value["versions"][0]["creator"]


@pytest.mark.parametrize("empty", ["", None])
def test_clearing_a_file_field_does_nothing(api: Api, table: str, filed: int, empty: Any) -> None:
    metadata = api.write(table, [{3: filed, FILE: empty}]).json()["metadata"]
    assert metadata["unchangedRecordIds"] == [filed]
    assert len(api.value(table, filed, FILE)["versions"]) == 1


def test_bad_file_values(api: Api, table: str) -> None:
    api.clear(table)
    assert api.write(table, [{FILE: "text"}]).status_code == 207
    assert api.write(table, [{FILE: {"fileName": "a.txt"}}]).status_code == 207
    assert api.write(table, [{FILE: file_value("", b"x")}]).status_code == 207
    nameless = api.write(table, [{TEXT: "n", FILE: {"data": "eA=="}}])
    assert nameless.status_code == 200
    record = nameless.json()["metadata"]["createdRecordIds"][0]
    assert api.value(table, record, FILE)["versions"] == []


def test_unsafe_file_contents_and_names_are_accepted(api: Api, table: str) -> None:
    api.clear(table)
    garbage = api.write(table, [{TEXT: "g", FILE: {"fileName": "g.txt", "data": "!!!not base64"}}])
    assert garbage.status_code == 200
    empty = api.add(table, {TEXT: "e", FILE: {"fileName": "e.txt", "data": ""}})
    assert get_file(api, table, empty).status_code == 200
    for name in ("../../etc/passwd", "C:\\x\\y.txt", "a.exe"):
        record = api.add(table, {TEXT: "n", FILE: file_value(name, b"x")})
        assert api.value(table, record, FILE)["versions"][0]["fileName"] == name


def test_a_file_sent_to_a_text_field_keeps_only_the_name(api: Api, table: str) -> None:
    api.clear(table)
    record = api.add(table, {TEXT: file_value("name.txt", b"contents")})
    assert api.value(table, record, TEXT) == "name.txt"


def test_file_errors(api: Api, table: str, filed: int) -> None:
    assert get_file(api, table, 99999999).status_code == 404
    assert get_file(api, table, filed, field=999).status_code == 404
    assert get_file(api, table, filed, 99).status_code == 404
    assert get_file(api, table, filed, field=TEXT).status_code == 400
    response = api.call("GET", f"/files/{MISSING_TABLE}/{filed}/{FILE}/0")
    assert response.status_code == 400
    assert "Invalid usertoken" in response.text


# Formulas


def formula(api: Api, table: str, text: Any, record: int | None = None) -> httpx.Response:
    body: dict[str, Any] = {"formula": text, "from": table}
    if record is not None:
        body["rid"] = record
    return api.post("/formula/run", body)


@pytest.mark.parametrize(
    ("text", "result", "kind"),
    [
        ("1 > 2", "0", "bool"),
        ("2 > 1", "1", "bool"),
        ("1/3", "0.3333333", "number"),
        ('"x"', "x", "text"),
        ("Days(3)", "72:00", "duration"),
        ("   ", "", "null"),
    ],
)
def test_formula_results(api: Api, table: str, text: str, result: str, kind: str) -> None:
    body = formula(api, table, text).json()
    assert (body["result"], body["resultType"], body["fieldTypeApplied"]) == (result, kind, False)


def test_formula_dates_are_display_text(api: Api, table: str) -> None:
    api.clear(table)
    record = api.add(table, {STAMP: "2026-03-04T05:06:07Z", DATE: "2026-03-04"})
    assert formula(api, table, "[Date]", record).json()["result"] != "2026-03-04"
    stamp = formula(api, table, "[Stamp]", record).json()
    assert stamp["resultType"] == "date/time"
    assert "T" not in stamp["result"]


def test_formula_errors(api: Api, table: str, rows: dict[str, int]) -> None:
    assert formula(api, table, "").status_code == 400
    assert formula(api, table, "[Text]").status_code == 400
    assert formula(api, table, "1 + 1", 99999999).status_code == 404
    assert api.post("/formula/run", {"formula": "1"}).status_code == 400
    assert formula(api, MISSING_TABLE, "1").status_code == 401
    for bad in ("1 +", "[No Such Field]", "NoSuchFunction()", '1 + "a"', "1/0"):
        assert formula(api, table, bad, rows["alpha"]).status_code == 400


def test_formula_names_ignore_case_and_read_other_tables(
    api: Api, scratch: Any, table: str, rows: dict[str, int]
) -> None:
    assert formula(api, table, "[text]", rows["alpha"]).json()["result"] == "alpha"
    assert formula(api, table, "today()").json()["resultType"] == "date"
    other = scratch.table(("Secret", "text"))
    other_id = other.info().id
    api.write(other_id, [{6: "other table value"}])
    read = f'ToText(GetFieldValues(GetRecords("{{3.GT.0}}", "{other_id}"), 6))'
    assert formula(api, table, read).json()["result"] == "other table value"


def test_formula_runs_change_nothing(api: Api, table: str, rows: dict[str, int]) -> None:
    before = api.value(table, rows["alpha"], 2)
    for _ in range(3):
        formula(api, table, "[Num] + 1", rows["alpha"])
    assert api.value(table, rows["alpha"], 2) == before


def create(api: Api, table: str, label: str, kind: str, text: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {"label": label, "fieldType": kind}
    if text is not None:
        body["properties"] = {"formula": text}
    return api.call("POST", "/fields", params={"tableId": table}, json=body)


@pytest.mark.parametrize(
    "kind",
    [
        "text",
        "numeric",
        "date",
        "checkbox",
        "duration",
        "timeofday",
        "currency",
        "percent",
        "rating",
        "url",
        "email",
        "datetime",
    ],
)
def test_field_types_that_take_a_formula(api: Api, scratch: Any, kind: str) -> None:
    table = scratch.table(("A", "text")).info().id
    sources = {
        "text": '"a"', "url": '"a"', "email": '"a"', "date": "Today()", "datetime": "Now()",
        "checkbox": "true", "duration": "Days(1)", "timeofday": "ToTimeOfDay(Now())",
    }  # fmt: skip
    response = create(api, table, "F", kind, sources.get(kind, "1"))
    assert response.status_code == 200, response.text


def test_timestamp_formulas_need_datetime(api: Api, scratch: Any) -> None:
    table = scratch.table(("A", "text")).info().id
    refused = create(api, table, "T", "timestamp", "Now()")
    assert refused.status_code == 400
    assert "[formula] property is not supported" in refused.text
    assert create(api, table, "M", "text-multi-line", '"a"').status_code == 400
    made = create(api, table, "D", "datetime", "Now()")
    assert made.json()["fieldType"] == "timestamp"


def test_formula_fields(api: Api, scratch: Any) -> None:
    made = scratch.table(("Num", "numeric"), ("Plain", "text"))
    table = made.info().id
    params = {"tableId": table}
    assert create(api, table, "E", "numeric", "").status_code == 400
    assert create(api, table, "B", "numeric", "1 +").status_code == 400
    assert create(api, table, "T", "text", "1").status_code == 400
    field = create(api, table, "F", "numeric", "[Num] / 3").json()["id"]
    record = api.add(table, {6: 1})
    assert api.value(table, record, field) == pytest.approx(1 / 3, abs=1e-15)
    update = lambda text: api.call(  # noqa: E731
        "POST", f"/fields/{field}", params=params, json={"properties": {"formula": text}}
    )
    assert update("1 +").status_code == 400
    assert update("[Num] * 2").status_code == 200
    assert api.value(table, record, field) == 2
    plain = api.call("POST", "/fields/7", params=params, json={"properties": {"formula": "1"}})
    assert plain.status_code == 400
    assert "Field must be of formula mode" in plain.text
    assert api.write(table, [{3: record, field: 1}]).status_code == 207
    renamed = api.call("POST", "/fields/6", params=params, json={"label": "Renamed"})
    assert renamed.status_code == 200
    got = api.call("GET", f"/fields/{field}", params=params).json()
    assert "[Renamed]" in got["properties"]["formula"]
    assert update("").status_code == 200
    assert api.value(table, record, field) is None


# Temporary tokens


def test_temporary_tokens_need_a_browser(api: Api, table: str) -> None:
    response = httpx.get(
        f"{API}/auth/temporary/{table}",
        headers={"QB-Realm-Hostname": api.scratch.client.realm},
        timeout=30,
    )
    assert response.status_code == 400
    assert "Domain not allowed" in response.text


# Field types


CASES = [
    ("text", "a", "a", ""),
    ("text-multiple-choice", "a", "a", ""),
    ("text-multi-line", "a", "a", ""),
    ("rich-text", "a", "a", ""),
    ("email", "a@example.com", "a@example.com", ""),
    ("url", "https://example.com", "https://example.com", ""),
    ("numeric", 1.5, 1.5, None),
    ("currency", 1.5, 1.5, None),
    ("rating", 3, 3, None),
    ("percent", 0.5, 0.5, None),
    ("date", "2026-03-04", "2026-03-04", ""),
    ("timestamp", "2026-03-04T05:06:07Z", "2026-03-04T05:06:07Z", ""),
    ("datetime", "2026-03-04T05:06:07Z", "2026-03-04T05:06:07Z", ""),
    ("timeofday", "13:30:00", "13:30:00", ""),
    ("checkbox", True, True, False),
    ("multitext", ["a"], ["a"], []),
]


@pytest.mark.parametrize(("kind", "written", "read", "empty"), CASES, ids=[c[0] for c in CASES])
def test_what_a_query_returns(
    api: Api, scratch: Any, kind: str, written: Any, read: Any, empty: Any
) -> None:
    table = scratch.table(("Other", "text")).info().id
    field = create(api, table, "F", kind).json()["id"]
    record = api.add(table, {field: written})
    assert api.value(table, record, field) == read
    blank = api.add(table, {6: "x"})
    assert api.value(table, blank, field) == empty


def test_other_field_shapes(api: Api, scratch: Any) -> None:
    table = scratch.table(("Other", "text")).info().id
    blank = api.add(table, {6: "x"})
    for kind, empty in (("user", None), ("multiuser", []), ("duration", None), ("phone", "")):
        field = create(api, table, kind, kind).json()["id"]
        assert api.value(table, blank, field) == empty
    address = create(api, table, "Addr", "address")
    assert address.status_code == 200
    assert api.value(table, blank, address.json()["id"]) == ""
    record = api.add(table, {create(api, table, "Dur", "duration").json()["id"]: 3600000})
    assert record


def test_record_id_is_an_integer_and_labels_are_unique(api: Api, scratch: Any) -> None:
    made = scratch.table(("Due Date", "text"))
    table = made.info().id
    assert isinstance(api.value(table, api.add(table, {6: "x"}), 3), int)
    assert create(api, table, "due date", "text").status_code == 400
    assert create(api, table, "Due-Date", "text").status_code == 200
    assert create(api, table, 'Code"""\nimport os', "text").status_code == 200


def test_get_table_needs_its_app(api: Api, scratch: Any, table: str) -> None:
    assert api.call("GET", f"/tables/{table}").status_code != 200
    params = {"appId": scratch.app_id}
    assert api.call("GET", f"/tables/{table}", params=params).status_code == 200
