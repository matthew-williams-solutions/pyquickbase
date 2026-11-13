"""Every error links to its section of the docs, as a note that `str()` leaves out."""

import pickle

from pyquickbase import (
    PolicyDecision,
    PolicyRefusedError,
    RateLimitError,
    SummaryTooLargeError,
    UnknownFieldError,
    WritePermissionDeniedError,
)
from pyquickbase.errors import DOCS_URL, QuickbaseError


def test_an_error_links_to_its_own_section() -> None:
    error = UnknownFieldError("No such field.")
    assert error.doc_url == f"{DOCS_URL}#UnknownFieldError"
    assert str(error) == "No such field."
    assert error.__notes__ == [f"See {DOCS_URL}#UnknownFieldError"]


def test_an_error_with_two_bases_gets_one_note() -> None:
    error = WritePermissionDeniedError("Refused.")
    assert error.__notes__ == [f"See {DOCS_URL}#WritePermissionDeniedError"]


def test_errors_with_their_own_arguments_link_too() -> None:
    errors = [
        SummaryTooLargeError(11, 10),
        RateLimitError("Slow down.", retry_after=3),
        PolicyRefusedError(PolicyDecision(allowed=False, verb="read")),
    ]
    for error in errors:
        assert error.__notes__ == [f"See {error.doc_url}"]


def test_a_subclass_outside_the_sdk_links_to_its_nearest_documented_base() -> None:
    class Mine(UnknownFieldError):
        pass

    assert Mine("x").doc_url == f"{DOCS_URL}#UnknownFieldError"


def test_an_unpickled_error_keeps_one_note() -> None:
    for error in (SummaryTooLargeError(11, 10), QuickbaseError("x")):
        again = pickle.loads(pickle.dumps(error))
        assert again.__notes__ == error.__notes__
