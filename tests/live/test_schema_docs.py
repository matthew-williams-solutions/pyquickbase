"""Check what the schema guide and field types page say. A failure means the docs are wrong."""

import datetime as dt
import secrets
from typing import Any

import pytest

from pyquickbase import FileUpload
from pyquickbase.errors import (
    BadRequestError,
    InvalidValueError,
    SchemaMismatchError,
    UnknownFieldError,
    WriteError,
)


def read(table: Any, record_id: int, label: str) -> Any:
    return next(iter(table.query(where=f"{{3.EX.{record_id}}}", select=[label])))[label]


def test_field_types_read_back_as_documented(scratch: Any) -> None:
    table = scratch.table(("Name", "text"))
    table.insert([{"Name": "first"}])
    owner = next(iter(table.query(select=["Record Owner"])))["Record Owner"]
    moment = dt.datetime(2026, 3, 4, 5, 6, 7, tzinfo=dt.UTC)
    # Type, what to write, what comes back, what an empty one reads as, and any options.
    cases: list[tuple[str, Any, Any, Any, dict[str, Any]]] = [
        ("text", "hello", "hello", "", {}),
        ("text-multiple-choice", "a", "a", "", {"choices": ["a", "b"]}),
        ("text-multi-line", "one\ntwo", "one\ntwo", "", {}),
        ("rich-text", "<b>x</b>", "<b>x</b>", "", {}),
        ("numeric", 1.5, 1.5, None, {}),
        ("currency", 2.5, 2.5, None, {}),
        ("rating", 3, 3.0, None, {}),
        ("percent", 0.5, 0.5, None, {}),
        ("multitext", ["a", "b"], ["a", "b"], [], {"choices": ["a", "b"]}),
        ("email", "a@example.com", "a@example.com", "", {}),
        ("url", "https://example.com", "https://example.com", "", {}),
        ("duration", 3600000, 3600000, None, {}),
        ("date", dt.date(2026, 3, 4), dt.date(2026, 3, 4), None, {}),
        ("datetime", moment, moment, None, {}),
        ("timeofday", "13:30:00", "13:30:00", "", {}),
        ("checkbox", True, True, False, {}),
        ("phone", "5551234567", "(555) 123-4567", "", {}),
    ]
    for kind, _, _, _, options in cases:
        table.create_field(f"V {kind}", kind, **options)
    table.create_field("V user", "user")
    table.create_field("V multiuser", "multiuser")
    table.create_field("V file", "file")

    blank = table.insert([{"Name": "blank"}]).created_ids[0]
    for kind, value, expected, empty, _ in cases:
        label = f"V {kind}"
        record_id = table.insert([{label: value}]).created_ids[0]
        assert read(table, record_id, label) == expected, kind
        assert type(read(table, record_id, label)) is type(expected), kind
        assert read(table, blank, label) == empty, kind

    record_id = table.insert([{"V user": owner}]).created_ids[0]
    assert read(table, record_id, "V user").id == owner.id
    assert read(table, blank, "V user") is None
    record_id = table.insert([{"V multiuser": [owner]}]).created_ids[0]
    assert read(table, record_id, "V multiuser")[0].id == owner.id
    assert read(table, blank, "V multiuser") == []
    record_id = table.insert([{"V file": FileUpload("a.txt", b"hi")}]).created_ids[0]
    assert read(table, record_id, "V file").latest.file_name == "a.txt"
    assert read(table, blank, "V file") is None


def test_a_datetime_field_reports_timestamp(scratch: Any) -> None:
    table = scratch.table()
    assert table.create_field("Seen", "datetime").type == "timestamp"
    assert table.ensure_field("Seen", "datetime").type == "timestamp"
    assert table.ensure_field("Seen", "timestamp").type == "timestamp"


@pytest.mark.parametrize(
    ("key", "value", "field_type"),
    [
        ("decimalPlaces", 2, "numeric"),
        ("doesTotal", True, "numeric"),
        ("currencySymbol", "€", "currency"),
        ("defaultValue", "x", "text"),
        ("maxLength", 10, "text"),
        ("comments", "hi", "text"),
        ("numLines", 3, "text-multi-line"),
        ("appearsAs", "Link", "url"),
        ("allowNewChoices", False, "text-multiple-choice"),
        ("sortAsGiven", True, "text-multiple-choice"),
    ],
)
def test_documented_properties_are_kept(
    scratch: Any, key: str, value: Any, field_type: str
) -> None:
    table = scratch.table()
    field = table.create_field("Thing", field_type, properties={key: value})
    assert field.raw["properties"][key] == value


def test_field_mistakes_are_refused(scratch: Any) -> None:
    table = scratch.table(("Name", "text"))
    with pytest.raises(BadRequestError, match="allowed values"):
        table.create_field("Bad", "nonsense")
    with pytest.raises(BadRequestError, match="already exists"):
        table.create_field("NAME", "text")
    with pytest.raises(BadRequestError, match="not supported"):
        table.create_field("Bad", "text", choices=["a"])
    with pytest.raises(BadRequestError, match="Extraneous key"):
        table.create_field("Bad", "text", properties={"nonsense": 1})
    with pytest.raises(BadRequestError, match="Extraneous key"):
        table.create_field("Bad", "text", properties={"fieldHelp": "Help"})
    assert "fieldHelp" in table.ensure_field("Name", "text").raw  # Beside properties, not in them.
    with pytest.raises(InvalidValueError):
        table.update_field("Name")
    with pytest.raises(SchemaMismatchError):
        table.ensure_field("Name", "numeric")
    assert table.ensure_field("NAME", "text").label == "Name"


def test_required_and_unique_depend_on_existing_records(scratch: Any) -> None:
    table = scratch.table(("Name", "text"), ("Code", "text"))
    table.insert([{"Name": "one", "Code": "same"}, {"Name": "two", "Code": "same"}])

    with pytest.raises(BadRequestError, match="non-unique"):
        table.update_field("Code", unique=True)
    assert not next(f for f in table.fields(refresh=True) if f.label == "Code").unique

    # A new required field is empty on every record, so the second request fails.
    with pytest.raises(BadRequestError, match="null values"):
        table.create_field("Owner", "text", required=True)
    owner = next(f for f in table.fields(refresh=True) if f.label == "Owner")
    assert not owner.required

    # Fill it in, and the same call repairs the field.
    table.update(
        [
            {"Record ID#": record.id, "Owner": "someone"}
            for record in table.query(select=["Record ID#"])
        ]
    )
    assert table.ensure_field("Owner", "text", required=True).required

    # A new unique field is fine: being empty on every record doesn't count as duplicates.
    assert table.create_field("Serial", "text", unique=True).unique


def test_updating_choices_replaces_them(scratch: Any) -> None:
    table = scratch.table(("Status", "text-multiple-choice", {"choices": ["a", "b"]}))
    assert set(table.update_field("Status", properties={"choices": ["a", "c"]}).choices) == {
        "a",
        "c",
    }


def test_deleting_fields(scratch: Any) -> None:
    table = scratch.table(("Name", "text"), ("Old", "text"), ("Older", "text"))
    with pytest.raises(WriteError, match="built-in"):
        table.delete_fields(["Old", 3])
    labels = {field.label for field in table.fields(refresh=True)}
    assert "Old" not in labels  # The rest of the list was still deleted.

    with pytest.raises(UnknownFieldError):
        table.delete_fields(["Older", "Nope"])
    assert "Older" in {field.label for field in table.fields(refresh=True)}
    with pytest.raises(InvalidValueError):
        table.delete_fields([])


def test_table_mistakes_are_refused(scratch: Any) -> None:
    table = scratch.table()
    name = table.info().name
    app = scratch.client.app(scratch.app_id)
    with pytest.raises(BadRequestError, match="exists"):
        app.create_table(name)
    # Two tables can't share a name through the API, whatever the case or spacing.
    for similar in (name.lower(), f"{name} "):
        with pytest.raises(BadRequestError, match="exists"):
            app.create_table(similar)
    other = app.create_table(f"ZZ Live {secrets.token_hex(4)}")
    scratch.created.append(other)
    with pytest.raises(BadRequestError, match="exists"):
        scratch.client.request(
            "POST", f"/tables/{other.info().id}", params={"appId": app.id}, json={"name": name}
        )
    with pytest.raises(InvalidValueError):
        table.drop(confirm=name.upper())
    found = scratch.client.app(scratch.app_id).ensure_table(name.upper())
    assert found.info().id == table.info().id


def test_relationships_add_what_the_guide_says(scratch: Any) -> None:
    parent = scratch.table(("Name", "text"), ("Other", "text"))
    child = scratch.table(("Hours", "numeric"))
    before = {field.label for field in parent.fields(refresh=True)}

    link = child.create_relationship(parent, foreign_key="Project", lookup_fields=["Name"])

    fields = {field.label: field for field in child.fields(refresh=True)}
    assert fields["Project"].is_foreign_key
    assert fields["Project"].type == "numeric"
    assert fields["Project - Name"].mode == "lookup"
    assert link.lookup_field_ids == (fields["Project - Name"].id,)
    added = [f for f in parent.fields(refresh=True) if f.label not in before]
    assert sorted(field.type for field in added) == ["dblink", "url"]

    # Calling it again makes another relationship, and ensure_ finds the first.
    assert child.create_relationship(parent, foreign_key="Project").id != link.id
    assert child.ensure_relationship(parent, foreign_key="project").id == link.id

    # A relationship can only be seen from the child.
    assert parent.relationships(refresh=True) == []
    assert link.id in [item.id for item in child.relationships(refresh=True)]

    # A renamed lookup is found again by what it points at.
    assert child.ensure_lookup_field(link, "Other", label="Other Name").label == "Other Name"
    assert child.ensure_lookup_field(link, "Other").label == "Other Name"
    with pytest.raises(UnknownFieldError):
        child.ensure_lookup_field(link, "Nope")


def test_summary_functions(scratch: Any) -> None:
    parent = scratch.table(("Name", "text"))
    child = scratch.table(
        ("Hours", "numeric"),
        ("Title", "text"),
        ("Done", "checkbox"),
        ("Due", "date"),
        ("Who", "user"),
        ("Rate", "currency"),
    )
    link = child.ensure_relationship(parent, foreign_key="Project")
    worked = [
        ("Hours", "SUM", "numeric"),
        ("Rate", "SUM", "currency"),
        ("Hours", "AVG", "numeric"),
        ("Hours", "MAX", "numeric"),
        ("Hours", "MIN", "numeric"),
        ("Due", "MAX", "date"),
        ("Due", "MIN", "date"),
        ("Hours", "STD-DEV", "numeric"),
        ("Hours", "DISTINCT-COUNT", "numeric"),
        ("Title", "DISTINCT-COUNT", "numeric"),
        ("Title", "COMBINED-TEXT", "multitext"),
        ("Who", "COMBINED-USER", "multiuser"),
    ]
    for number, (field, function, result) in enumerate(worked):
        made = child.ensure_summary_field(link, field, f"Works {number}", function=function)
        assert (made.type, made.mode, made.summary_function) == (result, "summary", function)
    # The SDK's own names map to Quickbase's, so this finds the STD-DEV summary made above.
    again = child.ensure_summary_field(link, "Hours", "Works 7", function="std_dev")
    assert again.summary_function == "STD-DEV"
    refused = [
        ("Done", "SUM"),
        ("Title", "MAX"),
    ]
    for number, (field, function) in enumerate(refused):
        with pytest.raises(BadRequestError):
            child.ensure_summary_field(link, field, f"Refused {number}", function=function)
    # COUNT takes no field, which Quickbase refuses for every one, so it has its own method.
    with pytest.raises(InvalidValueError, match="ensure_count_field"):
        child.ensure_summary_field(link, "Hours", "Refused count", function="COUNT")
    count = child.ensure_count_field(link, "Task Count")
    assert (count.type, count.mode, count.summary_function) == ("numeric", "summary", "COUNT")
    assert child.ensure_count_field(link, "task count").id == count.id
    with pytest.raises(SchemaMismatchError):
        child.ensure_summary_field(link, "Hours", "Name")

    total = child.ensure_summary_field(link, "Hours", "Total")
    record_id = parent.insert([{"Name": "P"}]).created_ids[0]
    assert read(parent, record_id, "Total") is None
    assert read(parent, record_id, count.label) == 0  # Unlike the others, a count is never empty
    child.insert([{"Hours": 2, "Project": record_id}, {"Hours": 3, "Project": record_id}])
    assert read(parent, record_id, total.label) == 5
    assert read(parent, record_id, count.label) == 2


def test_a_required_field_is_enforced_on_insert(scratch: Any) -> None:
    table = scratch.table(("Name", "text"), ("Must", "text", {"required": True}))
    assert next(f for f in table.fields(refresh=True) if f.label == "Must").required
    with pytest.raises(WriteError, match="required"):
        table.insert([{"Name": "no value"}])


def test_a_lookup_is_named_after_the_foreign_key(scratch: Any) -> None:
    app = scratch.client.app(scratch.app_id)
    parent = app.create_table(
        f"ZZ Live {secrets.token_hex(4)}", single_record_name="Client", plural_record_name="Clients"
    )
    scratch.created.append(parent)
    parent.create_field("Name", "text")
    parent.create_field("Other", "text")
    child = scratch.table(("Hours", "numeric"))
    link = child.create_relationship(parent, foreign_key="Buyer", lookup_fields=["Name"])
    labels = [field.label for field in child.fields(refresh=True) if field.mode == "lookup"]
    assert labels == ["Buyer - Name"]
    assert child.ensure_lookup_field(link, "Other").label == "Buyer - Other"
