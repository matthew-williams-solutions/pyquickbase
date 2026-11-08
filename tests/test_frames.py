"""Records to and from pandas and polars DataFrames."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pandas as pd
import polars as pl
import pytest

import pyquickbase._optional
from pyquickbase import (
    FileAttachment,
    FileVersion,
    InvalidValueError,
    Page,
    Quickbase,
    Record,
    User,
)
from pyquickbase._frames import frame_rows

LABELS = {"Record ID#": 3, "Title": 6, "Due": 7, "Done At": 8, "Owner": 9, "Tags": 10, "File": 11}
ANN = User(id="1.a", name="Ann", email="ann@example.com")
FILE = FileAttachment(
    url="", versions=(FileVersion(number=1, file_name="plan.pdf", uploaded=None, creator=None),)
)


def page(*records: Record) -> Page:
    return Page(records=records, total=len(records), skip=0, columns=tuple(LABELS))


FULL = Record(
    {
        3: 1,
        6: "Pour slab",
        7: date(2027, 1, 15),
        8: datetime(2027, 1, 15, 9, 30, tzinfo=UTC),
        9: ANN,
        10: ["a", "b"],
        11: FILE,
    },
    LABELS,
)
EMPTY = Record({3: 2, 6: "Frame walls", 7: None, 8: None, 9: None, 10: [], 11: None}, LABELS)


def test_a_page_becomes_a_pandas_frame() -> None:
    frame = page(FULL, EMPTY).to_pandas()
    assert list(frame.columns) == list(LABELS)
    assert frame.loc[0, "Owner"] == "ann@example.com"
    assert frame.loc[0, "File"] == "plan.pdf"
    assert frame.loc[0, "Due"] == date(2027, 1, 15)
    assert str(frame["Done At"].dtype).startswith("datetime64")
    assert pd.isna(frame.loc[1, "Owner"])


def test_a_page_becomes_a_polars_frame() -> None:
    frame = page(FULL, EMPTY).to_polars()
    assert frame.columns == list(LABELS)
    assert frame.schema["Due"] == pl.Date
    assert frame.schema["Tags"] == pl.List(pl.String)
    assert frame.row(0, named=True)["Owner"] == "ann@example.com"
    assert frame.row(1, named=True)["Due"] is None


def test_an_empty_page_keeps_its_columns() -> None:
    assert list(page().to_pandas().columns) == list(LABELS)
    assert page().to_polars().columns == list(LABELS)


def test_a_user_without_an_email_shows_their_id() -> None:
    hidden = Record({3: 1, 9: User(id="1.a", name="Ann")}, LABELS)
    assert page(hidden).to_pandas().loc[0, "Owner"] == "1.a"


def test_a_missing_library_says_how_to_install_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> Any:
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(pyquickbase._optional.importlib, "import_module", missing)
    with pytest.raises(ModuleNotFoundError, match=r"pip install 'pyquickbase\[polars\]'"):
        page(FULL).to_polars()


def test_pandas_rows_become_plain_values() -> None:
    frame = pd.DataFrame(
        {
            "Title": ["a", None],
            "Hours": [1.5, float("nan")],
            "Count": pd.array([1, None], dtype="Int64"),
            "Done At": pd.to_datetime(["2027-01-15T09:30:00Z", None]),
            "Tags": [["x"], []],
        },
        index=[10, 20],
    )
    rows = frame_rows(frame)
    assert rows == [
        {
            "Title": "a",
            "Hours": 1.5,
            "Count": 1,
            "Done At": datetime(2027, 1, 15, 9, 30, tzinfo=UTC),
            "Tags": ["x"],
        },
        {"Title": None, "Hours": None, "Count": None, "Done At": None, "Tags": []},
    ]
    assert type(rows[0]["Count"]) is int
    assert type(rows[0]["Done At"]) is datetime


def test_polars_rows_become_plain_values() -> None:
    frame = pl.DataFrame(
        {"Title": ["a", None], "Due": [date(2027, 1, 15), None], "Cost": [Decimal("1.50"), None]}
    )
    assert frame_rows(frame) == [
        {"Title": "a", "Due": date(2027, 1, 15), "Cost": Decimal("1.50")},
        {"Title": None, "Due": None, "Cost": None},
    ]


def test_anything_else_isnt_a_frame() -> None:
    assert frame_rows([{"Title": "a"}]) is None


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame([[1, 2]], columns=["Title", "Title"]),
        pd.DataFrame([[1]], columns=[1.5]),
        pd.DataFrame([[1]], columns=[True]),
    ],
)
def test_columns_must_name_fields_once(frame: Any) -> None:
    with pytest.raises(InvalidValueError):
        frame_rows(frame)


FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 7, "label": "Due", "fieldType": "date"},
]


def fake(sent: list[Any]) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path == "/tables":
            return httpx.Response(200, json=[{"id": "bqtasks", "name": "Tasks", "keyFieldId": 3}])
        if path == "/fields":
            return httpx.Response(200, json=FIELDS)
        body = json.loads(request.content)
        sent.append(body)
        if path == "/records":
            ids = list(range(1, len(body["data"]) + 1))
            meta = {"createdRecordIds": ids, "updatedRecordIds": [], "unchangedRecordIds": []}
            return httpx.Response(200, json={"metadata": meta})
        skip = body["options"]["skip"]
        rows = [{"3": {"value": n}, "6": {"value": f"T{n}"}} for n in range(1, 4)][skip : skip + 2]
        meta = {"totalRecords": 3, "numRecords": len(rows), "skip": skip}
        fields = [{"id": 3, "label": "Record ID#", "type": "recordid"},
                  {"id": 6, "label": "Title", "type": "text"}]  # fmt: skip
        return httpx.Response(200, json={"data": rows, "fields": fields, "metadata": meta})

    return httpx.MockTransport(answer)


def test_a_query_pages_into_one_frame() -> None:
    sent: list[Any] = []
    with Quickbase(realm="acme", token="t", http_client=httpx.Client(transport=fake(sent))) as qb:
        frame = qb.app("bq1234567").table("Tasks").query(select=["Title"], page_size=2).to_pandas()
    assert frame.to_dict(orient="list") == {"Record ID#": [1, 2, 3], "Title": ["T1", "T2", "T3"]}
    assert len(sent) == 2


def test_a_frame_can_be_inserted() -> None:
    sent: list[Any] = []
    frame = pd.DataFrame({"Title": ["a", "b"], "Due": [date(2027, 1, 15), None]})
    with Quickbase(realm="acme", token="t", http_client=httpx.Client(transport=fake(sent))) as qb:
        result = qb.app("bq1234567").table("Tasks").insert(frame)
    assert result.row_ids == (1, 2)
    assert sent[0]["data"] == [
        {"6": {"value": "a"}, "7": {"value": "2027-01-15"}},
        {"6": {"value": "b"}, "7": {"value": ""}},
    ]
