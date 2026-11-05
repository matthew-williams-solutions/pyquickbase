"""Write the two-column API reference pages, ready for `mkdocs build`.

uv run scripts/build_reference.py

Each resource gets `docs/reference/<name>.md` for the site, and `docs/reference/<name>.txt` as plain
Markdown for AI tools and for copying. Signatures and text come from the SDK, and examples from
`docs_src/reference/`, where each is written for the async client and the sync version derived from
it. tests/live/test_reference_examples.py runs them and checks their output. Like build_llms.py,
this uses only the standard library and `inspect`, so it doesn't depend on which tool builds the
site. None of what it writes is committed.

The build stops if a public method or argument has no description, if a method has no example, or
if the clients differ. Quickbase, App, and Table are converted so far.
"""

import inspect
import re
from pathlib import Path
from typing import Any, NamedTuple

import build_llms

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "reference"
EXAMPLES = ROOT / "docs_src" / "reference"
MAIN_REFERENCE = "../reference.md"
CONSTRUCTOR = "__init__"
# Methods that the async client names differently.
ASYNC_NAMES = {"close": "aclose"}


class Group(NamedTuple):
    title: str
    anchor: str
    guide: tuple[str, str] | None
    members: tuple[str, ...]


TABLE_GROUPS = (
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


def _setup(last_line: str) -> tuple[str, str]:
    """Return the setup code for each client, narrow enough for the code column."""
    body = f'    realm="acme", token="your_user_token"\n) as qb:\n    {last_line}'
    return (
        f"from pyquickbase import Quickbase\n\nwith Quickbase(\n{body}",
        f"from pyquickbase import AsyncQuickbase\n\nasync with AsyncQuickbase(\n{body}",
    )


class Resource(NamedTuple):
    name: str
    slug: str
    sync_class: type
    async_class: type
    intro: str
    note: str
    setup: str
    async_setup: str
    groups: tuple[Group, ...]


RESOURCES = (
    Resource(
        "Quickbase",
        "quickbase",
        pyquickbase.Quickbase,
        pyquickbase.AsyncQuickbase,
        "`Quickbase` is the client that everything else starts from. Use it as a context manager, "
        "so that its connections get closed. `AsyncQuickbase` takes the same options, except that "
        "`http_client` is an `httpx.AsyncClient`, and has the same methods, which you `await` "
        "where the examples show it.",
        "The examples below use `qb`, made as shown here. A live test runs every example with "
        "both clients and checks its output.",
        *_setup("print(qb.realm)"),
        (
            Group(
                "Create and close a client",
                "create-and-close",
                ("Configuring clients", "../guides/clients.md"),
                (CONSTRUCTOR, "from_env", "close"),
            ),
            Group(
                "Use a client",
                "use-a-client",
                ("Configuring clients", "../guides/clients.md"),
                ("app", "realm", "clear_schema_cache", "request"),
            ),
        ),
    ),
    Resource(
        "App",
        "app",
        pyquickbase.App,
        pyquickbase.AsyncApp,
        "An `App` lists an app's details and gives you its tables. Get one from "
        "`Quickbase.app()`. Getting one doesn't contact Quickbase, so an ID that doesn't exist "
        "only fails on first use.",
        "The examples below use `app`, made as shown here, in an app that has a table named "
        "`Job Tasks` with three records. A live test runs every example with both clients and "
        "checks its output. `AsyncApp` has the same methods, which you `await`, but `table()` and "
        "`typed()` are called without it.",
        *_setup('app = qb.app("bq1234567")'),
        (
            Group(
                "Look up the app's details",
                "app-details",
                ("Reading records", "../guides/reading.md"),
                ("id", "info", "tables", "roles"),
            ),
            Group(
                "Get a table",
                "get-a-table",
                ("Generated classes", "../guides/codegen.md"),
                ("table", "typed"),
            ),
            Group(
                "Create tables",
                "create-tables",
                ("Changing the schema", "../guides/schema.md"),
                ("ensure_table", "create_table"),
            ),
        ),
    ),
    Resource(
        "Table",
        "table",
        pyquickbase.Table,
        pyquickbase.AsyncTable,
        "A `Table` reads and writes one table's records and changes its fields. Get one from "
        "`App.table()`, by name or ID. Getting one doesn't contact Quickbase, so a table that "
        "doesn't exist raises `UnknownTableError` on first use.",
        "The examples below use `tasks`, made as shown here. `Job Tasks` has `Title`, `Status`, "
        "`Hours`, `Due Date`, `Code`, which is unique, and `Attachment`. It starts with three "
        "records, and each example starts from those again. A live test runs every example with "
        "both clients and checks its output. In the async client, `AsyncTable` has the same "
        "methods, which you `await`, but `query()` and `report()` are called without it.",
        *_setup('tasks = qb.app("bq1234567").table("Job Tasks")'),
        TABLE_GROUPS,
    ),
)
PAGES = {resource.name: f"{resource.slug}.md" for resource in RESOURCES}

_SECTIONS = {"Args:": "args", "Returns:": "returns", "Raises:": "raises"}
_ENTRY = re.compile(r"(\w+): (.*)")
_NAME = re.compile(r"\b[A-Za-z_]\w*\b")
_CALL = re.compile(r"`(\w+)\(\)`")
_SEE = re.compile(r"See `(\w+)`")
_PUBLIC_CODE = re.compile(r"`([A-Z]\w*)`")
_QUALIFIED = re.compile(r"`(" + "|".join(PAGES) + r")\.(\w+)\(\)`")
_SYNC_WORDS = (
    ("await ", ""),
    ("async for ", "for "),
    ("async with ", "with "),
    ("AsyncQuickbase", "Quickbase"),
    (".aclose(", ".close("),
)


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
    anchor: str
    heading: str
    is_property: bool
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


def _own_parameters(function: Any) -> list[inspect.Parameter]:
    """Return a function's parameters, without `self` or `cls`."""
    found = inspect.signature(function).parameters.values()
    return [parameter for parameter in found if parameter.name not in ("self", "cls")]


def _parameters(
    function: Any, doc: Docstring, owner: str, inherited: dict[str, str] | None = None
) -> list[Parameter]:
    found = _own_parameters(function)
    unknown = set(doc.args) - {parameter.name for parameter in found}
    if unknown:
        raise SystemExit(f"The docstring of {owner} describes unknown arguments: {sorted(unknown)}")
    known = {**(inherited or {}), **doc.args}
    return [
        Parameter(
            parameter.name,
            _annotation(parameter),
            None if parameter.default is inspect.Parameter.empty else repr(parameter.default),
            parameter.default is inspect.Parameter.empty,
            known.get(parameter.name, ""),
        )
        for parameter in found
    ]


def _return_type(function: Any, owner_class: str) -> str:
    annotation = inspect.signature(function).return_annotation
    text = build_llms.clean_types(inspect.formatannotation(annotation))
    return owner_class if text == "Self" else text


def _signature(name: str, function: Any, returns: str) -> str:
    """Write a signature on one line if it fits in the column, or else one parameter per line."""
    parameters = _own_parameters(function)
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
    suffix = f" -> {returns}" if returns else ""
    one_line = f"{name}({', '.join(items)}){suffix}"
    if len(one_line) <= 60:
        return one_line
    body = "".join(f"    {item},\n" for item in items)
    return f"{name}(\n{body}){suffix}"


def _public_members(cls: type) -> dict[str, Any]:
    """Return a class's public methods and properties, by name."""
    return {
        name: member
        for name, member in vars(cls).items()
        if not name.startswith("_")
        and (inspect.isfunction(member) or isinstance(member, property | classmethod))
    }


def _function(member: Any) -> Any:
    """Return what holds a member's signature and docstring."""
    return member.fget if isinstance(member, property) else getattr(member, "__func__", member)


def _shape(cls: type, *, names: dict[str, str]) -> dict[str, list[str]]:
    return {
        names.get(name, name): [p.name for p in _own_parameters(_function(member))]
        for name, member in _public_members(cls).items()
    }


def _check_clients_match(resource: Resource) -> None:
    expected = _shape(resource.sync_class, names=ASYNC_NAMES)
    if expected != _shape(resource.async_class, names={}):
        raise SystemExit(
            f"{resource.sync_class.__name__} and {resource.async_class.__name__} no longer have "
            "the same methods and arguments."
        )
    if any(group for group in resource.groups if CONSTRUCTOR in group.members):
        own = [p.name for p in _own_parameters(resource.sync_class)]
        other = [p.name for p in _own_parameters(resource.async_class)]
        if own != other:
            raise SystemExit(f"The constructors of {resource.name} differ between clients.")


def _check_groups(resource: Resource) -> None:
    listed = [name for group in resource.groups for name in group.members]
    public = set(_public_members(resource.sync_class))
    wanted = [name for name in listed if name != CONSTRUCTOR]
    if len(listed) != len(set(listed)) or set(wanted) != public:
        raise SystemExit(
            f"The groups of {resource.name} don't match its public methods: "
            f"{sorted(public.symmetric_difference(wanted))}"
        )


def read_example(resource: Resource, stem: str, owner: str) -> tuple[str, str]:
    example = EXAMPLES / resource.slug / f"{stem}.py"
    if not example.exists():
        raise SystemExit(f"{owner} has no example. Add {example.relative_to(ROOT)}.")
    output = example.with_suffix(".out")
    return (
        example.read_text(encoding="utf-8").strip(),
        output.read_text(encoding="utf-8").strip() if output.exists() else "",
    )


def read_method(resource: Resource, name: str, inherited: dict[str, str]) -> Method:
    cls = resource.sync_class
    owner = f"{resource.name}.{name}"
    if name == CONSTRUCTOR:
        doc = parse_docstring(inspect.getdoc(cls), resource.name)
        function: Any = cls.__init__
        async_code, output = read_example(resource, "constructor", resource.name)
        return Method(
            resource.name,
            "constructor",
            f"{resource.name}()",
            False,
            _signature(resource.name, function, ""),
            "None",
            doc,
            _parameters(function, doc, resource.name),
            to_sync(async_code),
            async_code,
            output,
        )
    member = vars(cls)[name]
    function = _function(member)
    doc = parse_docstring(inspect.getdoc(function), owner)
    returns = _return_type(function, resource.name)
    async_code, output = read_example(resource, name, owner)
    is_property = isinstance(member, property)
    return Method(
        name,
        name,
        name if is_property else f"{name}()",
        is_property,
        f"{name}: {returns}" if is_property else _signature(name, function, returns),
        returns,
        doc,
        _parameters(function, doc, owner, inherited),
        to_sync(async_code),
        async_code,
        output,
    )


def read_methods(resource: Resource) -> dict[str, Method]:
    _check_clients_match(resource)
    _check_groups(resource)
    # Options that from_env() shares with the constructor are described once, on the constructor.
    listed = [name for group in resource.groups for name in group.members]
    inherited = (
        parse_docstring(inspect.getdoc(resource.sync_class), resource.name).args
        if CONSTRUCTOR in listed
        else {}
    )
    methods = {name: read_method(resource, name, inherited) for name in listed}
    missing = missing_descriptions(methods)
    if missing:
        raise SystemExit(f"These arguments have no description: {', '.join(missing)}")
    stems = {"constructor" if name == CONSTRUCTOR else name for name in methods}
    extra = {path.stem for path in (EXAMPLES / resource.slug).glob("*.py")} - stems
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
    if name in PAGES:
        return f"[`{name}`]({PAGES[name]})"
    return f"[`{name}`]({MAIN_REFERENCE}#pyquickbase.{name})"


def _add_links(text: str, methods: dict[str, Method]) -> str:
    """Link what a sentence names. A bare method name may just be a word, so it needs `()`."""

    def qualified(found: re.Match[str]) -> str:
        cls = getattr(pyquickbase, found[1])
        if found[2] not in vars(cls):
            raise SystemExit(f"This text names a method that doesn't exist: {found[0]}")
        return f"[`{found[1]}.{found[2]}()`]({PAGES[found[1]]}#{found[2]})"

    def call(found: re.Match[str]) -> str:
        return f"[`{found[1]}()`](#{found[1]})" if found[1] in methods else found[0]

    def see(found: re.Match[str]) -> str:
        return f"See [`{found[1]}()`](#{found[1]})" if found[1] in methods else found[0]

    def public(found: re.Match[str]) -> str:
        return _public_link(found[1]) if found[1] in pyquickbase.__all__ else found[0]

    text = _QUALIFIED.sub(qualified, text)
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
    if method.name in ASYNC_NAMES:
        lines += [f"The async client calls this `{ASYNC_NAMES[method.name]}()`.", ""]
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
    if not method.is_property and (method.returns != "None" or doc.returns):
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
        if method.anchor == "constructor":
            continue
        for name in _NAME.findall(method.returns):
            if name in pyquickbase.__all__:
                returned.setdefault(name, []).append(method.heading)
    return returned


def _return_table(methods: dict[str, Method]) -> list[str]:
    """Write a table of what the methods return, with each type's own first line."""
    rows = ["| Type | What it is | Returned by |", "|---|---|---|"]
    by_heading = {method.heading: method.anchor for method in methods.values()}
    for name, callers in sorted(_returned_types(methods).items()):
        summary = (inspect.getdoc(getattr(pyquickbase, name)) or "").split("\n\n")[0]
        summary = " ".join(summary.split()).replace("|", "\\|")
        linked = ", ".join(f"[`{caller}`](#{by_heading[caller]})" for caller in callers)
        rows.append(f"| {_public_link(name)} | {summary} | {linked} |")
    return [*rows, ""]


def _guide_line(group: Group) -> list[str]:
    if group.guide is None:
        return []
    return [f"For the full picture, see [{group.guide[0]}]({group.guide[1]}).", ""]


def _page_links(resource: Resource) -> str:
    """Link to the other reference pages. This is plain Markdown, so it suits any docs tool."""
    links = [
        f"**{item.name}**" if item is resource else f"[{item.name}]({item.slug}.md)"
        for item in RESOURCES
    ]
    return " ".join(['<p class="ref-links ref-links--pages" markdown>', *links, "</p>"])


def build_page(resource: Resource, methods: dict[str, Method]) -> str:
    """Return the page for the site, with two columns and a tab for each client."""
    lines = [
        "---",
        "hide:",
        "  - navigation",
        "---",
        "",
        f"# {resource.name}",
        "",
        _page_links(resource),
        "",
        '<p class="ref-links" markdown>'
        "[All public names](../reference.md)"
        f'<a class="ref-markdown" href="../{resource.slug}.txt">View as Markdown</a>'
        '<button class="ref-copy" type="button" hidden>Copy for an LLM</button>'
        "</p>",
        "",
        *_columns(
            [_add_links(resource.intro, methods), "", _add_links(resource.note, methods), ""],
            _tabs(resource.setup, resource.async_setup),
        ),
        "## What the methods return {#returns}",
        "",
        *_return_table(methods),
    ]
    for group in resource.groups:
        lines += [f"## {group.title} {{#{group.anchor}}}", "", *_guide_line(group)]
        for name in group.members:
            method = methods[name]
            heading = [f"### `{method.heading}` {{#{method.anchor}}}", ""]
            code = _tabs(method.sync_code, method.async_code)
            if method.output:
                code += ['<p class="ref-output-label">Output</p>', ""]
                code += [f"```text\n{method.output}\n```", ""]
            lines += _columns(heading + _describe(method, methods), code)
    return "\n".join(lines)


def _qualified_heading(resource: Resource, method: Method) -> str:
    if method.anchor == "constructor":
        return method.heading
    return f"{resource.name}.{method.heading}"


def build_markdown(resource: Resource, methods: dict[str, Method], site_url: str) -> str:
    """Return the same content as one plain Markdown file, with both clients' code written out."""
    others = ", ".join(
        f"[{item.name}]({site_url}reference/{item.slug}.txt)"
        for item in RESOURCES
        if item is not resource
    )
    lines = [
        f"# pyquickbase API reference: {resource.name}",
        "",
        f"This describes version {pyquickbase.__version__}. "
        f"[The full reference]({site_url}reference/) lists every public name. "
        f"Other classes: {others}.",
        "",
        _add_links(resource.intro, methods),
        "",
        _add_links(resource.note, methods),
        "",
        f"Sync:\n\n```python\n{resource.setup}\n```",
        "",
        f"Async:\n\n```python\n{resource.async_setup}\n```",
        "",
        "## What the methods return",
        "",
        *_return_table(methods),
    ]
    for group in resource.groups:
        lines += [f"## {group.title}", "", *_guide_line(group)]
        for name in group.members:
            method = methods[name]
            heading = _qualified_heading(resource, method)
            lines += [f"### `{heading}`", "", *_describe(method, methods)]
            lines += [f"Sync example:\n\n```python\n{method.sync_code}\n```", ""]
            lines += [f"Async example:\n\n```python\n{method.async_code}\n```", ""]
            if method.output:
                lines += [f"Output:\n\n```text\n{method.output}\n```", ""]
    page = f"reference/{resource.slug}.md"
    text = build_llms.rewrite_links("\n".join(lines), page, site_url)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def main() -> None:
    config = build_llms.read_config()
    OUTPUT.mkdir(exist_ok=True)
    for resource in RESOURCES:
        methods = read_methods(resource)
        (OUTPUT / f"{resource.slug}.md").write_text(build_page(resource, methods), encoding="utf-8")
        (OUTPUT / f"{resource.slug}.txt").write_text(
            build_markdown(resource, methods, config.site_url), encoding="utf-8"
        )
        print(f"Wrote docs/reference/{resource.slug}.md and docs/reference/{resource.slug}.txt")


if __name__ == "__main__":
    main()
