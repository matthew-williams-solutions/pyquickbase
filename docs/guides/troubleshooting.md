---
description: "What to check when something goes wrong, by what you see: errors for things that exist, missing fields and records, surprising values, and failed writes."
---

# Troubleshooting

Find what you're seeing below. Each answer comes from a guide, or from behavior confirmed against
Quickbase in the [API notes](../api-notes.md), and links to it. Each error also links to its own
section of the [Errors](../reference/errors.md) reference, which says how to fix it.

## A token that works elsewhere raises AuthenticationError

Quickbase returns the same 401 for a token that's wrong, an app or table that doesn't exist, and an
app the token can't reach, so the error can't say which. Check that the token is assigned to the app,
and the app's and table's IDs. A badly formed ID returns 400 instead, as [`BadRequestError`](../reference/errors.md#BadRequestError). See the
[API notes](../api-notes.md#confirmed).

## UnknownTableError or UnknownFieldError for something that exists

- **The name must match exactly,** including capitals. The error suggests close matches.
- **Several fields can share a label.** The error lists their IDs, so use one.
- **The token's role, or the client's policy, can hide it,** and then it looks as if it doesn't exist.
- **The client's cache is older than the change** only for a moment: it looks a name up again before
  raising, so one just made in the Quickbase interface is found. See
  [How pyquickbase works](how-it-works.md#the-metadata-cache).

## A record doesn't have a field

Reading a field a record doesn't hold raises `KeyError`. Without `select`, a query returns only the
table's default fields, which may not include built-in ones such as `Record Owner`, so name the fields
you need in `select`. A report's records hold only its own columns, so `record.id` raises if Record
ID# isn't one of them. See [Reading records](reading.md#fetching-records).

## A query misses records, or matches more than expected

- **Text comparisons ignore case.** `F("Status") == "done"` also matches `Done`.
- **Deleting records while reading can skip others,** with no error, as pages are fetched by
  position. Read everything first, then delete. See [Reading records](reading.md#fetching-records).
- **A hand-written query string is sent as written** once the SDK has checked it for mistakes
  Quickbase would accept, such as a lowercase operator, so a condition that's valid but wrong isn't
  caught. Build conditions with [`F`](../reference/queries.md#F) where you can. See
  [Reading records](reading.md#hand-written-query-strings).

## An empty field doesn't read as None

An empty text field reads as `""`, and an empty checkbox as `False`, so check text with `if not
value`. Numbers and dates read as `None`. [Field types](../field-types.md) lists each type.

## A formula treats a blank number as 0

In a formula, an empty number field can read as 0, so `IsNull([Hours])` can be false. See the
[API notes](../api-notes.md#formulas).

## A formula reads nothing from another app

A formula can read other tables in its own app, but reading a table in another app returns an empty
result rather than an error. See the [API notes](../api-notes.md#formulas).

## A write raised WriteError, but some rows were saved

Quickbase writes the rows it accepts and refuses the rest. `error.result` lists what was created and
updated, and `failed_rows` says why each other row failed, so fix those and send only them. See
[Writing records](writing.md#when-rows-fail).

## insert() refuses a table

If someone has changed a table's key field from Record ID#, Quickbase updates a record when a row
matches it, rather than creating one, so [`insert()`](../reference/writes.md#Table-insert) refuses the table. Use [`upsert()`](../reference/writes.md#Table-upsert) with `merge_on`
set to the key field. See [Writing records](writing.md#tables-with-a-custom-key-field).

## A write timed out: was it saved?

It may have been. A write isn't retried after a timeout, because Quickbase may already have applied
it, so check before sending it again, such as with [`get()`](../reference/records.md#Table-get) or by its unique field. See
[Handling errors](errors.md#when-a-write-may-have-landed).

## RateLimitError

The client retries a rate limit for you, so this means its retries ran out, or Quickbase asked for a
longer wait than the client allows. Wait `error.retry_after` seconds, send fewer requests at once, or
give the client a [`RetryPolicy`](../reference/client.md#RetryPolicy) that waits longer. See [Configuring clients](clients.md#retries).

## A datetime raises InvalidValueError

A `datetime` needs a timezone, so that the time it names is clear. Use one such as
`datetime.now(timezone.utc)`, or a `date` for a date field. See
[Writing records](writing.md#writing-values).

## A report raises UnsupportedReportError

`run()` reads a table report's records. Use [`run_summary()`](../reference/reports.md#Report-run_summary) for a summary or chart report, which
reads its rows as Quickbase calculates them. A report that asks the user for a value can't run through
the API at all. See [Running reports](reports.md#reading-summary-and-chart-reports).

## A change made in Quickbase doesn't show up

The client keeps a table's fields for five minutes. Call [`qb.clear_metadata_cache()`](../reference/client.md#Quickbase-clear_metadata_cache), or pass
`refresh=True` to `fields()`. See [How pyquickbase works](how-it-works.md#the-metadata-cache).
