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
| A row fails | The request still succeeds, other rows are written, and `metadata.lineErrors` maps the row number, starting from 1, to its messages |
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

## Unverified

Check these against a sandbox app before the feature that needs them ships.

| Question | Needed for |
|---|---|
| Format and units of `retry-after` on a real 429 (both seconds and dates are handled) | Transport |
| Can a query return fewer than `top` records before the end? The test table was too small to show it, so advance by `numRecords` | Paging |
| Which endpoints have different rate limits? | Transport |
| Request size limits, which decide whether the default batch of 1,000 rows is always safe | Writes |
| The HTTP status of a partly failed write, assumed to be 207 (any 2xx is handled the same) | Writes |
| Correct API host for EU realms (pass `base_url` if it differs) | Realm handling |
| Temporary token format | Temporary tokens |
