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

## Unverified

Check these against a sandbox app before the feature that needs them ships.

| Question | Needed for |
|---|---|
| Format and units of `retry-after` on a real 429 (both seconds and dates are handled) | Transport |
| Can a query return fewer than `top` records before the end? The test table was too small to show it, so advance by `numRecords` | Paging |
| How checkbox values should be written in a query (`F` sends `true` and `false`) | Query builder |
| Shape of the 207 upsert response and `lineErrors`; request size limits | Upsert |
| What happens when `mergeFieldId` doesn't match, including for Record ID#? | Upsert, MCP updates |
| Which endpoints have different rate limits? | Transport |
| Correct API host for EU realms (pass `base_url` if it differs) | Realm handling |
| Temporary token format | Temporary tokens |
