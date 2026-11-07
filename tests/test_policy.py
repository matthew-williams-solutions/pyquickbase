"""The policy's rules, ported from the old ReadRules and WriteRules tests, so that nothing loosened
by accident, plus what's new. No network access.

Where a test here is stricter than the one it came from, its docstring says so.
"""

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pyquickbase import (
    BoundPolicy,
    ConfigurationError,
    Decision,
    F,
    Policy,
    PolicyRefusedError,
    Quickbase,
    ReadRequest,
    UnknownFieldError,
    WriteRequest,
    allow,
    deny,
    fields,
    limit,
    tables,
)
from pyquickbase._read_request import describe_read
from pyquickbase._write_request import describe_write
from pyquickbase.errors import InvalidResponseError

from .policy_helpers import APP, NOTES, TASKS, bind

# Writes


def row(*field_ids: Any) -> dict[str, Any]:
    return {str(field_id): {"value": "x"} for field_id in field_ids}


def records(rows: list[Any], *, key_field_id: int | None = 3, **extra: Any) -> WriteRequest:
    body = {"to": TASKS, "data": rows, **extra}
    return describe_write("POST", "/records", body, key_field_id=key_field_id)


def schema(
    method: str, path: str, body: Any = None, *, table: str = TASKS, **kwargs: Any
) -> WriteRequest:
    params = {"tableId": table, "appId": APP}
    return describe_write(method, path, body, params=params, **kwargs)


def allows(policy: BoundPolicy, request: WriteRequest | ReadRequest) -> bool:
    return policy.decide(request).allowed


def refusal(policy: BoundPolicy, request: WriteRequest | ReadRequest) -> str:
    decision = policy.decide(request)
    assert not decision.allowed
    return decision.message()


INSERT = records([row(6)])
UPDATE = records([row(3, 6)], mergeFieldId=3)
UPSERT = records([row(6)], mergeFieldId=6)
DELETE = describe_write("DELETE", "/records", {"from": TASKS})
DELETE_FILE = describe_write("DELETE", f"/files/{TASKS}/5/13/2", None)


def test_a_table_no_rule_names_is_refused() -> None:
    policy = bind(
        allow.insert(NOTES, fields.all()),
        allow.update(NOTES, fields.all()),
        allow.delete(NOTES),
        allow.delete_file(NOTES),
    )
    for request in (INSERT, UPDATE, DELETE, DELETE_FILE):
        assert "No rule allows it" in refusal(policy, request)


def test_tables_are_found_by_name_or_id_and_unknown_ones_fail_to_bind() -> None:
    assert allows(bind(allow.insert("Tasks", fields.all())), INSERT)
    assert allows(bind(allow.insert("tasks", fields.all())), INSERT)
    assert allows(bind(allow.insert(TASKS, fields.all())), INSERT)
    with pytest.raises(ConfigurationError, match="no table 'BQTASKS'"):
        bind(allow.insert("BQTASKS", fields.all()))


def test_nothing_is_granted_by_default() -> None:
    policy = bind()
    for request in (INSERT, UPDATE, UPSERT, DELETE, DELETE_FILE):
        assert not allows(policy, request)


def test_each_action_is_granted_separately() -> None:
    cases: list[tuple[list[Any], set[str]]] = [
        ([allow.insert(TASKS, fields.all())], {"insert"}),
        ([allow.update(TASKS, fields.all())], {"update"}),
        (
            [allow.insert(TASKS, fields.all()), allow.update(TASKS, fields.all())],
            {"insert", "update", "upsert"},
        ),
        ([allow.delete(TASKS)], {"delete"}),
        ([allow.delete_file(TASKS)], {"delete_file"}),
    ]
    for rules, expected in cases:
        policy = bind(*rules)
        for request in (INSERT, UPDATE, UPSERT, DELETE, DELETE_FILE):
            assert allows(policy, request) == (request.action in expected), (rules, request)


def test_field_lists_limit_updates_by_label_or_id() -> None:
    policy = bind(allow.update(TASKS, ["Status", 12]))
    assert allows(policy, records([row(3, 7)], mergeFieldId=3))
    assert allows(policy, records([row(3, 7, 12)]))
    message = refusal(policy, records([row(3, 7, 8)], mergeFieldId=3))
    assert "updating Hours (8) in Tasks (bqtasks)" in message
    assert 'add "Hours" to an allow.update("Tasks", ...) rule' in message


def test_field_lists_limit_inserts() -> None:
    policy = bind(allow.insert(TASKS, ["Title"]))
    assert allows(policy, records([row(6)]))
    assert not allows(policy, records([row(6), row(7)]))


def test_fields_that_cant_be_read_are_refused_even_for_every_field() -> None:
    """Stricter than before: `WriteAccess(insert=True)` let a key such as "07" through."""
    for rules in (
        [allow.insert(TASKS, [7]), allow.update(TASKS, [7])],
        [
            allow.insert(TASKS, fields.all()),
            allow.update(TASKS, fields.all()),
        ],
    ):
        policy = bind(*rules)
        for key in ("07", " 7", "+7", "8 "):
            assert "can't be determined" in refusal(policy, records([{key: {"value": "x"}}]))


def test_an_upsert_needs_both_and_its_fields_in_both() -> None:
    both = bind(allow.insert(TASKS, [6, 7]), allow.update(TASKS, [6, 7]))
    assert allows(both, records([row(6, 7)], mergeFieldId=6))
    narrower = bind(allow.insert(TASKS, [6, 7]), allow.update(TASKS, [7]))
    assert not allows(narrower, records([row(6, 7)], mergeFieldId=6))


def test_an_insert_grant_refuses_a_table_with_another_key_field() -> None:
    insert_only = bind(allow.insert(TASKS, fields.all()))
    assert allows(insert_only, records([row(6)], key_field_id=3))
    for key_field_id in (6, None):
        assert "updating" in refusal(insert_only, records([row(6)], key_field_id=key_field_id))
    both = bind(allow.insert(TASKS, fields.all()), allow.update(TASKS, fields.all()))
    assert allows(both, records([row(6)], key_field_id=6))


def test_a_merge_on_record_id_written_another_way_needs_both() -> None:
    update_only = bind(allow.update(TASKS, fields.all()))
    assert not allows(update_only, records([row(3, 6)], mergeFieldId="3"))


def test_field_lists_dont_limit_deletes() -> None:
    assert allows(bind(allow.update(TASKS, [7]), allow.delete(TASKS)), DELETE)
    assert not allows(bind(allow.update(TASKS, [7])), DELETE)


def test_other_writes_and_unknown_tables_are_refused() -> None:
    policy = bind(
        allow.insert(TASKS, fields.all()),
        allow.update(TASKS, fields.all()),
        allow.delete(TASKS),
        allow.delete_file(TASKS),
    )
    assert "Only record, file, and schema" in refusal(policy, describe_write("POST", "/apps", {}))
    assert "table can't be determined" in refusal(
        policy, describe_write("POST", "/records", {"data": [row(6)]})
    )
    other = describe_write("POST", "/records", {"to": "bqother", "data": [row(6)]})
    assert "isn't one of app bqapp's tables" in refusal(policy, other)


def test_refusals_never_repeat_values() -> None:
    policy = bind(allow.update(TASKS, [7]))
    message = refusal(
        policy, records([{"3": {"value": 1}, "8": {"value": "s3cret"}}], mergeFieldId=3)
    )
    assert "s3cret" not in message


def test_writing_a_field_needs_no_read_access() -> None:
    policy = bind(allow.update(TASKS, ["Hours"]))
    assert allows(policy, records([row(3, 8)], mergeFieldId=3))
    assert policy.visible(TASKS, 8)
    assert not policy.readable(TASKS, 8)


def test_a_deny_always_wins() -> None:
    policy = bind(
        allow.read(tables.all()),
        allow.update(TASKS, fields.all()),
        deny.update(TASKS, ["Hours"]),
    )
    assert allows(policy, records([row(3, 7)], mergeFieldId=3))
    decision = policy.decide(records([row(3, 7, 8)], mergeFieldId=3))
    assert not decision.allowed
    assert decision.rule == "deny.update('bqtasks', ['Hours'])"
    assert decision.hint is None


# Schema


@pytest.mark.parametrize(
    ("rule", "request_args", "allowed"),
    [
        (allow.schema(TASKS, "change_table"), ("POST", f"/tables/{TASKS}", {}), True),
        (allow.schema(TASKS, "add_field"), ("POST", f"/tables/{TASKS}", {}), False),
        (allow.schema(TASKS, "delete_table"), ("DELETE", f"/tables/{TASKS}", None), True),
        (allow.schema(TASKS, "change_table"), ("DELETE", f"/tables/{TASKS}", None), False),
        (allow.schema(TASKS, "add_field"), ("POST", "/fields", {"fieldType": "text"}), True),
        (allow.schema(TASKS, "add_field", field_types=["date"]), ("POST", "/fields",
                                                                  {"fieldType": "text"}), False),
        (allow.schema(TASKS, "add_field"), ("POST", "/fields", {}), False),
        (allow.schema(TASKS, "change_field", fields=[6]), ("POST", "/fields/6", {"label": "y"}),
         True),
        (allow.schema(TASKS, "change_field", fields=[7]), ("POST", "/fields/6", {"label": "y"}),
         False),
        (allow.schema(TASKS, "delete_field"), ("DELETE", "/fields", {"fieldIds": [6]}), True),
        (allow.schema(TASKS, "delete_field", fields=[6]), ("DELETE", "/fields",
                                                           {"fieldIds": [6, 7]}), False),
        (allow.schema(TASKS, "delete_field", fields=[6]), ("DELETE", "/fields",
                                                           {"fieldIds": "6"}), False),
    ],
)  # fmt: skip
def test_each_schema_change_is_granted_separately(
    rule: Any, request_args: tuple[str, str, Any], allowed: bool
) -> None:
    assert allows(bind(rule), schema(*request_args)) is allowed


def test_schema_changes_need_a_schema_grant() -> None:
    policy = bind(allow.insert(TASKS, fields.all()), allow.delete(TASKS))
    assert "adding a field to Tasks" in refusal(
        policy, schema("POST", "/fields", {"label": "x", "fieldType": "text"})
    )
    assert "add allow.create_tables()" in refusal(policy, schema("POST", "/tables", {"name": "x"}))


def test_formulas_need_their_own_grant_and_cant_make_links() -> None:
    formula = {"fieldType": "numeric", "properties": {"formula": "1"}}
    assert not allows(bind(allow.schema(TASKS, "add_field")), schema("POST", "/fields", formula))
    policy = bind(allow.schema(TASKS, "add_field", "change_field", "formula"))
    assert allows(policy, schema("POST", "/fields", formula))
    assert allows(policy, schema("POST", "/fields/6", {"properties": {"formula": "2"}}))
    for unsafe in ("url", "rich-text"):
        body = {"fieldType": unsafe, "properties": {"formula": '"x"'}}
        assert "acts as them" in refusal(policy, schema("POST", "/fields", body))
    assert allows(policy, schema("POST", "/fields", {"fieldType": "url"}))


def test_relationships_are_granted_on_both_tables_where_they_land() -> None:
    link = schema("POST", f"/tables/{TASKS}/relationship", {"parentTableId": NOTES})
    lookup = schema("POST", f"/tables/{TASKS}/relationship/7", {"lookupFieldIds": [6]},
                    parent_table_id=NOTES)  # fmt: skip
    summary = schema("POST", f"/tables/{TASKS}/relationship/7", {"summaryFields": [{}]},
                     parent_table_id=NOTES)  # fmt: skip
    unlink = schema("DELETE", f"/tables/{TASKS}/relationship/7", parent_table_id=NOTES)
    only_child = bind(allow.schema(TASKS, "link"))
    message = refusal(only_child, link)
    assert "Notes (bqnotes)" in message
    both = bind(allow.schema([TASKS, NOTES], "link"))
    assert allows(both, link)
    assert not allows(both, lookup)
    assert not allows(both, summary)
    assert not allows(both, unlink)
    granted = bind(
        allow.schema(TASKS, "link", "unlink", "lookup", "delete_field"),
        allow.schema(NOTES, "link", "unlink", "summary", "delete_field"),
    )
    for request in (link, lookup, summary, unlink):
        assert allows(granted, request), request.action
    # A lookup is granted on the child, and a summary on the parent.
    assert allows(bind(allow.schema(TASKS, "lookup")), lookup)
    assert not allows(bind(allow.schema(NOTES, "lookup")), lookup)
    assert allows(bind(allow.schema(NOTES, "summary")), summary)
    assert not allows(bind(allow.schema(TASKS, "summary")), summary)
    unknown_parent = schema("POST", f"/tables/{TASKS}/relationship/7", {"summaryFields": [{}]})
    assert "parent table can't be determined" in refusal(granted, unknown_parent)


def test_unlinking_needs_delete_field_where_its_lookups_and_summaries_are() -> None:
    from .policy_helpers import link as relationship

    links = [relationship(1, NOTES, TASKS, 9, lookups=[12], summaries=[7])]
    unlink = schema("DELETE", f"/tables/{TASKS}/relationship/1", parent_table_id=NOTES)
    narrow = bind(
        allow.schema([TASKS, NOTES], "unlink"),
        allow.schema(TASKS, "delete_field", fields=[12]),
        relationships=links,
    )
    assert "deleting Kind (7) in Notes" in refusal(narrow, unlink)
    granted = bind(
        allow.schema([TASKS, NOTES], "unlink"),
        allow.schema(TASKS, "delete_field", fields=[12]),
        allow.schema(NOTES, "delete_field", fields=["Kind"]),
        relationships=links,
    )
    assert allows(granted, unlink)


def test_created_tables_get_their_own_rules_in_a_new_policy() -> None:
    policy = bind(allow.create_tables(), allow.schema(tables.created(), "add_field"))
    assert allows(policy, schema("POST", "/tables", {"name": "x"}))
    assert not allows(policy, describe_write("POST", "/tables", {}, params={"appId": "other"}))
    add = schema("POST", "/fields", {"fieldType": "text"}, table="bqnew")
    assert not allows(policy, add)
    created = policy.with_created("bqnew", "New")
    assert allows(created, add)
    assert not allows(policy, add), "the old policy is unchanged"
    assert created.created == ("bqnew",)
    assert created.hash != policy.hash
    assert not allows(bind(allow.schema(tables.created(), "add_field")), add)


# Reads


def query(**body: Any) -> ReadRequest:
    found = describe_read("POST", "/records/query", None, {"from": TASKS, "sortBy": [], **body})
    assert found is not None
    return found


def paged(**body: Any) -> ReadRequest:
    return query(options={"top": 10, "skip": 0}, **body)


def report(report_type: str | None = "table", report_id: str = "7") -> ReadRequest:
    found = describe_read(
        "POST",
        f"/reports/{report_id}/run",
        {"tableId": TASKS, "top": 10, "skip": 0},
        None,
        report_type=report_type,
    )
    assert found is not None
    return found


def reads(**options: Any) -> BoundPolicy:
    extra = options.pop("rules", ())
    return bind(
        allow.read(TASKS, options.pop("fields", [6, 7]), **options),
        *extra,
        reports={
            TASKS: [
                {"id": "7", "name": "Open", "type": "table"},
                {"id": "8", "name": "Totals", "type": "summary"},
            ]
        },
    )


def test_allowed_queries_pass() -> None:
    allowed = paged(select=[3, 6, 7], where="{7.EX.'x'}", sortBy=[{"fieldId": 6, "order": "ASC"}])
    assert allows(reads(), allowed)


def test_record_id_is_allowed_wherever_anything_is_readable() -> None:
    request = paged(select=[3], where="{3.GT.'0'}", sortBy=[{"fieldId": 3, "order": "ASC"}])
    assert allows(reads(fields=["Title"]), request)
    assert not allows(bind(allow.insert(TASKS, fields.all())), request)


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ({"select": [3, 9]}, r"reading Notes \(9\)"),
        ({"select": [3], "where": "{9.EX.'x'}"}, r"filtering on Notes \(9\)"),
        ({"select": [3], "sortBy": [{"fieldId": 9, "order": "ASC"}]}, r"sorting by Notes \(9\)"),
        ({"select": [3], "where": "{7.EX.'_FID_9'}"}, "filters on can't be determined"),
        ({}, "returns can't be determined"),
    ],
)
def test_fields_outside_the_rules_are_refused(body: dict[str, Any], match: str) -> None:
    with pytest.raises(PolicyRefusedError, match=match):
        raise PolicyRefusedError(reads().decide(paged(**body)))


def test_filter_and_sort_default_to_the_read_fields_and_can_differ() -> None:
    narrow = reads(filter=[7], sort=["Title"])
    assert allows(narrow, paged(select=[6], where="{7.EX.'x'}"))
    assert "filtering" in refusal(narrow, paged(select=[6], where="{6.EX.'x'}"))
    assert "sorting" in refusal(narrow, paged(select=[6], sortBy=[{"fieldId": 7, "order": "ASC"}]))
    # A field may be filtered on without being returned.
    assert allows(reads(fields=[6], filter=[9]), paged(select=[6], where="{9.EX.'x'}"))
    assert allows(
        bind(allow.read(TASKS, [6]), allow.filter(TASKS, [9])),
        paged(select=[6], where="{9.EX.'x'}"),
    )


def test_every_field_includes_ones_added_after_binding() -> None:
    assert allows(reads(fields=fields.all()), paged(select=[3, 99], where="{98.EX.'x'}"))


def test_the_window_is_limited_counting_skip() -> None:
    limited = reads(max_paging_depth=50)
    assert allows(limited, query(select=[6], options={"top": 50, "skip": 0}))
    assert "at most 50 records" in refusal(
        limited, query(select=[6], options={"top": 51, "skip": 0})
    )
    assert "at most 50 records" in refusal(
        limited, query(select=[6], options={"top": 10, "skip": 45})
    )
    assert "get all of them" in refusal(limited, query(select=[6]))
    assert allows(reads(max_paging_depth=None), query(select=[6]))


def test_the_highest_record_limit_of_the_rules_that_read_a_table_applies() -> None:
    policy = bind(allow.read(tables.all()), allow.read(TASKS, max_paging_depth=500))
    assert policy.max_records(TASKS) == 500
    assert policy.max_records(NOTES) == 100


def test_summary_pages_have_their_own_limit() -> None:
    body = {"from": TASKS, "select": [6], "sortBy": [], "options": {"top": 1000, "skip": 0}}
    page = describe_read("POST", "/records/query", None, body, summarizing=True)
    assert page is not None
    assert allows(reads(max_paging_depth=10), page)
    assert "max_records_scanned" in refusal(reads(max_records_scanned=999), page)


def test_tables_no_rule_reads_are_refused() -> None:
    other = describe_read("GET", "/fields", {"tableId": NOTES}, None)
    assert other is not None
    assert "Nothing in the table is allowed" in refusal(reads(), other)


def test_reports_must_be_listed_by_name_or_id() -> None:
    by_id = [allow.report(TASKS, ["7"])]
    assert allows(reads(rules=by_id), report())
    assert allows(reads(rules=[allow.report(TASKS, [7])]), report())
    assert allows(reads(rules=[allow.report(TASKS, ["Open"])]), report())
    assert "isn't one an allow.report() rule lists" in refusal(
        reads(rules=[allow.report(TASKS, ["Totals"])]), report()
    )
    assert "add allow.report" in refusal(reads(), report())
    assert allows(reads(rules=[allow.report(TASKS, ["Totals"])]), report("summary", "8"))
    assert "at most 5" in refusal(reads(rules=by_id, max_paging_depth=5), report())
    with pytest.raises(ConfigurationError, match="no report 'Gone'"):
        reads(rules=[allow.report(TASKS, ["Gone"])])


def test_reports_through_request_are_refused() -> None:
    assert "can't be run through request()" in refusal(
        reads(rules=[allow.report(TASKS, ["7"])]), report(None)
    )


def test_files_need_download_even_when_the_field_is_read() -> None:
    download = describe_read("GET", f"/files/{TASKS}/1/13/0", None, None)
    assert download is not None
    assert "downloading files from" in refusal(reads(fields=[13]), download)
    assert allows(reads(rules=[allow.download(TASKS, ["Attachment"])]), download)


@pytest.mark.parametrize(
    ("method", "path", "body", "match"),
    [
        ("POST", "/formula/run", {"formula": "1", "from": TASKS}, "running a formula on"),
        ("GET", "/apps/bqapp", None, "not the app's details"),
        ("GET", "/apps/bqapp/roles", None, "not the app's roles"),
        ("GET", "/apps/bqapp/events", None, "Only records"),
    ],
)
def test_formulas_and_app_metadata_are_refused(
    method: str, path: str, body: Any, match: str
) -> None:
    found = describe_read(method, path, None, body)
    assert found is not None
    assert match in refusal(reads(fields=fields.all()), found)
    # Policy.read_only() reads everything, as policy=Policy.read_only() did.
    assert allows(Policy.read_only().bind_offline(), found)


def test_a_delete_is_held_to_the_filter_rules() -> None:
    delete = describe_read("DELETE", "/records", None, {"from": TASKS, "where": "{9.EX.'x'}"})
    assert delete is not None
    assert "filtering on Notes (9)" in refusal(reads(), delete)
    assert allows(reads(filter=[9]), delete)


def test_read_refusals_never_repeat_values() -> None:
    assert "s3cret" not in refusal(reads(), paged(select=[3], where="{9.EX.'s3cret'}"))


# Rules of your own


class Weekend:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def decide(self, request: ReadRequest | WriteRequest) -> Decision | None:
        self.seen.append(request.action)
        if isinstance(request, WriteRequest):
            return Decision(allowed=False, verb="update", reason="Closed for the weekend")
        return None

    def __repr__(self) -> str:
        return "Weekend()"


def test_a_rule_of_your_own_can_only_refuse() -> None:
    weekend = Weekend()
    policy = bind(allow.read(TASKS), allow.update(TASKS, fields.all()), weekend)
    assert "Closed for the weekend" in refusal(policy, UPDATE)
    assert allows(policy, paged(select=[6]))
    # It isn't asked about what the built-in rules already refuse.
    weekend.seen.clear()
    assert not allows(policy, INSERT)
    assert weekend.seen == []

    # And it can't grant.
    class Yes:
        def decide(self, request: Any) -> Decision:
            return Decision(allowed=True, verb="insert")

    assert not allows(bind(Yes()), INSERT)


def test_a_rule_of_your_own_must_be_passed_again_to_load() -> None:
    policy = bind(allow.read(TASKS), Weekend())
    with pytest.raises(ConfigurationError, match="Pass the same ones"):
        BoundPolicy.from_json(policy.to_json())
    assert BoundPolicy.from_json(policy.to_json(), rules=[Weekend()]) == policy


# Configuring


@pytest.mark.parametrize(
    "build",
    [
        lambda: allow.read(TASKS, []),
        lambda: allow.read(TASKS, [0]),
        lambda: allow.read(TASKS, [True]),
        lambda: allow.read(TASKS, max_paging_depth=0),
        lambda: allow.read(TASKS, max_paging_depth=1.5),
        lambda: allow.read(TASKS, max_records_scanned=True),
        lambda: allow.read(TASKS, user_emails="yes"),
        lambda: allow.read([]),
        lambda: allow.report(TASKS, "Open"),
        lambda: allow.report(TASKS, []),
        lambda: allow.report(tables.all(), ["Open"]),
        lambda: allow.schema(TASKS),
        lambda: allow.schema(TASKS, "rename"),
        lambda: allow.schema(TASKS, "add_field", field_types="text"),
        lambda: allow.schema(TASKS, "add_field", field_types=[]),
        lambda: allow.schema(TASKS, "add_field", field_types=[""]),
        lambda: fields.classed(),
        lambda: fields.labeled_like(""),
        lambda: limit.rows(TASKS, ""),
        lambda: Policy(rules=[lambda request: True]),
        lambda: Policy(rules=allow.read(TASKS)),
        lambda: Policy(new_fields="secret"),
        lambda: Policy(classes={"pay": ["Hourly Rate"]}),
        lambda: Policy(classes={"pay": []}),
        lambda: Policy(classes={"pay": [fields.new()]}),
        lambda: Policy(app="bq-app"),
    ],
)
def test_bad_policies_are_a_configuration_error(build: Any) -> None:
    with pytest.raises(ConfigurationError):
        build()


def test_binding_reports_every_problem_at_once() -> None:
    with pytest.raises(ConfigurationError) as caught:
        bind(
            allow.read("Jobs"),
            allow.update(TASKS, ["Labor Cost", "Status", 77]),
            allow.update(tables.all(), ["Title"]),
            deny.all(fields.classed("pay")),
            classes={"internal": ["Tasks.Sync ID"]},
        )
    message = str(caught.value)
    for problem in (
        "no table 'Jobs'",
        "no field 'Labor Cost'",
        "no field 77",
        "needs tables by name or ID",
        "there's no class 'pay'",
        "no table and field match 'Tasks.Sync ID'",
    ):
        assert problem in message
    assert "no field 'Status'" not in message


def test_a_policy_for_another_app_wont_bind() -> None:
    from pyquickbase._bound import bind_snapshot

    from .policy_helpers import snapshot

    with pytest.raises(ConfigurationError, match="for app bqother"):
        bind_snapshot(Policy(app="bqother"), snapshot())


def test_a_policy_that_names_tables_must_be_bound_before_a_client_takes_it() -> None:
    with pytest.raises(ConfigurationError, match="bind"):
        Quickbase(realm="acme", token="t", policy=Policy(rules=[allow.read(TASKS)]))
    with pytest.raises(ConfigurationError, match="policy takes"):
        Quickbase(realm="acme", token="t", policy="read_only")  # pyright: ignore[reportArgumentType]
    with Quickbase(realm="acme", token="t", policy=Policy.read_only()) as qb:
        assert qb.policy is not None
        assert not qb.policy.decide(INSERT).allowed


# Trimming


def trim(policy: BoundPolicy, request: ReadRequest | None, payload: Any, **kw: Any) -> Any:
    method = kw.get("method", request.method if request else "POST")
    path = kw.get("path", request.path if request else "/records")
    return policy.trim(request, method, path, payload)


def test_tables_are_left_out_unless_something_in_them_is_allowed() -> None:
    request = describe_read("GET", "/tables", {"appId": APP}, None)
    payload = [
        {"id": TASKS, "name": "Tasks", "keyFieldId": 3, "spaceUsed": "1 MB", "alias": "_DBID"},
        {"id": NOTES, "name": "Notes"},
        {"id": "bqsecret", "name": "Salaries"},
    ]
    assert trim(reads(), request, payload) == [{"id": TASKS, "name": "Tasks", "keyFieldId": 3}]


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
        {"id": 8, "label": "Hours", "fieldType": "text", "appearsByDefault": True},
        {"id": 9, "label": "Notes", "fieldType": "text"},
        {"id": 13, "label": "Attachment", "fieldType": "file"},
    ]
    policy = reads(
        fields=[6],
        filter=[7],
        rules=[allow.update(TASKS, ["Hours"]), allow.download(TASKS, ["Attachment"])],
    )
    found = trim(policy, request, payload)
    assert [item["id"] for item in found] == [3, 6, 7, 8, 13]
    assert found[1] == {
        "id": 6,
        "label": "Title",
        "fieldType": "text",
        "appearsByDefault": True,
        "fieldHelp": "The task's name",
        "properties": {"choices": ["a"]},
    }
    assert [item["appearsByDefault"] for item in found] == [True, True, False, False, False]


def test_reports_keep_listed_ones_without_their_filters() -> None:
    request = describe_read("GET", "/reports", {"tableId": TASKS}, None)
    payload = [
        {"id": "7", "name": "Open", "type": "table", "query": {"filter": "{9.EX.'s3cret'}"}},
        {"id": "8", "name": "Totals", "type": "summary"},
        {"id": "9", "name": "Hidden", "type": "table"},
    ]
    found = trim(reads(rules=[allow.report(TASKS, ["Open", "Totals"])]), request, payload)
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
    found = trim(reads(), request, payload)
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


def page(columns: list[tuple[int, str]], values: dict[int, Any]) -> dict[str, Any]:
    return {
        "fields": [{"id": fid, "label": f"F{fid}", "type": kind} for fid, kind in columns],
        "data": [{str(fid): {"value": value} for fid, value in values.items()}],
        "metadata": {"totalRecords": 1, "numRecords": 1, "skip": 0},
    }


def test_records_lose_unreadable_columns_emails_and_uploaders() -> None:
    payload = page(
        [(3, "recordid"), (10, "user"), (12, "multiuser"), (9, "text"), (13, "file")],
        {3: 1, 10: USER, 12: [USER, USER], 9: "s3cret", 13: FILE},
    )
    found = trim(reads(fields=[10, 12, 13]), report(), payload)
    assert [item["id"] for item in found["fields"]] == [3, 10, 12, 13]
    values = found["data"][0]
    assert set(values) == {"3", "10", "12", "13"}
    assert values["10"]["value"] == {"id": "1.ab", "name": "Ann"}
    assert values["12"]["value"] == [{"id": "1.ab", "name": "Ann"}] * 2
    assert values["13"]["value"] == {
        "url": "/files/x",
        "versions": [{"versionNumber": 1, "fileName": "a.txt", "uploaded": "2026"}],
    }


def test_user_emails_can_be_kept() -> None:
    payload = page([(6, "user")], {6: USER})
    assert (
        trim(reads(user_emails=True), paged(select=[6]), payload)["data"][0]["6"]["value"] == USER
    )


def test_users_are_recognized_by_shape_whatever_the_type_says() -> None:
    payload = page([(6, "text")], {6: USER})
    assert trim(reads(), paged(select=[6]), payload)["data"][0]["6"]["value"] == {
        "id": "1.ab",
        "name": "Ann",
    }


def test_an_allowed_summary_report_keeps_its_columns() -> None:
    # Column IDs here are positions, so 9 isn't field 9.
    payload = page([(2, "text"), (9, "numeric")], {2: "Open", 9: 5})
    policy = reads(rules=[allow.report(TASKS, ["Totals"])])
    found = trim(policy, report("summary", "8"), payload)
    assert [item["id"] for item in found["fields"]] == [2, 9]


def test_a_deleted_file_versions_details_are_dropped() -> None:
    found = trim(reads(), None, {"creator": USER}, method="DELETE", path=f"/files/{TASKS}/1/13/1")
    assert found is None


def test_a_response_of_the_wrong_shape_is_an_error_not_passed_through() -> None:
    with pytest.raises(InvalidResponseError, match="couldn't check it"):
        trim(reads(), paged(select=[6]), {"data": "s3cret"})


def test_a_hidden_field_acts_as_if_it_didnt_exist() -> None:
    policy = reads(fields=[6])
    assert not policy.visible(TASKS, 9)
    with pytest.raises(UnknownFieldError):
        policy.allows("read", "Tasks", field="Salary")


# Randomized checks

FIELD_KEYS = st.one_of(
    st.sampled_from(["3", "6", "7", "8", "03", "07", " 7", "7 ", "+7", "\u0667", "0"]),
    st.text(max_size=4),
)
ROWS = st.lists(st.dictionaries(FIELD_KEYS, st.just({"value": "x"}), max_size=4), max_size=4)
EXTRA = st.dictionaries(
    st.sampled_from(["mergeFieldId", "fieldsToReturn"]),
    st.one_of(st.none(), st.integers(), st.text(max_size=3), st.just([3])),
)
UPDATE_SEVEN = bind(allow.update(TASKS, [7]))
UPDATE_ALL = bind(allow.update(TASKS, fields.all()))
INSERT_ALL = bind(allow.insert(TASKS, fields.all()))
READ_SOME = reads()


@given(ROWS, EXTRA)
def test_a_field_list_never_lets_another_field_through(
    rows: list[dict[str, Any]], extra: dict[str, Any]
) -> None:
    if allows(UPDATE_SEVEN, records(rows, **extra)):
        assert {key for r in rows for key in r} <= {"3", "7"}


@given(ROWS, EXTRA)
def test_an_update_grant_never_lets_a_create_through(
    rows: list[dict[str, Any]], extra: dict[str, Any]
) -> None:
    if allows(UPDATE_ALL, records(rows, **extra)):
        merge = extra.get("mergeFieldId", 3)
        assert type(merge) is int
        assert merge == 3
        assert "mergeFieldId" in extra or all("3" in r for r in rows)


@given(ROWS, EXTRA, st.sampled_from([3, 6, None]))
def test_an_insert_grant_never_lets_a_change_through(
    rows: list[dict[str, Any]], extra: dict[str, Any], key_field_id: int | None
) -> None:
    if allows(INSERT_ALL, records(rows, key_field_id=key_field_id, **extra)):
        assert key_field_id == 3
        assert "mergeFieldId" not in extra
        assert not any("3" in r for r in rows)


@given(st.lists(st.integers(min_value=1, max_value=30), max_size=10))
def test_trimmed_records_only_hold_readable_fields(ids: list[int]) -> None:
    allowed = {3, 6, 7}
    payload = page([(fid, "text") for fid in ids], dict.fromkeys(ids, "x"))
    found = trim(READ_SOME, paged(select=[6]), payload)
    assert {int(key) for key in found["data"][0]} <= allowed
    assert {item["id"] for item in found["fields"]} <= allowed


def test_row_limits_bind_by_label() -> None:
    policy = bind(allow.read(TASKS), limit.rows(TASKS, F("Status") == "Open"))
    assert policy.row_limit(TASKS) == "{7.EX.'Open'}"
    assert policy.filterable(TASKS, 7)
    with pytest.raises(ConfigurationError, match="Missing"):
        bind(limit.rows(TASKS, F("Missing") == 1))


def test_an_empty_formula_on_a_plain_field_is_left_out() -> None:
    request = describe_read("GET", "/fields", {"tableId": TASKS}, None)
    payload = [{"id": 6, "label": "Title", "fieldType": "text", "properties": {"formula": ""}}]
    assert trim(reads(), request, payload) == [
        {"id": 6, "label": "Title", "fieldType": "text", "properties": {}}
    ]
