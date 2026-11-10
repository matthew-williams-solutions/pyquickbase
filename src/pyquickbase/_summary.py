"""Count, total, and compare records, overall or per group. No network access.

A model shouldn't do arithmetic it can be handed, and a wrong total that looks right is the worst
outcome here, so a value that isn't what its field's type says raises an error, rather than being
skipped. Totals add `Decimal` values, as floats would pick up rounding errors along the way.

Every statistic is listed once, in `STATISTICS`, which the summary, its operation's schema, sorting,
and `tool_result()` all read.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Any, Literal, cast, get_args

from ._models import Field, FieldIndex, Record, User
from ._query import F, FieldRef, FieldRefs, _as_list, _named  # pyright: ignore[reportPrivateUsage]
from .errors import InvalidQueryError, InvalidResponseError

PeriodUnit = Literal["day", "week", "month", "quarter", "year"]
"""How `Period` groups dates."""

_NUMERIC_TYPES = frozenset({"numeric", "currency", "percent", "rating", "duration"})
_DATE_TYPES = frozenset({"date", "timestamp"})
_RANGE_TYPES = _NUMERIC_TYPES | _DATE_TYPES | {"timeofday", "recordid"}
# A record can hold several of these at once, so it wouldn't belong to one group.
_UNGROUPABLE_TYPES = frozenset({"multitext", "multiuser", "file"})
# A page of records is held in memory while it's added up, at about 0.5 MB per 1,000 records for
# each field read. So a page holds about 50,000 field values, about 25 MB, however many fields a
# summary needs. Long text counts as several fields. Fewer requests matter more than smaller pages:
# Quickbase answers a large page nearly as fast as a small one, and limits requests per token.
_PAGE_FIELD_VALUES = 50_000
_MIN_PAGE_SIZE = 1_000
_MAX_PAGE_SIZE = 25_000
_LONG_TEXT_TYPES = frozenset({"text-multi-line", "rich-text"})
_LONG_TEXT_WEIGHT = 4

StatisticName = Literal["sum", "avg", "median", "std_dev", "min", "max", "distinct_count"]
"""A value `summarize()` works out for each group, from one field's values."""


@dataclass(frozen=True, slots=True)
class Statistic:
    """What one statistic takes, and what it does in words, for an operation's schema."""

    takes: frozenset[str] | None
    """The field types it works on, or None for any field a summary can group by."""
    does: str


STATISTICS: Mapping[StatisticName, Statistic] = {
    "sum": Statistic(_NUMERIC_TYPES, "add up"),
    "avg": Statistic(_NUMERIC_TYPES, "average"),
    "median": Statistic(_NUMERIC_TYPES, "find the middle value of"),
    "std_dev": Statistic(_NUMERIC_TYPES, "find the sample standard deviation of"),
    "min": Statistic(_RANGE_TYPES, "find the lowest of"),
    "max": Statistic(_RANGE_TYPES, "find the highest of"),
    "distinct_count": Statistic(None, "count the different values of"),
}
"""Every statistic, in the order results list them."""


@dataclass(frozen=True, slots=True)
class Period:
    """Group by a date or timestamp field's day, week, month, quarter, or year.

    Groups are labeled `2026-03-14`, `2026-W11` (an ISO week), `2026-03`, `2026-Q1`, and `2026`, so
    they sort in order.

    Args:
        field: The date or timestamp field, by label, ID, or field object.
        unit: `day`, `week`, `month`, `quarter`, or `year`.
        time_zone: The time zone that decides a timestamp's date. It has no effect on a date field.

    Raises:
        InvalidQueryError: `unit` isn't one of these, or `field` isn't a field.
    """

    field: FieldRef | F
    unit: PeriodUnit
    time_zone: tzinfo = UTC

    def __post_init__(self) -> None:
        if self.unit not in get_args(PeriodUnit):
            raise InvalidQueryError(
                f"A Period's unit must be one of: {', '.join(get_args(PeriodUnit))}."
            )
        if isinstance(self.field, bool) or not isinstance(self.field, str | int | F):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidQueryError("A Period takes a field's label or ID.")
        if not isinstance(self.time_zone, tzinfo):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidQueryError(
                "A Period's time_zone must be a time zone, such as ZoneInfo('Asia/Tokyo')."
            )


SummaryGroups = FieldRef | F | Period | Sequence[FieldRef | F | Period]
"""One field or period to group by, or a list of them."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PeriodChange:
    """How a group changed since the period before it, from `summarize(compare_periods=True)`.

    Attributes:
        previous: The period it's compared with, such as `2026-02` for `2026-03`.
        count: How many more records there are, which is negative for fewer.
        values: How much each numeric statistic rose, as `{"sum": {"Hours": 4.0}}`, or None where
            either period has no value. A period without records counts as 0 for `sum` and
            `distinct_count`, and has no value for the others.
    """

    previous: str
    count: int
    values: Mapping[str, Mapping[str, float | None]] = field(
        default_factory=dict[str, Mapping[str, float | None]]
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SummaryRow:
    """One group of records, from `table.summarize()`. Values are keyed by field label.

    Attributes:
        group: The values that define this group, or empty without `group_by`. An empty value is
            None, and a user is their email.
        count: How many records are in the group, including those with empty values.
        sum: The sum of each `sum` field, which is 0 if every value is empty.
        avg: The average of each `avg` field, or None if every value is empty.
        median: The middle value of each `median` field, or the average of the two middle ones,
            or None if every value is empty.
        std_dev: The sample standard deviation of each `std_dev` field, or None with fewer than
            two values.
        min: The lowest value of each `min` field, or None if every value is empty.
        max: The highest value of each `max` field, or None if every value is empty.
        distinct_count: How many different values each `distinct_count` field has, leaving out
            empty ones.
        change: With `compare_periods`, how the group changed since the period before. None for
            the first period, and without `compare_periods`.

    `repr` shows only the row's shape, because values may be sensitive and reprs end up in logs.
    """

    group: Mapping[str, Any] = field(default_factory=dict[str, Any])
    count: int = 0
    sum: Mapping[str, float] = field(default_factory=dict[str, float])
    avg: Mapping[str, float | None] = field(default_factory=dict[str, float | None])
    median: Mapping[str, float | None] = field(default_factory=dict[str, float | None])
    std_dev: Mapping[str, float | None] = field(default_factory=dict[str, float | None])
    min: Mapping[str, Any] = field(default_factory=dict[str, Any])
    max: Mapping[str, Any] = field(default_factory=dict[str, Any])
    distinct_count: Mapping[str, int] = field(default_factory=dict[str, int])
    change: PeriodChange | None = None

    def statistic(self, name: StatisticName) -> Mapping[str, Any]:
        """One statistic's values, by field label, such as `row.statistic("sum")`.

        Args:
            name: The statistic, such as `sum` or `median`.

        Returns:
            Its values, by field label, which is empty if the summary didn't ask for it.
        """
        return cast(Mapping[str, Any], getattr(self, name))

    def __repr__(self) -> str:
        return f"SummaryRow(group={sorted(self.group)!r}, count={self.count!r})"


def _period_label(value: date, unit: PeriodUnit) -> str:
    if unit == "day":
        return value.isoformat()
    if unit == "week":
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    if unit == "month":
        return f"{value.year}-{value.month:02d}"
    if unit == "quarter":
        return f"{value.year}-Q{(value.month - 1) // 3 + 1}"
    return str(value.year)


def _previous_period(label: str, unit: PeriodUnit) -> str:
    """The label of the period before one, such as `2026-02` for `2026-03`."""
    if unit == "day":
        return (date.fromisoformat(label) - timedelta(days=1)).isoformat()
    if unit == "week":
        year, week = label.split("-W")
        monday = date.fromisocalendar(int(year), int(week), 1)
        return _period_label(monday - timedelta(days=7), "week")
    if unit == "month":
        year, month = (int(part) for part in label.split("-"))
        return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
    if unit == "quarter":
        year, quarter = label.split("-Q")
        return f"{int(year) - 1}-Q4" if quarter == "1" else f"{year}-Q{int(quarter) - 1}"
    return str(int(label) - 1)


def _unexpected(item: Field) -> InvalidResponseError:
    # The value isn't shown, as it may be sensitive.
    return InvalidResponseError(
        f"Field {item.label!r} holds a value that isn't what a {item.type} field should hold, so "
        "this summary wouldn't be correct."
    )


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _group_order(value: Any) -> tuple[bool, str, Any]:
    """Sort a group's value among its field's others, with empty ones last."""
    if value is None:
        return (True, "", 0)
    if isinstance(value, int | float):
        return (False, "number", value)
    return (False, type(value).__name__, value)


GroupSortKind = Literal[
    "count", "group", "sum", "avg", "median", "std_dev", "min", "max", "distinct_count"
]


@dataclass(frozen=True, slots=True)
class GroupSort:
    """One key to sort summary rows by: their count, a group's value, or a calculated value."""

    kind: GroupSortKind
    label: str | None = None
    descending: bool = False

    def value(self, row: "SummaryRow") -> Any:
        if self.kind == "count":
            return row.count
        if self.kind == "group":
            return row.group.get(cast(str, self.label))
        return row.statistic(self.kind).get(cast(str, self.label))


def sort_rows(rows: Sequence["SummaryRow"], keys: Sequence[GroupSort]) -> list["SummaryRow"]:
    """Sort summary rows by each key in turn, keeping empty values last whichever the direction.

    Rows that tie on every key keep the order they came in, which is by their groups' values.
    """
    ordered = list(rows)
    # A stable sort by each key from the last to the first sorts by all of them.
    for key in reversed(keys):
        present = [row for row in ordered if key.value(row) is not None]
        empty = [row for row in ordered if key.value(row) is None]
        present.sort(key=lambda row: _group_order(key.value(row)), reverse=key.descending)
        ordered = present + empty
    return ordered


class _Numbers:
    """One numeric field's values in a group, kept exactly, with every value only for a median."""

    __slots__ = ("count", "squares", "total", "values")

    def __init__(self, keep: bool) -> None:
        self.count = 0
        self.total = Decimal(0)
        self.squares = Decimal(0)
        self.values: list[Decimal] | None = [] if keep else None

    def add(self, value: Decimal) -> None:
        self.count += 1
        self.total += value
        self.squares += value * value
        if self.values is not None:
            self.values.append(value)

    def average(self) -> float | None:
        return float(self.total / self.count) if self.count else None

    def median(self) -> float | None:
        if not self.values:
            return None
        ordered = sorted(self.values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[middle])
        return float((ordered[middle - 1] + ordered[middle]) / 2)

    def std_dev(self) -> float | None:
        if self.count < 2:
            return None
        spread = self.squares - self.total * self.total / self.count
        # Rounding can leave a tiny negative where every value is the same.
        return float((max(spread, Decimal(0)) / (self.count - 1)).sqrt())


class _Group:
    def __init__(self) -> None:
        self.count = 0
        self.numbers: dict[int, _Numbers] = {}
        self.lowest: dict[int, Any] = {}
        self.highest: dict[int, Any] = {}
        self.distinct: dict[int, set[Any]] = {}


class Summary:
    """The plan for one summary, which collects records as they're read."""

    def __init__(
        self,
        all_fields: Sequence[Field],
        *,
        group_by: SummaryGroups | None,
        statistics: Mapping[StatisticName, FieldRefs | None],
        compare_periods: bool = False,
    ) -> None:
        self._index = FieldIndex(all_fields)
        self._groups = self._resolve_groups(group_by)
        self._statistics: dict[StatisticName, list[Field]] = {
            name: self._statistic_fields(name, statistics.get(name)) for name in STATISTICS
        }
        self._numeric = {
            item.id: item
            for name in ("sum", "avg", "median", "std_dev")
            for item in self._statistics[name]
        }
        self._medians = {item.id for item in self._statistics["median"]}
        self._ranged = {item.id: item for name in ("min", "max") for item in self._statistics[name]}
        self._counted = {item.id: item for item in self._statistics["distinct_count"]}
        self._compared = self._period_position() if compare_periods else None
        self._seen: dict[tuple[Any, ...], _Group] = {}

    def _resolve(self, ref: FieldRef | F) -> Field:
        return self._index.resolve(_named(ref) if isinstance(ref, F) else ref)

    def _resolve_groups(self, group_by: SummaryGroups | None) -> list[tuple[Field, Period | None]]:
        if group_by is None:
            return []
        if isinstance(group_by, str | int | F | Period):
            given: list[Any] = [group_by]
        else:
            try:
                given = list(group_by)
            except TypeError:
                raise InvalidQueryError("group_by takes fields or a list of them.") from None
        groups: list[tuple[Field, Period | None]] = []
        for ref in given:
            if isinstance(ref, Period):
                item = self._resolve(ref.field)
                if item.type not in _DATE_TYPES:
                    raise InvalidQueryError(
                        f"Field {item.label!r} isn't a date, so it can't use a Period."
                    )
                groups.append((item, ref))
            elif isinstance(ref, str | int | F):
                item = self._resolve(ref)
                if item.type in _UNGROUPABLE_TYPES:
                    raise InvalidQueryError(
                        f"Field {item.label!r} can't be grouped by, because a record can hold "
                        "several of its values."
                    )
                groups.append((item, None))
            else:
                raise InvalidQueryError("group_by takes field labels, IDs, fields, or a Period.")
        labels = [item.label for item, _ in groups]
        if len(set(labels)) != len(labels):
            raise InvalidQueryError(
                "group_by can't use a field twice, as rows are keyed by its label."
            )
        return groups

    def _statistic_fields(self, name: StatisticName, refs: FieldRefs | None) -> list[Field]:
        items = [] if refs is None else [self._resolve(ref) for ref in _as_list(refs, name)]
        takes = STATISTICS[name].takes
        for item in items:
            if takes is None and item.type in _UNGROUPABLE_TYPES:
                raise InvalidQueryError(
                    f"{name} can't use {item.label!r}, because a record can hold several of its "
                    "values."
                )
            if takes is not None and item.type not in takes:
                kinds = "a numeric" if takes == _NUMERIC_TYPES else "a numeric, date, or time"
                raise InvalidQueryError(
                    f"{name} needs {kinds} field, and {item.label!r} is {item.type}."
                )
        return items

    def _period_position(self) -> int:
        positions = [index for index, (_, period) in enumerate(self._groups) if period]
        if len(positions) != 1:
            raise InvalidQueryError(
                "compare_periods needs exactly one Period in group_by, to say which period comes "
                "before which."
            )
        return positions[0]

    @property
    def counts_only(self) -> bool:
        """Whether this summary only counts, so it needs no records, only how many match."""
        return not self._groups and not any(self._statistics.values())

    def page_size(self) -> int:
        """How many records to read per request: fewer when more fields are read."""
        read = {item.id: item for item, _ in self._groups}
        for items in self._statistics.values():
            read.update({item.id: item for item in items})
        weight = 1 + sum(  # Record ID# comes with every record.
            _LONG_TEXT_WEIGHT if item.type in _LONG_TEXT_TYPES else 1 for item in read.values()
        )
        return max(_MIN_PAGE_SIZE, min(_MAX_PAGE_SIZE, _PAGE_FIELD_VALUES // weight))

    @property
    def select(self) -> list[int]:
        """The fields a record needs for this summary."""
        ids = [item.id for item, _ in self._groups]
        for items in self._statistics.values():
            ids += [item.id for item in items]
        return list(dict.fromkeys(ids))

    def _group_value(self, item: Field, period: Period | None, value: Any) -> Any:
        if _is_empty(value):
            return None
        if period is not None:
            if isinstance(value, datetime):
                return _period_label(value.astimezone(period.time_zone).date(), period.unit)
            if isinstance(value, date):
                return _period_label(value, period.unit)
            raise _unexpected(item)
        if isinstance(value, User):
            return value.email or value.id
        if isinstance(value, list | dict):
            raise _unexpected(item)
        return value  # pyright: ignore[reportUnknownVariableType]

    def _add_number(self, group: _Group, item: Field, value: Any) -> None:
        if _is_empty(value):
            return
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _unexpected(item)
        numbers = group.numbers.get(item.id)
        if numbers is None:
            numbers = group.numbers[item.id] = _Numbers(keep=item.id in self._medians)
        numbers.add(Decimal(str(value)))

    def _add_range(self, group: _Group, item: Field, value: Any) -> None:
        if _is_empty(value):
            return
        if item.type in _DATE_TYPES:
            valid = isinstance(value, date)
        elif item.type == "timeofday":
            valid = isinstance(value, str)
        else:
            valid = isinstance(value, int | float) and not isinstance(value, bool)
        if not valid:
            raise _unexpected(item)
        if item.id not in group.lowest or value < group.lowest[item.id]:
            group.lowest[item.id] = value
        if item.id not in group.highest or value > group.highest[item.id]:
            group.highest[item.id] = value

    def _add_distinct(self, group: _Group, item: Field, value: Any) -> None:
        found = self._group_value(item, None, value)
        if found is not None:
            group.distinct.setdefault(item.id, set()).add(found)

    def add(self, record: Record) -> None:
        key = tuple(
            self._group_value(item, period, record.get(item.id)) for item, period in self._groups
        )
        group = self._seen.setdefault(key, _Group())
        group.count += 1
        for item in self._numeric.values():
            self._add_number(group, item, record.get(item.id))
        for item in self._ranged.values():
            self._add_range(group, item, record.get(item.id))
        for item in self._counted.values():
            self._add_distinct(group, item, record.get(item.id))

    def _values(self, group: _Group) -> dict[StatisticName, dict[str, Any]]:
        def numbers(item: Field) -> _Numbers:
            return group.numbers.get(item.id) or _Numbers(keep=False)

        work: dict[StatisticName, Callable[[Field], Any]] = {
            "sum": lambda item: float(numbers(item).total),
            "avg": lambda item: numbers(item).average(),
            "median": lambda item: numbers(item).median(),
            "std_dev": lambda item: numbers(item).std_dev(),
            "min": lambda item: group.lowest.get(item.id),
            "max": lambda item: group.highest.get(item.id),
            "distinct_count": lambda item: len(group.distinct.get(item.id, ())),
        }
        return {
            name: {item.label: work[name](item) for item in items}
            for name, items in self._statistics.items()
        }

    def _row(self, key: tuple[Any, ...], group: _Group) -> SummaryRow:
        return SummaryRow(
            group={item.label: value for (item, _), value in zip(self._groups, key, strict=True)},
            count=group.count,
            **self._values(group),
        )

    def _change(self, key: tuple[Any, ...], row: SummaryRow, first: str) -> PeriodChange | None:
        position = cast(int, self._compared)
        label = key[position]
        if label is None or label <= first:
            return None
        unit = cast(Period, self._groups[position][1]).unit
        previous_label = _previous_period(label, unit)
        previous_key = (*key[:position], previous_label, *key[position + 1 :])
        before = self._row(previous_key, self._seen.get(previous_key) or _Group())
        values: dict[str, dict[str, float | None]] = {}
        for name, current in self._values_of(row).items():
            earlier = before.statistic(name)
            values[name] = {
                label: _difference(value, earlier.get(label)) for label, value in current.items()
            }
        return PeriodChange(previous=previous_label, count=row.count - before.count, values=values)

    @staticmethod
    def _values_of(row: SummaryRow) -> dict[StatisticName, Mapping[str, Any]]:
        return {name: row.statistic(name) for name in STATISTICS if row.statistic(name)}

    def rows(self) -> list[SummaryRow]:
        """The result so far: sorted groups, or a single row if there's no grouping."""
        if not self._groups and not self._seen:
            return [self._row((), _Group())]
        ordered = sorted(self._seen, key=lambda key: tuple(_group_order(value) for value in key))
        rows = [self._row(key, self._seen[key]) for key in ordered]
        if self._compared is None:
            return rows
        # A period before the first one in the result is outside what was summarized.
        labels = [key[self._compared] for key in ordered if key[self._compared] is not None]
        if not labels:
            return rows
        first = min(labels)
        return [
            replace(row, change=self._change(key, row, first))
            for key, row in zip(ordered, rows, strict=True)
        ]


def _difference(current: Any, previous: Any) -> float | None:
    """How much a numeric statistic rose, or None if it isn't numeric in both periods."""
    if isinstance(current, bool) or isinstance(previous, bool):
        return None
    if isinstance(current, int | float) and isinstance(previous, int | float):
        return float(Decimal(str(current)) - Decimal(str(previous)))
    return None
