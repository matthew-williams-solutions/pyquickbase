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

# An interactive page, which means nothing as text. The reference's pages under reference/ are left
# out too, except its overview, as build_reference.py writes their plain Markdown.
LEFT_OUT = {"playground.md"}
OVERVIEW = "reference/index.md"
# What each page is for, shown in llms.txt. A page missing from here stops the build.
DESCRIPTIONS = {
    "index.md": "What the SDK is, why to use it, and how to install it",
    "getting-started.md": "Install it, read some records, and write one",
    "guides/reading.md": "Tables, fields, queries with `F`, paging, record values, reports, "
    "DataFrames, and what each refusal raises",
    "guides/writing.md": "Inserts, updates, upserts, deletes, DataFrames, previews, rows that "
    "fail, and what each refusal leaves behind",
    "guides/files.md": "Uploading, downloading, and deleting file attachments",
    "guides/formulas.md": "Running formulas and creating formula fields, and why not to run "
    "formula text you don't control",
    "guides/schema.md": "Creating and changing tables, fields, relationships, lookups, and "
    "summaries, where fields are used, and what each refusal leaves behind",
    "guides/apps.md": "Creating, copying, and deleting apps, and the webhooks and automations "
    "that run in one",
    "guides/errors.md": "Every exception, and which ones mean a request wasn't sent",
    "guides/clients.md": "Sync or async, tokens, retries, proxies, and other settings",
    "agents/index.md": "Limiting what an agent can do, and why the token is the real boundary",
    "agents/untrusted-input.md": "Values and filters that come from a model",
    "agents/operations.md": "Giving a model typed operations on tables, reports, and schemas",
    "agents/results.md": "Returning results and errors to a model, links, and counts",
    "agents/changes.md": "Previews before writes, and schema changes from an agent",
    "policies/index.md": "What a policy is, how it decides, and read-only clients",
    "policies/tutorial.md": "Building a policy for an agent step by step",
    "policies/rules.md": "Allowing and refusing reads and writes by table and field",
    "policies/classes.md": "Groups of fields, formulas that read them, and new fields",
    "policies/rows.md": "Keeping a client to some records",
    "policies/schema.md": "Allowing schema changes table by table",
    "policies/custom-rules.md": "Refusing requests with logic of your own",
    "policies/debugging.md": "Reading refusals, and asking a policy what it allows",
    "policies/recipes.md": "Complete policies for common jobs",
    "policies/limits.md": "What a policy can't protect against",
    OVERVIEW: "An overview of the API reference, with where to find every public name",
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
_UNION = re.compile(r"\bUnion\[")


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
    # The top-level section a page is in, as `(indent, title)`. A section inside it, such as the
    # reference's "Core", doesn't change it.
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
            section = section or (indent, title)
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
    text = _QUOTED_TYPE.sub(r"\1", _MODULE_PREFIX.sub("", text)).replace("~", "")
    return _pipes(text)


def _pipes(text: str) -> str:
    """Write `Union[A, B]` as `A | B`, which is how Python 3.14 prints a union with a `Literal`."""
    while found := _UNION.search(text):
        parts: list[str] = []
        depth, start = 1, found.end()
        index = start
        while depth:
            char = text[index]
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
            if depth == 0 or (depth == 1 and char == ","):
                parts.append(text[start:index].strip())
                start = index + 1
            index += 1
        text = text[: found.start()] + " | ".join(parts) + text[index:]
    return text


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


def _page_source(path: str) -> str:
    """Return a nav page's Markdown. The overview is generated, so it may not be written yet."""
    if path == OVERVIEW:
        # build_reference.py uses this module's helpers, so it can only be imported once they exist.
        import build_reference

        return build_reference.build_overview()
    return (DOCS / path).read_text(encoding="utf-8")


def convert_page(page: Page, site_url: str) -> str:
    """Return one nav page as plain Markdown."""
    text = _resolve_snippets(_page_source(page.path))
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


def is_covered(path: str) -> bool:
    """Whether a nav page is in llms.txt, rather than the playground or a reference page."""
    if path in LEFT_OUT:
        return False
    return path == OVERVIEW or not path.startswith("reference/")


def _covered_pages(config: Config) -> list[Page]:
    pages = [page for page in config.pages if is_covered(page.path)]
    paths = {page.path for page in pages}
    if paths != set(DESCRIPTIONS):
        raise SystemExit(
            "DESCRIPTIONS doesn't match the nav in mkdocs.yml: "
            f"{sorted(paths.symmetric_difference(DESCRIPTIONS))}"
        )
    return pages


def reference_pages() -> list[tuple[str, str, str]]:
    """Return the reference's pages in menu order, as `(title, slug, what's in it)`."""
    # build_reference.py uses this module's helpers, so it can only be imported once they exist.
    import build_reference

    return [
        (build_reference.PAGES_BY_SLUG[slug].name, slug, about)
        for section in build_reference.SECTIONS
        for slug, about in section.pages
    ]


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
                for title, name, about in reference_pages()
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
