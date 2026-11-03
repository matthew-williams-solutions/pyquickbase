"""A simulated Quickbase API, so the docs playground needs no account, token, or server.

It runs inside the visitor's browser and holds a sample app of three linked tables, with lookups,
a formula, and rollups that stay current as records are added. It covers reading and adding records
so far.
Its quirks copy what's recorded in docs/api-notes.md, including the mistakes Quickbase accepts,
because those are what the SDK protects against.
"""

import copy
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

APP_ID = "bq1234567"
TASKS_ID = "bqtasks"
PROJECTS_ID = "bqprojects"
TIME_ID = "bqtime"
RECORD_ID = 3
Row = dict[int, Any]

_APP = {
    "id": APP_ID,
    "name": "Project Tracker (sample)",
    "description": "Sample data for the pyquickbase playground.",
    "timeZone": "(UTC) Coordinated Universal Time",
    "dateFormat": "MM-DD-YYYY",
}
_STATUSES = ["Open", "Blocked", "Done"]
_PRIORITIES = ["Low", "Medium", "High"]
_PROJECT_STATUSES = ["Active", "On Hold", "Complete"]
# How each type reads back when empty, from docs/api-notes.md.
_EMPTY: dict[str, Any] = {
    "numeric": None,
    "currency": None,
    "user": None,
    "checkbox": False,
    "multitext": [],
    "file": {"url": "", "versions": []},
}
_NUMERIC_TYPES = {"numeric", "currency", "recordid"}
_NOT_SIMULATED_IN_QUERIES = {"user", "file"}  # docs/api-notes.md doesn't record how they match.


def _field(
    field_id: int,
    label: str,
    field_type: str,
    *,
    default: bool = True,
    mode: str = "",
    **properties: Any,
) -> dict[str, Any]:
    return {
        "id": field_id,
        "label": label,
        "fieldType": field_type,
        "mode": mode,
        "required": False,
        "unique": field_id == RECORD_ID,
        "appearsByDefault": default,
        "properties": properties,
    }


_TASK_FIELDS = [
    _field(1, "Date Created", "timestamp", default=False),
    _field(RECORD_ID, "Record ID#", "recordid", default=False),
    _field(6, "Title", "text"),
    _field(7, "Status", "text-multiple-choice", choices=_STATUSES, allowNewChoices=False),
    _field(8, "Hours", "numeric"),
    _field(9, "Due Date", "date"),
    _field(10, "Notes", "text", default=False),
    _field(11, "Active", "checkbox", default=False),
    _field(12, "Related Project", "numeric", default=False),
    _field(13, "Priority", "text-multiple-choice", default=False, choices=_PRIORITIES),
    _field(14, "Assignee", "user", default=False),
    _field(15, "Tags", "multitext", default=False),
    _field(16, "Attachment", "file", default=False),
    _field(17, "Project Name", "text", default=False, mode="lookup"),
    _field(18, "Hours Logged", "numeric", default=False, mode="summary"),
    _field(
        19,
        "Over Estimate",
        "checkbox",
        default=False,
        mode="formula",
        formula="[Hours Logged] > [Hours]",
    ),
]
_PROJECT_FIELDS = [
    _field(RECORD_ID, "Record ID#", "recordid", default=False),
    _field(6, "Name", "text"),
    _field(7, "Budget", "currency"),
    _field(8, "Status", "text-multiple-choice", choices=_PROJECT_STATUSES, allowNewChoices=False),
    _field(9, "Owner", "user"),
    _field(10, "Start Date", "date", default=False),
    _field(11, "End Date", "date", default=False),
    _field(12, "Task Count", "numeric", default=False, mode="summary"),
    _field(13, "Total Hours", "numeric", default=False, mode="summary"),
]
_TIME_FIELDS = [
    _field(RECORD_ID, "Record ID#", "recordid", default=False),
    _field(6, "Task", "numeric"),
    _field(7, "Task Title", "text", mode="lookup"),
    _field(8, "Person", "user"),
    _field(9, "Date", "date"),
    _field(10, "Hours", "numeric"),
    _field(11, "Billable", "checkbox"),
    _field(12, "Note", "text", default=False),
]


def _person(name: str, user_id: str) -> dict[str, str]:
    email = f"{name.split()[0].lower()}@example.com"
    return {"email": email, "id": user_id, "name": name}


ANA = _person("Ana Reyes", "60001.ana")
BEN = _person("Ben Okafor", "60002.ben")
CHLOE = _person("Chloe Martin", "60003.chl")
DEV = _person("Dev Patel", "60004.dev")
ELENA = _person("Elena Novak", "60005.ele")


def _attachment(record_id: int, *versions: tuple[str, dict[str, str]]) -> dict[str, Any]:
    return {
        "url": f"/files/{TASKS_ID}/{record_id}/16/{len(versions)}",
        "versions": [
            {
                "versionNumber": number,
                "fileName": name,
                "uploaded": f"2026-03-{number:02d}T15:30:00Z",
                "creator": uploader,
            }
            for number, (name, uploader) in enumerate(versions, start=1)
        ],
    }


_ATTACHMENTS = {
    1: _attachment(1, ("contract-draft.pdf", ANA), ("contract-signed.pdf", BEN)),
    8: _attachment(8, ("report-inventory.xlsx", DEV)),
    13: _attachment(13, ("login-mockup.png", CHLOE)),
}


def _task(
    record_id: int,
    title: str,
    status: str,
    hours: float | None,
    due: str,
    notes: str,
    project: int,
    priority: str,
    assignee: dict[str, str] | None,
    tags: list[str],
) -> dict[int, Any]:
    return {
        1: f"2026-03-{record_id:02d}T09:00:00Z",
        RECORD_ID: record_id,
        6: title,
        7: status,
        8: hours,
        9: due,
        10: notes,
        11: status != "Done",
        12: project,
        13: priority,
        14: assignee,
        15: tags,
        16: _ATTACHMENTS.get(record_id, _EMPTY["file"]),
    }


# fmt: off
_TASK_ROWS = [
    _task(1, "Review vendor contract", "Open", 6.0, "2026-04-10", "Waiting on legal",
          1, "High", ANA, ["legal", "finance"]),
    _task(2, "Order new laptops", "Done", 2.5, "2026-03-20", "",
          1, "Low", ANA, ["purchasing"]),
    _task(3, "Update onboarding checklist", "Open", 4.0, "2026-05-01", "",
          2, "Medium", ELENA, ["hr"]),
    _task(4, "Plan team offsite", "Blocked", 12.0, "2026-06-15", "Budget not approved",
          2, "Low", ELENA, ["hr", "finance"]),
    _task(5, "Publish release notes", "Done", 1.5, "2026-03-28", "",
          3, "Low", DEV, ["docs"]),
    _task(6, "Review budget forecast", "Open", 8.0, "2026-04-22", "",
          1, "High", ANA, ["finance"]),
    _task(7, "Fix login redirect", "Blocked", 3.0, "2026-04-02", "Needs a test account",
          3, "High", BEN, ["backend", "security"]),
    _task(8, "Migrate reports", "Open", 16.0, "2026-07-01", "",
          3, "Medium", DEV, ["backend"]),
    _task(9, "Archive old projects", "Open", None, "", "No date set yet",
          2, "Low", None, []),
    _task(10, "Train new admins", "Done", 5.0, "2026-04-05", "",
          2, "Medium", ELENA, ["hr"]),
    _task(11, "Write security summary", "Open", 7.5, "2026-05-18", "",
          1, "Medium", BEN, ["security", "docs"]),
    _task(12, "Clean up unused fields", "Open", 2.0, "2026-04-30", "",
          3, "Low", DEV, []),
    _task(13, "Design portal login page", "Open", 10.0, "2026-08-14", "",
          4, "High", CHLOE, ["design"]),
    _task(14, "Renew O'Brien & Co retainer", "Open", 1.5, "2026-06-30", "",
          1, "Medium", ANA, ["legal", "finance"]),
    _task(15, "Review Terms AND Conditions", "Open", 3.0, "2026-07-20", "New wording from legal",
          4, "Medium", DEV, ["legal"]),
    _task(16, "Build invoice download", "Blocked", 20.0, "2026-09-04", "Waiting on the billing API",
          4, "High", BEN, ["backend"]),
    _task(17, "Write portal help pages", "Open", None, "", "",
          4, "Low", None, ["docs"]),
    _task(18, "Run accessibility audit", "Open", 6.0, "2026-09-25", "",
          4, "Medium", CHLOE, ["design", "compliance"]),
    _task(19, "Renew SSL certificates", "Done", 1.0, "2026-03-15", "",
          3, "High", BEN, ["security"]),
    _task(20, "Rotate API keys", "Open", 2.0, "2026-08-01", "",
          3, "High", BEN, ["security"]),
    _task(21, "Survey new hires", "Done", 3.0, "2026-04-18", "",
          2, "Low", ELENA, ["hr"]),
    _task(22, "Update expense policy", "Open", 4.0, "2026-08-28", "",
          1, "Low", ANA, ["finance"]),
    _task(23, "Load test the portal", "Open", 8.0, "2026-10-09", "",
          4, "Medium", DEV, ["backend"]),
    _task(24, "Book security training", "Open", 1.0, "2026-07-08", "",
          3, "Medium", ELENA, ["security", "hr"]),
]
_PROJECT_ROWS = [
    {RECORD_ID: 1, 6: "Operations", 7: 25000.0,
      8: "Active", 9: ANA, 10: "2026-01-05", 11: "2026-12-18"},
    {RECORD_ID: 2, 6: "People", 7: 12000.0,
      8: "Active", 9: ELENA, 10: "2026-02-02", 11: ""},
    {RECORD_ID: 3, 6: "Platform", 7: 40000.0,
      8: "Active", 9: BEN, 10: "2026-01-12", 11: "2026-09-30"},
    {RECORD_ID: 4, 6: "Customer Portal", 7: 60000.0,
      8: "Active", 9: DEV, 10: "2026-06-01", 11: "2026-12-04"},
    {RECORD_ID: 5, 6: "Data Warehouse", 7: 35000.0,
      8: "On Hold", 9: CHLOE, 10: "", 11: ""},
]
# fmt: on


def _entry(
    record_id: int,
    task: int,
    person: dict[str, str],
    day: str,
    hours: float,
    *,
    billable: bool = False,
    note: str = "",
) -> dict[int, Any]:
    return {RECORD_ID: record_id, 6: task, 8: person, 9: day, 10: hours, 11: billable, 12: note}


_TIME_ROWS = [
    _entry(1, 1, ANA, "2026-03-09", 2.0, note="First read"),
    _entry(2, 1, BEN, "2026-03-16", 1.5),
    _entry(3, 2, ANA, "2026-03-12", 2.5),
    _entry(4, 3, ELENA, "2026-03-18", 1.0),
    _entry(5, 4, ELENA, "2026-03-20", 2.0, note="Venue calls"),
    _entry(6, 5, DEV, "2026-03-26", 2.0),
    _entry(7, 6, ANA, "2026-03-27", 3.0),
    _entry(8, 7, BEN, "2026-03-30", 3.0, billable=True),
    _entry(9, 7, BEN, "2026-04-01", 1.5, billable=True, note="Still failing on staging"),
    _entry(10, 8, DEV, "2026-04-02", 4.0, billable=True),
    _entry(11, 8, DEV, "2026-04-09", 6.5, billable=True),
    _entry(12, 10, ELENA, "2026-04-03", 5.5),
    _entry(13, 11, BEN, "2026-04-14", 2.0),
    _entry(14, 12, DEV, "2026-04-15", 0.5),
    _entry(15, 13, CHLOE, "2026-06-08", 4.0, billable=True),
    _entry(16, 13, CHLOE, "2026-06-15", 3.5, billable=True),
    _entry(17, 14, ANA, "2026-06-10", 0.5),
    _entry(18, 15, DEV, "2026-06-11", 1.0, billable=True),
    _entry(19, 16, BEN, "2026-06-16", 5.0, billable=True),
    _entry(20, 16, BEN, "2026-06-23", 7.5, billable=True),
    _entry(21, 16, DEV, "2026-06-24", 2.0, billable=True, note="Pairing on the API"),
    _entry(22, 18, CHLOE, "2026-06-29", 2.0, billable=True),
    _entry(23, 19, BEN, "2026-03-13", 1.0),
    _entry(24, 20, BEN, "2026-07-06", 0.5),
    _entry(25, 21, ELENA, "2026-04-16", 3.5, note="More replies than expected"),
    _entry(26, 22, ANA, "2026-07-14", 1.0),
    _entry(27, 23, DEV, "2026-07-21", 2.5, billable=True),
    _entry(28, 24, ELENA, "2026-06-30", 0.5),
]


def _report(
    report_id: str,
    name: str,
    table_id: str,
    columns: list[int],
    *,
    report_type: str = "table",
    where: str = "",
    sort: list[dict[str, Any]] | None = None,
    incomplete: bool = False,
) -> dict[str, Any]:
    return {
        "id": report_id,
        "name": name,
        "type": report_type,
        "description": "",
        "query": {
            "tableId": table_id,
            "filter": where,
            "fields": columns,
            "sortBy": sort or [],
            "formulaFields": [],
        },
        "properties": {},
        "incomplete": incomplete,
        "usedCount": 0,
    }


def _link(
    parent: str, child: str, foreign_key: dict[str, Any], *lookups: dict[str, Any]
) -> dict[str, Any]:
    def short(field: dict[str, Any]) -> dict[str, Any]:
        return {"id": field["id"], "label": field["label"], "type": field["fieldType"]}

    return {
        "id": foreign_key["id"],
        "parentTableId": parent,
        "childTableId": child,
        "foreignKeyField": short(foreign_key),
        "isCrossApp": False,
        "lookupFields": [short(field) for field in lookups],
        "summaryFields": [],
    }


_RELATIONSHIPS = [
    _link(PROJECTS_ID, TASKS_ID, _TASK_FIELDS[8], _TASK_FIELDS[13]),
    _link(TASKS_ID, TIME_ID, _TIME_FIELDS[1], _TIME_FIELDS[2]),
]


def _sample_table(
    table_id: str, name: str, description: str, fields: list[dict[str, Any]], rows: list[Row]
) -> dict[str, Any]:
    return {
        "info": {
            "id": table_id,
            "name": name,
            "description": description,
            "keyFieldId": RECORD_ID,
        },
        "fields": fields,
        "rows": copy.deepcopy(rows),
    }


class _ApiError(Exception):
    def __init__(self, status: int, message: str, description: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.description = description


_OPERATORS = frozenset(
    {
        "EX", "XEX", "CT", "XCT", "SW", "XSW", "LT", "LTE", "GT", "GTE",
        "BF", "OBF", "AF", "OAF", "IR", "XIR", "HAS", "XHAS", "TV", "XTV",
    }
)  # fmt: skip
_NEGATED = frozenset({"XEX", "XCT", "XSW", "XIR", "XHAS", "XTV"})
_COMPARISONS: dict[str, Callable[[Any, Any], bool]] = {
    "LT": lambda a, b: a < b,
    "BF": lambda a, b: a < b,
    "LTE": lambda a, b: a <= b,
    "OBF": lambda a, b: a <= b,
    "GT": lambda a, b: a > b,
    "AF": lambda a, b: a > b,
    "GTE": lambda a, b: a >= b,
    "OAF": lambda a, b: a >= b,
}
_CLAUSE = re.compile(
    r"\{\s*([^.{}]*?)\s*\.\s*([A-Za-z]+)\s*\.\s*('(?:[^'{}\\]|\\[^{}])*'|[^{}]*?)\s*\}"
)
_ESCAPE = re.compile(r"\\(.)")

Predicate = Callable[[Row], bool]


def _text(value: Any) -> str:
    return "" if value is None else str(value).casefold()


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _to_date(value: Any) -> date | None:
    text = str(value)
    if text.casefold() == "today":
        return date.today()
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000, UTC).date()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _to_millis(value: Any) -> int | None:
    text = str(value)
    if text.isdigit():
        return int(text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp() * 1000)


def _comparable(value: Any, field_type: str) -> Any:
    """Return a value that sorts and compares correctly for its type, or None if it can't."""
    if _is_empty(value):
        return None
    if field_type in _NUMERIC_TYPES:
        try:
            return float(value)
        except ValueError:
            return None
    if field_type == "date":
        return _to_date(value)
    if field_type == "timestamp":
        return _to_millis(value)
    return _text(value)


def _equal(cell: Any, wanted: str, field_type: str) -> bool:
    if (
        field_type == "multitext"
    ):  # A match on any one value. Quickbase's docs describe HAS this way.
        return not cell if wanted == "" else _text(wanted) in map(_text, cell)
    if wanted == "":
        return _is_empty(cell)
    if field_type == "checkbox":
        return bool(cell) == (wanted.casefold() in {"true", "1"})
    left, right = _comparable(cell, field_type), _comparable(wanted, field_type)
    return left is not None and left == right


def _matches(operator: str, cell: Any, wanted: str, field_type: str) -> bool:
    if operator not in _OPERATORS:
        # Quickbase treats a lowercase or unknown operator as "contains", with no error.
        operator = "CT"
    if operator in {"IR", "XIR"}:
        raise _ApiError(400, f"The playground doesn't simulate the {operator} operator yet.")
    negate = operator in _NEGATED
    base = operator[1:] if negate else operator
    if base in {"EX", "TV", "HAS"}:
        result = _equal(cell, wanted, field_type)
    elif base == "CT":
        values = cell if field_type == "multitext" else [cell]
        result = any(_text(wanted) in _text(value) for value in values)
    elif base == "SW":
        result = _text(cell).startswith(_text(wanted))
    else:
        left, right = _comparable(cell, field_type), _comparable(wanted, field_type)
        result = left is not None and right is not None and _COMPARISONS[base](left, right)
    return not result if negate else result


class _WhereParser:
    """Parse a `where` string the way Quickbase does, including what it wrongly accepts.

    AND binding tighter than OR is an assumption: docs/api-notes.md doesn't confirm it. The SDK's
    `F` adds parentheses, so only hand-written strings can depend on it.
    """

    def __init__(self, text: str, fields: dict[int, dict[str, Any]]) -> None:
        self._text = text
        self._fields = fields
        self._pos = 0

    def parse(self) -> Predicate:
        predicate = self._any_of()
        self._skip_space()
        if self._pos != len(self._text):
            raise _ApiError(400, "Criteria is invalid.")
        return predicate

    def _skip_space(self) -> None:
        while self._pos < len(self._text) and self._text[self._pos].isspace():
            self._pos += 1

    def _take(self, token: str) -> bool:
        self._skip_space()
        if self._text.startswith(token, self._pos):
            self._pos += len(token)
            return True
        return False

    def _at_end_of_group(self) -> bool:
        self._skip_space()
        return self._pos == len(self._text) or self._text[self._pos] == ")"

    def _any_of(self) -> Predicate:
        options = [self._all_of()]
        while self._take("OR"):
            if self._at_end_of_group():  # Quickbase accepts a trailing join.
                break
            options.append(self._all_of())
        return lambda row: any(option(row) for option in options)

    def _all_of(self) -> Predicate:
        terms = [self._term()]
        while self._take("AND"):
            if self._at_end_of_group():
                break
            terms.append(self._term())
        return lambda row: all(term(row) for term in terms)

    def _term(self) -> Predicate:
        if self._take("("):
            inner = self._any_of()
            if not self._take(")"):
                raise _ApiError(400, "Criteria is invalid.")
            return inner
        self._skip_space()
        match = _CLAUSE.match(self._text, self._pos)
        if match is None:
            raise _ApiError(400, "Criteria is invalid.")
        self._pos = match.end()
        return self._clause(match[1], match[2], match[3])

    def _clause(self, field_ref: str, operator: str, raw: str) -> Predicate:
        # An unknown field, or a label in place of an ID, matches nothing and raises no error.
        field = self._fields.get(int(field_ref)) if field_ref.isdigit() else None
        if field is None:
            return lambda row: False
        field_id, field_type = field["id"], field["fieldType"]
        if field_type in _NOT_SIMULATED_IN_QUERIES:
            raise _ApiError(400, f"The playground doesn't simulate queries on {field_type} fields.")
        quoted = len(raw) >= 2 and raw.startswith("'") and raw.endswith("'")
        wanted = _ESCAPE.sub(r"\1", raw[1:-1] if quoted else raw)
        return lambda row: _matches(operator, row.get(field_id), wanted, field_type)


class SimulatedQuickbase:
    """An httpx handler for `httpx.MockTransport`. Each instance starts from sample data."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, Any]] = {
            TASKS_ID: _sample_table(
                TASKS_ID,
                "Tasks",
                "Work to do. Each task belongs to a project.",
                _TASK_FIELDS,
                _TASK_ROWS,
            ),
            PROJECTS_ID: _sample_table(
                PROJECTS_ID,
                "Projects",
                "The parent of Tasks, with rollups of its tasks.",
                _PROJECT_FIELDS,
                _PROJECT_ROWS,
            ),
            TIME_ID: _sample_table(
                TIME_ID,
                "Time Entries",
                "Hours logged against a task, which feed its Hours Logged.",
                _TIME_FIELDS,
                _TIME_ROWS,
            ),
        }
        self._refresh()
        self._reports: dict[str, list[dict[str, Any]]] = {
            TASKS_ID: [
                # Like Quickbase's own List All, this leaves out Record ID#.
                _report("1", "List All", TASKS_ID, [6, 7, 8, 9]),
                _report(
                    "2",
                    "Open tasks",
                    TASKS_ID,
                    [RECORD_ID, 6, 9],
                    where="{7.EX.'Open'}",
                    sort=[{"fieldId": 9, "order": "ASC"}],
                ),
                _report("3", "Hours by status", TASKS_ID, [7, 8], report_type="summary"),
                _report("4", "Tasks for a status", TASKS_ID, [6, 7], incomplete=True),
                _report(
                    "5",
                    "High priority work",
                    TASKS_ID,
                    [RECORD_ID, 6, 17, 14, 9],
                    where="{13.EX.'High'}AND{7.XEX.'Done'}",
                    sort=[{"fieldId": 9, "order": "ASC"}],
                ),
            ],
            PROJECTS_ID: [
                _report("1", "List All", PROJECTS_ID, [6, 7, 8, 9]),
                _report(
                    "2",
                    "Active projects",
                    PROJECTS_ID,
                    [RECORD_ID, 6, 7, 13],
                    where="{8.EX.'Active'}",
                    sort=[{"fieldId": 7, "order": "DESC"}],
                ),
                _report("3", "Budget by status", PROJECTS_ID, [8, 7], report_type="summary"),
            ],
            TIME_ID: [
                _report("1", "List All", TIME_ID, [6, 7, 8, 9, 10, 11]),
                _report(
                    "2",
                    "Billable time",
                    TIME_ID,
                    [RECORD_ID, 7, 8, 9, 10],
                    where="{11.EX.'true'}",
                    sort=[{"fieldId": 9, "order": "DESC"}],
                ),
            ],
        }

    def _refresh(self) -> None:
        """Work out lookups, summaries, and formulas again, as Quickbase does after a change."""
        tasks = self._tables[TASKS_ID]["rows"]
        projects = self._tables[PROJECTS_ID]["rows"]
        entries = self._tables[TIME_ID]["rows"]
        names = {project[RECORD_ID]: project[6] for project in projects}
        titles = {task[RECORD_ID]: task[6] for task in tasks}
        for entry in entries:
            entry[7] = titles.get(entry[6], "")
        for task in tasks:
            logged = sum((item[10] or 0.0 for item in entries if item[6] == task[RECORD_ID]), 0.0)
            task[17] = names.get(task[12], "")
            task[18] = logged
            task[19] = task[8] is not None and logged > task[8]
        for project in projects:
            owned = [task for task in tasks if task[12] == project[RECORD_ID]]
            project[12] = len(owned)
            project[13] = sum((task[8] or 0.0 for task in owned), 0.0)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        try:
            payload = self._route(request, path)
        except _ApiError as error:
            payload = {"message": error.message, "description": error.description}
            return httpx.Response(error.status, json=payload, headers={"qb-api-ray": "simulated"})
        # Quickbase saves the rows it can, so a row error still counts as a success status.
        partial = path == "/records" and payload["metadata"].get("lineErrors")
        return httpx.Response(
            207 if partial else 200, json=payload, headers={"qb-api-ray": "simulated"}
        )

    def _route(self, request: httpx.Request, path: str) -> Any:
        params = request.url.params
        route = (request.method, path)
        if route == ("POST", "/records/query"):
            return self._query(json.loads(request.content))
        if route == ("POST", "/records") and "mergeFieldId" not in (
            body := json.loads(request.content)
        ):
            return self._insert(body)
        if route == ("GET", "/tables"):
            self._app(params.get("appId"))
            return [table["info"] for table in self._tables.values()]
        if route == ("GET", "/fields"):
            return self._table(params.get("tableId"))["fields"]
        if route == ("GET", "/reports"):
            self._table(params.get("tableId"))
            return self._reports[params["tableId"]]
        if request.method == "GET" and (match := re.fullmatch(r"/apps/([^/]+)", path)):
            return self._app(match[1])
        if request.method == "GET" and (
            match := re.fullmatch(r"/tables/([^/]+)/relationships", path)
        ):
            return self._relationships(match[1], int(params.get("skip", 0)))
        if request.method == "POST" and (match := re.fullmatch(r"/reports/([^/]+)/run", path)):
            return self._run_report(match[1], params)
        raise _ApiError(
            403,
            f"The playground doesn't simulate {request.method} {path} yet.",
            "It covers reading records, schema, table reports, and adding records so far.",
        )

    def _app(self, app_id: str | None) -> dict[str, Any]:
        if app_id != APP_ID:
            raise _ApiError(401, "Invalid Authorization.")  # Unknown apps return 401, not 404.
        return _APP

    def _table(self, table_id: str | None) -> dict[str, Any]:
        if table_id is None:
            raise _ApiError(400, "Missing tableId.")
        if table_id not in self._tables:
            raise _ApiError(401, "Invalid Authorization.")
        return self._tables[table_id]

    def _relationships(self, table_id: str, skip: int) -> dict[str, Any]:
        """List only the relationships where this table is the child, as Quickbase does."""
        self._table(table_id)
        all_found = [link for link in _RELATIONSHIPS if link["childTableId"] == table_id]
        found = all_found[skip:]
        return {
            "relationships": found,
            "metadata": {
                "numRelationships": len(found),
                "skip": skip,
                "totalRelationships": len(all_found),
            },
        }

    def _select(
        self,
        table: dict[str, Any],
        where: str,
        columns: list[int],
        sort: list[dict[str, Any]],
        top: int,
        skip: int,
    ) -> dict[str, Any]:
        fields = {field["id"]: field for field in table["fields"]}
        predicate = _WhereParser(where, fields).parse() if where.strip() else None
        rows = [row for row in table["rows"] if predicate is None or predicate(row)]
        for item in reversed(sort):
            field = fields.get(item["fieldId"])
            if field is not None:
                rows = _sorted(rows, field, descending=item.get("order") == "DESC")
        chosen = [fields[column] for column in columns if column in fields]
        page = rows[skip : skip + top] if top else rows[skip:]
        return {
            "data": [
                {str(field["id"]): {"value": row.get(field["id"])} for field in chosen}
                for row in page
            ],
            "fields": [
                {"id": field["id"], "label": field["label"], "type": field["fieldType"]}
                for field in chosen
            ],
            "metadata": {
                "numFields": len(chosen),
                "numRecords": len(page),
                "skip": skip,
                "totalRecords": len(rows),
            },
        }

    def _query(self, body: dict[str, Any]) -> dict[str, Any]:
        if "from" not in body:
            raise _ApiError(400, "Missing from.")
        table = self._table(body["from"])
        options = body.get("options") or {}
        default = [field["id"] for field in table["fields"] if field["appearsByDefault"]]
        top = options.get("top", 0)
        result = self._select(
            table,
            body.get("where") or "",
            body.get("select") or default,
            body.get("sortBy") or [],
            top,
            options.get("skip", 0),
        )
        if "top" in options:
            result["metadata"]["top"] = top
        return result

    def _insert(self, body: dict[str, Any]) -> dict[str, Any]:
        table = self._table(body.get("to"))
        if not body.get("data"):
            raise _ApiError(400, "Missing data.")
        fields = {field["id"]: field for field in table["fields"]}
        created: list[int] = []
        errors: dict[str, list[str]] = {}
        for line, row in enumerate(body["data"], start=1):
            for key in row:
                _refuse_unsimulated_write(fields.get(int(key)))
            messages = [
                message
                for key, cell in row.items()
                if (message := _write_problem(fields.get(int(key)), key, cell["value"]))
            ]
            if messages:
                errors[str(line)] = messages
                continue
            record_id = max(existing[RECORD_ID] for existing in table["rows"]) + 1
            values = {int(key): cell["value"] for key, cell in row.items()}
            defaults = {field_id: _empty_value(field) for field_id, field in fields.items()}
            created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            stamp = {1: created_at} if 1 in fields else {}
            table["rows"].append({**defaults, **stamp, **values, RECORD_ID: record_id})
            created.append(record_id)
        self._refresh()
        metadata: dict[str, Any] = {
            "createdRecordIds": created,
            "updatedRecordIds": [],
            "unchangedRecordIds": [],
            "totalNumberOfRecordsProcessed": len(body["data"]),
        }
        if errors:
            metadata["lineErrors"] = errors
        return {"data": [], "metadata": metadata}

    def _run_report(self, report_id: str, params: httpx.QueryParams) -> dict[str, Any]:
        table = self._table(params.get("tableId"))
        reports = self._reports[params["tableId"]]
        report = next(
            (item for item in reports if report_id.isdigit() and int(item["id"]) == int(report_id)),
            None,
        )
        if report is None:
            raise _ApiError(404, "Report not found.")
        report["usedCount"] += 1  # Quickbase counts the run even when it fails.
        if report["incomplete"]:
            raise _ApiError(400, "Incomplete reports not supported")
        top, skip = int(params.get("top", 0)), int(params.get("skip", 0))
        if ("top" in params and top <= 0) or skip < 0:
            raise _ApiError(400, "Invalid top or skip.")
        if report["type"] != "table":
            return _summary(table, report["query"]["fields"], skip)
        query = report["query"]
        return self._select(table, query["filter"], query["fields"], query["sortBy"], top, skip)


def _write_problem(field: dict[str, Any] | None, key: str, value: Any) -> str | None:
    """Return Quickbase's row error for a value, or None if it can be written."""
    if field is None:
        return f'Field with ID "{key}" not found.'
    if field["mode"]:
        # Quickbase reports this as a row error, but its wording here isn't recorded.
        return f"The field with ID '{key}' can't be written, because it's a {field['mode']} field."
    choices = field["properties"].get("choices")
    if choices and not field["properties"].get("allowNewChoices", True) and value not in choices:
        return f"The field with ID '{key}' has an invalid choice."  # Quickbase's words.
    if field["fieldType"] in _NUMERIC_TYPES and not isinstance(value, int | float | None):
        return f'Incompatible value for field with ID "{key}".'
    return None


def _refuse_unsimulated_write(field: dict[str, Any] | None) -> None:
    if field is not None and field["fieldType"] in {"user", "file"}:
        raise _ApiError(
            403,
            f"The playground doesn't simulate writing to {field['fieldType']} fields yet.",
            "It covers reading records, schema, table reports, and adding records so far.",
        )


def _empty_value(field: dict[str, Any]) -> Any:
    return copy.deepcopy(_EMPTY.get(field["fieldType"], ""))


def _sorted(rows: list[Row], field: dict[str, Any], *, descending: bool) -> list[Row]:
    field_id, field_type = field["id"], field["fieldType"]
    filled = [row for row in rows if not _is_empty(row.get(field_id))]
    empty = [row for row in rows if _is_empty(row.get(field_id))]
    filled.sort(key=lambda row: _comparable(row[field_id], field_type), reverse=descending)
    return filled + empty


def _summary(table: dict[str, Any], columns: list[int], skip: int) -> dict[str, Any]:
    """Summary results number their columns by position, starting at 2, whatever the fields are."""
    group, value = columns
    labels = {field["id"]: field["label"] for field in table["fields"]}
    totals: dict[str, float] = {}
    for row in table["rows"]:
        totals[row[group]] = totals.get(row[group], 0.0) + (row[value] or 0.0)
    rows = list(totals.items())
    page = rows[skip:]
    return {
        "data": [{"2": {"value": name}, "3": {"value": total}} for name, total in page],
        "fields": [
            {"id": 2, "label": labels[group], "type": "text"},
            {"id": 3, "label": f"Total {labels[value]}", "type": "numeric"},
        ],
        "metadata": {
            "numFields": 2,
            "numRecords": len(page),
            "skip": skip,
            "totalRecords": len(rows),
        },
    }


def _shown(value: Any) -> Any:
    """Return a cell as the sample data panel should show it, which is what a person would read."""
    if isinstance(value, list):
        return ", ".join(value)
    if isinstance(value, dict):
        versions = value.get("versions")
        return versions[-1]["fileName"] if versions else value.get("name", "")
    return value


def _panel_order(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Start with what identifies a row, because a long timestamp would push it off the screen."""
    return sorted(fields, key=lambda field: {RECORD_ID: 0, 1: 2}.get(field["id"], 1))


def sample_tables() -> list[dict[str, Any]]:
    """The sample data as plain rows, to show beside the editor."""
    samples = []
    for table in SimulatedQuickbase()._tables.values():
        fields = _panel_order(table["fields"])
        samples.append(
            {
                "name": table["info"]["name"],
                "id": table["info"]["id"],
                "description": table["info"]["description"],
                "columns": [field["label"] for field in fields],
                "ids": [field["id"] for field in fields],
                "rows": [
                    [_shown(row.get(field["id"])) for field in fields] for row in table["rows"]
                ],
            }
        )
    return samples
