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
| Timestamps can also be compared with `AF` and `BF` using a date, such as `2027-03-01` | Live API, 2026-11-04 |
| With no `select`, only fields in the table's default set return, and Record ID# may not be one | Live API, 2026-11-02 |
| Timestamps return as `2026-10-21T11:49:56Z`, dates as `2026-10-18` | Live API, 2026-11-02 |
| A bad token, a nonexistent app, and an app the token can't reach all return 401 with the same message, `Access denied: User token is invalid`, so they can't be told apart | Live API, 2026-11-05 |
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
| `_curuser_`, on a `multiuser` field | **The token's owner**, with `EX`, `TV`, and `HAS`, ignoring case and surrounding spaces |
| `_curuser_`, on a `user` field | **The token's owner** with `TV`, ignoring case and surrounding spaces. Nothing with `EX` |
| Matching a `user` field | `TV` and `XTV` with an email or user ID, ignoring case, and with `''` for empty. **`EX` matched nothing**, including the field's real user, and so did `CT` and `HAS`. **`XEX` matched every record, including that user's.** `EX` and `XEX` with `''` do work. `F` sends `TV` and `XTV` for `==` and `!=` here |
| Matching a `multiuser` field | `EX`, `TV`, or `HAS` with an email or user ID, and `CT` with a name |
| Text on a date or timestamp field | Read as a date: `today`, `yesterday`, and `1 day ago` matched. Text it can't read, such as `garbage`, `last week`, or `now`, **matched nothing, with no error** |
| `today` and `yesterday` in a query, and `Today()` in a formula | Follow Quickbase's date rather than the client's, which can differ near midnight: at 00:55 EST on 2026-11-05, `Today()` returned `11-04-2026`. Live API, 2026-11-05 |
| `TRUE`, `NULL`, `today`, `*`, `%`, `_` on a text field | Literal |
| A number on a `percent` field | **Compared as whole percent**, though a percent reads and writes as a fraction: `{f.EX.'100'}` matched the 45 records at 100%, and `{f.EX.'1'}` matched none. `25`, `25.00`, and `25%` matched the records at 25%, and `0.25` matched none. `F` sends a number times 100. Live API, 2026-11-05 |
| A number on a `duration` field | **Matches nonsense, with no error.** On durations from 30 minutes to 20 hours, `GT 0.9999` matched every record with a value and `GT 1` matched none, and so did 8 hours as `8`, `480`, `28800`, or `28800000`. Live API, 2026-11-05 |
| Text on a `duration` field | Read as a duration: `6:00:00`, `6:00`, `6 hours`, `360 minutes`, `21600 seconds`, `0.25 days`, and `21600000 milliseconds` each matched the 7 records of 6 hours. **`N milliseconds` is exact**: `GT '71999999 milliseconds'` matched the one record of 20 hours. `ms` matched nothing. Negative values and `0 milliseconds` work, and `''` matches empty. `F` sends `N milliseconds`. Live API, 2026-11-05 |

## Writes

Tested on 2026-11-02 against temporary tables in a sandbox app.

| Behavior | Result |
|---|---|
| A row fails | The request returns 207, other rows are written, and `metadata.lineErrors` maps the row number, starting from 1, to its messages |
| `{"value": null}` | **Silently ignored**, leaving the old value |
| `{"value": ""}` | Clears text, date, timestamp, numeric, choice, and checkbox fields |
| Row with an existing Record ID# | Updates it, or reports it in `unchangedRecordIds` if nothing changed |
| Row with a missing Record ID#, with or without `mergeFieldId` | Row error, and no record is created |
| Row with an existing Record ID#, without `mergeFieldId` | Updates it, as it does with `mergeFieldId: 3`. Live API, 2026-11-05 |
| `mergeFieldId` of `"3"` or `3.0` | Accepted, as field 3. Live API, 2026-11-05 |
| A row's field key with padding, such as `"07"`, `" 7"`, or `"7 "` | **Accepted as field 7, and the value is written.** So check field IDs only in their usual form. Live API, 2026-11-05 |
| `keyFieldId` in `POST /tables/{id}` | 200, and **ignored**, so the key field stays 3. A table's key field can't be changed through the API. In the UI, select the field on the table's fields page and click **Set key**. Live API, 2026-11-05 |
| Custom key field: a row with an existing key value, without `mergeFieldId` | **Updates that record, and creates nothing.** A batch can do both, leaving `createdRecordIds` shorter than the rows. So `insert()` refuses these tables. Live API, 2026-11-05 |
| Custom key field: a row with a new key value, without `mergeFieldId` | Creates a record. Live API, 2026-11-05 |
| Custom key field: a row without the key field, without `mergeFieldId` | Row error, `Missing value for required field`, though it wasn't set as required. Live API, 2026-11-05 |
| Custom key field: a row with a Record ID#, without `mergeFieldId` | **500**, `You cannot include the record ID if it is not the key field`. Live API, 2026-11-05 |
| Custom key field: a row with a Record ID# and `mergeFieldId: 3` | Updates that record, and a Record ID# that doesn't exist is a row error, as on any table. Live API, 2026-11-05 |
| `mergeFieldId` on a unique field, value doesn't match | **Creates a record** |
| `mergeFieldId` on a non-unique field, or a row without the merge field | 400 for the whole request |
| Unknown field, formula field, built-in field | Row error |
| A batch where every row sets the same one field, and a row's value is empty | **That row is silently skipped, with no error**, so `createdRecordIds` no longer lines up with the rows. A batch of one row, or using two or more fields, creates it. `insert()` and `upsert()` refuse these rows |
| A value outside a field's choices | Accepted when `allowNewChoices` is on, which is the default for a field made through the API, otherwise a row error |
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
| `decimalPlaces: 0` on a numeric field | **Display only.** `1999.5` was stored and read back as `1999.5`. Whole numbers return as floats, such as `1999.0` |
| A foreign key's properties | `foreignKey: true` and `decimalPlaces: 0`. A lookup of a foreign key has `foreignKey: false` and `decimalPlaces: 0`. A plain numeric field has no `decimalPlaces` unless it was set |
| Whole numbers up to 9,007,199,254,740,991 in a numeric field | Read back exactly |

## Reports

Tested on 2026-11-02 against a temporary table in a sandbox app, with table, ask-the-user, summary,
and chart reports built in the UI. The API can't create reports.

| Behavior | Result |
|---|---|
| `GET /reports?tableId=` | A list of the same objects as `GET /reports/{id}?tableId=`: `id`, `name`, `type`, `description`, `query`, `properties`, `incomplete`, `usedCount`, and `usedLast` once used |
| Report IDs | Numeric strings, **unique only within a table**. A new table gets `1` (List All) and `2` (List Changes) |
| Deleted report IDs | Not reused. Checked by hand in the UI on 2026-11-04, so cached report details won't find a different report under an old ID |
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
| A sort with ties, 2,000 records on each of 3 values | The same, on 2026-11-04. 13 full runs of 6,000 records, with no `top` and page sizes from 37 to 6,000 in a shuffled table, all gave Record ID# order within each value. This is what was seen, not a promise from Quickbase |
| `top` omitted, in a 50,000-record table | Returned every record, and `metadata` has no `top` |
| Changing a saved report's type | The UI doesn't offer this |
| Running a report | **Adds 1 to `usedCount`.** A GET doesn't, a 400 for a bad `top` or `skip` doesn't, and records aren't modified. An earlier test saw a failed run add 1 too, for a reason not pinned down |
| Ask-the-user report | `incomplete: true`, and running it returns 400, `Incomplete reports not supported` |
| Summary and chart reports | **Same response shape as a table report, but `fields` IDs are column positions starting at 2.** The second column has ID 3, the Record ID# field in a table report, so nothing in the response tells them apart. Only `type` on the report does |
| Chart category values | Dates return as text, `01-01-2026`, not `yyyy-MM-dd` |
| Summary report column labels | Each total gets the field's label and its kind, so a sum and an average of one field are `Hours (tot)` and `Hours (avg)`. A chart's value column is `Hours (tot)` too. Live API, 2026-11-05 |
| `top` and `skip` on summary and chart reports | Page the rows as they do on a table report, and `totalRecords` counts every row. Live API, 2026-11-05 |
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
| `maxVersions` values | 1 to 1,000 were accepted, and 0 or a negative number returns 400. At 1, 2, and 5, only that many newest versions were kept. **A field set to 1 leaves `maxVersions` out of its properties** |
| `versionMode` | `keeplastversions` by default, which applies the limit. **`keepallversions` ignores `maxVersions`**: it kept all 150 uploads, and all 5 with `maxVersions: 2`. No cap was found in that range |
| Lowering `maxVersions` | Nothing is removed until the next upload, which then trims to the new limit. Raising it doesn't bring back removed versions |
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

The limit is the same elsewhere. On 2026-11-04, `POST /records`, `POST /records/query`, `DELETE /records`,
`POST /formula/run`, `POST /reports/{id}/run`, `POST /fields`, and `POST /tables` each got past it with
a body of exactly 41,943,040 bytes and returned 413 at one byte more. Only one realm has been tried.

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
| **Tables in another app** | **Not readable, and there's no error.** On 2026-11-04, with a token that could reach both apps, `GetRecord()`, `GetRecords()`, and `Size()` from another app returned 200 with an empty result, exactly as for a table ID that doesn't exist. The same formula run inside the table's own app returned the value, and so did a same-app read from the first app. A saved formula field behaved the same way. Quickbase's help describes queries as working on "unrelated tables in the same app". Only table IDs were tried, not aliases |
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
| Field types that took a formula | `text`, `numeric`, `date`, `checkbox`, `duration`, `timeofday`, `currency`, `percent`, `rating`, `url`, `email`, and `datetime`, and on 2026-11-04 `phone`, `user`, `multitext`, and `rich-text` |
| Field types the API refuses a formula on | `text-multi-line` and `multiuser`, with `[formula] property is not supported`. **The UI can make a `multiuser` formula**, such as `ToUserList(User())`, which reads as a list of users |
| **A timestamp formula** | **`timestamp` returns 400, `[formula] property is not supported`. `datetime` works, and the field then reports its type as `timestamp`** |
| `text-multi-line` | 400, `[formula] property is not supported` |
| Reading formula fields in a query | Full precision, unlike a formula run: `1/3` returns `0.3333333333333333`. A duration returns milliseconds, and a time of day `11:34:49.896` |
| **A blank number in a formula** | **Can read as 0, not empty.** With a numeric field made through the API left empty, and no child records, `If(IsNull([Hours]), null, [Hours] - Nz([Hours Logged]))` returned 0, so `IsNull([Hours])` was false. This is probably the field's "treat blank as zero" setting, which wasn't checked or turned off. Live API, 2026-11-05 |

## Rate limits

Tested on 2026-11-02 with short bursts from 12 threads against temporary tables.

| Behavior | Result |
|---|---|
| `retry-after` on a real 429 | **Whole seconds**, seen from `0` to `10`. After waiting that long, the next request succeeded |
| 450 calls from 12 threads, with the default 4 retries | Took about 30 seconds and hit 24 to 53 real 429s. **All 450 succeeded in most runs, but in 1 of 7, a call used up its retries and raised `RateLimitError`.** With retries off, about 300 of the 450 raised it |
| The same burst with `1.0.0rc3`, which waits before retrying on a `retry-after` of 0 | 10 runs on 2026-11-03: **all 450 succeeded every time**, with 24 to 42 real 429s, in 27 to 37 seconds. That fits an improvement over 1 failure in 7 runs, but a sample this small can't prove one |
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
downloads, and file deletes weren't tested with a temporary token. A token from a user with fewer
permissions was only tested on a delete: see [Code pages and dashboards](#code-pages-and-dashboards).

## Code pages and dashboards

Tested on 2026-11-03 in a sandbox app, in a logged-in browser, using a code page that runs Python in a
sandboxed frame. Dashboard settings were checked by hand, in the app's own screens.

| Behavior | Result |
|---|---|
| A code page's security policy | `default-src * 'unsafe-eval' 'unsafe-inline' data: filesystem: about: blob: ws: wss; worker-src 'none';`, so a page can't start a worker. Almost everything else is allowed, so any tighter limit has to come from the page itself |
| A code page in a dashboard's Web page widget | **Served from `realm.ui.quickbase.com`, and `GET /auth/temporary` fails there with no CORS header.** Reloading the page from the realm's own address, inside the widget, works |
| Saving a code page that contains `QB_USER_TOKEN="..."` | A warning that storing secrets in a browser page is insecure, even for a placeholder. It doesn't prevent saving, and moving the value into a variable didn't clear it |
| The dashboard rich text editor | Removes inline styles and collapses line breaks in `<pre>`. `<br>` and `&nbsp;` work |
| KPI widgets | Built from a gauge chart report. Tile colors come from the report's Low, Medium, and High legend colors |
| KPI widget settings | **Show target** hides the `TARGET` line, and **Custom title** replaces the report's name, so neither needs a change to the report |
| A gauge report saved with a goal of 0 | Still displays, and its settings page marks the goal "Must be greater than start" |
| Chart widget settings | Only **Report** and **Custom title**. Everything else is set on the report |
| A horizontal bar chart in a narrow widget | **Its category labels disappear, while the bars and axis remain.** There's no setting for this in the widget |
| Table report **Options** | **Show edit icon** and **Show view icon** remove the row icons, and **Hide row numbers** works on a table report in a dashboard, though the help only describes it for grid reports |
| A narrow browser window | The dashboard doesn't rearrange its widgets. It scrolls sideways, so a Web page widget keeps its width |
| Dashboard reports after a write through the API | **They don't update until the page is reloaded** |
| A delete with a table-scoped temporary token, from a role without delete rights | 403, `Insufficient permissions: Not enough rights to execute this request.` |
| The same delete with a filter that matches nothing, `{3.LT.0}` | Also 403, so it shows whether a role can delete before anything changes |

## Restricted users

Tested on 2026-11-02 against a temporary table in a sandbox app, with a second user's token. Its role
could only view that table, had no access to the app's other tables, and had access `None` on one text
field and one file field. See also [roles with write access](#roles-with-write-access) and
[record-level rules](#record-level-rules).

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
| Running a table report with a hidden column | **200, and the column is silently left out** |
| A file field's value, read with view access | Includes `creator`, with **the uploader's email address** |
| Downloading from a visible file field | 200 |
| Downloading from a hidden file field | 404, `No such field` |
| A formula using a hidden field, directly or through `GetRecords()` | 403, including when `GetRecords()` only filters on it. A visible field works |
| Insert, update, delete, upload, deleting a file version, creating a field | 403, `Insufficient permissions`, and nothing changed |
| A table the role has no access to | `GET /tables` leaves it out. Querying it, `GET /tables/{id}`, `GET /fields`, and a formula with it as `from` return 403. Tested on 2026-11-04 |
| **A formula reading a table the role has no access to** | **Empty, with no error.** `GetRecord()`, `GetRecords()`, and `Size()` return 200 with `""` from a table the role can view, while an admin's token gets the real value. A saved formula field shows each user their own result. Tested on 2026-11-04 with a role that had no access to the table or its fields |

## Roles with write access

Tested on 2026-11-04 in a real app, with a second user's token. Its role had custom field access on some
tables, and no delete rights. See also [record-level rules](#record-level-rules).

| Behavior | Result |
|---|---|
| Add off, and Modify on for the table | 207, with the row error `You don't have permission to add records to this table.` |
| Add and Modify both off for the table | **403, `Insufficient permissions`**, for inserts and updates, so the same refusal takes a different form |
| Modify off, with an upsert that matches an existing record | Row error, `You don't have permission to update records in this table.` An upsert that creates a record works |
| Inserting a duplicate into a unique field | Row error, `Adding non-unique value for field with ID "8".`, and no new record, with only add rights |
| A field set to View in the role | Readable and usable in `where`. Writing it gives the row error `Insufficient permissions to update data in a restricted field.` |
| **A row with an allowed field and a view-only field** | **The whole row is refused**, and the allowed field doesn't change |
| **A field created after a role's Custom Access was set** | **The role gets full access to it.** It could write the new field straight away, until it was set to View. Check a role after adding any field |
| `where` on lookup and foreign key fields | Works |
| Upsert with add and modify rights, including repeating it | Works |
| `DELETE /records` with no delete rights, with `{3.LT.0}` | 403 on every table |
| `POST /fields` and `POST /tables` | 403 |
| `GET /apps/{id}` and `GET /apps/{id}/roles` | **403**. `GET /tables` and `GET /fields` work |

`GET /apps/{id}/roles` with an administrator's token returns `id`, `name`, and `access`, including roles
you made. We didn't find any endpoint for reading or changing a role's permissions:
`/apps/{id}/roles/{id}` and `/apps/{id}/roles/{id}/permissions` return 404.

## Record-level rules

Tested on 2026-11-04 against 6,000 records split evenly across three categories, with a second user's
token. The role's View rule was `Category` is not `Gamma`, and its Modify rule was `Category` is
`Alpha`. It had Add but not Delete, and a hidden numeric field.

| Behavior | Result |
|---|---|
| Queries, counts, and reports | Only show records the View rule allows, and `totalRecords` counts only those, so nothing reveals how many others exist |
| `where` matching a hidden record, by ID or value | 200 with no records, not an error |
| `GetRecords()` and `GetRecord()` in a formula | Follow the rule. A count is of allowed records, and a hidden record reads as `""` |
| A formula with a hidden record as `rid` | **403, while a record that doesn't exist returns 404**, so this shows whether an ID exists |
| Updating a record the Modify rule doesn't allow, visible or hidden | Row error, `You don't have permission to update records in this table.` |
| Updating an ID that doesn't exist | Row error, `Can not find record by ID`, **which also shows whether an ID exists** |
| A request mixing allowed and refused records | 207. The allowed rows are written, and the others get row errors |
| **Changing an allowed record so the rules no longer match** | **Allowed, including moving it to where the role can't see it.** The rules check the record before the change, not after |
| **Inserting a record the role won't be able to see** | **Allowed.** The new ID comes back, but `fieldsToReturn` values don't, and the role can't read it afterward |
| Deleting, with no delete rights | 403, including a record the role just added |
| Writing to a hidden field | Row error, `Insufficient permissions to update data in a restricted field.` |

**Reports using a hidden field.** A report that sorts or filters on a field the role can't see isn't
available to that role, and nothing about the hidden field is revealed:

| Behavior | Result |
|---|---|
| `GET /reports` | **Leaves the report out**, so the SDK raises `UnknownReportError`, by name or ID |
| `POST /reports/{id}/run` | **400, `Invalid request: Internal error`.** A report ID that doesn't exist returns 404, so this shows that the report exists |
| A report that only shows the hidden field as a column | Listed, and runs without that column |
| `GET /reports/{id}` | 403 for every report we tried with this role, even ones it can run |

## Custom data rules

Rules are written in the table's Advanced settings, and the API can't create them. Tested on 2026-11-04 in a
real app. The help says they also cover imports, grid edit, and connected table refreshes, which weren't tested.

| Behavior | Result |
|---|---|
| A rule refusing an update or insert through the API | 207, with the row error `Table rule validation: <the rule's message>` |
| **An administrator** | **Is bound by the rule**, through both the API and a form |
| A pipeline's write | Is bound by the rule. The run fails with `Custom data rule failed`, Error Code 88 |
| Deleting a record | The help only mentions adding and editing, and this wasn't tested |
| **`IsNull()` on an empty numeric field with `blankIsZero: true`** | **False, because the field counts as 0.** A rule using it refused every record until `blankIsZero` was set to false, which the API accepts |

## Pipelines

Tested on 2026-11-04 in a real app, with the Quickbase channel's **Record Updated** trigger and **Update Record**
action. Pipelines can't be created or read through this API.

| Behavior | Result |
|---|---|
| An update through the API | Fires the trigger, and the action had run within 4 seconds |
| A field the action reads | Must be listed in the trigger's "Fields for subsequent steps" |
| The trigger's filter | `Status is Open` didn't fire on a change from Open to Draft, and did on Draft to Open |
| The record changing before the action runs | The action works on the record as it is then, so it can hit a custom data rule |

## Relationships

Tested on 2026-11-03 and 2026-11-04 in a real app, using `POST /tables/{child}/relationship/{id}`.

| Behavior | Result |
|---|---|
| `lookupFieldIds` | **The parent's field IDs, and it adds to the existing lookups.** Sending an existing lookup's ID, which is on the child, looked up whichever parent field has that number, and created a field nobody asked for |
| A new lookup's label | `<Foreign key's label> - <Field>`, such as `Buyer - Name` on a parent whose record name is `Client`, and it can be renamed |
| A second `POST /tables/{child}/relationship` to the same parent | **Makes another relationship**, even with the same foreign key label |
| `accumulationType` values | The API lists `AVG`, `SUM`, `MAX`, `MIN`, `STD-DEV`, `COUNT`, `COMBINED-TEXT`, `COMBINED-USER`, and `DISTINCT-COUNT`, in capitals |
| Summaries that worked | `SUM` of numeric and currency, `AVG` and `STD-DEV` of numeric, `MAX` and `MIN` of numeric and date, `DISTINCT-COUNT` of numeric and text, `COMBINED-TEXT` of text, and `COMBINED-USER` of user. Each kept the child's type, except that `AVG`, `STD-DEV`, and `DISTINCT-COUNT` are numeric, and the combined ones are `multitext` and `multiuser` |
| Summaries refused with 400 | `SUM` of checkbox, `MAX` of text, and `COUNT` of numeric, text, checkbox, and the record ID |
| **`COUNT` takes no field** | With no `summaryFid`, or `summaryFid: 0`, it counts the child records, as a `numeric` summary with `summaryTargetFieldId: 0`. With any field, including the record ID, it's 400, `That form of summarization/aggregation cannot be applied to this field`. Lowercase `count` is 400. Live API, 2026-11-05 |
| **A `COUNT` with no child records** | **0, not empty**, unlike the other summaries. Live API, 2026-11-05 |
| A lookup's properties | `lookupReferenceFieldId` is the foreign key on the child, and `lookupTargetFieldId` is the field on the parent |
| `summaryFields`, with `summaryFid`, `label`, and `accumulationType: "SUM"` | Creates the field on the parent. Works on a formula field of the child |
| A summary's properties | `summaryReferenceFieldId`, `summaryTargetFieldId`, and `summaryFunction` |
| **A summary with no child records** | **Empty, not 0**, except a `COUNT`. A formula using it is also empty unless it uses `Nz()`, which matters for something like a balance |
| Fields added to the parent | A `dblink` such as `Task records`, and a `url` formula such as `Add Task`, containing `a=API_GenAddRecordForm` |

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
| `dblink` | `""`, including when the relationship has child records | `""` |
| `vCardButton`, `ICalendarButton` | A relative link, such as `<table ID>?a=VCard&fid=14&rid=b`, on every record, even with no data to show | The link, or `""` as a lookup with no parent |
| `predecessor` | Record IDs as text, such as `1` or `1;2` | `""` |

| Behavior | Result |
|---|---|
| Formula fields | Return the same shapes as their type. A formula of `Null` matched its type on text, numeric, and checkbox fields |
| Lookup fields | Return exactly what the parent field returns, filled and empty, for every type `POST /fields` accepts except `file`, and for numeric, text, and checkbox formulas. With no parent record, they read as that type's empty value. Tested on 2026-11-04 |
| A lookup's reported type | The parent's type, except that a lookup of `text-multiple-choice` **reports `text`** |
| A lookup of a `file`, `dblink`, or `predecessor` field | 400, `The following field ids can not be lookup fields` |
| A lookup of a type only the UI can create | `vCardButton`, `ICalendarButton`, and a `multiuser` formula return what the parent returns, and report the parent's type. Tested on 2026-11-04 |
| Types only the UI can create | vCard, iCalendar, and Predecessor, which report as `vCardButton`, `ICalendarButton`, and `predecessor`. A Report link reports as `dblink`, and a relationship also creates one |
| Writing to `vCardButton`, `ICalendarButton`, or `dblink` | Row error, `Can not change the value of the read-only field` |
| Writing to `predecessor` | Works. It takes a record ID, as text or a number, or several in a list or a separated string, stored as written: `1,2` reads as `1, 2`. A missing ID or other text gives `Invalid entry in field`, and a record linking to itself gives a row error about links back to the first record. `""` clears it. `where` with `EX` matched a record holding that ID |
| Writable fields | Built-in fields 1 to 5 aren't, nor are formula, lookup, and summary fields, or `address` |
| An `address` field | Is read-only, with text subfields for its parts, which can be written. Creating one also creates its subfields, and sets `Country` to a default |
| `dblink` | Created by a relationship, on the parent table |
| Labels | **Can contain almost anything, including `"""`, line breaks, and code**, as can table names |
| Label uniqueness | Exact duplicates are refused, ignoring case, but `Due Date` and `Due-Date` can both exist |
| Table names | `POST /tables` and renaming with `POST /tables/{id}` refuse a name already in use with 400, `Table name exists`, ignoring case and surrounding spaces |
| Table names in the UI | Creating a table with a name already in the app, or renaming a table to one, is refused too. Tried by hand with the same name on 2026-11-04. Case and spacing variations weren't tried in the UI |
| Writing a timestamp | `2027-01-02T03:04:05+00:00` and three fractional digits are accepted. **Python's default of six, or one, is a row error**, `Incompatible value`. It reads back to the second |
| `properties` | An unknown key is refused with 400, `Extraneous key`. `fieldHelp` isn't a property, and neither are `choices` on a type without them |
| Settings outside `properties` | `POST /fields` and `POST /fields/{id}` take `fieldHelp`, `bold`, `noWrap`, `findEnabled`, and `appearsByDefault` beside `label`, and `GET /fields` returns them. `addToForms` is accepted on create and never returned. **`audited: true` was accepted and read back as false**, in an app without audit logging set up. `fieldHelp` inside `properties` is 400, `Extraneous key 'properties.fieldHelp' is not permitted`. Live API, 2026-11-05 |
| A field's response | Also lists `permissions`, each role's name, ID, and access to the field. `GET /fields` leaves them out |
| **Writing a `duration`** | **Milliseconds**, as it reads: `90000` reads back as `90000`, and `3600000` is an hour. Negative and 0 work. **A fraction is dropped with no error**, so `1.5` reads back as `1`. Text is parsed too: `"90000"`, `"1:30:00"`, and `"2 hours"` became `90000`, `5400000`, and `7200000`. Live API, 2026-11-05 |
| `POST /fields/{id}` with `required: true` | 400, `field contains null values`, if any record has it empty, which includes a new field on a table with records |
| `POST /fields/{id}` with `unique: true` | 400, `field contains non-unique values`, if it holds duplicates. A new, empty field on a table with records is allowed |
| A `phone` value | Reformatted: `5551234567` reads back as `(555) 123-4567` |
| A built-in field in `DELETE /fields` | Refused in `errors`, and the others in the list are deleted |
| Field IDs | Assigned in order, and address subfields use up IDs |
| `GET /tables/{id}` | Returned an error without its `appId`, so use `GET /tables?appId=` |

## Interface links

These are the current Quickbase interface's addresses, which the SDK's `*_url()` methods build. The interface isn't part of the API, so Quickbase
may change them without notice.

| Page | Path, after `https://<realm>` | Source |
|---|---|---|
| App home | `/nav/app/<app ID>/action/appoverview` | Browser, 2026-11-05 |
| App settings | `/nav/app/<app ID>/action/appsettingshome` | Browser, 2026-11-05 |
| Table home | `/nav/app/<app ID>/table/<table ID>/action/td` | Browser, 2026-11-05 |
| Report | `/nav/app/<app ID>/table/<table ID>/action/q?qid=<report ID>` | Browser, 2026-11-05 |
| Show a record | `/nav/app/<app ID>/table/<table ID>/action/dr?rid=<Record ID#>` | Browser, 2026-11-05 |
| Edit a record | `/nav/app/<app ID>/table/<table ID>/action/er?rid=<Record ID#>` | Browser, 2026-11-05 |
| Edit a field's settings | `/nav/app/<app ID>/table/<table ID>/action/mf?fid=<field ID>` | Browser, 2026-11-05 |
| Edit a report | `/nav/app/<app ID>/table/<table ID>/action/viewBuild?qid=<report ID>` | Browser, 2026-11-05 |
| Add a record | `/nav/app/<app ID>/table/<table ID>/action/nwr` | Browser, 2026-11-05 |
| A table's fields | `/nav/app/<app ID>/table/<table ID>/action/listfields` | Browser, 2026-11-05 |
| A table's relationships | `/nav/app/<app ID>/table/<table ID>/action/Relationships` | Browser, 2026-11-05 |
| A table's settings | `/nav/app/<app ID>/table/<table ID>/action/KeyProps` | Browser, 2026-11-05 |
| Edit a form | `/nav/app/<app ID>/table/<table ID>/action/DFormProps?dfid=<form ID>` | Browser, 2026-11-05 |

The interface's own links add `skip=0` to a report, `rl=...&page=1` to a record, `chain=1` to a
field, `originalQid=...` to a new record or a report's settings, and `page=1` to a new record. These
look like the interface's state, so the SDK leaves them out. The API can't list forms, so a form's ID
comes from its address in the interface.

A link to the records that match a filter isn't built, because no form has been confirmed. The
classic interface took `/db/<table ID>?a=q&query=<filter>`, and it isn't known whether that still
works.
