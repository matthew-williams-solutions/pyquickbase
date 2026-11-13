# Roadmap

What's left to do. Delete an item once it's done: `CHANGELOG.md` and git history record what's
finished, and `docs/api-notes.md` records what's been confirmed about Quickbase. Limits we've decided
to keep are explained in the guides, not here.

## Before 1.0

- Release a candidate with schema plans and those methods, then let it run for at least a week with no code
  changes. Each new candidate restarts the week.
- A plan checks a table it creates against the client's policy only when it's applied, so a change to
  that table can still be refused partway through. Planning again resumes from there.
- Run the full live suite against the new `Policy`. The four live tests its design asked for are in
  `tests/_async/live/test_policy_design.py`.
- In the 1.0 release commit:
  - Change the classifier in `pyproject.toml` from Beta to Production/Stable.
  - Remove the release candidate warnings from `README.md` and `docs/index.md`, and the advice to pin
    the exact version. If the pins go too, update `test_install_pins_match_the_version`.

## After 1.0

Nothing here should break anyone.

- `llms.txt` is at `/pyquickbase/llms.txt`, not the domain root where some tools look, because the
  site is served from a subpath. Only a custom domain would change that.

## Docs

The API reference is arranged by subject. What's left from the docs review:

- Decide whether every reference page should show, under a method's **Returns**, what the returned
  object holds. The Reports page tries it, from `INLINE_RETURNS` in `scripts/build_reference.py`.
- Generate `docs/field-types.md` from the SDK, as the reference is, and test or remove its eight
  "Not tested" cells. It's hand-written, so it has no Markdown copy for AI tools.
- New pages: how pyquickbase works (looking up labels, the metadata cache, lazy objects, batching,
  and paging), a map from each REST endpoint to its method, troubleshooting by symptom from what
  `docs/api-notes.md` has confirmed, and recipes for the core SDK like the policy recipes.
- `docs/api-notes.md` is a record of what was tested, so send readers to the troubleshooting page
  first once it exists.

## Apps, events, and DataFrames

- `AppTrigger` parsing is only tested against the published example. The test apps have no webhooks,
  actions, notifications, or automations, and the API can't make one, so confirm the shape on an app
  that has each kind, including whether an automation ever has a `name`.
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

- Is `api.quickbase.eu` the API host for EU realms? It answers without a token just as `api.quickbase.com`
  does, and the help only says EU realms use `.quickbase.eu`. Confirming it needs a real EU realm, as does
  confirming that XML calls go to `<realm>.quickbase.eu`.
- Does `/db/<app>?a=dbpage&pagename=...`, which `CodePage.url()` builds from the help's form, open the page in a
  browser? A user token gets a redirect to sign in, so this needs a browser session.
- Does a key field set on a table with records behave differently? `SetKeyField` was only tried on an empty table.
- Do the roles between Basic Access and administrator get the same answers from the XML methods? Basic Access with
  Share is untested too.
- Do errcodes 77 and 100, 101, 104, and 105 retry as designed? None has come up in live testing.
