"""Confirmations that tie an update or delete to the preview a person saw. No network access.

A model is told to preview before it changes anything, and to wait for the person's approval, but
nothing made it. With `require_preview`, an `update` or `delete` operation only runs with the
`confirmation` its preview returned, and only for exactly the arguments previewed, within
`CONFIRMATION_SECONDS`. A delete's confirmation also holds how many records matched, so a delete is
refused if that has changed since.

A confirmation is signed with the client's key, so nothing is stored and a confirmation can't be
made up. Each client makes a random key, so a confirmation only works with operations from the
client that issued it.
"""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any, cast

from .errors import QueryError

CONFIRMATION_SECONDS = 900
"""How long a preview's confirmation lasts: 15 minutes."""


def _canonical(value: Any) -> Any:
    """The same JSON for the same arguments, whatever the order of keys or of records."""
    if isinstance(value, Mapping):
        items = cast(Mapping[Any, Any], value)
        return {str(key): _canonical(items[key]) for key in sorted(items, key=str)}
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _records(records: Any) -> Any:
    """Records in a fixed order, so a preview and an update that list them differently agree."""
    if not isinstance(records, list):
        return records
    rows = [_canonical(row) for row in records]  # pyright: ignore[reportUnknownVariableType]
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def _signature(key: bytes, expires: int, kind: str, binding: Mapping[str, Any]) -> str:
    message = json.dumps(
        {"kind": kind, "expires": expires, **binding},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    digest = hmac.new(key, message, hashlib.sha256).digest()[:24]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def binding(
    kind: str, table_id: str, arguments: Mapping[str, Any], scope: Any, matching: int | None
) -> dict[str, Any]:
    """What a confirmation holds: the table, the arguments that decide what changes, the scope,
    and for a delete, how many records matched."""
    if kind == "update":
        decides: Any = _records(arguments.get("records"))
    else:
        decides = _canonical(arguments.get("where"))
    found = {"table": table_id, "arguments": decides, "scope": repr(scope)}
    if matching is not None:
        found["matching"] = matching
    return found


def issue(key: bytes, kind: str, bound: Mapping[str, Any], *, now: float | None = None) -> str:
    """A confirmation for these arguments, lasting `CONFIRMATION_SECONDS`."""
    expires = int(time.time() if now is None else now) + CONFIRMATION_SECONDS
    return f"{expires}.{_signature(key, expires, kind, bound)}"


def check(
    key: bytes,
    kind: str,
    token: Any,
    bound: Mapping[str, Any],
    *,
    now: float | None = None,
) -> None:
    """Refuse unless `token` is a current confirmation for exactly these arguments."""
    preview = f"preview_{kind}"
    if token is None:
        raise QueryError(
            f"This tool needs the confirmation that {preview} returns. Call {preview} with the "
            "same arguments, show the person what it found, and once they approve, send its "
            "confirmation. Nothing was changed."
        )
    expires_text, _, signature = token.partition(".") if isinstance(token, str) else ("", "", "")
    if not expires_text.isdigit() or not signature:
        raise QueryError(
            f"confirmation isn't one {preview} returned. Preview again. Nothing was changed."
        )
    expires = int(expires_text)
    if expires < (time.time() if now is None else now):
        raise QueryError(
            f"The confirmation has expired, as previews last {CONFIRMATION_SECONDS // 60} "
            f"minutes. Call {preview} again, and show the person the result. Nothing was changed."
        )
    if not hmac.compare_digest(signature, _signature(key, expires, kind, bound)):
        changed = " or the records that match have changed since," if kind == "delete" else ""
        raise QueryError(
            f"The confirmation doesn't match: these arguments differ from the preview,{changed} "
            f"or it came from another preview. Call {preview} again with exactly these "
            "arguments, and show the person the result. Nothing was changed."
        )
