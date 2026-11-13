# Preview and change a code page
from pyquickbase import Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    app = qb.app("bq1234567")
    for page in app.code_pages():
        print(page.id, page.name, page.type)

    page = app.code_page("welcome.html")
    new_text = page.read().text.replace("</h1>", " (updated)</h1>")

    preview = page.preview_change(new_text)
    print(preview.diff)

    result = page.write(new_text, expected_sha256=preview.current.sha256)
    print("Saved page", result.content.id, "- created" if result.created else "- replaced")
