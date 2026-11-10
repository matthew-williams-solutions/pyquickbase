"""Confirmations that tie a change to the preview a person saw. No network access.

A model is told to preview before it changes anything, and to wait for the person's approval, but
nothing made it. With `require_preview`, an `update` or `delete` operation only runs with the
`confirmation` its preview returned, and only for exactly the arguments previewed, within
`CONFIRMATION_SECONDS`. A delete's confirmation also holds how many records matched, so a delete is
refused if that has changed since. An `apply_schema_changes` operation always needs the
confirmation `preview_schema_changes` returned, which holds a fingerprint of the schema the plan was
checked against. An `apply_code_page_change` operation always needs the confirmation
`preview_code_page_change` returned, which holds the hash of the page as it was previewed.

A confirmation is signed with the client's key, so nothing is stored and a confirmation can't be
made up. Each client makes a random key, so a confirmation only works with operations from the
client that issued it, unless clients share one with `confirmation_key`. A confirmation starts with
an ID for its key, so one from another client says so, and what it's bound to includes the hash of
the client's policy, so one from a client with another policy doesn't match.
"""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any, Literal, cast

from .errors import InvalidConfirmationError

CONFIRMATION_SECONDS = 900
"""How long a preview's confirmation lasts: 15 minutes."""
_MAX_EXPIRY_DIGITS = 20

ConfirmationKind = Literal["update", "delete", "schema", "code_page"]
_PREVIEWS: dict[str, str] = {
    "update": "preview_update",
    "delete": "preview_delete",
    "schema": "preview_schema_changes",
    "code_page": "preview_code_page_change",
}
_NOUNS: dict[str, str] = {
    "update": "preview",
    "delete": "preview",
    "schema": "preview",
    "code_page": "preview",
}


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


def _signature(key: bytes, expires: int, kind: ConfirmationKind, binding: Mapping[str, Any]) -> str:
    message = json.dumps(
        {"kind": kind, "expires": expires, **binding},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    digest = hmac.new(key, message, hashlib.sha256).digest()[:24]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def key_id(key: bytes) -> str:
    """A short ID for a confirmation key, which reveals nothing about it."""
    return hashlib.sha256(b"pyquickbase confirmation key\0" + key).hexdigest()[:8]


def binding(
    kind: ConfirmationKind,
    table_id: str,
    arguments: Mapping[str, Any],
    scope: Any,
    matching: int | None,
    policy_hash: str | None = None,
) -> dict[str, Any]:
    """What a confirmation holds: the table, the arguments that decide what changes, the scope,
    the client's policy, and for a delete, how many records matched."""
    if kind == "update":
        decides: Any = _records(arguments.get("records"))
    else:
        decides = _canonical(arguments.get("where"))
    found = {"table": table_id, "arguments": decides, "scope": repr(scope), "policy": policy_hash}
    if matching is not None:
        found["matching"] = matching
    return found


def schema_binding(
    app_id: str, changes: list[dict[str, Any]], fingerprint: str, policy_hash: str | None = None
) -> dict[str, Any]:
    """What a schema plan's confirmation holds: the app, the changes, the schema they were
    checked against, and the client's policy, so applying it is refused if any of those differ."""
    return {
        "app": app_id,
        "arguments": _canonical(changes),
        "fingerprint": fingerprint,
        "policy": policy_hash,
    }


def code_page_binding(
    app_id: str,
    page: str | int,
    proposed_sha256: str,
    previous_sha256: str | None,
    policy_hash: str | None = None,
) -> dict[str, Any]:
    """What a code page change's confirmation holds: the app, the page, the new text's hash, the
    page's hash when it was previewed, and the client's policy."""
    return {
        "app": app_id,
        "page": page,
        "proposed": proposed_sha256,
        "previous": previous_sha256,
        "policy": policy_hash,
    }


def _is_expiry(text: str) -> bool:
    """Whether text is a time as `issue()` writes it, which `int()` can always read."""
    return text.isascii() and text.isdigit() and len(text) <= _MAX_EXPIRY_DIGITS


def expires_at(token: str) -> int | None:
    """When a confirmation stops working, in seconds since the epoch, or None if it isn't one."""
    parts = token.split(".")
    return int(parts[1]) if len(parts) == 3 and _is_expiry(parts[1]) else None


def issue(
    key: bytes, kind: ConfirmationKind, bound: Mapping[str, Any], *, now: float | None = None
) -> str:
    """A confirmation for these arguments, lasting `CONFIRMATION_SECONDS`."""
    expires = int(time.time() if now is None else now) + CONFIRMATION_SECONDS
    return f"{key_id(key)}.{expires}.{_signature(key, expires, kind, bound)}"


def check(
    key: bytes,
    kind: ConfirmationKind,
    token: Any,
    bound: Mapping[str, Any],
    *,
    now: float | None = None,
) -> None:
    """Refuse unless `token` is a current confirmation for exactly these arguments."""
    preview, noun = _PREVIEWS[kind], _NOUNS[kind]
    if token is None:
        raise InvalidConfirmationError(
            f"This tool needs the confirmation that {preview} returns. Call {preview} with the "
            "same arguments, show the person what it found, and once they approve, send its "
            "confirmation. Nothing was changed."
        )
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3 or not _is_expiry(parts[1]) or not parts[2] or not parts[2].isascii():
        raise InvalidConfirmationError(
            f"confirmation isn't one {preview} returned. Call {preview} again. Nothing was changed."
        )
    issued_by, expires_text, signature = parts
    if issued_by != key_id(key):
        raise InvalidConfirmationError(
            f"The confirmation came from another client or server process, whose key this one "
            f"doesn't have, so it can't be checked here. Call {preview} with this tool, or give "
            "the clients the same confirmation_key. Nothing was changed."
        )
    expires = int(expires_text)
    if expires < (time.time() if now is None else now):
        raise InvalidConfirmationError(
            f"The confirmation has expired, as a {noun}'s confirmation lasts "
            f"{CONFIRMATION_SECONDS // 60} minutes. Call {preview} again, and show the person the "
            "result. Nothing was changed."
        )
    if not hmac.compare_digest(signature, _signature(key, expires, kind, bound)):
        changed = {
            "delete": " the records that match have changed since,",
            "schema": " the schema has changed since,",
            "code_page": " the page has changed since,",
        }.get(kind, "")
        raise InvalidConfirmationError(
            f"The confirmation doesn't match: these arguments differ from the {noun}'s,{changed} "
            f"or it came from another {noun}, or from a client with a different policy. Call "
            f"{preview} again with exactly these arguments, and show the person the result. "
            "Nothing was changed."
        )
