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

A bad realm or app can return 400 rather than 401 or 404, even with an invalid token, so don't
assume a 400 means the request body was wrong.

## Unverified

Check these against a sandbox app before the feature that needs them ships.

| Question | Needed for |
|---|---|
| Format and units of `retry-after` on a real 429 (both seconds and dates are handled) | Transport |
| Can a query return fewer than `top` records before the end? What does `metadata` contain? | Paging |
| How do you escape a single quote in a query value? | Query builder |
| Do malformed `where` clauses really return zero records rather than an error? | Query validator |
| Shape of the 207 upsert response and `lineErrors`; request size limits | Upsert |
| What happens when `mergeFieldId` doesn't match, including for Record ID#? | Upsert, MCP updates |
| Which endpoints have different rate limits? | Transport |
| Correct API host for EU realms (pass `base_url` if it differs) | Realm handling |
| Temporary token format | Temporary tokens |
