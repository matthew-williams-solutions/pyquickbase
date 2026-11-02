# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A documentation site.
- Reports: `table.reports()`, `table.report()`, and `Report` with `run()`, `run_page()`, and
  `info()`, plus `ReportInfo`, `UnknownReportError`, and `UnsupportedReportError`. Only table reports
  can be read as records.
- File attachments: `FileUpload` for writes, plus `table.download_file()` and
  `table.delete_file_version()`.
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
