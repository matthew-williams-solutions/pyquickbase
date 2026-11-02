# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
