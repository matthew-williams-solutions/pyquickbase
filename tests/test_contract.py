"""Decisions we've promised to keep through 1.x. A failure here needs a deliberate choice."""

import base64
import dataclasses
import hashlib
import inspect
import os
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import pyquickbase
from pyquickbase import (
    AppInfo,
    Column,
    ConfigurationError,
    Field,
    FileDownload,
    FileUpload,
    FormulaResult,
    InvalidResponseError,
    Page,
    Relationship,
    ReportInfo,
    RetryPolicy,
    RowError,
    TableInfo,
    WhereCondition,
    WriteResult,
)
from pyquickbase import _cli as cli
from pyquickbase._codegen import _RESERVED_CLASSES, _RESERVED_NAMES, render
from pyquickbase._config import DEFAULT_BASE_URL, validate_base_url
from pyquickbase._models import (
    field_from_api,
    file_download_from_response,
    parse,
    relationship_from_api,
    table_info_from_api,
)
from pyquickbase._query import OPERATORS, build_query_body
from pyquickbase._typed import WRITABLE_TYPES, is_writable
from pyquickbase._writes import MAX_ROWS_BYTES, split_batches

GOLDEN = Path(__file__).parent / "fixtures" / "expected_codegen.py"
ALL_TYPES = [
    "text", "text-multiple-choice", "text-multi-line", "rich-text", "email", "url", "phone",
    "address", "timeofday", "numeric", "currency", "rating", "percent", "duration", "date",
    "timestamp", "checkbox", "multitext", "user", "multiuser", "file", "dblink", "some-new-type",
]  # fmt: skip


def schema() -> list[Field]:
    built = [{"id": 3, "label": "Record ID#", "fieldType": "recordid"}]
    built += [
        {"id": 6 + index, "label": f"A {kind}", "fieldType": kind}
        for index, kind in enumerate(ALL_TYPES)
    ]
    built += [
        {"id": 100, "label": "A formula", "fieldType": "numeric", "mode": "formula"},
        {"id": 101, "label": "A lookup", "fieldType": "text", "mode": "lookup"},
        {"id": 102, "label": "class", "fieldType": "text"},
        {"id": 103, "label": "A-text", "fieldType": "text"},
        {"id": 104, "label": "日本語", "fieldType": "text"},
    ]
    return [field_from_api(item) for item in built]


def test_generated_code_is_unchanged() -> None:
    """Catches accidental changes. To change the output on purpose, set QB_UPDATE_GOLDEN=1."""
    tables = [
        (table_info_from_api({"id": "bqtasks", "name": "Tasks"}), schema()),
        (table_info_from_api({"id": "bqother", "name": "Tasks Fields"}), schema()[:2]),
    ]
    source = render(tables)
    if os.environ.get("QB_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(source, encoding="utf-8")
    assert source == GOLDEN.read_text(encoding="utf-8")


def test_reserved_names_stay_frozen() -> None:
    # Adding or removing a name renames attributes in users' code. See the comment in _codegen.py.
    joined = " ".join(sorted(_RESERVED_NAMES))
    expected = "05709a7a07392c75c25e5bdcb5dd9436935279f43cb610a483709ab6e0d1109c"
    assert hashlib.sha256(joined.encode()).hexdigest() == expected


def test_a_table_named_like_an_import_gets_a_safe_name() -> None:
    assert set(_RESERVED_CLASSES) == {
        "Any", "ClassVar", "Column", "FileUpload", "Row", "Self", "TypedField", "TypedRecord",
        "Unset",
    }  # fmt: skip
    for words, name in (("Any", "Any_"), ("Self", "Self_"), ("Row", "Row_"), ("Unset", "Unset_")):
        table = table_info_from_api({"id": "bqtasks", "name": words})
        assert f"class {name}(TypedRecord):" in render([(table, schema()[:1])])


def test_operators() -> None:
    assert {
        "EX", "XEX", "CT", "XCT", "SW", "XSW", "LT", "LTE", "GT", "GTE",
        "BF", "OBF", "AF", "OAF", "IR", "XIR", "HAS", "XHAS", "TV", "XTV",
    } == OPERATORS  # fmt: skip


def test_writable_types() -> None:
    assert {
        "text", "text-multiple-choice", "text-multi-line", "rich-text", "numeric", "currency",
        "rating", "percent", "multitext", "email", "url", "duration", "date", "timestamp",
        "timeofday", "checkbox", "user", "multiuser", "phone", "file", "predecessor",
    } == WRITABLE_TYPES  # fmt: skip


def test_built_in_fields_end_at_five() -> None:
    def text(field_id: int) -> Field:
        return field_from_api({"id": field_id, "label": "A", "fieldType": "text"})

    assert not is_writable(text(5))
    assert is_writable(text(6))


@pytest.mark.parametrize(
    "url",
    ["http://localhost:8000", "http://127.0.0.1", "http://[::1]:8000", "https://a.example"],
)
def test_addresses_allowed_as_a_base_url(url: str) -> None:
    assert validate_base_url(url + "/") == url


@pytest.mark.parametrize(
    "url", ["http://a.example", "http://127.0.0.2", "http://localhost.example", "ftp://localhost"]
)
def test_addresses_refused_as_a_base_url(url: str) -> None:
    with pytest.raises(ConfigurationError):
        validate_base_url(url)


def test_request_size_limit() -> None:
    assert MAX_ROWS_BYTES == 40 * 1024 * 1024 - 1024
    assert DEFAULT_BASE_URL == "https://api.quickbase.com/v1"


def test_batches_are_measured_in_bytes() -> None:
    # Each character takes two bytes, so these fit by characters but not by bytes.
    wide = "é" * (MAX_ROWS_BYTES // 3)
    rows = [{"6": {"value": wide}}, {"6": {"value": wide}}]
    assert list(split_batches(rows, 1000)) == [(0, 1), (1, 2)]


def test_a_new_batch_starts_with_an_empty_count() -> None:
    size = MAX_ROWS_BYTES // 2 - 100
    rows = [{"6": {"value": "x" * size}} for _ in range(5)]
    assert list(split_batches(rows, 1000)) == [(0, 2), (2, 4), (4, 5)]


METADATA: list[Any] = [
    AppInfo(
        id="a", name="n", description="", time_zone=None, date_format=None, raw={"s": "SECRET"}
    ),
    TableInfo(id="a", name="n", description="", key_field_id=None, raw={"s": "SECRET"}),
    Field(
        id=1,
        label="l",
        type="text",
        mode=None,
        required=False,
        unique=False,
        appears_by_default=False,
        choices=(),
        allow_new_choices=False,
        raw={"s": "SECRET"},
    ),
    Relationship(
        id=1,
        parent_table_id="p",
        child_table_id="c",
        foreign_key_field_id=None,
        lookup_field_ids=(),
        is_cross_app=False,
        raw={"s": "SECRET"},
    ),
    ReportInfo(
        id="1", name="n", type="table", description="", incomplete=False, raw={"s": "SECRET"}
    ),
]
VALUES: list[Any] = [
    *METADATA,
    Page(records=(), total=0, skip=0),
    FormulaResult(text="1", type="number"),
    FileDownload(file_name="a", data=b"SECRET", content_type=None),
    FileUpload("a", b"SECRET"),
    WriteResult(),
    RowError(index=0, messages=()),
    WhereCondition(1, "EX", "v"),
    RetryPolicy(),
    Column("a", 1, "text", True, "A"),
]


@pytest.mark.parametrize("value", VALUES, ids=lambda v: type(v).__name__)
def test_public_values_cant_be_changed(value: Any) -> None:
    name = dataclasses.fields(value)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, name, "changed")
    assert not hasattr(value, "__dict__")


@pytest.mark.parametrize("value", METADATA, ids=lambda v: type(v).__name__)
def test_raw_stays_out_of_reprs_and_comparisons(value: Any) -> None:
    assert "SECRET" not in repr(value)
    assert value == dataclasses.replace(value, raw={"other": 1})


def test_keyword_only_where_promised() -> None:
    for kind in (AppInfo, TableInfo, Field, Relationship, ReportInfo, Page, FormulaResult):
        assert all(item.kw_only for item in dataclasses.fields(kind))
    for kind in (FileDownload, WriteResult, RowError, RetryPolicy):
        assert all(item.kw_only for item in dataclasses.fields(kind))
    assert not any(item.kw_only for item in dataclasses.fields(FileUpload))
    assert not any(item.kw_only for item in dataclasses.fields(Column))


def test_file_contents_stay_out_of_reprs() -> None:
    assert "SECRET" not in repr(FileUpload("a", b"SECRET"))
    assert "SECRET" not in repr(FileDownload(file_name="a", data=b"SECRET", content_type=None))
    assert "SECRET" not in repr(FormulaResult(text="SECRET", type="text"))


DEFAULTS = [
    ("Quickbase", "__init__", "timeout", 30.0),
    ("Quickbase", "__init__", "schema_ttl", 300.0),
    ("Quickbase", "__init__", "policy", None),
    ("Quickbase", "__init__", "base_url", "https://api.quickbase.com/v1"),
    ("Table", "query", "page_size", 1000),
    ("Table", "query_page", "limit", 100),
    ("Table", "query_page", "skip", 0),
    ("Table", "insert", "batch_size", 1000),
    ("Table", "update", "batch_size", 1000),
    ("Table", "upsert", "batch_size", 1000),
    ("Report", "run", "page_size", 1000),
    ("Report", "run_page", "limit", 100),
    ("Report", "run_page", "skip", 0),
    ("TypedTable", "query", "page_size", 1000),
]


@pytest.mark.parametrize(("owner", "method", "name", "value"), DEFAULTS)
def test_defaults(owner: str, method: str, name: str, value: Any) -> None:
    for prefix in ("", "Async"):
        function = getattr(getattr(pyquickbase, prefix + owner), method)
        assert inspect.signature(function).parameters[name].default == value


def test_retry_defaults() -> None:
    policy = RetryPolicy()
    assert (policy.max_retries, policy.base_delay, policy.max_delay, policy.max_retry_after) == (
        4,
        1.0,
        30.0,
        60.0,
    )


def test_retry_settings_can_be_zero_but_not_negative() -> None:
    RetryPolicy(max_retries=0, base_delay=0, max_delay=0, max_retry_after=0)
    for setting in ("max_retries", "base_delay", "max_delay", "max_retry_after"):
        with pytest.raises(ConfigurationError):
            RetryPolicy(**{setting: -1})


def test_metadata_settings_are_read() -> None:
    field = field_from_api(
        {
            "id": 6,
            "label": "A",
            "fieldType": "text",
            "required": True,
            "unique": True,
            "appearsByDefault": True,
            "mode": "lookup",
            "properties": {"choices": ["a"], "allowNewChoices": True, "formula": "1"},
        }
    )
    assert field.required
    assert field.unique
    assert field.appears_by_default
    assert field.mode == "lookup"
    assert field.choices == ("a",)
    assert field.allow_new_choices
    assert field.formula == "1"
    link = relationship_from_api(
        {"id": 1, "parentTableId": "p", "childTableId": "c", "isCrossApp": True}
    )
    assert link.is_cross_app
    assert table_info_from_api({"id": "t", "name": "n", "description": "d"}).description == "d"


def test_download_contents_must_be_strict_base64() -> None:
    headers = {"content-disposition": "attachment; filename*=utf-8''a.txt"}
    good = SimpleNamespace(headers=headers, content=base64.b64encode(b"x"))
    assert file_download_from_response(good).data == b"x"
    bad = SimpleNamespace(headers=headers, content=b"eA==!")
    with pytest.raises(InvalidResponseError):
        parse(file_download_from_response, bad, "file")


def test_negative_field_ids_sort_descending() -> None:
    fields = [
        field_from_api({"id": 3, "label": "Record ID#", "fieldType": "recordid"}),
        field_from_api({"id": 6, "label": "A", "fieldType": "text"}),
    ]

    def first_sort(ref: int) -> dict[str, Any]:
        return build_query_body("t", fields, where=None, select=None, order_by=ref)["sortBy"][0]

    assert first_sort(-6) == {"fieldId": 6, "order": "DESC"}
    assert first_sort(6) == {"fieldId": 6, "order": "ASC"}


def test_codegen_needs_its_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    for arguments in (["codegen"], ["codegen", "--app", "a"], ["codegen", "--output", "o"]):
        with pytest.raises(SystemExit) as caught:
            cli.main(arguments)
        assert caught.value.code == 2
    with pytest.raises(SystemExit):
        cli.main([])
    assert "required" in capsys.readouterr().err


def test_codegen_uses_a_read_only_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class Stop(pyquickbase.QuickbaseError):
        pass

    def from_env(**kwargs: Any) -> Any:
        seen.update(kwargs)
        raise Stop("stop here")

    monkeypatch.setattr(cli.Quickbase, "from_env", staticmethod(from_env))
    code = cli.main(["codegen", "--app", "a", "--output", str(tmp_path / "t.py")])
    assert code == 1
    assert isinstance(seen["policy"], pyquickbase.Policy)
    assert not seen["policy"].bind_offline().allows("insert", "bqtasks", field=6).allowed


@pytest.mark.parametrize("wanted", ["Tasks", "bqtasks"])
def test_codegen_finds_a_table_by_name_or_id(wanted: str) -> None:
    info = table_info_from_api({"id": "bqtasks", "name": "Tasks"})
    other = table_info_from_api({"id": "bqother", "name": "Other"})
    app: Any = types.SimpleNamespace(
        id="a",
        tables=lambda: [info, other],
        table=lambda _: types.SimpleNamespace(fields=lambda: schema()[:1]),
    )
    source = cli._tables(app, [wanted])
    assert "class Tasks(TypedRecord):" in source
    assert "class Other(TypedRecord):" not in source
