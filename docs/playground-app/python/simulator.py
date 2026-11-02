"""A simulated Quickbase API, so the docs playground needs no account, token, or server.

It runs inside the visitor's browser and holds a small sample app. It only covers reading so far.
Its quirks copy what's recorded in docs/api-notes.md, including the mistakes Quickbase accepts,
because those are what the SDK protects against.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

APP_ID = "bq1234567"
TASKS_ID = "bqtasks"
PROJECTS_ID = "bqprojects"
RECORD_ID = 3

_APP = {
    "id": APP_ID,
    "name": "Project Tracker (sample)",
    "description": "Sample data for the pyquickbase playground.",
    "timeZone": "(UTC) Coordinated Universal Time",
    "dateFormat": "MM-DD-YYYY",
}
_STATUSES = ["Open", "Blocked", "Done"]


def _field(
    field_id: int, label: str, field_type: str, *, default: bool = True, **properties: Any
) -> dict[str, Any]:
    return {
        "id": field_id,
        "label": label,
        "fieldType": field_type,
        "mode": "",
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
]
_PROJECT_FIELDS = [
    _field(RECORD_ID, "Record ID#", "recordid", default=False),
    _field(6, "Name", "text"),
    _field(7, "Budget", "numeric"),
]


def _task(
    record_id: int,
    title: str,
    status: str,
    hours: float | None,
    due: str,
    notes: str,
    project: int,
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
    }


_TASK_ROWS = [
    _task(1, "Review vendor contract", "Open", 6.0, "2026-04-10", "Waiting on legal", 1),
    _task(2, "Order new laptops", "Done", 2.5, "2026-03-20", "", 1),
    _task(3, "Update onboarding checklist", "Open", 4.0, "2026-05-01", "", 2),
    _task(4, "Plan team offsite", "Blocked", 12.0, "2026-06-15", "Budget not approved", 2),
    _task(5, "Publish release notes", "Done", 1.5, "2026-03-28", "", 3),
    _task(6, "Review budget forecast", "Open", 8.0, "2026-04-22", "", 1),
    _task(7, "Fix login redirect", "Blocked", 3.0, "2026-04-02", "Needs a test account", 3),
    _task(8, "Migrate reports", "Open", 16.0, "2026-07-01", "", 3),
    _task(9, "Archive old projects", "Open", None, "", "No date set yet", 2),
    _task(10, "Train new admins", "Done", 5.0, "2026-04-05", "", 2),
    _task(11, "Write security summary", "Open", 7.5, "2026-05-18", "", 1),
    _task(12, "Clean up unused fields", "Open", 2.0, "2026-04-30", "", 3),
]
_PROJECT_ROWS = [
    {RECORD_ID: 1, 6: "Operations", 7: 25000.0},
    {RECORD_ID: 2, 6: "People", 7: 12000.0},
    {RECORD_ID: 3, 6: "Platform", 7: 40000.0},
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

Row = dict[int, Any]
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
    if field_type in {"numeric", "recordid"}:
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
        result = _text(wanted) in _text(cell)
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
        quoted = len(raw) >= 2 and raw.startswith("'") and raw.endswith("'")
        wanted = _ESCAPE.sub(r"\1", raw[1:-1] if quoted else raw)
        return lambda row: _matches(operator, row.get(field_id), wanted, field_type)


class SimulatedQuickbase:
    """An httpx handler for `httpx.MockTransport`. Each instance starts from sample data."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, Any]] = {
            TASKS_ID: {
                "info": {
                    "id": TASKS_ID,
                    "name": "Tasks",
                    "description": "",
                    "keyFieldId": RECORD_ID,
                },
                "fields": _TASK_FIELDS,
                "rows": _TASK_ROWS,
            },
            PROJECTS_ID: {
                "info": {
                    "id": PROJECTS_ID,
                    "name": "Projects",
                    "description": "",
                    "keyFieldId": RECORD_ID,
                },
                "fields": _PROJECT_FIELDS,
                "rows": _PROJECT_ROWS,
            },
        }
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
            ],
            PROJECTS_ID: [_report("1", "List All", PROJECTS_ID, [6, 7])],
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        try:
            payload = self._route(request, path)
        except _ApiError as error:
            payload = {"message": error.message, "description": error.description}
            return httpx.Response(error.status, json=payload, headers={"qb-api-ray": "simulated"})
        return httpx.Response(200, json=payload, headers={"qb-api-ray": "simulated"})

    def _route(self, request: httpx.Request, path: str) -> Any:
        params = request.url.params
        route = (request.method, path)
        if route == ("POST", "/records/query"):
            return self._query(json.loads(request.content))
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
            "It covers reading records, schema, and table reports so far.",
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
        self._table(table_id)
        link = {
            "id": 12,
            "parentTableId": PROJECTS_ID,
            "childTableId": TASKS_ID,
            "foreignKeyField": {"id": 12, "label": "Related Project", "type": "numeric"},
            "isCrossApp": False,
            "lookupFields": [],
            "summaryFields": [],
        }
        found = [link][skip:]
        return {
            "relationships": found,
            "metadata": {"numRelationships": len(found), "skip": skip, "totalRelationships": 1},
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
            return _summary_by_status(table, skip)
        query = report["query"]
        return self._select(table, query["filter"], query["fields"], query["sortBy"], top, skip)


def _sorted(rows: list[Row], field: dict[str, Any], *, descending: bool) -> list[Row]:
    field_id, field_type = field["id"], field["fieldType"]
    filled = [row for row in rows if not _is_empty(row.get(field_id))]
    empty = [row for row in rows if _is_empty(row.get(field_id))]
    filled.sort(key=lambda row: _comparable(row[field_id], field_type), reverse=descending)
    return filled + empty


def _summary_by_status(table: dict[str, Any], skip: int) -> dict[str, Any]:
    """Summary results number their columns by position, starting at 2, whatever the fields are."""
    totals: dict[str, float] = {}
    for row in table["rows"]:
        totals[row[7]] = totals.get(row[7], 0.0) + (row[8] or 0.0)
    rows = list(totals.items())
    page = rows[skip:]
    return {
        "data": [{"2": {"value": status}, "3": {"value": hours}} for status, hours in page],
        "fields": [
            {"id": 2, "label": "Status", "type": "text"},
            {"id": 3, "label": "Total Hours", "type": "numeric"},
        ],
        "metadata": {
            "numFields": 2,
            "numRecords": len(page),
            "skip": skip,
            "totalRecords": len(rows),
        },
    }


def sample_tables() -> list[dict[str, Any]]:
    """The sample data as plain rows, to show beside the editor."""
    return [
        {
            "name": info["name"],
            "id": info["id"],
            "columns": [field["label"] for field in fields],
            "ids": [field["id"] for field in fields],
            "rows": [[row.get(field["id"]) for field in fields] for row in rows],
        }
        for info, fields, rows in (
            ({"name": "Tasks", "id": TASKS_ID}, _TASK_FIELDS, _TASK_ROWS),
            ({"name": "Projects", "id": PROJECTS_ID}, _PROJECT_FIELDS, _PROJECT_ROWS),
        )
    ]
