# Roadmap

What's left to do. Delete an item once it's done: `CHANGELOG.md` and git history record what's
finished, and `docs/api-notes.md` records what's been confirmed about Quickbase. Limits we've decided
to keep are explained in the guides, not here.

## Before 1.0

- Release a candidate with schema plans and those methods, then let it run for at least a week with no code
  changes. Each new candidate restarts the week.
- Check the formula functions a client with a policy allows, `SAFE_FUNCTIONS` in `_formula.py`,
  against Quickbase's function list. A missing one is refused by name, so this only widens what's
  allowed, but each one added must only read the record's own values.
- A plan checks a table it creates against the client's policy only when it's applied, so a change to
  that table can still be refused partway through. Planning again resumes from there.
- `policy_effects` works from the bound policy, not by binding the original `Policy` against the
  schema the plan leaves, so it doesn't see a class losing its only named field. A deny rule left
  matching nothing that way isn't a blocker yet.
- Run the full live suite against the new `Policy`. The four live tests its design asked for are in
  `tests/_async/live/test_policy_design.py`.
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

## Apps, events, and DataFrames

- `AppTrigger` parsing is only tested against the published example. The test apps have no webhooks,
  actions, notifications, or automations, and the API can't make one, so confirm the shape on an app
  that has each kind, including whether an automation ever has a `name`.
- A run killed during `tests/_async/live/test_apps.py` leaves an app named `ZZ Live App ...`. `client.apps()`
  can list it now, so `scripts/clean_live_tables.py` could find and delete it. None has existed yet to confirm that
  the token lists it.
- `field_usage()` asks again, field by field, for any field a response leaves out. A table larger
  than one response is untested, so it isn't known whether Quickbase ever leaves fields out.

## Demo app

The Quickbase demo app and its Python code page, which is kept outside this repo. Do these before anyone
else gets access, because the page writes with the viewer's own permissions:

- Pin the SDK version the page loads, and verify the downloaded files where possible. It currently
  loads whichever wheel the docs site lists.
- Block the Python frame from navigating away with data in the address, or disclose it plainly. The page
  closes the frame after it navigates, which is too late to stop the data leaving.
- Decide who gets access, and whether their writes should be limited. Anyone who can add and delete
  tasks can also run Reset tasks, which replaces the Tasks records for everyone.
- Find out which roles can add a code page. An administrator can, and a Basic Access role can't. The roles between
  them are untested.
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
  does, and the help only says EU realms use `.quickbase.eu`. Confirming it needs a real EU realm, as does
  confirming that XML calls go to `<realm>.quickbase.eu`.
- Does `/db/<app>?a=dbpage&pagename=...`, which `CodePage.url()` builds from the help's form, open the page in a
  browser? A user token gets a redirect to sign in, so this needs a browser session.
- Does a key field set on a table with records behave differently? `SetKeyField` was only tried on an empty table.
- Do the roles between Basic Access and administrator get the same answers from the XML methods? Basic Access with
  Share is untested too.
- Do errcodes 77 and 100, 101, 104, and 105 retry as designed? None has come up in live testing.
