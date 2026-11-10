"""Quickbase's XML API: what the SDK may send, and how to read what comes back. No network access.

`XML_CALLS` is the complete list of what the SDK can send. Each one says whether it's a read, and
how a policy sees it. Nothing public names these calls: a method such as `client.apps()` sends one,
and a parser here turns the response into the JSON shape the rest of the SDK reads, or a model.
See docs/api-notes.md for what's been confirmed about each call.
"""

import pyexpat
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal, NoReturn, cast
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import httpx

from ._config import DEFAULT_BASE_URL, LOOPBACK_HOSTS
from ._links import app_url
from ._models import AppMember, AppSummary, MemberRole, Role, UserProfile, role_from_api
from ._read_request import ReadAction, ReadRequest
from ._response import MAX_DETAIL_CHARS, UNAUTHORIZED_HINT, error_from_response
from ._write_request import WriteAction, WriteRequest
from .errors import (
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    InvalidResponseError,
    InvalidValueError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    UnsupportedOperationError,
)

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
"""The largest response parsed. A 1 MB code page reads back as about 1.05 MB."""

_ALLOWED_HOST_SUFFIXES = (".quickbase.com", ".quickbase.eu")
_TARGET = re.compile(r"[A-Za-z0-9]+")
# What XML 1.0 can hold. Quickbase refuses anything else as malformed, as Python's encoder does a
# lone surrogate, so these are refused before anything is sent.
_NOT_XML = re.compile(r"[^\t\n\r\x20-퟿-�\U00010000-\U0010ffff]")
_ERRCODE = re.compile(rb"<errcode>\s*(\d+)\s*</errcode>")
_ERRCODE_SEARCH_BYTES = 1024
_REQUEST_ID_HEADER = "tid"

_RATE_LIMITED = 77
_TEMPORARY = frozenset({100, 101, 104, 105})


def _no_entries() -> Mapping[int, Any]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True, kw_only=True)
class XmlCall:
    """One thing the SDK can ask for.

    Attributes:
        action: Quickbase's name for it.
        target: What's addressed: `main`, an `app`, or a `table`.
        read: Whether it only reads.
        safe_to_repeat: Whether it can be sent again after something that may have reached
            Quickbase. Every XML call is a POST, so the method can't say.
        policy_action: The `action` of the `ReadRequest` or `WriteRequest` a policy sees.
        codes: Errors this call reports differently from `_ERRORS_BY_CODE`.
        notes: Text added to the message for an error code.
    """

    action: str
    target: Literal["main", "app", "table"]
    read: bool
    safe_to_repeat: bool
    policy_action: str
    codes: Mapping[int, type[APIError]] = field(default_factory=_no_entries, compare=False)
    notes: Mapping[int, str] = field(default_factory=_no_entries, compare=False)


def _read(action: str, target: Literal["main", "app"], policy_action: str, **extra: Any) -> XmlCall:
    return XmlCall(
        action=action,
        target=target,
        read=True,
        safe_to_repeat=True,
        policy_action=policy_action,
        **extra,
    )


GRANTED_APPS = _read("API_GrantedDBs", "main", "apps")
CURRENT_USER = _read("API_GetUserInfo", "main", "current_user")
# An email nobody has comes back as invalid input, errcode 2.
FIND_USER = _read("API_GetUserInfo", "main", "find_user", codes={2: NotFoundError})
ROLE_INFO = _read("API_GetRoleInfo", "app", "roles")
APP_SCHEMA = _read("API_GetSchema", "app", "app")
USER_ROLE = _read("API_GetUserRole", "app", "member_roles")
USER_ROLES = _read("API_UserRoles", "app", "members")

XML_CALLS: tuple[XmlCall, ...] = (
    GRANTED_APPS,
    CURRENT_USER,
    FIND_USER,
    ROLE_INFO,
    APP_SCHEMA,
    USER_ROLE,
    USER_ROLES,
)
"""Everything the SDK can send in the XML format."""


def policy_request(call: XmlCall, target: str) -> ReadRequest | WriteRequest:
    """How a policy sees a call. It holds no values from the request."""
    path = f"/db/{target}"
    app_id = target if call.target == "app" else None
    if call.read:
        action = cast(ReadAction, call.policy_action)
        return ReadRequest(action=action, app_id=app_id, method="POST", path=path)
    return WriteRequest(
        action=cast(WriteAction, call.policy_action),
        table_id=target if call.target == "table" else None,
        field_ids=None,
        row_count=None,
        method="POST",
        path=path,
        app_id=app_id,
    )


def target_url(realm: str, base_url: str, target: str) -> str:
    """Where a call goes: the realm's own host, or a loopback `base_url` in a test.

    JSON requests always go to `api.quickbase.com`, but these go to the host the realm names, so a
    realm that isn't Quickbase's would be handed the token.

    Raises:
        ConfigurationError: The realm isn't on a Quickbase domain, `base_url` is customized to
            anything but the local machine, or `target` isn't an ID.
    """
    if not _TARGET.fullmatch(target):
        raise ConfigurationError("An app or table ID contains only letters and numbers.")
    parts = urlsplit(base_url)
    if parts.hostname in LOOPBACK_HOSTS:
        return f"{parts.scheme}://{parts.netloc}/db/{target}"
    if base_url != DEFAULT_BASE_URL:
        raise ConfigurationError(
            "This method uses the realm's own host, so it can't be used with a custom base_url "
            "other than a loopback address."
        )
    if not realm.endswith(_ALLOWED_HOST_SUFFIXES):
        raise ConfigurationError(
            f"This method sends the token to the realm's own host, and {realm!r} isn't on "
            "quickbase.com or quickbase.eu."
        )
    return f"https://{realm}/db/{target}"


def request_headers(call: XmlCall, user_agent: str) -> dict[str, str]:
    return {
        "Content-Type": "application/xml",
        "QUICKBASE-ACTION": call.action,
        "User-Agent": user_agent,
    }


def build_body(fields: Mapping[str, str | int]) -> bytes:
    """A request's body, with every value escaped. The token is never part of it.

    Raises:
        InvalidValueError: A value has a character XML can't hold. Nothing was sent.
    """
    parts: list[str] = []
    for tag, value in fields.items():
        text = str(value)
        if _NOT_XML.search(text):
            raise InvalidValueError(
                f"{tag} has a character XML can't hold, such as a control character."
            )
        parts.append(f"<{tag}>{escape(text)}</{tag}>")
    return f"<qdbapi>{''.join(parts)}</qdbapi>".encode()


@dataclass(slots=True)
class Element:
    """One parsed XML element. `tail` is the text after it, inside its parent."""

    tag: str
    attributes: dict[str, str]
    text: str = ""
    tail: str = ""
    children: "list[Element]" = field(default_factory=lambda: list[Element]())

    def find(self, tag: str) -> "Element | None":
        return next((item for item in self.children if item.tag == tag), None)

    def find_all(self, tag: str) -> "list[Element]":
        return [item for item in self.children if item.tag == tag]

    def text_of(self, tag: str) -> str | None:
        found = self.find(tag)
        return None if found is None or not found.text else found.text


class _Refused(Exception):
    """The document has something that's never processed."""


def _refuse(*_args: Any) -> NoReturn:
    raise _Refused


def parse_xml(body: bytes) -> Element:
    """Parse a response, refusing any DTD or entity declaration before it's processed.

    `ElementTree`'s C parser doesn't expose expat's handlers, and `defusedxml` isn't in the
    playground's Pyodide, so this uses `pyexpat` directly.

    Raises:
        InvalidResponseError: The body is too large, malformed, or has a DTD.
    """
    if len(body) > MAX_RESPONSE_BYTES:
        raise InvalidResponseError("Quickbase's response is larger than this client will read.")
    parser = pyexpat.ParserCreate("utf-8")
    parser.buffer_text = True
    root: list[Element] = []
    open_elements: list[Element] = []

    def start(tag: str, attributes: dict[str, str]) -> None:
        element = Element(tag, attributes)
        if open_elements:
            open_elements[-1].children.append(element)
        else:
            root.append(element)
        open_elements.append(element)

    def end(_tag: str) -> None:
        open_elements.pop()

    def text(data: str) -> None:
        if not open_elements:
            return
        current = open_elements[-1]
        if current.children:
            current.children[-1].tail += data
        else:
            current.text += data

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    try:
        parser.Parse(body, True)
    except (pyexpat.ExpatError, _Refused) as exc:
        raise InvalidResponseError("Quickbase's response isn't XML this client can read.") from exc
    return root[0]


# How each of Quickbase's error codes is raised. Anything else is decided by the HTTP status.
_ERRORS_BY_CODE: dict[int, type[APIError]] = {
    **dict.fromkeys((4, 20, 22, 24, 83), AuthenticationError),
    **dict.fromkeys((3, 7, 8, 19, 34, 61, 74, 114, 150, 151, 152), PermissionDeniedError),
    5: UnsupportedOperationError,
    **dict.fromkeys((21, 30, 31, 32, 33, 35, 37, 81, 87, 110), NotFoundError),
    **dict.fromkeys(
        (
            2,
            6,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            23,
            50,
            51,
            52,
            53,
            70,
            71,
            75,
            76,
            78,
            80,
            102,
            103,
            112,
            113,
        ),
        BadRequestError,
    ),
    77: RateLimitError,
    **dict.fromkeys((82, 84, 85, 100, 101, 104, 105), ServerError),
}


def retry_status(response: httpx.Response) -> int:
    """The status a response counts as for retrying, from its error code when it has one."""
    found = _ERRCODE.search(response.content[:_ERRCODE_SEARCH_BYTES])
    code = int(found[1]) if found else 0
    if code == _RATE_LIMITED:
        return 429
    return 503 if code in _TEMPORARY else response.status_code


def _clip(text: str | None) -> str | None:
    return text[:MAX_DETAIL_CHARS] if text else None


def _error(
    response: httpx.Response, root: Element, code: int, call: XmlCall, path: str
) -> APIError:
    message = _clip(root.text_of("errdetail") or root.text_of("errtext"))
    status, request_id = response.status_code, response.headers.get(_REQUEST_ID_HEADER)
    summary = f"Quickbase returned {status} for POST {path}"
    if message:
        summary += f": {message}"
    if note := call.notes.get(code):
        summary += f" {note}"
    if request_id:
        summary += f" (request ID: {request_id})"
    error_type = call.codes.get(code) or _ERRORS_BY_CODE.get(code)
    if error_type is None:
        error_type = type(error_from_response(response, "POST", path))
    if error_type is AuthenticationError:
        summary += f" {UNAUTHORIZED_HINT}"
    common: dict[str, Any] = {
        "status_code": status,
        "method": "POST",
        "path": path,
        "message": message,
        "request_id": request_id,
        "quickbase_code": code,
    }
    return error_type(summary, **common)


def read_envelope(response: httpx.Response, call: XmlCall, target: str) -> Element:
    """Return a call's parsed response, or raise the error it reports.

    The error code decides when there is one, as a failure could arrive with any status.

    Raises:
        APIError: Quickbase reported an error, or answered with a failing status and no envelope.
        InvalidResponseError: A successful response wasn't what a call returns.
    """
    path = f"/db/{target}"
    try:
        root = parse_xml(response.content)
    except InvalidResponseError:
        if response.is_success:
            raise
        raise error_from_response(response, "POST", path) from None
    raw_code = root.text_of("errcode") if root.tag == "qdbapi" else None
    if raw_code is None or not raw_code.strip().isdigit():
        if response.is_success:
            raise InvalidResponseError(f"Quickbase returned an unexpected response to POST {path}.")
        raise error_from_response(response, "POST", path)
    code = int(raw_code)
    if code != 0:
        raise _error(response, root, code, call, path)
    if not response.is_success:
        raise error_from_response(response, "POST", path)
    return root


def _required(element: Element, tag: str) -> str:
    found = element.text_of(tag)
    if found is None:
        raise InvalidResponseError(f"Quickbase's response has no {tag}.")
    return found


def apps_from_xml(root: Element, realm: str) -> list[AppSummary]:
    databases = root.find("databases")
    return [
        AppSummary(
            id=_required(item, "dbid"),
            name=item.text_of("dbname") or "",
            ancestor_id=item.text_of("ancestorappid"),
            oldest_ancestor_id=item.text_of("oldestancestorappid"),
            url=app_url(realm, _required(item, "dbid")),
        )
        for item in ([] if databases is None else databases.find_all("dbinfo"))
    ]


def user_from_xml(root: Element) -> UserProfile:
    user = root.find("user")
    if user is None or "id" not in user.attributes:
        raise InvalidResponseError("Quickbase's response has no user.")
    return UserProfile(
        id=user.attributes["id"],
        email=user.text_of("email"),
        first_name=user.text_of("firstName"),
        last_name=user.text_of("lastName"),
        screen_name=user.text_of("screenName"),
    )


def _role_dict(role: Element) -> dict[str, Any]:
    access = role.find("access")
    return {
        "id": int(role.attributes["id"]),
        "name": role.text_of("name") or "",
        "access": {"type": "" if access is None else access.text},
    }


def roles_from_xml(root: Element) -> tuple[Role, ...]:
    roles = root.find("roles")
    if roles is None:
        raise InvalidResponseError("Quickbase's response has no roles.")
    return tuple(role_from_api(_role_dict(item)) for item in roles.find_all("role"))


def app_info_dict(root: Element, app_id: str) -> dict[str, Any]:
    """An app's details, in the shape `GET /apps/{id}` returns them."""
    table = root.find("table")
    if table is None:
        raise InvalidResponseError("Quickbase's response has no app details.")
    return {
        "id": app_id,
        "name": table.text_of("name") or "",
        "description": table.text_of("desc") or "",
        "timeZone": root.text_of("time_zone"),
        "dateFormat": root.text_of("date_format"),
    }


def _member_roles(element: Element) -> list[Element]:
    roles = element.find("roles")
    return [] if roles is None else roles.find_all("role")


def members_from_xml(root: Element) -> list[AppMember]:
    users = root.find("users")
    if users is None:
        raise InvalidResponseError("Quickbase's response has no users.")
    found: list[AppMember] = []
    for user in users.find_all("user"):
        last_access = user.text_of("lastAccess")
        found.append(
            AppMember(
                id=user.attributes["id"],
                kind="group" if user.attributes.get("type") == "group" else "user",
                name=user.text_of("name") or "",
                first_name=user.text_of("firstName"),
                last_name=user.text_of("lastName"),
                last_access=_from_millis(last_access),
                roles=tuple(role_from_api(_role_dict(item)) for item in _member_roles(user)),
            )
        )
    return found


def _from_millis(value: str | None) -> datetime | None:
    if value is None or not value.strip().isdigit() or int(value) == 0:
        return None
    return datetime.fromtimestamp(int(value) / 1000, UTC)


def member_roles_from_xml(root: Element) -> list[MemberRole]:
    user = root.find("user")
    if user is None:
        raise InvalidResponseError("Quickbase's response has no user.")
    found: list[MemberRole] = []
    for item in _member_roles(user):
        member = item.find("member")
        found.append(
            MemberRole(
                role=role_from_api(_role_dict(item)),
                assigned_through=None if member is None else member.attributes.get("type"),
            )
        )
    return found
