"""Code pages: their text, what a change would do, and the operations a model can use. No network
access.

Quickbase reports line endings as LF however they were written, so text is compared and hashed in
that form, and a page never looks changed because of them.
"""

import difflib
import hashlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ._decision import CODE_PAGE_WARNING
from ._models import CodePageContent, CodePagePreview
from .errors import ConfigurationError, InvalidValueError

if TYPE_CHECKING:
    from ._bound import BoundPolicy

CODE_PAGE_OPERATIONS = (
    "list_code_pages",
    "read_code_page",
    "preview_code_page_change",
    "apply_code_page_change",
)
_WRITES = ("preview_code_page_change", "apply_code_page_change")


def normalize_text(text: str) -> str:
    """A page's text with LF line endings, as Quickbase reads it back."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def content(*, page_id: int | None, name: str | None, text: str) -> CodePageContent:
    normalized = normalize_text(text)
    return CodePageContent(id=page_id, name=name, text=normalized, sha256=text_sha256(normalized))


def check_ref(ref: Any) -> str | int:
    """A page's name or ID, or an error for anything else.

    Raises:
        InvalidValueError: It's neither a non-empty name nor a positive ID.
    """
    if isinstance(ref, bool) or not isinstance(ref, str | int):
        raise InvalidValueError("A code page is named by its name or its ID.")
    if isinstance(ref, str):
        if not ref.strip():
            raise InvalidValueError("A code page's name can't be blank.")
        return ref
    if ref < 1:
        raise InvalidValueError("A code page's ID is a positive whole number.")
    return ref


def normalize_page_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Read `page`, which models write for `code_page`, as the schema's name for it."""
    found = dict(arguments)
    if "page" in found and "code_page" not in found:
        found["code_page"] = found.pop("page")
    return found


def check_text(text: Any) -> str:
    if not isinstance(text, str):
        raise InvalidValueError("A code page's text must be text.")
    return normalize_text(text)


def build_preview(page: str | int, current: CodePageContent | None, text: str) -> CodePagePreview:
    """What writing `text` would do, against the page as it is now."""
    before = "" if current is None else current.text
    return CodePagePreview(
        code_page=page,
        current=current,
        proposed_sha256=text_sha256(text),
        diff="".join(
            difflib.unified_diff(
                before.splitlines(True),
                text.splitlines(True),
                fromfile="current",
                tofile="proposed",
            )
        ),
        unchanged=current is not None and current.text == text,
    )


def _page_argument(name: str) -> dict[str, Any]:
    description = "The code page, by name or by ID."
    if name in _WRITES:
        description += " A new name creates a new page."
    return {
        "anyOf": [{"type": "string", "minLength": 1}, {"type": "integer", "minimum": 1}],
        "description": description,
    }


def operation_schema(name: str, policy: "BoundPolicy | None") -> dict[str, Any]:
    """The JSON Schema for a code page operation's arguments.

    Raises:
        ConfigurationError: The client's policy doesn't allow what the operation does.
    """
    verb = "write_code_pages" if name in _WRITES else "read_code_pages"
    if policy is not None and not policy._can_use_code_pages(verb):  # pyright: ignore[reportPrivateUsage]
        raise ConfigurationError(
            f"This client can't offer {name}: its policy doesn't allow "
            f"{'allow.code_pages(write=True)' if name in _WRITES else 'allow.code_pages()'}."
        )
    properties: dict[str, Any] = {}
    if name != "list_code_pages":
        properties["code_page"] = _page_argument(name)
    if name in _WRITES:
        properties["text"] = {
            "type": "string",
            "description": f"The page's whole new text. {CODE_PAGE_WARNING}.",
        }
    if name == "apply_code_page_change":
        properties["confirmation"] = {
            "type": "string",
            "description": (
                "The confirmation from preview_code_page_change, called with exactly this page "
                "and text, after the person approved the change. It lasts 15 minutes."
            ),
        }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
