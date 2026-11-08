"""Bind policies against a fixture app, without a network, for the policy tests."""

from collections.abc import Mapping, Sequence
from typing import Any

from pyquickbase import BoundPolicy, Policy
from pyquickbase._bound import Snapshot, bind_snapshot
from pyquickbase._models import (
    field_from_api,
    relationship_from_api,
    report_info_from_api,
    table_info_from_api,
)
from pyquickbase._policy import NewFields, RuleLike

APP = "bqapp"
TASKS = "bqtasks"
NOTES = "bqnotes"


def field(field_id: int, label: str, kind: str = "text", **extra: Any) -> dict[str, Any]:
    return {"id": field_id, "label": label, "fieldType": kind, "appearsByDefault": True, **extra}


TASK_FIELDS = [
    field(3, "Record ID#", "recordid"),
    field(6, "Title"),
    field(7, "Status"),
    field(8, "Hours", "numeric"),
    field(9, "Notes"),
    field(10, "Owner", "user"),
    field(12, "Priority"),
    field(13, "Attachment", "file"),
]
NOTE_FIELDS = [field(3, "Record ID#", "recordid"), field(6, "Text"), field(7, "Kind")]
TABLES: dict[str, tuple[str, list[dict[str, Any]]]] = {
    TASKS: ("Tasks", TASK_FIELDS),
    NOTES: ("Notes", NOTE_FIELDS),
}


def link(
    link_id: int,
    parent: str,
    child: str,
    foreign_key: int,
    *,
    lookups: Sequence[int] = (),
    summaries: Sequence[int] = (),
) -> dict[str, Any]:
    return {
        "id": link_id,
        "parentTableId": parent,
        "childTableId": child,
        "foreignKeyField": {"id": foreign_key, "label": "Related", "type": "numeric"},
        "lookupFields": [{"id": item, "label": f"L{item}"} for item in lookups],
        "summaryFields": [{"id": item, "label": f"S{item}"} for item in summaries],
        "isCrossApp": False,
    }


def snapshot(
    tables: Mapping[str, tuple[str, list[dict[str, Any]]]] | None = None,
    *,
    relationships: Sequence[dict[str, Any]] = (),
    reports: Mapping[str, list[dict[str, Any]]] | None = None,
    app_id: str = APP,
    relationships_complete: bool = True,
) -> Snapshot:
    found = TABLES if tables is None else tables
    return Snapshot(
        app_id=app_id,
        tables=tuple(
            table_info_from_api({"id": table_id, "name": name, "keyFieldId": 3})
            for table_id, (name, _) in found.items()
        ),
        fields={
            table_id: tuple(field_from_api(item) for item in items)
            for table_id, (_, items) in found.items()
        },
        relationships=tuple(relationship_from_api(item) for item in relationships),
        reports={
            table_id: tuple(report_info_from_api(item) for item in items)
            for table_id, items in (reports or {}).items()
        },
        relationships_complete=relationships_complete,
    )


def bind(
    *rules: RuleLike,
    classes: Mapping[str, Any] | None = None,
    new_fields: NewFields = "read",
    tables: Mapping[str, tuple[str, list[dict[str, Any]]]] | None = None,
    relationships: Sequence[dict[str, Any]] = (),
    reports: Mapping[str, list[dict[str, Any]]] | None = None,
    app_id: str = APP,
    relationships_complete: bool = True,
) -> BoundPolicy:
    policy = Policy(classes=classes, rules=rules, new_fields=new_fields)
    return bind_snapshot(
        policy,
        snapshot(
            tables,
            relationships=relationships,
            reports=reports,
            app_id=app_id,
            relationships_complete=relationships_complete,
        ),
    )
