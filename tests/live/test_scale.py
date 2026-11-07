"""Large amounts of data and real rate limits. Slow, so these need QB_LIVE_SCALE=1 as well."""

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest

from pyquickbase import (
    APIError,
    F,
    FileUpload,
    Quickbase,
    RateLimitError,
    RetryPolicy,
    WriteError,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("QB_LIVE_SCALE") != "1", reason="Set QB_LIVE_SCALE=1 to run these."
)
TOTAL = 50_000
LARGEST_FILE = 31_457_208


def report(message: str) -> None:
    # Findings that aren't pass or fail. Run with -s to see them.
    os.write(1, f"\n  {message}\n".encode())


@pytest.fixture(scope="module")
def big(scratch: Any) -> Any:
    table = scratch.table(("Title", "text"), ("Group", "text"), ("Amount", "numeric"))
    started = time.monotonic()
    result = table.insert(
        {"Title": f"Row {n}", "Group": "ABC"[n % 3], "Amount": n} for n in range(TOTAL)
    )
    assert len(result.created_ids) == TOTAL
    assert len(set(result.created_ids)) == TOTAL
    report(f"inserted {TOTAL} rows in {time.monotonic() - started:.0f}s")
    return table


def test_every_record_comes_back_exactly_once(big: Any) -> None:
    started = time.monotonic()
    seen = [record.id for record in big.query(select=["Title"])]
    report(f"read {len(seen)} rows in {time.monotonic() - started:.0f}s")
    assert len(seen) == TOTAL
    assert len(set(seen)) == TOTAL
    assert seen == sorted(seen)
    assert big.count() == TOTAL


@pytest.mark.parametrize("page_size", [7000, 999])
def test_sorting_with_ties_is_stable_across_pages(big: Any, page_size: int) -> None:
    rows = [
        (r["Group"], r.id)
        for r in big.query(select=["Group"], order_by="Group", page_size=page_size)
    ]
    assert len(rows) == TOTAL
    assert len({record_id for _, record_id in rows}) == TOTAL
    assert rows == sorted(rows)


def test_paging_by_hand_and_query_filters(big: Any) -> None:
    where = F("Amount") >= TOTAL - 2500
    assert big.count(where) == 2500
    skip, seen = 0, 0
    while True:
        page = big.query_page(select=["Amount"], where=where, limit=1000, skip=skip)
        seen += len(page.records)
        if not page.has_more:
            break
        skip = page.next_skip
    assert seen == 2500


def test_a_report_of_a_large_table(big: Any, scratch: Any) -> None:
    rows = [record["Title"] for record in big.report("List All").run()]
    assert len(rows) == TOTAL
    assert len(set(rows)) == TOTAL
    table_id = big.info().id
    whole = scratch.client.request("POST", "/reports/1/run", params={"tableId": table_id})
    report(
        f"report run without top: returned {len(whole['data'])} of "
        f"{whole['metadata']['totalRecords']}, metadata {sorted(whole['metadata'])}"
    )
    assert whole["metadata"]["totalRecords"] == TOTAL


def test_changes_while_paging(big: Any, scratch: Any) -> None:
    """Paging uses offsets, so a delete behind the reader skips records. This shows how many."""
    table = scratch.table(("N", "numeric"))
    table.insert({"N": n} for n in range(600))
    seen: list[int] = []
    for index, record in enumerate(table.query(select=["N"], page_size=100)):
        seen.append(record["N"])
        if index == 150:
            table.delete(F("N") < 50)
    report(f"deleting 50 earlier rows while paging: read {len(seen)}, unique {len(set(seen))}")
    assert len(set(seen)) == len(seen)
    assert len(seen) < 600


def test_wide_rows_are_split_and_all_come_back(scratch: Any) -> None:
    table = scratch.table(("Title", "text"), ("Note", "text-multi-line"))
    rows = 3000
    note = "x" * 30_000
    requests: list[int] = []

    def log(request: httpx.Request) -> None:
        if request.url.path.endswith("/records"):
            requests.append(len(request.content))

    with httpx.Client(timeout=180, event_hooks={"request": [log]}) as http:
        with Quickbase(realm=scratch.realm, token=scratch.token, http_client=http) as qb:
            live = qb.app(scratch.app_id).table(table.info().id)
            result = live.insert({"Title": f"T{n}", "Note": note} for n in range(rows))
    report(f"{rows} wide rows took {len(requests)} write requests, largest {max(requests)} bytes")
    assert len(result.created_ids) == rows
    assert max(requests) <= 40 * 1024 * 1024
    assert len(requests) > 1

    sizes: list[int] = []
    skip = 0
    while True:
        page = table.query_page(select=["Title", "Note"], limit=rows, skip=skip)
        sizes.append(len(page.records))
        assert page.records or not page.has_more
        if not page.has_more:
            break
        skip = page.next_skip
    report(f"asked for top={rows} of wide rows, and pages held {sizes}")
    found = [record.id for record in table.query(select=["Title", "Note"], page_size=rows)]
    assert len(found) == rows
    assert len(set(found)) == rows
    table.delete("{3.GT.'0'}")


def test_large_files(scratch: Any) -> None:
    table = scratch.table(("Title", "text"), ("File", "file"), ("Second", "file"))
    data = os.urandom(LARGEST_FILE)
    with Quickbase(realm=scratch.realm, token=scratch.token, timeout=300) as qb:
        live = qb.app(scratch.app_id).table(table.info().id)
        made = live.insert([{"Title": "big", "File": FileUpload("big.bin", data)}])
        record_id = made.created_ids[0]
        assert live.download_file(record_id, "File").data == data
        with pytest.raises((APIError, WriteError)) as caught:
            live.update(
                [{"Record ID#": record_id, "File": FileUpload("big.bin", data + b"x" * 1_000_000)}]
            )
        report(f"a file over the limit raised {type(caught.value).__name__}")
        assert getattr(caught.value, "status_code", 413) == 413
        # Two files that only fit in separate requests.
        half = os.urandom(25_000_000)
        rows = [
            {"Title": "one", "File": FileUpload("one.bin", half)},
            {"Title": "two", "File": FileUpload("two.bin", half)},
        ]
        split = live.insert(rows)
        assert len(split.created_ids) == 2
        assert live.download_file(split.created_ids[1], "File").data == half


def test_a_real_rate_limit_is_retried(scratch: Any) -> None:
    """A long burst can use up a call's retries, which is documented, but almost all should land."""
    table = scratch.table(("Title", "text"))
    table_id = table.info().id
    statuses: Counter[int] = Counter()
    waits: list[str] = []

    def log(response: httpx.Response) -> None:
        statuses[response.status_code] += 1
        if response.status_code == 429:
            waits.append(response.headers.get("retry-after", ""))

    started = time.monotonic()
    with httpx.Client(timeout=60, event_hooks={"response": [log]}) as http:
        with Quickbase(realm=scratch.realm, token=scratch.token, http_client=http) as qb:
            live = qb.app(scratch.app_id).table(table_id)
            live.fields()

            def one(_: int) -> str:
                try:
                    assert live.count() == 0
                except RateLimitError:
                    return "rate limited"
                return "ok"

            with ThreadPoolExecutor(12) as pool:
                results = Counter(pool.map(one, range(450)))
    report(
        f"450 calls from 12 threads took {time.monotonic() - started:.0f}s, results "
        f"{dict(results)}, statuses {dict(statuses)}, retry-after {sorted(set(waits))}"
    )
    assert statuses[429] > 0, "The burst didn't reach the rate limit, so this proved nothing."
    assert results["ok"] + results["rate limited"] == 450
    assert results["ok"] >= 400
    time.sleep(11)


def test_rate_limit_error_when_retries_are_off(scratch: Any) -> None:
    table = scratch.table(("Title", "text"))
    table_id = table.info().id
    policy = RetryPolicy(max_retries=0)
    errors: list[RateLimitError] = []
    with Quickbase(realm=scratch.realm, token=scratch.token, retry=policy) as qb:
        live = qb.app(scratch.app_id).table(table_id)
        live.fields()

        def one(_: int) -> None:
            try:
                live.count()
            except RateLimitError as exc:
                errors.append(exc)

        with ThreadPoolExecutor(12) as pool:
            list(pool.map(one, range(450)))
    assert errors, "The burst didn't reach the rate limit, so this proved nothing."
    assert errors[0].status_code == 429
    assert errors[0].retry_after is not None
    assert "Quota" in str(errors[0])
    report(f"{len(errors)} rate limit errors, retry_after {errors[0].retry_after}")
    time.sleep(errors[0].retry_after + 1)


def test_deleting_a_large_table_in_one_call(big: Any) -> None:
    started = time.monotonic()
    assert big.delete("{3.GT.'0'}") == TOTAL
    report(f"deleted {TOTAL} rows in {time.monotonic() - started:.0f}s")
    assert big.count() == 0
