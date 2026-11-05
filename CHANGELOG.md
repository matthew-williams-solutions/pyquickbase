# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The message of `AuthenticationError` now says that a 401 may mean a wrong ID rather than a bad token.
  Quickbase uses the same wording for a bad token, an app or table that doesn't exist, and one the token
  can't reach, so the error's type stays the same.

### Documentation

- The API reference is now a set of pages, one for each class or topic, with a description of every
  argument and attribute and an example for both clients, which live tests run. Each page has a plain
  Markdown copy, listed in `llms.txt`.
- Links to anchors in the old reference page, such as `reference/#pyquickbase.Table`, no longer work.

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
