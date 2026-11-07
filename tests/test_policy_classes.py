"""What a bound policy works out from the app: classes carried through derived fields, fields added
later, row limits, saving, and rebinding. Plus the checks that offers() matches decide(), and a
snapshot of the decisions for a fixture app. No network access."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pyquickbase import (
    BoundPolicy,
    ConfigurationError,
    F,
    ReadRequest,
    allow,
    deny,
    fields,
    limit,
    tables,
)
from pyquickbase._read_request import describe_read
from pyquickbase._write_request import describe_write

from .policy_helpers import NOTES, TASKS, bind, field, link

GRID = Path(__file__).parent / "fixtures" / "policy_grid.txt"

# Notes is the parent of Tasks, through Tasks' field 9.
PAY = field(20, "Rate", "currency")
TASK_FIELDS = [
    field(3, "Record ID#", "recordid"),
    field(6, "Title"),
    field(7, "Status"),
    field(8, "Hours", "numeric"),
    field(9, "Note", "numeric"),
    PAY,
    field(21, "Cost", "currency", mode="formula", properties={"formula": "[Hours] * [Rate]"}),
    field(22, "Cost Label", mode="formula", properties={"formula": "ToText([Cost])"}),
    field(23, "Broken", mode="formula", properties={"formula": '"unclosed'}),
    field(24, "Elsewhere", mode="formula", properties={"formula": 'GetRecord(1, "bqother")'}),
    field(25, "Loop A", mode="formula", properties={"formula": "[Loop B] & [Rate]"}),
    field(26, "Loop B", mode="formula", properties={"formula": "[Loop A]"}),
    field(27, "By ID", mode="formula", properties={"formula": "[_FID_20] + 1"}),
    field(28, "Note Text", mode="lookup",
          properties={"lookupReferenceFieldId": 9, "lookupTargetFieldId": 7}),
    field(29, "Plain", mode="formula", properties={"formula": "[Title]"}),
]  # fmt: skip
NOTE_FIELDS = [
    field(3, "Record ID#", "recordid"),
    field(6, "Text"),
    field(7, "Secret"),
    field(30, "Total Cost", "currency", mode="summary",
          properties={"summaryReferenceFieldId": 9, "summaryTargetFieldId": 21,
                      "summaryFunction": "SUM"}),
    field(31, "Open Count", "numeric", mode="summary",
          properties={"summaryReferenceFieldId": 9, "summaryTargetFieldId": 0,
                      "summaryFunction": "COUNT", "summaryQuery": "{20.GT.'5'}"}),
    field(32, "All Count", "numeric", mode="summary",
          properties={"summaryReferenceFieldId": 9, "summaryTargetFieldId": 0,
                      "summaryFunction": "COUNT"}),
]  # fmt: skip
APP_TABLES = {TASKS: ("Tasks", TASK_FIELDS), NOTES: ("Notes", NOTE_FIELDS)}
LINKS = [link(1, NOTES, TASKS, 9, lookups=[28], summaries=[30, 31, 32])]


def app(*rules: Any, **options: Any) -> BoundPolicy:
    return bind(
        *rules,
        tables=options.pop("tables", APP_TABLES),
        relationships=options.pop("relationships", LINKS),
        **options,
    )


PAY_CLASS = {"pay": [("Tasks", "Rate")]}


def classes_of(policy: BoundPolicy, table_id: str, field_id: int) -> set[str]:
    return set(policy.classes_of(table_id, field_id)) - {"\x00rows"}


# Classes carry over


def test_formulas_lookups_and_summaries_get_the_classes_of_what_they_read() -> None:
    policy = app(classes={**PAY_CLASS, "secret": ["Notes.Secret"]})
    assert classes_of(policy, TASKS, 20) == {"pay"}
    assert classes_of(policy, TASKS, 21) == {"pay"}, "a formula of Rate"
    assert classes_of(policy, TASKS, 22) == {"pay"}, "a formula of that formula"
    assert classes_of(policy, NOTES, 30) == {"pay"}, "a summary of the formula, across tables"
    assert classes_of(policy, TASKS, 28) == {"secret"}, "a lookup of Notes' Secret"
    assert classes_of(policy, TASKS, 27) == {"pay"}, "a formula naming Rate by [_FID_20]"
    assert classes_of(policy, TASKS, 29) == set()
    assert classes_of(app(classes=PAY_CLASS), TASKS, 28) == set()


def test_a_summary_gets_the_classes_of_the_fields_its_filter_uses() -> None:
    policy = app(classes=PAY_CLASS)
    assert classes_of(policy, NOTES, 31) == {"pay"}
    assert classes_of(policy, NOTES, 32) == set(), "a count without a filter reads no field"


def test_cycles_settle() -> None:
    policy = app(classes=PAY_CLASS)
    assert classes_of(policy, TASKS, 25) == {"pay"}
    assert classes_of(policy, TASKS, 26) == {"pay"}


def test_a_formula_that_cant_be_read_counts_as_reading_its_whole_table() -> None:
    policy = app(classes=PAY_CLASS)
    assert classes_of(policy, TASKS, 23) == {"pay"}


def test_a_formula_that_reads_other_records_counts_as_reading_the_whole_app() -> None:
    policy = app(classes={"secret": ["Notes.Secret"]})
    assert classes_of(policy, TASKS, 24) == {"secret"}


def test_denying_a_class_hides_everything_derived_from_it() -> None:
    policy = app(allow.read(tables.all()), deny.all(fields.classed("pay")), classes=PAY_CLASS)
    hidden = [fid for fid in (20, 21, 22, 23, 25, 26, 27) if not policy.visible(TASKS, fid)]
    assert hidden == [20, 21, 22, 23, 25, 26, 27]
    assert policy.visible(TASKS, 29)
    assert not policy.visible(NOTES, 30)
    assert not policy.visible(NOTES, 31)
    assert policy.visible(NOTES, 32)
    assert policy.hidden_labels(TASKS) >= {"rate", "cost", "broken"}


def test_classes_can_be_matched_by_label_pattern_across_tables() -> None:
    policy = app(classes={"internal": [fields.labeled_like("*count")]})
    assert classes_of(policy, NOTES, 31) == {"internal"}
    assert classes_of(policy, NOTES, 32) == {"internal"}
    assert classes_of(policy, TASKS, 6) == set()


def test_an_ambiguous_dotted_name_must_be_written_as_a_pair() -> None:
    tables_with_dots = {
        "bqa": ("A", [field(6, "B.C")]),
        "bqab": ("A.B", [field(6, "C")]),
    }
    with pytest.raises(ConfigurationError, match="could be several fields"):
        bind(classes={"x": ["A.B.C"]}, tables=tables_with_dots)
    policy = bind(classes={"x": [("A.B", "C")]}, tables=tables_with_dots)
    assert classes_of(policy, "bqab", 6) == {"x"}


# Fields added after binding


def query(table: str, *select: int, where: str = "") -> ReadRequest:
    body: dict[str, Any] = {"from": table, "select": list(select), "sortBy": [],
                            "options": {"top": 1, "skip": 0}}  # fmt: skip
    if where:
        body["where"] = where
    found = describe_read("POST", "/records/query", None, body)
    assert found is not None
    return found


def update(table: str, *field_ids: int) -> Any:
    row = {"3": {"value": 1}, **{str(item): {"value": "x"} for item in field_ids}}
    return describe_write("POST", "/records", {"to": table, "data": [row], "mergeFieldId": 3})


def test_a_new_field_is_read_only_by_default() -> None:
    policy = app(allow.read(TASKS), allow.update(TASKS, fields.all()))
    assert policy.decide(query(TASKS, 99)).allowed
    decision = policy.decide(update(TASKS, 99))
    assert not decision.allowed
    assert "added after the policy was bound" in decision.reason
    assert decision.hint == "bind the policy again"
    assert policy.decide(update(TASKS, 6)).allowed


def test_a_new_field_can_be_hidden_or_given_a_class() -> None:
    hidden = app(allow.read(TASKS), new_fields="hidden")
    assert not hidden.visible(TASKS, 99)
    classed = app(
        allow.read(TASKS),
        deny.read(TASKS, fields.classed("unreviewed")),
        classes={"unreviewed": [("Tasks", "Title")]},
        new_fields="unreviewed",
    )
    assert not classed.visible(TASKS, 99)
    assert classes_of(classed, TASKS, 99) == {"unreviewed"}


def test_fields_new_matches_only_fields_added_later() -> None:
    policy = app(allow.read(TASKS, fields.new()))
    assert policy.readable(TASKS, 99)
    assert not policy.readable(TASKS, 6)


# Row limits


def test_a_summary_of_a_row_limited_child_without_the_limit_is_hidden() -> None:
    policy = app(allow.read(tables.all()), limit.rows(TASKS, F("Rate") > 5))
    assert policy.row_limit(TASKS) == "{20.GT.'5'}"
    assert not policy.visible(NOTES, 30)
    assert not policy.visible(NOTES, 32)
    assert policy.visible(NOTES, 31), "its filter holds the limit"
    decision = policy.decide(query(NOTES, 30))
    assert decision.layer == "rows"
    assert decision.rule == "limit.rows('bqtasks', F('Rate') > 5)"


def test_a_lookup_from_a_row_limited_parent_is_hidden_but_onto_a_limited_child_is_fine() -> None:
    from_limited = app(
        allow.read(tables.all()), allow.schema(TASKS, "lookup"), limit.rows(NOTES, F("Text") == "x")
    )
    assert not from_limited.visible(TASKS, 28)
    decision = from_limited.allows("lookup", "Tasks", source=("Notes", "Text"))
    assert (decision.allowed, decision.layer) == (False, "rows")
    onto_limited = app(
        allow.read(tables.all()), allow.schema(TASKS, "lookup"), limit.rows(TASKS, F("Hours") > 1)
    )
    assert onto_limited.visible(TASKS, 28)
    assert onto_limited.allows("lookup", "Tasks", source=("Notes", "Text")).allowed


def test_a_row_limited_table_refuses_what_the_limit_cant_be_added_to() -> None:
    policy = app(
        allow.read(TASKS),
        allow.download(TASKS),
        allow.report(TASKS),
        limit.rows(TASKS, F("Hours") > 1),
    )
    report = describe_read(
        "POST", "/reports/1/run", {"tableId": TASKS, "top": 1, "skip": 0}, None, report_type="table"
    )
    download = describe_read("GET", f"/files/{TASKS}/1/6/0", None, None)
    assert report is not None
    assert download is not None
    for request in (report, download):
        decision = policy.decide(request)
        assert (decision.allowed, decision.layer) == (False, "rows")
    assert policy.filterable(TASKS, 8), "fields the limit uses may be filtered on"


def test_several_row_limits_on_a_table_are_all_applied() -> None:
    policy = app(
        allow.read(TASKS), limit.rows(TASKS, F("Hours") > 1), limit.rows(TASKS, F("Title") == "x")
    )
    assert policy.row_limit(TASKS) == "({8.GT.'1'})AND({6.EX.'x'})"


# Saving, created tables, and rebinding


def test_a_saved_policy_decides_the_same() -> None:
    policy = app(
        allow.read(tables.all(), max_records=20),
        allow.update(TASKS, ["Status"]),
        allow.create_tables(),
        allow.schema(tables.created(), "add_field"),
        deny.all(fields.classed("pay")),
        limit.rows(TASKS, F("Hours") > 1),
        classes=PAY_CLASS,
    ).with_created("bqnew", "New")
    loaded = BoundPolicy.from_json(policy.to_json())
    assert loaded == policy
    assert loaded.hash == policy.hash
    assert loaded.created == ("bqnew",)
    assert grid(loaded) == grid(policy)
    assert json.loads(policy.to_json())["format"] == 1


def test_a_policy_that_isnt_saved_wont_load() -> None:
    for text in ("", "[]", '{"format": 2}', '{"format": 1}'):
        with pytest.raises(ConfigurationError, match=r"saved by BoundPolicy.to_json\(\)"):
            BoundPolicy.from_json(text)


def renamed(label: str) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    items = [dict(item, label=label) if item["id"] == 7 else item for item in TASK_FIELDS]
    return {**APP_TABLES, TASKS: ("Tasks", items)}


def test_rename_delete_and_add_a_field_then_bind_again() -> None:
    rules = (allow.read(TASKS), allow.update(TASKS, ["Status"]))
    before = app(*rules)
    # A rename keeps its rules until the policy is bound again, as they're held by ID.
    assert before.decide(update(TASKS, 7)).allowed
    with pytest.raises(ConfigurationError, match="has no field 'Status'"):
        app(*rules, tables=renamed("State"))
    after = app(allow.read(TASKS), allow.update(TASKS, ["State"]), tables=renamed("State"))
    assert after.decide(update(TASKS, 7)).allowed
    # A deleted field is unknown to a policy bound afterward.
    gone = {**APP_TABLES, TASKS: ("Tasks", [item for item in TASK_FIELDS if item["id"] != 6])}
    assert not app(allow.read(TASKS), tables=gone)._fields[TASKS].get(6)  # pyright: ignore[reportPrivateUsage]
    # A field added after binding is new, and once bound again it's like any other.
    added = {**APP_TABLES, TASKS: ("Tasks", [*TASK_FIELDS, field(40, "Added")])}
    assert not before.decide(update(TASKS, 40)).allowed
    rebound = app(allow.read(TASKS), allow.update(TASKS, fields.all()), tables=added)
    assert rebound.decide(update(TASKS, 40)).allowed


def test_explain_names_the_rules_and_classes() -> None:
    policy = app(
        allow.read(tables.all()), deny.read(TASKS, fields.classed("pay")), classes=PAY_CLASS
    )
    text = policy.explain("read", "Tasks", field="Plain")
    assert text.splitlines()[0] == "Allowed: reading Plain (29) in Tasks (bqtasks)."
    text = policy.explain("update", "Tasks", field="Title")
    assert "Refused: updating Title (6) in Tasks (bqtasks)." in text
    assert 'To allow it: add "Title" to an allow.update("Tasks", ...) rule.' in text


# offers() matches decide(), and the grid of decisions

RULES = st.sampled_from(
    [
        allow.read(tables.all()),
        allow.read(TASKS, ["Title", "Status"], filter=["Status"]),
        allow.read(NOTES, fields.new()),
        allow.filter(TASKS, ["Hours"]),
        allow.sort(tables.all(), fields.all()),
        allow.insert(TASKS, fields.all()),
        allow.insert(NOTES, ["Text"]),
        allow.update(TASKS, ["Status", "Hours"]),
        allow.update(tables.all(), fields.classed("pay")),
        allow.delete(TASKS),
        allow.download(tables.all()),
        allow.delete_file(TASKS, ["Title"]),
        allow.schema(TASKS, "add_field", "change_field", fields=["Title"]),
        deny.all(fields.classed("pay")),
        deny.read(TASKS, ["Hours"]),
        deny.update(tables.all(), fields.labeled_like("*cost*")),
        deny.all(NOTES),
        limit.rows(TASKS, F("Hours") > 1),
    ]
)


@settings(max_examples=60, deadline=None)
@given(st.lists(RULES, max_size=6))
def test_offers_matches_decide(rules: list[Any]) -> None:
    policy = app(*rules, classes=PAY_CLASS)
    for table_id, (_, items) in APP_TABLES.items():
        offers = policy.offers(table_id)
        for verb, offered in (
            ("readable", offers.readable),
            ("filterable", offers.filterable),
            ("sortable", offers.sortable),
            ("insertable", offers.insertable),
            ("updatable", offers.updatable),
        ):
            # A write can't set Record ID#, which names the record, so it's compared for reads only.
            ids = [item["id"] for item in items if verb in READS or item["id"] != 3]
            expected = [fid for fid in ids if decided(policy, verb, table_id, fid)]
            found = [int(item.id) for item in offered if verb in READS or item.id != 3]
            assert found == expected, (verb, table_id)
        delete = describe_write("DELETE", "/records", {"from": table_id})
        assert offers.can_delete == policy.decide(delete).allowed


READS = ("readable", "filterable", "sortable")


def decided(policy: BoundPolicy, verb: str, table_id: str, field_id: int) -> bool:
    if verb == "readable":
        return policy.decide(query(table_id, field_id)).allowed
    if verb == "filterable":
        return policy.decide(query(table_id, where=f"{{{field_id}.EX.'x'}}")).allowed
    if verb == "sortable":
        body = {"from": table_id, "select": [], "options": {"top": 1, "skip": 0},
                "sortBy": [{"fieldId": field_id, "order": "ASC"}]}  # fmt: skip
        found = describe_read("POST", "/records/query", None, body)
        assert found is not None
        return policy.decide(found).allowed
    row = {str(field_id): {"value": "x"}}
    if verb == "insertable":
        request = describe_write(
            "POST", "/records", {"to": table_id, "data": [row]}, key_field_id=3
        )
        return policy.decide(request).allowed
    return policy.decide(update(table_id, field_id)).allowed


GRID_VERBS = ("read", "filter", "sort", "insert", "update", "download", "change_field")


def grid(policy: BoundPolicy) -> str:
    lines: list[str] = []
    for table_id, (name, items) in APP_TABLES.items():
        for item in items:
            row = "".join(
                "Y" if policy._check(verb, table_id, item["id"]).allowed else "."  # pyright: ignore[reportPrivateUsage]
                for verb in GRID_VERBS
            )
            lines.append(f"{name:<6} {item['label']:<12} {row}")
    return "\n".join(lines)


def test_the_decision_grid_matches_the_snapshot() -> None:
    policies = {
        "everything read, pay denied, Tasks row-limited": app(
            allow.read(tables.all()),
            allow.update(TASKS, ["Status", "Hours"]),
            allow.insert(TASKS, fields.all()),
            allow.schema(TASKS, "change_field", fields=["Title"]),
            deny.all(fields.classed("pay")),
            limit.rows(TASKS, F("Hours") > 1),
            classes=PAY_CLASS,
        ),
        "Policy.read_only()": app(
            allow.read(tables.all(), max_records=None), allow.download(tables.all())
        ),
    }
    current = "\n\n".join(
        f"# {name}\n# {' '.join(GRID_VERBS)}\n{grid(policy)}" for name, policy in policies.items()
    )
    if os.environ.get("QB_UPDATE_POLICY_GRID"):
        GRID.write_text(current + "\n", encoding="utf-8")
    assert current + "\n" == GRID.read_text(encoding="utf-8"), (
        "The decisions changed. If that's intended, run "
        "QB_UPDATE_POLICY_GRID=1 uv run pytest tests/test_policy_classes.py"
    )
