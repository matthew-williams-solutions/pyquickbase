# Roadmap

What's left to do. Delete an item once it's done: `CHANGELOG.md` and git history record what's
finished, and `docs/api-notes.md` records what's been confirmed about Quickbase. Limits we've decided
to keep are explained in the guides, not here.

## Before 1.0

- Let the latest candidate run for at least a week with no code changes.
- In the 1.0 release commit:
  - Change the classifier in `pyproject.toml` from Beta to Production/Stable.
  - Remove the release candidate warnings from `README.md` and `docs/index.md`, and the advice to pin
    the exact version. If the pins go too, update `test_install_pins_match_the_version`.
  - If codegen's output has changed, refresh `tests/fixtures/generated_tables.py` one last time.

## After 1.0

Nothing here should break anyone.

- Switch the docs from Material to Zensical, **by 2027-05-05**, when Material's maintenance ends. A
  trial build of 0.0.74 worked, and its strict mode also catches broken anchors, which MkDocs doesn't.
  Wait for 0.1.0, announced for 2026-11-05. `scripts/build_llms.py` reads the nav from
  `mkdocs.yml`, so update it if the config moves.
- `llms.txt` is at `/pyquickbase/llms.txt`, not the domain root where some tools look, because the
  site is served from a subpath. Only a custom domain would change that.
- Real types in generated classes for `vCardButton` and `ICalendarButton`, as read-only `str`, and
  `predecessor`, as a writable `str`. All three are `Any` until then, and the
  [API notes](docs/api-notes.md#field-types) record what they return.
- Find out how `COUNT` summaries work. `ensure_summary_field()` always sends a child field, and
  Quickbase refused `COUNT` on every field tried, so the SDK may need to leave it out.
- A way to set `fieldHelp` and the other field settings the API takes outside `properties`.
- A per-call timeout for uploads and downloads.
- A small config file to choose generated names.
- `select` on typed tables, which needs a design, because a generated class requires every column.
- A setting that repeats a typed table's schema check after a set time.
- Typed access to file and user values, as a dictionary subclass, which could leave out uploader emails.
- Better errors for a report that uses a field the role can't see. `UnknownReportError` says the table
  has no such report, and running it by raw ID gives `Internal error`, so neither mentions the role.

## Guides

Bring these up to the format of `docs/guides/schema.md`, `reading.md`, and `writing.md`: organized by task,
with a complete setup in the first example, sections that make sense on their own, and a table of what each
refusal raises and leaves behind. Check new claims in a live test before publishing them.

- `docs/guides/files.md`
- `docs/guides/formulas.md`
- `docs/guides/errors.md`
- `docs/guides/clients.md`
- `docs/guides/agents.md`
- `docs/guides/codegen.md`, which so far only gained a section on `query_page()`

Give the methods these guides cover full `Args`, `Returns`, and `Raises` docstrings at the same time.

## API reference

`scripts/build_reference.py` writes `Table` in the two-column layout, with live-tested examples. If that
page is kept:

- Convert the other resources, then replace `docs/reference.md` with an overview that links to them, and add
  each page's Markdown file to `llms.txt`.

## Docs playground

- Simulate updates and deletes, then add "Try it" links from each guide to its own example.
- Simulate queries on `user` fields, which `docs/api-notes.md` now records. The simulator refuses them.

## Demo app

The Quickbase demo app and its Python code page, which is kept outside this repo. Do these before anyone
else gets access, because the page writes with the viewer's own permissions:

- Pin the SDK version the page loads, and verify the downloaded files where possible. It currently
  loads whichever wheel the docs site lists.
- Block the Python frame from navigating away with data in the address, or disclose it plainly. The page
  closes the frame after it navigates, which is too late to stop the data leaving.
- Decide who gets access, and whether their writes should be limited. Anyone who can add and delete
  tasks can also run Reset tasks, which replaces the Tasks records for everyone.
- Find out which roles can add a code page. The help only says "app builders".
- Keep Overdue Tasks from drifting. The sample due dates are fixed, so the number climbs over time.

## Open questions

Nothing is waiting on these. Test one against a sandbox app before depending on the answer, then move
the result to `docs/api-notes.md`.

**Queries and limits**

- Which endpoints have different rate limits?
- Do other realms have a different request size limit? Seven endpoints share the one in the
  [API notes](docs/api-notes.md#file-attachments).

**Realms and apps**

- Is `api.quickbase.eu` the API host for EU realms? It answers without a token just as `api.quickbase.com`
  does, and the help only says EU realms use `.quickbase.eu`. Confirming it needs a real EU realm.
