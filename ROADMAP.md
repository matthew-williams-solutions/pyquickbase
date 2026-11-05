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

## Agent features

- Confirm what unit Quickbase takes when writing a `duration` field, then let operations write them.
  Reading one returns milliseconds, and operations leave them out of `records` until then.

## After 1.0

Nothing here should break anyone.

- Switch the docs from Material to Zensical, **by 2027-05-05**, when Material's maintenance ends. A
  trial build of 0.0.74 worked, and its strict mode also catches broken anchors, which MkDocs doesn't.
  Wait for 0.1.0, announced for 2026-11-05. `scripts/build_llms.py` reads the nav from
  `mkdocs.yml`, so update it if the config moves.
- `llms.txt` is at `/pyquickbase/llms.txt`, not the domain root where some tools look, because the
  site is served from a subpath. Only a custom domain would change that.
- Find out how `COUNT` summaries work. `ensure_summary_field()` always sends a child field, and
  Quickbase refused `COUNT` on every field tried, so the SDK may need to leave it out.
- A way to set `fieldHelp` and the other field settings the API takes outside `properties`.
- A small config file to choose generated names.
- A setting that repeats a typed table's schema check after a set time.

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
