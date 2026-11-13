"""Link the first mention of each public name or method in each section of the hand-written docs.

uv run scripts/link_names.py          # rewrite the pages
uv run scripts/link_names.py --check  # fail if a page has a mention that should be linked

A mention is inline code naming something the API reference documents: a public name such as
`WriteResult`, a method as `Table.upsert()` or on a variable the docs use for one, such as
`tasks.upsert()`, or a method name that only one class has, such as `upsert()`. Within each
section, between `##` headings, only the first mention of each thing is linked, and a link already
there counts as that mention. Code blocks, headings, and text already inside a link are left alone.
The links are written into the pages, so they work wherever the Markdown is read, and a test checks
that the pages are up to date.
"""

import posixpath
import re
import sys

import build_llms
import build_reference

DOCS = build_llms.DOCS
# Pages left alone: the changelog records what was true then, and the playground has no prose.
LEFT_OUT = {"changelog.md", "playground.md"}
# What the docs call an instance of each class in their examples. `page` is left out, as the
# guides use it for a page of records as often as for a code page.
VARIABLES = {
    "qb": "Quickbase",
    "app": "App",
    "tasks": "Table",
    "table": "Table",
    "projects": "Table",
    "report": "Report",
    "policy": "Policy",
    "bound": "BoundPolicy",
}

# Public names that are also common argument names, such as `limit=`, so they're only linked as
# `limit.rows()`.
AMBIGUOUS = {"limit", "fields", "tables"}

_FENCE = re.compile(r"^\s*(```|~~~)")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_CODE = re.compile(r"`([^`\n]+)`")
_MENTION = re.compile(r"^(?:(\w+)\.)?(\w+)(\(\))?$")


def _targets() -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """Return where each method is, by `(owner, name)`, and each method name only one owner has."""
    methods = dict(build_reference.METHOD_LINKS)
    reference = build_reference.read_reference()
    namespaced: set[tuple[str, str]] = set()
    for symbol in reference.symbols.values():
        for method in symbol.methods:
            slug = build_reference.NAME_LINKS[symbol.name].partition("#")[0]
            methods[(symbol.name, method.name)] = f"{slug}#{method.anchor}"
            if build_reference.is_namespace(symbol):
                namespaced.add((symbol.name, method.name))
    for source in build_reference.CLASSES:
        for (owner, name), link in list(methods.items()):
            if owner == source.name:
                methods[(source.async_class.__name__, name)] = link
    owners: dict[str, set[str]] = {}
    # A namespace's functions, such as `allow.insert()`, are always written with it, so they don't
    # make `insert()` ambiguous.
    for owner, name in methods:
        if name != build_reference.CONSTRUCTOR and (owner, name) not in namespaced:
            owners.setdefault(name, set()).add(owner.removeprefix("Async"))
    unique = {
        name: methods[(next(iter(found)), name)]
        for name, found in owners.items()
        if len(found) == 1 and name not in build_reference.NAME_LINKS
    }
    return methods, unique


METHODS, UNIQUE_METHODS = _targets()


def target_of(code: str) -> str | None:
    """Return where the reference documents what a piece of inline code names, if it does."""
    found = _MENTION.match(code)
    if found is None:
        return None
    owner, name, call = found[1], found[2], found[3]
    if owner is None:
        if name in build_reference.NAME_LINKS:
            return None if name in AMBIGUOUS else build_reference.NAME_LINKS[name]
        return UNIQUE_METHODS.get(name) if call else None
    owner = VARIABLES.get(owner, owner)
    return METHODS.get((owner, name))


def _relative(target: str, page: str) -> str:
    path, _, anchor = target.partition("#")
    relative = posixpath.relpath(f"reference/{path}", posixpath.dirname(page) or ".")
    return f"{relative}#{anchor}" if anchor else relative


def link_page(text: str, page: str) -> str:
    """Return a page with the first mention of each documented thing in each section linked."""
    out: list[str] = []
    fence: str | None = None
    seen: set[str] = set()
    front = text.startswith("---\n")
    for index, line in enumerate(text.split("\n")):
        if front:
            out.append(line)
            front = not (index > 0 and line == "---")
            continue
        found = _FENCE.match(line)
        if fence is not None or found:
            if found:
                fence = None if fence == found[1] else (fence or found[1])
            out.append(line)
            continue
        if line.startswith("#"):
            if line.startswith(("# ", "## ")):
                seen = set()
            out.append(line)
            continue
        out.append(_link_line(line, page, seen))
    return "\n".join(out)


def _link_line(line: str, page: str, seen: set[str]) -> str:
    # Links already in the line count as mentions, and their text is left alone.
    for _, href in _LINK.findall(line):
        if "reference/" in href:
            seen.add(posixpath.normpath(posixpath.join(posixpath.dirname(page), href)))
    pieces = _LINK.split(line)
    # `split` with two groups gives text, link text, link target, text, and so on.
    for index in range(0, len(pieces), 3):
        pieces[index] = _CODE.sub(lambda found: _link_code(found, page, seen), pieces[index])
    rebuilt = pieces[0]
    for index in range(1, len(pieces), 3):
        rebuilt += f"[{pieces[index]}]({pieces[index + 1]}){pieces[index + 2]}"
    return rebuilt


def _link_code(found: re.Match[str], page: str, seen: set[str]) -> str:
    target = target_of(found[1])
    if target is None:
        return found[0]
    href = _relative(target, page)
    key = posixpath.normpath(posixpath.join(posixpath.dirname(page), href))
    if key in seen:
        return found[0]
    seen.add(key)
    return f"[{found[0]}]({href})"


def pages() -> list[str]:
    """Return the hand-written pages in the nav, as paths from docs/."""
    return [
        page.path
        for page in build_llms.read_config().pages
        if not page.path.startswith("reference/") and page.path not in LEFT_OUT
    ]


def main() -> None:
    check = "--check" in sys.argv[1:]
    stale: list[str] = []
    for page in pages():
        path = DOCS / page
        text = path.read_text(encoding="utf-8")
        linked = link_page(text, page)
        if linked != text:
            stale.append(page)
            if not check:
                path.write_text(linked, encoding="utf-8")
    if check and stale:
        raise SystemExit(f"Run scripts/link_names.py to link names in: {', '.join(stale)}")
    print(f"{'Would link' if check else 'Linked'} names in {len(stale)} pages")


if __name__ == "__main__":
    main()
