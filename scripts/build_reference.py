"""Write the two-column API reference pages, ready for `mkdocs build`.

uv run scripts/build_reference.py

Each page is written as `docs/reference/<name>.md` for the site, and `docs/reference/<name>.txt` as
plain Markdown for AI tools and for copying. Signatures and text come from the SDK, and examples
from `docs_src/reference/`, where each is written for the async client and the sync version derived
from it. tests/live/test_reference_examples.py runs them and checks their output. Like
build_llms.py, this uses only the standard library and `inspect`, so it doesn't depend on which tool
builds the site. None of what it writes is committed.

The build stops if a public name isn't on a page, if a public method, argument, or attribute has no
description, if something has no example and isn't listed as not needing one, or if the clients
differ.
"""

import dataclasses
import inspect
import re
from pathlib import Path
from typing import Any, NamedTuple

import build_llms

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "reference"
EXAMPLES = ROOT / "docs_src" / "reference"
CONSTRUCTOR = "__init__"
# Methods that the async client names differently.
ASYNC_NAMES = {"close": "aclose"}
OVERVIEW = "../reference.md"

# Classes you make yourself, so they show a constructor rather than a list of attributes.
HAS_PUBLIC_CONSTRUCTOR = {
    "F",
    "FileUpload",
    "Period",
    "WriteRules",
    "WriteAccess",
    "ReadRules",
    "ReadAccess",
    "RetryPolicy",
}
# Public so that the SDK can call them across modules, and of no use from outside it.
INTERNAL_METHODS = {"Expression.compile"}
# Type aliases have no docstrings of their own.
ALIASES = {
    "FieldRef": "A field, by label (`str`) or ID (`int`).",
    "FieldRefs": "One or more fields, by label, ID, or `F`, as `select` and `order_by` take them.",
    "PeriodUnit": "How much time each group of a `Period` covers.",
    "SummaryGroups": "What `Table.summarize()` can group by.",
    "WriteAction": "What kind of write a `WriteRequest` is.",
    "ReadAction": "What kind of read a `ReadRequest` is.",
    "OperationName": "What `Table.operation()` can offer a model, each running the table or "
    "report method of the same name.",
    "ToolErrorCode": "The `code` in what `tool_error()` returns. Codes don't change within 1.x, so "
    "a tool can act on them.",
    "ReadPolicy": "A function that decides on each read. Only a return value of `True` lets one "
    "through. `ReadRules` is one, or you can write your own.",
    "WritePolicy": "A function that decides on each write. Only a return value of `True` lets one "
    "through. `WriteRules` is one, or you can write your own.",
    "OrderBy": "What a generated class's methods take as `order_by`, which is fields rather than "
    "labels, so that renaming a label can't break it.",
}
# What can't be shown happening in an example, and why.
NO_EXAMPLE = {
    "NetworkError": "`RequestTimeoutError` shows catching this",
    "InvalidResponseError": "only Quickbase returning something unexpected raises this",
    "PermissionDeniedError": "this needs a second token with less access",
    "WritePermissionError": "this needs a second token with less access",
    "RateLimitError": "this needs a real burst of requests",
    "ServerError": "only a failure at Quickbase raises this",
}
# Shown on the page, but not run by the live tests, because of what it needs.
NOT_RUN_LIVE = {
    "report/summary_rows": "A summary report can only be built in the Quickbase interface. "
    "tests/_async/live/test_sdk.py tests this method.",
}


class Group(NamedTuple):
    title: str
    anchor: str
    guide: tuple[str, str] | None
    members: tuple[str, ...]


def _setup(last_line: str) -> tuple[str, str]:
    """Return the setup code for each client, narrow enough for the code column."""
    body = f'    realm="acme", token="your_user_token"\n) as qb:\n    {last_line}'
    return (
        f"from pyquickbase import Quickbase\n\nwith Quickbase(\n{body}",
        f"from pyquickbase import AsyncQuickbase\n\nasync with AsyncQuickbase(\n{body}",
    )


def _typed_setup() -> tuple[str, str]:
    """Return the setup code for a generated class, which needs importing as well."""
    sync_code, async_code = _setup('tasks = qb.app("bq1234567").typed(JobTasks)')
    first = "from myapp.tables import JobTasks\n"
    return first + sync_code, first + async_code


class Resource(NamedTuple):
    """A page about one class, with an example for each of its methods."""

    name: str
    slug: str
    sync_class: type
    async_class: type
    intro: str
    note: str
    setup: str
    async_setup: str
    groups: tuple[Group, ...]


class SymbolPage(NamedTuple):
    """A page about several public names, with an example for each name."""

    name: str
    slug: str
    intro: str
    note: str
    setup: str
    async_setup: str
    groups: tuple[Group, ...]


_LIVE_TEST = "A live test runs every example with both clients and checks its output."
_PROJECTS = "`projects` is a table named `Job Projects`, with a `Name` field."
_JOB_TASKS = (
    "`Job Tasks` has `Title`, `Status`, `Hours`, `Due Date`, `Code`, which is unique, and "
    "`Attachment`. It starts with three records, and each example starts from those again."
)
_READING = ("Reading records", "../guides/reading.md")
_WRITING = ("Writing records", "../guides/writing.md")
_SCHEMA = ("Changing the schema", "../guides/schema.md")
_CLIENTS = ("Configuring clients", "../guides/clients.md")
_CODEGEN = ("Generated classes", "../guides/codegen.md")
_FILES = ("File attachments", "../guides/files.md")
_FORMULAS = ("Formulas", "../guides/formulas.md")
_AGENTS = ("Using pyquickbase from an AI agent", "../guides/agents.md")
_ERRORS = ("Handling errors", "../guides/errors.md")

_TABLE_LINE = 'tasks = qb.app("bq1234567").table("Job Tasks")'
_SEARCH_LINES = (
    '\n    search = await tasks.operation(\n        "query", fields=["Title", "Status"]\n    )'
)

RESOURCES = (
    Resource(
        "Quickbase",
        "quickbase",
        pyquickbase.Quickbase,
        pyquickbase.AsyncQuickbase,
        "`Quickbase` is the client that everything else starts from. Use it as a context manager, "
        "so that its connections get closed. `AsyncQuickbase` takes the same options, except that "
        "`http_client` is an `httpx.AsyncClient`, and has the same methods, which you `await` "
        "where the examples show it.",
        f"The examples below use `qb`, made as shown here. {_LIVE_TEST}",
        *_setup("print(qb.realm)"),
        (
            Group(
                "Create and close a client",
                "create-and-close",
                _CLIENTS,
                (CONSTRUCTOR, "from_env", "close"),
            ),
            Group(
                "Use a client",
                "use-a-client",
                _CLIENTS,
                ("app", "realm", "clear_schema_cache", "request"),
            ),
        ),
    ),
    Resource(
        "App",
        "app",
        pyquickbase.App,
        pyquickbase.AsyncApp,
        "An `App` lists an app's details and gives you its tables. Get one from "
        "`Quickbase.app()`. Getting one doesn't contact Quickbase, so an ID that doesn't exist "
        "only fails on first use.",
        "The examples below use `app`, made as shown here, in an app that has a table named "
        f"`Job Tasks` with three records. {_LIVE_TEST} `AsyncApp` has the same methods, which you "
        "`await`, but `table()` and `typed()` are called without it.",
        *_setup('app = qb.app("bq1234567")'),
        (
            Group(
                "Look up the app's details",
                "app-details",
                _READING,
                ("id", "info", "tables", "roles"),
            ),
            Group("Get a table", "get-a-table", _CODEGEN, ("table", "typed")),
            Group("Create tables", "create-tables", _SCHEMA, ("ensure_table", "create_table")),
        ),
    ),
    Resource(
        "Table",
        "table",
        pyquickbase.Table,
        pyquickbase.AsyncTable,
        "A `Table` reads and writes one table's records and changes its fields. Get one from "
        "`App.table()`, by name or ID. Getting one doesn't contact Quickbase, so a table that "
        "doesn't exist raises `UnknownTableError` on first use.",
        f"The examples below use `tasks`, made as shown here. {_JOB_TASKS} {_PROJECTS} "
        f"{_LIVE_TEST} In the async client, `AsyncTable` has the same methods, which you `await`, "
        "but `query()` and `report()` are called without it.",
        *_setup(_TABLE_LINE),
        (
            Group(
                "Read records",
                "read-records",
                _READING,
                ("query", "query_page", "first", "get", "count"),
            ),
            Group(
                "Write records", "write-records", _WRITING, ("insert", "update", "upsert", "delete")
            ),
            Group(
                "Preview writes", "preview-writes", _WRITING, ("preview_update", "preview_delete")
            ),
            Group(
                "Summarize and calculate",
                "summarize-and-calculate",
                _FORMULAS,
                ("summarize", "run_formula"),
            ),
            Group(
                "Look up the table's details",
                "table-details",
                _READING,
                ("info", "fields", "relationships", "reports", "report", "resolve_field_ids"),
            ),
            Group("Work with files", "files", _FILES, ("download_file", "delete_file_version")),
            Group("Offer an operation to a model", "operations", _AGENTS, ("operation",)),
            Group(
                "Change the schema",
                "schema",
                _SCHEMA,
                (
                    "create_field",
                    "update_field",
                    "delete_fields",
                    "ensure_field",
                    "create_relationship",
                    "ensure_relationship",
                    "ensure_lookup_field",
                    "ensure_summary_field",
                    "drop",
                ),
            ),
        ),
    ),
    Resource(
        "Report",
        "report",
        pyquickbase.Report,
        pyquickbase.AsyncReport,
        "A `Report` runs a report that was built in the Quickbase interface. Get one from "
        "`Table.report()`, by name or ID. Getting one doesn't contact Quickbase, so a report that "
        "doesn't exist raises `UnknownReportError` on first use.",
        f"The examples below use `tasks`, made as shown here. Every new table has a report named "
        f"`List All`. {_LIVE_TEST} The exception is `summary_rows()`, because a summary report can "
        "only be built in the interface. `AsyncReport` has the same methods, which you `await`, "
        "but `run()` is called without it.",
        *_setup(_TABLE_LINE),
        (
            Group("Run a report", "run-a-report", _READING, ("run", "run_page", "summary_rows")),
            Group("Look up the report's details", "report-details", _READING, ("info",)),
        ),
    ),
    Resource(
        "Operation",
        "operation",
        pyquickbase.Operation,
        pyquickbase.AsyncOperation,
        "An `Operation` is one table operation offered to a model as a tool: a JSON Schema of "
        "the arguments it may send, and `run()`, which checks them against the same lists before "
        "calling the table. Get one from `Table.operation()`.",
        f"The examples below use `search`, made as shown here. {_JOB_TASKS} {_LIVE_TEST}",
        _setup(_TABLE_LINE)[0] + _SEARCH_LINES.replace("await ", ""),
        _setup(_TABLE_LINE)[1] + _SEARCH_LINES,
        (
            Group("Describe it to a model", "describe-it", _AGENTS, ("schema", "name")),
            Group("Run it", "run-it", _AGENTS, ("run",)),
        ),
    ),
    Resource(
        "TypedTable",
        "typed-table",
        pyquickbase.TypedTable,
        pyquickbase.AsyncTypedTable,
        "A `TypedTable` reads and writes a table through a class from `pyquickbase codegen`, so "
        "your editor and type checker know every field. Get one from `App.typed()`. The class is "
        "compared with the table on first use, and it uses field IDs, so renaming a label can't "
        "break it.",
        "The examples below use `tasks`, made as shown here, and `JobTasks`, the class generated "
        f"for `Job Tasks`. {_JOB_TASKS} {_LIVE_TEST} `AsyncTypedTable` has the same methods, which "
        "you `await`, but `query()` is called without it, and `table` is a property.",
        *_typed_setup(),
        (
            Group(
                "Read records",
                "read-records",
                _CODEGEN,
                ("query", "query_page", "first", "get", "count"),
            ),
            Group(
                "Write records", "write-records", _CODEGEN, ("insert", "update", "upsert", "delete")
            ),
            Group(
                "Preview writes", "preview-writes", _WRITING, ("preview_update", "preview_delete")
            ),
            Group("Summarize", "summaries", _READING, ("summarize",)),
            Group("Check the class", "check-the-class", _CODEGEN, ("check_schema", "table")),
        ),
    ),
)

_SYMBOL_NOTE = (
    "The examples below use `tasks`, made as shown here, and `app`, which is "
    f'`qb.app("bq1234567")`. {_JOB_TASKS} {_LIVE_TEST}'
)
SYMBOL_PAGES = (
    SymbolPage(
        "Queries",
        "queries",
        "Conditions choose which records a query, count, or delete applies to. Build them with "
        "`F`, which escapes values for you, or with `where_from_dict()` for plain data from a "
        "model or a web form.",
        _SYMBOL_NOTE,
        *_setup(_TABLE_LINE),
        (
            Group("Build a condition", "build-a-condition", _READING, ("F", "Expression")),
            Group(
                "Conditions from plain data",
                "plain-data",
                _AGENTS,
                ("where_from_dict", "where_schema"),
            ),
            Group(
                "Check a hand-written condition",
                "hand-written",
                _READING,
                ("parse_where", "WhereCondition"),
            ),
            Group("Ways to name a field", "naming-fields", _READING, ("FieldRef", "FieldRefs")),
        ),
    ),
    SymbolPage(
        "Records and results",
        "records",
        "These are what reads and writes give you back, and what you pass to write a file.",
        _SYMBOL_NOTE,
        *_setup(_TABLE_LINE),
        (
            Group("Read results", "read-results", _READING, ("Record", "RecordIterator", "Page")),
            Group("Write results", "write-results", _WRITING, ("WriteResult", "RowError")),
            Group("Files", "files", _FILES, ("FileUpload", "FileDownload")),
            Group("Formula results", "formula-results", _FORMULAS, ("FormulaResult",)),
            Group(
                "Previews of writes",
                "previews",
                _WRITING,
                ("UpdatePreview", "RecordChange", "FieldChange"),
            ),
            Group(
                "Summaries",
                "summaries",
                _READING,
                ("SummaryRow", "Period", "PeriodUnit", "SummaryGroups"),
            ),
        ),
    ),
    SymbolPage(
        "Metadata",
        "metadata",
        "These describe an app and what's in it. The client caches them for five minutes by "
        "default, and each holds everything Quickbase returned in `raw`.",
        f"{_SYMBOL_NOTE} {_PROJECTS}",
        *_setup(_TABLE_LINE),
        (
            Group("Apps", "apps", _READING, ("AppInfo", "Role")),
            Group(
                "Tables and reports", "tables-and-reports", _READING, ("TableInfo", "ReportInfo")
            ),
            Group("Fields and relationships", "fields", _SCHEMA, ("Field", "Relationship")),
        ),
    ),
    SymbolPage(
        "Limits and retries",
        "limits",
        "Pass these to `Quickbase()` to limit what a client can write and read, and to choose how "
        "it retries. These limits protect against bugs in your code and mistakes by a model, so "
        "give your token access to as little as possible as well.",
        "Each example makes its own client from the environment, with `bqtasks123` standing for "
        f"the ID of the `Job Tasks` table. {_LIVE_TEST}",
        *_setup(_TABLE_LINE),
        (
            Group(
                "Limit writes by table, action, and field",
                "table-rules",
                _CLIENTS,
                ("WriteRules", "WriteAccess"),
            ),
            Group(
                "Limit reads by table, field, and number of records",
                "read-rules",
                _CLIENTS,
                ("ReadRules", "ReadAccess"),
            ),
            Group(
                "Write your own policy",
                "own-policy",
                _CLIENTS,
                (
                    "WriteRequest",
                    "ReadRequest",
                    "all_of",
                    "WritePolicy",
                    "ReadPolicy",
                    "WriteAction",
                    "ReadAction",
                ),
            ),
            Group("Retries", "retries", _CLIENTS, ("RetryPolicy",)),
        ),
    ),
    SymbolPage(
        "Tools for models",
        "tools",
        "These turn results and errors into plain data for the result of a model's tool call, in "
        "the same shapes and value formats whatever the call. They work with any tool-calling "
        "framework, as `json.dumps()` always accepts what they return. `Table.operation()` makes "
        "the tools themselves.",
        "Each example makes its own client from the environment, with `bqtasks123` standing for "
        f"the ID of the `Job Tasks` table. {_JOB_TASKS} {_LIVE_TEST}",
        *_setup(_TABLE_LINE),
        (
            Group("Results", "results", _AGENTS, ("tool_result",)),
            Group("Errors", "errors", _AGENTS, ("tool_error", "ToolErrorCode")),
            Group("Operations", "operations", _AGENTS, ("OperationName",)),
        ),
    ),
    SymbolPage(
        "Generated classes",
        "generated",
        "These support the classes from `pyquickbase codegen`, and you rarely need them directly. "
        "`TypedTable` has its own page.",
        "The examples below use `app`, as on the `App` page, and `JobTasks`, the class generated "
        f"for `Job Tasks`. {_JOB_TASKS} {_LIVE_TEST}",
        *_setup('app = qb.app("bq1234567")'),
        (
            Group("Generated records", "records", _CODEGEN, ("TypedRecord", "Column")),
            Group("Queries", "queries", _CODEGEN, ("TypedField", "OrderBy")),
            Group("Results", "results", _CODEGEN, ("TypedIterator", "TypedPage")),
            Group("Writes", "writes", _CODEGEN, ("Row", "Unset")),
        ),
    ),
    SymbolPage(
        "Errors",
        "errors",
        "Everything the SDK raises derives from `QuickbaseError`. Errors that mean a request "
        "wasn't sent come first, then the ones for a request that failed. A timed-out write may "
        "still have been applied.",
        _SYMBOL_NOTE,
        *_setup(_TABLE_LINE),
        (
            Group("The base class", "base", _ERRORS, ("QuickbaseError",)),
            Group(
                "Mistakes caught before sending",
                "before-sending",
                _ERRORS,
                (
                    "ConfigurationError",
                    "QueryError",
                    "InvalidValueError",
                    "RequestRefusedError",
                    "WriteRefusedError",
                    "ReadRefusedError",
                ),
            ),
            Group(
                "Names that don't match",
                "names",
                _ERRORS,
                (
                    "UnknownTableError",
                    "UnknownFieldError",
                    "UnknownReportError",
                    "UnsupportedReportError",
                    "SchemaMismatchError",
                ),
            ),
            Group("Writes", "writes", _WRITING, ("WriteError", "WritePermissionError")),
            Group(
                "Responses from Quickbase",
                "responses",
                _ERRORS,
                (
                    "APIError",
                    "BadRequestError",
                    "AuthenticationError",
                    "PermissionDeniedError",
                    "NotFoundError",
                    "RateLimitError",
                    "ServerError",
                ),
            ),
            Group(
                "Connection failures",
                "connections",
                _ERRORS,
                ("NetworkError", "RequestTimeoutError", "InvalidResponseError"),
            ),
        ),
    ),
)
ALL_PAGES: tuple[Resource | SymbolPage, ...] = (*RESOURCES, *SYMBOL_PAGES)
CLASS_PAGES = {resource.name: f"{resource.slug}.md" for resource in RESOURCES}


def _name_links() -> dict[str, str]:
    """Return where each public name is documented, relative to a reference page."""
    links = {page.name: f"{page.slug}.md" for page in RESOURCES}
    for page in SYMBOL_PAGES:
        for group in page.groups:
            for name in group.members:
                links[name] = f"{page.slug}.md#{name}"
    # These are described with their sync versions, or with the class they belong to.
    links["AsyncQuickbase"] = links["Quickbase"]
    links["AsyncApp"] = links["App"]
    links["AsyncTable"] = links["Table"]
    links["AsyncReport"] = links["Report"]
    links["AsyncOperation"] = links["Operation"]
    links["AsyncTypedTable"] = links["TypedTable"]
    links["AsyncRecordIterator"] = links["RecordIterator"]
    links["AsyncTypedIterator"] = links["TypedIterator"]
    links["UNSET"] = links["Unset"]
    return links


NAME_LINKS = _name_links()

_SECTIONS = {"Args:": "args", "Attributes:": "attrs", "Returns:": "returns", "Raises:": "raises"}
_ENTRY = re.compile(r"\*{0,2}(\w+): (.*)")
_NAME = re.compile(r"\b[A-Za-z_]\w*\b")
_CALL = re.compile(r"`(\w+)\(\)`")
_SEE = re.compile(r"See `(\w+)`")
_PUBLIC_CODE = re.compile(r"`([A-Za-z]\w*)`")
_QUALIFIED = re.compile(r"`(" + "|".join(CLASS_PAGES) + r")\.(\w+)(\(\))?`")
_SYNC_WORDS = (
    ("await ", ""),
    ("async for ", "for "),
    ("async with ", "with "),
    ("AsyncQuickbase", "Quickbase"),
    (".aclose(", ".close("),
)


class Docstring(NamedTuple):
    summary: str
    prose: list[str]
    args: dict[str, str]
    attrs: dict[str, str]
    returns: str
    raises: list[tuple[str, str]]


class Parameter(NamedTuple):
    name: str
    annotation: str
    default: str | None
    required: bool
    description: str


class Method(NamedTuple):
    name: str
    anchor: str
    heading: str
    is_property: bool
    signature: str
    returns: str
    doc: Docstring
    parameters: list[Parameter]
    sync_code: str
    async_code: str
    output: str


class Symbol(NamedTuple):
    """One public name on a `SymbolPage`, whatever kind of thing it is."""

    name: str
    doc: Docstring
    definition: str
    signature: str
    returns: str
    bases: list[str]
    parameters: list[Parameter]
    attributes: list[Parameter]
    methods: list[Method]
    sync_code: str
    async_code: str
    output: str


def to_sync(code: str) -> str:
    """Turn an example for the async client into the same example for the sync client."""
    for async_text, sync_text in _SYNC_WORDS:
        code = code.replace(async_text, sync_text)
    return code


def parse_docstring(text: str | None, owner: str) -> Docstring:
    """Split a Google-style docstring into its parts."""
    if not text or not text.strip():
        raise SystemExit(f"{owner} has no description.")
    prose: list[str] = []
    parts: dict[str, list[str]] = {"args": [], "attrs": [], "returns": [], "raises": []}
    section: str | None = None
    fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
        if not fence and line.strip() in _SECTIONS:
            section = _SECTIONS[line.strip()]
        elif section is None or (line.strip() and not line.startswith(" ")):
            section = None
            prose.append(line)
        elif line.strip():
            parts[section].append(line)
    return Docstring(
        *_paragraphs(prose),
        dict(_entries(parts["args"], owner)),
        dict(_entries(parts["attrs"], owner)),
        " ".join(line.strip() for line in parts["returns"]),
        _entries(parts["raises"], owner),
    )


def _join_block(block: str) -> str:
    """Join a block's wrapped lines, keeping each row of a table and each item of a list."""
    if "```" in block:
        return block
    lines = [line.strip() for line in block.splitlines()]
    if all(line.startswith("|") for line in lines):
        return "\n".join(lines)
    if lines[0].startswith("- "):
        items: list[str] = []
        for line in lines:
            if line.startswith("- ") or not items:
                items.append(line)
            else:
                items[-1] = f"{items[-1]} {line}"
        return "\n".join(items)
    return " ".join(lines)


def _paragraphs(lines: list[str]) -> tuple[str, list[str]]:
    """Join a docstring's wrapped lines into paragraphs, leaving code blocks, tables, and lists."""
    blocks = "\n".join(lines).strip().split("\n\n")
    paragraphs = [_join_block(block) for block in blocks]
    return paragraphs[0], paragraphs[1:]


def _entries(lines: list[str], owner: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in lines:
        if line.startswith("        "):
            name, text = entries[-1]
            entries[-1] = (name, f"{text} {line.strip()}")
            continue
        entry = _ENTRY.fullmatch(line.strip())
        if entry is None:
            raise SystemExit(f"Can't read this line of the docstring of {owner}: {line!r}")
        entries.append((entry[1], entry[2]))
    return entries


def _clean_annotation(annotation: Any) -> str:
    return build_llms.clean_types(inspect.formatannotation(annotation))


def _annotation(parameter: inspect.Parameter) -> str:
    return _clean_annotation(parameter.annotation)


def _own_parameters(function: Any) -> list[inspect.Parameter]:
    """Return a function's parameters, without `self` or `cls`."""
    found = inspect.signature(function).parameters.values()
    return [parameter for parameter in found if parameter.name not in ("self", "cls")]


def _parameters(
    function: Any, doc: Docstring, owner: str, inherited: dict[str, str] | None = None
) -> list[Parameter]:
    found = _own_parameters(function)
    unknown = set(doc.args) - {parameter.name for parameter in found}
    if unknown:
        raise SystemExit(f"The docstring of {owner} describes unknown arguments: {sorted(unknown)}")
    known = {**(inherited or {}), **doc.args}
    return [
        Parameter(
            parameter.name,
            _annotation(parameter),
            None if parameter.default is inspect.Parameter.empty else repr(parameter.default),
            parameter.default is inspect.Parameter.empty,
            known.get(parameter.name, ""),
        )
        for parameter in found
    ]


def _return_type(function: Any, owner_class: str) -> str:
    text = _clean_annotation(inspect.signature(function).return_annotation)
    return owner_class if text == "Self" else text


def _signature(name: str, function: Any, returns: str) -> str:
    """Write a signature on one line if it fits in the column, or else one parameter per line."""
    parameters = _own_parameters(function)
    items: list[str] = []
    for index, parameter in enumerate(parameters):
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY and (
            index == 0 or parameters[index - 1].kind is not inspect.Parameter.KEYWORD_ONLY
        ):
            items.append("*")
        default = (
            "" if parameter.default is inspect.Parameter.empty else f" = {parameter.default!r}"
        )
        star = "*" if parameter.kind is inspect.Parameter.VAR_POSITIONAL else ""
        items.append(f"{star}{parameter.name}: {_annotation(parameter)}{default}")
    suffix = f" -> {returns}" if returns else ""
    one_line = f"{name}({', '.join(items)}){suffix}"
    if len(one_line) <= 60:
        return one_line
    body = "".join(f"    {item},\n" for item in items)
    return f"{name}(\n{body}){suffix}"


def _public_members(cls: type) -> dict[str, Any]:
    """Return a class's public methods and properties, by name."""
    return {
        name: member
        for name, member in vars(cls).items()
        if not name.startswith("_")
        and f"{cls.__name__}.{name}" not in INTERNAL_METHODS
        and (inspect.isfunction(member) or isinstance(member, property | classmethod))
    }


def _function(member: Any) -> Any:
    """Return what holds a member's signature and docstring."""
    return member.fget if isinstance(member, property) else getattr(member, "__func__", member)


def _shape(cls: type, *, names: dict[str, str]) -> dict[str, list[str]]:
    return {
        names.get(name, name): [p.name for p in _own_parameters(_function(member))]
        for name, member in _public_members(cls).items()
    }


def _check_clients_match(resource: Resource) -> None:
    expected = _shape(resource.sync_class, names=ASYNC_NAMES)
    if expected != _shape(resource.async_class, names={}):
        raise SystemExit(
            f"{resource.sync_class.__name__} and {resource.async_class.__name__} no longer have "
            "the same methods and arguments."
        )
    if any(group for group in resource.groups if CONSTRUCTOR in group.members):
        own = [p.name for p in _own_parameters(resource.sync_class)]
        other = [p.name for p in _own_parameters(resource.async_class)]
        if own != other:
            raise SystemExit(f"The constructors of {resource.name} differ between clients.")


def _check_groups(resource: Resource) -> None:
    listed = [name for group in resource.groups for name in group.members]
    public = set(_public_members(resource.sync_class))
    wanted = [name for name in listed if name != CONSTRUCTOR]
    if len(listed) != len(set(listed)) or set(wanted) != public:
        raise SystemExit(
            f"The groups of {resource.name} don't match its public methods: "
            f"{sorted(public.symmetric_difference(wanted))}"
        )


def check_every_name_is_covered() -> None:
    """Stop the build if a public name has no page, or a page lists one that isn't public."""
    public = set(pyquickbase.__all__) - {"__version__"}
    if set(NAME_LINKS) != public:
        raise SystemExit(
            f"These names need a place in the reference: {sorted(public - set(NAME_LINKS))}. "
            f"These aren't public: {sorted(set(NAME_LINKS) - public)}."
        )


def read_example(slug: str, stem: str, owner: str) -> tuple[str, str]:
    example = EXAMPLES / slug / f"{stem}.py"
    if not example.exists():
        raise SystemExit(f"{owner} has no example. Add {example.relative_to(ROOT)}.")
    output = example.with_suffix(".out")
    return (
        example.read_text(encoding="utf-8").strip(),
        output.read_text(encoding="utf-8").strip() if output.exists() else "",
    )


def read_method(resource: Resource, name: str, inherited: dict[str, str]) -> Method:
    cls = resource.sync_class
    owner = f"{resource.name}.{name}"
    if name == CONSTRUCTOR:
        doc = parse_docstring(inspect.getdoc(cls), resource.name)
        function: Any = cls.__init__
        async_code, output = read_example(resource.slug, "constructor", resource.name)
        return Method(
            resource.name,
            "constructor",
            f"{resource.name}()",
            False,
            _signature(resource.name, function, ""),
            "None",
            doc,
            _parameters(function, doc, resource.name),
            to_sync(async_code),
            async_code,
            output,
        )
    member = vars(cls)[name]
    function = _function(member)
    doc = parse_docstring(inspect.getdoc(function), owner)
    returns = _return_type(function, resource.name)
    async_code, output = read_example(resource.slug, name, owner)
    is_property = isinstance(member, property)
    return Method(
        name,
        name,
        name if is_property else f"{name}()",
        is_property,
        f"{name}: {returns}" if is_property else _signature(name, function, returns),
        returns,
        doc,
        _parameters(function, doc, owner, inherited),
        to_sync(async_code),
        async_code,
        output,
    )


def _check_examples_belong(slug: str, stems: set[str]) -> None:
    extra = {path.stem for path in (EXAMPLES / slug).glob("*.py")} - stems
    if extra:
        raise SystemExit(f"These examples in {slug} aren't for anything public: {sorted(extra)}")


def read_methods(resource: Resource) -> dict[str, Method]:
    _check_clients_match(resource)
    _check_groups(resource)
    # Options that from_env() shares with the constructor are described once, on the constructor.
    listed = [name for group in resource.groups for name in group.members]
    inherited = (
        parse_docstring(inspect.getdoc(resource.sync_class), resource.name).args
        if CONSTRUCTOR in listed
        else {}
    )
    methods = {name: read_method(resource, name, inherited) for name in listed}
    missing = missing_descriptions(methods)
    if missing:
        raise SystemExit(f"These arguments have no description: {', '.join(missing)}")
    _check_examples_belong(
        resource.slug, {"constructor" if name == CONSTRUCTOR else name for name in methods}
    )
    return methods


def missing_descriptions(methods: dict[str, Method]) -> list[str]:
    """List the arguments whose docstrings don't describe them."""
    return [
        f"{method.name}({parameter.name})"
        for method in methods.values()
        for parameter in method.parameters
        if not parameter.description
    ]


def _dataclass_fields(item: Any) -> tuple[Any, ...]:
    return dataclasses.fields(item) if dataclasses.is_dataclass(item) else ()


def _attribute_types(item: Any) -> dict[str, str]:
    """Return the types of a class's attributes, from its fields or its constructor."""
    fields = _dataclass_fields(item)
    if fields:
        return {field.name: _clean_annotation(field.type) for field in fields}
    try:
        found = inspect.signature(item).parameters.values()
    except (TypeError, ValueError):
        return {}
    return {
        parameter.name: _annotation(parameter)
        for parameter in found
        if parameter.annotation is not inspect.Parameter.empty
    }


def _attributes(item: Any, doc: Docstring, *, has_constructor: bool) -> list[Parameter]:
    """Return a class's attributes and properties, which all need descriptions."""
    types = _attribute_types(item)
    found: dict[str, str] = dict(doc.attrs)
    if not has_constructor:
        for field in _dataclass_fields(item):
            if field.name not in doc.attrs:
                raise SystemExit(f"{item.__name__}.{field.name} has no description.")
    result = [
        Parameter(name, types.get(name, ""), None, False, text) for name, text in found.items()
    ]
    for name, member in _public_members(item).items():
        if isinstance(member, property):
            text = parse_docstring(inspect.getdoc(member.fget), f"{item.__name__}.{name}")
            description = " ".join([text.summary, *text.prose])
            annotation = _return_type(member.fget, item.__name__)
            result.append(Parameter(name, annotation, None, False, description))
    return result


def _symbol_methods(item: type) -> list[Method]:
    methods: list[Method] = []
    for name, member in _public_members(item).items():
        if isinstance(member, property):
            continue
        function = _function(member)
        owner = f"{item.__name__}.{name}"
        doc = parse_docstring(inspect.getdoc(function), owner)
        returns = _return_type(function, item.__name__)
        parameters = _parameters(function, doc, owner)
        missing = [parameter.name for parameter in parameters if not parameter.description]
        if missing:
            raise SystemExit(f"{owner} doesn't describe these arguments: {missing}")
        methods.append(
            Method(
                name,
                f"{item.__name__}-{name}",
                f"{item.__name__}.{name}()",
                False,
                _signature(name, function, returns),
                returns,
                doc,
                parameters,
                "",
                "",
                "",
            )
        )
    return methods


def _symbol_example(slug: str, name: str) -> tuple[str, str, str]:
    """Return an example's sync code, async code, and output, or nothing if it needn't have one."""
    if name in NO_EXAMPLE or name in ALIASES:
        if (EXAMPLES / slug / f"{name}.py").exists():
            raise SystemExit(f"{name} has an example, so remove it from NO_EXAMPLE.")
        return "", "", ""
    async_code, output = read_example(slug, name, name)
    return to_sync(async_code), async_code, output


def read_symbol(page: SymbolPage, name: str) -> Symbol:
    item: Any = getattr(pyquickbase, name)
    sync_code, async_code, output = _symbol_example(page.slug, name)
    if name in ALIASES:
        doc = Docstring(ALIASES[name], [], {}, {}, "", [])
        definition = f"{name} = {build_llms.clean_types(repr(item))}"
        return Symbol(name, doc, definition, "", "", [], [], [], [], "", "", "")
    doc = parse_docstring(inspect.getdoc(item), name)
    if inspect.isfunction(item):
        returns = _return_type(item, name)
        parameters = _parameters(item, doc, name)
        signature = _signature(name, item, returns)
        attributes: list[Parameter] = []
        methods: list[Method] = []
    else:
        has_constructor = name in HAS_PUBLIC_CONSTRUCTOR
        returns = ""
        parameters = _parameters(item, doc, name) if has_constructor else []
        signature = _signature(name, item, "") if has_constructor else ""
        attributes = _attributes(item, doc, has_constructor=has_constructor)
        methods = _symbol_methods(item)
    missing = [parameter.name for parameter in parameters if not parameter.description]
    if missing:
        raise SystemExit(f"{name} doesn't describe these arguments: {missing}")
    bases = (
        [base.__name__ for base in item.__bases__ if base.__name__ in NAME_LINKS]
        if inspect.isclass(item) and issubclass(item, Exception)
        else []
    )
    bases_line = f"class {name}({', '.join(bases)})" if bases else ""
    return Symbol(
        name,
        doc,
        bases_line,
        signature,
        returns,
        bases,
        parameters,
        attributes,
        methods,
        sync_code,
        async_code,
        output,
    )


def read_symbols(page: SymbolPage) -> dict[str, Symbol]:
    listed = [name for group in page.groups for name in group.members]
    symbols = {name: read_symbol(page, name) for name in listed}
    _check_examples_belong(page.slug, set(listed))
    return symbols


def _public_link(name: str) -> str:
    return f"[`{name}`]({NAME_LINKS[name]})"


def _add_links(text: str, methods: dict[str, Method]) -> str:
    """Link what a sentence names. A bare method name may just be a word, so it needs `()`."""

    def qualified(found: re.Match[str]) -> str:
        cls = getattr(pyquickbase, found[1])
        if found[2] not in vars(cls):
            raise SystemExit(f"This text names a method that doesn't exist: {found[0]}")
        return f"[`{found[1]}.{found[2]}()`]({CLASS_PAGES[found[1]]}#{found[2]})"

    def call(found: re.Match[str]) -> str:
        if found[1] in methods:
            return f"[`{found[1]}()`](#{found[1]})"
        return _public_link(found[1]) if found[1] in NAME_LINKS else found[0]

    def see(found: re.Match[str]) -> str:
        return f"See [`{found[1]}()`](#{found[1]})" if found[1] in methods else found[0]

    def public(found: re.Match[str]) -> str:
        return _public_link(found[1]) if found[1] in NAME_LINKS else found[0]

    # A docstring's links are relative to the docs folder, and these pages are a level below it.
    text = text.replace("](guides/", "](../guides/")
    # Leave code blocks in a docstring alone.
    pieces = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    for index in range(0, len(pieces), 2):
        piece = _QUALIFIED.sub(qualified, pieces[index])
        pieces[index] = _PUBLIC_CODE.sub(public, _SEE.sub(see, _CALL.sub(call, piece)))
    return "".join(pieces)


def _linked_type(annotation: str) -> str:
    """Write a type with its public names linked, as in `list[` `Field` `]`."""
    pieces: list[str] = []
    plain = ""
    for piece in re.split(r"(\b[A-Z]\w*\b)", annotation):
        if piece in NAME_LINKS:
            if plain.strip():
                pieces.append(f"`{plain.strip()}`")
            plain = ""
            pieces.append(_public_link(piece))
        else:
            plain += piece
    if plain.strip():
        pieces.append(f"`{plain.strip()}`")
    return " ".join(pieces)


def _parameter_note(parameter: Parameter) -> str:
    if parameter.required:
        return "required"
    return "optional" if parameter.default == "None" else f"default `{parameter.default}`"


def _parameter_lines(parameters: list[Parameter], methods: dict[str, Method]) -> list[str]:
    lines: list[str] = []
    for parameter in parameters:
        note = _parameter_note(parameter)
        head = f"- **`{parameter.name}`** `{parameter.annotation}`, {note}"
        text = _add_links(parameter.description, methods)
        # Two spaces make a line break, so the description starts below the name and type.
        lines.append(f"{head}  \n  {text}" if text else head)
    return [*lines, ""]


def _attribute_lines(attributes: list[Parameter], methods: dict[str, Method]) -> list[str]:
    lines: list[str] = []
    for attribute in attributes:
        kind = f" `{attribute.annotation}`" if attribute.annotation else ""
        text = _add_links(attribute.description, methods)
        lines.append(f"- **`{attribute.name}`**{kind}  \n  {text}")
    return [*lines, ""]


def _return_lines(returns: str, doc: Docstring, methods: dict[str, Method]) -> list[str]:
    if returns in ("None", "") and not doc.returns:
        return []
    lines = [f"**Returns** {_linked_type(returns)}", ""] if returns else ["**Returns**", ""]
    return lines + ([_add_links(doc.returns, methods), ""] if doc.returns else [])


def _raises_lines(doc: Docstring, methods: dict[str, Method]) -> list[str]:
    if not doc.raises:
        return []
    lines = ["**Raises**", ""]
    for name, text in doc.raises:
        error = _public_link(name) if name in NAME_LINKS else f"`{name}`"
        lines.append(f"- {error}: {_add_links(text, methods)}")
    return [*lines, ""]


def _prose_lines(doc: Docstring, methods: dict[str, Method]) -> list[str]:
    lines = [_add_links(doc.summary, methods), ""]
    for paragraph in doc.prose:
        lines += [_add_links(paragraph, methods), ""]
    return lines


def _describe(method: Method, methods: dict[str, Method]) -> list[str]:
    """Write a method's description, parameters, return value, and errors as Markdown."""
    doc = method.doc
    lines = _prose_lines(doc, methods)
    if method.name in ASYNC_NAMES:
        lines += [f"The async client calls this `{ASYNC_NAMES[method.name]}()`.", ""]
    lines += [f"```python\n{method.signature}\n```", ""]
    if method.parameters:
        lines += ["**Parameters**", "", *_parameter_lines(method.parameters, methods)]
    if not method.is_property:
        lines += _return_lines(method.returns, doc, methods)
    return lines + _raises_lines(doc, methods)


def _describe_symbol(symbol: Symbol, *, anchors: bool) -> list[str]:
    """Write a public name's description as Markdown."""
    doc = symbol.doc
    none: dict[str, Method] = {}
    lines = _prose_lines(doc, none)
    if symbol.bases:
        linked = ", ".join(_public_link(base) for base in symbol.bases)
        lines += [f"**Inherits from** {linked}", ""]
    if symbol.signature:
        lines += [f"```python\n{symbol.signature}\n```", ""]
    if symbol.parameters:
        lines += ["**Parameters**", "", *_parameter_lines(symbol.parameters, none)]
    if symbol.attributes:
        lines += ["**Attributes**", "", *_attribute_lines(symbol.attributes, none)]
    lines += _return_lines(symbol.returns, doc, none)
    lines += _raises_lines(doc, none)
    for method in symbol.methods:
        anchor = f" {{#{method.anchor}}}" if anchors else ""
        lines += [f"#### `{method.heading}`{anchor}", "", *_describe(method, none)]
    return lines


def _indented(code: str, language: str = "python") -> list[str]:
    fenced = f"```{language}\n{code}\n```"
    return [f"    {line}" for line in fenced.splitlines()]


def _tabs(sync_code: str, async_code: str) -> list[str]:
    return [
        '=== "Sync"',
        "",
        *_indented(sync_code),
        "",
        '=== "Async"',
        "",
        *_indented(async_code),
        "",
    ]


def _columns(doc: list[str], code: list[str]) -> list[str]:
    return [
        '<div class="ref-row" markdown>',
        '<div class="ref-doc" markdown>',
        "",
        *doc,
        "</div>",
        '<div class="ref-code" markdown>',
        "",
        *code,
        "</div>",
        "</div>",
        "",
    ]


def _output_lines(output: str) -> list[str]:
    if not output:
        return []
    return ['<p class="ref-output-label">Output</p>', "", f"```text\n{output}\n```", ""]


def _returned_types(methods: dict[str, Method]) -> dict[str, list[str]]:
    returned: dict[str, list[str]] = {}
    for method in methods.values():
        if method.anchor == "constructor":
            continue
        for name in _NAME.findall(method.returns):
            if name in NAME_LINKS:
                returned.setdefault(name, []).append(method.heading)
    return returned


def _return_table(methods: dict[str, Method]) -> list[str]:
    """Write a table of what the methods return, with each type's own first line."""
    rows = ["| Type | What it is | Returned by |", "|---|---|---|"]
    by_heading = {method.heading: method.anchor for method in methods.values()}
    for name, callers in sorted(_returned_types(methods).items()):
        summary = (inspect.getdoc(getattr(pyquickbase, name)) or "").split("\n\n")[0]
        summary = " ".join(summary.split()).replace("|", "\\|")
        linked = ", ".join(f"[`{caller}`](#{by_heading[caller]})" for caller in callers)
        rows.append(f"| {_public_link(name)} | {summary} | {linked} |")
    return [*rows, ""]


def _guide_line(group: Group) -> list[str]:
    if group.guide is None:
        return []
    return [f"For the full picture, see [{group.guide[0]}]({group.guide[1]}).", ""]


def _page_links(current: Resource | SymbolPage) -> str:
    """Link to the other reference pages. This is plain Markdown, so it suits any docs tool."""
    links = [
        f"**{item.name}**" if item is current else f"[{item.name}]({item.slug}.md)"
        for item in ALL_PAGES
    ]
    return " ".join(['<p class="ref-links ref-links--pages" markdown>', *links, "</p>"])


def _page_header(page: Resource | SymbolPage) -> list[str]:
    return [
        "---",
        "hide:",
        "  - navigation",
        "---",
        "",
        f"# {page.name}",
        "",
        _page_links(page),
        "",
        '<p class="ref-links" markdown>'
        f"[Overview]({OVERVIEW})"
        f'<a class="ref-markdown" href="../{page.slug}.txt">View as Markdown</a>'
        '<button class="ref-copy" type="button" hidden>Copy for an LLM</button>'
        "</p>",
        "",
    ]


def build_page(resource: Resource, methods: dict[str, Method]) -> str:
    """Return a class's page for the site, with two columns and a tab for each client."""
    lines = [
        *_page_header(resource),
        *_columns(
            [_add_links(resource.intro, methods), "", _add_links(resource.note, methods), ""],
            _tabs(resource.setup, resource.async_setup),
        ),
        "## What the methods return {#returns}",
        "",
        *_return_table(methods),
    ]
    for group in resource.groups:
        lines += [f"## {group.title} {{#{group.anchor}}}", "", *_guide_line(group)]
        for name in group.members:
            method = methods[name]
            heading = [f"### `{method.heading}` {{#{method.anchor}}}", ""]
            code = _tabs(method.sync_code, method.async_code) + _output_lines(method.output)
            lines += _columns(heading + _describe(method, methods), code)
    return "\n".join(lines)


def _symbol_code(symbol: Symbol) -> list[str]:
    if symbol.sync_code:
        return _tabs(symbol.sync_code, symbol.async_code) + _output_lines(symbol.output)
    return [f"```python\n{symbol.definition}\n```", ""] if symbol.definition else []


def build_symbol_page(page: SymbolPage, symbols: dict[str, Symbol]) -> str:
    """Return a page of several public names for the site."""
    none: dict[str, Method] = {}
    lines = [
        *_page_header(page),
        *_columns(
            [_add_links(page.intro, none), "", _add_links(page.note, none), ""],
            _tabs(page.setup, page.async_setup),
        ),
    ]
    for group in page.groups:
        lines += [f"## {group.title} {{#{group.anchor}}}", "", *_guide_line(group)]
        for name in group.members:
            symbol = symbols[name]
            heading = [f"### `{name}` {{#{name}}}", ""]
            body = _describe_symbol(symbol, anchors=True)
            lines += _columns(heading + body, _symbol_code(symbol))
    return "\n".join(lines)


def _qualified_heading(resource: Resource, method: Method) -> str:
    if method.anchor == "constructor":
        return method.heading
    return f"{resource.name}.{method.heading}"


def _plain_examples(sync_code: str, async_code: str, output: str, *, both: bool) -> list[str]:
    lines: list[str] = []
    if sync_code:
        label = "Sync example" if both else "Example"
        lines += [f"{label}:\n\n```python\n{sync_code}\n```", ""]
    if both and async_code:
        lines += [f"Async example:\n\n```python\n{async_code}\n```", ""]
    if output:
        lines += [f"Output:\n\n```text\n{output}\n```", ""]
    return lines


def _plain_intro(page: Resource | SymbolPage, site_url: str, *, both: bool) -> list[str]:
    none: dict[str, Method] = {}
    others = ", ".join(
        f"[{item.name}]({site_url}reference/{item.slug}.txt)"
        for item in ALL_PAGES
        if item is not page
    )
    lines: list[str] = []
    if both:
        lines += [
            f"# pyquickbase API reference: {page.name}",
            "",
            f"This describes version {pyquickbase.__version__}. Other pages: {others}.",
            "",
        ]
    lines += [_add_links(page.intro, none), "", _add_links(page.note, none), ""]
    if both:
        lines += [
            f"Sync:\n\n```python\n{page.setup}\n```",
            "",
            f"Async:\n\n```python\n{page.async_setup}\n```",
            "",
        ]
    else:
        lines += [f"```python\n{page.setup}\n```", ""]
    return lines


def _finish_plain(lines: list[str], slug: str, site_url: str) -> str:
    text = build_llms.rewrite_links("\n".join(lines), f"reference/{slug}.md", site_url)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def build_markdown(
    resource: Resource, methods: dict[str, Method], site_url: str, *, both_clients: bool = True
) -> str:
    """Return a class's page as one plain Markdown file."""
    lines = _plain_intro(resource, site_url, both=both_clients)
    lines += ["## What the methods return", "", *_return_table(methods)]
    for group in resource.groups:
        lines += [f"## {group.title}", "", *_guide_line(group)]
        for name in group.members:
            method = methods[name]
            lines += [f"### `{_qualified_heading(resource, method)}`", ""]
            lines += _describe(method, methods)
            lines += _plain_examples(
                method.sync_code, method.async_code, method.output, both=both_clients
            )
    return _finish_plain(lines, resource.slug, site_url)


def build_symbol_markdown(
    page: SymbolPage, symbols: dict[str, Symbol], site_url: str, *, both_clients: bool = True
) -> str:
    """Return a page of several public names as one plain Markdown file."""
    lines = _plain_intro(page, site_url, both=both_clients)
    for group in page.groups:
        lines += [f"## {group.title}", "", *_guide_line(group)]
        for name in group.members:
            symbol = symbols[name]
            lines += [f"### `{name}`", "", *_describe_symbol(symbol, anchors=False)]
            if symbol.definition and not symbol.sync_code:
                lines += [f"```python\n{symbol.definition}\n```", ""]
            lines += _plain_examples(
                symbol.sync_code, symbol.async_code, symbol.output, both=both_clients
            )
    return _finish_plain(lines, page.slug, site_url)


def full_text(site_url: str) -> list[tuple[str, str, str]]:
    """Return `(title, slug, Markdown)` for each page, with sync examples, for llms-full.txt."""
    check_every_name_is_covered()
    pages: list[tuple[str, str, str]] = []
    for resource in RESOURCES:
        text = build_markdown(resource, read_methods(resource), site_url, both_clients=False)
        pages.append((resource.name, resource.slug, text))
    for page in SYMBOL_PAGES:
        text = build_symbol_markdown(page, read_symbols(page), site_url, both_clients=False)
        pages.append((page.name, page.slug, text))
    return pages


def main() -> None:
    config = build_llms.read_config()
    check_every_name_is_covered()
    OUTPUT.mkdir(exist_ok=True)

    def write(slug: str, page: str, markdown: str) -> None:
        (OUTPUT / f"{slug}.md").write_text(page, encoding="utf-8")
        (OUTPUT / f"{slug}.txt").write_text(markdown, encoding="utf-8")

    for resource in RESOURCES:
        methods = read_methods(resource)
        write(
            resource.slug,
            build_page(resource, methods),
            build_markdown(resource, methods, config.site_url),
        )
    for page in SYMBOL_PAGES:
        symbols = read_symbols(page)
        write(
            page.slug,
            build_symbol_page(page, symbols),
            build_symbol_markdown(page, symbols, config.site_url),
        )
    print(f"Wrote {len(ALL_PAGES)} pages to docs/reference/")


if __name__ == "__main__":
    main()
