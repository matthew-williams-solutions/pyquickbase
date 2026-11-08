"""Check what the reading and writing guides say. A failure means the docs are wrong."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from pyquickbase import F, Policy, Quickbase
from pyquickbase.errors import (
    BadRequestError,
    InvalidValueError,
    PolicyRefusedError,
    QueryError,
    UnknownFieldError,
    UnknownTableError,
    WriteError,
)

CHOICES = {"choices": ["Open", "Done"], "properties": {"allowNewChoices": False}}


def make_table(scratch: Any) -> Any:
    return scratch.table(
        ("Title", "text"),
        ("Status", "text-multiple-choice", CHOICES),
        ("Code", "text", {"unique": True}),
        ("Plain", "text"),
        ("Amount", "currency"),
        ("Due", "date"),
        ("Seen", "datetime"),
        ("Doubled", "numeric", {"formula": "[Amount] * 2"}),
    )


def test_reading_mistakes_are_refused_before_querying(scratch: Any) -> None:
    table = make_table(scratch)
    app = scratch.client.app(scratch.app_id)
    with pytest.raises(UnknownTableError):
        app.table("No Such Table").count()
    with pytest.raises(UnknownFieldError):
        table.query_page(select=["Nope"])
    with pytest.raises(UnknownFieldError):
        table.query_page(order_by="Nope")
    with pytest.raises(UnknownFieldError):
        table.query_page(where=F("Nope") == 1)
    with pytest.raises(QueryError):
        table.query_page(where="{6.ex.'x'}")
    with pytest.raises(QueryError):
        table.query_page(limit=0)
    with pytest.raises(QueryError):
        table.query_page(skip=-1)
    with pytest.raises(QueryError):
        table.query(page_size=0)
    with pytest.raises(QueryError):
        table.query(max_total=0)
    with pytest.raises(QueryError):
        table.query_page(order_by=1.5)
    with pytest.raises(InvalidValueError):
        table.get(0)


def test_reading_values_and_pages(scratch: Any) -> None:
    table = make_table(scratch)
    table.insert([{"Title": f"Row {n}", "Plain": "x"} for n in range(1, 6)])

    assert table.first(where=F("Title") == "Nowhere") is None
    assert table.get(999999) is None
    assert table.get(2, select=["Title"])["Title"] == "Row 2"

    record = table.first(select=["Title"])
    assert record["Title"] == "Row 1"
    assert record[6] == "Row 1"
    assert dict(record)["Title"] == "Row 1"
    assert 3 in record
    with pytest.raises(KeyError):
        record["Plain"]  # Not selected.

    # Without select, a built-in field that isn't a default isn't returned.
    with pytest.raises(KeyError):
        table.first()["Record Owner"]
    assert table.first(select=["Record Owner"])["Record Owner"].id

    seen: list[str] = []
    skip = 0
    while True:
        page = table.query_page(select=["Title"], limit=2, skip=skip)
        assert page.total == 5
        seen += [item["Title"] for item in page.records]
        if not page.has_more:
            break
        skip = page.next_skip
    assert seen == [f"Row {n}" for n in range(1, 6)]
    assert page.next_skip == 5

    # Running past the end isn't an error.
    beyond = table.query_page(limit=2, skip=50)
    assert (len(beyond.records), beyond.total, beyond.has_more) == (0, 5, False)

    newest = list(table.query(select=["Title"], order_by="-Title", max_total=2))
    assert [item["Title"] for item in newest] == ["Row 5", "Row 4"]
    assert table.count(F("Title") == "Row 1") == 1


def test_writing_results_and_values(scratch: Any) -> None:
    table = make_table(scratch)
    made = table.insert(
        [
            {
                "Title": "One",
                "Code": "A",
                "Amount": Decimal("19.99"),
                "Due": date(2027, 1, 15),
                "Seen": datetime(2027, 1, 15, 8, 30, tzinfo=UTC),
                "Plain": "keep",
            }
        ]
    )
    record_id = made.created_ids[0]
    select = ["Title", "Amount", "Due", "Seen", "Plain"]
    row = table.get(record_id, select=select)
    assert row["Amount"] == 19.99
    assert row["Due"] == date(2027, 1, 15)
    assert row["Seen"] == datetime(2027, 1, 15, 8, 30, tzinfo=UTC)

    changed = table.update([{"Record ID#": record_id, "Title": "Two", "Due": None}])
    assert changed.updated_ids == (record_id,)
    row = table.get(record_id, select=select)
    assert (row["Title"], row["Due"], row["Plain"]) == ("Two", None, "keep")

    repeat = table.update([{"Record ID#": record_id, "Title": "Two"}])
    assert (repeat.updated_ids, repeat.unchanged_ids) == ((), (record_id,))

    updated = table.upsert([{"Code": "A", "Title": "Three"}], merge_on="Code")
    assert (updated.created_ids, updated.updated_ids) == ((), (record_id,))
    created = table.upsert([{"Code": "B", "Title": "New"}], merge_on="Code")
    assert len(created.created_ids) == 1
    same = table.upsert([{"Code": "B", "Title": "New"}], merge_on="Code")
    assert (same.created_ids, same.updated_ids) == ((), ())
    assert same.unchanged_ids == created.created_ids

    assert table.delete(F("Code") == "Nowhere") == 0
    assert table.delete(F("Code") == "B") == 1

    assert table.insert([]).created_ids == ()


def test_rows_refused_before_sending(scratch: Any) -> None:
    table = make_table(scratch)
    before = table.count()
    bad_calls = [
        lambda: table.insert([{"Record ID#": 1, "Title": "x"}]),
        lambda: table.update([{"Title": "x"}]),
        lambda: table.upsert([{"Title": "x"}], merge_on="Code"),
        lambda: table.insert([{"Title": None}]),
        lambda: table.insert([{}]),
        lambda: table.insert([{"Seen": datetime(2027, 1, 1)}]),
        lambda: table.insert([{"Title": object()}]),
    ]
    for call in bad_calls:
        with pytest.raises(InvalidValueError):
            call()
    with pytest.raises(UnknownFieldError):
        table.insert([{"Nope": "x"}])
    with pytest.raises(QueryError):
        table.insert([{"Title": "x"}], batch_size=0)
    with pytest.raises(QueryError):
        table.delete("{6.ex.'x'}")
    with pytest.raises(QueryError):
        table.delete(None)
    assert table.count() == before


def test_rows_refused_by_quickbase(scratch: Any) -> None:
    table = make_table(scratch)
    table.insert([{"Title": "First", "Code": "A"}])

    with pytest.raises(WriteError) as caught:
        table.insert(
            [
                {"Title": "Good"},
                {"Code": "A"},
                {"Status": "Nonsense"},
                {"Doubled": 3},
                {"Record Owner": "x"},
            ]
        )
    result = caught.value.result
    assert len(result.created_ids) == 1
    by_index = {item.index: item for item in result.errors}
    assert by_index[1].reason == "duplicate"
    assert by_index[1].field_id == 8
    assert by_index[2].reason == "invalid_choice"
    assert by_index[2].field_id == 7
    assert by_index[3].reason == "other"
    assert by_index[4].reason == "other"

    with pytest.raises(WriteError):
        table.update([{"Record ID#": 999999, "Title": "x"}])
    with pytest.raises(BadRequestError):
        table.upsert([{"Plain": "x", "Title": "y"}], merge_on="Plain")


def test_new_choices_are_allowed_unless_turned_off(scratch: Any) -> None:
    table = scratch.table(("Open Choice", "text-multiple-choice", {"choices": ["A"]}))
    assert next(f for f in table.fields() if f.label == "Open Choice").allow_new_choices
    made = table.insert([{"Open Choice": "Not listed"}]).created_ids[0]
    assert table.get(made, select=["Open Choice"])["Open Choice"] == "Not listed"
    updated = table.update_field("Open Choice", properties={"allowNewChoices": False})
    assert not updated.allow_new_choices
    with pytest.raises(WriteError) as caught:
        table.insert([{"Open Choice": "Also not listed"}])
    assert caught.value.result.errors[0].reason == "invalid_choice"


def test_a_read_only_client_refuses_writes(scratch: Any) -> None:
    make_table(scratch)
    with Quickbase(realm=scratch.realm, token=scratch.token, policy=Policy.read_only()) as qb:
        table = qb.app(scratch.app_id).table(scratch.created[-1].info().id)
        assert table.count() == 0
        with pytest.raises(PolicyRefusedError):
            table.insert([{"Title": "x"}])
        with pytest.raises(PolicyRefusedError):
            table.delete(F("Title") == "x")


def test_the_current_user_and_how_user_fields_match(scratch: Any) -> None:
    table = scratch.table(("Who", "user"), ("Many", "multiuser"), ("Note", "text"))
    table.insert([{"Note": "_curuser_"}])
    me = next(iter(table.query(select=["Record Owner"])))["Record Owner"]
    mine = table.insert([{"Who": me, "Many": [me]}]).created_ids[0]

    def found(where: Any) -> list[int]:
        return [record.id for record in table.query(where=where, select=[3])]

    # What Quickbase does with the operators that `==` and `!=` would otherwise send.
    who_id = table.resolve_field_ids(["Who"])[0]
    assert found(f"{{{who_id}.EX.'{me.email}'}}") == []
    assert found(f"{{{who_id}.XEX.'{me.email}'}}") == [1, mine]

    # What the docs tell you to use.
    assert found(F("Who") == me.email) == [mine]
    assert found(F("Who") == me.id) == [mine]
    assert found(F("Who") == me) == [mine]
    assert found(F("Who") == me.email.upper()) == [mine]
    assert found(F("Who") == "nobody@example.com") == []
    assert found(F("Who") != me.email) == [1]
    assert found(F("Who") != "nobody@example.com") == [1, mine]
    assert found(F("Who") == None) == [1]  # noqa: E711
    assert found(F("Who") != None) == [mine]  # noqa: E711
    assert found(F("Who").is_in([me.email, "nobody@example.com"])) == [mine]
    assert found(F("Many") == me.email) == [mine]
    assert found(F("Note") == "_curuser_") == [1]

    # Why F refuses this, and that a query string still means it.
    many, who = table.resolve_field_ids(["Many", "Who"])
    assert found(f"{{{many}.EX.'_curuser_'}}") == [mine]
    assert found(f"{{{many}.EX.' _CURUSER_ '}}") == [mine]
    assert found(f"{{{who}.TV.'_curuser_'}}") == [mine]
    for where in (F("Many") == "_curuser_", F("Who").op("TV", " _CURUSER_ ")):
        with pytest.raises(QueryError, match="_curuser_"):
            found(where)


def test_relative_dates_and_unreadable_dates(scratch: Any) -> None:
    table = scratch.table(("Due", "date"))
    # Quickbase's "today" follows the app's time zone, which can differ from this machine's.
    today = datetime.strptime(table.run_formula("Today()").value, "%m-%d-%Y").date()
    recent, old = table.insert(
        [{"Due": today - timedelta(days=1)}, {"Due": date(2000, 1, 1)}]
    ).created_ids

    def found(where: Any) -> list[int]:
        return [record.id for record in table.query(where=where, select=[3])]

    assert found(F("Due") == "yesterday") == [recent]
    assert found(F("Due") == "1 day ago") == [recent]
    assert found(F("Due") < "today") == [recent, old]
    assert found(F("Due") == "not a date") == []


def test_datetimes_with_fractions_of_a_second(scratch: Any) -> None:
    table = scratch.table(("Seen", "datetime"))
    moment = datetime(2027, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    for value in (moment, moment.replace(microsecond=0), datetime.now(UTC)):
        record_id = table.insert([{"Seen": value}]).created_ids[0]
        read = table.get(record_id, select=["Seen"])["Seen"]
        assert read == value.replace(microsecond=0)  # It reads back to the second.
