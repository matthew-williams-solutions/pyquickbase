# Configuring clients

`Quickbase` and `AsyncQuickbase` take the same options. Use them as context managers so that
connections get closed.

## Sync or async

The clients have the same methods, and the sync client is a true sync client rather than a wrapper
around asyncio. That means it works in notebooks, in threads, and inside a running event loop.
One sync client can be shared across threads. The async client works with both asyncio and Trio.

## Limiting what a client can do

Pass `read_only=True` to guarantee a client can't change data. It only sends GET requests, record
queries, report runs, and formula runs, including through `request()`, and raises `ReadOnlyError`
for anything else without sending it, though a table method may read the table's metadata first.
Running a report adds to its usage count in Quickbase, but changes no records.

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token", read_only=True) as qb:
    print(qb.app("bq1234567").tables())
```

To allow specific writes and nothing else, pass `allowed_writes`. Paths must match exactly.

```python
qb = Quickbase(realm="acme", token="your_user_token", allowed_writes={("POST", "/records")})
```

These protect against bugs in your code. Also give your token access to as little as possible, so
that Quickbase enforces the limit too.

## Retries

```python
from pyquickbase import Quickbase, RetryPolicy

policy = RetryPolicy(max_retries=2, base_delay=0.5, max_delay=10, max_retry_after=30)
qb = Quickbase(realm="acme", token="your_user_token", retry=policy)
```

`max_retry_after` is the longest `Retry-After` the client will wait for. Past that, it raises
`RateLimitError`. A `Retry-After` of 0 uses the backoff from `base_delay` instead, because Quickbase
can send it while still limiting. Use `max_retries=0` to disable retrying.

## Using another API host

`base_url` sets the API root, which is `https://api.quickbase.com/v1` by default. The right host for EU
realms hasn't been confirmed, so pass `base_url` if yours differs. It must use HTTPS, except for
loopback addresses.

## Proxies, TLS, and logging

Pass your own [httpx](https://www.python-httpx.org/) client for proxies, custom TLS, event hooks, or
custom transports. You remain responsible for closing it.

```python
import httpx
from pyquickbase import Quickbase

http_client = httpx.Client(proxy="http://proxy.internal:8080")
qb = Quickbase(
    realm="acme",
    token="your_user_token",
    user_agent="my-app/1.0",
    http_client=http_client,
)
```

Tokens are kept out of `repr`s, exceptions, and logs, and the client refuses to send them over plain
HTTP, except to your own machine.

## Calling any endpoint

For endpoints without a dedicated method, `request()` returns the decoded JSON body.

```python
reports = qb.request("GET", "/reports", params={"tableId": "bq7654321"})
```
