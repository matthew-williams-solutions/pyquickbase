# Quickbase API notes

What this SDK relies on, and how far each point has been confirmed. Much of the developer portal
renders in the browser and has no published spec that we've found, so check anything marked
"unverified" against a real app before depending on it.

## Confirmed

| Behavior | Source |
|---|---|
| Headers: `QB-Realm-Hostname` (full hostname), `Authorization: QB-USER-TOKEN <token>` | Accepted by the live API |
| Error body: JSON with `message` and `description` | Live API, 2026-11-02 |
| `qb-api-ray` response header holds a request ID | Live API, 2026-11-02 |
| 100 requests per 10 seconds per user token for most endpoints; some differ | [Rate limiting docs](https://help.quickbase.com/docs/rate-limiting-overview.md) |
| On a 429, read `retry-after`; retry only for 429 | Same page |
| Operators: CT, XCT, HAS, XHAS, EX, TV, XTV, XEX, SW, XSW, BF, OBF, AF, OAF, IR, XIR, LT, LTE, GT, GTE, uppercase only | [Query docs](https://help.quickbase.com/docs/components-of-a-query) |
| Query dates: `yyyy-MM-dd` or milliseconds, not date-time strings | Same page |
| Query `metadata` has `numFields`, `numRecords`, `skip`, `totalRecords`, and `top` when it was sent | Live API, 2026-11-02 |
| Omitting `options` or sending `top: 0` returns all matches in a small table; a `skip` past the end returns no records | Live API, 2026-11-02 |
| `EX` on text ignores case | Live API, 2026-11-02 |
| `AND` and `OR` must be uppercase; whitespace around them is accepted | Live API, 2026-11-02 |
| Unquoted values such as `{3.EX.5}` work the same as quoted ones | Live API, 2026-11-02 |
| `EX ''` matches empty fields and `XEX ''` matches non-empty ones | Live API, 2026-11-02 |
| On a date field, `LT`, `LTE`, `GT`, and `GTE` match the same records as `BF`, `OBF`, `AF`, and `OAF` | Live API, 2026-11-02 |
| Timestamps can be compared with `AF` and `BF` using milliseconds | Live API, 2026-11-02 |
| With no `select`, only fields in the table's default set return, and Record ID# may not be one | Live API, 2026-11-02 |
| Timestamps return as `2026-10-21T11:49:56Z`, dates as `2026-10-18` | Live API, 2026-11-02 |
| A nonexistent app or table returns 401 with a valid token, not 404 | Live API, 2026-11-02 |

A bad realm or app can return 400 rather than 401 or 404, even with an invalid token, so don't
assume a 400 means the request body was wrong.

## Query pitfalls

Tested on 2026-11-02 against a small sandbox app. These matter because many mistakes return a
plausible result instead of an error.

| `where` clause | Result |
|---|---|
| Lowercase or unknown operator: `{6.ex.'x'}`, `{6.NOPE.'x'}` | **Behaves like `CT`** and returns matches |
| Unquoted value: `{6.EX.foo}` | No records, no error |
| Unknown field: `{999999.EX.'x'}` | No records, no error |
| Field label instead of ID: `{'Title'.EX.'x'}` | No records, no error |
| Trailing join: `{6.EX.'x'}AND` | No error |
| Lowercase join, missing brace, or free text | 400, `Criteria is invalid.` |

**Quoting values.** Tested with a temporary record containing `O'Brien`, a backslash, and a brace:

| Text in a value | Result |
|---|---|
| `\'` or an unescaped `'` | Matches a stored apostrophe, but the unescaped form is ambiguous, so `F` and the string validator use `\'` |
| `''` | Doesn't match a stored apostrophe |
| `\\` | Matches a stored backslash; a single `\` before another character doesn't |
| `{` or `}` | 400, `Criteria is invalid.`, so values containing braces can't be searched for |

**A value can break out of its clause.** With `zz'}OR{3.GT.'0` as the value, the query matched every
record whether the quotes were left alone, written as `\'`, or written as `''`. Every breakout we
found needs a brace, so `F` rejects braces in values. The string validator can't tell a breakout
from a genuine second condition, so use `F` for any text you don't control.

## Writes

Tested on 2026-11-02 against temporary tables in a sandbox app.

| Behavior | Result |
|---|---|
| A row fails | The request returns 207, other rows are written, and `metadata.lineErrors` maps the row number, starting from 1, to its messages |
| `{"value": null}` | **Silently ignored**, leaving the old value |
| `{"value": ""}` | Clears text, date, timestamp, numeric, choice, and checkbox fields |
| Row with an existing Record ID# | Updates it, or reports it in `unchangedRecordIds` if nothing changed |
| Row with a missing Record ID#, with or without `mergeFieldId` | Row error, and no record is created |
| `mergeFieldId` on a unique field, value doesn't match | **Creates a record** |
| `mergeFieldId` on a non-unique field, or a row without the merge field | 400 for the whole request |
| Unknown field, formula field, built-in field | Row error |
| A value outside a field's choices | Accepted when `allowNewChoices` is on, otherwise a row error |
| Empty `data` | 400 |
| `DELETE /records` without `where` or with a blank one | 400 |
| `DELETE /records` with a malformed `where` | **`numberDeleted: 0`, no error** |
| `DELETE /records` with a lowercase operator | **Acts like `CT`**, so it can delete more than intended |
| `DELETE /fields` with `{"fieldIds": [...]}` | Partial failures are listed in `errors` beside `deletedFieldIds` |
| `POST /fields` with `unique` or `required` | 400. Set them afterward with `POST /fields/{id}` |
| Reading a cleared date or timestamp | Returns `""`, and a cleared numeric returns null |
| Checkbox queries | `{id.EX.'true'}`, `{id.EX.'1'}`, and `{id.TV.'true'}` match the same records |

## Reports

Tested on 2026-11-02 against a temporary table in a sandbox app, with table, ask-the-user, summary,
and chart reports built in the UI. The API can't create reports.

| Behavior | Result |
|---|---|
| `GET /reports?tableId=` | A list of the same objects as `GET /reports/{id}?tableId=`: `id`, `name`, `type`, `description`, `query`, `properties`, `incomplete`, `usedCount`, and `usedLast` once used |
| Report IDs | Numeric strings, **unique only within a table**. A new table gets `1` (List All) and `2` (List Changes) |
| Report names | **Can repeat within a table** |
| `POST /reports/{id}/run?tableId=` | Needs no body. Returns `fields`, `data`, and `metadata`, as a query does |
| `top` and `skip` | Must be query parameters. **In the body they're silently ignored, and every record returns** |
| `top` omitted | Returned every record in a 12-record table |
| `top` of 0 or less, or a negative `skip` | 400, although a query accepts `top: 0` |
| `skip` past the end | No records, no error |
| A report ID with leading zeros, such as `01` | Runs the same report |
| Missing `tableId` | 400 |
| Unknown table | 401 for an ID that never existed, 404 for a dropped table |
| Unknown or non-numeric report ID | 404 |
| The report's filter, sort, and columns | Applied. Rows hold only the report's columns |
| A report whose columns leave out Record ID#, including the default `List All` | Rows have no field 3 |
| Running a report | **Adds 1 to `usedCount`, even when the run fails.** A GET doesn't, and records aren't modified |
| Ask-the-user report | `incomplete: true`, and running it returns 400, `Incomplete reports not supported` |
| Summary and chart reports | **Same response shape as a table report, but `fields` IDs are column positions starting at 2.** The second column has ID 3, the Record ID# field in a table report, so nothing in the response tells them apart. Only `type` on the report does |
| Chart category values | Dates return as text, `01-01-2026`, not `yyyy-MM-dd` |
| Renaming a column in a report | The UI doesn't offer this |

## File attachments

Tested on 2026-11-02 against a temporary table in a sandbox app, using a new `file` field with its
default settings.

| Behavior | Result |
|---|---|
| Upload | `POST /records` with `{"value": {"fileName": ..., "data": <base64>}}` |
| Download | `GET /files/{table}/{record}/{field}/{version}`. **The body is base64**, whatever `Content-Type` says. Bytes round-trip exactly, including all 256 values |
| File name in a download | `Content-Disposition: attachment; filename*=utf-8''<percent-encoded>` |
| `Content-Type` of a download | Based on the file name's extension: an `.html` name gives `text/html`, with a base64 body |
| Version `0` | The latest, **for `DELETE` as well as `GET`** |
| Each upload | Adds a version, even with identical contents, and reports the record as updated |
| Version limit | A new field has `maxVersions: 3`. **The oldest is silently removed**, and its download returns 404 |
| Version numbers | Not reused or renumbered after a delete or removal |
| `DELETE` a version | Returns that version's details. Deleting the last one keeps the record and empties the field |
| `{"value": ""}` or `{"value": null}` on a file field | **No error and no change**, reported in `unchangedRecordIds`. On insert, creates a record with no file |
| `{"value": "text"}`, or a value without `data` | Row error |
| A value without `fileName` | **No error, and creates a record with no file** |
| Empty `fileName` | Row error |
| Invalid base64 | **Accepted, and the file holds garbage**, with no row error, also inside a batch |
| Empty `data` | Accepted, as an empty file |
| A file value sent to a text field | **Stores the file name and discards the contents** |
| File names | **Stored as sent, including `../../etc/passwd`, `C:\\x\\y.txt`, and line breaks.** A NUL byte cuts the name short. No extension was refused, including `.exe` and `.html` |
| Reading a file field | `{"url": "/files/...", "versions": [...]}`, with a relative `url`. Empty is `{"url": "", "versions": []}` |
| Each entry in `versions` | `versionNumber`, `fileName`, `uploaded`, and `creator`, **which includes the uploader's email address** |
| Missing record, field, or version | 404 |
| A non-file field | 400 |
| Unknown table | 400, `Invalid usertoken`, unlike other endpoints' 401 |

**Request size limit.** `POST /records` accepted a body of exactly 41,943,040 bytes (40 MiB) and
returned 413, `request entity too large`, at one byte more. It's a limit on the whole request: two 25
MB files in one request returned 413, and two 15 MB files succeeded. It applies without files too:
1,500 rows of 30,000 characters returned 413 and wrote nothing. The largest file that fits was
31,457,208 bytes. Downloads worked up to that size.

## Unverified

Check these against a sandbox app before the feature that needs them ships.

| Question | Needed for |
|---|---|
| Format and units of `retry-after` on a real 429 (both seconds and dates are handled) | Transport |
| Can a query return fewer than `top` records before the end? The test table was too small to show it, so advance by `numRecords` | Paging |
| Which endpoints have different rate limits? | Transport |
| Correct API host for EU realms (pass `base_url` if it differs) | Realm handling |
| Temporary token format | Temporary tokens |
| Table reports with a formula column, or with Record ID# as a column | Reports |
| Whether pages of a report are stable when its sort has ties | Reports |
| Whether a report's `top`-less run returns every record in a large table | Reports |
| Whether other endpoints or realms have a different request size limit | Writes |
| File fields with other version settings, and limits on total storage | File attachments |
| How reports and files behave for tokens with fewer permissions, since all testing used one token | Reports, file attachments |

## Known limits to revisit

Choices made in the SDK that are worth another look before 1.0.

| Limit | Why it's there | Possible change |
|---|---|---|
| Report details are cached for `schema_ttl`, so a report changed in the UI can be checked against old details | Avoids an extra request for every page | Refresh before each run, or compare the run response |
| Summary and chart reports can only be read through `qb.request()` | Their column IDs are positions, so they can't be records | A separate method returning `{label: value}` rows |
| `timeout` is set per client, so a large upload on a slow connection may need a larger one for every request | The transport has one timeout | A per-call timeout for uploads and downloads |
| Files are held in memory as bytes and base64 while being sent | The API takes base64 inside JSON, so there's nothing to stream | Probably none, unless Quickbase adds another upload route |
| `record["Attachment"]` returns Quickbase's raw dictionary | Kept simple for the first version | A typed value with versions, which could leave out uploader emails |
