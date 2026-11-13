---
description: "Reading, previewing, and replacing code pages, and why a policy doesn't limit what a page does."
---

# Code pages

A code page is an HTML or JavaScript page stored in a Quickbase app. pyquickbase can read, preview, and replace them.

!!! danger "A code page runs with the viewer's permissions"
    A code page runs in the viewer's browser, with the viewer's permissions. A policy controls what this client may
    do. It doesn't control what a page does once someone opens it. Allowing page writes lets the writer run code as
    anyone who views that page, including an administrator, who could change roles or read fields this policy hides.

## Reading a page

Get a page by name, or by ID as a whole number. `read()` returns its text and a hash.

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    app = qb.app("bq1234567")
    page = app.code_page("report.html")
    print(page.read().text)
```

`app.code_pages()` lists an app's pages, by name, ID, and type. Quickbase doesn't document the call behind it, so
`code_page()` works without it, by name or ID, in case it goes away.

Home pages are listed too, but Quickbase refuses to read or replace them this way. Reading one raises
`BadRequestError`.

## Previewing and writing

`preview_change()` says what a write would do, with a diff, and `write()` does it.

```python
new_text = "<h1>Jobs</h1>\n"

preview = page.preview_change(new_text)
print(preview.diff)

result = page.write(new_text, expected_sha256=preview.current.sha256)
print(result.created, result.content.id)
```

A name that isn't in use creates a page, and a name that's in use replaces that page and keeps its ID. Passing an ID
that doesn't exist raises `NotFoundError`, because there's nothing to replace.

## Putting a page back

**Quickbase keeps no history of a page, and the API can't delete one.** `write()` always reads the page first, and
returns what it replaced as `previous`, which is the only copy. Keep it if you might want to go back:

```python
result = page.write(new_text)
saved = result.previous.text if result.previous else None

page.write(saved)  # later, to put it back
```

## Avoiding a lost change

Quickbase can't check that a page hasn't changed at the moment it writes. With `expected_sha256`, `write()` reads the
page just before writing and raises `CodePageChangedError` if it's a different version, with nothing written. A change
in the moment between that check and the write can still be overwritten.

## Line endings and size

Quickbase returns every line ending as LF, whether a page was saved with LF, CRLF, or a lone CR, so the original line
endings can't be recovered. The SDK writes LF, and hashes and compares text that way, so a page never looks changed
because of them.

Everything else reads back exactly, including non-ASCII text, `]]>`, tabs, and trailing spaces. A page of 1 MB saved
in testing and one of 5 MB didn't, which raises `APIError`.

## From an agent

See [Code pages from an agent](../agents/changes.md#code-pages-from-an-agent) for the operations a model can use, and
[Code pages](../policies/rules.md#code-pages) for what a policy can allow.
