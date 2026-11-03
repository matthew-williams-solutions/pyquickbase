# Roadmap

What's left to do. Delete an item once it's done: `CHANGELOG.md` and git history record what's
finished, and `docs/api-notes.md` records what's been confirmed about Quickbase. Limits we've decided
to keep are explained in the guides, not here.

## Before 1.0

- Yank `1.0.0rc1`, `0.2.0a1`, and `0.1.0a1` on PyPI, which let a value in `F` act as query syntax.
- Decide whether a `Retry-After` of `0` should wait a moment. Quickbase sends it while still limiting, so
  those retries are used up immediately. In a long burst, a call can run out of retries.
- Finish testing the release candidate:
  - Run CI once on Windows and macOS, and strict type-check a sample app from a clean install.
  - Add a snapshot test of every public name and signature.
  - Run the docs examples against a sandbox app, and have someone new follow the docs cold.
  - Use it for something real, then let the latest candidate run for at least a week with no code
    changes.
- In the 1.0 release commit:
  - Change the classifier in `pyproject.toml` from Beta to Production/Stable.
  - Remove the release candidate warnings from `README.md` and `docs/index.md`, and the advice to pin
    the exact version. If the pins go too, update `test_install_pins_match_the_version`.
  - If codegen's output has changed, refresh `tests/fixtures/generated_tables.py` one last time.

## After 1.0

Nothing here should break anyone.

- Switch the docs from Material to Zensical, **by 2027-05-05**, when Material's maintenance ends. A
  trial build of 0.0.74 worked, and its strict mode also catches broken anchors, which MkDocs doesn't.
  Wait for 0.1.0, announced for 2026-11-05.
- A method for summary and chart reports, returning `{label: value}` rows.
- A per-call timeout for uploads and downloads.
- A small config file to choose generated names.
- `query_page()`, `select`, and `batch_size` on typed tables. `select` needs a design, because a
  generated class requires every column.
- A setting that repeats a typed table's schema check after a set time.
- Typed access to file and user values, as a dictionary subclass, which could leave out uploader emails.
- Export `FieldRef`, `OrderBy`, and `Expression`, which appear in public signatures.
- Tie a `Row` to its class when the code runs. Only a type checker refuses a row from another class.

## Docs playground

- Simulate updates and deletes, then add "Try it" links from each guide to its own example.

## Open questions

Nothing is waiting on these. Test one against a sandbox app before depending on the answer, then move
the result to `docs/api-notes.md`.

**Queries and limits**

- Which endpoints have different rate limits?
- Do other endpoints or realms have a different request size limit?

**Reports**

- Can a deleted report's ID be reused? A new report got ID 8, the highest so far, but the deleted
  report's own ID wasn't recorded. If it can, cached report details could misread a total as a Record
  ID#.
- Do a report's pages stay stable with ties above 1,200 records? A report's sort can't add Record ID#.

**Queries, continued**

- What does `_curuser_` match on a user field? On a text field it's literal, but a value from someone
  else could match the token owner's records on a user field. Other relative values on date fields, such
  as `today`, may behave the same way.

**Fields and files**

- What do lookups return for types other than text, numeric, and date, and what about field types only
  the UI can create? These are `Any` and read-only in generated classes until confirmed.
- How do file fields behave with other version settings, and are there limits on total storage?

**Realms and apps**

- What's the API host for EU realms?
- Can a formula read a table in another app?
- How do roles with write access or record-level rules behave, and a report that sorts or filters on a
  hidden field? Only a view-only role with hidden fields has been tested.
- Can a restricted user's formula read a table their role has no access to? A role's hidden fields held
  up, but this wasn't tested, because the role's other tables were the seeded ones.
- Can renaming a table, or using the UI, give two tables the same name? If so, `app.table()` takes the
  first match, unlike fields and reports, which raise an error.
