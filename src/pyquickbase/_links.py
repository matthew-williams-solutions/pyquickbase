"""Links to pages in the Quickbase interface. No network access.

The paths are the ones the current Quickbase interface uses, as confirmed in a browser and listed
in docs/api-notes.md. Only IDs the caller already resolved go into a link, never a field's value.
"""

from urllib.parse import quote, urlencode

from ._writes import require_id

RECORD_ID_PLACEHOLDER = "{Record ID#}"
"""Where the ID goes in `Table.record_url_pattern()`, named like the field it comes from."""


def _segment(value: str) -> str:
    # IDs are letters and numbers, but quoting means a stray value can't change the path.
    return quote(value, safe="")


def app_url(realm: str, app_id: str) -> str:
    return f"https://{realm}/nav/app/{_segment(app_id)}/action/appoverview"


def _table_base(realm: str, app_id: str, table_id: str) -> str:
    return f"https://{realm}/nav/app/{_segment(app_id)}/table/{_segment(table_id)}/action"


def table_url(realm: str, app_id: str, table_id: str) -> str:
    return f"{_table_base(realm, app_id, table_id)}/td"


def report_url(realm: str, app_id: str, table_id: str, report_id: str) -> str:
    return f"{_table_base(realm, app_id, table_id)}/q?{urlencode({'qid': report_id})}"


def record_url(realm: str, app_id: str, table_id: str, record_id: int, *, edit: bool) -> str:
    require_id("record_id", record_id)
    return record_url_pattern(realm, app_id, table_id, edit=edit).replace(
        RECORD_ID_PLACEHOLDER, str(record_id)
    )


def record_url_pattern(realm: str, app_id: str, table_id: str, *, edit: bool) -> str:
    if not isinstance(edit, bool):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("edit takes True or False.")
    action = "er" if edit else "dr"
    return f"{_table_base(realm, app_id, table_id)}/{action}?rid={RECORD_ID_PLACEHOLDER}"


def field_url(realm: str, app_id: str, table_id: str, field_id: int) -> str:
    require_id("field_id", field_id)
    return f"{_table_base(realm, app_id, table_id)}/mf?{urlencode({'fid': field_id})}"
