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
- A method for summary and chart reports, returning `{label: value}` rows.
- Find out how `COUNT` summaries work. `ensure_summary_field()` always sends a child field, and
  Quickbase refused `COUNT` on every field tried, so the SDK may need to leave it out.
- A way to set `fieldHelp` and the other field settings the API takes outside `properties`.
- A per-call timeout for uploads and downloads.
- A small config file to choose generated names.
- `select` on typed tables, which needs a design, because a generated class requires every column.
- A setting that repeats a typed table's schema check after a set time.
- Typed access to file and user values, as a dictionary subclass, which could leave out uploader emails.

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
- Do other endpoints or realms have a different request size limit?

**Reports**

- Can a deleted report's ID be reused? A new report got ID 8, the highest so far, but the deleted
  report's own ID wasn't recorded. If it can, cached report details could misread a total as a Record
  ID#.
- Do a report's pages stay stable with ties above 1,200 records? A report's sort can't add Record ID#.

**Fields and files**

- What do lookups return for types other than text, numeric, and date, and what about field types only
  the UI can create? These are `Any` and read-only in generated classes until confirmed.
- How do file fields behave with other version settings, and are there limits on total storage?

**Realms and apps**

- What's the API host for EU realms?
- Can a formula read a table in another app?
- How do roles with record-level rules behave, and a report that sorts or filters on a hidden field?
  Roles with write access are in the [API notes](docs/api-notes.md#roles-with-write-access).
- Can a restricted user's formula read a table their role has no access to? A role's hidden fields held
  up, but this wasn't tested, because the role's other tables were the seeded ones.
  `docs/guides/agents.md` calls this untested, so update it with the answer.
- Can the UI give two tables the same name? The API refuses it, ignoring case. If so, `app.table()`
  takes the first match, unlike fields and reports, which raise an error.
