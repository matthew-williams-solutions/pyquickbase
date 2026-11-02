# Contributing

## Setup

Install [uv](https://docs.astral.sh/uv/), then run `uv sync`. Before opening a pull request, run:

```bash
uv run ruff check .
uv run ruff format .
uv run pyright
uv run scripts/generate_sync.py
uv run pytest --cov
```

Tests use mocked HTTP and need no Quickbase account.

## Docs

Preview the docs site with `uv run --group docs mkdocs serve`. Every public name needs an entry in
`docs/reference.md`, and a test checks this.

The playground in `docs/playground-app/` needs `uv run scripts/build_playground.py` once before
previewing it, and again after changing the SDK or an example. It downloads pinned copies of Pyodide
and CodeMirror and builds the SDK's wheel, none of which is committed. Its simulator copies what's in
`docs/api-notes.md`, so update both if you confirm that Quickbase behaves differently.

## Sync and async code

**Only edit the async code.** `src/pyquickbase/_sync/` and `tests/_sync/` are generated from their
`_async` counterparts, and CI fails if they are out of date. After changing async code, run
`uv run scripts/generate_sync.py` and commit the result.

Code that does no network access, such as parsing and validation, belongs outside `_async` so both
clients share it.

## Public API

Anything exported from `pyquickbase/__init__.py` or defined in `errors.py` is public. Modules
starting with `_` are private and may change at any time. Update `CHANGELOG.md` for changes that
users would notice.

`docs/stability.md` says what 1.x promises, so check it before changing anything public. From 1.0, a
breaking change needs a major version, and removing something needs a `DeprecationWarning` in a minor
release first. Generated code must keep working on later 1.x versions, so treat what it relies on as
public, even when the name is private. `tests/fixtures/generated_tables.py` is an old generated file
that the tests run against the current code. From 1.0, don't regenerate it to make a test pass, because
a failure there means you've broken users' files.

## Secrets

Never commit real tokens, realm names, or customer data, including in test fixtures. Tokens must
never appear in exceptions, `repr`s, or logs.

## Notes and plans

`docs/api-notes.md` holds what's been confirmed about the Quickbase API. Please update it if you
confirm or disprove something.

`ROADMAP.md` holds what's left to do, including anything untested and any limit we may want to change.
Delete an item once it's done.
