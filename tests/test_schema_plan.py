"""Schema changes in their JSON form, as a model sends them. No network access."""

from typing import Any

import pytest

from pyquickbase import (
    CreateField,
    CreateLookupField,
    CreateRelationship,
    CreateSummaryField,
    DeleteField,
    InvalidValueError,
    PlannedChange,
    SchemaPlan,
    UpdateField,
)
from pyquickbase._schema_plan import (
    change_action,
    change_from_json,
    change_to_json,
    changes_from_json,
    quickbase_function,
    summary_function,
)


@pytest.mark.parametrize(
    "change",
    [
        CreateField(table="Tasks", label="Due", type="date", required=True),
        CreateField(table="Tasks", label="Pay", type="numeric", formula="[Hours] * 40"),
        UpdateField(table="Tasks", field=7, choices=("Open", "Done")),
        DeleteField(table="Tasks", field="Notes"),
        CreateSummaryField(child_table="Tasks", foreign_key="Project", label="N", function="count"),
        CreateSummaryField(
            child_table="Tasks",
            foreign_key="Project",
            label="Open",
            function="count",
            where="{7.EX.1}",
        ),
    ],
)
def test_a_change_survives_its_json_form(change: Any) -> None:
    data = change_to_json(change)
    assert data["action"] == change_action(change)
    assert None not in data.values()
    assert change_from_json(data) == change


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ([], "can't be empty"),
        ({"action": "create_field"}, "must be a list"),
        ([{"action": "drop_app"}], r"changes\[0\]\.action must be one of"),
        ([{"action": "create_field", "table": "T", "label": "L"}],
         r"changes\[0\]\.type is required"),
        ([{"action": "create_field", "table": "T", "label": "L", "type": "money"}], "type must be"),
        ([{"action": "delete_field", "table": "T", "field": "F", "why": "x"}], "doesn't take: why"),
        ([{"action": "delete_field", "table": "T", "field": True}], "can't be true or false"),
        ([{"action": "delete_field", "table": " ", "field": "F"}], "can't be blank"),
        ([{"action": "update_field", "table": "T", "field": "F", "choices": "A"}], "list of text"),
        ([{"action": "update_field", "table": "T", "field": "F", "required": "yes"}],
         "must be bool"),
        ([{"action": "create_summary_field", "child_table": "T", "foreign_key": "P", "label": "L",
           "function": "median"}], "function must be one of"),
        (["create_field"], "must be an object"),
    ],
)  # fmt: skip
def test_a_change_that_cant_be_used_says_where(data: Any, match: str) -> None:
    with pytest.raises(InvalidValueError, match=match):
        changes_from_json(data)


@pytest.mark.parametrize(
    ("data", "change"),
    [
        ({"kind": "create_relationship", "child": "Tasks", "parent": "Projects",
          "foreign_key": "Project"},
         CreateRelationship(child_table="Tasks", parent_table="Projects", foreign_key="Project")),
        ({"action": "create_lookup", "child_table": "Tasks", "foreign_key": "Project",
          "parent_field": "Name"},
         CreateLookupField(child_table="Tasks", foreign_key="Project", parent_field="Name")),
        ({"action": "Create Summary Field", "child_table": "Tasks", "foreign_key": "Project",
          "label": "Total", "function": "SUM", "child_field": "Hours"},
         CreateSummaryField(child_table="Tasks", foreign_key="Project", label="Total",
                            function="sum", child_field="Hours")),
        ({"action": "create_summary", "child": "Tasks", "foreign_key": "Project",
          "label": "Spread", "function": "STD-DEV", "child_field": "Hours"},
         CreateSummaryField(child_table="Tasks", foreign_key="Project", label="Spread",
                            function="std_dev", child_field="Hours")),
    ],
)  # fmt: skip
def test_spellings_models_write_are_read_as_one_change(data: Any, change: Any) -> None:
    assert change_from_json(data) == change


@pytest.mark.parametrize(
    ("name", "function", "code"),
    [
        ("sum", "sum", "SUM"),
        ("SUM", "sum", "SUM"),
        ("total", "sum", "SUM"),
        ("Average", "avg", "AVG"),
        ("std-dev", "std_dev", "STD-DEV"),
        ("DISTINCT-COUNT", "distinct_count", "DISTINCT-COUNT"),
        ("combined text", "combined_text", "COMBINED-TEXT"),
        ("count", "count", "COUNT"),
    ],
)
def test_a_function_has_one_name_and_quickbase_has_another(
    name: str, function: Any, code: str
) -> None:
    assert summary_function(name) == function
    assert quickbase_function(function) == code


@pytest.mark.parametrize("name", ["median", "", "sum of", 3, None])
def test_an_unknown_function_is_none(name: Any) -> None:
    assert summary_function(name) is None


def test_the_schema_spelling_wins_over_an_alias() -> None:
    data = {"action": "delete_table", "kind": "delete_field", "table": "T"}
    with pytest.raises(InvalidValueError, match="doesn't take: kind"):
        change_from_json(data)


def test_a_plan_may_be_limited_in_length() -> None:
    item = {"action": "delete_field", "table": "T", "field": "F"}
    assert len(changes_from_json([item] * 3, most=3)) == 3
    with pytest.raises(InvalidValueError, match="at most 2 changes, and this has 3"):
        changes_from_json([item] * 3, most=2)


def test_a_plan_gathers_its_blockers_and_warnings() -> None:
    change = DeleteField(table="T", field="F")
    planned = [
        PlannedChange(change=change, status="ready", risk="destructive", description="a",
                      warnings=("w1",)),
        PlannedChange(change=change, status="blocked", risk="destructive", description="b",
                      blockers=("b1",)),
    ]  # fmt: skip
    plan = SchemaPlan(app_id="bq1", changes=tuple(planned), fingerprint="f")
    assert (plan.blockers, plan.warnings, plan.has_changes) == (("b1",), ("w1",), True)
    assert "confirmation" not in repr(SchemaPlan(app_id="a", changes=(), fingerprint="f",
                                                 confirmation="secret"))  # fmt: skip
