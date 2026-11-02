# pyquickbase

An unofficial, typed Python SDK for the [Quickbase](https://www.quickbase.com/) API, with matching
sync and async clients.

> **Status: early alpha.** The connection layer is complete, but the higher-level API is still
> being built, so anything may change before 1.0. This project is not affiliated with or endorsed
> by Quickbase, Inc.

## What works today

- Sync and async clients with the same API, and support for asyncio and Trio
- Automatic retries for rate limits, honoring `Retry-After`, and for connection failures
- Specific exceptions that include Quickbase's error message and request ID
- Tokens kept out of `repr`s, exceptions, and logs, and never sent to other hosts or over plain HTTP
- `qb.request()` for calling any Quickbase endpoint directly

## Coming next

Field labels instead of numeric IDs, a Python query builder, automatic paging, typed records, and
write support with per-record error reporting.

## Usage

The package isn't on PyPI yet. To try it, install from a clone with `uv sync`.

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tables = qb.request("GET", "/tables", params={"appId": "bq1234567"})
```

```python
from pyquickbase import AsyncQuickbase

async with AsyncQuickbase(realm="acme", token="your_user_token") as qb:
    tables = await qb.request("GET", "/tables", params={"appId": "bq1234567"})
```

`Quickbase.from_env()` reads `QB_REALM` and `QB_USER_TOKEN` from the environment instead.

## Handling errors

Every exception derives from `QuickbaseError`.

```python
from pyquickbase import NotFoundError, QuickbaseError, RateLimitError

try:
    app = qb.request("GET", "/apps/bq1234567")
except NotFoundError:
    app = None
except RateLimitError as error:
    print(f"Still rate limited. Try again in {error.retry_after} seconds.")
except QuickbaseError as error:
    print(error)
```

Only rate limits and connection failures are retried. A timed-out write isn't retried because
Quickbase may already have applied it, so check the data before trying again.

## Customizing

Adjust retries with `RetryPolicy`. For proxies, custom TLS, logging hooks, or custom transports,
pass your own [httpx](https://www.python-httpx.org/) client, which you remain responsible for
closing.

```python
import httpx
from pyquickbase import Quickbase, RetryPolicy

http_client = httpx.Client(proxy="http://proxy.internal:8080")
qb = Quickbase(
    realm="acme",
    token="your_user_token",
    retry=RetryPolicy(max_retries=2),
    user_agent="my-app/1.0",
    http_client=http_client,
)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
