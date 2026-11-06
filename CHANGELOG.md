# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0rc10] - 2026-11-05

This candidate lets the report operations take a report the way models send one, found by testing
an MCP server built on the SDK in Claude Desktop. Every call that worked before still works, and
only two refusal messages read differently.

### Fixed

- **The `run_report` and `summary_report` operations refused a report sent any way but its ID as
  text**, so a model that sent `8` rather than `"8"`, or the report's name, was told it couldn't run
  a report its tool listed. `run()` now also takes the ID as a number, or the report's exact name,
  ignoring case, of a report the tool lists. A name shared by two of them is refused, asking for
  the ID. The schema still lists the IDs, and its description says a name works too.
- **A report of the other kind gets a refusal that says what to do.** A table report sent to
  `summary_report`, or a chart sent to `run_report`, says which operation runs it, where the message
  only said it couldn't be run.

## [1.0.0rc9] - 2026-11-05

This candidate fixes filters on percent and duration fields, which matched the wrong records with no
error, and helps a model's tools say why a delete or write would be refused before anyone approves
it. Each was found by testing an MCP server built on the SDK, and checked against the live API.

**Upgrading.** A percent or duration filter that worked around the bug by sending Quickbase's own
units as a number, such as `F("Done") >= 50` for 50%, now means 5,000%. Send `0.5`, or the text
`"50"`, which is still sent as written.

### Fixed

- **Filters on `percent` and `duration` fields compared the wrong values, and matched the wrong
  records with no error.** A query compares a percent in whole percent, so `F("Done") >= 0.5` matched
  0.5% and up. A bare number on a duration matches nonsense, so `F("Budget") > 28800000` matched
  nothing. `F` and `where_from_dict()` now send a percent as whole percent and a duration as
  `N milliseconds`, which compares exactly, so a number means what it means when writing: a fraction,
  and milliseconds. Text is still sent as written. The same applies to a `scope` and to `delete()`.

### Added

- `F` takes a `timedelta` on a `duration` field.
- A `preview_delete` operation's result says, in `tool_result()`'s `over_limit`, when more records
  match than its `max_records`, so a model knows the delete would be refused before asking anyone
  to approve it.
- A write that sets a field the tool may read but not write now names the field. A field it can't
  read still isn't named.

### Changed

- The refusal for a read past `ReadAccess(max_records=...)` says that it limits how far paging can go,
  not only the size of a page, and so do the docs for `ReadAccess` and `table.operation()`.

## [1.0.0rc8] - 2026-11-05

This candidate holds the fixes from a review before 1.0. Most important, it closes gaps in what a model's
tools could do: an operation's `scope` limited what a write filtered but not what it wrote, and the
`delete` operation had no limit. It also reads user and file values as dataclasses, adds `select` to
typed tables, retries reads after more kinds of failure, and fills in what the API was missing, such as
`COUNT` summaries, duration writes, and field settings. Each was checked against the live API.

**Upgrading.** These changes can break working code:

- **User and file values are dataclasses, not dictionaries.** Read `owner.email` rather than
  `owner["email"]`, and `attachment.latest.file_name` rather than
  `attachment["versions"][-1]["fileName"]`. An empty file field is None, where it was
  `{"url": "", "versions": []}`.
- **A `duration` field takes whole milliseconds or a `timedelta`.** A fraction, such as `1.5`, or text,
  such as `"1:30:00"`, raises `InvalidValueError`.
- **An operation's write can't set a field its scope uses**, and `insert` needs a scope of `==`
  conditions joined with `&`.
- **httpx 0.28 or later is required.**

### Security

- An operation's `scope` now holds for what a write sets, not only for what it filters. Before, a model
  with an `insert` tool scoped to one project could create records in another, and an `update` tool could
  move a record out of its scope. `insert`, `update`, and `preview_update` now refuse a row that sets a
  field the scope uses, and `insert` fills those fields in from the scope. It refuses to run when the
  scope isn't only `==` conditions joined with `&`, as a new record couldn't be made to match it.
- The `delete` operation counts what its filter matches first, and refuses the call, deleting nothing,
  when that's more than `max_records`, 100 by default. Before, one call could delete every record in its
  scope. The tool's schema tells the model the limit.

### Added

Typed tables and values:

- `User`, `FileAttachment`, and `FileVersion`, the frozen dataclasses that user, `multiuser`, and file
  fields read as, in records and generated classes. A `User`'s `repr` leaves out the email address, and a
  `User` can be written to a user field or compared in `F`.
- `select` on a typed table's `query()`, `query_page()`, `first()`, and `get()`, which reads only the
  fields of the class you list. The other attributes are left unset, so reading one raises
  `AttributeError` saying it wasn't selected. `Select` is its type.
- Generated classes type `vCardButton` and `ICalendarButton` fields as read-only `str`, and `predecessor`
  fields as a writable `str`, rather than `Any`. Run `pyquickbase codegen` again to get them.

Writing:

- `WriteResult.row_ids`, which gives each row of an `insert()` its new Record ID#, in the order of your
  rows, with None for a row that failed. `created_ids` leaves failed rows out, so it only lines up with
  your rows when none failed.
- Operations can write `duration` fields, as whole milliseconds, and `predecessor` fields, as text.
  Table writes and generated classes also take a `timedelta` for a duration.
- `timeout` on `insert()`, `update()`, `upsert()`, and `download_file()`, and on a typed table's writes,
  which replaces the client's timeout for that call, such as for a large file. It applies with your own
  `http_client` too.

Schema:

- `ensure_count_field()`, which counts a child table's records on the parent, and counts 0 for a parent
  with none. `COUNT` takes no field, so `ensure_summary_field()` now raises `InvalidValueError` for it,
  where Quickbase refused every field it was sent.
- `help_text`, `bold`, `no_wrap`, `find_enabled`, and `appears_by_default` on `create_field()`,
  `update_field()`, and `ensure_field()`, and `add_to_forms` on `create_field()`, for the settings
  Quickbase takes beside `properties`. `Field.help_text` reads a field's help, and `ReadRules` keep it.

### Changed

- **Breaking:** user and file values are no longer dictionaries, as described under Upgrading.
  `tool_result()` writes a user as `{"id", "name", "email"}`, and a file version as
  `{"number", "file_name", "uploaded", "creator"}`.
- **Breaking:** a `duration` field refuses fractions and text, because Quickbase drops a fraction of a
  millisecond without saying so, and guesses at text.
- **Breaking:** pyquickbase needs httpx 0.28 or later, whose request bodies it measures to keep each
  batch under Quickbase's 40 MiB limit. httpx 0.27 sent larger bodies than were measured.
- Reads, meaning GET requests, record queries, report runs, and formula runs, are retried after a
  timeout, a dropped connection, or a 502, 503, or 504, as sending one again changes nothing. A write
  still isn't. Any request is also retried after httpx's `PoolTimeout`, as nothing was sent.
- The `preview_update` operation offers only fields the client may both read and change, so it no longer
  offers a write-only field that every call would be refused for.

### Fixed

- `summarize()` on a client with `ReadRules` raises `ReadRefusedError` if more records match while it
  reads than `max_summary_records`, rather than returning a summary of only some of them.
- `tool_error()` reports `changed: "some"` when a failed call still changed something: when
  `delete_fields()` deleted some of the fields, and when `create_field()` created the field but couldn't
  set `required` or `unique`. That error's message now says the field was created.
- `insert()`, `update()`, and `upsert()` raise `InvalidValueError` for a NaN or infinite number, rather
  than a `ValueError` from httpx.
- `Decimal` values, in writes and queries, and `float` values in queries, are written without an
  exponent, so `Decimal("1E+3")` is sent as `1000`. A NaN, infinite, or enormous number in a query raises
  `QueryError`.
- `preview_update()` reads in batches no larger than the client's `ReadRules` `max_records`, so a preview
  of more records than that is no longer refused.
- `UnknownReportError` says the report may be hidden from the token's role, as Quickbase hides one whose
  filter or sort uses a field the role can't see. Running such a report by ID through `qb.request()`
  gives `Internal error`, and that error now says why.

### Documentation

- The guides to errors, files, and formulas are rewritten by task, and the codegen and clients guides
  gain complete first examples. Each ends with a table of what each refusal raises and leaves behind.
- The playground simulates updates, deletes, and queries on user fields, with two new examples, and each
  guide section links to the example that shows it.
- The API notes record how Quickbase writes durations, counts child records, and takes field settings.

## [1.0.0rc7] - 2026-11-05

This candidate adds limits on what a client can read, output for a model's tool call, and table
operations offered to a model with their JSON Schema. It also makes the API reference a set of pages. It
has breaking changes: the write limits are renamed and `allowed_writes` is removed, so check the list
under Changed before upgrading.

### Added

- `read_policy` on `Quickbase` and `AsyncQuickbase`, which decides on each read before it's sent, and
  works with any of the limits on writes. `ReadRules` and `ReadAccess` list what a client may read from
  each table: which fields it may return, filter on, and sort by, which reports and file fields it may
  use, and how many records one query can reach. A table or field they don't list acts as if the token
  couldn't see it. They also leave email addresses out of user and file values unless asked, and keep
  only the metadata the SDK reads, so a field's formula and a report's filter aren't in `raw`. They
  refuse formulas, app details and roles, reports run through `request()`, and any change to tables or
  fields. A policy can also be your own function, which takes a `ReadRequest`.
- `ReadRefusedError`, raised for a read the policy refuses, and `RequestRefusedError`, the base of it and
  of `WriteRefusedError`.
- `tool_result()` and `tool_error()`, which turn results and errors into plain data for the result of a
  model's tool call, for any tool-calling framework. Results use the same shapes and value formats every
  time: dates as `YYYY-MM-DD`, timestamps in UTC, every empty value as null, and paging as `returned`,
  `total`, `skip`, `next_skip`, and `has_more`. `compact=True` sends each label once, and `max_records`
  and `max_text` cut a result while saying so. Errors carry a fixed `code` from `ToolErrorCode`, a
  `hint` for the model, whether a retry can help, and whether anything was `changed`, which tells a
  partial write from a complete failure. An exception from outside the SDK says nothing about itself.
- `table.operation()`, which turns one table operation into a tool for a model: an `Operation` with the
  JSON Schema of the arguments a model may send, and `run()`, which checks them against the same lists
  before calling the table. It covers queries, counts, summaries, inserts, updates, deletes, their
  previews, table and summary reports, formulas, and file downloads and deletes. The schema offers only
  what the client's `ReadRules` and `WriteRules` allow, and an operation the client can't run is refused
  when it's made. `run()` turns JSON values into dates, times, numbers, and file uploads by field type,
  and `scope` adds a condition your code fixes to every call. A formula's text is always fixed by your
  code. `descriptions` tells the model what fields mean.
- `tool_result()` takes table, field, and report metadata, without `raw`, and a downloaded file, with its
  text when it's UTF-8 text.
- `where_schema()` takes `descriptions`, and `where_from_dict()` takes `Field` objects in `fields`, so the
  same list works for both.

### Changed

- **Breaking:** renamed `TableRules` to `WriteRules`, `Allow` to `WriteAccess`, and `ReadOnlyError` to
  `WriteRefusedError`, so that the names for limiting writes match those for limiting reads. A refused
  write still raises the same error in the same places.
- **Breaking:** removed `allowed_writes`, which only did what a `write_policy` can. A policy that checks
  `request.method` and `request.path` does the same, as
  [Combining limits](https://matthew-williams-solutions.github.io/pyquickbase/guides/clients/#combining-limits) shows.
- `all_of()` combines read policies as well as write policies, but not the two kinds at once.
- A field or table that can't be found now says "this client can't see it" rather than "this token",
  as a read policy can hide it too.

- The message of `AuthenticationError` now says that a 401 may mean a wrong ID rather than a bad token.
  Quickbase uses the same wording for a bad token, an app or table that doesn't exist, and one the token
  can't reach, so the error's type stays the same.

### Documentation

- The API reference is now a set of pages, one for each class or topic, with a description of every
  argument and attribute and an example for both clients, which live tests run. Each page has a plain
  Markdown copy, listed in `llms.txt`.
- Links to anchors in the old reference page, such as `reference/#pyquickbase.Table`, no longer work.
- The reference page "Write limits and retries" is now "Limits and retries", and covers read limits.

## [1.0.0rc6] - 2026-11-05

This candidate adds what an AI agent or MCP server needs to use Quickbase safely: filters from plain
data, limits on what a client can write, previews of writes, and summaries. It has one breaking change,
to `insert()`, so check the list under Changed before upgrading.

### Added

- `where_from_dict()`, which builds a query condition from plain data, such as the JSON from an AI
  model's tool call or a web form. It refuses anything it doesn't recognize, and checks each value
  against its field's type, so `today` or `garbage` on a date field raises `QueryError`. Its `fields`
  argument limits which fields a filter may use.
- `where_schema()`, which describes that format as JSON Schema with no `$ref`, for a tool's input in
  MCP or any other LLM tool system.
- `write_policy` on `Quickbase` and `AsyncQuickbase`, which decides on each write before it's sent.
  `TableRules` and `Allow` list what a client may do to each table, by action and by field, and refuse
  everything else. A policy can also be your own function, which takes a `WriteRequest`, and `all_of()`
  requires several to pass. Only a return value of `True` lets a write through.
- `preview_delete()` and `preview_update()` on tables and typed tables, which show what a delete or
  update would do without changing anything, so that a person or model can check it first.
  `preview_update()` returns the new `UpdatePreview`, with each change's before and after values.
- `summarize()` on tables and typed tables, which counts records and finds totals, averages, minimums,
  and maximums, overall or per group, with `Period` to group dates by month and so on. It reads every
  matching record, and `max_records` refuses a scan larger than you expect, rather than returning a
  partial result.
- `Report.summary_rows()`, which reads a summary or chart report built in the UI as
  `{column label: value}` rows. These reports used to need `qb.request()`.

### Changed

- **Breaking:** `insert()` now raises `InvalidValueError`, and sends nothing, on a table whose key field
  isn't `Record ID#`. Before, a row with an existing value in that field silently changed that record
  rather than creating one, and `created_ids` could end up shorter than the rows. Use `upsert()` on those
  tables. Changing the key field in the UI can take as long as `schema_ttl` to be noticed.

## [1.0.0rc5] - 2026-11-04

This candidate closes gaps that would be hard to change after 1.0, and has four more breaking
changes, so check the list under Changed before upgrading.

### Added

- `Expression`, `FieldRef`, `FieldRefs`, and `OrderBy` are now exported, as they appear in public
  signatures.
- `batch_size` on `insert()`, `update()`, and `upsert()` of a typed table.
- `TypedTable.query_page()`, which returns a page of generated class instances as a new `TypedPage`,
  with `total`, `skip`, `has_more`, and `next_skip`. It runs the same schema check as other typed
  methods.

### Fixed

- A `datetime` with a fraction of a second can now be written, including `datetime.now(UTC)`. Before,
  Quickbase refused it as a row error, because Python's default of six fractional digits isn't
  accepted.
- `order_by` and `select` on an untyped table now accept a field from a generated class or `F`, such
  as `tasks.table.query_page(order_by=Tasks.fields.title)`. Before, it raised a bare `TypeError`. Any
  other unusable value now raises `QueryError`.
- `ensure_table()` now returns a table you can use when the name differs only in case from an
  existing one. Before, it found the table, then raised `UnknownTableError` on first use.

### Changed

- **Breaking:** `==` and `!=` on a `user` field now work, by email or user ID. Before, `==` silently
  matched nothing, and `!=` silently matched every record, including that user's, so a `delete()` with
  it could remove more than intended. Check any code that compares a `user` field.
- **Breaking:** `F` now raises `QueryError` for `_curuser_` on a `user` or `multiuser` field, because
  Quickbase reads it as the token's owner, so a value from someone else could match that person's
  records. A query string can still use it on purpose.
- **Breaking:** a typed table now raises `InvalidValueError` for a row made by a class for another
  table, whose field IDs would mean different fields. Before, only a type checker refused it.
- **Breaking:** `order_by` on a typed table now takes only what its signature says, a field of the
  generated class or its ID, alone or in a list. Anything else raises `QueryError`. Before, `["Title"]`
  and `[F("Title")]` happened to work, `"Title"` was read as one field name per letter, and
  `F("Title")` raised a bare `TypeError`. Use `Tasks.fields.title`.
- Fuller docstrings for the schema, query, and write methods, longer schema, reading, and writing
  guides, and a new field types page.
- `SchemaMismatchError`'s description now covers the `ensure_` methods, which also raise it.

## [1.0.0rc4] - 2026-11-04

These changes come from building a customer portal on the SDK.

### Changed

- **Breaking:** generated classes now read `currency` fields as `Decimal`, and foreign keys and
  `numeric` fields set to 0 decimal places as `int`. A fractional value in an `int` field raises
  `InvalidResponseError` rather than being rounded, because Quickbase only uses that setting for
  display. Generate your classes again, and update code that expects `float` for these fields.
- **Breaking:** `pyquickbase codegen` now leaves out the links Quickbase makes for each relationship,
  such as `Add Task` and `Task records`. Pass `--include-links` to keep them.
- **Breaking:** a write refused by the token's role now always raises `WritePermissionError`, which
  is both a `WriteError` and a `PermissionDeniedError`. Before, the same refusal raised one or the
  other depending on the role's other permissions.

### Added

- `RowError.kind` and `RowError.field_id`, so you can recognize a duplicate value, a refused row, or a
  row refused by a custom data rule, without reading Quickbase's wording.
- `App.ensure_table()`, `Table.ensure_field()`, `Table.ensure_relationship()`,
  `Table.ensure_lookup_field()`, and `Table.ensure_summary_field()`, for setup scripts that are safe
  to run again. The last two also add lookups to an existing relationship and create summary fields,
  which needed `request()` before.
- `first()` and `get()` on `Table` and `TypedTable`.
- `App.roles()`, and the new `Role` type.
- `Field` attributes for what a lookup or summary points at, plus `decimal_places` and
  `is_foreign_key`. `Relationship.summary_field_ids`.
- `from_env(token_env=...)` and `pyquickbase codegen --token-env`, for a program with several tokens.

## [1.0.0rc3] - 2026-11-02

### Added

- A guide to
  [using pyquickbase from an AI agent](https://matthew-williams-solutions.github.io/pyquickbase/guides/agents/).
- [`llms.txt`](https://matthew-williams-solutions.github.io/pyquickbase/llms.txt) and
  [`llms-full.txt`](https://matthew-williams-solutions.github.io/pyquickbase/llms-full.txt) on the
  docs site, which hold a list of the docs and all of them in one file.
- The playground's sample app now has three linked tables, with users, tags, attachments, a lookup, a
  formula, and rollups, and four more examples.

### Fixed

- `pyquickbase codegen` now writes the same line endings on every system, so generating on Windows
  doesn't change every line of a file generated elsewhere.
- A `Retry-After` of 0 now waits using `RetryPolicy`'s backoff instead of retrying immediately.
  Quickbase can send it while still limiting, so immediate retries could be used up during a burst.
- If a later batch of a write got a successful response in the wrong shape, `InvalidResponseError` lost
  the results of the earlier batches. It's now a `WriteError`, like any other failure in a later batch.

## [1.0.0rc2] - 2026-11-02

### Security

- `F` no longer lets a value act as query syntax, which could match, or delete, records it shouldn't.
  `_FID_6` compares with field 6, so `F("Title") == "_FID_6"` matched every record, and it now raises
  `QueryError`. `a OR b` matched `a` and `b` rather than that text, and now matches that text.

### Fixed

- `insert()` and `upsert()` now raise `InvalidValueError` for a row with no values, because Quickbase
  can silently skip it, which also shifts `created_ids` against your rows.
- `pyquickbase codegen` now always reads and writes UTF-8, so labels like `日本語` no longer fail or
  produce a broken file on Windows.
- `table.relationships()` documents that it lists only relationships where the table is the child.
- A corrupt compressed response, or a redirect loop with your own `http_client`, now raises
  `NetworkError` instead of an `httpx` exception.
- Reading pages now raises `InvalidResponseError` if a response ignores `skip`, instead of returning
  the same records forever.

### Added

- A [playground](https://matthew-williams-solutions.github.io/pyquickbase/playground/) on the docs site,
  which runs the SDK in the browser against simulated data, with nothing to install.

## [1.0.0rc1] - 2026-11-02

### Added

- A stability and versioning policy, which applies from 1.0.

### Changed

- `TypedTable.check_schema()` now always fetches the table's fields again, instead of doing nothing
  after the first check. Other methods still check once, on first use, against cached metadata.
- A generated class no longer fails when a field becomes writable. Otherwise, supporting a new field
  type in a later version would break classes generated earlier.
- Generated names no longer depend on the Python version running `pyquickbase codegen`, so a label
  can't get a different name on a newer Python.
- The error from `TypedRecord.from_record()` now mentions that a report may have left out a field.
- `SchemaMismatchError` and `UnknownFieldError` now say a field may be hidden from the token, because
  Quickbase leaves out fields a role can't see, as if they didn't exist.

### Removed

- `from_api()` on `AppInfo`, `TableInfo`, `Field`, `Relationship`, `ReportInfo`, `FormulaResult`, and
  `Page`, `FileDownload.from_response()`, `FileUpload.to_api()`, and `WriteResult.merged()` and
  `summary()`. These were internal helpers, and they're now private so they can change freely.

## [0.2.0a1] - 2026-11-02

### Added

- A documentation site.
- Reports: `table.reports()`, `table.report()`, and `Report` with `run()`, `run_page()`, and
  `info()`, plus `ReportInfo`, `UnknownReportError`, and `UnsupportedReportError`. Only table reports
  can be read as records.
- File attachments: `FileUpload` for writes, plus `table.download_file()` and
  `table.delete_file_version()`.
- `pyquickbase codegen`, which generates a typed class for each table, and `app.typed()`, which reads
  and writes through those classes, including `query()`, `insert()`, `update()`, and `upsert()`. A
  class that no longer matches its table raises `SchemaMismatchError`.
- Formulas: `table.run_formula()`, which returns a `FormulaResult`, `formula=` on `create_field()` and
  `update_field()`, and `Field.formula`.

### Changed

- `read_only` and `allowed_writes` clients now allow running formulas, with `POST /formula/run`.
- A blank formula now raises `InvalidValueError` on `create_field()` and `update_field()`, including
  through `properties`, because Quickbase accepts it on a formula field and empties the field's values.
- A file attachment field now accepts only a `FileUpload`, and raises `InvalidValueError` for anything
  else, including `None` and hand-built dictionaries, because Quickbase silently accepts those and
  keeps or loses the file.
- Writes are also split by size, to stay under Quickbase's 40 MiB request limit, so `batch_size` is
  now an upper limit.
- `read_only` and `allowed_writes` clients now allow running reports, with
  `POST /reports/{id}/run`. This adds to the report's usage count in Quickbase, but changes no
  records.
- `Record.id` now explains what's missing when a report's records have no Record ID#. It still
  raises `KeyError`.

### Fixed

- A rate limit error now includes Quickbase's message, which arrives in a different format.
- A README example that was missing an import.

## [0.1.0a1] - 2026-11-02

First alpha release.

### Added

- `Quickbase` and `AsyncQuickbase` clients with `request()` for calling any endpoint.
- `RetryPolicy`, with retries for HTTP 429 and connection failures.
- The `QuickbaseError` exception hierarchy.
- `qb.app()` and `app.table()`, with app, table, field, and relationship metadata and a schema cache.
- `F` for building queries by field label, with values escaped for you.
- Checks that reject broken `where` strings that Quickbase would otherwise accept.
- `table.query()` for automatic paging, plus `query_page()` and `count()`.
- `Record`, with access by label or ID and date and timestamp conversion.
- `read_only=True` on the clients, which refuses any request that could change data.
- `allowed_writes` on the clients, which limits them to reads plus the writes you list.
- `parse_where()` for inspecting a hand-written `where` string.
- `table.insert()`, `update()`, `upsert()`, and `delete()`, with per-row error reporting in `WriteError`.
- `app.create_table()`, `table.create_field()`, `update_field()`, `delete_fields()`,
  `create_relationship()`, and `drop()`.
