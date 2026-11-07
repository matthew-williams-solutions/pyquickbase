"""Schema changes in their JSON form, as a model sends them. No network access."""

from typing import Any

import pytest

from pyquickbase import (
    CreateField,
    CreateSummaryField,
    DeleteField,
    InvalidValueError,
    PlannedChange,
    SchemaPlan,
    UpdateField,
)
from pyquickbase._schema_plan import (
    change_from_json,
    change_kind,
    change_to_json,
    changes_from_json,
)


@pytest.mark.parametrize(
    "change",
    [
        CreateField(table="Tasks", label="Due", type="date", required=True),
        CreateField(table="Tasks", label="Pay", type="numeric", formula="[Hours] * 40"),
        UpdateField(table="Tasks", field=7, choices=("Open", "Done")),
        DeleteField(table="Tasks", field="Notes"),
        CreateSummaryField(child="Tasks", foreign_key="Project", label="N", function="COUNT"),
        CreateSummaryField(
            child="Tasks", foreign_key="Project", label="Open", function="COUNT", where="{7.EX.1}"
        ),
    ],
)
def test_a_change_survives_its_json_form(change: Any) -> None:
    data = change_to_json(change)
    assert data["kind"] == change_kind(change)
    assert None not in data.values()
    assert change_from_json(data) == change


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ([], "can't be empty"),
        ({"kind": "create_field"}, "must be a list"),
        ([{"kind": "drop_app"}], r"changes\[0\]\.kind must be one of"),
        ([{"kind": "create_field", "table": "T", "label": "L"}], r"changes\[0\]\.type is required"),
        ([{"kind": "create_field", "table": "T", "label": "L", "type": "money"}], "type must be"),
        ([{"kind": "delete_field", "table": "T", "field": "F", "why": "x"}], "doesn't take: why"),
        ([{"kind": "delete_field", "table": "T", "field": True}], "can't be true or false"),
        ([{"kind": "delete_field", "table": " ", "field": "F"}], "can't be blank"),
        ([{"kind": "update_field", "table": "T", "field": "F", "choices": "A"}], "list of text"),
        ([{"kind": "update_field", "table": "T", "field": "F", "required": "yes"}], "must be bool"),
        ([{"kind": "create_summary", "child": "T", "foreign_key": "P", "label": "L",
           "function": "sum"}], "function must be one of"),
        (["create_field"], "must be an object"),
    ],
)  # fmt: skip
def test_a_change_that_cant_be_used_says_where(data: Any, match: str) -> None:
    with pytest.raises(InvalidValueError, match=match):
        changes_from_json(data)


def test_a_plan_may_be_limited_in_length() -> None:
    item = {"kind": "delete_field", "table": "T", "field": "F"}
    assert len(changes_from_json([item] * 3, most=3)) == 3
    with pytest.raises(InvalidValueError, match="at most 2 changes, and this has 3"):
        changes_from_json([item] * 3, most=2)


def test_a_plan_gathers_its_blockers_and_warnings() -> None:
    change = DeleteField(table="T", field="F")
    planned = [
        PlannedChange(change=change, status="change", risk="destructive", summary="a",
                      warnings=("w1",)),
        PlannedChange(change=change, status="blocked", risk="destructive", summary="b",
                      blockers=("b1",)),
    ]  # fmt: skip
    plan = SchemaPlan(app_id="bq1", changes=tuple(planned), fingerprint="f")
    assert (plan.blockers, plan.warnings, plan.has_changes) == (("b1",), ("w1",), True)
    assert "confirmation" not in repr(SchemaPlan(app_id="a", changes=(), fingerprint="f",
                                                 confirmation="secret"))  # fmt: skip
