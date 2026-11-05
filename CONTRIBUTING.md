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

`tests/test_public_api.py` compares every public name and signature with
`tests/fixtures/public_api.txt`. If a change to the public API is intended, check it against
[the stability policy](docs/stability.md), then update the file with
`QB_UPDATE_PUBLIC_API=1 uv run pytest tests/test_public_api.py`.

`tests/test_properties.py` is randomized, and quick by default. Set `QB_DEEP_TESTS=1` to look much
harder, which takes about 15 minutes.

## Live tests

`tests/_async/live`, `tests/_sync/live`, and `tests/live` use a real Quickbase app, and only run when
asked for. Use a sandbox app. They create tables named `ZZ Live ...`, plus `Tasks` for the docs examples, drop exactly those afterward, and
fail if the record count of any other table changed.

```bash
QB_LIVE=1 QB_REALM=... QB_USER_TOKEN=... QB_APP_ID=... uv run pytest tests/_async/live tests/_sync/live tests/live
```

Tests about reaching across apps also need `QB_SECOND_APP_ID`, another sandbox app the same token can
reach, and skip without it. The same rules apply to that app.

Tests of a restricted user skip without these, which take a one-time setup in the UI:

- `QB_RESTRICTED_TOKEN`: a token for a second user with no admin rights, assigned to `QB_APP_ID`.
- `QB_VIEWABLE_TABLE_ID`: a table in that app that the user's role can view, set up as described below.
- `QB_DENIED_TABLE_ID`: a table in that app the role has no access to, with a text field named `Confidential Note`
  and at least one record that has a value in it.

The viewable table needs:

- A text field named `Category`, with records holding `Alpha`, `Beta`, and `Gamma`.
- A numeric field named `Internal Rank`, with values.
- Two reports: `Records by Internal Rank`, sorted on that field, and `Top Ranked Records`, filtered on it.
- In the role: a View rule of `Category` is not `Gamma`, a Modify rule of `Category` is `Alpha`, Add but
  not Delete, and no view or modify access to `Internal Rank`.

`QB_FIELD_TYPES_TABLE_ID` is a table for field types only the UI can create, and its test skips without
it. It needs a text field named `Name`, then these fields, added in the UI: `Contact Card` (vCard),
`Calendar Entry` (iCalendar), `Depends On` (Predecessor), and `Team Members` (a list-user formula), with
at least one record.

These tests add a temporary field to the viewable table as the main token. The restricted user only
tries writes that should be refused, and the run fails if either table's record count changes. Give
anything you keep in these apps a clear, professional name and description.

The full suite takes about 20 minutes. pytest shows its progress as it goes, so to run it in the
background, add `> live.log 2>&1` and check it with `tail -n 3 live.log`. Piping the output through
`tail` hides the progress until the end.

To test a published release instead of this checkout, install it in a separate environment and set
`QB_LIVE_VERSION` to its version. Running a report adds to its usage count, so these tests only run
reports on the tables they create, the two reports above, and the two below.

Tests of `summary_rows()` skip without two reports, which the API can't create: `API Summary Test` and
`API Chart Test`, on a table named `Sample Tasks` that holds the tasks `Book room` and `Prepare agenda`,
with a numeric `Field Type Sample` of 1. The summary report groups by `Task Name` and shows both the total
and the average of that field. The chart is a bar chart of the same total.

## Docs

Preview the docs site with `uv run --group docs mkdocs serve`. Every public name needs a place on a
page in `scripts/build_reference.py`, and in the table in `docs/reference.md`, and tests check both.

The playground in `docs/playground-app/` needs `uv run scripts/build_playground.py` once before
previewing it, and again after changing the SDK or an example. It downloads pinned copies of Pyodide
and CodeMirror and builds the SDK's wheel, none of which is committed. Its simulator copies what's in
`docs/api-notes.md`, so update both if you confirm that Quickbase behaves differently.

`uv run scripts/build_llms.py` writes `docs/llms.txt` and `docs/llms-full.txt`, which aren't
committed. Run it once before previewing or building the docs, because the home page links to them,
and again to see a change in them. It reads the pages from the `nav` in `mkdocs.yml`, so a new page
needs a description in the script. It deliberately uses no docs plugin or anything from the `docs`
dependency group.

`uv run scripts/build_reference.py` writes the API reference in `docs/reference/`, which also isn't
committed, so run it once before previewing or building the docs. Its examples are in
`docs_src/reference/`, written for the async client, with the sync version derived from them. A new
public name stops the build until it has a place on a page and an example, and it and its arguments
and attributes have descriptions. The live tests run every example with both clients and compare
what it prints to the `.out` file beside it, which `QB_UPDATE_REFERENCE_OUTPUT=1` records.

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

`tests/test_contract.py` holds the decisions we've promised to keep, such as defaults and generated
names. To change what `pyquickbase codegen` writes on purpose, run its golden test with
`QB_UPDATE_GOLDEN=1`, and review the diff.

## Secrets

Never commit real tokens, realm names, or customer data, including in test fixtures. Tokens must
never appear in exceptions, `repr`s, or logs.

## Notes and plans

`docs/api-notes.md` holds what's been confirmed about the Quickbase API. Please update it if you
confirm or disprove something.

`ROADMAP.md` holds what's left to do, including anything untested and any limit we may want to change.
Delete an item once it's done.
