"""A policy resolved against an app, and the one evaluator that decides every request. No network
access.

`bind_snapshot()` turns a `Policy`'s labels into IDs once, and works out each field's tags,
carrying them through formulas, lookups, and summaries. `BoundPolicy.decide()` is the only code
that decides anything. `offers()` asks it about one field and one verb at a time, so what an
operation offers is exactly what's allowed, and `trim()` uses the same answers to leave hidden
fields out of what comes back.
"""

import fnmatch
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from ._decision import (
    APP_VERBS,
    CODE_PAGE_WARNING,
    FIELD_VERBS,
    DecisionLayer,
    NamedItem,
    PolicyDecision,
    PolicyVerb,
)
from ._formula import field_id_reference, formula_parts, unsafe_functions
from ._models import RECORD_ID_FIELD, Field, FieldIndex, Relationship, ReportInfo, TableInfo
from ._policy import CustomRule, FieldSelector, Grant, Policy, PolicyRule
from ._query import compile_where, where_field_ids
from ._read_request import (
    COLUMN_KEYS,
    EVENT_KEYS,
    FIELD_KEYS,
    FIELD_PROPERTIES,
    FILE_PATH,
    REFUSED_READS,
    REPORT_KEYS,
    TABLE_KEYS,
    USAGE_FIELD_KEYS,
    ReadRequest,
    pick,
    shows_only,
    trim_value,
)
from ._write_request import UNSAFE_FORMULA_TYPES, WriteRequest
from .errors import (
    ConfigurationError,
    InvalidResponseError,
    PolicyRefusedError,
    QuickbaseError,
    UnknownFieldError,
)

T = TypeVar("T")

_FORMAT = 1
_ROWS_CLASS = "\x00rows"
"""The tag of a lookup or summary that reads past a row limit, which every policy denies."""
_SEEN_VERBS = FIELD_VERBS
"""A field any of these allow is visible, so its label can be used. Otherwise it's hidden."""
_READS = ("read", "filter", "sort")
_NEW_PROBE = (1 << 30) + 1
"""A field ID no table has, which the policy treats as a field added after binding."""
_ANY_FIELD = 1 << 30
"""A field ID no table has, which only a rule for every field matches."""
_NEW_FIELD_VERBS = frozenset({"read", "filter", "sort", "download"})


@dataclass(frozen=True, slots=True)
class Snapshot:
    """An app's schema, as `bind_snapshot()` reads it."""

    app_id: str
    tables: tuple[TableInfo, ...]
    fields: Mapping[str, tuple[Field, ...]]
    relationships: tuple[Relationship, ...]
    reports: Mapping[str, tuple[ReportInfo, ...]] = field(default_factory=dict[str, Any])
    relationships_complete: bool = True
    """False when the token's role couldn't read them all. What's missing can't be traced, so every
    lookup and summary is treated as reading whatever a policy hides or limits."""


@dataclass(frozen=True, slots=True)
class _Match:
    """Which fields a grant covers, by ID."""

    all: bool = False
    new: bool = False
    tags: frozenset[str] = frozenset()
    ids: Mapping[str, frozenset[int]] = field(default_factory=dict[str, frozenset[int]])


@dataclass(frozen=True, slots=True)
class _Grant:
    verbs: frozenset[str]
    fields: _Match | None


@dataclass(frozen=True, slots=True, kw_only=True)
class _Rule:
    effect: str
    text: str
    layer: DecisionLayer = "policy"
    app: bool = False
    table_ids: frozenset[str] = frozenset()
    all_tables: bool = False
    created: bool = False
    grants: tuple[_Grant, ...] = ()
    max_records: int | None = None
    max_summary_records: int | None = None
    max_delete: int | None = None
    user_emails: bool = False
    field_types: frozenset[str] | None = None
    reports: Mapping[str, frozenset[str]] | None = None


@dataclass(frozen=True, slots=True)
class _Link:
    id: int
    parent: str
    child: str
    foreign_key: int | None
    lookups: tuple[int, ...]
    summaries: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _Verdict:
    allowed: bool
    rule: str | None = None
    reason: str = ""
    hint: str | None = None
    layer: DecisionLayer = "policy"


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyOffers(Generic[T]):
    """What a `BoundPolicy` allows on one table, for building an operation's schema. Each list
    comes from asking `decide()` about one field and one verb at a time.

    Attributes:
        table: The table.
        readable: Fields it may return.
        filterable: Fields it may filter on.
        sortable: Fields it may sort by.
        insertable: Fields a new record may set.
        updatable: Fields an update may change.
        downloadable: File fields it may download from.
        file_deletable: File fields it may delete versions from.
        can_delete: Whether it may delete records.
        reports: Reports it may run.
        schema_verbs: The schema verbs allowed on the table, such as `create_field`.
        field_types: The field types `create_field` may create, or None for any.
        max_paging_depth: The most records one query may reach, or None for no limit.
        max_records_scanned: The most records one summary may read, or None for no limit.
        max_records_per_delete: The most records one delete may remove, or None for no limit.
        user_emails: Whether user values keep email addresses.
        row_limit: The condition every query is limited to, or None.
        can_read_code_pages: Whether it may list and read the app's code pages.
        can_write_code_pages: Whether it may create and replace them.
    """

    table: NamedItem
    readable: tuple[T, ...] = ()
    filterable: tuple[T, ...] = ()
    sortable: tuple[T, ...] = ()
    insertable: tuple[T, ...] = ()
    updatable: tuple[T, ...] = ()
    downloadable: tuple[T, ...] = ()
    file_deletable: tuple[T, ...] = ()
    can_delete: bool = False
    reports: tuple[Any, ...] = ()
    schema_verbs: frozenset[str] = frozenset()
    field_types: frozenset[str] | None = None
    max_paging_depth: int | None = None
    max_records_scanned: int | None = None
    max_records_per_delete: int | None = None
    user_emails: bool = False
    row_limit: str | None = None
    can_read_code_pages: bool = False
    can_write_code_pages: bool = False


def _query(table_id: str, **kwargs: Any) -> ReadRequest:
    base: dict[str, Any] = {
        "field_ids": frozenset(),
        "filter_field_ids": frozenset(),
        "sort_field_ids": frozenset(),
        "limit": 1,
        "skip": 0,
    }
    return ReadRequest(
        action="query",
        table_id=table_id,
        method="POST",
        path="/records/query",
        **{**base, **kwargs},
    )


def _write(action: Any, table_id: str, **kwargs: Any) -> WriteRequest:
    base: dict[str, Any] = {"field_ids": None, "row_count": None}
    return WriteRequest(
        action=action, table_id=table_id, method="POST", path="/", **{**base, **kwargs}
    )


class BoundPolicy:
    """A `Policy` resolved against one app, keyed by ID, which decides every request a client
    sends. It's frozen: a client that creates a table gets a new one, as `client.policy`.

    Quickbase never reuses a field's ID within a table, so an ID pinned here can't come to mean
    another field. A field added after binding gets the policy's `new_fields`, and a renamed one
    keeps its rules, until the policy is bound again.
    """

    __slots__ = (
        "_cache",
        "_created",
        "_custom",
        "_field_types",
        "_fields",
        "_links",
        "_new_fields",
        "_offline",
        "_open_reads",
        "_reports",
        "_row_fields",
        "_rows",
        "_rules",
        "_tables",
        "_tags",
        "app_id",
    )

    def __init__(
        self,
        *,
        app_id: str | None,
        offline: bool,
        tables: Mapping[str, str],
        fields: Mapping[str, Mapping[int, str]],
        field_types: Mapping[str, Mapping[int, str]],
        tags: Mapping[str, Mapping[int, frozenset[str]]],
        rules: Sequence[_Rule],
        custom: Sequence[CustomRule],
        rows: Mapping[str, tuple[str, str]],
        row_fields: Mapping[str, frozenset[int]],
        links: Sequence[_Link],
        reports: Mapping[str, Mapping[str, tuple[str, str]]],
        new_fields: str,
        created: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.app_id = app_id
        self._offline = offline
        self._tables = dict(tables)
        self._fields = {key: dict(value) for key, value in fields.items()}
        self._field_types = {key: dict(value) for key, value in field_types.items()}
        self._tags = {key: dict(value) for key, value in tags.items()}
        self._rules = tuple(rules)
        self._custom = tuple(custom)
        self._rows = dict(rows)
        self._row_fields = dict(row_fields)
        self._links = tuple(links)
        self._reports = {key: dict(value) for key, value in reports.items()}
        self._new_fields = new_fields
        self._created = tuple(created)
        self._open_reads = not any(
            item.effect in ("deny", "limit") for item in self._rules
        ) and any(
            item.effect == "allow"
            and item.all_tables
            and any("run_formula" in grant.verbs for grant in item.grants)
            for item in self._rules
        )
        self._cache: dict[tuple[str, str, int | None], _Verdict] = {}

    # Names

    @property
    def created(self) -> tuple[str, ...]:
        """The IDs of the tables this client created after binding."""
        return tuple(table_id for table_id, _ in self._created)

    def _table_name(self, table_id: str) -> str:
        found = self._tables.get(table_id) or dict(self._created).get(table_id)
        return found or table_id

    def _named_table(self, table_id: str | None) -> NamedItem | None:
        return None if table_id is None else NamedItem(table_id, self._table_name(table_id))

    def _named_fields(self, table_id: str | None, ids: Iterable[int]) -> tuple[NamedItem, ...]:
        labels = self._fields.get(table_id or "", {})
        return tuple(NamedItem(item, labels.get(item, str(item))) for item in sorted(ids))

    def _table_id(self, ref: str) -> str:
        """A table's ID, from its name or ID."""
        if ref in self._tables or ref in dict(self._created):
            return ref
        for table_id, name in (*self._tables.items(), *self._created):
            if name.casefold() == ref.casefold():
                return table_id
        if self._offline:
            return ref
        raise ConfigurationError(f"The policy doesn't know table {ref!r}.")

    def _field_id(self, table_id: str, ref: str | int) -> int:
        if isinstance(ref, int):
            return ref
        for field_id, label in self._fields.get(table_id, {}).items():
            if label.casefold() == ref.casefold():
                return field_id
        raise UnknownFieldError(
            f"The policy doesn't know field {ref!r} in {self._table_name(table_id)}."
        )

    # Matching

    def _known_table(self, table_id: str) -> bool:
        return self._offline or table_id in self._tables or table_id in dict(self._created)

    def _is_new(self, table_id: str, field_id: int) -> bool:
        known = self._fields.get(table_id)
        if known is None or self._offline or field_id == _ANY_FIELD:
            return False
        return field_id not in known

    def tags_of(self, table_id: str, field_id: int) -> frozenset[str]:
        """The tags a field is in, including those it reads through formulas, lookups, and
        summaries.

        Args:
            table_id: The table's ID.
            field_id: The field's ID.

        Returns:
            The tag names, which are empty for a field in none.
        """
        found = self._tags.get(table_id, {}).get(field_id, frozenset())
        if self._is_new(table_id, field_id) and self._new_fields not in ("read", "hidden"):
            found = found | {self._new_fields}
        return found

    def _rule_has_table(self, rule: _Rule, table_id: str) -> bool:
        if rule.app:
            return False
        created = table_id in dict(self._created)
        if table_id in rule.table_ids:
            return True
        if rule.all_tables and (self._offline or table_id in self._tables or created):
            return True
        return rule.created and created

    def _match_field(self, match: _Match | None, table_id: str, field_id: int) -> bool:
        if match is None:
            return True
        if match.all:
            return True
        if match.new and self._is_new(table_id, field_id):
            return True
        if field_id in match.ids.get(table_id, ()):
            return True
        return bool(match.tags & self.tags_of(table_id, field_id))

    # The evaluator

    def _check(self, verb: str, table_id: str, field_id: int | None = None) -> _Verdict:
        key = (verb, table_id, field_id)
        found = self._cache.get(key)
        if found is None:
            found = self._evaluate(verb, table_id, field_id)
            self._cache[key] = found
        return found

    def _evaluate(self, verb: str, table_id: str, field_id: int | None) -> _Verdict:
        if not self._known_table(table_id):
            return _Verdict(
                False,
                reason=f"The table isn't one of app {self.app_id}'s tables when the policy was "
                "bound, or one this client created",
                hint="bind the policy again, after the table exists",
            )
        for rule in self._rules:
            if rule.effect != "deny" or not self._rule_has_table(rule, table_id):
                continue
            for grant in rule.grants:
                if verb not in grant.verbs:
                    continue
                if field_id is None and grant.fields is not None:
                    continue
                if field_id is not None and not self._match_field(grant.fields, table_id, field_id):
                    continue
                reason = (
                    "It reads past a row limit"
                    if rule.layer == "rows"
                    else "A deny rule covers it, and a deny always wins"
                )
                return _Verdict(False, rule.text, reason, None, rule.layer)
        rows = self._rows.get(table_id)
        if rows is not None and verb in ("report", "download", "delete_file", "run_formula"):
            return _Verdict(
                False,
                rows[1],
                "The table has a row limit, which can't be added to this request",
                None,
                "rows",
            )
        new = field_id is not None and self._is_new(table_id, field_id)
        if new and self._new_fields == "hidden":
            return _Verdict(
                False,
                reason="The field was added after the policy was bound, and new_fields hides it",
                hint="bind the policy again",
            )
        if new and verb not in _NEW_FIELD_VERBS:
            return _Verdict(
                False,
                reason="The field was added after the policy was bound, so it's read-only",
                hint="bind the policy again",
            )
        for rule in self._rules:
            if rule.effect != "allow" or not self._rule_has_table(rule, table_id):
                continue
            for grant in rule.grants:
                if verb not in grant.verbs:
                    continue
                if (field_id is None) != (grant.fields is None):
                    continue
                if field_id == RECORD_ID_FIELD and verb in _READS:
                    return _Verdict(True, rule.text)
                if field_id is not None and not self._match_field(grant.fields, table_id, field_id):
                    continue
                return _Verdict(True, rule.text)
        if field_id == RECORD_ID_FIELD and verb in _READS and self._reads_table(table_id):
            return _Verdict(True, "Record ID# is readable wherever anything is")
        if verb == "filter" and rows is not None and field_id in self._row_fields.get(table_id, ()):
            return _Verdict(True, rows[1], layer="rows")
        return _Verdict(
            False,
            reason="No rule allows it",
            hint=self._hint(verb, table_id, field_id),
        )

    def _reads_table(self, table_id: str) -> bool:
        return any(
            rule.effect == "allow"
            and self._rule_has_table(rule, table_id)
            and any(verb in grant.verbs for grant in rule.grants for verb in _READS)
            for rule in self._rules
        )

    def _hint(self, verb: str, table_id: str, field_id: int | None) -> str:
        table = json.dumps(self._table_name(table_id))
        if field_id is None:
            if verb in ("delete", "report"):
                return f"add allow.{verb}({table})"
            if verb == "read":
                return f"add its fields to an allow.read({table}, ...) rule"
            return f"add allow.schema({table}, {json.dumps(verb)})"
        label = json.dumps(self._fields.get(table_id, {}).get(field_id, field_id))
        if verb in ("update_field", "delete_field"):
            return f"add allow.schema({table}, {json.dumps(verb)}, fields=[{label}])"
        return f"add {label} to an allow.{verb}({table}, ...) rule"

    def _app_check(self, app_id: str | None, verb: str = "create_tables") -> _Verdict:
        if not self._offline and app_id != self.app_id:
            return _Verdict(False, reason=f"The policy is bound to app {self.app_id}")
        for effect in ("deny", "allow"):
            for rule in self._rules:
                if (
                    rule.effect == effect
                    and rule.app
                    and any(verb in grant.verbs for grant in rule.grants)
                ):
                    return _Verdict(
                        effect == "allow",
                        rule.text,
                        "" if effect == "allow" else "A deny rule covers it",
                    )
        return _Verdict(False, reason="No rule allows it", hint=_APP_HINTS[verb])

    def _decision(
        self,
        verdict: _Verdict,
        verb: str,
        table_id: str | None,
        field_ids: Iterable[int] = (),
    ) -> PolicyDecision:
        return PolicyDecision(
            allowed=verdict.allowed,
            verb=cast(PolicyVerb, verb),
            layer=verdict.layer,
            rule=verdict.rule,
            reason=verdict.reason or ("Allowed" if verdict.allowed else ""),
            hint=verdict.hint,
            table=self._named_table(table_id),
            fields=self._named_fields(table_id, field_ids),
        )

    def _all(
        self, checks: Sequence[tuple[str, str, int | None]], unknown: str | None = None
    ) -> PolicyDecision:
        """Allowed only if every check is. `unknown` refuses, for a request that can't be read."""
        # With nothing to check there's nothing to vouch for it, so it stays refused.
        nothing = _Verdict(False, reason="It doesn't name anything the policy can check")
        last: tuple[_Verdict, str, str | None, list[int]] = (nothing, "read", None, [])
        if unknown is not None:
            verb, table_id, _ = checks[0] if checks else ("read", None, None)
            return self._decision(_Verdict(False, reason=unknown), verb, table_id)
        touched: dict[str, list[int]] = {}
        for verb, table_id, field_id in checks:
            verdict = self._check(verb, table_id, field_id)
            if not verdict.allowed:
                return self._decision(
                    verdict, verb, table_id, () if field_id is None else (field_id,)
                )
            ids = touched.setdefault(table_id, [])
            if field_id is not None:
                ids.append(field_id)
            last = (verdict, verb, table_id, ids)
        verdict, verb, table_id, ids = last
        return self._decision(verdict, verb, table_id, ids)

    def decide(self, request: ReadRequest | WriteRequest) -> PolicyDecision:
        """Decide whether a request may be sent. This is the only code that decides.

        Args:
            request: From `describe_read()` or `describe_write()`.

        Returns:
            A `PolicyDecision`. A refusal names the first part refused, and the rule.
        """
        if isinstance(request, ReadRequest):
            decision = self._decide_read(request)
        else:
            decision = self._decide_write(request)
        if not decision.allowed:
            return decision
        for rule in self._custom:
            found = rule.decide(request)
            if found is not None and not found.allowed:
                return found
        return decision

    def _window(self, request: ReadRequest, limit: int | None, name: str) -> PolicyDecision | None:
        if limit is None:
            return None
        table = request.table_id
        if request.limit is None or request.skip is None:
            reason = "It doesn't say how many records to return, so it would get all of them"
        elif request.limit + request.skip > limit:
            reason = (
                f"The policy allows reaching at most {limit} records, counting skip, set by "
                f"{name}, which is how far paging can go, not only the size of a page. Ask for "
                "fewer records with limit, or narrow the filter"
            )
        else:
            return None
        verb = "report" if request.action in ("report", "summary_report") else "read"
        return self._decision(_Verdict(False, reason=reason, hint=f"raise {name}"), verb, table)

    def _decide_read(self, request: ReadRequest) -> PolicyDecision:
        action, table_id = request.action, request.table_id
        if action in REFUSED_READS:
            reason = f"{REFUSED_READS[action]}. Use a client without a policy"
            return self._decision(_Verdict(False, reason=reason), "read", None)
        if action == "code_pages":
            verdict = self._app_check(request.app_id, "read_code_pages")
            return self._decision(verdict, "read_code_pages", request.app_id)
        if self._open_reads:
            # Policy.read_only() reads whatever the token can, as a client without one would.
            return PolicyDecision(
                allowed=True, verb="read", rule="Policy.read_only()", reason="Allowed"
            )
        if action in ("tables", "app_triggers"):
            # Both are trimmed to the tables the policy shows.
            return PolicyDecision(allowed=True, verb="read", reason="Allowed")
        if action in ("app", "roles", "other"):
            reason = {
                "app": "Only tables can be read, not the app's details",
                "roles": "Only tables can be read, not the app's roles",
            }.get(
                action,
                "Only records, reports, files, and metadata can be read. A report can't be run "
                "through request()",
            )
            return self._decision(_Verdict(False, reason=reason), "read", table_id)
        if table_id is None:
            return self._decision(
                _Verdict(False, reason="Its table can't be determined"), "read", None
            )
        if action == "formula":
            return self._all([("run_formula", table_id, None)])
        if action in ("fields", "relationships", "reports", "field_usage"):
            if self.table_visible(table_id):
                return PolicyDecision(
                    allowed=True, verb="read", table=self._named_table(table_id), reason="Allowed"
                )
            verdict = self._check("read", table_id, None)
            return self._decision(
                _Verdict(False, reason="Nothing in the table is allowed", hint=verdict.hint),
                "read",
                table_id,
            )
        if action in ("query", "summarize"):
            unknown = None
            if request.field_ids is None:
                unknown = "The fields it returns can't be determined"
            elif request.filter_field_ids is None:
                unknown = "The fields it filters on can't be determined"
            elif request.sort_field_ids is None:
                unknown = "The fields it sorts by can't be determined"
            if unknown:
                return self._all([("read", table_id, None)], unknown)
            checks = [
                *(("read", table_id, item) for item in sorted(request.field_ids or ())),
                *(("filter", table_id, item) for item in sorted(request.filter_field_ids or ())),
                *(("sort", table_id, item) for item in sorted(request.sort_field_ids or ())),
            ]
            decision = self._all(checks or [("read", table_id, RECORD_ID_FIELD)])
            if not decision.allowed:
                return decision
            if action == "summarize":
                window = self._window(
                    request, self.max_summary_records(table_id), "max_records_scanned"
                )
            else:
                window = self._window(request, self.max_records(table_id), "max_paging_depth")
            return decision if window is None else window
        if action == "delete":
            if request.filter_field_ids is None:
                return self._all(
                    [("filter", table_id, None)], "The fields it filters on can't be determined"
                )
            return self._all(
                [("filter", table_id, item) for item in sorted(request.filter_field_ids)]
                or [("delete", table_id, None)]
            )
        if action in ("report", "summary_report"):
            verdict = self._report(table_id, request.report_id)
            if not verdict.allowed:
                return self._decision(verdict, "report", table_id)
            window = self._window(request, self.max_records(table_id), "max_paging_depth")
            return self._decision(verdict, "report", table_id) if window is None else window
        if action == "file":
            if not request.field_ids:
                return self._all(
                    [("download", table_id, None)], "The file field can't be determined"
                )
            return self._all([("download", table_id, item) for item in sorted(request.field_ids)])
        return self._decision(
            _Verdict(False, reason="It isn't a read the policy knows"), "read", table_id
        )

    def _report(self, table_id: str, report_id: str | None) -> _Verdict:
        verdict = self._check("report", table_id, None)
        if not verdict.allowed:
            return verdict
        for rule in self._rules:
            if rule.effect != "allow" or not self._rule_has_table(rule, table_id):
                continue
            if not any("report" in grant.verbs for grant in rule.grants):
                continue
            listed = None if rule.reports is None else rule.reports.get(table_id, frozenset())
            if listed is None or report_id in listed:
                return _Verdict(True, rule.text)
        name = self._reports.get(table_id, {}).get(report_id or "", (report_id, ""))[0]
        return _Verdict(
            False,
            reason=f"Report {name} isn't one an allow.report() rule lists",
            hint=f"list it in allow.report({json.dumps(self._table_name(table_id))}, [...])",
        )

    def _decide_write(self, request: WriteRequest) -> PolicyDecision:
        action, table_id, ids = request.action, request.table_id, request.field_ids
        if action in ("create_app", "copy_app", "delete_app"):
            return self._decision(
                _Verdict(
                    False,
                    reason="A policy covers one app's tables, so it can't create, copy, or delete "
                    "an app. Use a client without a policy",
                ),
                "update",
                None,
            )
        if action == "write_role_assignments":
            reason = (
                "A client that could change roles could give itself anything a policy withholds, "
                "so no policy can allow it. Use a client without a policy"
            )
            return self._decision(_Verdict(False, reason=reason), "update", None)
        if action == "write_code_page":
            verdict = self._app_check(request.app_id, "write_code_pages")
            reason = CODE_PAGE_WARNING
            if verdict.reason:
                reason = f"{verdict.reason}. {reason}"
            return self._decision(
                _Verdict(verdict.allowed, verdict.rule, reason, verdict.hint),
                "write_code_pages",
                request.app_id,
            )
        if action == "other":
            return self._decision(
                _Verdict(False, reason="Only record, file, and schema writes can be allowed"),
                "update",
                table_id,
            )
        if action == "create_table":
            verdict = self._app_check(request.app_id)
            return self._decision(verdict, "create_tables", request.app_id)
        if table_id is None:
            return self._decision(
                _Verdict(False, reason="Its table can't be determined"), "update", None
            )
        if action in ("insert", "update", "upsert"):
            verbs = {"insert": ["insert"], "update": ["update"], "upsert": ["insert", "update"]}[
                action
            ]
            if ids is None:
                return self._all(
                    [(verbs[0], table_id, None)], "The fields it writes can't be determined"
                )
            return self._all(
                [(verb, table_id, item) for verb in verbs for item in sorted(ids)]
                or [(verb, table_id, RECORD_ID_FIELD) for verb in verbs]
            )
        if action == "delete":
            return self._all([("delete", table_id, None)])
        if action == "delete_file":
            if not ids:
                return self._all(
                    [("delete_file", table_id, None)], "The file field can't be determined"
                )
            return self._all([("delete_file", table_id, item) for item in sorted(ids)])
        if action == "set_key_field":
            return self._all([("set_key_field", table_id, None)])
        if action == "update_table":
            return self._all([("update_table", table_id, None)])
        if action == "delete_table":
            return self._all([("delete_table", table_id, None)])
        checks: list[tuple[str, str, int | None]] = []
        if action == "create_field":
            checks.append(("create_field", table_id, None))
            if "formula" in request.derived:
                checks.append(("formula", table_id, None))
            decision = self._all(checks)
            if not decision.allowed:
                return decision
            refused = self._field_type(request)
            return decision if refused is None else refused
        if action == "update_field":
            if not ids:
                return self._all(
                    [("update_field", table_id, None)], "The field can't be determined"
                )
            checks += [("update_field", table_id, item) for item in sorted(ids)]
            if "formula" in request.derived:
                checks.append(("formula", table_id, None))
            return self._all(checks)
        if action == "delete_field":
            if not ids:
                return self._all(
                    [("delete_field", table_id, None)], "The fields can't be determined"
                )
            return self._all([("delete_field", table_id, item) for item in sorted(ids)])
        parent = request.parent_table_id
        if parent is None:
            return self._all([("link", table_id, None)], "Its parent table can't be determined")
        if action in ("create_relationship", "delete_relationship"):
            verb = "link" if action == "create_relationship" else "unlink"
            checks += [(verb, table_id, None), (verb, parent, None)]
            if action == "delete_relationship":
                known = [
                    link for link in self._links if link.child == table_id and link.parent == parent
                ]
                for link in known:
                    checks += [("delete_field", table_id, item) for item in link.lookups]
                    checks += [("delete_field", parent, item) for item in link.summaries]
                if not known:
                    # Its fields aren't known, so deleting any field must be allowed on both.
                    checks += [
                        ("delete_field", table_id, _ANY_FIELD),
                        ("delete_field", parent, _ANY_FIELD),
                    ]
        if "lookup" in request.derived:
            checks.append(("lookup", table_id, None))
        if "summary" in request.derived:
            checks.append(("summary", parent, None))
        if not checks:
            return self._all([("lookup", table_id, None)], "It adds no lookup or summary")
        return self._all(checks)

    def _field_type(self, request: WriteRequest) -> PolicyDecision | None:
        field_type, table_id = request.field_type, request.table_id or ""
        if field_type is None:
            return self._decision(
                _Verdict(False, reason="Its field type can't be determined"),
                "create_field",
                table_id,
            )
        if "formula" in request.derived and field_type in UNSAFE_FORMULA_TYPES:
            return self._decision(
                _Verdict(
                    False,
                    reason=f"A {field_type} formula makes something people click, which acts "
                    "as them",
                ),
                "formula",
                table_id,
            )
        allowed = self.field_types(table_id)
        if allowed is not None and field_type not in allowed:
            return self._decision(
                _Verdict(
                    False,
                    reason=f"The table only allows fields of type {', '.join(sorted(allowed))}",
                    hint="add the type to allow.schema(..., field_types=[...])",
                ),
                "create_field",
                table_id,
            )
        return None

    # Questions

    def allows(
        self,
        verb: PolicyVerb,
        table: str,
        *,
        field: str | int | None = None,
        fields: Sequence[str | int] = (),
        source: tuple[str, str | int] | None = None,
    ) -> PolicyDecision:
        """Whether the policy allows a verb on a table, or on some of its fields.

        ```python
        bound.allows(
            "update",
            "Job Tasks",
            field="Labor Cost",
        )
        bound.allows(
            "lookup",
            "Job Tasks",
            source=("Technicians", "Hourly Rate"),
        )
        ```

        Args:
            verb: Such as `read`, `update`, or `lookup`.
            table: The table, by name or ID.
            field: One field, by label or ID.
            fields: Several fields. With neither, a verb that applies to fields, such as `read`,
                is asked about every field in the table, and a refusal names the first refused.
            source: For `lookup` and `summary`, the other table's field the new one would show,
                which needs `read`.

        Returns:
            A `PolicyDecision`.

        Raises:
            ConfigurationError: The policy doesn't know the table.
            UnknownFieldError: The policy doesn't know a field.
        """
        if verb in APP_VERBS:
            return self._decision(self._app_check(table, verb), verb, table)
        table_id = self._table_id(table)
        if verb in FIELD_VERBS:
            wanted = [*([] if field is None else [field]), *fields]
            ids = [self._field_id(table_id, item) for item in wanted]
            every = sorted(self._fields.get(table_id) or (RECORD_ID_FIELD,))
            return self._all([(verb, table_id, item) for item in ids or every])
        decision = self._all([(verb, table_id, None)])
        if decision.allowed and source is not None:
            other = self._table_id(source[0])
            source_id = self._field_id(other, source[1])
            found = self._all([("read", other, source_id)])
            if not found.allowed:
                return PolicyDecision(
                    allowed=False,
                    verb=verb,
                    layer="derived",
                    rule=found.rule,
                    reason=f"It would show {found.fields[0]} of {found.table}, which can't be read",
                    hint=found.hint,
                    table=decision.table,
                    fields=found.fields,
                )
            rows = self._rows.get(other)
            if verb == "lookup" and rows is not None:
                return PolicyDecision(
                    allowed=False,
                    verb=verb,
                    layer="rows",
                    rule=rows[1],
                    reason=f"{self._table_name(other)} has a row limit, and a lookup would show "
                    "its records anywhere",
                    table=decision.table,
                )
        return decision

    def explain(
        self,
        verb: PolicyVerb,
        table: str,
        *,
        field: str | int | None = None,
        fields: Sequence[str | int] = (),
        source: tuple[str, str | int] | None = None,
    ) -> str:
        """What `allows()` decides, in words, with every rule that covers the table, and the
        tags of the fields involved.

        Args:
            verb: Such as `read`, `update`, or `lookup`.
            table: The table, by name or ID.
            field: One field, by label or ID.
            fields: Several fields. With neither, a verb that applies to fields, such as `read`,
                is asked about every field in the table, and a refusal names the first refused.
            source: For `lookup` and `summary`, the other table's field the new one would show.

        Returns:
            Several lines of text.
        """
        decision = self.allows(verb, table, field=field, fields=fields, source=source)
        lines = [
            ("Allowed" if decision.allowed else "Refused") + f": {decision.what}.",
            f"Layer: {decision.layer}. Rule: {decision.rule or 'none'}.",
        ]
        if decision.reason and not decision.allowed:
            lines.append(f"Reason: {decision.reason}.")
        if decision.hint and not decision.allowed:
            lines.append(f"To allow it: {decision.hint}.")
        table_id = table if verb in APP_VERBS else self._table_id(table)
        covering = [
            rule.text
            for rule in self._rules
            if (rule.app and verb in APP_VERBS and any(verb in g.verbs for g in rule.grants))
            or (self._rule_has_table(rule, table_id) and any(verb in g.verbs for g in rule.grants))
        ]
        if table_id in self._rows:
            covering.append(self._rows[table_id][1])
        lines.append("Rules that cover it: " + ("; ".join(covering) or "none") + ".")
        tags = sorted(
            {
                name
                for item in decision.fields
                for name in self.tags_of(table_id, int(item.id))
                if name != _ROWS_CLASS
            }
        )
        if tags:
            lines.append(f"Tags: {', '.join(tags)}.")
        return "\n".join(lines)

    def readable(self, table_id: str, field_id: int) -> bool:
        return self._check("read", table_id, field_id).allowed

    def filterable(self, table_id: str, field_id: int) -> bool:
        return self._check("filter", table_id, field_id).allowed

    def visible(self, table_id: str, field_id: int) -> bool:
        """Whether a field can be named. A hidden one acts as if it didn't exist."""
        return any(self._check(verb, table_id, field_id).allowed for verb in _SEEN_VERBS)

    def new_field_effects(
        self, table_id: str, sources: Sequence[tuple[str, int]] | None = ()
    ) -> tuple[str, ...]:
        """What the policy would do with a field a plan adds, which reads `sources`, or every
        field in the app for None."""
        name = self._table_name(table_id)
        if table_id not in self._fields:
            return (f"{name} is new, so its fields follow the rules for tables.created().",)
        found: list[str] = []
        read = self._check("read", table_id, _NEW_PROBE)
        if self._new_fields == "hidden":
            found.append("It's hidden, as new_fields is 'hidden', until the policy is bound again.")
        elif read.allowed:
            found.append(f"It can be read, through {read.rule}.")
        else:
            found.append("No rule reads it, so it's hidden until a rule names it.")
        found.append("It can't be written until the policy is bound again.")
        if sources is None:
            tags = {
                item for table in self._tags.values() for value in table.values() for item in value
            }
        else:
            tags = {item for table, field in sources for item in self.tags_of(table, field)}
        tags.discard(_ROWS_CLASS)
        if tags:
            found.append(f"Once bound again, it's in {_class_words(tags)}.")
            for rule in self._rules:
                if rule.effect != "deny" or not self._rule_has_table(rule, table_id):
                    continue
                if any(grant.fields is not None and grant.fields.tags & tags
                       for grant in rule.grants):  # fmt: skip
                    found.append(f"{rule.text} then hides it.")
        return tuple(found)

    def deletion_effects(
        self, table_id: str, field_ids: Sequence[int] = (), *, table: bool = False
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Which rules lose what a plan deletes, and the blockers for any rule it leaves matching
        nothing. Deleting the table, with `table=True`, loses every field and the table itself."""
        effects: list[str] = []
        blockers: list[str] = []
        name = self._table_name(table_id)
        gone = set(self._fields.get(table_id, {})) if table else set(field_ids)
        for rule in self._rules:
            if rule.effect not in ("allow", "deny") or rule.layer != "policy":
                continue
            if table and table_id in rule.table_ids:
                left = rule.table_ids - {table_id}
                effects.append(f"{rule.text} loses table {name}.")
                if not left and not rule.all_tables and not rule.created:
                    blockers.append(_nothing_left(rule.text, f"table {name}"))
                continue
            lost: set[int] = set()
            empty = False
            for grant in rule.grants:
                match = grant.fields
                if match is None:
                    continue
                named = match.ids.get(table_id, frozenset())
                if not named & gone:
                    continue
                lost |= named & gone
                others = any(ids for key, ids in match.ids.items() if key != table_id)
                if not (named - gone) and not (match.all or match.new or match.tags or others):
                    empty = True
            if lost:
                labels = ", ".join(str(item) for item in self._named_fields(table_id, lost))
                effects.append(f"{rule.text} loses {labels}.")
                if empty:
                    blockers.append(_nothing_left(rule.text, labels))
        return tuple(dict.fromkeys(effects)), tuple(dict.fromkeys(blockers))

    def hidden_labels(self, table_id: str) -> frozenset[str]:
        """The policy's names-only view: the labels, ignoring case, of a table's fields that the
        client can't see, as they were when it was bound. Nothing else about them is kept."""
        return frozenset(
            label.casefold()
            for field_id, label in self._fields.get(table_id, {}).items()
            if not self.visible(table_id, field_id)
        )

    def table_visible(self, table_id: str) -> bool:
        """Whether a table can be named: something in it, or on it, is allowed."""
        if not self._known_table(table_id):
            return False
        for verb in (
            "delete",
            "report",
            "update_table",
            "delete_table",
            "create_field",
            "lookup",
            "summary",
            "link",
            "set_key_field",
        ):
            if self._check(verb, table_id, None).allowed:
                return True
        if self._offline:
            return any(self._check(verb, table_id, RECORD_ID_FIELD).allowed for verb in _SEEN_VERBS)
        known = self._fields.get(table_id)
        if known is None:
            # A created table: any rule that covers it with a field grant.
            return any(
                rule.effect == "allow" and self._rule_has_table(rule, table_id)
                for rule in self._rules
            )
        return any(self.visible(table_id, item) for item in known)

    def _limits(self, table_id: str, name: str, verb: str = "read") -> list[int | None]:
        return [
            getattr(rule, name)
            for rule in self._rules
            if rule.effect == "allow"
            and self._rule_has_table(rule, table_id)
            and any(verb in grant.verbs for grant in rule.grants)
        ]

    def max_records(self, table_id: str) -> int | None:
        """The most records one query may reach in a table, or None for no limit.

        Args:
            table_id: The table's ID.
        """
        found = self._limits(table_id, "max_records")
        return None if not found or None in found else max(cast(list[int], found))

    def max_summary_records(self, table_id: str) -> int | None:
        """The most records `summarize()` may read from a table, or None for no limit."""
        found = self._limits(table_id, "max_summary_records")
        return None if not found or None in found else max(cast(list[int], found))

    def max_delete(self, table_id: str) -> int | None:
        """The most records one delete may remove, or None for no limit."""
        found = self._limits(table_id, "max_delete", "delete")
        return None if not found or None in found else max(cast(list[int], found))

    def user_emails(self, table_id: str) -> bool:
        return any(
            rule.user_emails
            for rule in self._rules
            if rule.effect == "allow" and self._rule_has_table(rule, table_id)
        )

    def field_types(self, table_id: str) -> frozenset[str] | None:
        """The types `create_field` may create in a table, or None for any."""
        found: set[str] = set()
        for rule in self._rules:
            if rule.effect != "allow" or not self._rule_has_table(rule, table_id):
                continue
            if any("create_field" in grant.verbs for grant in rule.grants):
                if rule.field_types is None:
                    return None
                found |= rule.field_types
        return frozenset(found)

    def row_limit(self, table_id: str) -> str | None:
        """The condition every query and delete of a table is ANDed with, by field ID, or None.

        Args:
            table_id: The table's ID.
        """
        found = self._rows.get(table_id)
        return None if found is None else found[0]

    def row_limit_rule(self, table_id: str) -> str | None:
        found = self._rows.get(table_id)
        return None if found is None else found[1]

    def emails_shown(self, table_id: str) -> bool:
        return self.user_emails(table_id)

    def offers(self, table: str) -> PolicyOffers[NamedItem]:
        """What the policy allows on a table, by field, as an operation's schema offers it.

        Args:
            table: The table, by name or ID.

        Returns:
            An `PolicyOffers`, whose lists hold each field's `NamedItem`.

        Raises:
            ConfigurationError: The policy doesn't know the table, or was bound without an app.
        """
        table_id = self._table_id(table)
        known = self._fields.get(table_id)
        if known is None:
            raise ConfigurationError(
                f"The policy doesn't know {table!r}'s fields. Bind it again to see them."
            )
        items = [
            (NamedItem(field_id, label), self._field_types.get(table_id, {}).get(field_id, ""))
            for field_id, label in sorted(known.items())
        ]
        reports = [
            NamedItem(report_id, name)
            for report_id, (name, _) in self._reports.get(table_id, {}).items()
        ]
        types = {
            report_id: kind for report_id, (_, kind) in self._reports.get(table_id, {}).items()
        }
        return self.offers_for(
            table_id,
            items,
            lambda item: int(item.id),
            reports,
            lambda item: (str(item.id), types.get(str(item.id), "table")),
        )

    def offers_for(
        self,
        table_id: str,
        items: Sequence[tuple[T, str]],
        field_id: Callable[[T], int],
        reports: Sequence[Any] = (),
        report_key: Callable[[Any], tuple[str, str]] | None = None,
    ) -> PolicyOffers[T]:
        """`offers()` for fields the caller holds, as `(item, field type)`, with `field_id(item)`
        giving each one's ID, and reports with `report_key(report)` giving `(ID, type)`."""

        def ok(request: ReadRequest | WriteRequest) -> bool:
            return self.decide(request).allowed

        def where(test: Callable[[int], bool], only_files: bool = False) -> tuple[T, ...]:
            return tuple(
                item
                for item, kind in items
                if (not only_files or kind == "file") and test(field_id(item))
            )

        readable = where(lambda fid: ok(_query(table_id, field_ids=frozenset({fid}))))
        filterable = where(lambda fid: ok(_query(table_id, filter_field_ids=frozenset({fid}))))
        sortable = where(lambda fid: ok(_query(table_id, sort_field_ids=frozenset({fid}))))
        insertable = where(
            lambda fid: ok(_write("insert", table_id, field_ids=frozenset({fid}), row_count=1))
        )
        updatable = where(
            lambda fid: ok(_write("update", table_id, field_ids=frozenset({fid}), row_count=1))
        )
        downloadable = where(
            lambda fid: ok(
                ReadRequest(
                    action="file",
                    table_id=table_id,
                    field_ids=frozenset({fid}),
                    method="GET",
                    path="/files",
                )
            ),
            only_files=True,
        )
        file_deletable = where(
            lambda fid: ok(_write("delete_file", table_id, field_ids=frozenset({fid}))),
            only_files=True,
        )
        can_delete = ok(_write("delete", table_id)) and ok(
            ReadRequest(
                action="delete",
                table_id=table_id,
                filter_field_ids=frozenset(),
                method="DELETE",
                path="/records",
            )
        )
        offered_reports: list[Any] = []
        for report in reports:
            if report_key is None:
                break
            report_id, kind = report_key(report)
            action = "report" if kind == "table" else "summary_report"
            request = ReadRequest(
                action=action,
                table_id=table_id,
                limit=1,
                skip=0,
                report_id=report_id,
                method="POST",
                path="/reports",
            )
            if ok(request):
                offered_reports.append(report)
        schema = frozenset(verb for verb in _SCHEMA_TABLE_VERBS if self._schema_ok(verb, table_id))
        return PolicyOffers(
            table=NamedItem(table_id, self._table_name(table_id)),
            readable=readable,
            filterable=filterable,
            sortable=sortable,
            insertable=insertable,
            updatable=updatable,
            downloadable=downloadable,
            file_deletable=file_deletable,
            can_delete=can_delete,
            reports=tuple(offered_reports),
            schema_verbs=schema,
            field_types=self.field_types(table_id) if "create_field" in schema else frozenset(),
            max_paging_depth=self.max_records(table_id),
            max_records_scanned=self.max_summary_records(table_id),
            max_records_per_delete=self.max_delete(table_id),
            user_emails=self.user_emails(table_id),
            row_limit=self.row_limit(table_id),
            can_read_code_pages=self.can_use_code_pages("read_code_pages"),
            can_write_code_pages=self.can_use_code_pages("write_code_pages"),
        )

    def _schema_ok(self, verb: str, table_id: str) -> bool:
        """Whether a schema verb is allowed on a table for some field, as a plan may ask."""
        if verb in ("update_field", "delete_field"):
            known = self._fields.get(table_id)
            if known is None:
                return self._check(verb, table_id, _ANY_FIELD).allowed or any(
                    rule.effect == "allow"
                    and self._rule_has_table(rule, table_id)
                    and any(verb in grant.verbs for grant in rule.grants)
                    for rule in self._rules
                )
            return any(self._check(verb, table_id, item).allowed for item in known)
        return self._check(verb, table_id, None).allowed

    def schema_tables(self) -> list[NamedItem]:
        """The tables any schema verb is allowed on."""
        return [
            NamedItem(table_id, name)
            for table_id, name in (*self._tables.items(), *self._created)
            if any(self._schema_ok(verb, table_id) for verb in _SCHEMA_TABLE_VERBS)
        ]

    def can_create_tables(self, app_id: str) -> bool:
        return self._app_check(app_id).allowed

    def can_use_code_pages(self, verb: str) -> bool:
        """Whether a code page verb is allowed in the app this policy is bound to."""
        return self._app_check(self.app_id, verb).allowed

    def with_created(self, table_id: str, name: str) -> "BoundPolicy":
        """A copy that knows a table this client just created."""
        state = self._state()
        state["created"] = [*state["created"], [table_id, name]]
        return BoundPolicy._from_state(state, self._custom)

    @property
    def created_offers(self) -> frozenset[str]:
        """The schema verbs a table this client creates would get."""
        probe = self.with_created("\x00new", "new table")
        return frozenset(
            verb
            for verb in _SCHEMA_TABLE_VERBS
            if probe._schema_ok(verb, "\x00new")  # pyright: ignore[reportPrivateUsage]
        )

    # Trimming

    def trim(self, request: ReadRequest | None, method: str, path: str, payload: Any) -> Any:
        """Leave out of a response what the policy hides. A response of the wrong shape raises
        `InvalidResponseError`, rather than passing through unchecked."""
        if request is None:
            # A deleted file version's details include the uploader, and the SDK doesn't use them.
            return None if method == "DELETE" and FILE_PATH.fullmatch(path) else payload
        try:
            return self._trim(request, payload)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise InvalidResponseError(
                f"Quickbase returned an unexpected response to {method} {path}, so this client's "
                "policy couldn't check it."
            ) from exc

    def _trim(self, request: ReadRequest, payload: Any) -> Any:
        action, table_id = request.action, request.table_id or ""
        if action == "tables":
            return [pick(item, TABLE_KEYS) for item in payload if self.table_visible(item["id"])]
        if action == "fields":
            readable = [
                (item["label"], item["id"])
                for item in payload
                if self.readable(table_id, item["id"])
            ]
            return [
                self._trim_field(table_id, item, readable)
                for item in payload
                if self.visible(table_id, item["id"])
            ]
        if action == "field_usage":
            return [
                {
                    "field": pick(item["field"], USAGE_FIELD_KEYS),
                    "usage": {
                        kind: {"count": use["count"]}
                        for kind, use in item["usage"].items()
                        if isinstance(use, Mapping)
                    },
                }
                for item in payload
                if self.visible(table_id, item["field"]["id"])
            ]
        if action == "app_triggers":
            # Owners are people, and the SDK doesn't need them to say what may run.
            return [
                pick(item, EVENT_KEYS)
                for item in payload
                if isinstance(item.get("tableId"), str) and self.table_visible(item["tableId"])
            ]
        if action == "reports":
            return [
                pick(item, REPORT_KEYS)
                for item in payload
                if self._report(table_id, str(int(item["id"]))).allowed
            ]
        if action == "relationships":
            return {
                **payload,
                "relationships": [self._trim_link(item) for item in payload["relationships"]],
            }
        if action in ("query", "summarize", "report"):
            return self._trim_records(table_id, payload, keep_all=False)
        if action == "summary_report":
            return self._trim_records(table_id, payload, keep_all=True)
        return payload

    def _trim_field(
        self, table_id: str, item: Mapping[str, Any], readable: Sequence[tuple[str, int]]
    ) -> dict[str, Any]:
        kept = pick(item, FIELD_KEYS)
        # A field that can't be returned mustn't be selected by default, or every default query
        # fails.
        if not self.readable(table_id, item["id"]):
            kept["appearsByDefault"] = False
        properties = item.get("properties")
        if isinstance(properties, Mapping):
            found = cast(Mapping[str, Any], properties)
            kept["properties"] = pick(found, FIELD_PROPERTIES)
            formula = found.get("formula")
            if isinstance(formula, str) and formula and shows_only(formula, readable):
                kept["properties"]["formula"] = formula
            query = found.get("summaryQuery")
            if isinstance(query, str) and self._query_shown(table_id, found, query):
                kept["properties"]["summaryQuery"] = query
        return kept

    def _query_shown(self, table_id: str, properties: Mapping[str, Any], query: str) -> bool:
        """Whether a summary's filter can be shown: every field it uses is readable on the child."""
        if not query.strip():
            return True
        key = properties.get("summaryReferenceFieldId")
        child = next(
            (
                link.child
                for link in self._links
                if link.parent == table_id and link.foreign_key == key
            ),
            None,
        )
        used = where_field_ids(query)
        if child is None or used is None:
            return False
        return all(self.readable(child, item) for item in used)

    def _trim_link(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """Keep each relationship, so paging by count still works, but only its visible fields."""
        child, parent = item["childTableId"], item["parentTableId"]
        kept: dict[str, Any] = pick(item, ("id", "parentTableId", "childTableId", "isCrossApp"))
        key = item.get("foreignKeyField")
        if key and self.visible(child, key["id"]):
            kept["foreignKeyField"] = pick(key, ("id", "label"))
        kept["lookupFields"] = [
            pick(found, ("id", "label"))
            for found in item.get("lookupFields") or ()
            if self.visible(child, found["id"])
        ]
        kept["summaryFields"] = [
            pick(found, ("id", "label"))
            for found in item.get("summaryFields") or ()
            if self._known_table(parent) and self.visible(parent, found["id"])
        ]
        return kept

    def _trim_records(self, table_id: str, payload: Mapping[str, Any], *, keep_all: bool) -> Any:
        """Keep only the readable columns. A summary report's IDs are column positions, not fields,
        so `keep_all` keeps every column of a report that was allowed on purpose."""
        keep = {
            int(item["id"])
            for item in payload["fields"]
            if keep_all or self.readable(table_id, int(item["id"]))
        }
        emails = self.user_emails(table_id)
        return {
            **payload,
            "fields": [pick(item, COLUMN_KEYS) for item in payload["fields"] if item["id"] in keep],
            "data": [
                {
                    key: {"value": cell["value"] if emails else trim_value(cell["value"])}
                    for key, cell in row.items()
                    if int(key) in keep
                }
                for row in payload["data"]
            ],
        }

    # Saving

    def _state(self) -> dict[str, Any]:
        return {
            "format": _FORMAT,
            "app_id": self.app_id,
            "offline": self._offline,
            "tables": self._tables,
            "fields": {
                key: {str(k): v for k, v in value.items()} for key, value in self._fields.items()
            },
            "field_types": {
                key: {str(k): v for k, v in value.items()}
                for key, value in self._field_types.items()
            },
            "tags": {
                key: {str(k): sorted(v) for k, v in value.items() if v}
                for key, value in self._tags.items()
            },
            "rules": [_rule_state(rule) for rule in self._rules],
            "custom": [repr(rule) for rule in self._custom],
            "rows": {key: list(value) for key, value in self._rows.items()},
            "row_fields": {key: sorted(value) for key, value in self._row_fields.items()},
            "links": [
                [
                    link.id,
                    link.parent,
                    link.child,
                    link.foreign_key,
                    list(link.lookups),
                    list(link.summaries),
                ]
                for link in self._links
            ],
            "reports": {
                key: {k: list(v) for k, v in value.items()} for key, value in self._reports.items()
            },
            "new_fields": self._new_fields,
            "created": [list(item) for item in self._created],
        }

    @property
    def hash(self) -> str:
        """A fingerprint of everything the policy decides with, which confirmations hold."""
        text = json.dumps(self._state(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    def to_json(self) -> str:
        """Save the bound policy, to use in another process without binding again.

        Custom `CustomRule`s can't be saved, so pass them again to `from_json()`.
        """
        return json.dumps(self._state(), sort_keys=True)

    @classmethod
    def from_json(cls, text: str, *, rules: Sequence[CustomRule] = ()) -> "BoundPolicy":
        """Load a bound policy that `to_json()` saved.

        Args:
            text: What `to_json()` returned.
            rules: The custom `CustomRule`s the policy had, which can't be saved.

        Raises:
            ConfigurationError: The text isn't a saved policy, or the custom rules don't match.
        """
        try:
            state = json.loads(text)
            if state.get("format") != _FORMAT:
                raise ValueError("format")
            found = cls._from_state(state, tuple(rules))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ConfigurationError("That isn't a policy saved by BoundPolicy.to_json().") from exc
        if [repr(rule) for rule in rules] != state["custom"]:
            raise ConfigurationError(
                "The policy was saved with custom rules "
                f"{state['custom']!r}. Pass the same ones to from_json(rules=...)."
            )
        return found

    @classmethod
    def _from_state(cls, state: Mapping[str, Any], custom: Sequence[CustomRule]) -> "BoundPolicy":
        return cls(
            app_id=state["app_id"],
            offline=bool(state["offline"]),
            tables=dict(state["tables"]),
            fields={
                key: {int(k): v for k, v in value.items()} for key, value in state["fields"].items()
            },
            field_types={
                key: {int(k): v for k, v in value.items()}
                for key, value in state["field_types"].items()
            },
            tags={
                key: {int(k): frozenset(v) for k, v in value.items()}
                for key, value in state["tags"].items()
            },
            rules=[_rule_from_state(item) for item in state["rules"]],
            custom=custom,
            rows={key: (value[0], value[1]) for key, value in state["rows"].items()},
            row_fields={key: frozenset(value) for key, value in state["row_fields"].items()},
            links=[
                _Link(item[0], item[1], item[2], item[3], tuple(item[4]), tuple(item[5]))
                for item in state["links"]
            ],
            reports={
                key: {k: (v[0], v[1]) for k, v in value.items()}
                for key, value in state["reports"].items()
            },
            new_fields=state["new_fields"],
            created=[(item[0], item[1]) for item in state["created"]],
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, BoundPolicy) and self.hash == other.hash

    def __hash__(self) -> int:
        return hash(self.hash)

    def __repr__(self) -> str:
        rules, digest = len(self._rules), self.hash[:12]
        return f"BoundPolicy(app_id={self.app_id!r}, rules={rules}, hash={digest!r})"


_APP_HINTS = {
    "create_tables": "add allow.create_tables()",
    "read_code_pages": "add allow.code_pages()",
    "write_code_pages": "add allow.code_pages(write=True)",
}
_SCHEMA_TABLE_VERBS = (
    "create_field",
    "update_field",
    "delete_field",
    "formula",
    "lookup",
    "summary",
    "link",
    "unlink",
    "update_table",
    "delete_table",
    "set_key_field",
)


def _class_words(tags: Iterable[str]) -> str:
    names = sorted(tags)
    return f"tag {names[0]}" if len(names) == 1 else f"tags {', '.join(names)}"


def _nothing_left(rule: str, what: str) -> str:
    return (
        f"{rule} would match nothing once {what} is deleted, so it would no longer mean what it "
        "says. Change the rule first, then bind the policy again."
    )


def _match_state(match: _Match | None) -> Any:
    if match is None:
        return None
    return {
        "all": match.all,
        "new": match.new,
        "tags": sorted(match.tags),
        "ids": {key: sorted(value) for key, value in match.ids.items()},
    }


def _rule_state(rule: _Rule) -> dict[str, Any]:
    return {
        "effect": rule.effect,
        "text": rule.text,
        "layer": rule.layer,
        "app": rule.app,
        "table_ids": sorted(rule.table_ids),
        "all_tables": rule.all_tables,
        "created": rule.created,
        "grants": [[sorted(grant.verbs), _match_state(grant.fields)] for grant in rule.grants],
        "max_records": rule.max_records,
        "max_summary_records": rule.max_summary_records,
        "max_delete": rule.max_delete,
        "user_emails": rule.user_emails,
        "field_types": None if rule.field_types is None else sorted(rule.field_types),
        "reports": None
        if rule.reports is None
        else {key: sorted(value) for key, value in rule.reports.items()},
    }


def _rule_from_state(state: Mapping[str, Any]) -> _Rule:
    def match(value: Any) -> _Match | None:
        if value is None:
            return None
        return _Match(
            all=bool(value["all"]),
            new=bool(value["new"]),
            tags=frozenset(value["tags"]),
            ids={key: frozenset(ids) for key, ids in value["ids"].items()},
        )

    return _Rule(
        effect=state["effect"],
        text=state["text"],
        layer=state["layer"],
        app=bool(state["app"]),
        table_ids=frozenset(state["table_ids"]),
        all_tables=bool(state["all_tables"]),
        created=bool(state["created"]),
        grants=tuple(_Grant(frozenset(verbs), match(fields)) for verbs, fields in state["grants"]),
        max_records=state["max_records"],
        max_summary_records=state["max_summary_records"],
        max_delete=state["max_delete"],
        user_emails=bool(state["user_emails"]),
        field_types=None if state["field_types"] is None else frozenset(state["field_types"]),
        reports=None
        if state["reports"] is None
        else {key: frozenset(value) for key, value in state["reports"].items()},
    )


# Binding


class _Binder:
    """Resolves a policy against a snapshot, collecting every problem."""

    def __init__(self, policy: Policy, snapshot: Snapshot | None) -> None:
        self.policy = policy
        self.snapshot = snapshot
        self.problems: list[str] = []
        self.tables: dict[str, TableInfo] = (
            {} if snapshot is None else {t.id: t for t in snapshot.tables}
        )
        self.fields: dict[str, tuple[Field, ...]] = (
            {} if snapshot is None else dict(snapshot.fields)
        )

    def table(self, ref: str, where: str) -> str | None:
        if ref in self.tables:
            return ref
        found = [t.id for t in self.tables.values() if t.name.casefold() == ref.casefold()]
        if len(found) == 1:
            return found[0]
        self.problems.append(f"{where}: the app has no table {ref!r}.")
        return None

    def field(self, table_id: str, ref: str | int, where: str) -> int | None:
        try:
            return FieldIndex(self.fields.get(table_id, ())).resolve(ref).id
        except QuickbaseError:
            name = self.tables[table_id].name
            self.problems.append(f"{where}: table {name!r} has no field {ref!r}.")
            return None

    def like(self, pattern: str) -> dict[str, frozenset[int]]:
        wanted = pattern.casefold()
        return {
            table_id: frozenset(
                item.id for item in found if fnmatch.fnmatchcase(item.label.casefold(), wanted)
            )
            for table_id, found in self.fields.items()
        }

    def class_target(self, name: str, target: Any) -> dict[str, set[int]]:
        where = f"tag {name!r}"
        if isinstance(target, FieldSelector):
            if target.kind == "all":
                return {key: {item.id for item in value} for key, value in self.fields.items()}
            return {key: set(value) for key, value in self.like(str(target.values[0])).items()}
        if isinstance(target, tuple):
            pair = cast(tuple[str, str | int], target)
            table_id = self.table(pair[0], where)
            if table_id is None:
                return {}
            found = self.field(table_id, pair[1], where)
            return {} if found is None else {table_id: {found}}
        text = cast(str, target)
        matches: list[tuple[str, int]] = []
        for position, char in enumerate(text):
            if char != ".":
                continue
            left, right = text[:position], text[position + 1 :]
            table_ids = [
                t.id
                for t in self.tables.values()
                if left in (t.id,) or t.name.casefold() == left.casefold()
            ]
            for table_id in table_ids:
                try:
                    matches.append(
                        (table_id, FieldIndex(self.fields.get(table_id, ())).resolve(right).id)
                    )
                except QuickbaseError:
                    continue
        if not matches:
            self.problems.append(f"{where}: no table and field match {text!r}.")
            return {}
        if len(matches) > 1:
            self.problems.append(
                f"{where}: {text!r} could be several fields. Write it as ('Table', 'Field')."
            )
            return {}
        return {matches[0][0]: {matches[0][1]}}

    def selectors(self, rule: PolicyRule) -> tuple[frozenset[str], bool, bool]:
        ids: set[str] = set()
        every = created = False
        for selector in rule.tables or ():
            if selector.kind == "all":
                every = True
            elif selector.kind == "created":
                created = True
            elif self.snapshot is not None:
                found = self.table(str(selector.values[0]), rule.text)
                if found is not None:
                    ids.add(found)
        return frozenset(ids), every, created

    def match(
        self, rule: PolicyRule, grant: Grant, table_ids: frozenset[str], wide: bool
    ) -> _Match | None:
        if grant.fields is None:
            return None
        every = new = False
        tags: set[str] = set()
        ids: dict[str, set[int]] = {}
        for selector in grant.fields:
            if selector.kind == "all":
                every = True
            elif selector.kind == "new":
                new = True
            elif selector.kind == "tagged":
                for name in selector.values:
                    if name not in self.policy.tags:
                        self.problems.append(f"{rule.text}: there's no tag {name!r}.")
                tags.update(str(item) for item in selector.values)
            elif selector.kind == "labeled_like":
                for table_id, found in self.like(str(selector.values[0])).items():
                    ids.setdefault(table_id, set()).update(found)
            else:
                if wide:
                    self.problems.append(
                        f"{rule.text}: names fields, so it needs tables by name or ID, not "
                        "tables.all() or tables.created()."
                    )
                    continue
                for table_id in table_ids:
                    for ref in selector.values:
                        found = self.field(table_id, ref, rule.text)
                        if found is not None:
                            ids.setdefault(table_id, set()).add(found)
        return _Match(
            all=every,
            new=new,
            tags=frozenset(tags),
            ids={key: frozenset(value) for key, value in ids.items()},
        )

    def reports(
        self, rule: PolicyRule, table_ids: frozenset[str]
    ) -> dict[str, frozenset[str]] | None:
        listed = rule.options.get("reports")
        if listed is None:
            return None
        found: dict[str, frozenset[str]] = {}
        for table_id in table_ids:
            known: tuple[ReportInfo, ...] = (
                self.snapshot.reports.get(table_id, ()) if self.snapshot else ()
            )
            ids: set[str] = set()
            for ref in listed:
                match = [
                    item.id
                    for item in known
                    if item.id == str(ref) or (str(ref).isdigit() and item.id == str(int(ref)))
                ] or [item.id for item in known if item.name.casefold() == str(ref).casefold()]
                if len(match) == 1:
                    ids.add(match[0])
                elif match:
                    self.problems.append(
                        f"{rule.text}: several reports are named {ref!r}. Use its ID."
                    )
                else:
                    self.problems.append(f"{rule.text}: the table has no report {ref!r}.")
            found[table_id] = frozenset(ids)
        return found


def _sources(
    table_id: str,
    item: Field,
    fields: Mapping[str, tuple[Field, ...]],
    links: Sequence[_Link],
) -> list[tuple[str, int]] | None:
    """The fields a derived field reads, or None for every field in the app."""
    if item.mode == "lookup":
        link = next(
            (
                found
                for found in links
                if found.child == table_id and found.foreign_key == item.lookup_reference_field_id
            ),
            None,
        )
        if link is None or item.lookup_target_field_id is None:
            return None
        return [(link.parent, item.lookup_target_field_id)]
    if item.mode == "summary":
        link = next(
            (
                found
                for found in links
                if found.parent == table_id and found.foreign_key == item.summary_reference_field_id
            ),
            None,
        )
        if link is None:
            return None
        read: list[tuple[str, int]] = []
        if item.summary_target_field_id:
            read.append((link.child, item.summary_target_field_id))
        if item.summary_query and item.summary_query.strip():
            used = where_field_ids(item.summary_query)
            if used is None:
                read += [(link.child, other.id) for other in fields.get(link.child, ())]
            else:
                read += [(link.child, other) for other in used]
        return read
    if item.mode == "formula" or item.formula is not None:
        own = [(table_id, other.id) for other in fields.get(table_id, ())]
        if not item.formula:
            return own
        try:
            parts = formula_parts(item.formula)
        except PolicyRefusedError:
            return own
        if unsafe_functions(parts):
            return None
        index = FieldIndex(fields.get(table_id, ()))
        read = []
        for reference in parts.fields:
            ref: str | int = field_id_reference(reference) or reference
            try:
                read.append((table_id, index.resolve(ref).id))
            except QuickbaseError:
                return own
        return read
    return []


def _carry(
    seed: dict[str, dict[int, set[str]]],
    fields: Mapping[str, tuple[Field, ...]],
    links: Sequence[_Link],
) -> dict[str, dict[int, frozenset[str]]]:
    """Give each formula, lookup, and summary the tags of what it reads, through chains and
    cycles, until nothing changes."""
    tags = {
        key: {item.id: set(seed.get(key, {}).get(item.id, ())) for item in value}
        for key, value in fields.items()
    }
    sources = {
        (table_id, item.id): _sources(table_id, item, fields, links)
        for table_id, value in fields.items()
        for item in value
    }
    changed = True
    while changed:
        changed = False
        everything = {name for value in tags.values() for found in value.values() for name in found}
        for (table_id, field_id), read in sources.items():
            if read == []:
                continue
            found = tags[table_id][field_id]
            before = len(found)
            if read is None:
                found |= everything
            else:
                for other_table, other_id in read:
                    found |= tags.get(other_table, {}).get(other_id, set())
            if len(found) != before:
                changed = True
    return {key: {k: frozenset(v) for k, v in value.items()} for key, value in tags.items()}


def bind_snapshot(policy: Policy, snapshot: Snapshot) -> BoundPolicy:
    """Resolve a policy against an app's schema.

    Raises:
        ConfigurationError: Anything can't be resolved. The message lists every problem.
    """
    if policy.app_id is not None and policy.app_id != snapshot.app_id:
        raise ConfigurationError(f"The policy is for app {policy.app_id}, not {snapshot.app_id}.")
    binder = _Binder(policy, snapshot)
    links = [
        _Link(
            item.id,
            item.parent_table_id,
            item.child_table_id,
            item.foreign_key_field_id,
            item.lookup_field_ids,
            item.summary_field_ids,
        )
        for item in snapshot.relationships
    ]
    seed: dict[str, dict[int, set[str]]] = {}
    for name, targets in policy.tags.items():
        for target in targets:
            for table_id, ids in binder.class_target(name, target).items():
                for field_id in ids:
                    seed.setdefault(table_id, {}).setdefault(field_id, set()).add(name)
    rules, custom, rows = _bind_rules(policy, binder)
    row_text: dict[str, tuple[str, str]] = {}
    row_fields: dict[str, frozenset[int]] = {}
    for table_id, parts in rows.items():
        clauses = [clause for clause, _ in parts]
        where = clauses[0] if len(clauses) == 1 else "AND".join(f"({item})" for item in clauses)
        row_text[table_id] = (where, "; ".join(text for _, text in parts))
        used: set[int] = set()
        for clause in clauses:
            used |= where_field_ids(clause) or set()
        row_fields[table_id] = frozenset(used)
    # A lookup from a row-limited parent, or a summary of a row-limited child without the limit
    # in its filter, reads records the limit leaves out.
    by_id = {(t, f.id): f for t, value in snapshot.fields.items() for f in value}
    for link in links:
        if link.parent in row_text:
            for field_id in link.lookups:
                seed.setdefault(link.child, {}).setdefault(field_id, set()).add(_ROWS_CLASS)
        if link.child in row_text:
            limit_text = row_text[link.child][0]
            for field_id in link.summaries:
                found = by_id.get((link.parent, field_id))
                query = (found.summary_query or "") if found else ""
                if limit_text not in query:
                    seed.setdefault(link.parent, {}).setdefault(field_id, set()).add(_ROWS_CLASS)
    if row_text and not snapshot.relationships_complete:
        for table_id, value in snapshot.fields.items():
            for item in value:
                if item.mode in ("lookup", "summary"):
                    seed.setdefault(table_id, {}).setdefault(item.id, set()).add(_ROWS_CLASS)
    if any(_ROWS_CLASS in value for found in seed.values() for value in found.values()):
        texts = "; ".join(sorted({text for _, text in row_text.values()}))
        rules.insert(
            0,
            _Rule(
                effect="deny",
                text=texts,
                layer="rows",
                all_tables=True,
                grants=(_Grant(frozenset(_SEEN_VERBS), _Match(tags=frozenset({_ROWS_CLASS}))),),
            ),
        )
    if binder.problems:
        raise ConfigurationError(
            "The policy can't be bound to app "
            f"{snapshot.app_id}:\n" + "\n".join(f"- {item}" for item in binder.problems)
        )
    tags = _carry(seed, snapshot.fields, links)
    return BoundPolicy(
        app_id=snapshot.app_id,
        offline=False,
        tables={item.id: item.name for item in snapshot.tables},
        fields={
            key: {item.id: item.label for item in value} for key, value in snapshot.fields.items()
        },
        field_types={
            key: {item.id: item.type for item in value} for key, value in snapshot.fields.items()
        },
        tags=tags,
        rules=rules,
        custom=custom,
        rows=row_text,
        row_fields=row_fields,
        links=links,
        reports={
            key: {item.id: (item.name, item.type) for item in value}
            for key, value in snapshot.reports.items()
        },
        new_fields=policy.new_fields,
    )


def _bind_rules(
    policy: Policy, binder: _Binder
) -> tuple[list[_Rule], list[CustomRule], dict[str, list[tuple[str, str]]]]:
    rules: list[_Rule] = []
    custom: list[CustomRule] = []
    rows: dict[str, list[tuple[str, str]]] = {}
    for item in policy.rules:
        if not isinstance(item, PolicyRule):
            custom.append(item)
            continue
        table_ids, every, created = binder.selectors(item)
        if item.effect == "limit":
            targets: set[str] = set(table_ids) | (set(binder.tables) if every else set())
            if created:
                binder.problems.append(f"{item.text}: a row limit needs tables that exist now.")
            for table_id in sorted(targets):
                try:
                    clause = compile_where(
                        item.options["where"], FieldIndex(binder.fields.get(table_id, ()))
                    )
                except QuickbaseError as exc:
                    binder.problems.append(f"{item.text}: {exc}")
                    continue
                rows.setdefault(table_id, []).append((clause, item.text))
            continue
        wide = every or created
        grants = tuple(
            _Grant(grant.verbs, binder.match(item, grant, table_ids, wide)) for grant in item.grants
        )
        delete = any("delete" in grant.verbs for grant in item.grants)
        rules.append(
            _Rule(
                effect=item.effect,
                text=item.text,
                app=item.tables is None,
                table_ids=table_ids,
                all_tables=every,
                created=created,
                grants=grants,
                max_records=None if delete else item.options.get("max_records"),
                max_summary_records=item.options.get("max_summary_records"),
                max_delete=item.options.get("max_records") if delete else None,
                user_emails=bool(item.options.get("user_emails")),
                field_types=item.options.get("field_types"),
                reports=binder.reports(item, table_ids),
            )
        )
    return rules, custom, rows


def bind_offline(policy: Policy) -> BoundPolicy:
    """Bind a policy that names nothing, so every table and field counts as known."""
    if policy.needs_app:
        raise ConfigurationError(
            "This policy names tables, fields, tags, reports, or row limits, so it needs an "
            "app. Bind it with `await policy.bind(app)` first, and pass the BoundPolicy."
        )
    binder = _Binder(policy, None)
    rules, custom, _ = _bind_rules(policy, binder)
    return BoundPolicy(
        app_id=policy.app_id,
        offline=True,
        tables={},
        fields={},
        field_types={},
        tags={},
        rules=rules,
        custom=custom,
        rows={},
        row_fields={},
        links=(),
        reports={},
        new_fields=policy.new_fields,
    )


def derived_refusal(
    verb: PolicyVerb,
    reason: str,
    *,
    table: NamedItem | None = None,
    rule: str | None = None,
    hint: str | None = None,
    layer: DecisionLayer = "derived",
) -> PolicyRefusedError:
    """A refusal of a formula, lookup, or summary because of what it reads."""
    return PolicyRefusedError(
        PolicyDecision(
            allowed=False, verb=verb, layer=layer, rule=rule, reason=reason, hint=hint, table=table
        )
    )
