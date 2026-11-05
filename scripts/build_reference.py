"""Write the two-column API reference pages, ready for `mkdocs build`.

uv run scripts/build_reference.py

Each resource gets `docs/reference/<name>.md` for the site, and `docs/reference/<name>.txt` as plain
Markdown for AI tools and for copying. Signatures and text come from the SDK, and examples from
`docs_src/reference/`, where each is written for the async client and the sync version derived from
it. tests/live/test_reference_examples.py runs them and checks their output. Like build_llms.py,
this uses only the standard library and `inspect`, so it doesn't depend on which tool builds the
site. None of what it writes is committed.

The build stops if a public method or argument has no description, if a method has no example, or
if the clients differ. Only Table is converted so far.
"""

import inspect
import re
from pathlib import Path
from typing import Any, NamedTuple

import build_llms

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "reference"
EXAMPLES = ROOT / "docs_src" / "reference" / "table"
MAIN_REFERENCE = "../reference.md"

SETUP = """\
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
"""
ASYNC_SETUP = """\
from pyquickbase import AsyncQuickbase

async with AsyncQuickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Job Tasks")
"""
INTRO = (
    "A `Table` reads and writes one table's records and changes its fields. Get one from "
    "`App.table()`, by name or ID. Getting one doesn't contact Quickbase, so a table that doesn't "
    "exist raises `UnknownTableError` on first use."
)
EXAMPLE_NOTE = (
    "The examples below use `tasks`, made as shown here. `Job Tasks` has `Title`, `Status`, "
    "`Hours`, `Due Date`, `Code`, which is unique, and `Attachment`. It starts with three records, "
    "and each example starts from those again. A live test runs every example with both clients "
    "and checks its output. In the async client, `AsyncTable` has the same methods, which you "
    "`await`, but `query()` and `report()` are called without it."
)


class Group(NamedTuple):
    title: str
    anchor: str
    guide: tuple[str, str] | None
    members: tuple[str, ...]


GROUPS = (
    Group(
        "Read records",
        "read-records",
        ("Reading records", "../guides/reading.md"),
        ("query", "query_page", "first", "get", "count"),
    ),
    Group(
        "Write records",
        "write-records",
        ("Writing records", "../guides/writing.md"),
        ("insert", "update", "upsert", "delete"),
    ),
    Group(
        "Preview writes",
        "preview-writes",
        ("Writing records", "../guides/writing.md"),
        ("preview_update", "preview_delete"),
    ),
    Group(
        "Summarize and calculate",
        "summarize-and-calculate",
        ("Formulas", "../guides/formulas.md"),
        ("summarize", "run_formula"),
    ),
    Group(
        "Look up the table's details",
        "table-details",
        ("Reading records", "../guides/reading.md"),
        ("info", "fields", "relationships", "reports", "report", "resolve_field_ids"),
    ),
    Group(
        "Work with files",
        "files",
        ("File attachments", "../guides/files.md"),
        ("download_file", "delete_file_version"),
    ),
    Group(
        "Change the schema",
        "schema",
        ("Changing the schema", "../guides/schema.md"),
        (
            "create_field",
            "update_field",
            "delete_fields",
            "ensure_field",
            "create_relationship",
            "ensure_relationship",
            "ensure_lookup_field",
            "ensure_summary_field",
            "drop",
        ),
    ),
)

_SECTIONS = {"Args:": "args", "Returns:": "returns", "Raises:": "raises"}
_ENTRY = re.compile(r"(\w+): (.*)")
_NAME = re.compile(r"\b[A-Za-z_]\w*\b")
_CALL = re.compile(r"`(\w+)\(\)`")
_SEE = re.compile(r"See `(\w+)`")
_PUBLIC_CODE = re.compile(r"`([A-Z]\w*)`")
_SYNC_WORDS = (("await ", ""), ("async for ", "for "), ("async with ", "with "))


class Docstring(NamedTuple):
    summary: str
    prose: list[str]
    args: dict[str, str]
    returns: str
    raises: list[tuple[str, str]]


class Parameter(NamedTuple):
    name: str
    annotation: str
    default: str | None
    required: bool
    description: str


class Method(NamedTuple):
    name: str
    signature: str
    returns: str
    doc: Docstring
    parameters: list[Parameter]
    sync_code: str
    async_code: str
    output: str


def to_sync(code: str) -> str:
    """Turn an example for the async client into the same example for the sync client."""
    for async_text, sync_text in _SYNC_WORDS:
        code = code.replace(async_text, sync_text)
    return code


def parse_docstring(text: str | None, owner: str) -> Docstring:
    """Split a Google-style docstring into its parts."""
    if not text or not text.strip():
        raise SystemExit(f"{owner} has no description.")
    prose: list[str] = []
    parts: dict[str, list[str]] = {"args": [], "returns": [], "raises": []}
    section: str | None = None
    for line in text.splitlines():
        if line.strip() in _SECTIONS:
            section = _SECTIONS[line.strip()]
        elif section is None or (line.strip() and not line.startswith(" ")):
            section = None
            prose.append(line)
        elif line.strip():
            parts[section].append(line)
    paragraphs = [item.replace("\n", " ") for item in "\n".join(prose).strip().split("\n\n")]
    return Docstring(
        paragraphs[0],
        paragraphs[1:],
        dict(_entries(parts["args"], owner)),
        " ".join(line.strip() for line in parts["returns"]),
        _entries(parts["raises"], owner),
    )


def _entries(lines: list[str], owner: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in lines:
        if line.startswith("        "):
            name, text = entries[-1]
            entries[-1] = (name, f"{text} {line.strip()}")
            continue
        entry = _ENTRY.fullmatch(line.strip())
        if entry is None:
            raise SystemExit(f"Can't read this line of the docstring of {owner}: {line!r}")
        entries.append((entry[1], entry[2]))
    return entries


def _annotation(parameter: inspect.Parameter) -> str:
    return build_llms.clean_types(inspect.formatannotation(parameter.annotation))


def _parameters(function: Any, doc: Docstring, owner: str) -> list[Parameter]:
    found = [p for p in inspect.signature(function).parameters.values() if p.name != "self"]
    unknown = set(doc.args) - {parameter.name for parameter in found}
    if unknown:
        raise SystemExit(f"The docstring of {owner} describes unknown arguments: {sorted(unknown)}")
    return [
        Parameter(
            parameter.name,
            _annotation(parameter),
            None if parameter.default is inspect.Parameter.empty else repr(parameter.default),
            parameter.default is inspect.Parameter.empty,
            doc.args.get(parameter.name, ""),
        )
        for parameter in found
    ]


def _return_type(function: Any) -> str:
    annotation = inspect.signature(function).return_annotation
    return build_llms.clean_types(inspect.formatannotation(annotation))


def _signature(name: str, function: Any) -> str:
    """Write a signature on one line if it fits in the column, or else one parameter per line."""
    parameters = [p for p in inspect.signature(function).parameters.values() if p.name != "self"]
    items: list[str] = []
    for index, parameter in enumerate(parameters):
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY and (
            index == 0 or parameters[index - 1].kind is not inspect.Parameter.KEYWORD_ONLY
        ):
            items.append("*")
        default = (
            "" if parameter.default is inspect.Parameter.empty else f" = {parameter.default!r}"
        )
        items.append(f"{parameter.name}: {_annotation(parameter)}{default}")
    returns = _return_type(function)
    one_line = f"{name}({', '.join(items)}) -> {returns}"
    if len(one_line) <= 60:
        return one_line
    body = "".join(f"    {item},\n" for item in items)
    return f"{name}(\n{body}) -> {returns}"


def _check_clients_match() -> None:
    def members(cls: type) -> dict[str, list[str]]:
        return {
            name: list(inspect.signature(member).parameters)
            for name, member in vars(cls).items()
            if inspect.isfunction(member) and not name.startswith("_")
        }

    if members(pyquickbase.Table) != members(pyquickbase.AsyncTable):
        raise SystemExit("Table and AsyncTable no longer have the same methods and arguments.")


def _check_groups() -> None:
    public = {
        name
        for name, member in vars(pyquickbase.Table).items()
        if inspect.isfunction(member) and not name.startswith("_")
    }
    listed = [name for group in GROUPS for name in group.members]
    if len(listed) != len(set(listed)) or set(listed) != public:
        raise SystemExit(
            "GROUPS doesn't match Table's public methods: "
            f"{sorted(public.symmetric_difference(listed))}"
        )


def read_method(name: str) -> Method:
    function = vars(pyquickbase.Table)[name]
    owner = f"Table.{name}"
    doc = parse_docstring(inspect.getdoc(function), owner)
    example = EXAMPLES / f"{name}.py"
    if not example.exists():
        raise SystemExit(f"{owner} has no example. Add {example.relative_to(ROOT)}.")
    async_code = example.read_text(encoding="utf-8").strip()
    output = example.with_suffix(".out")
    return Method(
        name,
        _signature(name, function),
        _return_type(function),
        doc,
        _parameters(function, doc, owner),
        to_sync(async_code),
        async_code,
        output.read_text(encoding="utf-8").strip() if output.exists() else "",
    )


def read_methods() -> dict[str, Method]:
    _check_clients_match()
    _check_groups()
    methods = {name: read_method(name) for group in GROUPS for name in group.members}
    missing = missing_descriptions(methods)
    if missing:
        raise SystemExit(f"These arguments have no description: {', '.join(missing)}")
    expected = {f"{name}.py" for name in methods}
    extra = {path.name for path in EXAMPLES.glob("*.py")} - expected
    if extra:
        raise SystemExit(f"These examples aren't for a public method: {sorted(extra)}")
    return methods


def missing_descriptions(methods: dict[str, Method]) -> list[str]:
    """List the arguments whose docstrings don't describe them."""
    return [
        f"{method.name}({parameter.name})"
        for method in methods.values()
        for parameter in method.parameters
        if not parameter.description
    ]


def _public_link(name: str) -> str:
    return f"[`{name}`]({MAIN_REFERENCE}#pyquickbase.{name})"


def _add_links(text: str, methods: dict[str, Method]) -> str:
    """Link what a sentence names. A bare method name may just be a word, so it needs `()`."""

    def call(found: re.Match[str]) -> str:
        return f"[`{found[1]}()`](#{found[1]})" if found[1] in methods else found[0]

    def see(found: re.Match[str]) -> str:
        return f"See [`{found[1]}()`](#{found[1]})" if found[1] in methods else found[0]

    def public(found: re.Match[str]) -> str:
        return _public_link(found[1]) if found[1] in pyquickbase.__all__ else found[0]

    return _PUBLIC_CODE.sub(public, _SEE.sub(see, _CALL.sub(call, text)))


def _linked_type(annotation: str) -> str:
    """Write a type with its public names linked, as in `list[` `Field` `]`."""
    pieces: list[str] = []
    plain = ""
    for piece in re.split(r"(\b[A-Z]\w*\b)", annotation):
        if piece in pyquickbase.__all__:
            if plain:
                pieces.append(f"`{plain.strip()}`")
                plain = ""
            pieces.append(_public_link(piece))
        else:
            plain += piece
    if plain.strip():
        pieces.append(f"`{plain.strip()}`")
    return " ".join(pieces)


def _parameter_note(parameter: Parameter) -> str:
    if parameter.required:
        return "required"
    return "optional" if parameter.default == "None" else f"default `{parameter.default}`"


def _describe(method: Method, methods: dict[str, Method]) -> list[str]:
    """Write a method's description, parameters, return value, and errors as Markdown."""
    doc = method.doc
    lines = [_add_links(doc.summary, methods), ""]
    for paragraph in doc.prose:
        lines += [_add_links(paragraph, methods), ""]
    lines += [f"```python\n{method.signature}\n```", ""]
    if method.parameters:
        lines += ["**Parameters**", ""]
        for parameter in method.parameters:
            note = _parameter_note(parameter)
            head = f"- **`{parameter.name}`** `{parameter.annotation}`, {note}"
            text = _add_links(parameter.description, methods)
            # Two spaces make a line break, so the description starts below the name and type.
            lines.append(f"{head}  \n  {text}" if text else head)
        lines.append("")
    if method.returns != "None" or doc.returns:
        lines += [f"**Returns** {_linked_type(method.returns)}", ""]
        if doc.returns:
            lines += [_add_links(doc.returns, methods), ""]
    if doc.raises:
        lines += ["**Raises**", ""]
        for name, text in doc.raises:
            error = _public_link(name) if name in pyquickbase.__all__ else f"`{name}`"
            lines.append(f"- {error}: {_add_links(text, methods)}")
        lines.append("")
    return lines


def _indented(code: str, language: str = "python") -> list[str]:
    fenced = f"```{language}\n{code}\n```"
    return [f"    {line}" for line in fenced.splitlines()]


def _tabs(sync_code: str, async_code: str) -> list[str]:
    return [
        '=== "Sync"',
        "",
        *_indented(sync_code),
        "",
        '=== "Async"',
        "",
        *_indented(async_code),
        "",
    ]


def _columns(doc: list[str], code: list[str]) -> list[str]:
    return [
        '<div class="ref-row" markdown>',
        '<div class="ref-doc" markdown>',
        "",
        *doc,
        "</div>",
        '<div class="ref-code" markdown>',
        "",
        *code,
        "</div>",
        "</div>",
        "",
    ]


def _returned_types(methods: dict[str, Method]) -> dict[str, list[str]]:
    returned: dict[str, list[str]] = {}
    for method in methods.values():
        for name in _NAME.findall(method.returns):
            if name in pyquickbase.__all__:
                returned.setdefault(name, []).append(method.name)
    return returned


def _return_table(methods: dict[str, Method]) -> list[str]:
    """Write a table of what the methods return, with each type's own first line."""
    rows = ["| Type | What it is | Returned by |", "|---|---|---|"]
    for name, callers in sorted(_returned_types(methods).items()):
        summary = (inspect.getdoc(getattr(pyquickbase, name)) or "").split("\n\n")[0]
        summary = " ".join(summary.split()).replace("|", "\\|")
        linked = ", ".join(f"[`{caller}()`](#{caller})" for caller in callers)
        rows.append(f"| {_public_link(name)} | {summary} | {linked} |")
    return [*rows, ""]


def _guide_line(group: Group) -> list[str]:
    if group.guide is None:
        return []
    return [f"For the full picture, see [{group.guide[0]}]({group.guide[1]}).", ""]


def build_page(methods: dict[str, Method]) -> str:
    """Return the page for the site, with two columns and a tab for each client."""
    lines = [
        "---",
        "hide:",
        "  - navigation",
        "---",
        "",
        "# Table",
        "",
        '<p class="ref-links" markdown>'
        "[All of the API reference](../reference.md)"
        '<a class="ref-markdown" href="../table.txt">View as Markdown</a>'
        '<button class="ref-copy" type="button" hidden>Copy for an LLM</button>'
        "</p>",
        "",
        *_columns(
            [_add_links(INTRO, methods), "", _add_links(EXAMPLE_NOTE, methods), ""],
            _tabs(SETUP.strip(), ASYNC_SETUP.strip()),
        ),
        "## What the methods return {#returns}",
        "",
        *_return_table(methods),
    ]
    for group in GROUPS:
        lines += [f"## {group.title} {{#{group.anchor}}}", "", *_guide_line(group)]
        for name in group.members:
            method = methods[name]
            heading = [f"### `{name}()` {{#{name}}}", ""]
            code = _tabs(method.sync_code, method.async_code)
            if method.output:
                code += ['<p class="ref-output-label">Output</p>', ""]
                code += [f"```text\n{method.output}\n```", ""]
            lines += _columns(heading + _describe(method, methods), code)
    return "\n".join(lines)


def build_markdown(methods: dict[str, Method], site_url: str) -> str:
    """Return the same content as one plain Markdown file, with both clients' code written out."""
    lines = [
        "# pyquickbase API reference: Table",
        "",
        f"This describes version {pyquickbase.__version__}. "
        f"[The full reference]({site_url}reference/) lists every public name.",
        "",
        _add_links(INTRO, methods),
        "",
        _add_links(EXAMPLE_NOTE, methods),
        "",
        f"Sync:\n\n```python\n{SETUP.strip()}\n```",
        "",
        f"Async:\n\n```python\n{ASYNC_SETUP.strip()}\n```",
        "",
        "## What the methods return",
        "",
        *_return_table(methods),
    ]
    for group in GROUPS:
        lines += [f"## {group.title}", "", *_guide_line(group)]
        for name in group.members:
            method = methods[name]
            lines += [f"### `Table.{name}()`", "", *_describe(method, methods)]
            lines += [f"Sync example:\n\n```python\n{method.sync_code}\n```", ""]
            lines += [f"Async example:\n\n```python\n{method.async_code}\n```", ""]
            if method.output:
                lines += [f"Output:\n\n```text\n{method.output}\n```", ""]
    text = build_llms.rewrite_links("\n".join(lines), "reference/table.md", site_url)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def main() -> None:
    config = build_llms.read_config()
    methods = read_methods()
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "table.md").write_text(build_page(methods), encoding="utf-8")
    (OUTPUT / "table.txt").write_text(build_markdown(methods, config.site_url), encoding="utf-8")
    print("Wrote docs/reference/table.md and docs/reference/table.txt")


if __name__ == "__main__":
    main()
