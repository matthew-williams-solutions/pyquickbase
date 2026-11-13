"""Write the API reference pages, ready for `zensical build`.

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

import ast
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
import pyquickbase.errors
from pyquickbase import _output  # pyright: ignore[reportPrivateUsage]

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "reference"
EXAMPLES = ROOT / "docs_src" / "reference"
CONSTRUCTOR = "__init__"
# Beyond this many parameters, a class's page folds the rest away.
MAX_SHOWN_PARAMETERS = 3
# What each method and function is called in its heading and the sidebar, by its qualified name:
# what it does, in plain words, with its name beside it. The same verb means the same kind of
# action on every page: "Get or create" for an ensure_ method, and "Link to" for a URL. A property
# is named by what it is. An object, error, or type keeps its name, as that's how it appears in
# signatures and tracebacks.
TITLES: dict[str, str] = {
    "Quickbase.__init__": "Create a client",
    "Quickbase.from_env": "Create a client from the environment",
    "Quickbase.close": "Close a client",
    "Quickbase.app": "Get an app",
    "Quickbase.apps": "List apps",
    "Quickbase.current_user": "Find out who the token belongs to",
    "Quickbase.find_user": "Find a user by email",
    "Quickbase.create_app": "Create an app",
    "Quickbase.realm": "The client's realm",
    "Quickbase.policy": "The client's policy",
    "Quickbase.clear_metadata_cache": "Clear the metadata cache",
    "Quickbase.request": "Call any endpoint",
    "App.id": "The app's ID",
    "App.info": "Get the app's details",
    "App.tables": "List tables",
    "App.roles": "List roles",
    "App.members": "List members",
    "App.code_pages": "List code pages",
    "App.code_page": "Get a code page",
    "App.member_roles": "List a member's roles",
    "App.create_role_assignments": "Give roles",
    "App.delete_role_assignments": "Take away roles",
    "App.update_role_assignments": "Move members between roles",
    "App.triggers": "List webhooks and automations",
    "App.table": "Get a table",
    "App.url": "Link to the app",
    "App.settings_url": "Link to the app's settings",
    "App.ensure_table": "Get or create a table",
    "App.create_table": "Create a table",
    "App.preview_schema_changes": "Preview schema changes",
    "App.apply_schema_changes": "Apply schema changes",
    "App.operation": "Make an app tool for a model",
    "App.copy": "Copy the app",
    "App.drop": "Delete the app",
    "Report.run": "Run a report",
    "Report.run_page": "Fetch one page of a report",
    "Report.run_summary": "Get a summary report's rows",
    "Report.info": "Get the report's details",
    "Report.url": "Link to the report",
    "TableOperation.input_schema": "The arguments' JSON Schema",
    "TableOperation.name": "The operation's name",
    "TableOperation.run": "Run a table operation",
    "CodePage.read": "Read the page",
    "CodePage.preview_change": "Preview a change",
    "CodePage.write": "Write the page",
    "CodePage.url": "Link to the page",
    "AppOperation.input_schema": "The arguments' JSON Schema",
    "AppOperation.name": "The operation's name",
    "AppOperation.run": "Run an app operation",
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
    "Policy.read_only": "Make a read-only policy",
    "Policy.bind": "Bind to an app",
    "Policy.bind_offline": "Bind without an app",
    "BoundPolicy.tags_of": "Get a field's tags",
    "BoundPolicy.decide": "Decide a request",
    "BoundPolicy.allows": "Check what's allowed",
    "BoundPolicy.explain": "Explain what's allowed",
    "BoundPolicy.max_records": "Get a table's record limit",
    "BoundPolicy.row_limit": "Get a table's row limit",
    "BoundPolicy.offers": "Get what an operation may offer",
    "BoundPolicy.to_json": "Save a bound policy",
    "BoundPolicy.from_json": "Load a bound policy",
    "PolicyDecision.message": "Get the refusal's message",
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
    "allow.code_pages": "Allow code pages",
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
    "deny.code_pages": "Deny code pages",
    "deny.all": "Deny everything",
    "limit.rows": "Limit the rows",
    "fields.all": "Select every field",
    "fields.new": "Select new fields",
    "fields.tagged": "Select fields by class",
    "fields.labeled_like": "Select fields by label",
    "tables.all": "Select every table",
    "tables.created": "Select created tables",
    "CustomRule.decide": "Refuse a request",
    "FieldUsage.describe": "Describe a field's uses in words",
    "RecordIterator.to_pandas": "Fetch records into pandas",
    "RecordIterator.to_polars": "Fetch records into polars",
    "RecordIterator.to_csv": "Write records to a CSV file",
    "RecordIterator.to_excel": "Write records to an Excel file",
    "RecordPage.to_csv": "Write a page to a CSV file",
    "RecordPage.to_excel": "Write a page to an Excel file",
    "FileRows.raise_for_failures": "Refuse a file with errors",
    "RecordPage.to_pandas": "Turn a page into pandas",
    "RecordPage.to_polars": "Turn a page into polars",
    "SummaryRow.statistic": "Get one statistic's values",
    "RetryPolicy.backoff": "Get the backoff delay",
    "RetryPolicy.rate_limit_delay": "Get the wait after a 429",
    "tool_result": "Return a result to a model",
    "tool_error": "Return an error to a model",
    "Table.query": "Query records",
    "Table.query_page": "Fetch one page of records",
    "Table.first": "Get the first matching record",
    "Table.get": "Get a record by ID",
    "Table.count": "Count records",
    "Table.compile_where": "Show a condition's query text",
    "Table.insert": "Create records",
    "Table.update": "Update records",
    "Table.upsert": "Create or update records",
    "Table.delete": "Delete records",
    "Table.preview_update": "Preview an update",
    "Table.preview_delete": "Preview a delete",
    "Table.summarize": "Summarize records",
    "Table.run_formula": "Run a formula",
    "Table.info": "Get the table's details",
    "Table.fields": "List fields",
    "Table.relationships": "List relationships",
    "Table.reports": "List reports",
    "Table.triggers": "List webhooks and automations",
    "Table.field_usage": "See where fields are used",
    "Table.rows_from_file": "Read rows from a CSV or Excel file",
    "Table.report": "Get a report",
    "Table.resolve_field_ids": "Turn field labels into IDs",
    "Table.download_file": "Download a file",
    "Table.delete_file_version": "Delete a file version",
    "Table.operation": "Make a tool for a model",
    "Table.url": "Link to the table",
    "Table.record_url": "Link to a record",
    "Table.record_url_template": "Link to any record",
    "Table.new_record_url": "Link to a new record",
    "Table.field_url": "Link to a field's settings",
    "Table.fields_url": "Link to the list of fields",
    "Table.relationships_url": "Link to the relationships",
    "Table.settings_url": "Link to the table's settings",
    "Table.form_url": "Link to a form",
    "Table.create_field": "Create a field",
    "Table.update_field": "Update a field",
    "Table.delete_fields": "Delete fields",
    "Table.ensure_field": "Create or update a field",
    "Table.create_relationship": "Create a relationship",
    "Table.ensure_relationship": "Get or create a relationship",
    "Table.ensure_lookup_field": "Get or create a lookup field",
    "Table.ensure_summary_field": "Get or create a summary field",
    "Table.ensure_count_field": "Get or create a count field",
    "Table.delete_relationship": "Delete a relationship",
    "Table.update_info": "Update the table's details",
    "Table.drop": "Delete the table",
}
# Methods that the async client names differently.
ASYNC_NAMES = {"close": "aclose"}

# Classes you make yourself, so they show a constructor rather than a list of attributes.
HAS_PUBLIC_CONSTRUCTOR = {
    "F",
    "FileUpload",
    "Period",
    "Policy",
    "PolicyDecision",
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
    "SetKeyField",
    "RoleAssignment",
    "RoleAssignmentUpdate",
    "RetryPolicy",
}
# Public so that the SDK can call them across modules, and of no use from outside it.
INTERNAL_METHODS = {"WhereExpression.compile"}
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
    "SchemaChangeAction": (
        "The `action` of a change in the JSON form a model sends, such as `create_field`."
    ),
    "SummaryFieldFunction": "How `CreateSummaryField` combines a child's values. `count` "
    "counts the child records.",
    "ChangeRisk": "How much a planned change can lose: `additive` only adds, `modifying` changes "
    "what's there, and `destructive` deletes data or fields.",
    "AppOperationName": "What `App.operation()` can offer a model.",
    "DerivedKind": "A field whose values come from other fields: a `formula`, a `lookup` of a "
    "parent's field, or a `summary` of a child's. `WriteRequest.derived` lists the ones a write "
    "makes or changes.",
    "ReadAction": "What kind of read a `ReadRequest` is.",
    "TableOperationName": "What `Table.operation()` can offer a model, each running the table or "
    "report method of the same name.",
    "ToolErrorCode": "The `code` in what `tool_error()` returns. Codes don't change within 1.x, so "
    "a tool can act on them.",
    "PolicyVerb": "What a policy grants or refuses, such as `read`, `update`, or `lookup`.",
    "PolicySchemaVerb": "What `allow.schema()` grants on a table, where the change lands.",
    "DecisionLayer": "Which check made a `PolicyDecision`: `policy`, `rows`, or `derived`.",
}
# What can't be shown happening in an example, and why.
NO_EXAMPLE = {
    "SchemaChangeError": "this needs a change to fail partway through a plan, which "
    "tests/_async/test_schema_plan.py shows",
    "NetworkError": "`RequestTimeoutError` shows catching this",
    "InvalidResponseError": "only Quickbase returning something unexpected raises this",
    "PermissionDeniedError": "this needs a second token with less access",
    "WritePermissionDeniedError": "this needs a second token with less access",
    "RateLimitError": "this needs a real burst of requests",
    "ServerError": "only a failure at Quickbase raises this",
    "NoLongerAvailableError": "only Quickbase retiring a call raises this",
}
# Shown on the page, but not run by the live tests, because of what it needs.
NOT_RUN_LIVE = {
    "report/run_summary": "A summary report can only be built in the Quickbase interface. "
    "tests/_async/live/test_sdk.py tests this method.",
    "quickbase/create_app": "It makes a lasting app. tests/_async/live/test_apps.py tests this "
    "method, and deletes what it makes.",
    "app/copy": "It makes a lasting app. tests/_async/live/test_apps.py tests this method, and "
    "deletes what it makes.",
    "app/drop": "It would delete the test app. tests/_async/live/test_apps.py tests this method "
    "on a copy.",
}


class ClassSource(NamedTuple):
    """A class whose methods the reference shows, with its examples in docs_src/reference/<slug>/.

    Its methods can be on several pages, as each is shown where its subject is.
    """

    name: str
    slug: str
    sync_class: type
    async_class: type
    about: str
    # How to get one, for the async client, shown beside `about`.
    get: str


_LIVE_TEST = "A live test runs every example with both clients and checks its output."
_JOB_TASKS = (
    "`Job Tasks` has `Title`, `Status`, `Hours`, `Due Date`, `Code`, which is unique, and "
    "`Attachment`. It starts with three records, and each example starts from those again."
)
_READING = ("Reading records", "../guides/reading.md")
_SUMMARIES = ("Summarizing records", "../guides/summaries.md")
_REPORTS = ("Running reports", "../guides/reports.md")
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
_OPERATIONS = ("Giving a model operations", "../agents/operations.md")
_RESULTS = ("Results and errors", "../agents/results.md")
_ERRORS = ("Handling errors", "../guides/errors.md")
_APPS = ("Working with apps", "../guides/apps.md")
_CODE_PAGES = ("Code pages", "../guides/code-pages.md")
_SPREADSHEETS = ("CSV and Excel files", "../guides/spreadsheets.md")

_CODE_PAGE_WARNING_TEXT = (
    "A code page runs in the browser of whoever opens it, with that person's permissions. A "
    "client's policy controls what the client may do, and has no say in what a page does once "
    "someone opens it."
)

CLASSES = (
    ClassSource(
        "Quickbase",
        "quickbase",
        pyquickbase.Quickbase,
        pyquickbase.AsyncQuickbase,
        "`Quickbase` is the client that everything else starts from. Use it as a context manager, "
        "so that its connections get closed. `AsyncQuickbase` takes the same options, except that "
        "`http_client` is an `httpx.AsyncClient`, and has the same methods, which you `await` "
        "where the examples show it.",
        'async with AsyncQuickbase(\n    realm="acme", token="your_user_token"\n) as qb:\n'
        "    print(qb.realm)",
    ),
    ClassSource(
        "App",
        "app",
        pyquickbase.App,
        pyquickbase.AsyncApp,
        "An `App` is one app: its details, tables, roles, and code pages. Get one from "
        "`Quickbase.app()`. Getting one doesn't contact Quickbase, so an ID that doesn't exist "
        "only fails on first use. `AsyncApp` has the same methods.",
        'app = qb.app("bq1234567")',
    ),
    ClassSource(
        "Table",
        "table",
        pyquickbase.Table,
        pyquickbase.AsyncTable,
        "A `Table` is one table: its records, fields, reports, and files. Get one from "
        "`App.table()`, by name or ID. Getting one doesn't contact Quickbase, so a table that "
        "doesn't exist raises `UnknownTableError` on first use. `AsyncTable` has the same "
        "methods.",
        'tasks = qb.app("bq1234567").table("Job Tasks")',
    ),
    ClassSource(
        "Report",
        "report",
        pyquickbase.Report,
        pyquickbase.AsyncReport,
        "A `Report` runs a report that was built in the Quickbase interface. Get one from "
        "`Table.report()`, by name or ID. Getting one doesn't contact Quickbase, so a report that "
        "doesn't exist raises `UnknownReportError` on first use. `AsyncReport` has the same "
        "methods.",
        'report = tasks.report("List All")',
    ),
    ClassSource(
        "CodePage",
        "code-page",
        pyquickbase.CodePage,
        pyquickbase.AsyncCodePage,
        "A `CodePage` reads, previews, and writes one of an app's code pages. Get one from "
        f"`App.code_page()`, by name or ID. {_CODE_PAGE_WARNING_TEXT} `AsyncCodePage` has the "
        "same methods.",
        'page = app.code_page("pyquickbase-live-test.html")',
    ),
    ClassSource(
        "TableOperation",
        "table-operation",
        pyquickbase.TableOperation,
        pyquickbase.AsyncTableOperation,
        "A `TableOperation` is one table operation offered to a model as a tool: a JSON Schema of "
        "the arguments it may send, and `run()`, which checks them against the same lists before "
        "calling the table. Get one from `Table.operation()`. `AsyncTableOperation` has the same "
        "methods.",
        'search = await tasks.operation(\n    "query", fields=["Title", "Status"]\n)',
    ),
    ClassSource(
        "AppOperation",
        "app-operation",
        pyquickbase.AppOperation,
        pyquickbase.AsyncAppOperation,
        "An `AppOperation` is one app-wide operation offered to a model as a tool, for schema "
        "changes or code pages. Get one from `App.operation()`. The `apply_` operations only run "
        "with the confirmation their preview returned for the same arguments, while the app is as "
        f"it was. {_CODE_PAGE_WARNING_TEXT} `AsyncAppOperation` has the same methods.",
        'plan = await app.operation("preview_schema_changes")',
    ),
)
CLASS_BY_NAME = {source.name: source for source in CLASSES}
# Folders of examples for public names other than these classes, each named `<name>.py`. The live
# test's setup for each folder is keyed by its name, so an example stays in its folder whichever
# page shows it.
SYMBOL_DIRS = (
    "queries",
    "records",
    "metadata",
    "schema-changes",
    "policies",
    "rules",
    "retries",
    "tools",
    "errors",
)
# Folders whose examples make their own client from the environment, rather than using the
# page's setup.
OWN_CLIENT_DIRS = {"policies", "rules", "retries", "tools"}
EXAMPLE_DIRS = (*(source.slug for source in CLASSES), *SYMBOL_DIRS)

# What a page's setup can make, for the async client, in order, and what each needs first. The live
# test gives every example `qb`, `app`, `tasks`, and `projects`, and the rest by folder.
SETUP_LINES = {
    "app": 'app = qb.app("bq1234567")',
    "tasks": 'tasks = app.table("Job Tasks")',
    "projects": 'projects = app.table("Job Projects")',
    "search": 'search = await tasks.operation(\n    "query", fields=["Title", "Status"]\n)',
    "plan": 'plan = await app.operation("preview_schema_changes")',
    "page": 'page = app.code_page("pyquickbase-live-test.html")',
}
SETUP_NEEDS = {
    "tasks": "app",
    "projects": "app",
    "search": "tasks",
    "plan": "app",
    "page": "app",
}


class Group(NamedTuple):
    """A section of a page: its methods and public names, in order, with the guide on them.

    A member is a class's method as `Table.upsert`, its constructor as `Quickbase.__init__`, a
    class itself as `Table`, or any other public name, such as `WriteResult`.
    """

    title: str
    anchor: str
    guide: tuple[str, str] | None
    members: tuple[str, ...]


class Page(NamedTuple):
    """A page of the reference about one subject, with everything you call and get back for it."""

    name: str
    slug: str
    intro: str
    groups: tuple[Group, ...]
    # Said about the examples, beyond what the generated note says.
    note: str = ""


_LINKS_GUIDE = ("Results and errors", "../agents/results.md")

PAGES = (
    Page(
        "Client",
        "client",
        "The client is where everything starts: it holds the token, sends each request, retries "
        "what it can, and caches what describes the app.",
        (
            Group(
                "Create and close a client",
                "create-and-close",
                _CLIENTS,
                ("Quickbase", "Quickbase.__init__", "Quickbase.from_env", "Quickbase.close"),
            ),
            Group(
                "Use a client",
                "use-a-client",
                _CLIENTS,
                (
                    "Quickbase.app",
                    "Quickbase.realm",
                    "Quickbase.policy",
                    "Quickbase.clear_metadata_cache",
                    "Quickbase.request",
                ),
            ),
            Group("Retry requests", "retries", _CLIENTS, ("RetryPolicy",)),
        ),
    ),
    Page(
        "Errors",
        "errors",
        "Everything the SDK raises derives from `QuickbaseError`, and each error's `doc_url` links "
        "to its section here, which says what causes it and how to fix it. Errors that mean a "
        "request wasn't sent come first, then the ones for a request that failed. A timed-out "
        "write may still have been applied.",
        (
            Group("The base class", "base", _ERRORS, ("QuickbaseError",)),
            Group(
                "Mistakes caught before sending",
                "before-sending",
                _ERRORS,
                (
                    "ConfigurationError",
                    "InvalidQueryError",
                    "SummaryTooLargeError",
                    "InvalidConfirmationError",
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
                    "CodePageChangedError",
                ),
            ),
            Group(
                "Writes",
                "writes",
                _WRITING,
                ("WriteError", "WritePermissionDeniedError", "SchemaChangeError"),
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
                    "NoLongerAvailableError",
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
    Page(
        "Records",
        "records",
        "Read a table's records, one page at a time or all of them, and what each record holds.",
        (
            Group(
                "Read records",
                "read-records",
                _READING,
                ("Table.query", "Table.query_page", "Table.first", "Table.get", "Table.count"),
            ),
            Group(
                "What reads return",
                "read-results",
                _READING,
                ("Record", "RecordIterator", "RecordPage", "User"),
            ),
        ),
    ),
    Page(
        "Writes",
        "writes",
        "Create, update, and delete records, preview a change before making it, and what each "
        "write reports back.",
        (
            Group(
                "Write records",
                "write-records",
                _WRITING,
                ("Table.insert", "Table.update", "Table.upsert", "Table.delete"),
            ),
            Group(
                "What writes return", "write-results", _WRITING, ("WriteResult", "WriteRowFailure")
            ),
            Group(
                "Preview writes",
                "previews",
                _WRITING,
                (
                    "Table.preview_update",
                    "Table.preview_delete",
                    "UpdatePreview",
                    "DeletePreview",
                    "RecordChange",
                    "ValueChange",
                ),
            ),
        ),
    ),
    Page(
        "Queries",
        "queries",
        "Conditions choose which records a query, count, or delete applies to. Build them with "
        "`F`, which escapes values for you, or with `where_from_dict()` for plain data from a "
        "model or a web form.",
        (
            Group(
                "Build a condition",
                "build-a-condition",
                _READING,
                ("F", "WhereExpression", "Table.compile_where"),
            ),
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
    Page(
        "Summaries and formulas",
        "summaries",
        "Totals, averages, and other statistics worked out by Quickbase, grouped by field or by "
        "period, and formulas run against a table without a formula field.",
        (
            Group(
                "Summarize records",
                "summarize",
                _SUMMARIES,
                (
                    "Table.summarize",
                    "SummaryRow",
                    "PeriodChange",
                    "Period",
                    "PeriodUnit",
                    "StatisticName",
                    "SummaryGroups",
                ),
            ),
            Group("Run a formula", "formulas", _FORMULAS, ("Table.run_formula", "FormulaResult")),
        ),
    ),
    Page(
        "Files and spreadsheets",
        "files",
        "Upload, download, and delete file attachments, and read rows from a CSV or Excel file "
        "with each value checked before anything is written.",
        (
            Group(
                "File attachments",
                "attachments",
                _FILES,
                (
                    "FileUpload",
                    "Table.download_file",
                    "Table.delete_file_version",
                    "FileDownload",
                    "FileAttachment",
                    "FileVersion",
                ),
            ),
            Group(
                "CSV and Excel files",
                "spreadsheets",
                _SPREADSHEETS,
                (
                    "Table.rows_from_file",
                    "FileRows",
                    "FileRowFailure",
                    "FileFormat",
                    "FileLike",
                    "ColumnMap",
                ),
            ),
        ),
    ),
    Page(
        "Reports",
        "reports",
        "Find and run the reports built in the Quickbase interface.",
        (
            Group(
                "Find a report",
                "find-a-report",
                _REPORTS,
                ("Report", "Table.reports", "Table.report", "Report.info", "ReportInfo"),
            ),
            Group(
                "Run a report",
                "run-a-report",
                _REPORTS,
                ("Report.run", "Report.run_page", "Report.run_summary"),
            ),
        ),
        "Every new table has a report named `List All`.",
    ),
    Page(
        "Apps",
        "apps",
        "Find, create, copy, and delete apps, and see and change who has which role.",
        (
            Group(
                "Find an app",
                "find-an-app",
                _APPS,
                ("App", "Quickbase.apps", "App.id", "App.info", "AppListing", "AppInfo"),
            ),
            Group(
                "Create, copy, and delete apps",
                "create-copy-delete",
                _APPS,
                ("Quickbase.create_app", "App.copy", "App.drop"),
            ),
            Group(
                "Users and roles",
                "users-and-roles",
                _APPS,
                (
                    "Quickbase.current_user",
                    "Quickbase.find_user",
                    "App.roles",
                    "App.members",
                    "App.member_roles",
                    "UserAccount",
                    "Role",
                    "AppMember",
                    "MemberRole",
                ),
            ),
            Group(
                "Change who has a role",
                "change-roles",
                _APPS,
                (
                    "App.create_role_assignments",
                    "App.delete_role_assignments",
                    "App.update_role_assignments",
                    "RoleAssignment",
                    "RoleAssignmentUpdate",
                    "RoleAssignmentResult",
                    "RoleAssignmentFailure",
                ),
            ),
            Group(
                "Webhooks and automations",
                "triggers",
                _APPS,
                ("App.triggers", "Table.triggers", "AppTrigger"),
            ),
        ),
    ),
    Page(
        "Tables",
        "tables",
        "Get an app's tables, create them, and change or delete them.",
        (
            Group(
                "Get a table",
                "get-a-table",
                _READING,
                ("Table", "App.table", "App.tables", "Table.info", "TableInfo"),
            ),
            Group(
                "Create and change tables",
                "change-tables",
                _SCHEMA,
                ("App.ensure_table", "App.create_table", "Table.update_info", "Table.drop"),
            ),
        ),
    ),
    Page(
        "Fields and relationships",
        "fields",
        "A table's fields, where each is used, and the relationships, lookups, and summaries that "
        "link tables. [Field types](../field-types.md) lists what each type holds.",
        (
            Group(
                "Look up fields",
                "look-up-fields",
                _SCHEMA,
                (
                    "Table.fields",
                    "Table.resolve_field_ids",
                    "Table.field_usage",
                    "Field",
                    "FieldUsage",
                    "TableSchema",
                ),
            ),
            Group(
                "Change fields",
                "change-fields",
                _SCHEMA,
                (
                    "Table.create_field",
                    "Table.update_field",
                    "Table.ensure_field",
                    "Table.delete_fields",
                ),
            ),
            Group(
                "Relationships",
                "relationships",
                _SCHEMA,
                (
                    "Table.relationships",
                    "Relationship",
                    "Table.create_relationship",
                    "Table.ensure_relationship",
                    "Table.delete_relationship",
                ),
            ),
            Group(
                "Lookups and summaries",
                "lookups-and-summaries",
                _SCHEMA,
                (
                    "Table.ensure_lookup_field",
                    "Table.ensure_summary_field",
                    "Table.ensure_count_field",
                ),
            ),
        ),
    ),
    Page(
        "Schema plans",
        "schema-plans",
        "Describe changes to an app's tables, fields, and relationships, then pass them to "
        "`App.preview_schema_changes()`, which says what each would do without making it, and "
        "`App.apply_schema_changes()`, which makes them if the app hasn't changed since. A change "
        "the app already has counts as done.",
        (
            Group(
                "Plan and apply",
                "plans",
                _SCHEMA,
                (
                    "App.preview_schema_changes",
                    "App.apply_schema_changes",
                    "SchemaPlan",
                    "PlannedChange",
                    "SchemaChangeResult",
                    "ChangeRisk",
                ),
            ),
            Group("Tables", "tables", _SCHEMA, ("CreateTable", "UpdateTable", "DeleteTable")),
            Group(
                "Fields",
                "fields",
                _SCHEMA,
                ("CreateField", "UpdateField", "DeleteField", "SetKeyField"),
            ),
            Group(
                "Relationships",
                "relationships",
                _SCHEMA,
                (
                    "CreateRelationship",
                    "CreateLookupField",
                    "CreateSummaryField",
                    "DeleteRelationship",
                    "SummaryFieldFunction",
                ),
            ),
            Group("Any change", "any-change", _AGENTS, ("SchemaChange", "SchemaChangeAction")),
        ),
        "The examples only plan, unless they say otherwise.",
    ),
    Page(
        "Code pages",
        "code-pages",
        f"Read, preview, and replace an app's code pages. {_CODE_PAGE_WARNING_TEXT}",
        (
            Group(
                "Find a code page",
                "find-a-page",
                _CODE_PAGES,
                ("CodePage", "App.code_pages", "App.code_page", "CodePageInfo"),
            ),
            Group(
                "Read and change a page",
                "read-and-change",
                _CODE_PAGES,
                (
                    "CodePage.read",
                    "CodePage.preview_change",
                    "CodePage.write",
                    "CodePageContent",
                    "CodePagePreview",
                    "CodePageWriteResult",
                ),
            ),
        ),
        "`pyquickbase-live-test.html` is a page the live tests keep.",
    ),
    Page(
        "Links",
        "links",
        "Links to apps, tables, records, forms, and settings in the Quickbase interface, for a "
        "person to open. Making one only contacts Quickbase to look up a table's or field's ID, "
        "which the client's cache usually has, and doesn't check that a record or form exists.",
        (
            Group("Apps", "apps", _LINKS_GUIDE, ("App.url", "App.settings_url")),
            Group(
                "Tables and fields",
                "tables-and-fields",
                _LINKS_GUIDE,
                (
                    "Table.url",
                    "Table.settings_url",
                    "Table.fields_url",
                    "Table.field_url",
                    "Table.relationships_url",
                ),
            ),
            Group(
                "Records and forms",
                "records-and-forms",
                _LINKS_GUIDE,
                (
                    "Table.record_url",
                    "Table.record_url_template",
                    "Table.new_record_url",
                    "Table.form_url",
                ),
            ),
            Group(
                "Reports and code pages",
                "reports-and-code-pages",
                _LINKS_GUIDE,
                ("Report.url", "CodePage.url"),
            ),
        ),
    ),
    Page(
        "Policies",
        "policies",
        "A `Policy` says what a client may read and change. Bind it to an app, which pins every "
        "table and field by ID, and pass the `BoundPolicy` to `Quickbase()` as `policy`. It "
        "protects against bugs in your code and mistakes by a model, so give your token access to "
        "as little as possible as well, as Quickbase's own permissions are still the real "
        "boundary.",
        (
            Group("Write and bind a policy", "policies", _POLICIES, ("Policy", "BoundPolicy")),
            Group(
                "Decisions",
                "decisions",
                _DEBUGGING,
                ("PolicyDecision", "NamedItem", "PolicyOffers"),
            ),
        ),
    ),
    Page(
        "Rules",
        "rules",
        "A policy is a list of rules. `allow` and `deny` name what a client may do with tables "
        "and fields, `limit` keeps it to some records, and a `CustomRule` of your own can refuse "
        "anything else. Anything no rule allows is refused, and a deny always wins.",
        (
            Group(
                "Allow, deny, and limit",
                "rules",
                _RULES,
                (
                    "allow",
                    "deny",
                    "limit",
                    "PolicyRule",
                    "PolicyVerb",
                    "PolicySchemaVerb",
                    "DecisionLayer",
                ),
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
                    "CustomRule",
                    "WriteRequest",
                    "ReadRequest",
                    "WriteAction",
                    "DerivedKind",
                    "ReadAction",
                ),
            ),
        ),
    ),
    Page(
        "Operations for models",
        "operations",
        "Operations offer a model a table's or an app's methods as tools, each with a JSON Schema "
        "of the arguments it may send, checked again when it runs. `tool_result()` and "
        "`tool_error()` turn what comes back into plain data, in the same shapes whatever the "
        "call, which `json.dumps()` always accepts, so they work with any tool-calling framework.",
        (
            Group(
                "Table operations",
                "table-operations",
                _OPERATIONS,
                (
                    "Table.operation",
                    "TableOperation",
                    "TableOperation.input_schema",
                    "TableOperation.name",
                    "TableOperation.run",
                    "TableOperationName",
                ),
            ),
            Group(
                "App operations",
                "app-operations",
                _OPERATIONS,
                (
                    "App.operation",
                    "AppOperation",
                    "AppOperation.input_schema",
                    "AppOperation.name",
                    "AppOperation.run",
                    "AppOperationName",
                ),
            ),
            Group(
                "Results and errors",
                "results",
                _RESULTS,
                ("tool_result", "tool_error", "ToolErrorCode"),
            ),
        ),
    ),
)


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
        "Client and errors",
        "The client, how it retries, and what it raises.",
        (
            ("client", "Create and close a client, retry requests, and call any endpoint"),
            ("errors", "Everything the SDK raises, what causes it, and how to fix it"),
        ),
    ),
    Section(
        "Data",
        "Reading and writing records, and what comes back.",
        (
            ("records", "Query records, and what each record holds"),
            ("writes", "Create, update, delete, and preview, and what each write reports"),
            ("queries", "Build conditions with `F`, or from plain data"),
            ("summaries", "Totals and other statistics by group or period, and formulas"),
            ("files", "File attachments, and rows from CSV and Excel files"),
            ("reports", "Find and run reports built in the Quickbase interface"),
        ),
    ),
    Section(
        "Apps and schema",
        "Apps, their tables and fields, and changes to them.",
        (
            ("apps", "Find, create, copy, and delete apps, and who has which role"),
            ("tables", "Get, create, change, and delete tables"),
            ("fields", "Fields, where they're used, relationships, lookups, and summaries"),
            ("schema-plans", "Changes to the schema, checked in a plan before they're made"),
            ("code-pages", "Read, preview, and write an app's code pages"),
            ("links", "Links to apps, tables, records, and settings in the Quickbase interface"),
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
        "Policies",
        "What a client may read and change.",
        (
            ("policies", "Write a policy, bind it, and ask what it allows"),
            ("rules", "Allow, deny, and limit tables, fields, and rows"),
        ),
    ),
    Section(
        "AI agents",
        "Tools for a model, and their results and errors as plain data.",
        (("operations", "Table and app operations, and results and errors for a tool call"),),
    ),
)
PAGES_BY_SLUG = {page.slug: page for page in PAGES}
# Where the reference's pages were before it was arranged by subject, for mkdocs.yml's redirects.
OLD_PAGES = {
    "quickbase": "client",
    "app": "apps",
    "table": "records",
    "report": "reports",
    "code-page": "code-pages",
    "metadata": "apps",
    "schema-changes": "schema-plans",
    "app-operation": "operations",
    "table-operation": "operations",
    "tools": "operations",
    "retries": "client",
}


def _split(member: str) -> tuple[str, str] | None:
    """Return `(class, method)` for a class's method, or None for anything else."""
    owner, dot, name = member.partition(".")
    return (owner, name) if dot and owner in CLASS_BY_NAME else None


def anchor_of(member: str) -> str:
    """Return a member's anchor: `Table-upsert` for a method, and the name for anything else."""
    split = _split(member)
    if split is None:
        return member
    owner, name = split
    return f"{owner}-constructor" if name == CONSTRUCTOR else f"{owner}-{name}"


def _members() -> list[tuple[str, str]]:
    """Return every page's members, as `(slug, member)`."""
    return [
        (page.slug, member) for page in PAGES for group in page.groups for member in group.members
    ]


def _method_links() -> dict[tuple[str, str], str]:
    """Return where each class's methods are, relative to a reference page."""
    return {
        split: f"{slug}.md#{anchor_of(member)}"
        for slug, member in _members()
        if (split := _split(member)) is not None
    }


def _name_links() -> dict[str, str]:
    """Return where each public name is documented, relative to a reference page."""
    links = {member: f"{slug}.md#{member}" for slug, member in _members() if _split(member) is None}
    # The async classes are described with their sync versions.
    for source in CLASSES:
        if source.name in links:
            links[source.async_class.__name__] = links[source.name]
    if "RecordIterator" in links:
        links["AsyncRecordIterator"] = links["RecordIterator"]
    return links


METHOD_LINKS = _method_links()
NAME_LINKS = _name_links()


def scope_of(owner: str) -> dict[str, str]:
    """Return where each of a class's methods is, for linking `name()` in its own docstrings."""
    return {name: link for (cls, name), link in METHOD_LINKS.items() if cls == owner}


_SECTIONS = {"Args:": "args", "Attributes:": "attrs", "Returns:": "returns", "Raises:": "raises"}
_ENTRY = re.compile(r"\*{0,2}(\w+): (.*)")
_NAME = re.compile(r"\b[A-Za-z_]\w*\b")
_CALL = re.compile(r"`(\w+)\(\)`")
_SEE = re.compile(r"See `(\w+)`")
# Not a name that an earlier pass has already linked, as in [`Quickbase`](quickbase.md).
_PUBLIC_CODE = re.compile(r"(?<!\[)`([A-Za-z]\w*)`")
_QUALIFIED = re.compile(r"`(" + "|".join(CLASS_BY_NAME) + r")\.(\w+)(\(\))?`")
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
    # The class or public name it belongs to, whether the async client awaits it, and the folder
    # of its example.
    owner: str
    awaited: bool
    slug: str


class Symbol(NamedTuple):
    """One public name other than a class with methods, whatever kind of thing it is."""

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
    # The folder of its example, if it has one.
    slug: str


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
    """Write a type for the docs, by its public alias where it has one, such as `PolicyVerb`.

    Python resolves an alias such as `verb: PolicyVerb` to what it stands for, which can be dozens
    of values. A string annotation is written as the type it names.
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

    A signature such as `query(*, where: WhereExpression | None = None) -> RecordIterator` isn't a
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


def _check_clients_match(source: ClassSource) -> None:
    expected = _shape(source.sync_class, names=ASYNC_NAMES)
    if expected != _shape(source.async_class, names={}):
        raise SystemExit(
            f"{source.sync_class.__name__} and {source.async_class.__name__} no longer have "
            "the same methods and arguments."
        )
    own = [p.name for p in _own_parameters(source.sync_class)]
    other = [p.name for p in _own_parameters(source.async_class)]
    if (source.name, CONSTRUCTOR) in METHOD_LINKS and own != other:
        raise SystemExit(f"The constructors of {source.name} differ between clients.")


def listed_methods(source: ClassSource) -> list[str]:
    """Return a class's methods in the order the pages list them, constructor included."""
    return [name for cls, name in METHOD_LINKS if cls == source.name]


def _check_methods_listed(source: ClassSource, listed: list[str]) -> None:
    public = set(_public_members(source.sync_class))
    wanted = [name for name in listed if name != CONSTRUCTOR]
    if set(wanted) != public:
        raise SystemExit(
            f"The pages don't list exactly the public methods of {source.name}: "
            f"{sorted(public.symmetric_difference(wanted))}"
        )


def check_every_name_is_covered() -> None:
    """Stop the build if a public name has no page, or a page lists one that isn't public.

    It also stops it if a page isn't in exactly one of `SECTIONS`, if something is on two pages,
    or if a class or one of its methods is on none.
    """
    listed = [slug for section in SECTIONS for slug, _ in section.pages]
    if sorted(listed) != sorted(PAGES_BY_SLUG):
        raise SystemExit(f"Each page needs to be in exactly one section: {sorted(listed)}")
    members = [member for _, member in _members()]
    twice = sorted({member for member in members if members.count(member) > 1})
    if twice:
        raise SystemExit(f"These are on more than one page: {twice}")
    for source in CLASSES:
        _check_methods_listed(source, listed_methods(source))
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


def _awaited(source: ClassSource, name: str) -> bool:
    """Whether the async client's version of a method is awaited."""
    member = getattr(source.async_class, ASYNC_NAMES.get(name, name))
    return inspect.iscoroutinefunction(member)


def read_method(source: ClassSource, name: str, inherited: dict[str, str]) -> Method:
    cls = source.sync_class
    owner = f"{source.name}.{name}"
    if name == CONSTRUCTOR:
        doc = parse_docstring(inspect.getdoc(cls), source.name)
        function: Any = cls.__init__
        async_code, output = read_example(source.slug, "constructor", source.name)
        return Method(
            source.name,
            anchor_of(owner),
            f"{source.name}()",
            False,
            _signature(source.name, function, ""),
            "None",
            doc,
            _parameters(function, doc, source.name),
            to_sync(async_code),
            async_code,
            output,
            source.name,
            True,
            source.slug,
        )
    member = vars(cls)[name]
    function = _function(member)
    doc = parse_docstring(inspect.getdoc(function), owner)
    returns = _return_type(function, source.name)
    async_code, output = read_example(source.slug, name, owner)
    is_property = isinstance(member, property)
    return Method(
        name,
        anchor_of(owner),
        name if is_property else f"{name}()",
        is_property,
        format_code(f"{name}: {returns}") if is_property else _signature(name, function, returns),
        returns,
        doc,
        _parameters(function, doc, owner, inherited),
        to_sync(async_code),
        async_code,
        output,
        source.name,
        is_property or _awaited(source, name),
        source.slug,
    )


def _check_examples_belong(slug: str, stems: set[str]) -> None:
    extra = {path.stem for path in (EXAMPLES / slug).glob("*.py")} - stems
    if extra:
        raise SystemExit(f"These examples in {slug} aren't for anything public: {sorted(extra)}")


def read_methods(source: ClassSource) -> dict[str, Method]:
    """Read every method of a class that the pages list, by name."""
    _check_clients_match(source)
    listed = listed_methods(source)
    _check_methods_listed(source, listed)
    # Options that from_env() shares with the constructor are described once, on the constructor.
    inherited = (
        parse_docstring(inspect.getdoc(source.sync_class), source.name).args
        if CONSTRUCTOR in listed
        else {}
    )
    methods = {name: read_method(source, name, inherited) for name in listed}
    missing = missing_descriptions(methods)
    if missing:
        raise SystemExit(f"These arguments have no description: {', '.join(missing)}")
    _check_examples_belong(
        source.slug, {"constructor" if name == CONSTRUCTOR else name for name in methods}
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
                item.__name__,
                True,
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
                   _signature(key, function, returns), returns, doc, parameters, "", "", "",
                   name, True, "")
        )  # fmt: skip
    return methods


@functools.cache
def symbol_dirs() -> dict[str, str]:
    """Return the folder of each public name's example, stopping the build if two have one."""
    found: dict[str, str] = {}
    for slug in SYMBOL_DIRS:
        for path in sorted((EXAMPLES / slug).glob("*.py")):
            if path.stem in found:
                raise SystemExit(f"{path.stem} has examples in both {found[path.stem]} and {slug}.")
            found[path.stem] = slug
    return found


def _symbol_example(name: str) -> tuple[str, str, str, str]:
    """Return an example's sync code, async code, output, and folder, or nothing if it needn't."""
    slug = symbol_dirs().get(name, "")
    if name in NO_EXAMPLE or name in ALIASES:
        if slug:
            raise SystemExit(f"{name} has an example, so remove it from NO_EXAMPLE.")
        return "", "", "", ""
    if not slug:
        raise SystemExit(
            f"{name} has no example. Add it to one of {', '.join(SYMBOL_DIRS)} in "
            f"{EXAMPLES.relative_to(ROOT)}."
        )
    async_code, output = read_example(slug, name, name)
    return to_sync(async_code), async_code, output, slug


def read_symbol(name: str) -> Symbol:
    item: Any = getattr(pyquickbase, name)
    sync_code, async_code, output, slug = _symbol_example(name)
    if name in ALIASES:
        doc = Docstring(ALIASES[name], [], {}, {}, "", [])
        definition = format_code(f"{name} = {build_llms.clean_types(repr(item))}")
        return Symbol(name, doc, definition, "", "", [], [], [], [], "", "", "", "")
    doc = parse_docstring(inspect.getdoc(item), name)
    if not inspect.isclass(item) and not inspect.isfunction(item):
        # A namespace of functions, such as `allow`, shown by its methods under its own name.
        return Symbol(
            name, doc, "", "", "", [], [], [], _namespace_methods(name, _type_of(item)),
            sync_code, async_code, output, slug,
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
    if len(bases_line) > LINE_LIMIT:
        listed = "".join(f"    {base},\n" for base in bases)
        bases_line = f"class {name}(\n{listed})"
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
        slug,
    )


def read_symbols() -> dict[str, Symbol]:
    """Read every public name that the pages list, other than the classes with methods."""
    names = [member for _, member in _members() if member in NAME_LINKS]
    symbols = {name: read_symbol(name) for name in names if name not in CLASS_BY_NAME}
    for slug in SYMBOL_DIRS:
        _check_examples_belong(slug, {name for name, s in symbols.items() if s.slug == slug})
    return symbols


class Reference(NamedTuple):
    """Everything the pages show, read once: each class's methods, and every other name."""

    methods: dict[str, dict[str, Method]]
    symbols: dict[str, Symbol]


@functools.cache
def read_reference() -> Reference:
    check_every_name_is_covered()
    return Reference(
        {source.name: read_methods(source) for source in CLASSES},
        read_symbols(),
    )


def _public_link(name: str) -> str:
    return f"[`{name}`]({NAME_LINKS[name]})"


def _add_links(text: str, methods: dict[str, str]) -> str:
    """Link what a sentence names. A bare method name may just be a word, so it needs `()`.

    `methods` says where each method of the class the text is about is, so that `count()` in a
    table method's docstring links to `Table.count()`.
    """

    def qualified(found: re.Match[str]) -> str:
        cls = getattr(pyquickbase, found[1])
        if found[2] not in vars(cls):
            raise SystemExit(f"This text names a method that doesn't exist: {found[0]}")
        return f"[`{found[1]}.{found[2]}()`]({METHOD_LINKS[(found[1], found[2])]})"

    def call(found: re.Match[str]) -> str:
        if found[1] in methods:
            return f"[`{found[1]}()`]({methods[found[1]]})"
        return _public_link(found[1]) if found[1] in NAME_LINKS else found[0]

    def see(found: re.Match[str]) -> str:
        return f"See [`{found[1]}()`]({methods[found[1]]})" if found[1] in methods else found[0]

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


def _parameter_lines(parameters: list[Parameter], methods: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for parameter in parameters:
        note = _parameter_note(parameter)
        head = f"- **`{parameter.name}`** `{parameter.annotation}`, {note}"
        text = _add_links(parameter.description, methods)
        # Two spaces make a line break, so the description starts below the name and type.
        lines.append(f"{head}  \n  {text}" if text else head)
    return [*lines, ""]


def _attribute_lines(attributes: list[Parameter], methods: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for attribute in attributes:
        kind = f" `{attribute.annotation}`" if attribute.annotation else ""
        text = _add_links(attribute.description, methods)
        lines.append(f"- **`{attribute.name}`**{kind}  \n  {text}")
    return [*lines, ""]


def _return_lines(returns: str, doc: Docstring, methods: dict[str, str]) -> list[str]:
    if returns in ("None", "") and not doc.returns:
        return []
    lines = [f"**Returns** {_linked_type(returns)}", ""] if returns else ["**Returns**", ""]
    return lines + ([_add_links(doc.returns, methods), ""] if doc.returns else [])


def _raises_lines(doc: Docstring, methods: dict[str, str]) -> list[str]:
    if not doc.raises:
        return []
    lines = ["**Raises**", ""]
    for name, text in doc.raises:
        error = _public_link(name) if name in NAME_LINKS else f"`{name}`"
        lines.append(f"- {error}: {_add_links(text, methods)}")
    return [*lines, ""]


def _prose_lines(doc: Docstring, methods: dict[str, str]) -> list[str]:
    lines = [_add_links(doc.summary, methods), ""]
    for paragraph in doc.prose:
        lines += [_add_links(paragraph, methods), ""]
    return lines


def _shown_parameters(parameters: list[Parameter], methods: dict[str, str]) -> list[str]:
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
    method: Method,
    methods: dict[str, str],
    *,
    two_columns: bool = False,
    returned: list[str] | None = None,
) -> list[str]:
    """Write a method's description, parameters, return value, and errors as Markdown.

    With `two_columns`, the signature is left out, as it goes beside the description with the
    example, and long parameter lists are folded. `returned` goes after the return value.
    """
    doc = method.doc
    lines = _prose_lines(doc, methods)
    if method.name in ASYNC_NAMES:
        lines += [f"The async client calls this `{ASYNC_NAMES[method.name]}()`.", ""]
    elif not method.awaited:
        lines += ["The async client calls this without `await`.", ""]
    stem = "constructor" if method.anchor.endswith("-constructor") else method.name
    if f"{method.slug}/{stem}" in NOT_RUN_LIVE:
        lines += [
            f"The live tests don't run this example. {NOT_RUN_LIVE[f'{method.slug}/{stem}']}",
            "",
        ]
    if not two_columns:
        lines += [f"```python\n{method.signature}\n```", ""]
    if method.parameters:
        listed = (_shown_parameters if two_columns else _parameter_lines)(
            method.parameters, methods
        )
        lines += ["**Parameters**", "", *listed]
    if not method.is_property:
        lines += _return_lines(method.returns, doc, methods) + (returned or [])
    return lines + _raises_lines(doc, methods)


def _describe_symbol(
    symbol: Symbol, *, anchors: bool, example: list[str] | None = None
) -> list[str]:
    """Write a public name's description as Markdown, with any example before its methods."""
    doc = symbol.doc
    none: dict[str, str] = {}
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


def _code_block(async_code: str) -> list[str]:
    """Write code for both clients: in tabs if they differ, or once if they don't."""
    sync_code = to_sync(async_code)
    if sync_code == async_code:
        return [f"```python\n{async_code}\n```", ""]
    return _tabs(sync_code, async_code)


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


def _returned_types(methods: list[Method]) -> dict[str, list[Method]]:
    returned: dict[str, list[Method]] = {}
    for method in methods:
        if method.anchor.endswith("-constructor"):
            continue
        for name in _NAME.findall(method.returns):
            if name in NAME_LINKS:
                returned.setdefault(name, []).append(method)
    return returned


def _return_table(methods: list[Method]) -> list[str]:
    """Write a table of what a page's methods return, with each type's own first line."""
    returned = _returned_types(methods)
    if not returned:
        return []
    rows = ["| Type | What it is | Returned by |", "|---|---|---|"]
    for name, callers in sorted(returned.items()):
        summary = (inspect.getdoc(getattr(pyquickbase, name)) or "").split("\n\n")[0]
        summary = " ".join(summary.split()).replace("|", "\\|")
        linked = ", ".join(
            f"[`{caller.owner}.{caller.heading}`]({METHOD_LINKS[(caller.owner, caller.name)]})"
            for caller in callers
        )
        rows.append(f"| {_public_link(name)} | {summary} | {linked} |")
    return ["## What the methods return", "", *rows, ""]


def _guide_line(group: Group) -> list[str]:
    if group.guide is None:
        return []
    return [f"For the full picture, see [{group.guide[0]}]({group.guide[1]}).", ""]


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


def _uses(code: str) -> set[str]:
    """Return the names from the setup that an example uses, rather than makes itself."""
    tree = ast.parse(code)
    loaded = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    stored = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    return (loaded - stored) & set(SETUP_LINES)


class Setup(NamedTuple):
    """What a page's examples share: the setup code for each client, and a note on it."""

    sync_code: str
    async_code: str
    note: str


def _example_codes(page: Page, reference: Reference) -> tuple[list[str], list[str]]:
    """Return the page's examples that use its setup, and those that make their own client."""
    shared: list[str] = []
    own: list[str] = []
    for member in (member for group in page.groups for member in group.members):
        split = _split(member)
        if split is not None:
            shared.append(reference.methods[split[0]][split[1]].async_code)
        elif member in reference.symbols and reference.symbols[member].async_code:
            symbol = reference.symbols[member]
            (own if symbol.slug in OWN_CLIENT_DIRS else shared).append(symbol.async_code)
    return shared, own


def _listed(names: list[str]) -> str:
    quoted = [f"`{name}`" for name in names]
    if len(quoted) < 3:
        return " and ".join(quoted)
    return f"{', '.join(quoted[:-1])}, and {quoted[-1]}"


def page_setup(page: Page, reference: Reference) -> Setup:
    """Return the setup for a page's examples, making only what they use, and a note on them."""
    shared, own = _example_codes(page, reference)
    used: set[str] = set()
    for code in shared:
        used |= _uses(code)
    for name in list(used):
        while name in SETUP_NEEDS:
            name = SETUP_NEEDS[name]
            used.add(name)
    names = [name for name in SETUP_LINES if name in used]
    parts: list[str] = []
    async_code = ""
    if shared:
        body = [SETUP_LINES[name] for name in names] or ["print(qb.realm)"]
        lines = "\n".join(body).replace("\n", "\n    ")
        async_code = (
            'async with AsyncQuickbase(\n    realm="acme", token="your_user_token"\n) as qb:\n'
            f"    {lines}"
        )
        async_code = f"from pyquickbase import AsyncQuickbase\n\n{async_code}"
        parts.append(f"The examples below use {_listed(names or ['qb'])}, made as shown here.")
    if "tasks" in used or any("bqtasks123" in code for code in own):
        parts.append(_JOB_TASKS)
    if "projects" in used:
        parts.append("`projects` is a table named `Job Projects`, with a `Name` field.")
    if own:
        whose = "An example that makes its own client reads it" if shared else "Each example makes"
        parts.append(
            f"{whose} {'' if shared else 'its own client '}from the environment, with "
            "`bqtasks123` standing for the ID of the `Job Tasks` table."
        )
    if page.note:
        parts.append(page.note)
    parts.append(_LIVE_TEST)
    sync_code = to_sync(async_code) if async_code else ""
    return Setup(sync_code, async_code, " ".join(parts))


def _page_actions(page: Page) -> str:
    return (
        '<p class="ref-actions">'
        f'<a class="ref-markdown" href="../{page.slug}.txt" '
        'title="Open this page as plain Markdown">Markdown</a>'
        '<button class="ref-copy" type="button" '
        'title="Copy this page as Markdown, to give to an LLM" hidden>Copy for LLM</button>'
        "</p>"
    )


def _front_matter(page: Page) -> list[str]:
    about = next(
        about for section in SECTIONS for slug, about in section.pages if slug == page.slug
    )
    description = about.replace("`", "").replace('"', "'")
    return [
        "---",
        f'description: "{description}."',
        "toc_in_nav: true",
        "hide:",
        "  - toc",
        "---",
        "",
    ]


def _two_column_header(page: Page, setup: Setup) -> list[str]:
    """Write the front matter, title, and introduction, with the setup code beside it."""
    none: dict[str, str] = {}
    lines = [*_front_matter(page), _page_actions(page), "", f"# {page.name}", ""]
    intro = [_add_links(page.intro, none), "", _add_links(setup.note, none), ""]
    if not setup.async_code:
        return [*lines, *intro]
    row = _row(intro, _tabs(setup.sync_code, setup.async_code))
    row[0] = '<div class="ref-method ref-intro" markdown>'
    return [*lines, *row]


def _called_names(reference: Reference) -> list[str]:
    """Return everything that's titled by what it does: methods and functions."""
    called = [f"{owner}.{name}" for owner, methods in reference.methods.items() for name in methods]
    for name, symbol in reference.symbols.items():
        if _is_called(name, symbol):
            called.append(name)
        called += [method.heading.removesuffix("()") for method in symbol.methods]
    return called


def check_titles(reference: Reference) -> None:
    """Stop the build if what's called and what's titled differ."""
    called = _called_names(reference)
    untitled = [name for name in called if name not in TITLES]
    unknown = sorted(set(TITLES) - set(called))
    if untitled or unknown:
        raise SystemExit(
            f"TITLES needs a title for {untitled or 'nothing'}, "
            f"and has titles for names that aren't called: {unknown or 'none'}"
        )


def _is_called(name: str, symbol: Symbol) -> bool:
    """Whether a public name is a function, so it's titled by what it does."""
    return bool(symbol.signature) and not name[0].isupper()


def is_namespace(symbol: Symbol) -> bool:
    """Whether a public name is a namespace of functions, such as `allow`."""
    return not inspect.isclass(getattr(pyquickbase, symbol.name)) and bool(symbol.methods)


def method_labels(page: Page, reference: Reference) -> dict[str, str]:
    """Return how each method on a page is named beside its title, such as `upsert()`.

    Where two classes' methods share a name on the page, such as `url()`, each is named with its
    class, as `Table.url()`.
    """
    labels: dict[str, str] = {}
    for member in (member for group in page.groups for member in group.members):
        split = _split(member)
        if split is not None:
            labels[member] = reference.methods[split[0]][split[1]].heading
    taken = list(labels.values())
    return {
        member: f"{member.partition('.')[0]}.{label}" if taken.count(label) > 1 else label
        for member, label in labels.items()
    }


def _heading(level: str, title: str, label: str, anchor: str) -> str:
    """Write a heading: the title in plain words, then the name it's called by, for scanning."""
    return f'{level} {title} <code class="ref-call">{label}</code> {{#{anchor}}}'


# Pages that show, under a method's return value, what the returned object holds, so a reader
# needn't leave the page. A trial on one page, as it makes each method's entry longer.
INLINE_RETURNS = {"reports"}


def _holds(symbol: Symbol) -> str:
    if symbol.attributes and symbol.methods:
        return "holds, and what it can do"
    return "holds" if symbol.attributes else "can do"


def returned_lines(method: Method, reference: Reference) -> list[str]:
    """Write a folded list of what each object a method returns holds, linked to its own entry."""
    lines: list[str] = []
    for name in dict.fromkeys(_NAME.findall(method.returns)):
        symbol = reference.symbols.get(name)
        if symbol is None or is_namespace(symbol) or not (symbol.attributes or symbol.methods):
            continue
        page = NAME_LINKS[name].partition("#")[0]
        lines += [
            '<details class="ref-more ref-returned" markdown>',
            f"<summary>What a <code>{name}</code> {_holds(symbol)}</summary>",
            "",
        ]
        if symbol.attributes:
            lines += _attribute_lines(symbol.attributes, {})
        for item in symbol.methods:
            title = TITLES[item.heading.removesuffix("()")]
            lines.append(f"- [`{item.name}()`]({page}#{item.anchor}): {title}")
        if symbol.methods:
            lines.append("")
        lines += [f"See [`{name}`]({NAME_LINKS[name]}) for more.", "", "</details>", ""]
    return lines


def _two_column_method(
    method: Method, title: str, label: str, returned: list[str] | None = None
) -> list[str]:
    """Write a method as two columns: what it does on the left, and its code on the right."""
    code = [f"```python\n{method.signature}\n```", ""]
    code += _example_lines(method.sync_code, method.async_code, method.output)
    scope = scope_of(method.owner)
    text = [
        _heading("###", title, label, method.anchor),
        "",
        *_describe(method, scope, two_columns=True, returned=returned),
    ]
    return _row(text, code)


def _class_row(source: ClassSource) -> list[str]:
    """Write a class's own entry: what it is, and how to get one."""
    text = [
        f"### `{source.name}` {{#{source.name}}}",
        "",
        _add_links(source.about, scope_of(source.name)),
        "",
    ]
    return _row(text, _code_block(source.get))


def _symbol_text(symbol: Symbol) -> list[str]:
    """Write what a public name is, for the left column: everything but its code."""
    doc = symbol.doc
    none: dict[str, str] = {}
    lines = _prose_lines(doc, none)
    if symbol.bases:
        linked = ", ".join(_public_link(base) for base in symbol.bases)
        lines += [f"**Inherits from** {linked}", ""]
    if symbol.parameters:
        lines += ["**Parameters**", "", *_shown_parameters(symbol.parameters, none)]
    if symbol.attributes:
        lines += ["**Attributes**", "", *_attribute_lines(symbol.attributes, none)]
    return lines + _return_lines(symbol.returns, doc, none) + _raises_lines(doc, none)


def _symbol_rows(symbol: Symbol) -> list[str]:
    """Write a public name as a row, with its signature and example beside it, then its methods."""
    code = [f"```python\n{symbol.signature}\n```", ""] if symbol.signature else []
    if symbol.sync_code:
        code += _example_lines(symbol.sync_code, symbol.async_code, symbol.output)
    elif symbol.definition:
        code += [f"```python\n{symbol.definition}\n```", ""]
    name = symbol.name
    heading = (
        _heading("###", TITLES[name], f"{name}()", name)
        if _is_called(name, symbol)
        else f"### `{name}` {{#{name}}}"
    )
    lines = _row([heading, "", *_symbol_text(symbol)], code)
    namespace = is_namespace(symbol)
    for method in symbol.methods:
        title = TITLES[method.heading.removesuffix("()")]
        label = method.heading if namespace else f"{method.name}()"
        text = [_heading("####", title, label, method.anchor), ""]
        text += _describe(method, {}, two_columns=True)
        lines += _row(text, [f"```python\n{method.signature}\n```", ""])
    return lines


def _error_codes() -> dict[str, tuple[str, bool]]:
    """Return each error's `tool_error()` code, and whether it says to try again."""
    codes: dict[str, tuple[str, bool]] = {}
    for item in vars(pyquickbase.errors).values():
        if not (isinstance(item, type) and issubclass(item, pyquickbase.QuickbaseError)):
            continue
        if issubclass(item, pyquickbase.PolicyRefusedError):
            codes[item.__name__] = ("read_refused` or `write_refused", False)
            continue
        code = next(code for cls, code in _output._CODES if issubclass(item, cls))  # pyright: ignore[reportPrivateUsage]
        codes[item.__name__] = (code, code in _output._RETRYABLE)  # pyright: ignore[reportPrivateUsage]
    return codes


def _error_tree(order: list[str]) -> str:
    """Draw the errors as a tree, under the first base of each, naming its other bases."""
    errors = {name: getattr(pyquickbase.errors, name) for name in order}
    children: dict[str, list[str]] = {}
    for name in order[1:]:
        children.setdefault(errors[name].__bases__[0].__name__, []).append(name)

    def draw(name: str, prefix: str, last: bool, top: bool) -> list[str]:
        others = [base.__name__ for base in errors[name].__bases__[1:]]
        also = f"  (also {', '.join(others)})" if others else ""
        branch = "" if top else ("└─ " if last else "├─ ")
        lines = [f"{prefix}{branch}{name}{also}"]
        below = children.get(name, [])
        inner = prefix if top else prefix + ("   " if last else "│  ")
        for index, child in enumerate(below):
            lines += draw(child, inner, index == len(below) - 1, False)
        return lines

    return "\n".join(draw(order[0], "", True, True))


def error_overview(page: Page) -> list[str]:
    """Write the errors page's overview: every error as a tree, and what `tool_error()` says."""
    order = [member for group in page.groups for member in group.members]
    codes = _error_codes()
    rows = ["| Error | `tool_error()` code | Worth retrying |", "|---|---|---|"]
    for name in order:
        code, retry = codes[name]
        rows.append(f"| [`{name}`](#{name}) | `{code}` | {'Yes' if retry else 'No'} |")
    text = [
        "Catching a class catches everything below it in the tree. For a model, `tool_error()` "
        "turns each error into the code shown here, and also says whether anything changed, which "
        "depends on how far the call got.",
        "",
        *rows,
        "",
    ]
    tree = [f"```text\n{_error_tree(order)}\n```", ""]
    return ["## Every error {#every-error}", "", *_row(text, tree)]


# Written after a page's introduction, before its first section.
OVERVIEWS = {"errors": error_overview}


def build_page(page: Page, reference: Reference) -> str:
    """Return a page for the site, in two columns, with its contents in the left sidebar.

    A method is headed by its title from `TITLES` and its name, an object by its name, and each
    "Returns" links to what's returned, so there's no table of return types.
    """
    setup = page_setup(page, reference)
    labels = method_labels(page, reference)
    lines = _two_column_header(page, setup)
    if page.slug in OVERVIEWS:
        lines += OVERVIEWS[page.slug](page)
    for group in page.groups:
        lines += [f"## {group.title} {{#{group.anchor}}}", "", *_guide_line(group)]
        for member in group.members:
            split = _split(member)
            if split is not None:
                method = reference.methods[split[0]][split[1]]
                returned = (
                    returned_lines(method, reference) if page.slug in INLINE_RETURNS else None
                )
                lines += _two_column_method(method, TITLES[member], labels[member], returned)
            elif member in CLASS_BY_NAME:
                lines += _class_row(CLASS_BY_NAME[member])
            else:
                lines += _symbol_rows(reference.symbols[member])
    return "\n".join(lines)


def build_overview() -> str:
    """Return the reference's overview: a card for each section, then every name and method."""
    lines = [
        "---",
        'description: "Every public class, function, type, and method of pyquickbase, by subject."',
        "---",
        "",
        "# API reference",
        "",
        "Every public class, function, and type, with a tested example for both clients, arranged "
        "by subject. Each page also has a plain Markdown copy for AI tools. Everything here is "
        "public, as [Stability and versioning](../stability.md) describes, and modules starting "
        "with `_` are private. The async classes have the same methods as the sync ones, which "
        "you `await`, so they're described together.",
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
    lines += ["", "</div>", "", "## Every method {#methods}", ""]
    lines += ["By name, each followed by the class it belongs to.", ""]
    lines += ['<div class="ref-names" markdown>', ""]
    methods = sorted(
        ((name, owner) for owner, name in METHOD_LINKS if name != CONSTRUCTOR),
        key=lambda item: (item[0].casefold(), item[1]),
    )
    for name, owner in methods:
        lines.append(f"- [`{name}`]({METHOD_LINKS[(owner, name)]}) {owner}")
    return "\n".join([*lines, "", "</div>", ""])


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


def _plain_intro(page: Page, setup: Setup, site_url: str, *, both: bool) -> list[str]:
    none: dict[str, str] = {}
    others = ", ".join(
        f"[{item.name}]({site_url}reference/{item.slug}.txt)" for item in PAGES if item is not page
    )
    lines: list[str] = []
    if both:
        lines += [
            f"# pyquickbase API reference: {page.name}",
            "",
            f"This describes version {pyquickbase.__version__}. Other pages: {others}.",
            "",
        ]
    lines += [_add_links(page.intro, none), "", _add_links(setup.note, none), ""]
    if setup.async_code and both:
        lines += [
            f"Sync:\n\n```python\n{setup.sync_code}\n```",
            "",
            f"Async:\n\n```python\n{setup.async_code}\n```",
            "",
        ]
    elif setup.async_code:
        lines += [f"```python\n{setup.sync_code}\n```", ""]
    return lines


def _finish_plain(lines: list[str], slug: str, site_url: str) -> str:
    text = build_llms.rewrite_links("\n".join(lines), f"reference/{slug}.md", site_url)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _plain_overview(page: Page) -> list[str]:
    """The errors page's overview as plain Markdown, without the two columns."""
    if page.slug not in OVERVIEWS:
        return []
    lines = OVERVIEWS[page.slug](page)
    kept = [line for line in lines if not line.startswith("<div") and line != "</div>"]
    return [re.sub(r" \{#[\w-]+\}$", "", line) for line in kept]


def build_markdown(
    page: Page, reference: Reference, site_url: str, *, both_clients: bool = True
) -> str:
    """Return a page as one plain Markdown file."""
    setup = page_setup(page, reference)
    lines = _plain_intro(page, setup, site_url, both=both_clients)
    lines += _plain_overview(page)
    on_page = [
        reference.methods[split[0]][split[1]]
        for group in page.groups
        for member in group.members
        if (split := _split(member)) is not None
    ]
    lines += _return_table(on_page)
    for group in page.groups:
        lines += [f"## {group.title}", "", *_guide_line(group)]
        for member in group.members:
            split = _split(member)
            if split is not None:
                method = reference.methods[split[0]][split[1]]
                heading = (
                    method.heading if split[1] == CONSTRUCTOR else f"{split[0]}.{method.heading}"
                )
                lines += [f"### `{heading}`", "", f"{TITLES[member]}.", ""]
                lines += _describe(method, scope_of(method.owner))
                lines += _plain_examples(
                    method.sync_code, method.async_code, method.output, both=both_clients
                )
            elif member in CLASS_BY_NAME:
                source = CLASS_BY_NAME[member]
                lines += [f"### `{member}`", "", _add_links(source.about, scope_of(member)), ""]
                lines += [f"```python\n{to_sync(source.get)}\n```", ""]
            else:
                symbol = reference.symbols[member]
                lines += [f"### `{member}`", "", *_describe_symbol(symbol, anchors=False)]
                if symbol.definition and not symbol.sync_code:
                    lines += [f"```python\n{symbol.definition}\n```", ""]
                lines += _plain_examples(
                    symbol.sync_code, symbol.async_code, symbol.output, both=both_clients
                )
    return _finish_plain(lines, page.slug, site_url)


def full_text(site_url: str) -> list[tuple[str, str, str]]:
    """Return `(title, slug, Markdown)` for each page, with sync examples, for llms-full.txt."""
    reference = read_reference()
    return [
        (
            PAGES_BY_SLUG[slug].name,
            slug,
            build_markdown(PAGES_BY_SLUG[slug], reference, site_url, both_clients=False),
        )
        for section in SECTIONS
        for slug, _ in section.pages
    ]


def main() -> None:
    config = build_llms.read_config()
    reference = read_reference()
    check_titles(reference)
    if OUTPUT.exists():
        for stale in [*OUTPUT.glob("*.md"), *OUTPUT.glob("*.txt")]:
            stale.unlink()
    OUTPUT.mkdir(exist_ok=True)
    for page in PAGES:
        (OUTPUT / f"{page.slug}.md").write_text(build_page(page, reference), encoding="utf-8")
        markdown = build_markdown(page, reference, config.site_url)
        (OUTPUT / f"{page.slug}.txt").write_text(markdown, encoding="utf-8")
    (OUTPUT / "index.md").write_text(build_overview(), encoding="utf-8")
    print(f"Wrote {len(PAGES)} pages and the overview to docs/reference/")


if __name__ == "__main__":
    main()
