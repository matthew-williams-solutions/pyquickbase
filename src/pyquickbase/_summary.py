"""Count and total records, overall or per group. No network access.

A model shouldn't do arithmetic it can be handed, and a wrong total that looks right is the worst
outcome here, so a value that isn't what its field's type says raises an error, rather than being
skipped. Totals add `Decimal` values, as floats would pick up rounding errors along the way.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from decimal import Decimal
from typing import Any, Literal, get_args

from ._models import Field, FieldIndex, Record, User
from ._query import F, FieldRef, FieldRefs, _as_list, _named  # pyright: ignore[reportPrivateUsage]
from .errors import InvalidResponseError, QueryError

PeriodUnit = Literal["day", "week", "month", "quarter", "year"]
"""How `Period` groups dates."""

_NUMERIC_TYPES = frozenset({"numeric", "currency", "percent", "rating", "duration"})
_DATE_TYPES = frozenset({"date", "timestamp"})
_RANGE_TYPES = _NUMERIC_TYPES | _DATE_TYPES | {"timeofday", "recordid"}
# A record can hold several of these at once, so it wouldn't belong to one group.
_UNGROUPABLE_TYPES = frozenset({"multitext", "multiuser", "file"})


@dataclass(frozen=True, slots=True)
class Period:
    """Group by a date or timestamp field's day, week, month, quarter, or year.

    Groups are labeled `2026-03-14`, `2026-W11` (an ISO week), `2026-03`, `2026-Q1`, and `2026`, so
    they sort in order.

    Args:
        field: The date or timestamp field, by label, ID, or field object.
        unit: `day`, `week`, `month`, `quarter`, or `year`.
        tz: The time zone that decides a timestamp's date. It has no effect on a date field.

    Raises:
        QueryError: `unit` isn't one of these, or `field` isn't a field.
    """

    field: FieldRef | F
    unit: PeriodUnit
    tz: tzinfo = UTC

    def __post_init__(self) -> None:
        if self.unit not in get_args(PeriodUnit):
            raise QueryError(f"A Period's unit must be one of: {', '.join(get_args(PeriodUnit))}.")
        if isinstance(self.field, bool) or not isinstance(self.field, str | int | F):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise QueryError("A Period takes a field's label or ID.")
        if not isinstance(self.tz, tzinfo):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise QueryError("A Period's tz must be a time zone, such as ZoneInfo('Asia/Tokyo').")


SummaryGroups = FieldRef | F | Period | Sequence[FieldRef | F | Period]
"""One field or period to group by, or a list of them."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SummaryRow:
    """One group of records, from `table.summarize()`. Values are keyed by field label.

    Attributes:
        group: The values that define this group, or empty without `group_by`. An empty value is
            None, and a user is their email.
        count: How many records are in the group, including those with empty values.
        sum: The sum of each `sum` field, which is 0 if every value is empty.
        avg: The average of each `avg` field, or None if every value is empty.
        min: The lowest value of each `min` field, or None if every value is empty.
        max: The highest value of each `max` field, or None if every value is empty.

    `repr` shows only the row's shape, because values may be sensitive and reprs end up in logs.
    """

    group: Mapping[str, Any] = field(default_factory=dict[str, Any])
    count: int = 0
    sum: Mapping[str, float] = field(default_factory=dict[str, float])
    avg: Mapping[str, float | None] = field(default_factory=dict[str, float | None])
    min: Mapping[str, Any] = field(default_factory=dict[str, Any])
    max: Mapping[str, Any] = field(default_factory=dict[str, Any])

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


class _Group:
    def __init__(self) -> None:
        self.count = 0
        self.totals: dict[int, Decimal] = {}
        self.counts: dict[int, int] = {}
        self.lowest: dict[int, Any] = {}
        self.highest: dict[int, Any] = {}


class Summary:
    """The plan for one summary, which collects records as they're read."""

    def __init__(
        self,
        all_fields: Sequence[Field],
        *,
        group_by: SummaryGroups | None,
        sum: FieldRefs | None,
        avg: FieldRefs | None,
        min: FieldRefs | None,
        max: FieldRefs | None,
    ) -> None:
        self._index = FieldIndex(all_fields)
        self._groups = self._resolve_groups(group_by)
        self._total = self._numeric(sum, "sum")
        self._average = self._numeric(avg, "avg")
        self._minimum = self._ranged(min, "min")
        self._maximum = self._ranged(max, "max")
        # Totals and averages share a running sum.
        self._summed = {item.id: item for item in (*self._total, *self._average)}
        self._ranged_fields = {item.id: item for item in (*self._minimum, *self._maximum)}
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
                raise QueryError("group_by takes fields or a list of them.") from None
        groups: list[tuple[Field, Period | None]] = []
        for ref in given:
            if isinstance(ref, Period):
                item = self._resolve(ref.field)
                if item.type not in _DATE_TYPES:
                    raise QueryError(
                        f"Field {item.label!r} isn't a date, so it can't use a Period."
                    )
                groups.append((item, ref))
            elif isinstance(ref, str | int | F):
                item = self._resolve(ref)
                if item.type in _UNGROUPABLE_TYPES:
                    raise QueryError(
                        f"Field {item.label!r} can't be grouped by, because a record can hold "
                        "several of its values."
                    )
                groups.append((item, None))
            else:
                raise QueryError("group_by takes field labels, IDs, fields, or a Period.")
        labels = [item.label for item, _ in groups]
        if len(set(labels)) != len(labels):
            raise QueryError("group_by can't use a field twice, as rows are keyed by its label.")
        return groups

    def _numeric(self, refs: FieldRefs | None, argument: str) -> list[Field]:
        items = [] if refs is None else [self._resolve(ref) for ref in _as_list(refs, argument)]
        for item in items:
            if item.type not in _NUMERIC_TYPES:
                raise QueryError(
                    f"{argument} needs a numeric field, and {item.label!r} is {item.type}."
                )
        return items

    def _ranged(self, refs: FieldRefs | None, argument: str) -> list[Field]:
        items = [] if refs is None else [self._resolve(ref) for ref in _as_list(refs, argument)]
        for item in items:
            if item.type not in _RANGE_TYPES:
                raise QueryError(
                    f"{argument} needs a numeric, date, or time field, and {item.label!r} is "
                    f"{item.type}."
                )
        return items

    @property
    def select(self) -> list[int]:
        """The fields a record needs for this summary."""
        ids = [item.id for item, _ in self._groups]
        return list(dict.fromkeys([*ids, *self._summed, *self._ranged_fields]))

    def _group_value(self, item: Field, period: Period | None, value: Any) -> Any:
        if _is_empty(value):
            return None
        if period is not None:
            if isinstance(value, datetime):
                return _period_label(value.astimezone(period.tz).date(), period.unit)
            if isinstance(value, date):
                return _period_label(value, period.unit)
            raise _unexpected(item)
        if isinstance(value, User):
            return value.email or value.id
        if isinstance(value, list | dict):
            raise _unexpected(item)
        return value  # pyright: ignore[reportUnknownVariableType]

    def _add_sum(self, group: _Group, item: Field, value: Any) -> None:
        if _is_empty(value):
            return
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _unexpected(item)
        group.totals[item.id] = group.totals.get(item.id, Decimal(0)) + Decimal(str(value))
        group.counts[item.id] = group.counts.get(item.id, 0) + 1

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

    def add(self, record: Record) -> None:
        key = tuple(
            self._group_value(item, period, record.get(item.id)) for item, period in self._groups
        )
        group = self._seen.setdefault(key, _Group())
        group.count += 1
        for item in self._summed.values():
            self._add_sum(group, item, record.get(item.id))
        for item in self._ranged_fields.values():
            self._add_range(group, item, record.get(item.id))

    def _row(self, key: tuple[Any, ...], group: _Group) -> SummaryRow:
        def average(item: Field) -> float | None:
            count = group.counts.get(item.id)
            return float(group.totals[item.id] / count) if count else None

        return SummaryRow(
            group={item.label: value for (item, _), value in zip(self._groups, key, strict=True)},
            count=group.count,
            sum={item.label: float(group.totals.get(item.id, 0)) for item in self._total},
            avg={item.label: average(item) for item in self._average},
            min={item.label: group.lowest.get(item.id) for item in self._minimum},
            max={item.label: group.highest.get(item.id) for item in self._maximum},
        )

    def rows(self) -> list[SummaryRow]:
        """The result so far: sorted groups, or a single row if there's no grouping."""
        if not self._groups and not self._seen:
            return [self._row((), _Group())]
        ordered = sorted(self._seen, key=lambda key: tuple(_group_order(value) for value in key))
        return [self._row(key, self._seen[key]) for key in ordered]
