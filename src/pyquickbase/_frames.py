"""Records to and from pandas and polars DataFrames. No network access.

Neither library is a dependency. Each is imported only when a method that needs it is called, and
a DataFrame passed in is recognized only if its library is already imported, so code that never
uses DataFrames never loads them.
"""

import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from ._models import FileAttachment, Record, User
from ._optional import require_library
from .errors import InvalidValueError

if TYPE_CHECKING:
    import pandas
    import polars

DataFrame: TypeAlias = "pandas.DataFrame | polars.DataFrame"
"""A pandas or polars DataFrame, which `insert()`, `update()`, and `upsert()` take as rows."""


def _cell(value: Any) -> Any:
    """A record's value as a DataFrame holds it: a user as their email, or their ID when the
    client's policy hides emails, and a file as its latest version's name."""
    if isinstance(value, User):
        return value.email or value.id
    if isinstance(value, FileAttachment):
        return value.latest.file_name
    if isinstance(value, list):
        return [_cell(item) for item in cast(list[Any], value)]
    return value


def _rows(records: Iterable[Record], columns: Sequence[str]) -> tuple[list[str], list[list[Any]]]:
    rows = list(records)
    labels = list(columns) or ([str(label) for label in rows[0]] if rows else [])
    return labels, [[_cell(record.get(label)) for label in labels] for record in rows]


def to_pandas(records: Iterable[Record], columns: Sequence[str]) -> "pandas.DataFrame":
    """Records as a pandas DataFrame, with a column for each label in `columns`."""
    pd = require_library("pandas", "to_pandas()")
    labels, rows = _rows(records, columns)
    return pd.DataFrame.from_records(rows, columns=labels)


def to_polars(records: Iterable[Record], columns: Sequence[str]) -> "polars.DataFrame":
    """Records as a polars DataFrame, with a column for each label in `columns`."""
    pl = require_library("polars", "to_polars()")
    labels, rows = _rows(records, columns)
    # Read every row to choose each column's type, since a column may be empty for a while.
    return pl.DataFrame(rows, schema=labels, orient="row", infer_schema_length=None)


def _loaded_frame(value: Any) -> str | None:
    """Which library's DataFrame this is, if its library is loaded. Nothing else can make one."""
    for name in ("pandas", "polars"):
        module = sys.modules.get(name)
        if module is not None and isinstance(value, module.DataFrame):
            return name
    return None


def _plain(value: Any) -> Any:
    """A DataFrame's value as the SDK writes it: missing values as None, and numpy and pandas
    scalars as Python's own types."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    library = type(value).__module__.partition(".")[0]
    if library not in ("numpy", "pandas"):
        return value
    scalar: Any = value
    if hasattr(scalar, "tolist") and getattr(scalar, "ndim", 0):
        return [_plain(item) for item in cast(list[Any], scalar.tolist())]
    pd = sys.modules.get("pandas")
    if pd is not None and pd.isna(scalar):
        return None
    if hasattr(scalar, "to_pydatetime"):
        return scalar.to_pydatetime()
    return scalar.item() if hasattr(scalar, "item") else scalar


def frame_rows(value: Any) -> list[Mapping[str | int, Any]] | None:
    """A DataFrame's rows as `{label or ID: value}` dictionaries, or None if it isn't one.

    The index is left out, as it isn't a column. A missing value leaves its field empty.
    """
    library = _loaded_frame(value)
    if library is None:
        return None
    columns = list(value.columns)
    if len(set(columns)) != len(columns):
        raise InvalidValueError("The DataFrame has several columns with the same name.")
    for column in columns:
        if not isinstance(column, str | int) or isinstance(column, bool):
            raise InvalidValueError(
                f"Column {column!r} isn't a field label or ID. Name each column after a field."
            )
    records = value.to_dict(orient="records") if library == "pandas" else value.to_dicts()
    return [
        {key: _plain(cell) for key, cell in cast(Mapping[str | int, Any], row).items()}
        for row in records
    ]


def rows_or_frame(records: Any) -> Iterable[Mapping[str | int, Any]]:
    """Rows as given, or a DataFrame's rows."""
    rows = frame_rows(records)
    return cast(Iterable[Mapping[str | int, Any]], records) if rows is None else rows
