"""What a policy decided, and why. No network access, and nothing else in the SDK imported."""

from dataclasses import dataclass
from typing import Literal, get_args

Verb = Literal[
    "read",
    "filter",
    "sort",
    "insert",
    "update",
    "delete",
    "download",
    "delete_file",
    "report",
    "run_formula",
    "add_field",
    "change_field",
    "delete_field",
    "formula",
    "lookup",
    "summary",
    "link",
    "unlink",
    "change_table",
    "delete_table",
    "create_tables",
]
"""What a policy grants or refuses. See `allow`."""

SchemaVerb = Literal[
    "add_field",
    "change_field",
    "delete_field",
    "formula",
    "lookup",
    "summary",
    "link",
    "unlink",
    "change_table",
    "delete_table",
]
"""What `allow.schema()` grants on a table."""

Layer = Literal["policy", "rows", "derived", "confirmation"]
"""Which check made a decision. See `Decision`."""

VERBS: tuple[str, ...] = get_args(Verb)
SCHEMA_VERBS: tuple[str, ...] = get_args(SchemaVerb)
READ_VERBS = frozenset({"read", "filter", "sort", "download", "report", "run_formula"})
"""The verbs that only read, so that a refusal can say which kind it was."""
FIELD_VERBS = frozenset(
    {"read", "filter", "sort", "insert", "update", "download", "delete_file", "change_field"}
    | {"delete_field"}
)
"""The verbs that apply to one field. The others apply to a whole table, or to the app."""


@dataclass(frozen=True, slots=True)
class Named:
    """A table or field, by its ID and its name when the policy was bound.

    Attributes:
        id: The table's ID, or the field's.
        name: The table's name, or the field's label. Its ID, as text, if the policy doesn't know
            it.
    """

    id: str | int
    name: str

    def __str__(self) -> str:
        return self.name if self.name == str(self.id) else f"{self.name} ({self.id})"


_VERB_TEXT: dict[str, str] = {
    "read": "reading",
    "filter": "filtering on",
    "sort": "sorting by",
    "insert": "inserting",
    "update": "updating",
    "delete": "deleting records from",
    "download": "downloading files from",
    "delete_file": "deleting file versions from",
    "report": "running",
    "run_formula": "running a formula on",
    "add_field": "adding a field to",
    "change_field": "changing",
    "delete_field": "deleting",
    "formula": "making a formula in",
    "lookup": "adding a lookup to",
    "summary": "adding a summary to",
    "link": "linking",
    "unlink": "unlinking",
    "change_table": "changing",
    "delete_table": "deleting",
    "create_tables": "creating tables in",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Decision:
    """What a `BoundPolicy` decided about a request, and why. It holds no values from the request,
    so it can be logged. A `Rule` of your own returns one to refuse a request.

    Args:
        allowed: Whether the request may be sent.
        verb: What was asked, such as `update`, for the first part refused, or the last allowed.
        layer: Which check decided: `policy` for the rules, `rows` for a row limit, `derived`
            for what a formula, lookup, or summary reads, and `confirmation` for a preview or
            plan's confirmation.
        rule: The rule that decided, as written, such as `deny.all(fields.classed("pay"))`, or
            None when nothing allowed it.
        reason: Why, in a sentence.
        hint: What would allow it, or None when nothing should, such as for a deny.
        table: The table it's about, if any.
        fields: The fields it's about, if any.
    """

    allowed: bool
    verb: Verb
    layer: Layer = "policy"
    rule: str | None = None
    reason: str = ""
    hint: str | None = None
    table: Named | None = None
    fields: tuple[Named, ...] = ()

    @property
    def what(self) -> str:
        """What was asked, by name, such as `updating Labor Cost (6) in Job Tasks (bqtasks)`."""
        verb = _VERB_TEXT.get(self.verb, self.verb)
        names = ", ".join(str(item) for item in self.fields)
        table = "" if self.table is None else str(self.table)
        if self.verb == "create_tables":
            return f"{verb} app {table}" if table else "creating tables"
        if names and table:
            return f"{verb} {names} in {table}"
        return f"{verb} {names or table or 'this request'}"

    def message(self) -> str:
        """The refusal's message, in the form `PolicyRefusedError` uses."""
        text = f"Refused by {self.layer}: {self.what}. {self.reason.rstrip('.')}."
        text += f" (rule: {self.rule or 'none'}) Nothing was sent."
        if self.hint:
            text += f" To allow it: {self.hint.rstrip('.')}."
        return text
