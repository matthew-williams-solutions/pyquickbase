"""Tests for describing reads, which do no network access. The policy tests are in
test_policy.py."""

from typing import Any

import pytest

from pyquickbase import ReadRequest
from pyquickbase._read_request import describe_read

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
    assert (request.limit, request.skip) == (20, 40)


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
    assert query(select=[3]).limit is None


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
