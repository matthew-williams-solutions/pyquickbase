"""Tests for describing writes, which do no network access. The policy tests are in
test_policy.py."""

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyquickbase import WriteRequest
from pyquickbase._write_request import describe_write

TASKS = "bqtasks"
NOTES = "bqnotes"


def row(*field_ids: Any) -> dict[str, Any]:
    return {str(field_id): {"value": "x"} for field_id in field_ids}


def records(rows: list[Any], *, key_field_id: int | None = 3, **extra: Any) -> WriteRequest:
    body = {"to": TASKS, "data": rows, **extra}
    return describe_write("POST", "/records", body, key_field_id=key_field_id)


# Describing a write


def test_a_row_without_a_record_id_is_an_insert() -> None:
    request = records([row(6, 7), row(8)])
    assert request.action == "insert"
    assert request.table_id == TASKS
    assert request.field_ids == {6, 7, 8}
    assert request.row_count == 2
    assert (request.method, request.path) == ("POST", "/records")


@pytest.mark.parametrize("key_field_id", [6, None])
def test_without_a_merge_field_an_insert_is_only_certain_with_record_id_as_key(
    key_field_id: int | None,
) -> None:
    # Quickbase updates the record when a row has an existing value in a custom key field.
    assert records([row(6, 7)], key_field_id=key_field_id).action == "upsert"
    assert records([row(7)], key_field_id=key_field_id).action == "upsert"


def test_a_merge_on_record_id_is_an_update_whatever_the_key_field() -> None:
    assert records([row(3, 7)], key_field_id=6, mergeFieldId=3).action == "update"


def test_merging_on_record_id_is_an_update() -> None:
    request = records([row(3, 7)], mergeFieldId=3)
    assert request.action == "update"
    assert request.field_ids == {7}


def test_rows_with_a_record_id_and_no_merge_field_are_an_update() -> None:
    request = records([row(3, 7), row(3, 8)])
    assert request.action == "update"
    assert request.field_ids == {7, 8}


def test_a_mix_of_rows_could_do_either() -> None:
    assert records([row(3, 7), row(8)]).action == "upsert"
    assert records([row(3, 7), row(8)]).field_ids == {3, 7, 8}


@pytest.mark.parametrize("merge", [11, "3", 3.0, True, None, [3]])
def test_merging_on_anything_else_could_create_a_record(merge: Any) -> None:
    # Quickbase accepts "3" and 3.0 as field 3, but only a plain 3 is certain to be an update here.
    request = records([row(3, 7)], mergeFieldId=merge)
    assert request.action == "upsert"
    assert 3 in (request.field_ids or ())


@pytest.mark.parametrize("key", ["07", " 7", "7 ", "+7", "0", "\u0667", "7.0", "", "x"])
def test_field_keys_in_any_other_form_are_unknown(key: str) -> None:
    # Quickbase accepts "07" and " 7" as field 7, which could otherwise get around a field list.
    for extra in ({}, {"mergeFieldId": 3}):
        request = records([{"3": {"value": 1}, key: {"value": "x"}}], **extra)
        assert request.field_ids is None
        assert request.action == "upsert"


@pytest.mark.parametrize("rows", [["x"], [None], [[]], [5]])
def test_rows_that_arent_objects_are_unknown(rows: list[Any]) -> None:
    assert records(rows).field_ids is None


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "x",
        {},
        {"to": TASKS},
        {"to": TASKS, "data": "x"},
        {"to": TASKS, "data": {"6": {"value": 1}}},
    ],
)
def test_bodies_it_cant_read_are_other(body: Any) -> None:
    assert describe_write("POST", "/records", body).action == "other"


@pytest.mark.parametrize("to", [None, 5, ["bqtasks"], {"id": "bqtasks"}])
def test_a_table_that_isnt_text_is_unknown(to: Any) -> None:
    assert describe_write("POST", "/records", {"to": to, "data": [row(6)]}).table_id is None
    assert describe_write("DELETE", "/records", {"from": to, "where": "x"}).table_id is None


def test_the_wrong_key_for_the_table_leaves_it_unknown() -> None:
    assert describe_write("POST", "/records", {"To": TASKS, "data": [row(6)]}).table_id is None
    assert describe_write("DELETE", "/records", {"to": TASKS}).table_id is None


def test_deletes() -> None:
    request = describe_write("DELETE", "/records", {"from": TASKS, "where": "{3.GT.'0'}"})
    assert request.action == "delete"
    assert request.table_id == TASKS
    assert request.field_ids is None
    assert request.row_count is None


def test_deleting_a_file_version() -> None:
    request = describe_write("DELETE", "/files/bqtasks/5/13/2", None)
    assert request.action == "delete_file"
    assert request.table_id == TASKS
    assert request.field_ids == {13}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/apps/bqapp"),
        ("GET", "/apps"),
        ("DELETE", "/apps"),
        ("POST", "/apps/bqapp/copy/"),
        ("PUT", "/fields"),
        ("POST", "/fields/06"),
        ("POST", "/tables/bqtasks/relationships"),
        ("PATCH", "/tables/bqtasks"),
        ("PUT", "/records"),
        ("PATCH", "/records"),
        ("POST", "/records/"),
        ("POST", "/Records"),
        ("POST", "/x/../records"),
        ("POST", "/files/bqtasks/5/13/2"),
        ("DELETE", "/files/bqtasks/5/13"),
        ("DELETE", "/files/../bqtasks/5/13/2"),
        ("DELETE", "/files/bqtasks/5/13/2/"),
    ],
)
def test_everything_else_is_other(method: str, path: str) -> None:
    request = describe_write(method, path, {"to": TASKS, "from": TASKS, "data": [row(6)]})
    assert request.action == "other"
    assert request.table_id is None


def schema(
    method: str, path: str, body: Any = None, *, table: str = TASKS, **kwargs: Any
) -> WriteRequest:
    params = {"tableId": table, "appId": "bqapp"}
    return describe_write(method, path, body, params=params, **kwargs)


@pytest.mark.parametrize(
    ("method", "path", "body", "action", "table_id"),
    [
        ("POST", "/tables", {"name": "New"}, "create_table", None),
        ("POST", f"/tables/{TASKS}", {"name": "New"}, "update_table", TASKS),
        ("DELETE", f"/tables/{TASKS}", None, "delete_table", TASKS),
        ("POST", "/fields", {"label": "x", "fieldType": "text"}, "create_field", TASKS),
        ("POST", "/fields/6", {"label": "y"}, "update_field", TASKS),
        ("DELETE", "/fields", {"fieldIds": [6, 7]}, "delete_field", TASKS),
        ("POST", f"/tables/{TASKS}/relationship", {"parentTableId": NOTES}, "create_relationship",
         TASKS),
        ("POST", f"/tables/{TASKS}/relationship/7", {"lookupFieldIds": [6]},
         "update_relationship", TASKS),
        ("DELETE", f"/tables/{TASKS}/relationship/7", None, "delete_relationship", TASKS),
    ],
)  # fmt: skip
def test_schema_changes_are_described(
    method: str, path: str, body: Any, action: str, table_id: str | None
) -> None:
    request = schema(method, path, body)
    assert (request.action, request.table_id) == (action, table_id)


def test_a_schema_change_says_what_it_touches() -> None:
    created = schema("POST", "/fields", {"fieldType": "date", "properties": {"formula": ""}})
    assert (created.field_type, created.derived) == ("date", {"formula"})
    assert schema("POST", "/fields/6", {"label": "y"}).field_ids == {6}
    assert schema("DELETE", "/fields", {"fieldIds": [6, 7]}).field_ids == {6, 7}
    assert schema("DELETE", "/fields", {"fieldIds": ["6"]}).field_ids is None
    assert schema("POST", "/tables", {"name": "x"}).app_id == "bqapp"
    link = schema("POST", f"/tables/{TASKS}/relationship", {"parentTableId": NOTES,
                                                         "lookupFieldIds": [6]})  # fmt: skip
    assert (link.parent_table_id, link.derived) == (NOTES, {"lookup"})
    path = f"/tables/{TASKS}/relationship/7"
    added = schema("POST", path, {"summaryFields": [{}]}, parent_table_id=NOTES)
    assert (added.parent_table_id, added.derived) == (NOTES, {"summary"})
    assert schema("POST", path, {"lookupFieldIds": [6]}).parent_table_id is None
    assert schema("POST", path, {"lookupFieldIds": "6"}).action == "other"


def test_a_description_holds_no_values() -> None:
    assert "s3cret" not in repr(records([{"6": {"value": "s3cret"}}]))


# Randomized checks

JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=6),
    lambda inner: (
        st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=4), inner, max_size=3)
    ),
    max_leaves=12,
)
FIELD_KEYS = st.one_of(
    st.sampled_from(["3", "6", "7", "8", "03", "07", " 7", "7 ", "+7", "\u0667", "0"]),
    st.text(max_size=4),
)
ROWS = st.lists(st.dictionaries(FIELD_KEYS, st.just({"value": "x"}), max_size=4), max_size=4)
EXTRA = st.dictionaries(st.sampled_from(["mergeFieldId", "fieldsToReturn"]), JSON_VALUES)


@given(st.sampled_from(["POST", "DELETE", "PUT"]), st.sampled_from(["/records", "/x"]), JSON_VALUES)
def test_any_body_can_be_described(method: str, path: str, body: Any) -> None:
    assert describe_write(method, path, body).method == method


@pytest.mark.parametrize(
    ("method", "path", "action", "app_id"),
    [
        ("POST", "/apps", "create_app", None),
        ("POST", "/apps/bqapp/copy", "copy_app", "bqapp"),
        ("DELETE", "/apps/bqapp", "delete_app", "bqapp"),
    ],
)
def test_making_and_deleting_apps(method: str, path: str, action: str, app_id: str | None) -> None:
    request = describe_write(method, path, {"name": "x"})
    assert (request.action, request.app_id, request.table_id) == (action, app_id, None)
