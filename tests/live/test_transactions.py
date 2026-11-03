"""What a payment integration needs: exact amounts, repeated events, and writes that may land."""

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest

from pyquickbase import F, Quickbase, RequestTimeoutError, WriteError


@pytest.fixture
def ledger(scratch: Any) -> Any:
    return scratch.table(
        ("Event ID", "text", {"unique": True}),
        ("Status", "text"),
        ("Amount Cents", "numeric"),
        ("Amount", "currency"),
    )


@pytest.mark.parametrize(
    "amount",
    ["0.01", "0.07", "19.99", "1234.56", "1000000.01", "99999999.99", "0.10", "-5.25"],
)
def test_amounts_round_trip_to_the_cent(ledger: Any, amount: str) -> None:
    ledger.insert([{"Event ID": "e", "Amount": Decimal(amount)}])
    record = next(iter(ledger.query(select=["Amount"])))
    assert Decimal(str(record["Amount"])) == Decimal(amount)


@pytest.mark.parametrize("cents", [1, 1999, 123456789, 900719925474099, 9007199254740991])
def test_whole_cents_stay_exact(ledger: Any, cents: int) -> None:
    ledger.insert([{"Event ID": "e", "Amount Cents": cents}])
    value = next(iter(ledger.query(select=["Amount Cents"])))["Amount Cents"]
    assert int(value) == cents


def test_a_repeated_event_updates_rather_than_duplicates(ledger: Any) -> None:
    event = [{"Event ID": "evt_1", "Status": "paid", "Amount Cents": 1999}]
    assert len(ledger.upsert(event, merge_on="Event ID").created_ids) == 1
    again = ledger.upsert(event, merge_on="Event ID")
    assert (again.created_ids, again.updated_ids) == ((), ())
    assert len(again.unchanged_ids) == 1
    assert ledger.count() == 1


def test_insert_refuses_a_repeated_event(ledger: Any) -> None:
    event = [{"Event ID": "evt_1", "Status": "paid"}]
    ledger.insert(event)
    with pytest.raises(WriteError) as caught:
        ledger.insert(event)
    assert len(caught.value.result.errors) == 1
    assert ledger.count() == 1


def test_simultaneous_repeats_of_an_event_make_one_record(scratch: Any, ledger: Any) -> None:
    table_id = ledger.info().id
    barrier = threading.Barrier(8)

    def deliver(attempt: int) -> str:
        with Quickbase(realm=scratch.realm, token=scratch.token) as qb:
            table = qb.app(scratch.app_id).table(table_id)
            table.fields()
            barrier.wait()
            try:
                table.upsert(
                    [{"Event ID": "evt_race", "Status": f"attempt {attempt}"}], merge_on="Event ID"
                )
            except WriteError as exc:
                return f"WriteError: {exc}"
            return "ok"

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(deliver, range(8)))
    assert results == ["ok"] * 8
    assert ledger.count(F("Event ID") == "evt_race") == 1


def test_simultaneous_different_events_all_land(scratch: Any, ledger: Any) -> None:
    table_id = ledger.info().id

    def deliver(attempt: int) -> None:
        with Quickbase(realm=scratch.realm, token=scratch.token) as qb:
            qb.app(scratch.app_id).table(table_id).upsert(
                [{"Event ID": f"evt_{attempt}", "Amount Cents": attempt}], merge_on="Event ID"
            )

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(deliver, range(40)))
    assert ledger.count() == 40


def test_a_timed_out_write_can_be_retried_safely_with_upsert(scratch: Any, ledger: Any) -> None:
    table_id = ledger.info().id
    event = [{"Event ID": "evt_timeout", "Status": "paid"}]
    with Quickbase(realm=scratch.realm, token=scratch.token, timeout=0.001) as qb:
        table = qb.app(scratch.app_id).table(table_id)
        # Metadata lookups can time out too, so give it its cache from a normal client first.
        qb._schema.set(
            f"tables:{scratch.app_id}", tuple(scratch.client.app(scratch.app_id).tables())
        )
        qb._schema.set(f"fields:{table_id}", tuple(ledger.fields()))
        with pytest.raises(RequestTimeoutError):
            table.upsert(event, merge_on="Event ID")
    ledger.upsert(event, merge_on="Event ID")
    assert ledger.count(F("Event ID") == "evt_timeout") == 1
