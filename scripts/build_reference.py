"""Write the API reference pages, ready for `mkdocs build`.

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
import functools
import inspect
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple, get_overloads

import build_llms

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "reference"
EXAMPLES = ROOT / "docs_src" / "reference"
CONSTRUCTOR = "__init__"
# Beyond this many parameters, a class's page folds the rest away.
MAX_SHOWN_PARAMETERS = 3
# What each method on a class's page is called in its heading and the sidebar: what it does, in
# plain words, with the method itself in the signature beside it. The same verb means the same kind
# of action on every page: "Get or create" for an ensure_ method, and "Link to" for a URL. A
# property is named by what it is.
TITLES: dict[str, dict[str, str]] = {
    "quickbase": {
        "__init__": "Create a client",
        "from_env": "Create a client from the environment",
        "close": "Close a client",
        "app": "Get an app",
        "apps": "List apps",
        "current_user": "Find out who the token belongs to",
        "find_user": "Find a user by email",
        "create_app": "Create an app",
        "realm": "The client's realm",
        "policy": "The client's policy",
        "clear_schema_cache": "Clear the metadata cache",
        "request": "Call any endpoint",
    },
    "app": {
        "id": "The app's ID",
        "info": "Get the app's details",
        "tables": "List tables",
        "roles": "List roles",
        "members": "List members",
        "member_roles": "List a member's roles",
        "events": "List webhooks and automations",
        "table": "Get a table",
        "url": "Link to the app",
        "settings_url": "Link to the app's settings",
        "ensure_table": "Get or create a table",
        "create_table": "Create a table",
        "preview_schema_changes": "Preview schema changes",
        "apply_schema_changes": "Apply schema changes",
        "operation": "Make a schema tool for a model",
        "copy": "Copy the app",
        "drop": "Delete the app",
    },
    "report": {
        "run": "Run a report",
        "run_page": "Fetch one page of a report",
        "summary_rows": "Get a summary report's rows",
        "info": "Get the report's details",
        "url": "Link to the report",
    },
    "operation": {
        "schema": "The arguments' JSON Schema",
        "name": "The operation's name",
        "run": "Run an operation",
    },
    "schema-operation": {
        "schema": "The arguments' JSON Schema",
        "name": "The operation's name",
        "run": "Run a schema operation",
    },
    # Pages of public names: a function, or a method of an object, has a title. An object, error, or
    # type keeps its name, as that's how it appears in signatures and tracebacks.
    "queries": {
        "F.contains": "Match text that contains",
        "F.not_contains": "Match text that doesn't contain",
        "F.starts_with": "Match text that starts with",
        "F.not_starts_with": "Match text that doesn't start with",
        "F.has": "Match one of a field's values",
        "F.not_has": "Match none of a field's values",
        "F.is_in": "Match any of several values",
        "F.op": "Use any operator",
        "where_from_dict": "Build a condition from JSON",
        "where_schema": "Describe conditions as JSON Schema",
        "parse_where": "Check a hand-written condition",
    },
    "policies": {
        "Policy.read_only": "Make a read-only policy",
        "Policy.bind": "Bind to an app",
        "Policy.bind_offline": "Bind without an app",
        "BoundPolicy.classes_of": "Get a field's classes",
        "BoundPolicy.decide": "Decide a request",
        "BoundPolicy.allows": "Check what's allowed",
        "BoundPolicy.explain": "Explain what's allowed",
        "BoundPolicy.max_records": "Get a table's record limit",
        "BoundPolicy.row_limit": "Get a table's row limit",
        "BoundPolicy.offers": "Get what an operation may offer",
        "BoundPolicy.to_json": "Save a bound policy",
        "BoundPolicy.from_json": "Load a bound policy",
        "Decision.message": "Get the refusal's message",
    },
    "rules": {
        "allow.filter": "Allow filtering",
        "allow.sort": "Allow sorting",
        "allow.insert": "Allow creating records",
        "allow.update": "Allow updating records",
        "allow.download": "Allow downloading files",
        "allow.delete_file": "Allow deleting file versions",
        "allow.schema": "Allow schema changes",
        "allow.create_tables": "Allow creating tables",
        "allow.read": "Allow reading",
        "allow.delete": "Allow deleting records",
        "allow.report": "Allow running reports",
        "deny.filter": "Deny filtering",
        "deny.sort": "Deny sorting",
        "deny.insert": "Deny creating records",
        "deny.update": "Deny updating records",
        "deny.download": "Deny downloading files",
        "deny.delete_file": "Deny deleting file versions",
        "deny.schema": "Deny schema changes",
        "deny.create_tables": "Deny creating tables",
        "deny.read": "Deny reading",
        "deny.delete": "Deny deleting records",
        "deny.report": "Deny running reports",
        "deny.all": "Deny everything",
        "limit.rows": "Limit the rows",
        "fields.all": "Select every field",
        "fields.new": "Select new fields",
        "fields.classed": "Select fields by class",
        "fields.labeled_like": "Select fields by label",
        "tables.all": "Select every table",
        "tables.created": "Select created tables",
        "Rule.decide": "Refuse a request",
    },
    "metadata": {
        "FieldUsage.describe": "Describe a field's uses in words",
    },
    "records": {
        "RecordIterator.to_pandas": "Fetch records into pandas",
        "RecordIterator.to_polars": "Fetch records into polars",
        "RecordIterator.to_csv": "Write records to a CSV file",
        "RecordIterator.to_excel": "Write records to an Excel file",
        "Page.to_csv": "Write a page to a CSV file",
        "Page.to_excel": "Write a page to an Excel file",
        "FileRows.raise_for_errors": "Refuse a file with errors",
        "Page.to_pandas": "Turn a page into pandas",
        "Page.to_polars": "Turn a page into polars",
        "SummaryRow.statistic": "Get one statistic's values",
    },
    "retries": {
        "RetryPolicy.backoff": "Get the backoff delay",
        "RetryPolicy.rate_limit_delay": "Get the wait after a 429",
    },
    "tools": {
        "tool_result": "Return a result to a model",
        "tool_error": "Return an error to a model",
    },
    "table": {
        "query": "Query records",
        "query_page": "Fetch one page of records",
        "first": "Get the first matching record",
        "get": "Get a record by ID",
        "count": "Count records",
        "compile_where": "Show a condition's query text",
        "insert": "Create records",
        "update": "Update records",
        "upsert": "Create or update records",
        "delete": "Delete records",
        "preview_update": "Preview an update",
        "preview_delete": "Preview a delete",
        "summarize": "Summarize records",
        "run_formula": "Run a formula",
        "info": "Get the table's details",
        "fields": "List fields",
        "relationships": "List relationships",
        "reports": "List reports",
        "events": "List webhooks and automations",
        "field_usage": "See where fields are used",
        "rows_from_file": "Read rows from a CSV or Excel file",
        "report": "Get a report",
        "resolve_field_ids": "Turn field labels into IDs",
        "download_file": "Download a file",
        "delete_file_version": "Delete a file version",
        "operation": "Make a tool for a model",
        "url": "Link to the table",
        "record_url": "Link to a record",
        "record_url_template": "Link to any record",
        "new_record_url": "Link to a new record",
        "field_url": "Link to a field's settings",
        "fields_url": "Link to the list of fields",
        "relationships_url": "Link to the relationships",
        "settings_url": "Link to the table's settings",
        "form_url": "Link to a form",
        "create_field": "Create a field",
        "update_field": "Update a field",
        "delete_fields": "Delete fields",
        "ensure_field": "Create or update a field",
        "create_relationship": "Create a relationship",
        "ensure_relationship": "Get or create a relationship",
        "ensure_lookup_field": "Get or create a lookup field",
        "ensure_summary_field": "Get or create a summary field",
        "ensure_count_field": "Get or create a count field",
        "delete_relationship": "Delete a relationship",
        "update_info": "Update the table's details",
        "drop": "Delete the table",
    },
}
# Methods that the async client names differently.
ASYNC_NAMES = {"close": "aclose"}

# Classes you make yourself, so they show a constructor rather than a list of attributes.
HAS_PUBLIC_CONSTRUCTOR = {
    "F",
    "FileUpload",
    "Period",
    "Policy",
    "Decision",
    "CreateTable",
    "UpdateTable",
    "DeleteTable",
    "CreateField",
    "UpdateField",
    "DeleteField",
    "CreateRelationship",
    "CreateLookupField",
    "CreateSummaryField",
    "DeleteRelationship",
    "RetryPolicy",
}
# Public so that the SDK can call them across modules, and of no use from outside it.
INTERNAL_METHODS = {
    "Expression.compile",
    "BoundPolicy.readable",
    "BoundPolicy.filterable",
    "BoundPolicy.visible",
    "BoundPolicy.table_visible",
    "BoundPolicy.hidden_labels",
    "BoundPolicy.max_summary_records",
    "BoundPolicy.max_delete",
    "BoundPolicy.user_emails",
    "BoundPolicy.field_types",
    "BoundPolicy.row_limit_rule",
    "BoundPolicy.emails_shown",
    "BoundPolicy.offers_for",
    "BoundPolicy.schema_tables",
    "BoundPolicy.can_create_tables",
    "BoundPolicy.with_created",
    "BoundPolicy.created_offers",
    "BoundPolicy.trim",
    "BoundPolicy.new_field_effects",
    "BoundPolicy.deletion_effects",
}
# Type aliases have no docstrings of their own.
ALIASES = {
    "FieldRef": "A field, by label (`str`) or ID (`int`).",
    "FieldRefs": "One or more fields, by label, ID, or `F`, as `select` and `order_by` take them.",
    "PeriodUnit": "How much time each group of a `Period` covers.",
    "StatisticName": "A value `summarize()` works out from one field's values in each group.",
    "FileFormat": "A file's format: `csv`, including tab-separated files, or `xlsx`.",
    "FileLike": "A path, or an open file: text for CSV, and binary for Excel.",
    "ColumnMap": "Which field each file column goes in, by label or ID, or None to skip it.",
    "SummaryGroups": "What `Table.summarize()` can group by.",
    "WriteAction": "What kind of write a `WriteRequest` is.",
    "SchemaChange": (
        "Any one of the changes on this page, as `App.preview_schema_changes()` takes them."
    ),
    "SchemaAction": (
        "The `action` of a change in the JSON form a model sends, such as `create_field`."
    ),
    "SummaryFunction": "How `CreateSummaryField` combines a child's values. `count` counts the "
    "child records.",
    "Risk": "How much a planned change can lose: `additive` only adds, `modifying` changes what's "
    "there, and `destructive` deletes data or fields.",
    "SchemaOperationName": "What `App.operation()` can offer a model.",
    "DerivedKind": "A field whose values come from other fields: a `formula`, a `lookup` of a "
    "parent's field, or a `summary` of a child's. `WriteRequest.derived` lists the ones a write "
    "makes or changes.",
    "ReadAction": "What kind of read a `ReadRequest` is.",
    "OperationName": "What `Table.operation()` can offer a model, each running the table or "
    "report method of the same name.",
    "ToolErrorCode": "The `code` in what `tool_error()` returns. Codes don't change within 1.x, so "
    "a tool can act on them.",
    "Verb": "What a policy grants or refuses, such as `read`, `update`, or `lookup`.",
    "SchemaVerb": "What `allow.schema()` grants on a table, where the change lands.",
    "Layer": "Which check made a `Decision`: `policy`, `rows`, `derived`, or `confirmation`.",
}
# What can't be shown happening in an example, and why.
NO_EXAMPLE = {
    "SchemaChangeError": "this needs a change to fail partway through a plan, which "
    "tests/_async/test_schema_plan.py shows",
    "NetworkError": "`RequestTimeoutError` shows catching this",
    "InvalidResponseError": "only Quickbase returning something unexpected raises this",
    "PermissionDeniedError": "this needs a second token with less access",
    "WritePermissionError": "this needs a second token with less access",
    "RateLimitError": "this needs a real burst of requests",
    "ServerError": "only a failure at Quickbase raises this",
    "UnsupportedOperationError": "only Quickbase retiring a call raises this",
}
# Shown on the page, but not run by the live tests, because of what it needs.
NOT_RUN_LIVE = {
    "report/summary_rows": "A summary report can only be built in the Quickbase interface. "
    "tests/_async/live/test_sdk.py tests this method.",
    "quickbase/create_app": "It makes a lasting app. tests/_async/live/test_apps.py tests this "
    "method, and deletes what it makes.",
    "app/copy": "It makes a lasting app. tests/_async/live/test_apps.py tests this method, and "
    "deletes what it makes.",
    "app/drop": "It would delete the test app. tests/_async/live/test_apps.py tests this method "
    "on a copy.",
}


class Group(NamedTuple):
    title: str
    anchor: str
    guide: tuple[str, str] | None
    members: tuple[str, ...]


def _setup(last_line: str) -> tuple[str, str]:
    """Return the setup code for each client."""
    body = f'    realm="acme", token="your_user_token"\n) as qb:\n    {last_line}'
    return (
        f"from pyquickbase import Quickbase\n\nwith Quickbase(\n{body}",
        f"from pyquickbase import AsyncQuickbase\n\nasync with AsyncQuickbase(\n{body}",
    )


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
_POLICIES = ("Policies", "../policies/index.md")
_RULES = ("Writing rules", "../policies/rules.md")
_CUSTOM_RULES = ("Rules of your own", "../policies/custom-rules.md")
_DEBUGGING = ("Debugging refusals", "../policies/debugging.md")
_FILES = ("File attachments", "../guides/files.md")
_FORMULAS = ("Formulas", "../guides/formulas.md")
_AGENTS = ("Using pyquickbase from an AI agent", "../agents/index.md")
_ERRORS = ("Handling errors", "../guides/errors.md")
_APPS = ("Working with apps", "../guides/apps.md")
_SPREADSHEETS = ("CSV and Excel files", "../guides/spreadsheets.md")
_LINKS = "Link to the Quickbase interface"

_TABLE_LINE = 'tasks = qb.app("bq1234567").table("Job Tasks")'
_PLAN_LINES = '\n    plan = await app.operation("preview_schema_changes")'
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
                ("app", "realm", "policy", "clear_schema_cache", "request"),
            ),
            Group("Apps and users", "apps-and-users", _APPS, ("apps", "current_user", "find_user")),
            Group("Create an app", "create-an-app", _APPS, ("create_app",)),
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
        "`await`, but `table()`, `url()`, and `settings_url()` are called without it.",
        *_setup('app = qb.app("bq1234567")'),
        (
            Group(
                "Look up the app's details",
                "app-details",
                _READING,
                ("id", "info", "tables", "roles", "events"),
            ),
            Group("See who has access", "who-has-access", _APPS, ("members", "member_roles")),
            Group("Get a table", "get-a-table", _READING, ("table",)),
            Group(_LINKS, "links", _AGENTS, ("url", "settings_url")),
            Group("Create tables", "create-tables", _SCHEMA, ("ensure_table", "create_table")),
            Group(
                "Change the schema with a plan",
                "plan-schema",
                _SCHEMA,
                ("preview_schema_changes", "apply_schema_changes"),
            ),
            Group("Give a model the schema", "operations", _AGENTS, ("operation",)),
            Group("Copy or delete the app", "copy-or-delete", _APPS, ("copy", "drop")),
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
                ("query", "query_page", "first", "get", "count", "compile_where"),
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
                (
                    "info",
                    "fields",
                    "field_usage",
                    "relationships",
                    "reports",
                    "report",
                    "events",
                    "resolve_field_ids",
                ),
            ),
            Group(
                "Import a CSV or Excel file", "import-a-file", _SPREADSHEETS, ("rows_from_file",)
            ),
            Group("Work with files", "files", _FILES, ("download_file", "delete_file_version")),
            Group("Offer an operation to a model", "operations", _AGENTS, ("operation",)),
            Group(
                _LINKS,
                "links",
                _AGENTS,
                (
                    "url",
                    "record_url",
                    "record_url_template",
                    "new_record_url",
                    "field_url",
                    "fields_url",
                    "relationships_url",
                    "settings_url",
                    "form_url",
                ),
            ),
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
                    "ensure_count_field",
                    "delete_relationship",
                    "update_info",
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
            Group(_LINKS, "links", _AGENTS, ("url",)),
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
        "SchemaOperation",
        "schema-operation",
        pyquickbase.SchemaOperation,
        pyquickbase.AsyncSchemaOperation,
        "A `SchemaOperation` is one schema operation offered to a model as a tool: "
        "`describe_schema`, `preview_schema_changes`, or `apply_schema_changes`. Get one from "
        "`App.operation()`. `apply_schema_changes` only runs with the confirmation "
        "`preview_schema_changes` returned for the same changes, while the schema is as it was.",
        f"The examples below use `plan`, made as shown here. {_JOB_TASKS} {_LIVE_TEST}",
        _setup('app = qb.app("bq1234567")')[0] + _PLAN_LINES.replace("await ", ""),
        _setup('app = qb.app("bq1234567")')[1] + _PLAN_LINES,
        (
            Group("Describe it to a model", "describe-it", _AGENTS, ("schema", "name")),
            Group("Run it", "run-it", _AGENTS, ("run",)),
        ),
    ),
)

_OWN_CLIENT_NOTE = (
    "Each example makes its own client from the environment, with `bqtasks123` standing for "
    f"the ID of the `Job Tasks` table. {_LIVE_TEST}"
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
            Group(
                "Read results",
                "read-results",
                _READING,
                ("Record", "RecordIterator", "Page", "User"),
            ),
            Group("Write results", "write-results", _WRITING, ("WriteResult", "RowError")),
            Group(
                "Files",
                "files",
                _FILES,
                ("FileUpload", "FileDownload", "FileAttachment", "FileVersion"),
            ),
            Group(
                "CSV and Excel files",
                "spreadsheets",
                _SPREADSHEETS,
                ("FileRows", "FileRowError", "FileFormat", "FileLike", "ColumnMap"),
            ),
            Group("Formula results", "formula-results", _FORMULAS, ("FormulaResult",)),
            Group(
                "Previews of writes",
                "previews",
                _WRITING,
                ("UpdatePreview", "DeletePreview", "RecordChange", "FieldChange"),
            ),
            Group(
                "Summaries",
                "summaries",
                _READING,
                (
                    "SummaryRow",
                    "PeriodChange",
                    "Period",
                    "PeriodUnit",
                    "StatisticName",
                    "SummaryGroups",
                ),
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
            Group("Apps", "apps", _APPS, ("AppSummary", "AppInfo", "Role", "AppEvent")),
            Group("People", "people", _APPS, ("UserProfile", "AppMember", "MemberRole")),
            Group(
                "Tables and reports", "tables-and-reports", _READING, ("TableInfo", "ReportInfo")
            ),
            Group(
                "Fields and relationships",
                "fields",
                _SCHEMA,
                ("Field", "FieldUsage", "Relationship", "TableSchema"),
            ),
        ),
    ),
    SymbolPage(
        "Schema changes",
        "schema-changes",
        "Describe changes to an app's tables, fields, and relationships, then pass them to "
        "`App.preview_schema_changes()`, which says what each would do without making it, and "
        "`App.apply_schema_changes()`, which makes them if the app hasn't changed since. A change "
        "the app already has counts as done.",
        f"{_SYMBOL_NOTE} {_PROJECTS} The examples only plan, unless they say otherwise.",
        *_setup(_TABLE_LINE),
        (
            Group(
                "Tables",
                "tables",
                _SCHEMA,
                ("CreateTable", "UpdateTable", "DeleteTable"),
            ),
            Group("Fields", "fields", _SCHEMA, ("CreateField", "UpdateField", "DeleteField")),
            Group(
                "Relationships",
                "relationships",
                _SCHEMA,
                (
                    "CreateRelationship",
                    "CreateLookupField",
                    "CreateSummaryField",
                    "DeleteRelationship",
                    "SummaryFunction",
                ),
            ),
            Group(
                "Plans and results",
                "plans",
                _SCHEMA,
                ("SchemaPlan", "PlannedChange", "SettingChange", "SchemaChangeResult", "Risk"),
            ),
            Group("Any change", "any-change", _AGENTS, ("SchemaChange", "SchemaAction")),
        ),
    ),
    SymbolPage(
        "Policies",
        "policies",
        "A `Policy` says what a client may read and change. Bind it to an app, which pins every "
        "table and field by ID, and pass the `BoundPolicy` to `Quickbase()` as `policy`. It "
        "protects against bugs in your code and mistakes by a model, so give your token access to "
        "as little as possible as well, as Quickbase's own permissions are still the real "
        "boundary.",
        _OWN_CLIENT_NOTE,
        "",
        "",
        (
            Group("Write and bind a policy", "policies", _POLICIES, ("Policy", "BoundPolicy")),
            Group("Decisions", "decisions", _DEBUGGING, ("Decision", "Named", "Offers")),
        ),
    ),
    SymbolPage(
        "Rules",
        "rules",
        "A policy is a list of rules. `allow` and `deny` name what a client may do with tables "
        "and fields, `limit` keeps it to some records, and a `Rule` of your own can refuse "
        "anything else. Anything no rule allows is refused, and a deny always wins.",
        _OWN_CLIENT_NOTE,
        "",
        "",
        (
            Group(
                "Allow, deny, and limit",
                "rules",
                _RULES,
                ("allow", "deny", "limit", "PolicyRule", "Verb", "SchemaVerb", "Layer"),
            ),
            Group(
                "Tables and fields",
                "selectors",
                _RULES,
                ("fields", "tables", "FieldSelector", "TableSelector"),
            ),
            Group(
                "Rules of your own",
                "own-rules",
                _CUSTOM_RULES,
                (
                    "Rule",
                    "WriteRequest",
                    "ReadRequest",
                    "WriteAction",
                    "DerivedKind",
                    "ReadAction",
                ),
            ),
        ),
    ),
    SymbolPage(
        "Retries",
        "retries",
        "A `RetryPolicy` says how a client retries a request that hit a rate limit or a "
        "connection failure, and how long it waits. Pass one to `Quickbase()` as `retry`.",
        _OWN_CLIENT_NOTE,
        "",
        "",
        (Group("Retrying requests", "retries", _CLIENTS, ("RetryPolicy",)),),
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
        "",
        "",
        (
            Group("Results", "results", _AGENTS, ("tool_result",)),
            Group("Errors", "errors", _AGENTS, ("tool_error", "ToolErrorCode")),
            Group("Operations", "operations", _AGENTS, ("OperationName", "SchemaOperationName")),
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
                    "TooManyRecordsError",
                    "ConfirmationError",
                    "InvalidValueError",
                    "FileTooLargeError",
                    "PolicyRefusedError",
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
            Group(
                "Writes",
                "writes",
                _WRITING,
                ("WriteError", "WritePermissionError", "SchemaChangeError"),
            ),
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
                    "UnsupportedOperationError",
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


class Section(NamedTuple):
    """A group of reference pages, as the menu, the overview, and llms.txt show them."""

    title: str
    about: str
    pages: tuple[tuple[str, str], ...]
    # Hand-written pages that belong here, as `(title, path from docs/, what's in it)`.
    extra: tuple[tuple[str, str, str], ...] = ()


# The reference's sections, in order, with what each page holds. mkdocs.yml's nav must match this,
# which a test checks, and every page is in exactly one section.
SECTIONS = (
    Section(
        "Core",
        "The client, and the apps, tables, and reports you reach through it.",
        (
            ("quickbase", "Create and close a client, and call any endpoint"),
            ("app", "An app's details, its tables, and schema changes with a plan"),
            ("table", "Read and write records and files, and change a table's fields"),
            ("report", "Run reports built in the Quickbase interface"),
        ),
    ),
    Section(
        "Data",
        "The conditions you send, and what comes back.",
        (
            ("queries", "Build conditions with `F`, or from plain data"),
            ("records", "What reads and writes return, files, previews, and summaries"),
            ("metadata", "What describes apps, tables, fields, relationships, and reports"),
        ),
        (
            (
                "Field types",
                "field-types.md",
                "What to write to each field type, and what it reads as",
            ),
        ),
    ),
    Section(
        "Schema",
        "Changes to tables, fields, and relationships, checked before they're made.",
        (
            ("schema-changes", "The changes, and the plans that check them"),
            ("schema-operation", "One schema operation offered to a model"),
        ),
    ),
    Section(
        "Policies",
        "What a client may read and change.",
        (
            ("policies", "Write a policy, bind it, and ask what it allows"),
            ("rules", "Allow, deny, and limit tables, fields, and rows"),
        ),
    ),
    Section(
        "AI agents",
        "Operations and results for a model's tool calls.",
        (
            ("operation", "One table operation offered to a model"),
            ("tools", "Results and errors as plain data for a tool call"),
        ),
    ),
    Section(
        "Errors and retries",
        "What the SDK raises, and how a client retries.",
        (
            ("errors", "Everything the SDK raises, and what each one holds"),
            ("retries", "How a client retries rate limits and failed connections"),
        ),
    ),
)
PAGES_BY_SLUG = {page.slug: page for page in ALL_PAGES}
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
    links["AsyncSchemaOperation"] = links["SchemaOperation"]
    links["AsyncRecordIterator"] = links["RecordIterator"]
    return links


NAME_LINKS = _name_links()

_SECTIONS = {"Args:": "args", "Attributes:": "attrs", "Returns:": "returns", "Raises:": "raises"}
_ENTRY = re.compile(r"\*{0,2}(\w+): (.*)")
_NAME = re.compile(r"\b[A-Za-z_]\w*\b")
_CALL = re.compile(r"`(\w+)\(\)`")
_SEE = re.compile(r"See `(\w+)`")
# Not a name that an earlier pass has already linked, as in [`Quickbase`](quickbase.md).
_PUBLIC_CODE = re.compile(r"(?<!\[)`([A-Za-z]\w*)`")
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


# The longest line in the code column, which docs/stylesheets/reference.css sizes to fit.
# Examples are held to it by a test, and signatures and definitions are formatted to it.
LINE_LIMIT = 62
# The longest line of code in the description beside it, which is narrower.
TEXT_LINE_LIMIT = 44
# A type alias shorter than this is left written out: `FieldRef` is `str | int`, which appears in
# many places that aren't about fields.
_MIN_ALIAS_LENGTH = 24


@functools.cache
def _alias_texts() -> tuple[tuple[str, str], ...]:
    """Each public type alias as Python writes it out, longest first, with its name."""
    texts = [(build_llms.clean_types(repr(getattr(pyquickbase, name))), name) for name in ALIASES]
    found = [(text, name) for text, name in texts if len(text) >= _MIN_ALIAS_LENGTH]
    return tuple(sorted(found, key=lambda item: -len(item[0])))


def _clean_annotation(annotation: Any) -> str:
    """Write a type for the docs, by its public alias where it has one, such as `Verb`.

    Python resolves an alias such as `verb: Verb` to what it stands for, which can be dozens of
    values. A string annotation is written as the type it names.
    """
    text = annotation if isinstance(annotation, str) else inspect.formatannotation(annotation)
    text = build_llms.clean_types(text)
    for expanded, name in _alias_texts():
        text = text.replace(expanded, name)
    return text


@functools.cache
def _ruff_format(source: str) -> str | None:
    """Format Python source as ruff would, to `LINE_LIMIT`, or None if it isn't valid Python."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--isolated", "--line-length", str(LINE_LIMIT),
         "--stdin-filename", "reference.py", "-"],
        input=source, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def format_code(code: str) -> str:
    """Format a signature, a type alias, or a property, so no line is longer than `LINE_LIMIT`.

    A signature such as `query(*, where: Expression | None = None) -> RecordIterator` isn't a
    statement on its own, so it's formatted as a function and then unwrapped. Anything that
    can't be parsed is returned as it was, which the test of line lengths would catch.
    """
    if re.match(r"^\w+\(", code):
        formatted = _ruff_format(f"def {code}: ...")
        if formatted is not None:
            unwrapped = re.sub(r":\s*\.\.\.$", "", formatted.removeprefix("def "))
            return _parenthesize_unions(unwrapped)
        return code
    return _ruff_format(code) or code


_SPLIT_PARAMETER = re.compile(r"^    (\*{0,2}\w+): (.+)$")


def _parenthesize_unions(code: str) -> str:
    """Put a parameter's union in parentheses where ruff split it, as black does.

    ruff writes a long union as `tables: str` with `| TableSelector` on the next lines, at the
    same indent as the parameters, so the type is hard to pick out. This puts it on one line
    inside parentheses if it fits there, or else one member per line inside them.
    """
    lines = code.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        found = _SPLIT_PARAMETER.match(lines[index])
        if not found or index + 1 == len(lines) or not lines[index + 1].startswith("    | "):
            out.append(lines[index])
            index += 1
            continue
        members = [found[2]]
        index += 1
        while index < len(lines) and lines[index].startswith("    | "):
            members.append(lines[index].removeprefix("    | "))
            index += 1
        last, equals, default = members[-1].partition(" = ")
        members[-1] = last.removesuffix(",")
        after = f" = {default}" if equals else ","
        joined = " | ".join(members)
        out.append(f"    {found[1]}: (")
        if 8 + len(joined) <= LINE_LIMIT:
            out.append(f"        {joined}")
        else:
            out += [f"        {members[0]}", *(f"        | {member}" for member in members[1:])]
        out.append(f"    ){after}")
    return "\n".join(out)


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
    """The return type, or nothing for an overloaded function, whose signatures show each one."""
    if get_overloads(function):
        return ""
    text = _clean_annotation(inspect.signature(function).return_annotation)
    return owner_class if text == "Self" else text


def _signature(name: str, function: Any, returns: str) -> str:
    """Write a signature formatted to fit the code column, as `format_code()` does.

    An overloaded function shows each overload, as the one that runs only says `Any`.
    """
    if overloads := get_overloads(function):
        return "\n".join(
            _one_signature(name, item, _clean_annotation(inspect.signature(item).return_annotation))
            for item in overloads
        )
    return _one_signature(name, function, returns)


def _one_signature(name: str, function: Any, returns: str) -> str:
    parameters = _own_parameters(function)
    items: list[str] = []
    for index, parameter in enumerate(parameters):
        # A bare `*` starts keyword-only parameters, unless `*args` already has.
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY and (
            index == 0
            or parameters[index - 1].kind
            not in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_POSITIONAL)
        ):
            items.append("*")
        default = (
            "" if parameter.default is inspect.Parameter.empty else f" = {parameter.default!r}"
        )
        star = "*" if parameter.kind is inspect.Parameter.VAR_POSITIONAL else ""
        items.append(f"{star}{parameter.name}: {_annotation(parameter)}{default}")
    suffix = f" -> {returns}" if returns else ""
    return format_code(f"{name}({', '.join(items)}){suffix}")


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
    """Stop the build if a public name has no page, or a page lists one that isn't public.

    It also stops it if a page isn't in exactly one of `SECTIONS`.
    """
    listed = [slug for section in SECTIONS for slug, _ in section.pages]
    if sorted(listed) != sorted(PAGES_BY_SLUG):
        raise SystemExit(f"Each page needs to be in exactly one section: {sorted(listed)}")
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
        format_code(f"{name}: {returns}") if is_property else _signature(name, function, returns),
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


def _type_of(item: object) -> type:
    return type(item)


def _namespace_methods(name: str, cls: type) -> list[Method]:
    """The methods of a namespace object, such as `allow.read()`, including inherited ones."""
    members: dict[str, Any] = {}
    for klass in reversed(cls.__mro__[:-1]):
        for key, member in vars(klass).items():
            if not key.startswith("_") and (
                isinstance(member, staticmethod) or inspect.isfunction(member)
            ):
                members[key] = member
    methods: list[Method] = []
    for key, member in members.items():
        function = getattr(member, "__func__", member)
        owner = f"{name}.{key}"
        doc = parse_docstring(inspect.getdoc(function), owner)
        returns = _return_type(function, name)
        parameters = _parameters(function, doc, owner)
        missing = [parameter.name for parameter in parameters if not parameter.description]
        if missing:
            raise SystemExit(f"{owner} doesn't describe these arguments: {missing}")
        methods.append(
            Method(key, f"{name}-{key}", f"{name}.{key}()", False,
                   _signature(key, function, returns), returns, doc, parameters, "", "", "")
        )  # fmt: skip
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
        definition = format_code(f"{name} = {build_llms.clean_types(repr(item))}")
        return Symbol(name, doc, definition, "", "", [], [], [], [], "", "", "")
    doc = parse_docstring(inspect.getdoc(item), name)
    if not inspect.isclass(item) and not inspect.isfunction(item):
        # A namespace of functions, such as `allow`, shown by its methods under its own name.
        return Symbol(
            name, doc, "", "", "", [], [], [], _namespace_methods(name, _type_of(item)),
            sync_code, async_code, output,
        )  # fmt: skip
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
    text = text.replace("](guides/", "](../guides/").replace("](agents/", "](../agents/")
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


def _shown_parameters(parameters: list[Parameter], methods: dict[str, Method]) -> list[str]:
    """Write the first few parameters, and fold the rest away when there are many."""
    if len(parameters) <= MAX_SHOWN_PARAMETERS + 1:
        return _parameter_lines(parameters, methods)
    shown, folded = parameters[:MAX_SHOWN_PARAMETERS], parameters[MAX_SHOWN_PARAMETERS:]
    names = ", ".join(f"<code>{parameter.name}</code>" for parameter in folded)
    return [
        *_parameter_lines(shown, methods),
        '<details class="ref-more" markdown>',
        f"<summary>{len(folded)} more: {names}</summary>",
        "",
        *_parameter_lines(folded, methods),
        "</details>",
        "",
    ]


def _describe(
    method: Method, methods: dict[str, Method], *, two_columns: bool = False
) -> list[str]:
    """Write a method's description, parameters, return value, and errors as Markdown.

    With `two_columns`, the signature is left out, as it goes beside the description with the
    example, and long parameter lists are folded.
    """
    doc = method.doc
    lines = _prose_lines(doc, methods)
    if method.name in ASYNC_NAMES:
        lines += [f"The async client calls this `{ASYNC_NAMES[method.name]}()`.", ""]
    if not two_columns:
        lines += [f"```python\n{method.signature}\n```", ""]
    if method.parameters:
        listed = (_shown_parameters if two_columns else _parameter_lines)(
            method.parameters, methods
        )
        lines += ["**Parameters**", "", *listed]
    if not method.is_property:
        lines += _return_lines(method.returns, doc, methods)
    return lines + _raises_lines(doc, methods)


def _describe_symbol(
    symbol: Symbol, *, anchors: bool, example: list[str] | None = None
) -> list[str]:
    """Write a public name's description as Markdown, with any example before its methods."""
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
    lines += example or []
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


def _output_lines(output: str) -> list[str]:
    if not output:
        return []
    return ['<p class="ref-label">Output</p>', "", f"```text\n{output}\n```", ""]


def _example_lines(sync_code: str, async_code: str, output: str) -> list[str]:
    """Write an example below its description, with a tab for each client."""
    if not sync_code:
        return []
    label = ['<p class="ref-label">Example</p>', ""]
    return label + _tabs(sync_code, async_code) + _output_lines(output)


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


def _page_header(page: Resource | SymbolPage, methods: dict[str, Method]) -> list[str]:
    """Write the title, with links to the page as Markdown, and the introduction."""
    lines = [
        '<p class="ref-actions">'
        f'<a class="ref-markdown" href="../{page.slug}.txt" '
        'title="Open this page as plain Markdown">Markdown</a>'
        '<button class="ref-copy" type="button" '
        'title="Copy this page as Markdown, to give to an LLM" hidden>Copy for LLM</button>'
        "</p>",
        "",
        f"# {page.name}",
        "",
        _add_links(page.intro, methods),
        "",
        _add_links(page.note, methods),
        "",
    ]
    return lines + (_tabs(page.setup, page.async_setup) if page.setup else [])


def _two_column_method(method: Method, methods: dict[str, Method], title: str) -> list[str]:
    """Write a method as two columns: what it does on the left, and its code on the right."""
    code = [f"```python\n{method.signature}\n```", ""]
    code += _example_lines(method.sync_code, method.async_code, method.output)
    text = [f"### {title} {{#{method.anchor}}}", "", *_describe(method, methods, two_columns=True)]
    return _row(text, code)


def _row(text: list[str], code: list[str]) -> list[str]:
    """Write one entry as two columns: what it is on the left, and its code on the right."""
    return [
        '<div class="ref-method" markdown>',
        '<div class="ref-text" markdown>',
        "",
        *text,
        "</div>",
        '<div class="ref-code" markdown>',
        "",
        *code,
        "</div>",
        "</div>",
        "",
    ]


def _two_column_header(page: Resource | SymbolPage, methods: dict[str, Method]) -> list[str]:
    """Write the front matter, title, and introduction, with the setup code beside it."""
    header = _page_header(page, methods)
    split = header.index(f"# {page.name}") + 2
    intro = [line for line in header[split:] if not line.startswith(("===", "    "))]
    front = ["---", "toc_in_nav: true", "hide:", "  - toc", "---", ""]
    if not page.setup:
        return [*front, *header[:split], *intro]
    row = _row(intro, _tabs(page.setup, page.async_setup))
    row[0] = '<div class="ref-method ref-intro" markdown>'
    return [*front, *header[:split], *row]


def _check_titles(slug: str, called: list[str]) -> dict[str, str]:
    """Return a page's titles, stopping the build if what's called and what's titled differ."""
    titles = TITLES.get(slug, {})
    untitled = [name for name in called if name not in titles]
    unknown = sorted(set(titles) - set(called))
    if untitled or unknown:
        raise SystemExit(
            f"TITLES[{slug!r}] needs a title for {untitled or 'nothing'}, "
            f"and has titles for names not called on the page: {unknown or 'none'}"
        )
    return titles


def build_page(resource: Resource, methods: dict[str, Method]) -> str:
    """Return a class's page for the site, in two columns, with its contents in the left sidebar.

    Each method's heading is its title from `TITLES`, and each method's "Returns" links to what it
    returns, so there's no table of return types.
    """
    members = [name for group in resource.groups for name in group.members]
    titles = _check_titles(resource.slug, members)
    lines = _two_column_header(resource, methods)
    for group in resource.groups:
        lines += [f"## {group.title} {{#{group.anchor}}}", "", *_guide_line(group)]
        for name in group.members:
            lines += _two_column_method(methods[name], methods, titles[name])
    return "\n".join(lines)


def _is_called(name: str, symbol: Symbol) -> bool:
    """Whether a public name is a function, so it's titled by what it does."""
    return bool(symbol.signature) and not name[0].isupper()


def _symbol_text(symbol: Symbol) -> list[str]:
    """Write what a public name is, for the left column: everything but its code."""
    doc = symbol.doc
    none: dict[str, Method] = {}
    lines = _prose_lines(doc, none)
    if symbol.bases:
        linked = ", ".join(_public_link(base) for base in symbol.bases)
        lines += [f"**Inherits from** {linked}", ""]
    if symbol.parameters:
        lines += ["**Parameters**", "", *_shown_parameters(symbol.parameters, none)]
    if symbol.attributes:
        lines += ["**Attributes**", "", *_attribute_lines(symbol.attributes, none)]
    return lines + _return_lines(symbol.returns, doc, none) + _raises_lines(doc, none)


def build_symbol_page(page: SymbolPage, symbols: dict[str, Symbol]) -> str:
    """Return a page of several public names for the site, laid out like a class's page.

    Each name is a row, with its signature and example beside it, and each of its methods is a row
    below it. A function or method is headed by its title, and anything else by its name.
    """
    called = [
        *(name for name, symbol in symbols.items() if _is_called(name, symbol)),
        *(method.heading.removesuffix("()") for s in symbols.values() for method in s.methods),
    ]
    titles = _check_titles(page.slug, called)
    lines = _two_column_header(page, {})
    for group in page.groups:
        lines += [f"## {group.title} {{#{group.anchor}}}", "", *_guide_line(group)]
        for name in group.members:
            symbol = symbols[name]
            code = [f"```python\n{symbol.signature}\n```", ""] if symbol.signature else []
            if symbol.sync_code:
                code += _example_lines(symbol.sync_code, symbol.async_code, symbol.output)
            elif symbol.definition:
                code += [f"```python\n{symbol.definition}\n```", ""]
            heading = titles[name] if _is_called(name, symbol) else f"`{name}`"
            lines += _row([f"### {heading} {{#{name}}}", "", *_symbol_text(symbol)], code)
            for method in symbol.methods:
                title = titles[method.heading.removesuffix("()")]
                text = [f"#### {title} {{#{method.anchor}}}", ""]
                text += _describe(method, {}, two_columns=True)
                lines += _row(text, [f"```python\n{method.signature}\n```", ""])
    return "\n".join(lines)


def build_overview() -> str:
    """Return the reference's overview: a card for each section, then every public name."""
    lines = [
        "# API reference",
        "",
        "Every public class, function, and type, with a tested example for both clients. Each "
        "page also has a plain Markdown copy for AI tools. Everything here is public, as "
        "[Stability and versioning](../stability.md) describes, and modules starting with `_` "
        "are private. The async classes have the same methods as the sync ones, which you "
        "`await`, so they're described together.",
        "",
        '<div class="grid cards ref-cards" markdown>',
        "",
    ]
    for section in SECTIONS:
        lines += [f"-   **{section.title}**", "", f"    {section.about}", ""]
        for slug, about in section.pages:
            lines.append(f"    - [{PAGES_BY_SLUG[slug].name}]({slug}.md) {about}")
        for title, path, about in section.extra:
            lines.append(f"    - [{title}](../{path}) {about}")
        lines.append("")
    lines += ["</div>", "", "## Every public name {#names}", ""]
    lines += ['<div class="ref-names" markdown>', ""]
    for name in sorted(NAME_LINKS, key=str.casefold):
        lines.append(f"- [`{name}`]({NAME_LINKS[name]})")
    return "\n".join([*lines, "", "</div>", ""])


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
    if page.setup and both:
        lines += [
            f"Sync:\n\n```python\n{page.setup}\n```",
            "",
            f"Async:\n\n```python\n{page.async_setup}\n```",
            "",
        ]
    elif page.setup:
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
    for slug in (slug for section in SECTIONS for slug, _ in section.pages):
        page = PAGES_BY_SLUG[slug]
        if isinstance(page, Resource):
            text = build_markdown(page, read_methods(page), site_url, both_clients=False)
        else:
            text = build_symbol_markdown(page, read_symbols(page), site_url, both_clients=False)
        pages.append((page.name, slug, text))
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
    (OUTPUT / "index.md").write_text(build_overview(), encoding="utf-8")
    print(f"Wrote {len(ALL_PAGES)} pages and the overview to docs/reference/")


if __name__ == "__main__":
    main()
