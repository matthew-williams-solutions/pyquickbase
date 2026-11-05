"""Write docs/llms.txt and docs/llms-full.txt, ready for `mkdocs build`.

uv run scripts/build_llms.py

llms.txt lists the docs, and llms-full.txt holds them in one Markdown file, with the API reference
from build_reference.py. This uses only the standard library, not a docs plugin, so it doesn't
depend on which tool builds the site. None of this is committed.
"""

import posixpath
import re
from pathlib import Path
from typing import NamedTuple

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
CONFIG = ROOT / "mkdocs.yml"

# Reference pages with their own plain Markdown files, as `(title, name, what's in it)`.
REFERENCE_PAGES = (
    ("Quickbase", "quickbase", "Creating and closing a client, and calling any endpoint"),
    ("App", "app", "An app's details, getting its tables, and creating tables"),
    ("Table", "table", "Reading and writing records, files, and changing a table's fields"),
    ("Report", "report", "Running reports built in the Quickbase interface"),
    ("TypedTable", "typed-table", "Reading and writing through a generated class"),
    ("Queries", "queries", "`F`, conditions from plain data, and checking hand-written ones"),
    ("Records and results", "records", "What reads and writes return, files, and summaries"),
    ("Metadata", "metadata", "What describes apps, tables, fields, relationships, and reports"),
    (
        "Limits and retries",
        "limits",
        "Limiting what a client can write and read, and how it retries",
    ),
    ("Generated classes", "generated", "What supports the classes from `pyquickbase codegen`"),
    ("Errors", "errors", "Everything the SDK raises, and what each one holds"),
)
# An interactive page, which means nothing as text, and the pages above.
LEFT_OUT = {"playground.md", *(f"reference/{page[1]}.md" for page in REFERENCE_PAGES)}
# What each page is for, shown in llms.txt. A page missing from here stops the build.
DESCRIPTIONS = {
    "index.md": "What the SDK is, why to use it, and how to install it",
    "getting-started.md": "Install it, read some records, and write one",
    "guides/reading.md": "Tables, fields, queries with `F`, paging, record values, reports, and "
    "what each refusal raises",
    "guides/writing.md": "Inserts, updates, upserts, deletes, rows that fail, and what each "
    "refusal leaves behind",
    "guides/files.md": "Uploading, downloading, and deleting file attachments",
    "guides/formulas.md": "Running formulas and creating formula fields, and why not to run "
    "formula text you don't control",
    "guides/codegen.md": "Typed classes for your tables from `pyquickbase codegen`",
    "guides/schema.md": "Creating and changing tables, fields, relationships, lookups, and "
    "summaries, and what each refusal leaves behind",
    "guides/errors.md": "Every exception, and which ones mean a request wasn't sent",
    "guides/clients.md": "Read-only clients, allowed writes, retries, proxies, and other settings",
    "guides/agents.md": "Limiting what an agent can do, untrusted values, and reading results as "
    "data",
    "reference.md": "An overview of the API reference, with where to find every public name",
    "field-types.md": "Every field type, what to write, what it reads as, and field settings",
    "stability.md": "What 1.x promises, and what it doesn't",
    "api-notes.md": "How Quickbase's REST API behaves, as confirmed against a live app",
    "changelog.md": "What changed in each release",
}
# Text that means a page wasn't fully converted.
UNCONVERTED = (":::", "--8<--", "!!!")

_NAV_ITEM = re.compile(r"""^( +)- ["']?(.+?)["']?:\s*(\S+\.md)?$""")
_FENCE = re.compile(r"^[>\s]*(`{3,}|~{3,})")
_SNIPPET = re.compile(r'^--8<-- "(.+)"$', re.MULTILINE)
_BLOCK = re.compile(r'^(!!!|===)\s*(\w+)?\s*(?:"(.*)")?\s*$')
_LINK = re.compile(r"(\]\()([^)\s]+)")
_LINK_DEFINITION = re.compile(r"^(\[[^\]]+\]:\s+)(\S+)")
_CODE_SPAN = re.compile(r"(`[^`]*`)")
_HEADING = re.compile(r"^(#{1,5}) ")
_MODULE_PREFIX = re.compile(r"\b(?:collections\.abc|typing|pyquickbase(?:\.\w+)*)\.")
_QUOTED_TYPE = re.compile(r"'([A-Z][\w\[\], ]*)'")


class Page(NamedTuple):
    title: str
    path: str
    section: str | None


class Config(NamedTuple):
    site_url: str
    summary: str
    pages: list[Page]


def read_config(text: str | None = None) -> Config:
    """Read what's needed from mkdocs.yml, without PyYAML, which belongs to the docs tools."""
    lines = (CONFIG.read_text(encoding="utf-8") if text is None else text).splitlines()
    values = dict(line.split(": ", 1) for line in lines if re.match(r"site_\w+: ", line))
    start = lines.index("nav:") + 1
    pages: list[Page] = []
    section: tuple[int, str] | None = None
    for line in lines[start:]:
        if line and not line.startswith(" "):
            break
        if not line.strip():
            continue
        item = _NAV_ITEM.match(line)
        if item is None:
            raise SystemExit(f"Can't read this line of the nav in mkdocs.yml: {line!r}")
        indent, title, path = len(item[1]), item[2], item[3]
        if section is not None and indent <= section[0]:
            section = None
        if path is None:
            section = (indent, title)
        else:
            pages.append(Page(title, path, section[1] if section else None))
    return Config(values["site_url"].rstrip("/") + "/", values["site_description"], pages)


def page_url(site_url: str, path: str) -> str:
    """Return where the site serves a page, using directory URLs as MkDocs does by default."""
    stem = path.removesuffix(".md")
    if stem == "index":
        return site_url
    return f"{site_url}{stem.removesuffix('/index')}/"


def _next_fence(line: str, fence: str | None) -> str | None:
    """Track whether the next line is inside a code block."""
    found = _FENCE.match(line)
    if found is None:
        return fence
    if fence is None:
        return found[1]
    return None if found[1].startswith(fence) else fence


def clean_types(text: str) -> str:
    """Show types by their public names, as in `Expression`, not `pyquickbase._query.Expression`."""
    return _QUOTED_TYPE.sub(r"\1", _MODULE_PREFIX.sub("", text)).replace("~", "")


def _resolve_snippets(text: str) -> str:
    return _SNIPPET.sub(lambda found: (ROOT / found[1]).read_text(encoding="utf-8"), text)


def convert_blocks(text: str) -> str:
    """Turn admonitions into block quotes and tabs into labeled sections, including nested ones."""
    lines = text.splitlines()
    out: list[str] = []
    fence: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        block = None if fence else _BLOCK.match(line)
        fence = _next_fence(line, fence)
        if block is None:
            out.append(line)
            continue
        body: list[str] = []
        while index < len(lines) and (not lines[index].strip() or lines[index].startswith("    ")):
            body.append(lines[index][4:])
            index += 1
        inner = convert_blocks("\n".join(body)).strip("\n").splitlines()
        kind, title = block[2], block[3]
        if block[1] == "===":
            out += [f"**{title}:**", "", *inner, ""]
        else:
            label = ": ".join(part for part in ((kind or "Note").capitalize(), title) if part)
            quoted = [f"> {inner_line}" if inner_line else ">" for inner_line in inner]
            out += [f"> **{label}**", ">", *quoted, ""]
    return "\n".join(out)


def _absolute(target: str, page: str, site_url: str) -> str:
    """Turn a link in a page into the URL it reaches on the site. Other sites' links stay as is."""
    if re.match(r"[a-z][a-z0-9+.-]*:", target):
        return target
    path, hash_mark, fragment = target.partition("#")
    if not path:
        return f"{page_url(site_url, page)}{hash_mark}{fragment}"
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(page), path))
    if resolved.startswith(".."):
        raise SystemExit(f"{page} links outside the docs: {target}")
    url = page_url(site_url, resolved) if resolved.endswith(".md") else f"{site_url}{resolved}"
    return f"{url}{hash_mark}{fragment}"


def rewrite_links(text: str, page: str, site_url: str) -> str:
    """Make every link absolute, leaving anything that's code alone."""

    def rewrite_inline(found: re.Match[str]) -> str:
        return f"{found[1]}{_absolute(found[2], page, site_url)}"

    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        in_code = fence is not None or _FENCE.match(line) is not None
        fence = _next_fence(line, fence)
        if in_code:
            out.append(line)
            continue
        parts = _CODE_SPAN.split(_LINK_DEFINITION.sub(rewrite_inline, line))
        parts[::2] = [_LINK.sub(rewrite_inline, part) for part in parts[::2]]
        out.append("".join(parts))
    return "\n".join(out)


def demote_headings(text: str) -> str:
    """Move a page's headings down a level, under the single title of the combined file."""
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        in_code = fence is not None or _FENCE.match(line) is not None
        fence = _next_fence(line, fence)
        out.append(_HEADING.sub(r"#\1 ", line) if not in_code else line)
    return "\n".join(out)


def _without_title(text: str, page: str) -> str:
    """Remove a page's title, because the combined file heads each page with its nav title."""
    title = re.search(r"^# .*\n?", text, re.MULTILINE)
    if title is None:
        raise SystemExit(f"{page} has no title.")
    return text[: title.start()] + text[title.end() :]


def convert_page(page: Page, site_url: str) -> str:
    """Return one nav page as plain Markdown."""
    text = _resolve_snippets((DOCS / page.path).read_text(encoding="utf-8"))
    text = _without_title(text, page.path)
    text = convert_blocks(text)
    text = demote_headings(rewrite_links(text, page.path, site_url)).strip()
    return f"## {page.title}\n\nSource: <{page_url(site_url, page.path)}>\n\n{text}\n"


_NOT_AFFILIATED = (
    "pyquickbase is unofficial. It isn't affiliated with or endorsed by Quickbase, Inc."
)


def _reference_pages(site_url: str) -> list[str]:
    """Return the API reference's pages, under the combined file's headings."""
    # build_reference.py uses this module's helpers, so it can only be imported once they exist.
    import build_reference

    return [
        f"## API reference: {title}\n\nSource: <{page_url(site_url, f'reference/{slug}.md')}>\n\n"
        + demote_headings(text).strip()
        for title, slug, text in build_reference.full_text(site_url)
    ]


def _covered_pages(config: Config) -> list[Page]:
    pages = [page for page in config.pages if page.path not in LEFT_OUT]
    paths = {page.path for page in pages}
    if paths != set(DESCRIPTIONS):
        raise SystemExit(
            "DESCRIPTIONS doesn't match the nav in mkdocs.yml: "
            f"{sorted(paths.symmetric_difference(DESCRIPTIONS))}"
        )
    return pages


def build_index(config: Config) -> str:
    """Return llms.txt, in the format described at llmstxt.org."""
    site_url = config.site_url
    sections: dict[str, list[str]] = {
        "Docs": [
            f"- [All the docs in one file]({site_url}llms-full.txt): Every page below as one "
            "Markdown document, including the API reference",
            *(
                f"- [API reference: {title}]({site_url}reference/{name}.txt): {about}, as "
                "Markdown, with parameters, return values, errors, and tested examples"
                for title, name, about in REFERENCE_PAGES
            ),
        ]
    }
    for page in _covered_pages(config):
        entry = f"- [{page.title}]({page_url(site_url, page.path)}): {DESCRIPTIONS[page.path]}"
        sections.setdefault(page.section or "Docs", []).append(entry)
    about = f"{_NOT_AFFILIATED} This describes version {pyquickbase.__version__}."
    parts = [f"# {pyquickbase.__name__}", f"> {config.summary}", about]
    parts += [f"## {name}\n\n" + "\n".join(entries) for name, entries in sections.items()]
    return "\n\n".join(parts) + "\n"


def build_full(config: Config) -> str:
    """Return llms-full.txt: every page but the playground, as one Markdown document."""
    site_url = config.site_url
    intro = (
        f"These are all the docs for version {pyquickbase.__version__} "
        f"(`pyquickbase.__version__`), as one file. The [playground]({site_url}playground/) is "
        f"left out because it's interactive. A list of the pages is in "
        f"[llms.txt]({site_url}llms.txt)."
    )
    parts = [
        f"# {pyquickbase.__name__}",
        f"> {config.summary}",
        f"{_NOT_AFFILIATED} {intro}",
        *(convert_page(page, site_url).strip() for page in _covered_pages(config)),
        *_reference_pages(site_url),
    ]
    full = re.sub(r"\n{3,}", "\n\n", "\n\n".join(parts)) + "\n"
    for text in UNCONVERTED:
        if text in full:
            raise SystemExit(f"llms-full.txt still contains {text!r}, so a page wasn't converted.")
    return full


def main() -> None:
    config = read_config()
    (DOCS / "llms.txt").write_text(build_index(config), encoding="utf-8")
    (DOCS / "llms-full.txt").write_text(build_full(config), encoding="utf-8")
    print("Wrote docs/llms.txt and docs/llms-full.txt")


if __name__ == "__main__":
    main()
