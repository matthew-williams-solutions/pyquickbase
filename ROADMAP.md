# Roadmap

What's left to do. Delete an item once it's done: `CHANGELOG.md` and git history record what's
finished, and `docs/api-notes.md` records what's been confirmed about Quickbase. Limits we've decided
to keep are explained in the guides, not here.

## Before 1.0

- Release a candidate with schema plans, `feature/schema-changes`, then let it run for at least a week
  with no code changes.
- Check the formula functions a client with a policy allows, `SAFE_FUNCTIONS` in `_formula.py`,
  against Quickbase's function list. A missing one is refused by name, so this only widens what's
  allowed, but each one added must only read the record's own values.
- A plan checks a table it creates against the client's policy only when it's applied, so a change to
  that table can still be refused partway through. Planning again resumes from there.
- `limit.rows()` covers queries, summaries, and deletes, but not updates by `Record ID#`, which name a
  record without a filter. Checking them would mean a count before each write batch, which could
  race with another change.
- `policy_effects` works from the bound policy, not by binding the original `Policy` against the
  schema the plan leaves, so it doesn't see a class losing its only named field. A deny rule left
  matching nothing that way isn't a blocker yet.
- Adding a lookup or summary is `update_relationship` in `WriteRequest.action`, because Quickbase adds
  both with a POST to an existing relationship, and `request.derived` says which. Schema changes call
  them `create_lookup_field` and `create_summary_field`. Giving `WriteAction` those values means
  deciding what a request that adds both is called, changing the checks in `_bound.py`, and breaking
  custom rules that test for `update_relationship`.
- `BoundPolicy` has public methods that only the SDK uses, such as `trim()` and `offers_for()`, and a
  public constructor. Move them behind a private interface, so the public API is only `decide()`,
  `allows()`, `explain()`, `offers()`, and saving.
- Run the live tests against the new `Policy`, and add the ones the design asks for: a hidden label
  collision, a summary over a row-limited table, a lookup with only read access on the parent, and a
  created table after `from_json()`.
- In the 1.0 release commit:
  - Change the classifier in `pyproject.toml` from Beta to Production/Stable.
  - Remove the release candidate warnings from `README.md` and `docs/index.md`, and the advice to pin
    the exact version. If the pins go too, update `test_install_pins_match_the_version`.

## After 1.0

Nothing here should break anyone.

- Switch the docs from Material to Zensical, **by 2027-05-05**, when Material's maintenance ends. A
  trial build of 0.0.74 worked, and its strict mode also catches broken anchors, which MkDocs doesn't.
  Wait for 0.1.0, announced for 2026-11-05. `scripts/build_llms.py` reads the nav from
  `mkdocs.yml`, so update it if the config moves.
- `llms.txt` is at `/pyquickbase/llms.txt`, not the domain root where some tools look, because the
  site is served from a subpath. Only a custom domain would change that.
- Link each error to the docs, as Stripe does: a `doc_url` on every `QuickbaseError`, pointing to a
  section on its cause and fix, at the end of its message, and in `tool_error()` results. The
  sections must exist first, so `docs/reference/errors.md` or a new errors page needs a stable
  anchor for each error, and a test that every error's anchor exists.

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

- Does the new interface have a link to the records that match a filter, or does the classic
  `/db/<table ID>?a=q&query=<filter>` still redirect to one? If so, add a filtered link built from
  `compile_where()`.

- Is `api.quickbase.eu` the API host for EU realms? It answers without a token just as `api.quickbase.com`
  does, and the help only says EU realms use `.quickbase.eu`. Confirming it needs a real EU realm.
