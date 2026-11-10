"""What a policy does when a request is unclear, and what it says about itself. No network access.

A policy refuses what it can't work out, so each test here is a case where allowing by mistake
would pass unnoticed.
"""

from typing import Any

import pytest

from pyquickbase import (
    BoundPolicy,
    ConfigurationError,
    F,
    Policy,
    PolicyRefusedError,
    ReadRequest,
    WriteRequest,
    allow,
    deny,
    fields,
    limit,
    tables,
)
from pyquickbase._policy import FieldSelector
from pyquickbase._read_request import describe_read
from pyquickbase._write_request import describe_write

from .policy_helpers import APP, NOTE_FIELDS, NOTES, TASK_FIELDS, TASKS, bind, field, link


def read(action: Any, table: str | None = TASKS, **extra: Any) -> ReadRequest:
    return ReadRequest(action=action, table_id=table, method="GET", path="/x", **extra)


def write(action: Any, table: str | None = TASKS, **extra: Any) -> WriteRequest:
    settings = {"field_ids": None, "row_count": None, **extra}
    return WriteRequest(action=action, table_id=table, method="POST", path="/x", **settings)


def query(where: str, *selected: int) -> ReadRequest | None:
    body = {"from": TASKS, "select": list(selected), "where": where, "sortBy": []}
    return describe_read("POST", "/records/query", None, {**body, "options": {"top": 10}})


READS_TITLE = allow.read(TASKS, [6])


@pytest.mark.parametrize(
    ("policy", "request_", "reason"),
    [
        (bind(allow.read(tables.all())), read("query", None), "table can't be determined"),
        (bind(allow.delete(TASKS)), read("delete", filter_field_ids=None), "can't be determined"),
        (bind(allow.download(TASKS, [13])), read("file", field_ids=frozenset()), "file field"),
        (bind(allow.read(TASKS)), read("query_plan"), "isn't a read the policy knows"),
        (bind(allow.read(tables.all())), read("app", None), "not the app's details"),
        (bind(allow.read(tables.all())), read("roles", None), "not the app's roles"),
        (bind(allow.read(tables.all())), read("other", None), "can't be run through request"),
        (bind(allow.insert(TASKS, [6])), write("insert", None), "table can't be determined"),
        (bind(allow.schema(TASKS, "update_field")), write("update_field"), "field can't be"),
    ],
    ids=["no-table", "delete-filter", "file-field", "unknown-action", "app", "roles", "other",
         "write-no-table", "update-field"],
)  # fmt: skip
def test_what_cant_be_worked_out_is_refused(
    policy: BoundPolicy, request_: Any, reason: str
) -> None:
    decision = policy.decide(request_)
    assert not decision.allowed
    assert reason in decision.reason


@pytest.mark.parametrize("action", ["app", "roles", "other"])
def test_read_only_covers_what_a_field_rule_doesnt(action: Any) -> None:
    assert Policy.read_only().bind_offline().decide(read(action, None)).allowed


def test_a_relationship_change_that_adds_nothing_needs_the_lookup_grant() -> None:
    decision = bind().decide(write("update_relationship", parent_table_id=NOTES))
    assert not decision.allowed
    assert "adds no lookup" in decision.reason


@pytest.mark.parametrize(
    ("rule", "request_"),
    [
        (deny.delete(TASKS), describe_write("DELETE", "/records", {"from": TASKS})),
        (deny.report(TASKS), read("report", report_id="1", limit=1, skip=0)),
    ],
    ids=["delete", "report"],
)
def test_a_deny_beats_an_allow_for_deletes_and_reports(rule: Any, request_: Any) -> None:
    open_rules = [allow.delete(TASKS), allow.report(TASKS)]
    assert bind(*open_rules).decide(request_).allowed
    decision = bind(*open_rules, rule).decide(request_)
    assert not decision.allowed
    assert decision.rule == rule.text


def test_a_rule_for_other_reports_doesnt_allow_this_one() -> None:
    reports = {TASKS: [{"id": "1", "name": "Open", "type": "table"},
                       {"id": "2", "name": "Closed", "type": "table"}]}  # fmt: skip
    policy = bind(allow.report(TASKS, ["Open"]), allow.delete(TASKS), reports=reports)
    assert policy.decide(read("report", report_id="1", limit=1, skip=0)).allowed
    assert not policy.decide(read("report", report_id="2", limit=1, skip=0)).allowed


def test_a_row_limits_own_field_can_be_filtered_on_without_a_grant() -> None:
    policy = bind(READS_TITLE, limit.rows(TASKS, F("Status") == "Open"))
    decision = policy.decide(query("{7.EX.'Open'}", 6))
    assert decision.allowed
    assert decision.layer == "rows"
    assert not policy.decide(query("{8.GT.'1'}", 6)).allowed


def test_a_lookup_from_a_row_limited_parent_is_refused_by_the_policy() -> None:
    rules = [allow.read(tables.all()), allow.schema(TASKS, "lookup")]
    links = [link(9, NOTES, TASKS, 12)]
    assert bind(*rules, relationships=links).allows("lookup", TASKS, source=(NOTES, "Text")).allowed
    limited = bind(*rules, limit.rows(NOTES, F("Kind") == "Public"), relationships=links)
    assert not limited.allows("lookup", TASKS, source=(NOTES, "Text")).allowed
    assert "a lookup would show its records anywhere" in limited.explain(
        "lookup", TASKS, source=(NOTES, "Text")
    )


def test_a_lookup_of_a_hidden_parent_field_is_refused_by_the_policy() -> None:
    policy = bind(allow.read(NOTES, ["Kind"]), allow.schema(TASKS, "lookup"))
    assert policy.allows("lookup", TASKS, source=(NOTES, "Kind")).allowed
    assert not policy.allows("lookup", TASKS, source=(NOTES, "Text")).allowed


def test_explain_names_the_row_limit_and_tags() -> None:
    rows = limit.rows(TASKS, F("Status") == "Open")
    policy = bind(allow.read(tables.all()), rows, tags={"pay": [("Tasks", "Hours")]})
    text = policy.explain("read", TASKS, field=8)
    assert rows.text in text
    assert "Tags: pay." in text


def test_what_a_plan_says_about_a_new_field() -> None:
    hidden = bind(allow.read(TASKS), new_fields="hidden")
    assert hidden.new_field_effects(TASKS)[0].startswith("It's hidden, as new_fields is 'hidden'")
    named = bind(READS_TITLE)
    assert (
        named.new_field_effects(TASKS)[0]
        == "No rule reads it, so it's hidden until a rule names it."
    )
    assert bind(allow.read(TASKS)).new_field_effects("bqmade") == (
        "bqmade is new, so its fields follow the rules for tables.created().",
    )


def test_an_unknown_table_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="doesn't know table 'Nope'"):
        bind(allow.read(TASKS)).offers("Nope")


def test_an_offline_policy_treats_any_table_as_a_name() -> None:
    assert Policy.read_only().bind_offline().decide(read("query", "bqanything", limit=1)).allowed


def test_a_new_table_gets_the_offers_for_created_tables() -> None:
    assert "create_field" in bind(allow.schema(tables.created(), "create_field")).created_offers
    assert not bind(allow.schema(TASKS, "create_field")).created_offers


def test_tables_with_a_schema_grant_are_listed() -> None:
    listed = bind(allow.schema(TASKS, "create_field")).schema_tables()
    assert [item.id for item in listed] == [TASKS]


def test_equal_policies_hash_the_same_and_show_no_rules() -> None:
    one, other = bind(allow.read(TASKS)), bind(allow.read(TASKS))
    assert one == other
    assert len({one, other}) == 1
    assert one != bind(allow.read(NOTES))
    assert repr(one) == f"BoundPolicy(app_id={APP!r}, rules=1, hash={one.hash[:12]!r})"


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda: allow.read([5]), "takes table names"),
        (lambda: allow.report(TASKS, ["ok", 1.5]), "reports takes a list"),
        (lambda: allow.code_pages(write="yes"), "write takes True or False"),
        (lambda: deny.all(FieldSelector("named", ("Title",))), "takes a fields.\\* selector"),
        (lambda: Policy(tags={"": ["Title"]}), "needs a name"),
        (lambda: Policy(new_fields=""), "new_fields takes"),
    ],
    ids=["table", "reports", "write", "deny-named", "tag-name", "new-fields"],
)
def test_a_badly_written_rule_is_refused_when_its_made(call: Any, match: str) -> None:
    with pytest.raises(ConfigurationError, match=match):
        call()


@pytest.mark.parametrize(
    ("where", "text"),
    [
        (F("Status") == "Open", "F('Status') == 'Open'"),
        ((F("Status") == "a") | (F("Hours") > 2), "F('Status') == 'a' | F('Hours') > 2"),
        (((F("Status") == "a") & (F("Hours") > 2)) | (F("Hours") < 1),
         "(F('Status') == 'a' & F('Hours') > 2) | F('Hours') < 1"),
        (F("Title").contains("x"), "F('Title').contains('x')"),
    ],
    ids=["equals", "or", "nested", "method"],
)  # fmt: skip
def test_a_row_limit_reads_as_it_was_written(where: Any, text: str) -> None:
    assert limit.rows(TASKS, where).text == f"limit.rows({TASKS!r}, {text})"


def test_selectors_show_how_they_were_written() -> None:
    assert repr(fields.tagged("pay")) == "fields.tagged('pay')"
    assert repr(tables.all()) == "tables.all()"
    assert repr(FieldSelector("named", ("Title", 6))) == "['Title', 6]"
    assert repr(allow.read(TASKS)) == f"allow.read({TASKS!r})"
    assert repr(Policy(rules=[allow.read(TASKS)])).startswith("Policy(app=None, rules=[")


@pytest.mark.parametrize(
    ("rules", "options", "expected"),
    [
        ([allow.read(tables.all())], {}, False),
        ([allow.read(TASKS)], {}, True),
        ([allow.read(tables.all(), ["Title"])], {}, True),
        (
            [allow.read(tables.all(), fields.tagged("pay"))],
            {"tags": {"pay": ["Tasks.Hours"]}},
            True,
        ),
        ([limit.rows(TASKS, F("Status") == "x")], {}, True),
        ([allow.report(TASKS, ["Open"])], {}, True),
        ([allow.read(tables.all())], {"new_fields": "hidden"}, False),
    ],
    ids=["open", "named-table", "named-field", "tag", "limit", "named-report", "hidden"],
)
def test_a_policy_says_when_it_needs_an_app(
    rules: list[Any], options: dict[str, Any], expected: bool
) -> None:
    assert Policy(rules=rules, **options).needs_app is expected


def test_two_reports_with_one_name_must_be_written_by_id() -> None:
    reports = {TASKS: [{"id": "1", "name": "Open", "type": "table"},
                       {"id": "2", "name": "Open", "type": "table"}]}  # fmt: skip
    with pytest.raises(ConfigurationError, match="several reports are named 'Open'"):
        bind(allow.report(TASKS, ["Open"]), reports=reports)


def test_a_row_limit_cant_cover_tables_that_dont_exist_yet() -> None:
    with pytest.raises(ConfigurationError, match="needs tables that exist now"):
        bind(limit.rows(tables.created(), F("Status") == "x"))


def test_a_tag_can_cover_every_field_or_a_pair_that_doesnt_exist() -> None:
    every = bind(allow.read(tables.all()), tags={"all": [fields.all()]})
    assert "all" in every.tags_of(TASKS, 6)
    with pytest.raises(ConfigurationError, match="Nope"):
        bind(allow.read(tables.all()), tags={"pay": [("Nope", "Hours")]})


def summary_of(query: str, reference: int = 12) -> dict[str, Any]:
    return field(30, "Total", "numeric", mode="summary", properties={
        "summaryReferenceFieldId": reference, "summaryTargetFieldId": 6, "summaryFunction": "SUM",
        "summaryQuery": query,
    })  # fmt: skip


def test_a_summary_whose_filter_cant_be_read_counts_as_reading_every_child_field() -> None:
    data = {TASKS: ("Tasks", TASK_FIELDS), NOTES: ("Notes", [*NOTE_FIELDS, summary_of("{bad")])}
    links = [link(9, NOTES, TASKS, 12)]
    tags = {"pay": [("Tasks", "Hours")]}
    open_ = bind(allow.read(tables.all()), tables=data, relationships=links, tags=tags)
    assert open_.readable(NOTES, 30)
    denied = deny.read(tables.all(), fields.tagged("pay"))
    closed = bind(allow.read(tables.all()), denied, tables=data, relationships=links, tags=tags)
    assert "pay" in closed.tags_of(NOTES, 30)
    assert not closed.readable(NOTES, 30)


@pytest.mark.parametrize(
    ("query", "reference", "shown"),
    [("   ", 12, True), ("{6.EX.'x'}", 12, True), ("{8.EX.'x'}", 12, False),
     ("{6.EX.'x'}", 99, False), ("{bad", 12, False)],
    ids=["blank", "readable", "hidden-field", "unknown-relationship", "unreadable"],
)  # fmt: skip
def test_a_summarys_filter_is_only_shown_when_its_fields_are_readable(
    query: str, reference: int, shown: bool
) -> None:
    item = summary_of(query, reference)
    data = {TASKS: ("Tasks", TASK_FIELDS), NOTES: ("Notes", [*NOTE_FIELDS, item])}
    policy = bind(
        allow.read(NOTES),
        allow.read(TASKS, [6]),
        tables=data,
        relationships=[link(9, NOTES, TASKS, 12)],
    )
    trimmed = policy.trim(read("fields", NOTES), "GET", "/fields", [item])
    assert ("summaryQuery" in trimmed[0]["properties"]) is shown


def test_refusing_raises_the_error_that_carries_the_decision() -> None:
    decision = bind().decide(read("query", TASKS, limit=1))
    with pytest.raises(PolicyRefusedError) as caught:
        raise PolicyRefusedError(decision)
    assert caught.value.decision is decision


def test_an_offline_policy_answers_by_name() -> None:
    assert Policy.read_only().bind_offline().allows("read", "Tasks", field=6).allowed


def test_a_table_made_after_binding_has_no_known_fields() -> None:
    made = bind(allow.read(tables.all())).with_created("bqmade", "Made")
    with pytest.raises(ConfigurationError, match="doesn't know 'Made'"):
        made.offers("Made")


def test_a_rule_without_a_report_grant_doesnt_stop_one_with_it() -> None:
    policy = bind(allow.delete(TASKS), allow.report(TASKS))
    assert policy.decide(read("report", report_id="1", limit=1, skip=0)).allowed


def test_a_row_limit_isnt_counted_among_what_a_deletion_changes() -> None:
    policy = bind(allow.read(TASKS, [6]), limit.rows(TASKS, F("Status") == "x"))
    effects, blockers = policy.deletion_effects(TASKS, [6])
    assert effects == (f"allow.read({TASKS!r}, [6]) loses Title (6).",)
    assert len(blockers) == 1


def test_a_table_selector_shows_its_name() -> None:
    assert repr(allow.read(TASKS).tables[0]) == repr(TASKS)


def test_a_query_cell_holding_other_details_is_left_alone() -> None:
    policy = bind(allow.read(TASKS))
    payload = {
        "fields": [{"id": 6, "label": "Title", "type": "text"}],
        "data": [{"6": {"value": {"a": 1}}}],
        "metadata": {"totalRecords": 1, "skip": 0},
    }
    request_ = query("{6.EX.'x'}", 6)
    trimmed = policy.trim(request_, "POST", "/records/query", payload)
    assert trimmed["data"] == [{"6": {"value": {"a": 1}}}]


def test_a_decision_is_true_only_when_it_allows() -> None:
    policy = bind(allow.read(TASKS, [6]))
    assert policy.allows("read", TASKS, field=6)
    assert not policy.allows("read", TASKS, field=8)


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        (read("report", report_id="1", limit=500, skip=0), "records"),
        (read("report", report_id="1", limit=1, skip=5000), "records"),
    ],
    ids=["too-many", "too-deep"],
)
def test_a_report_past_the_paging_limit_stays_refused(request_: Any, reason: str) -> None:
    policy = bind(
        allow.read(TASKS, max_records_scanned=100, max_paging_depth=100), allow.report(TASKS)
    )
    decision = policy.decide(request_)
    assert not decision.allowed
    assert reason in decision.reason
