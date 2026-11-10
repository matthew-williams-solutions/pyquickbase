"""The XML format: what's sent, what's refused, and how each error is reported. No network."""

import httpx
import pytest

from pyquickbase import (
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
from pyquickbase._code_pages import build_preview, check_ref, content, normalize_text, text_sha256
from pyquickbase._config import DEFAULT_BASE_URL
from pyquickbase._links import code_page_url
from pyquickbase._xml import (
    GRANTED_APPS,
    MAX_RESPONSE_BYTES,
    Element,
    build_body,
    page_text_from_xml,
    pages_from_xml,
    parse_xml,
    read_envelope,
    retry_status,
    target_url,
)
from tests.xml_fake import envelope, failure


def response(status: int, content: bytes, **headers: str) -> httpx.Response:
    return httpx.Response(status, content=content, headers=headers)


def test_every_value_is_escaped() -> None:
    assert build_body({"pagebody": "<b>&</b>]]>"}) == (
        b"<qdbapi><pagebody>&lt;b&gt;&amp;&lt;/b&gt;]]&gt;</pagebody></qdbapi>"
    )


@pytest.mark.parametrize("text", ["\x00", "\x0b", "a\ud800b", "￿"])
def test_text_xml_cant_hold_is_refused_before_sending(text: str) -> None:
    with pytest.raises(InvalidValueError):
        build_body({"pagebody": text})


def test_parsing_keeps_attributes_text_and_tails() -> None:
    root = parse_xml(b'<a x="1">one<b/>two<c>three</c>four</a>')
    assert root.attributes == {"x": "1"}
    assert root.text == "one"
    assert [(item.tag, item.text, item.tail) for item in root.children] == [
        ("b", "", "two"),
        ("c", "three", "four"),
    ]


@pytest.mark.parametrize(
    "body",
    [
        b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>',
        b"<!DOCTYPE a><a/>",
        b'<!DOCTYPE a SYSTEM "http://example.com/a.dtd"><a/>',
        b"not xml",
        b"<a>",
        b"",
    ],
)
def test_a_dtd_or_malformed_body_is_refused(body: bytes) -> None:
    with pytest.raises(InvalidResponseError):
        parse_xml(body)


def test_a_response_over_the_limit_is_refused() -> None:
    with pytest.raises(InvalidResponseError, match="larger"):
        parse_xml(b"<a>" + b"x" * MAX_RESPONSE_BYTES + b"</a>")


@pytest.mark.parametrize(
    ("codes", "error"),
    [
        ((4, 20, 22, 24, 83), AuthenticationError),
        ((3, 7, 8, 19, 34, 61, 74, 114, 150, 151, 152), PermissionDeniedError),
        ((5,), UnsupportedOperationError),
        ((21, 30, 31, 32, 33, 35, 37, 81, 87, 110), NotFoundError),
        ((2, 6, 9, 10, 11, 12, 13, 14, 15, 23, 50, 51, 52, 53), BadRequestError),
        ((70, 71, 75, 76, 78, 80), BadRequestError),
        ((102, 103, 112, 113), BadRequestError),
        ((77,), RateLimitError),
        ((82, 84, 85, 100, 101, 104, 105), ServerError),
    ],
)  # fmt: skip
def test_each_error_code_raises_its_error(codes: tuple[int, ...], error: type[APIError]) -> None:
    for code in codes:
        with pytest.raises(error) as raised:
            read_envelope(response(400, failure("X", code, "Nope")), GRANTED_APPS, "main")
        assert raised.value.quickbase_code == code
        assert raised.value.status_code == 400


def test_the_details_are_preferred_because_some_error_text_is_useless() -> None:
    body = failure("X", 83, "Invalid error code: 83", "User token is invalid")
    with pytest.raises(AuthenticationError) as raised:
        read_envelope(response(400, body, tid="req-1"), GRANTED_APPS, "main")
    assert raised.value.message == "User token is invalid"
    assert raised.value.request_id == "req-1"
    assert "check the ID" in str(raised.value)


def test_an_unknown_code_is_decided_by_the_status() -> None:
    with pytest.raises(PermissionDeniedError):
        read_envelope(response(403, failure("X", 999, "Odd")), GRANTED_APPS, "main")
    with pytest.raises(APIError) as raised:
        read_envelope(response(200, failure("X", 999, "Odd")), GRANTED_APPS, "main")
    assert type(raised.value) is APIError


def test_an_error_in_a_200_is_still_an_error() -> None:
    with pytest.raises(PermissionDeniedError):
        read_envelope(response(200, failure("X", 3, "Denied")), GRANTED_APPS, "main")


def test_a_failure_with_no_envelope_is_decided_by_the_status() -> None:
    with pytest.raises(PermissionDeniedError) as raised:
        read_envelope(response(403, b"Forbidden"), GRANTED_APPS, "main")
    assert raised.value.quickbase_code is None


@pytest.mark.parametrize("body", [b"Fine", b"<other/>", b"<qdbapi><errcode>x</errcode></qdbapi>"])
def test_a_success_that_isnt_an_envelope_is_invalid(body: bytes) -> None:
    with pytest.raises(InvalidResponseError):
        read_envelope(response(200, body), GRANTED_APPS, "main")


def test_a_success_returns_the_parsed_body() -> None:
    root = read_envelope(response(200, envelope("X", "<a>1</a>")), GRANTED_APPS, "main")
    assert isinstance(root, Element)
    assert root.text_of("a") == "1"


@pytest.mark.parametrize(
    ("code", "status"), [(77, 429), (100, 503), (101, 503), (104, 503), (105, 503), (3, 403)]
)
def test_what_retrying_counts_a_response_as(code: int, status: int) -> None:
    assert retry_status(response(403, failure("X", code, "Nope"))) == status


def test_a_page_that_mentions_an_error_code_isnt_mistaken_for_one() -> None:
    body = envelope("X", "<pagebody>&lt;errcode&gt;77&lt;/errcode&gt;</pagebody>")
    assert retry_status(response(200, body)) == 200


@pytest.mark.parametrize(
    ("realm", "expected"),
    [
        ("acme.quickbase.com", "https://acme.quickbase.com/db/main"),
        ("acme.quickbase.eu", "https://acme.quickbase.eu/db/main"),
    ],
)
def test_only_quickbase_hosts_get_a_token(realm: str, expected: str) -> None:
    assert target_url(realm, DEFAULT_BASE_URL, "main") == expected


@pytest.mark.parametrize(
    "realm",
    [
        "evil.example",
        "acme.quickbase.com.evil.example",
        "127.0.0.1",
        "quickbase.com.evil.example",
        "notquickbase.com",
        "acme.evilquickbase.com",
    ],
)
def test_any_other_host_is_refused(realm: str) -> None:
    with pytest.raises(ConfigurationError):
        target_url(realm, DEFAULT_BASE_URL, "main")


def test_a_loopback_base_url_is_used_for_tests() -> None:
    assert target_url("anything", "http://127.0.0.1:9/v1", "bq1") == "http://127.0.0.1:9/db/bq1"


@pytest.mark.parametrize("base_url", ["https://proxy.example/v1", "https://api.quickbase.eu/v1"])
def test_a_custom_base_url_is_refused(base_url: str) -> None:
    with pytest.raises(ConfigurationError):
        target_url("acme.quickbase.com", base_url, "main")


@pytest.mark.parametrize("target", ["../x", "a/b", "a?b", ""])
def test_a_target_that_isnt_an_id_is_refused(target: str) -> None:
    with pytest.raises(ConfigurationError):
        target_url("acme.quickbase.com", DEFAULT_BASE_URL, target)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<pagebody>plain text</pagebody>", "plain text"),
        ("<pagebody></pagebody>", ""),
        ("<pagebody>\r\nline1<BR/>\nline2<BR/>\n\r\n\t</pagebody>", "line1\nline2\n"),
        ("<pagebody>\r\nline1<BR/>\nline2\r\n\t</pagebody>", "line1\nline2"),
        ("<pagebody>\r\n<BR/>\n\r\n\t</pagebody>", "\n"),
        ("<pagebody>\r\na<BR/>\n<BR/>\nb\r\n\t</pagebody>", "a\n\nb"),
        ("<pagebody>\r\n&lt;BR/&gt;<BR/>\nb\t  \r\n\t</pagebody>", "<BR/>\nb\t  "),
    ],
)
def test_page_text_is_rebuilt_from_how_quickbase_returns_it(body: str, expected: str) -> None:
    root = parse_xml(envelope("API_GetDBPage", body))
    assert page_text_from_xml(root) == expected


def test_a_real_page_response_reads_back() -> None:
    # A real response from 2026-11-09, with its text replaced.
    body = (
        "<pagebody>\n&lt;!doctype html&gt;<BR/>\n&lt;title&gt;A page&lt;/title&gt;<BR/>\n"
        "&lt;p&gt;It&#039;s used in tests.&lt;/p&gt;<BR/>\n\n\t</pagebody>"
    )
    root = parse_xml(envelope("API_GetDBPage", body))
    assert page_text_from_xml(root) == (
        "<!doctype html>\n<title>A page</title>\n<p>It's used in tests.</p>\n"
    )


def test_pages_are_listed_with_their_types() -> None:
    body = (
        '<pages><page id="6" type="Text">report.html</page>'
        '<page id="1" type="Home Page">Dashboard</page></pages>'
    )
    assert [(p.id, p.name, p.type) for p in pages_from_xml(parse_xml(envelope("X", body)))] == [
        (6, "report.html", "Text"),
        (1, "Dashboard", "Home Page"),
    ]


@pytest.mark.parametrize(
    ("given", "expected"),
    [("a\r\nb", "a\nb"), ("a\rb", "a\nb"), ("a\nb", "a\nb"), ("a\r\n\r\nb", "a\n\nb")],
)
def test_line_endings_are_normalized(given: str, expected: str) -> None:
    assert normalize_text(given) == expected


def test_the_same_text_hashes_the_same_whatever_its_line_endings() -> None:
    assert content(page_id=1, name=None, text="a\r\nb").sha256 == text_sha256("a\nb")


def test_a_pages_repr_hides_its_text() -> None:
    page = content(page_id=1, name="a.html", text="secret words")
    assert "secret" not in repr(page)
    assert "length=12" in repr(page)
    preview = build_preview("a.html", page, "other secret")
    assert "secret" not in repr(preview)


def test_a_preview_says_what_changes() -> None:
    page = content(page_id=1, name="a.html", text="one\ntwo\n")
    preview = build_preview("a.html", page, "one\nthree\n")
    assert "-two" in preview.diff
    assert "+three" in preview.diff
    assert not preview.unchanged
    assert build_preview("a.html", page, "one\ntwo\n").unchanged
    assert not build_preview("new.html", None, "").unchanged


@pytest.mark.parametrize("ref", ["", "  ", 0, -1, True, None, 1.5, ["a"]])
def test_a_page_needs_a_name_or_id(ref: object) -> None:
    with pytest.raises(InvalidValueError):
        check_ref(ref)


def test_code_page_links_are_escaped() -> None:
    assert code_page_url("acme.quickbase.com", "bq1", "a b.html") == (
        "https://acme.quickbase.com/db/bq1?a=dbpage&pagename=a+b.html"
    )
    assert code_page_url("acme.quickbase.com", "bq1", 6) == (
        "https://acme.quickbase.com/db/bq1?a=dbpage&pageID=6"
    )
