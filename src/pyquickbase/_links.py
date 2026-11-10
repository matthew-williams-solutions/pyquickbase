"""Links to pages in the Quickbase interface. No network access.

The paths are the ones the current Quickbase interface uses, as confirmed in a browser and listed
in docs/api-notes.md. Only IDs the caller already resolved go into a link, never a field's value.
"""

from urllib.parse import quote, urlencode

from ._writes import require_id

RECORD_ID_PLACEHOLDER = "{record_id}"
"""Where the ID goes in `Table.record_url_template()`."""


def _segment(value: str) -> str:
    # IDs are letters and numbers, but quoting means a stray value can't change the path.
    return quote(value, safe="")


def app_url(realm: str, app_id: str, *, settings: bool = False) -> str:
    action = "appsettingshome" if settings else "appoverview"
    return f"https://{realm}/nav/app/{_segment(app_id)}/action/{action}"


def code_page_url(realm: str, app_id: str, page: str | int) -> str:
    query = {"a": "dbpage", ("pageID" if isinstance(page, int) else "pagename"): page}
    return f"https://{realm}/db/{_segment(app_id)}?{urlencode(query)}"


def _require_bool(name: str, value: bool) -> None:
    if not isinstance(value, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(f"{name} takes True or False.")


def table_page(
    realm: str, app_id: str, table_id: str, action: str, query: dict[str, int | str] | None = None
) -> str:
    """Link to one of a table's pages, such as `td` for its home, or `listfields`."""
    base = f"https://{realm}/nav/app/{_segment(app_id)}/table/{_segment(table_id)}/action/{action}"
    return f"{base}?{urlencode(query)}" if query else base


def report_url(realm: str, app_id: str, table_id: str, report_id: str, *, edit: bool) -> str:
    _require_bool("edit", edit)
    return table_page(realm, app_id, table_id, "viewBuild" if edit else "q", {"qid": report_id})


def record_url(realm: str, app_id: str, table_id: str, record_id: int, *, edit: bool) -> str:
    require_id("record_id", record_id)
    return record_url_template(realm, app_id, table_id, edit=edit).replace(
        RECORD_ID_PLACEHOLDER, str(record_id)
    )


def record_url_template(realm: str, app_id: str, table_id: str, *, edit: bool) -> str:
    _require_bool("edit", edit)
    base = table_page(realm, app_id, table_id, "er" if edit else "dr")
    return f"{base}?rid={RECORD_ID_PLACEHOLDER}"


def field_url(realm: str, app_id: str, table_id: str, field_id: int) -> str:
    require_id("field_id", field_id)
    return table_page(realm, app_id, table_id, "mf", {"fid": field_id})


def form_url(realm: str, app_id: str, table_id: str, form_id: int) -> str:
    require_id("form_id", form_id)
    return table_page(realm, app_id, table_id, "DFormProps", {"dfid": form_id})
