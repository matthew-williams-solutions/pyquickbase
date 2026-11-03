# Quickbase API notes

What this SDK relies on, and how far each point has been confirmed. Much of the developer portal
renders in the browser and has no published spec that we've found, so check anything not listed here
against a real app before depending on it. Questions we haven't answered yet are in the
[roadmap](https://github.com/matthew-williams-solutions/pyquickbase/blob/main/ROADMAP.md#open-questions).

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
| **A query can return fewer than `top` records before the end.** With rows of 30,000 characters, `top: 3000` returned 831 at a time, so advance by the number returned | Live API, 2026-11-02 |
| Paging uses offsets, so deleting 50 earlier rows while reading 600 **silently skipped 50 unread records** | Live API, 2026-11-02 |
| 50,000 rows: inserted in 12 seconds, read in 8, deleted in one request in 1 second. Each came back exactly once, and a sort with ties stayed stable across pages of 999 and 7,000 with Record ID# as the last sort key | Live API, 2026-11-02 |
| `EX` on text ignores case | Live API, 2026-11-02 |
| `AND` and `OR` must be uppercase; whitespace around them is accepted | Live API, 2026-11-02 |
| Unquoted values such as `{3.EX.5}` work the same as quoted ones | Live API, 2026-11-02 |
| `EX ''` matches empty fields and `XEX ''` matches non-empty ones | Live API, 2026-11-02 |
| On a date field, `LT`, `LTE`, `GT`, and `GTE` match the same records as `BF`, `OBF`, `AF`, and `OAF` | Live API, 2026-11-02 |
| Timestamps can be compared with `AF` and `BF` using milliseconds | Live API, 2026-11-02 |
| With no `select`, only fields in the table's default set return, and Record ID# may not be one | Live API, 2026-11-02 |
| Timestamps return as `2026-10-21T11:49:56Z`, dates as `2026-10-18` | Live API, 2026-11-02 |
| A nonexistent app or table returns 401 with a valid token, not 404. An ID of the wrong shape, such as `abc` or one that's too long, returns 400, `Invalid request` | Live API, 2026-11-02 |
| `GET /tables/{id}/relationships` lists only relationships where the table is the child. A parent table returns none | Live API, 2026-11-02 |
| Creating a table with a name already in the app, ignoring case, returns 400, `Invalid input` | Live API, 2026-11-02 |

A bad realm or app can return 400 rather than 401 or 404, even with an invalid token, so don't
assume a 400 means the request body was wrong.

## Query pitfalls

Tested on 2026-11-02 against a small sandbox app. These matter because many mistakes return a
plausible result instead of an error.

| `where` clause | Result |
|---|---|
| Lowercase or unknown operator: `{6.ex.'x'}`, `{6.NOPE.'x'}` | **Behaves like `CT`** and returns matches |
| Unknown field: `{999999.EX.'x'}` | No records, no error |
| Anything but a number as the field: `{'Title'.EX.'x'}`, `{Title.EX.'x'}`, even `{'No Such'.EX.'x'}` | **No error, and it matched records holding the value.** The SDK's validator refuses these |
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
record whether the quotes were left alone, written as `\'`, or written as `''`. So `F` rejects braces
in values. The string validator can't tell a breakout from a genuine second condition, so use `F` for
any text you don't control.

**A quoted value can still be query syntax**, with no way to escape it. Tested on 2026-11-02:

| Value | Result |
|---|---|
| `a OR b` | **Matches `a` and `b`, and not a stored `a OR b`.** Also with `EX`, `CT`, `SW`, `TV`, and their opposites, on text, numeric, and date fields |
| `x AND y` | Matches nothing, not even a stored `x AND y` |
| `OR` or `AND` alone, `x OR`, `OR y` | The word is dropped: `x OR` matches `x`, and `OR` alone matches nothing |
| What splits a value | **Only uppercase `OR` and `AND` between spaces or tabs.** `Or`, `x,OR,y`, `x-OR-y`, `xOR y`, and a line break or non-breaking space around it don't |
| The same words in lowercase | Literal text. Comparisons ignore case, so `x or y` finds a stored `x OR y`, including on multi-select fields. This is what `F` sends |
| `_FID_6`, in any case | **Compares with field 6, so on field 6 it matches every record.** `DELETE /records` with `{6.EX.'_FID_6'}` deleted every record. `F` rejects these |
| `_FID_6x`, `_FID_06`, `_FID_ 6`, `_FID_6 x`, `\_FID_6` | The same. A leading space, other text, or `\\` before `_FID_` makes it literal, so there's no way to search for this exact text |
| `_ask1_` | 400, `Incomplete reports not supported`. `_ASK1_` is literal |
| `_curuser_`, on a text field | Literal |
| `TRUE`, `NULL`, `today`, `*`, `%`, `_` on a text field | Literal |

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
| A batch where every row sets the same one field, and a row's value is empty | **That row is silently skipped, with no error**, so `createdRecordIds` no longer lines up with the rows. A batch of one row, or using two or more fields, creates it. `insert()` and `upsert()` refuse these rows |
| A value outside a field's choices | Accepted when `allowNewChoices` is on, otherwise a row error |
| The row error for an invalid choice | `The field with ID '7' has an invalid choice.` Confirmed again in a three-row import: 207, `raise_for_status()` passes, and the other two rows are saved |
| Empty `data` | 400 |
| `DELETE /records` without `where`, or with an empty one | 400 |
| `DELETE /records` with only whitespace as `where` | **200, with `numberDeleted: 0`**, and nothing deleted |
| `DELETE /records` with a malformed `where` | **`numberDeleted: 0`, no error** |
| `DELETE /records` with a lowercase operator | **Acts like `CT`**, so it can delete more than intended |
| `DELETE /fields` with `{"fieldIds": [...]}` | Partial failures are listed in `errors` beside `deletedFieldIds` |
| `POST /fields` with `unique` or `required` | 400. Set them afterward with `POST /fields/{id}` |
| Reading a cleared date or timestamp | Returns `""`, and a cleared numeric returns null |
| Checkbox queries | `{id.EX.'true'}`, `{id.EX.'1'}`, and `{id.TV.'true'}` match the same records |

## Payments and repeated events

Tested on 2026-11-02 with the SDK against temporary tables in a sandbox app.

| Behavior | Result |
|---|---|
| 8 simultaneous `POST /records` with the same new value on a unique `mergeFieldId` | All succeeded, and **exactly 1 record was created** |
| 40 simultaneous writes of different values | All landed |
| Repeating the same write | Reported as unchanged, with no new record |
| `insert` of a value already in a unique field | Row error, and no new record |
| `Decimal` amounts from `0.01` to `99999999.99` in a currency field | Read back to the exact cent, through `Decimal(str(value))` |
| Whole numbers up to 9,007,199,254,740,991 in a numeric field | Read back exactly |

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
| `top` of 0 or less, or a negative `skip` | 400, although a query accepts `top: 0` |
| `skip` past the end | No records, no error |
| A report ID with leading zeros, such as `01` | Runs the same report |
| Missing `tableId` | 400 |
| Unknown table | 401 for an ID that never existed, 404 for a dropped table |
| Unknown or non-numeric report ID | 404 |
| The report's filter, sort, and columns | Applied. Rows hold only the report's columns |
| A report whose columns leave out Record ID#, including the default `List All` | Rows have no field 3 |
| A table report with Record ID# as a column, even in second position | `fields` uses the real ID, 3, with type `recordid`, unlike a summary report's positions. All 1,200 rows matched a query's IDs |
| A table report with a formula column | `fields` uses the formula field's real ID and its type, such as `numeric`. All 1,200 values matched a query |
| A sort with ties, 400 records on each of 3 values | Ties came back in Record ID# order. Six full runs, with page sizes of 7, 37, 100, and 1,000, gave the same order, with nothing repeated or missing. Editing a tied record's other field didn't move it |
| `top` omitted, in a 50,000-record table | Returned every record, and `metadata` has no `top` |
| Changing a saved report's type | The UI doesn't offer this |
| Running a report | **Adds 1 to `usedCount`.** A GET doesn't, a 400 for a bad `top` or `skip` doesn't, and records aren't modified. An earlier test saw a failed run add 1 too, for a reason not pinned down |
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

## Formulas

Tested on 2026-11-02 against temporary tables in a sandbox app.

| Behavior | Result |
|---|---|
| `POST /formula/run` | Takes `formula` and `from`, and `rid` if the formula uses fields. Returns `result`, `resultType`, and `fieldTypeApplied` |
| `result` | **Always a string**, whatever the type |
| `resultType` values seen | `text`, `number`, `bool`, `date`, `date/time`, `timeofday`, `duration`, `textlist`, `user`, `null` |
| Booleans | **`"1"` and `"0"`**, so `"0"` is a truthy string in Python |
| Numbers | Rounded for display: `1/3` returns `"0.3333333"` |
| Dates and timestamps | **Display text in the app's format, with no time zone:** `03-04-2026` and `03-03-2026 09:06 PM`, where the stored time was `2026-03-04T05:06:07Z`. A query returns the same field as ISO 8601 |
| Durations | Display text: `Days(3)` returns `"72:00"` |
| Empty values | `""`, for any type |
| A blank formula | `""` returns 400. **Whitespace returns 200 with an empty `null` result** |
| A formula using a field, with no `rid` | 400 |
| Missing record | 404, even for a formula that uses no fields |
| Missing `from` | 400 |
| Unknown table | 401 |
| A syntax error, unknown field or function, type mismatch, or `1/0` | 400. **Some messages include HTML**, such as a `javascript:` link |
| Function and field name case | Ignored: `today()` and `[title]` work |
| `fieldTypeApplied` | `false` in every test. Sending `fieldType` or `resultType` changed nothing, and we don't know what sets it |
| **Other tables** | **A formula can read other tables** with `GetRecord()` and `GetRecords()`, whatever `from` says |
| Other information | `AppID()`, `URLRoot()`, and `UserToEmail(User())` return the app ID, realm, and the token owner's email |
| Side effects | No record changed, including `Date Modified`, after about 60 runs |
| Formula length | A 1,000,000-character formula worked |

**Formula fields.** `POST /fields` with `properties.formula` creates one, with `mode: "formula"`. Unlike
many other mistakes, these are checked:

| Behavior | Result |
|---|---|
| A syntax error, unknown field, or result type that doesn't match the field | 400. A failed update keeps the old formula |
| Updating the formula | Values change right away, and other properties are kept |
| **Updating to an empty formula** | **200, and the field's values become empty** |
| Creating with an empty formula, or adding a formula to a plain field | 400, `Field must be of formula mode.` |
| Renaming a field a formula uses | Quickbase updates the formula's text |
| Writing to a formula field | Row error |
| Field types that took a formula | `text`, `numeric`, `date`, `checkbox`, `duration`, `timeofday`, `currency`, `percent`, `rating`, `url`, `email`, and `datetime` |
| **A timestamp formula** | **`timestamp` returns 400, `[formula] property is not supported`. `datetime` works, and the field then reports its type as `timestamp`** |
| `text-multi-line` | 400, `[formula] property is not supported` |
| Reading formula fields in a query | Full precision, unlike a formula run: `1/3` returns `0.3333333333333333`. A duration returns milliseconds, and a time of day `11:34:49.896` |

## Rate limits

Tested on 2026-11-02 with short bursts from 12 threads against temporary tables.

| Behavior | Result |
|---|---|
| `retry-after` on a real 429 | **Whole seconds**, seen from `0` to `10`. After waiting that long, the next request succeeded |
| 450 calls from 12 threads, with the default 4 retries | Took about 30 seconds and hit 24 to 53 real 429s. **All 450 succeeded in most runs, but in 1 of 7, a call used up its retries and raised `RateLimitError`.** With retries off, about 300 of the 450 raised it |
| `retry-after: 0` | Sent while still limiting, so an immediate retry can get another 429 |
| 429 body | `{"error": "Quota has been exceeded"}`, **not the usual `message` and `description`** |
| When it started | After roughly 170 to 185 responses in 4 to 6 seconds, for both `/formula/run` and `/records/query`. That's more than the documented 100 per 10 seconds, so don't rely on the number. These bursts don't show a difference between the two endpoints |

## Temporary tokens

**The SDK doesn't support temporary tokens.** Their main use is browser code pages, a script can't get
one, and the SDK's table lookup needs `GET /tables`, which a table-scoped token can't call. These
findings are kept for when that's revisited. Supporting them would need a `temporary_token` client
option that treats `app.table()` as an ID and skips that lookup, for a backend acting for a logged-in
user.

Tested on 2026-11-02 against temporary tables in a sandbox app, with tokens fetched from a logged-in
browser's console, because a script can't get one.

| Behavior | Result |
|---|---|
| `GET /auth/temporary/{table or app ID}` from a script | **400, `Domain not allowed`, with or without credentials.** The check runs before authentication |
| The same request from a page on the realm | Works, using the browser's session (`credentials: "include"`). Returns `temporaryAuthorization` |
| Format | An opaque string of 137 characters, with letters, digits, and `.` or `=` |
| Header | `Authorization: QB-TEMP-TOKEN <token>` |
| Lifetime | The token stopped working 266 seconds after being pasted into the test. It was fetched shortly before that, so this fits the documented 5 minutes without confirming it |
| Expired, or out of scope | Both return 401, `Invalid Authorization.`, so they can't be told apart |
| A malformed token, or a user token with this header | 400, `Invalid Authorization` |
| A temporary token with `QB-USER-TOKEN` | 401, `User token is invalid` |
| Requesting another token with one | 400, `Domain not allowed` |

| Request | Table-scoped token | App-scoped token |
|---|---|---|
| Records in its table: query, write | **200, including writes** | 401 |
| Fields and reports of its table, running a report or formula | 200 | 401 |
| Another table: query, write, fields | 401 | 401 |
| `GET /tables` and `GET /apps/{id}` | **401** | 200 |
| **A formula that reads another table** | **200, and returns that table's data** | 401 |

Tokens are scoped narrowly, and an app-scoped token doesn't reach its tables' contents. **Table scope
doesn't contain a formula**: `GetRecord()` read a table the token couldn't otherwise reach. Uploads,
downloads, and file deletes weren't tested with a temporary token, and neither were tokens from users
with fewer permissions.

## Restricted users

Tested on 2026-11-02 against a temporary table in a sandbox app, with a second user's token. Its role
could only view that table, had no access to the app's other tables, and had access `None` on one text
field and one file field. A role with write access or record-level rules wasn't tested.

| Behavior | Result |
|---|---|
| A token's permissions | Its user's, through their role |
| `GET /fields` | **A hidden field is left out**, exactly as if it didn't exist, so a deleted field and a hidden one can't be told apart |
| `GET /fields/{id}` for a hidden field | 500, `QuickBase is experiencing technical difficulties` |
| `GET /tables` | Lists only tables the role can use |
| `GET /apps/{id}` | 403, for this role |
| `select` with a hidden field and a visible one | **200, and the hidden field is silently left out** |
| `select` with only hidden fields | 400, `Report contains a restricted field` |
| `where` or `sortBy` on a hidden field | 403, whether or not anything matches |
| Running a table report with a hidden column | **200, and the column is silently left out.** A report with a hidden field in its sort or filter wasn't tested |
| A file field's value, read with view access | Includes `creator`, with **the uploader's email address** |
| Downloading from a visible file field | 200 |
| Downloading from a hidden file field | 404, `No such field` |
| A formula using a hidden field, directly or through `GetRecords()` | 403, including when `GetRecords()` only filters on it. A visible field works |
| Insert, update, delete, upload, deleting a file version, creating a field | 403, `Insufficient permissions`, and nothing changed |

## Field types

Tested on 2026-11-02 against temporary tables in a sandbox app. These decide the types in generated
classes, and an unlisted type becomes `Any` and read-only.

`POST /fields` accepts these types: `text`, `text-multiple-choice`, `text-multi-line`, `rich-text`,
`numeric`, `currency`, `rating`, `percent`, `multitext`, `email`, `url`, `duration`, `date`,
`datetime`, `timestamp`, `timeofday`, `checkbox`, `user`, `multiuser`, `address`, `phone`, and `file`.
A `datetime` field reports its type as `timestamp`.

| Field type | A query returns | When empty |
|---|---|---|
| `recordid` | Integer | Never empty |
| `text`, `text-multiple-choice`, `text-multi-line`, `rich-text`, `email`, `url`, `phone` | Text | **`""`, not null** |
| `numeric`, `currency`, `rating`, `percent` | Number | null |
| `duration` | Milliseconds | null |
| `date` | `2026-03-04` | `""` |
| `timestamp` | `2026-03-04T05:06:07Z` | `""` |
| `timeofday` | `13:30:00` | `""` |
| `checkbox` | Boolean | `false` |
| `multitext` | A list of text | `[]` |
| `user` | `{"email", "id", "name"}` | null |
| `multiuser` | A list of users | `[]` |
| `file` | `{"url", "versions"}` | `{"url": "", "versions": []}` |
| `address` | Display text, such as `1 Main St, Springfield 12345` | `""` |
| `dblink` | `""` in the one case seen | |

| Behavior | Result |
|---|---|
| Lookup and formula fields | Return the same shapes as their type. Lookups were tested holding text, numeric, and date values, and empty for user, multitext, and checkbox. A formula of `Null` matched its type on text, numeric, and checkbox fields |
| Writable fields | Built-in fields 1 to 5 aren't, nor are formula, lookup, and summary fields, or `address` |
| An `address` field | Is read-only, with text subfields for its parts, which can be written. Creating one also creates its subfields, and sets `Country` to a default |
| `dblink` | Created by a relationship, on the parent table |
| Labels | **Can contain almost anything, including `"""`, line breaks, and code**, as can table names |
| Label uniqueness | Exact duplicates are refused, ignoring case, but `Due Date` and `Due-Date` can both exist |
| Field IDs | Assigned in order, and address subfields use up IDs |
| `GET /tables/{id}` | Returned an error without its `appId`, so use `GET /tables?appId=` |
