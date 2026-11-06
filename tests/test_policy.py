"""Tests for write policies, which do no network access."""

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyquickbase import (
    ConfigurationError,
    Quickbase,
    SchemaAccess,
    WriteAccess,
    WriteRefusedError,
    WriteRequest,
    WriteRules,
    all_of,
)
from pyquickbase._policy import check_policy, describe_write

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
        ("POST", "/apps"),
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


def full(**grant: Any) -> WriteAccess:
    return WriteAccess(schema=SchemaAccess(**grant))


def test_schema_changes_need_a_schema_grant() -> None:
    rules = WriteRules({TASKS: WriteAccess(insert=True, update=True, delete=True)})
    with pytest.raises(WriteRefusedError, match="doesn't allow schema changes"):
        rules(schema("POST", "/fields", {"label": "x", "fieldType": "text"}))
    with pytest.raises(WriteRefusedError, match="creating tables isn't allowed"):
        rules(schema("POST", "/tables", {"name": "x"}))


@pytest.mark.parametrize(
    ("grant", "request_args", "allowed"),
    [
        ({"update_table": True}, ("POST", f"/tables/{TASKS}", {}), True),
        ({}, ("POST", f"/tables/{TASKS}", {}), False),
        ({"delete_table": True}, ("DELETE", f"/tables/{TASKS}", None), True),
        ({"update_table": True}, ("DELETE", f"/tables/{TASKS}", None), False),
        ({"create_fields": True}, ("POST", "/fields", {"fieldType": "text"}), True),
        ({"create_fields": True, "field_types": ["date"]}, ("POST", "/fields",
                                                            {"fieldType": "text"}), False),
        ({"create_fields": True}, ("POST", "/fields", {}), False),
        ({"update_fields": [6]}, ("POST", "/fields/6", {"label": "y"}), True),
        ({"update_fields": [7]}, ("POST", "/fields/6", {"label": "y"}), False),
        ({"delete_fields": True}, ("DELETE", "/fields", {"fieldIds": [6]}), True),
        ({"delete_fields": [6]}, ("DELETE", "/fields", {"fieldIds": [6, 7]}), False),
        ({"delete_fields": [6]}, ("DELETE", "/fields", {"fieldIds": "6"}), False),
    ],
)  # fmt: skip
def test_each_schema_change_is_granted_separately(
    grant: dict[str, Any], request_args: tuple[str, str, Any], allowed: bool
) -> None:
    rules = WriteRules({TASKS: full(**grant)})
    assert allows(rules, schema(*request_args)) is allowed


def test_formulas_need_their_own_grant_and_cant_make_links() -> None:
    formula = {"fieldType": "numeric", "properties": {"formula": "1"}}
    assert not allows(WriteRules({TASKS: full(create_fields=True)}), schema("POST", "/fields",
                                                                             formula))  # fmt: skip
    rules = WriteRules({TASKS: full(create_fields=True, update_fields=True, formulas=True)})
    assert allows(rules, schema("POST", "/fields", formula))
    assert allows(rules, schema("POST", "/fields/6", {"properties": {"formula": "2"}}))
    for unsafe in ("url", "rich-text"):
        body = {"fieldType": unsafe, "properties": {"formula": '"x"'}}
        with pytest.raises(WriteRefusedError, match="acts as them"):
            rules(schema("POST", "/fields", body))
    assert allows(rules, schema("POST", "/fields", {"fieldType": "url"}))


def test_relationships_need_both_tables_and_their_grants() -> None:
    link = schema("POST", f"/tables/{TASKS}/relationship", {"parentTableId": NOTES})
    lookup = schema("POST", f"/tables/{TASKS}/relationship/7", {"lookupFieldIds": [6]},
                    parent_table_id=NOTES)  # fmt: skip
    summary = schema("POST", f"/tables/{TASKS}/relationship/7", {"summaryFields": [{}]},
                     parent_table_id=NOTES)  # fmt: skip
    unlink = schema("DELETE", f"/tables/{TASKS}/relationship/7", parent_table_id=NOTES)
    only_child = WriteRules({TASKS: full(relationships=True)})
    with pytest.raises(WriteRefusedError, match=f"table {NOTES} isn't listed"):
        only_child(link)
    both = WriteRules({TASKS: full(relationships=True), NOTES: full(relationships=True)})
    assert allows(both, link)
    assert not allows(both, lookup)
    assert not allows(both, summary)
    assert not allows(both, unlink)
    granted = WriteRules(
        {
            TASKS: full(relationships=True, lookups=True, delete_fields=True),
            NOTES: full(relationships=True, summaries=True, delete_fields=True),
        }
    )
    for request in (link, lookup, summary, unlink):
        assert allows(granted, request), request.action
    unknown_parent = schema("POST", f"/tables/{TASKS}/relationship/7", {"summaryFields": [{}]})
    with pytest.raises(WriteRefusedError, match="parent table can't be determined"):
        granted(unknown_parent)


def test_new_tables_get_their_own_access() -> None:
    rules = WriteRules({}, create_tables_in=["bqapp"], new_tables=full(create_fields=True))
    assert allows(rules, schema("POST", "/tables", {"name": "x"}))
    field = schema("POST", "/fields", {"fieldType": "text"}, table="bqnew")
    assert not allows(rules, field)
    rules._adopt("bqnew")  # pyright: ignore[reportPrivateUsage]
    assert allows(rules, field)
    assert not allows(WriteRules({}, create_tables_in=["other"]), schema("POST", "/tables", {}))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"update_fields": []},
        {"formulas": 1},
        {"field_types": "text"},
        {"field_types": []},
        {"field_types": [""]},
    ],
)
def test_schema_access_checks_its_settings(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError):
        SchemaAccess(**kwargs)


def test_write_rules_check_their_new_settings() -> None:
    with pytest.raises(ConfigurationError, match="schema takes"):
        WriteAccess(schema=True)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ConfigurationError, match="create_tables_in"):
        WriteRules({}, create_tables_in="bqapp")
    with pytest.raises(ConfigurationError, match="new_tables"):
        WriteRules({}, new_tables=SchemaAccess())  # pyright: ignore[reportArgumentType]


def test_a_description_holds_no_values() -> None:
    assert "s3cret" not in repr(records([{"6": {"value": "s3cret"}}]))


# WriteRules


def allows(rules: WriteRules, request: WriteRequest) -> bool:
    try:
        return rules(request)
    except WriteRefusedError:
        return False


def test_a_table_that_isnt_listed_is_refused() -> None:
    rules = WriteRules(
        {NOTES: WriteAccess(insert=True, update=True, delete=True, delete_files=True)}
    )
    for request in (
        records([row(6)]),
        records([row(3, 6)], mergeFieldId=3),
        describe_write("DELETE", "/records", {"from": TASKS}),
        describe_write("DELETE", "/files/bqtasks/5/13/2", None),
    ):
        with pytest.raises(WriteRefusedError, match=f"table {TASKS} isn't listed"):
            rules(request)


def test_table_ids_must_match_exactly() -> None:
    rules = WriteRules({"BQTASKS": WriteAccess(insert=True)})
    assert not allows(rules, records([row(6)]))


def test_nothing_is_granted_by_default() -> None:
    rules = WriteRules({TASKS: WriteAccess()})
    assert not allows(rules, records([row(6)]))
    assert not allows(rules, records([row(3, 6)], mergeFieldId=3))
    assert not allows(rules, records([row(6)], mergeFieldId=6))
    assert not allows(rules, describe_write("DELETE", "/records", {"from": TASKS}))
    assert not allows(rules, describe_write("DELETE", "/files/bqtasks/5/13/2", None))


def test_each_action_is_granted_separately() -> None:
    insert = records([row(6)])
    update = records([row(3, 6)], mergeFieldId=3)
    upsert = records([row(6)], mergeFieldId=6)
    delete = describe_write("DELETE", "/records", {"from": TASKS})
    delete_file = describe_write("DELETE", "/files/bqtasks/5/13/2", None)
    cases: list[tuple[dict[str, Any], set[str]]] = [
        ({"insert": True}, {"insert"}),
        ({"update": True}, {"update"}),
        ({"insert": True, "update": True}, {"insert", "update", "upsert"}),
        ({"delete": True}, {"delete"}),
        ({"delete_files": True}, {"delete_file"}),
    ]
    for grant, expected in cases:
        rules = WriteRules({TASKS: WriteAccess(**grant)})
        for request in (insert, update, upsert, delete, delete_file):
            assert allows(rules, request) == (request.action in expected), (grant, request.action)


def test_field_lists_limit_updates() -> None:
    rules = WriteRules({TASKS: WriteAccess(update=[7, 12])})
    assert rules(records([row(3, 7)], mergeFieldId=3))
    assert rules(records([row(3, 7, 12)]))
    with pytest.raises(WriteRefusedError, match=r"doesn't allow fields \[8\]"):
        rules(records([row(3, 7, 8)], mergeFieldId=3))


def test_field_lists_limit_inserts() -> None:
    rules = WriteRules({TASKS: WriteAccess(insert=[6])})
    assert rules(records([row(6)]))
    assert not allows(rules, records([row(6), row(7)]))


def test_a_field_list_refuses_fields_it_cant_read() -> None:
    rules = WriteRules({TASKS: WriteAccess(insert=[7], update=[7])})
    for key in ("07", " 7", "+7", "8 "):
        request = records([{key: {"value": "x"}}])
        with pytest.raises(WriteRefusedError, match="can't be determined"):
            rules(request)


def test_an_upsert_needs_both_and_its_fields_in_both() -> None:
    both = WriteRules({TASKS: WriteAccess(insert=[6, 7], update=[6, 7])})
    assert both(records([row(6, 7)], mergeFieldId=6))
    narrower = WriteRules({TASKS: WriteAccess(insert=[6, 7], update=[7])})
    assert not allows(narrower, records([row(6, 7)], mergeFieldId=6))


def test_an_insert_grant_refuses_a_table_with_another_key_field() -> None:
    insert_only = WriteRules({TASKS: WriteAccess(insert=True)})
    assert insert_only(records([row(6)], key_field_id=3))
    for key_field_id in (6, None):
        with pytest.raises(WriteRefusedError, match="updating isn't allowed"):
            insert_only(records([row(6)], key_field_id=key_field_id))
    both = WriteRules({TASKS: WriteAccess(insert=True, update=True)})
    assert both(records([row(6)], key_field_id=6))


def test_a_merge_on_record_id_written_another_way_needs_both() -> None:
    update_only = WriteRules({TASKS: WriteAccess(update=True)})
    assert not allows(update_only, records([row(3, 6)], mergeFieldId="3"))


def test_true_allows_any_field_even_one_it_cant_read() -> None:
    rules = WriteRules({TASKS: WriteAccess(insert=True, update=True)})
    assert rules(records([{"07": {"value": "x"}}]))


def test_field_lists_dont_limit_deletes() -> None:
    assert allows(
        WriteRules({TASKS: WriteAccess(update=[7], delete=True)}),
        describe_write("DELETE", "/records", {"from": TASKS}),
    )
    assert not allows(
        WriteRules({TASKS: WriteAccess(update=[7])}),
        describe_write("DELETE", "/records", {"from": TASKS}),
    )


def test_other_writes_and_unknown_tables_are_refused() -> None:
    rules = WriteRules(
        {TASKS: WriteAccess(insert=True, update=True, delete=True, delete_files=True)}
    )
    with pytest.raises(WriteRefusedError, match="only record, file, and schema"):
        rules(describe_write("POST", "/apps", {}))
    with pytest.raises(WriteRefusedError, match="table can't be determined"):
        rules(describe_write("POST", "/records", {"data": [row(6)]}))


def test_refusals_never_repeat_values() -> None:
    rules = WriteRules({TASKS: WriteAccess(update=[7])})
    with pytest.raises(WriteRefusedError) as caught:
        rules(records([{"3": {"value": 1}, "8": {"value": "s3cret"}}], mergeFieldId=3))
    assert "s3cret" not in str(caught.value)


@pytest.mark.parametrize(
    "grant",
    [
        {"insert": "Status"},
        {"update": ["Status"]},
        {"update": [7, "Status"]},
        {"update": []},
        {"insert": ()},
        {"update": [True]},
        {"update": [0]},
        {"update": [-7]},
        {"update": 7},
        {"insert": None},
        {"delete": "yes"},
        {"delete": [7]},
        {"delete_files": 1},
    ],
)
def test_bad_grants_are_refused(grant: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError):
        WriteAccess(**grant)


def test_a_grant_cant_change_afterward() -> None:
    access = WriteAccess(update=[7])
    assert access.update == {7}
    with pytest.raises(AttributeError):
        access.update = True  # type: ignore[misc]  # pyright: ignore[reportAttributeAccessIssue]


def test_rules_cant_change_when_the_dictionary_does() -> None:
    tables = {TASKS: WriteAccess(insert=True)}
    rules = WriteRules(tables)
    tables[NOTES] = WriteAccess(insert=True)
    assert not allows(rules, describe_write("POST", "/records", {"to": NOTES, "data": [row(6)]}))


@pytest.mark.parametrize(
    "tables",
    [None, [TASKS], TASKS, {TASKS: True}, {TASKS: None}, {"": WriteAccess()}, {5: WriteAccess()}],
)
def test_bad_rules_are_refused(tables: Any) -> None:
    with pytest.raises(ConfigurationError):
        WriteRules(tables)


# Checking a policy


@pytest.mark.parametrize("answer", [False, None, 1, "yes", [1], object()])
def test_only_true_lets_a_write_through(answer: Any) -> None:
    with pytest.raises(WriteRefusedError, match="refused"):
        check_policy(lambda _: answer, records([row(6)]))


def test_a_policy_that_raises_refuses_the_write() -> None:
    def broken(_: WriteRequest) -> bool:
        raise KeyError("s3cret")

    with pytest.raises(WriteRefusedError, match="raised KeyError") as caught:
        check_policy(broken, records([row(6)]))
    assert "s3cret" not in str(caught.value)
    assert isinstance(caught.value.__cause__, KeyError)


def test_a_policy_can_give_its_own_reason() -> None:
    def closed(_: WriteRequest) -> bool:
        raise WriteRefusedError("Closed for the weekend.")

    with pytest.raises(WriteRefusedError, match="Closed for the weekend"):
        check_policy(closed, records([row(6)]))


def test_interrupts_arent_swallowed() -> None:
    def interrupted(_: WriteRequest) -> bool:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        check_policy(interrupted, records([row(6)]))


def test_all_of_needs_every_policy() -> None:
    yes = lambda _: True  # noqa: E731
    no = lambda _: False  # noqa: E731
    assert all_of(yes, yes)(records([row(6)])) is True
    with pytest.raises(WriteRefusedError):
        all_of(yes, no)(records([row(6)]))
    with pytest.raises(WriteRefusedError):
        all_of(no, yes)(records([row(6)]))


def test_all_of_stops_at_the_first_refusal_and_keeps_its_reason() -> None:
    calls: list[str] = []

    def later(_: WriteRequest) -> bool:
        calls.append("later")
        return True

    rules = WriteRules({NOTES: WriteAccess(insert=True)})
    with pytest.raises(WriteRefusedError, match="isn't listed"):
        all_of(rules, later)(records([row(6)]))
    assert calls == []


async def _async_policy(_: WriteRequest) -> bool:
    return True


@pytest.mark.parametrize("policies", [(), (None,), ("x",), (_async_policy,)])
def test_all_of_refuses_what_it_cant_use(policies: tuple[Any, ...]) -> None:
    with pytest.raises(ConfigurationError):
        all_of(*policies)


@pytest.mark.parametrize("policy", ["x", 5, {TASKS: WriteAccess()}, _async_policy])
def test_clients_refuse_a_policy_they_cant_use(policy: Any) -> None:
    with pytest.raises(ConfigurationError, match="write_policy"):
        Quickbase(realm="acme", token="t", write_policy=policy)


def test_read_only_and_a_policy_contradict_each_other() -> None:
    with pytest.raises(ConfigurationError, match="read_only or write_policy"):
        Quickbase(realm="acme", token="t", read_only=True, write_policy=lambda _: True)


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


@given(ROWS, EXTRA)
def test_a_field_list_never_lets_another_field_through(
    rows: list[dict[str, Any]], extra: dict[str, Any]
) -> None:
    rules = WriteRules({TASKS: WriteAccess(update=[7])})
    if allows(rules, records(rows, **extra)):
        assert {key for r in rows for key in r} <= {"3", "7"}


@given(ROWS, EXTRA)
def test_an_update_grant_never_lets_a_create_through(
    rows: list[dict[str, Any]], extra: dict[str, Any]
) -> None:
    rules = WriteRules({TASKS: WriteAccess(update=True)})
    if allows(rules, records(rows, **extra)):
        merge = extra.get("mergeFieldId", 3)
        assert type(merge) is int
        assert merge == 3
        # Every row names a record in the usual form, or the merge on Record ID# refuses it.
        assert "mergeFieldId" in extra or all("3" in r for r in rows)


@given(ROWS, EXTRA, st.sampled_from([3, 6, None]))
def test_an_insert_grant_never_lets_a_change_through(
    rows: list[dict[str, Any]], extra: dict[str, Any], key_field_id: int | None
) -> None:
    rules = WriteRules({TASKS: WriteAccess(insert=True)})
    if allows(rules, records(rows, key_field_id=key_field_id, **extra)):
        assert key_field_id == 3
        assert "mergeFieldId" not in extra
        assert not any("3" in r for r in rows)
