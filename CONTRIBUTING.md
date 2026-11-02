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

## Secrets

Never commit real tokens, realm names, or customer data, including in test fixtures. Tokens must
never appear in exceptions, `repr`s, or logs.

`docs/api-notes.md` tracks which Quickbase API behavior has been confirmed. Please update it if you
confirm or disprove something.
