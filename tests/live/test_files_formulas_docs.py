"""Check what the files and formulas guides say. A failure means the docs are wrong."""

import os
import secrets
from typing import Any

import pytest

from pyquickbase import FileUpload, Policy, Quickbase
from pyquickbase.errors import (
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    UnknownReportError,
)


def versions_kept(table: Any, record_id: int) -> list[int]:
    record = next(iter(table.query(where=f"{{3.EX.{record_id}}}", select=["File"])))
    return [version.number for version in record["File"].versions]


def upload(table: Any, record_id: int, times: int) -> None:
    for number in range(times):
        table.update([{"Record ID#": record_id, "File": FileUpload(f"{number}.txt", b"x")}])


@pytest.mark.parametrize(
    ("properties", "kept"),
    [
        ({}, [3, 4, 5]),
        ({"maxVersions": 1}, [5]),
        ({"maxVersions": 2}, [4, 5]),
        ({"versionMode": "keepallversions"}, [1, 2, 3, 4, 5]),
        ({"versionMode": "keepallversions", "maxVersions": 2}, [1, 2, 3, 4, 5]),
    ],
)
def test_version_settings_decide_what_is_kept(
    scratch: Any, properties: dict[str, Any], kept: list[int]
) -> None:
    table = scratch.table(("Title", "text"), ("File", "file", {"properties": properties}))
    record_id = table.insert([{"Title": "versions"}]).created_ids[0]
    upload(table, record_id, 5)
    assert versions_kept(table, record_id) == kept


def test_changing_max_versions_only_applies_at_the_next_upload(scratch: Any) -> None:
    table = scratch.table(("Title", "text"), ("File", "file", {"properties": {"maxVersions": 5}}))
    record_id = table.insert([{"Title": "versions"}]).created_ids[0]
    upload(table, record_id, 5)

    def set_limit(limit: int) -> None:
        field_id = table.resolve_field_ids(["File"])[0]
        scratch.client.request(
            "POST",
            f"/fields/{field_id}",
            params={"tableId": table.info().id},
            json={"properties": {"maxVersions": limit}},
        )

    set_limit(2)
    assert versions_kept(table, record_id) == [1, 2, 3, 4, 5]
    upload(table, record_id, 1)
    assert versions_kept(table, record_id) == [5, 6]
    set_limit(5)
    assert versions_kept(table, record_id) == [5, 6]


def test_max_versions_must_be_at_least_one(scratch: Any) -> None:
    table = scratch.table()
    with pytest.raises(BadRequestError):
        table.create_field("File", "file", properties={"maxVersions": 0})


def test_a_formula_cant_read_another_app(scratch: Any) -> None:
    there = scratch.table(second_app=True)
    secret = there.create_field("Secret", "text")
    record_id = there.insert([{"Secret": "marker"}]).created_ids[0]
    table_id = there.info().id
    read = f'ToText(GetFieldValues(GetRecord({record_id},"{table_id}"),{secret.id}))'
    assert there.run_formula(read).text == "marker"

    here = scratch.table(("Name", "text"))
    here.insert([{"Name": "here"}])
    assert here.run_formula(read).text == ""
    assert here.run_formula('ToText(Size(GetRecords("{3.GT.0}","bzzzzzzzz")))').text == ""

    # A saved field is no different, and a read within the app shows the formula itself is sound.
    here.create_field("Cross", "text", formula=read)
    assert next(iter(here.query(select=["Cross"])))["Cross"] == ""
    name_id = here.resolve_field_ids(["Name"])[0]
    local = f'ToText(GetFieldValues(GetRecords("{{3.GT.0}}","{here.info().id}"),{name_id}))'
    assert here.run_formula(local).text == "here"


def test_a_formula_cant_read_a_table_the_role_cant_access(scratch: Any) -> None:
    names = ("QB_RESTRICTED_TOKEN", "QB_VIEWABLE_TABLE_ID", "QB_DENIED_TABLE_ID")
    if not all(os.environ.get(name) for name in names):
        pytest.skip(f"Set {', '.join(names)} to run this. See CONTRIBUTING.md.")
    token, viewable_id, denied_id = (os.environ[name] for name in names)

    app = scratch.client.app(scratch.app_id)
    denied = app.table(denied_id)
    records = denied.query(where="{3.GT.0}", select=["Confidential Note"], page_size=1)
    note = next(iter(records))
    secret_id = denied.resolve_field_ids(["Confidential Note"])[0]
    read = f'ToText(GetFieldValues(GetRecord({note.id},"{denied_id}"),{secret_id}))'
    viewable = app.table(viewable_id)
    assert viewable.run_formula(read).text == note["Confidential Note"] != ""

    with Quickbase(realm=scratch.realm, token=token, policy=Policy.read_only()) as restricted:
        limited = restricted.app(scratch.app_id).table(viewable_id)
        assert limited.run_formula(read).text == ""
        count = f'ToText(Size(GetRecords("{{3.GT.0}}","{denied_id}")))'
        assert limited.run_formula(count).text == ""
        own = f'ToText(Size(GetRecords("{{3.GT.0}}","{viewable_id}")))'
        assert int(limited.run_formula(own).text) > 0

        for method, path, kwargs in (
            ("POST", "/records/query", {"json": {"from": denied_id}}),
            ("GET", f"/tables/{denied_id}", {"params": {"appId": scratch.app_id}}),
            ("GET", "/fields", {"params": {"tableId": denied_id}}),
            ("POST", "/formula/run", {"json": {"formula": "1+1", "from": denied_id}}),
        ):
            with pytest.raises(PermissionDeniedError):
                restricted.request(method, path, **kwargs)
        listed = {item.id for item in restricted.app(scratch.app_id).tables()}
        assert denied_id not in listed

        # A saved field shows each user their own result.
        saved = viewable.create_field(f"ZZ Live {secrets.token_hex(4)}", "text", formula=read)
        try:
            query = {"from": viewable_id, "select": [saved.id], "options": {"top": 1}}
            for client, expected in (
                (scratch.client, note["Confidential Note"]),
                (restricted, ""),
            ):
                row = client.request("POST", "/records/query", json=query)["data"][0]
                assert row[str(saved.id)]["value"] == expected
        finally:
            viewable.delete_fields([saved.id])


def limited_client(scratch: Any) -> tuple[Quickbase, str]:
    names = ("QB_RESTRICTED_TOKEN", "QB_VIEWABLE_TABLE_ID")
    if not all(os.environ.get(name) for name in names):
        pytest.skip(f"Set {', '.join(names)} to run this. See CONTRIBUTING.md.")
    token, table_id = (os.environ[name] for name in names)
    return Quickbase(realm=scratch.realm, token=token), table_id


def test_record_rules_limit_what_a_role_reads_and_changes(scratch: Any) -> None:
    client, table_id = limited_client(scratch)
    main = scratch.client.app(scratch.app_id).table(table_id)

    def first_in(category: str) -> int:
        where = f"{{6.EX.'{category}'}}"
        return next(iter(main.query(where=where, select=["Category"], page_size=1))).id

    alpha, beta, gamma = (first_in(item) for item in ("Alpha", "Beta", "Gamma"))
    allowed = sum(main.count(where=f"{{6.EX.'{item}'}}") for item in ("Alpha", "Beta"))

    with client:
        table = client.app(scratch.app_id).table(table_id)
        assert table.count() == allowed
        assert table.count(where=f"{{3.EX.{gamma}}}") == 0
        assert table.count(where="{6.EX.'Gamma'}") == 0
        count = f'ToText(Size(GetRecords("{{3.GT.0}}","{table_id}")))'
        assert table.run_formula(count).text == str(allowed)
        read = 'ToText(GetFieldValues(GetRecord({},"{}"),6))'
        assert table.run_formula(read.format(alpha, table_id)).text == "Alpha"
        assert table.run_formula(read.format(gamma, table_id)).text == ""

        # A hidden record and a missing one are refused differently.
        with pytest.raises(PermissionDeniedError):
            table.run_formula("1+1", record_id=gamma)
        with pytest.raises(NotFoundError):
            table.run_formula("1+1", record_id=99_999_999)

        # These should all be refused, so nothing here changes the table.
        rank_id = main.resolve_field_ids(["Internal Rank"])[0]
        for row, message in (
            ({"3": {"value": beta}, "6": {"value": "Alpha"}}, "permission to update"),
            ({"3": {"value": gamma}, "6": {"value": "Alpha"}}, "permission to update"),
            ({"3": {"value": alpha}, str(rank_id): {"value": 1}}, "restricted field"),
        ):
            body = {"to": table_id, "data": [row]}
            errors = client.request("POST", "/records", json=body)["metadata"]["lineErrors"]
            assert message in errors["1"][0]
        with pytest.raises(PermissionDeniedError):
            client.request("DELETE", "/records", json={"from": table_id, "where": "{3.LT.0}"})


@pytest.mark.parametrize("name", ["Records by Internal Rank", "Top Ranked Records"])
def test_a_report_using_a_hidden_field_isnt_available(scratch: Any, name: str) -> None:
    client, table_id = limited_client(scratch)
    main = scratch.client.app(scratch.app_id).table(table_id)
    report_id = next(item.id for item in main.reports(refresh=True) if item.name == name)

    with client:
        table = client.app(scratch.app_id).table(table_id)
        assert name not in {item.name for item in table.reports()}
        with pytest.raises(UnknownReportError):
            list(table.report(name).run())
        with pytest.raises(BadRequestError, match="Internal error"):
            client.request("POST", f"/reports/{report_id}/run", params={"tableId": table_id})


def test_field_types_only_the_ui_can_create(scratch: Any) -> None:
    table_id = os.environ.get("QB_FIELD_TYPES_TABLE_ID")
    if not table_id:
        pytest.skip("Set QB_FIELD_TYPES_TABLE_ID to run this. See CONTRIBUTING.md.")
    table = scratch.client.app(scratch.app_id).table(table_id)
    fields = {item.label: item for item in table.fields(refresh=True)}
    assert fields["Contact Card"].type == "vCardButton"
    assert fields["Calendar Entry"].type == "ICalendarButton"
    assert fields["Depends On"].type == "predecessor"
    assert fields["Team Members"].type == "multiuser"
    assert fields["Team Members"].mode == "formula"

    labels = ["Contact Card", "Calendar Entry", "Depends On", "Team Members"]
    record = next(iter(table.query(select=labels)))
    assert "a=VCard" in record["Contact Card"]
    assert "a=ICal" in record["Calendar Entry"]
    assert isinstance(record["Depends On"], str)
    assert isinstance(record["Team Members"], list)

    # These should all be refused, so nothing here changes the table.
    for label, value, message in (
        ("Contact Card", "x", "read-only"),
        ("Calendar Entry", "x", "read-only"),
        ("Team Members", [{"id": "x"}], "formula"),
        ("Depends On", "99999999", "Invalid entry"),
    ):
        row = {"3": {"value": record.id}, str(fields[label].id): {"value": value}}
        body = {"to": table_id, "data": [row]}
        errors = scratch.client.request("POST", "/records", json=body)["metadata"]["lineErrors"]
        assert message in errors["1"][0]
