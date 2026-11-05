"""Tests for read policies, which do no network access."""

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyquickbase import (
    ConfigurationError,
    Quickbase,
    ReadAccess,
    ReadRefusedError,
    ReadRequest,
    ReadRules,
    WriteAccess,
    WriteRules,
    all_of,
)
from pyquickbase._read_policy import check_read, describe_read
from pyquickbase.errors import InvalidResponseError

TASKS = "bqtasks"


def query(**body: Any) -> ReadRequest:
    found = describe_read("POST", "/records/query", None, {"from": TASKS, "sortBy": [], **body})
    assert found is not None
    return found


def paged(**body: Any) -> ReadRequest:
    return query(options={"top": 10, "skip": 0}, **body)


def report(report_type: str | None = "table", **params: Any) -> ReadRequest:
    found = describe_read(
        "POST",
        "/reports/7/run",
        {"tableId": TASKS, "top": 10, "skip": 0, **params},
        None,
        report_type=report_type,
    )
    assert found is not None
    return found


# Describing a read


def test_a_query_lists_its_fields_filter_sort_and_window() -> None:
    request = query(
        select=[3, 6],
        where="{7.EX.'Open'}AND({8.GT.'1'}OR{7.EX.'x'})",
        sortBy=[{"fieldId": 9, "order": "DESC"}],
        options={"top": 20, "skip": 40},
    )
    assert request.action == "query"
    assert request.table_id == TASKS
    assert request.field_ids == {3, 6}
    assert request.filter_field_ids == {7, 8}
    assert request.sort_field_ids == {9}
    assert (request.top, request.skip) == (20, 40)


def test_a_query_without_a_filter_has_no_filter_fields() -> None:
    assert query(select=[3]).filter_field_ids == frozenset()
    assert query(select=[3], where="  ").filter_field_ids == frozenset()


@pytest.mark.parametrize(
    "where",
    [
        "{7.EX.'_FID_9'}",  # Compares with field 9.
        "{7.EX.'x _fid_9'}",
        "{'Title'.EX.'x'}",  # Quickbase matches this against any field.
        "{Title.EX.'x'}",
        "{7.ex.'x'}",
        "{7.EX.'x'",
        "not a filter",
    ],
)
def test_a_filter_that_cant_be_read_is_unknown(where: str) -> None:
    assert query(select=[3], where=where).filter_field_ids is None


def test_a_filter_that_isnt_text_is_unknown() -> None:
    assert query(select=[3], where=["{7.EX.'x'}"]).filter_field_ids is None


@pytest.mark.parametrize(
    "sort", [None, False, [{"fieldId": "7", "order": "ASC"}], [{"fieldId": 7}]]
)
def test_a_sort_that_isnt_spelled_out_is_unknown(sort: Any) -> None:
    body: dict[str, Any] = {"from": TASKS, "select": [3]}
    if sort is not None:
        body["sortBy"] = sort
    found = describe_read("POST", "/records/query", None, body)
    assert found is not None
    assert found.sort_field_ids is None


@pytest.mark.parametrize("select", [None, "6", [6, "7"], [True], [0]])
def test_a_select_that_isnt_a_list_of_ids_is_unknown(select: Any) -> None:
    body: dict[str, Any] = {} if select is None else {"select": select}
    assert query(**body).field_ids is None


def test_a_query_without_top_has_no_top() -> None:
    assert query(select=[3]).top is None


@pytest.mark.parametrize(
    "body",
    [
        {"from": TASKS, "groupBy": [{"fieldId": 7}]},
        {"from": TASKS, "options": {"top": 1, "other": 1}},
        {"from": TASKS, "options": "top"},
        "not an object",
    ],
)
def test_a_query_with_anything_unrecognized_is_other(body: Any) -> None:
    found = describe_read("POST", "/records/query", None, body)
    assert found is not None
    assert found.action == "other"


def test_summary_pages_are_marked() -> None:
    body = {"from": TASKS, "select": [3], "sortBy": [], "options": {"top": 1, "skip": 0}}
    found = describe_read("POST", "/records/query", None, body, summarizing=True)
    assert found is not None
    assert found.action == "summarize"


def test_a_report_needs_its_type_which_a_request_doesnt_say() -> None:
    assert report().action == "report"
    assert report("summary").action == "summary_report"
    assert report("chart").action == "summary_report"
    assert report(None).action == "other"


def test_a_report_id_with_leading_zeros_is_the_same_report() -> None:
    found = describe_read("POST", "/reports/007/run", {"tableId": TASKS}, None, report_type="table")
    assert found is not None
    assert found.report_id == "7"


def test_a_report_with_a_body_or_other_parameters_is_other() -> None:
    with_body = describe_read(
        "POST", "/reports/7/run", {"tableId": TASKS}, {"top": 1}, report_type="table"
    )
    assert with_body is not None
    assert with_body.action == "other"
    assert report(filter="x").action == "other"


@pytest.mark.parametrize(
    ("method", "path", "params", "action", "table_id"),
    [
        ("GET", "/tables", {"appId": "bqapp"}, "tables", None),
        ("GET", "/fields", {"tableId": TASKS}, "fields", TASKS),
        ("GET", "/fields", {"tableId": TASKS, "includeFieldPerms": True}, "fields", TASKS),
        ("GET", "/reports", {"tableId": TASKS}, "reports", TASKS),
        ("GET", f"/tables/{TASKS}/relationships", {"skip": 0}, "relationships", TASKS),
        ("GET", f"/files/{TASKS}/1/13/0", None, "file", TASKS),
        ("GET", "/apps/bqapp", None, "app", None),
        ("GET", "/apps/bqapp/roles", None, "roles", None),
        ("GET", "/fields", {"tableId": [TASKS]}, "other", None),
        ("GET", "/reports", {"tableId": [TASKS]}, "other", None),
        ("GET", "/tables", {}, "other", None),
        ("GET", "/fields", {"tableId": TASKS, "x": 1}, "other", None),
        ("GET", "/fields/6", {"tableId": TASKS}, "other", None),
        ("GET", "/apps/bqapp/events", None, "other", None),
        ("GET", f"/tables/{TASKS}", {"appId": "bqapp"}, "other", None),
        ("POST", "/formula/run", None, "formula", None),
        ("POST", "/fields", None, "other", None),
        ("DELETE", "/tables/bqtasks", None, "other", None),
        ("HEAD", "/tables", None, "other", None),
    ],
)
def test_each_endpoint_is_described(
    method: str, path: str, params: Any, action: str, table_id: str | None
) -> None:
    found = describe_read(method, path, params, None)
    assert found is not None
    assert (found.action, found.table_id) == (action, table_id)


def test_a_get_with_a_body_is_other() -> None:
    found = describe_read("GET", "/tables", {"appId": "bqapp"}, {"x": 1})
    assert found is not None
    assert found.action == "other"


def test_a_file_download_names_its_field() -> None:
    found = describe_read("GET", f"/files/{TASKS}/1/13/2", None, None)
    assert found is not None
    assert found.field_ids == {13}


def test_writes_that_return_no_values_arent_reads() -> None:
    assert describe_read("POST", "/records", None, {"to": TASKS, "data": []}) is None
    assert describe_read("DELETE", f"/files/{TASKS}/1/13/1", None, None) is None


def test_a_write_that_returns_values_is_other() -> None:
    body = {"to": TASKS, "data": [], "fieldsToReturn": [9]}
    found = describe_read("POST", "/records", None, body)
    assert found is not None
    assert found.action == "other"


def test_a_delete_lists_its_filter_fields() -> None:
    found = describe_read("DELETE", "/records", None, {"from": TASKS, "where": "{9.EX.'x'}"})
    assert found is not None
    assert (found.action, found.filter_field_ids) == ("delete", {9})


def test_a_request_holds_no_values() -> None:
    request = query(select=[3], where="{7.EX.'s3cret'}", options={"top": 1, "skip": 0})
    assert "s3cret" not in repr(request)


# Deciding


def rules(**access: Any) -> ReadRules:
    return ReadRules({TASKS: ReadAccess(**{"fields": [6, 7], **access})})


def test_allowed_queries_pass() -> None:
    allowed = paged(select=[3, 6, 7], where="{7.EX.'x'}", sortBy=[{"fieldId": 6, "order": "ASC"}])
    assert rules()(allowed) is True


def test_record_id_is_always_allowed() -> None:
    request = paged(select=[3], where="{3.GT.'0'}", sortBy=[{"fieldId": 3, "order": "ASC"}])
    assert rules(fields=False)(request) is True


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ({"select": [3, 9]}, r"returning fields \[9\]"),
        ({"select": [3], "where": "{9.EX.'x'}"}, r"filtering on fields \[9\]"),
        ({"select": [3], "sortBy": [{"fieldId": 9, "order": "ASC"}]}, r"sorting by fields \[9\]"),
        ({"select": [3], "where": "{7.EX.'_FID_9'}"}, "filtering on can't be determined"),
        ({}, "returning can't be determined"),
    ],
)
def test_fields_outside_the_lists_are_refused(body: dict[str, Any], match: str) -> None:
    with pytest.raises(ReadRefusedError, match=match):
        rules()(paged(**body))


def test_filter_and_sort_default_to_the_returned_fields_and_can_differ() -> None:
    narrow = rules(filter=[7], sort=False)
    assert narrow(paged(select=[6], where="{7.EX.'x'}")) is True
    with pytest.raises(ReadRefusedError, match="filtering"):
        narrow(paged(select=[6], where="{6.EX.'x'}"))
    with pytest.raises(ReadRefusedError, match="sorting"):
        narrow(paged(select=[6], sortBy=[{"fieldId": 7, "order": "ASC"}]))
    # A field may be filtered on without being returned, which is a choice the rules make.
    assert rules(fields=[6], filter=[9])(paged(select=[6], where="{9.EX.'x'}")) is True


def test_true_allows_every_field() -> None:
    assert rules(fields=True)(paged(select=[3, 99], where="{98.EX.'x'}")) is True


def test_the_window_is_limited_counting_skip() -> None:
    limited = rules(max_records=50)
    assert limited(query(select=[6], options={"top": 50, "skip": 0})) is True
    with pytest.raises(ReadRefusedError, match="at most 50 records"):
        limited(query(select=[6], options={"top": 51, "skip": 0}))
    with pytest.raises(ReadRefusedError, match="at most 50 records"):
        limited(query(select=[6], options={"top": 10, "skip": 45}))
    with pytest.raises(ReadRefusedError, match="would get all"):
        limited(query(select=[6]))


def test_summary_pages_have_their_own_limit() -> None:
    body = {"from": TASKS, "select": [6], "sortBy": [], "options": {"top": 1000, "skip": 0}}
    page = describe_read("POST", "/records/query", None, body, summarizing=True)
    assert page is not None
    assert rules(max_records=10)(page) is True
    with pytest.raises(ReadRefusedError, match="max_summary_records"):
        rules(max_summary_records=999)(page)


def test_unlisted_tables_are_refused() -> None:
    other = describe_read("GET", "/fields", {"tableId": "bqother"}, None)
    assert other is not None
    with pytest.raises(ReadRefusedError, match="bqother isn't listed"):
        rules()(other)


def test_reports_must_be_listed_as_their_kind() -> None:
    assert rules(reports=["7"])(report()) is True
    assert rules(reports=[7])(report()) is True
    with pytest.raises(ReadRefusedError, match="report 7 isn't a listed table report"):
        rules()(report())
    with pytest.raises(ReadRefusedError, match="summary report"):
        rules(reports=["7"])(report("summary"))
    assert rules(summary_reports=["7"])(report("summary")) is True
    with pytest.raises(ReadRefusedError, match="at most 5"):
        rules(reports=["7"], max_records=5)(report())


def test_reports_through_request_are_refused() -> None:
    with pytest.raises(ReadRefusedError, match=r"can't be run through request\(\)"):
        rules(reports=["7"])(report(None))


def test_files_must_be_listed_even_when_the_field_is_returned() -> None:
    download = describe_read("GET", f"/files/{TASKS}/1/13/0", None, None)
    assert download is not None
    with pytest.raises(ReadRefusedError, match="downloading"):
        rules(fields=[13])(download)
    assert rules(files=[13])(download) is True


@pytest.mark.parametrize(
    ("method", "path", "body", "match"),
    [
        ("POST", "/formula/run", {"formula": "1", "from": TASKS}, "a formula can read"),
        ("GET", "/apps/bqapp", None, "only the listed tables"),
        ("GET", "/apps/bqapp/roles", None, "only the listed tables"),
        ("POST", "/fields", {"label": "x"}, "can't be changed"),
        ("GET", "/apps/bqapp/events", None, "only records"),
    ],
)
def test_formulas_metadata_and_schema_changes_are_refused(
    method: str, path: str, body: Any, match: str
) -> None:
    found = describe_read(method, path, None, body)
    assert found is not None
    with pytest.raises(ReadRefusedError, match=match):
        rules(fields=True)(found)


def test_a_delete_is_held_to_the_filter_list() -> None:
    delete = describe_read("DELETE", "/records", None, {"from": TASKS, "where": "{9.EX.'x'}"})
    assert delete is not None
    with pytest.raises(ReadRefusedError, match=r"filtering on fields \[9\]"):
        rules()(delete)
    assert rules(filter=[9])(delete) is True


def test_refusals_never_repeat_values() -> None:
    with pytest.raises(ReadRefusedError) as caught:
        rules()(paged(select=[3], where="{9.EX.'s3cret'}"))
    assert "s3cret" not in str(caught.value)


def test_a_policy_must_return_exactly_true() -> None:
    request = paged(select=[3])
    for result in (False, None, 1, "yes"):
        with pytest.raises(ReadRefusedError, match="refused this query"):
            check_read(lambda _, result=result: result, request)

    def broken(_: ReadRequest) -> bool:
        raise KeyError("s3cret")

    with pytest.raises(ReadRefusedError, match="raised KeyError") as caught:
        check_read(broken, request)
    assert "s3cret" not in str(caught.value)
    assert isinstance(caught.value.__cause__, KeyError)


# Configuring


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fields": []},
        {"fields": ["Title"]},
        {"fields": "6"},
        {"fields": [6], "filter": []},
        {"fields": [6], "write_only": True},
        {"fields": [6], "files": ["Attachment"]},
        {"fields": [6], "reports": ["Open tasks"]},
        {"fields": [6], "reports": "7"},
        {"fields": [6], "max_records": 0},
        {"fields": [6], "max_records": 1.5},
        {"fields": [6], "max_summary_records": True},
        {"fields": [6], "user_emails": "yes"},
    ],
)
def test_bad_access_is_a_configuration_error(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError):
        ReadAccess(**kwargs)


def test_fields_is_required() -> None:
    with pytest.raises(TypeError):
        ReadAccess()  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]


def test_bad_rules_are_a_configuration_error() -> None:
    for tables in ([], {"": ReadAccess(fields=True)}, {TASKS: WriteAccess()}):
        with pytest.raises(ConfigurationError):
            ReadRules(tables)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def test_rules_of_the_wrong_kind_are_refused() -> None:
    reads = ReadRules({TASKS: ReadAccess(fields=True)})
    writes = WriteRules({TASKS: WriteAccess(insert=True)})
    with pytest.raises(ConfigurationError, match="Pass it as read_policy"):
        Quickbase(realm="acme", token="t", write_policy=reads)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    with pytest.raises(ConfigurationError, match="Pass it as write_policy"):
        Quickbase(realm="acme", token="t", read_policy=writes)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
    with pytest.raises(ConfigurationError, match="can't combine"):
        all_of(reads, writes)  # type: ignore[call-overload]  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_a_read_policy_can_be_combined_with_read_only() -> None:
    reads = ReadRules({TASKS: ReadAccess(fields=True)})
    with Quickbase(realm="acme", token="t", read_policy=reads, read_only=True):
        pass


def test_an_async_read_policy_is_refused() -> None:
    async def policy(_: ReadRequest) -> bool:
        return True

    with pytest.raises(ConfigurationError, match="can't be async"):
        Quickbase(realm="acme", token="t", read_policy=policy)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def test_all_of_combines_read_policies() -> None:
    seen: list[str] = []

    def log(request: ReadRequest) -> bool:
        seen.append(request.action)
        return True

    combined = all_of(rules(), log)
    assert combined(paged(select=[6])) is True
    with pytest.raises(ReadRefusedError):
        combined(paged(select=[9]))
    assert seen == ["query"]


# Trimming


def trim(access: ReadRules, request: ReadRequest | None, payload: Any, **kw: Any) -> Any:
    method = kw.get("method", request.method if request else "POST")
    path = kw.get("path", request.path if request else "/records")
    return access._trim_response(request, method, path, payload)  # pyright: ignore[reportPrivateUsage]


def test_tables_are_left_out_unless_listed_and_keep_only_known_keys() -> None:
    request = describe_read("GET", "/tables", {"appId": "bqapp"}, None)
    payload = [
        {"id": TASKS, "name": "Tasks", "keyFieldId": 3, "spaceUsed": "1 MB", "alias": "_DBID"},
        {"id": "bqsecret", "name": "Salaries"},
    ]
    assert trim(rules(), request, payload) == [{"id": TASKS, "name": "Tasks", "keyFieldId": 3}]


def test_fields_keep_visible_ones_without_formulas_or_permissions() -> None:
    request = describe_read("GET", "/fields", {"tableId": TASKS}, None)
    payload = [
        {"id": 3, "label": "Record ID#", "fieldType": "recordid", "appearsByDefault": True},
        {
            "id": 6,
            "label": "Title",
            "fieldType": "text",
            "appearsByDefault": True,
            "fieldHelp": "The task's name",
            "bold": True,
            "properties": {"formula": "[Salary]", "choices": ["a"]},
            "permissions": [{"role": "x"}],
        },
        {"id": 7, "label": "Status", "fieldType": "text", "appearsByDefault": True},
        {"id": 8, "label": "Code", "fieldType": "text", "appearsByDefault": True},
        {"id": 9, "label": "Notes", "fieldType": "text"},
        {"id": 13, "label": "Attachment", "fieldType": "file"},
    ]
    found = trim(rules(fields=[6], filter=[7], write_only=[8], files=[13]), request, payload)
    assert [item["id"] for item in found] == [3, 6, 7, 8, 13]
    assert found[1] == {
        "id": 6,
        "label": "Title",
        "fieldType": "text",
        "appearsByDefault": True,
        "fieldHelp": "The task's name",
        "properties": {"choices": ["a"]},
    }
    # Only a field that may be returned is selected by default.
    assert [item["appearsByDefault"] for item in found] == [True, True, False, False, False]


def test_reports_keep_listed_ones_without_their_filters() -> None:
    request = describe_read("GET", "/reports", {"tableId": TASKS}, None)
    payload = [
        {"id": "7", "name": "Open", "type": "table", "query": {"filter": "{9.EX.'s3cret'}"}},
        {"id": "8", "name": "Totals", "type": "summary"},
        {"id": "9", "name": "Hidden", "type": "table"},
    ]
    found = trim(rules(reports=["7"], summary_reports=["8"]), request, payload)
    assert found == [
        {"id": "7", "name": "Open", "type": "table"},
        {"id": "8", "name": "Totals", "type": "summary"},
    ]


def test_relationships_keep_their_count_and_only_visible_fields() -> None:
    request = describe_read("GET", f"/tables/{TASKS}/relationships", {"skip": 0}, None)
    payload = {
        "relationships": [
            {
                "id": 6,
                "parentTableId": "bqsecret",
                "childTableId": TASKS,
                "foreignKeyField": {"id": 9, "label": "Salary", "type": "numeric"},
                "lookupFields": [{"id": 6, "label": "Title"}, {"id": 10, "label": "Hidden"}],
                "summaryFields": [{"id": 20, "label": "Total"}],
                "isCrossApp": False,
            }
        ],
        "metadata": {"totalRelationships": 1, "numRelationships": 1, "skip": 0},
    }
    found = trim(rules(), request, payload)
    assert found["metadata"] == payload["metadata"]
    assert found["relationships"] == [
        {
            "id": 6,
            "parentTableId": "bqsecret",
            "childTableId": TASKS,
            "isCrossApp": False,
            "lookupFields": [{"id": 6, "label": "Title"}],
            "summaryFields": [],
        }
    ]


USER = {"email": "a@example.com", "id": "1.ab", "name": "Ann", "userName": "ann"}
FILE = {
    "url": "/files/x",
    "versions": [
        {"versionNumber": 1, "fileName": "a.txt", "uploaded": "2026", "creator": USER},
    ],
}


def page(fields: list[tuple[int, str]], row: dict[int, Any]) -> dict[str, Any]:
    return {
        "fields": [{"id": fid, "label": f"F{fid}", "type": kind} for fid, kind in fields],
        "data": [{str(fid): {"value": value} for fid, value in row.items()}],
        "metadata": {"totalRecords": 1, "numRecords": 1, "skip": 0},
    }


def test_records_lose_unlisted_columns_emails_and_uploaders() -> None:
    payload = page(
        [(3, "recordid"), (6, "user"), (7, "multiuser"), (9, "text"), (13, "file")],
        {3: 1, 6: USER, 7: [USER, USER], 9: "s3cret", 13: FILE},
    )
    found = trim(rules(fields=[6, 7, 13]), report(), payload)
    assert [item["id"] for item in found["fields"]] == [3, 6, 7, 13]
    row = found["data"][0]
    assert set(row) == {"3", "6", "7", "13"}
    assert row["6"]["value"] == {"id": "1.ab", "name": "Ann"}
    assert row["7"]["value"] == [{"id": "1.ab", "name": "Ann"}] * 2
    assert row["13"]["value"] == {
        "url": "/files/x",
        "versions": [{"versionNumber": 1, "fileName": "a.txt", "uploaded": "2026"}],
    }
    assert found["metadata"] == payload["metadata"]


def test_user_emails_can_be_kept() -> None:
    payload = page([(6, "user")], {6: USER})
    found = trim(rules(user_emails=True), paged(select=[6]), payload)
    assert found["data"][0]["6"]["value"] == USER


def test_users_are_recognized_by_shape_whatever_the_type_says() -> None:
    payload = page([(6, "text")], {6: USER})
    assert trim(rules(), paged(select=[6]), payload)["data"][0]["6"]["value"] == {
        "id": "1.ab",
        "name": "Ann",
    }


def test_a_listed_summary_report_keeps_its_columns() -> None:
    # Column IDs here are positions, so 9 isn't field 9.
    payload = page([(2, "text"), (9, "numeric")], {2: "Open", 9: 5})
    found = trim(rules(summary_reports=["7"]), report("summary"), payload)
    assert [item["id"] for item in found["fields"]] == [2, 9]


def test_a_deleted_file_versions_details_are_dropped() -> None:
    found = trim(rules(), None, {"creator": USER}, method="DELETE", path=f"/files/{TASKS}/1/13/1")
    assert found is None


def test_a_response_of_the_wrong_shape_is_an_error_not_passed_through() -> None:
    with pytest.raises(InvalidResponseError, match="couldn't check it"):
        trim(rules(), paged(select=[6]), {"data": "s3cret"})


@given(st.lists(st.integers(min_value=1, max_value=30), max_size=10))
def test_trimmed_records_only_hold_allowed_fields(ids: list[int]) -> None:
    allowed = {3, 6, 7}
    payload = page([(fid, "text") for fid in ids], dict.fromkeys(ids, "x"))
    found = trim(rules(), paged(select=[6]), payload)
    assert {int(key) for key in found["data"][0]} <= allowed
    assert {item["id"] for item in found["fields"]} <= allowed
