# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A guide to
  [using pyquickbase from an AI agent](https://matthew-williams-solutions.github.io/pyquickbase/guides/agents/).
- [`llms.txt`](https://matthew-williams-solutions.github.io/pyquickbase/llms.txt) and
  [`llms-full.txt`](https://matthew-williams-solutions.github.io/pyquickbase/llms-full.txt) on the
  docs site, which hold a list of the docs and all of them in one file.

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
