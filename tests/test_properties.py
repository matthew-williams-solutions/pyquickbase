"""Randomized tests of the code that keeps users safe. Hypothesis looks for a case that breaks."""

import contextlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

from pyquickbase import (
    ConfigurationError,
    F,
    FormulaResult,
    InvalidResponseError,
    InvalidValueError,
    Policy,
    QueryError,
    RetryPolicy,
    parse_where,
)
from pyquickbase._config import (
    build_url,
    is_read,
    normalize_realm,
    validate_base_url,
    validate_token,
)
from pyquickbase._models import (
    FieldIndex,
    app_info_from_api,
    field_from_api,
    formula_result_from_api,
    page_from_api,
    parse,
    relationship_from_api,
    report_info_from_api,
    table_info_from_api,
)
from pyquickbase._query import compile_where, format_value
from pyquickbase._retry import parse_retry_after
from pyquickbase._write_request import describe_write
from pyquickbase._writes import (
    FileUpload,
    WriteResult,
    build_rows,
    merge_write_result,
    refuse_blank_rows,
    split_batches,
    to_api_value,
)

# A quick run by default. Set QB_DEEP_TESTS=1 to look much harder.
EXAMPLES = 2000 if os.environ.get("QB_DEEP_TESTS") == "1" else 40
settings.register_profile(
    "sdk", deadline=None, suppress_health_check=[HealthCheck.too_slow], max_examples=EXAMPLES
)
settings.load_profile("sdk")

FIELDS = [
    field_from_api({"id": 3, "label": "Record ID#", "fieldType": "recordid"}),
    field_from_api({"id": 6, "label": "Title", "fieldType": "text"}),
    field_from_api({"id": 7, "label": "Amount", "fieldType": "numeric"}),
]
INDEX = FieldIndex(FIELDS)
# Text likely to hit the query syntax, mixed with anything at all.
TRICKY = st.sampled_from(
    [
        "OR", "AND", "or", " ", "\t", "\n", "'", "\\", "\\'", "{", "}", "_FID_", "_fid_6", "_FID_6",
        ".", "EX", "(", ")", "%", "*", " ", " ", "'}OR{3.GT.'0",
    ]
)  # fmt: skip
TEXT = st.one_of(st.text(), st.lists(st.one_of(TRICKY, st.text(max_size=3))).map("".join))
OPERATORS = st.sampled_from(["EX", "XEX", "CT", "XCT", "SW", "XSW", "TV", "XTV", "HAS", "LT", "GT"])


@given(TEXT, OPERATORS)
@example("a OR b", "EX")
@example("_FID_6", "EX")
@example("zz'}OR{3.GT.'0", "EX")
def test_a_value_stays_one_condition_or_is_refused(value: str, operator: str) -> None:
    try:
        clause = compile_where(F("Title").op(operator, value), INDEX)
    except QueryError:
        return
    found = parse_where(clause, FIELDS)
    assert len(found) == 1
    assert (found[0].field_id, found[0].operator) == (6, operator)
    assert found[0].value.lower() == value.lower()


@given(TEXT)
@example("a OR b")
@example("a\tAND\tb")
@example("OR")
def test_no_value_reaches_quickbase_as_syntax(value: str) -> None:
    try:
        sent = format_value(value)
    except QueryError:
        return
    # What the live API treats as syntax, per docs/api-notes.md.
    assert not re.search(r"(?:^|[ \t])(?:OR|AND)(?:$|[ \t])", sent)
    assert not re.match(r"\s*\\?_fid_", sent, re.IGNORECASE)
    assert "{" not in sent
    assert "}" not in sent
    assert not re.search(r"(?<!\\)(?:\\\\)*'", sent), "An unescaped quote could end the value."


@given(st.text(alphabet=st.characters(exclude_characters="{}")))
def test_text_without_syntax_is_unchanged_apart_from_case_and_escapes(value: str) -> None:
    assume(not re.search(r"(?:^|\s)_fid_", value, re.IGNORECASE))
    sent = format_value(value)
    assert sent.replace("\\\\", "\\").replace("\\'", "'").lower() == value.lower()


@given(
    st.recursive(
        TEXT.map(lambda v: ("leaf", v)),
        lambda inner: st.tuples(st.sampled_from("&|"), inner, inner),
        max_leaves=8,
    )
)
def test_combined_conditions_parse_back_with_every_condition(tree: Any) -> None:
    def build(node: Any) -> Any:
        if node[0] == "leaf":
            return F("Title") == node[1]
        left, right = build(node[1]), build(node[2])
        return left & right if node[0] == "&" else left | right

    def leaves(node: Any) -> int:
        return 1 if node[0] == "leaf" else leaves(node[1]) + leaves(node[2])

    try:
        clause = compile_where(build(tree), INDEX)
    except QueryError:
        return
    assert len(parse_where(clause, FIELDS)) == leaves(tree)


@given(TEXT)
def test_a_where_string_is_accepted_or_refused_cleanly(where: str) -> None:
    with contextlib.suppress(QueryError):
        parse_where(where, FIELDS)


PATHS = st.one_of(
    st.text(max_size=40),
    st.sampled_from(["/records", "/records/query", "/formula/run", "/reports/1/run", "/tables/x"]),
    st.lists(
        st.sampled_from(["records", "query", "reports", "1", "run", "..", ".", "%2e", ""]),
        max_size=5,
    ).map(lambda parts: "/" + "/".join(parts)),
    st.text(max_size=8).map(lambda end: "/reports/1/run" + end),
    st.text(max_size=8).map(lambda end: "/records/query" + end),
    st.text(alphabet="0123456789٠١٢٣", max_size=4).map(lambda n: f"/reports/{n}/run"),
)
METHODS = st.sampled_from(
    ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "post", "GeT", ""]
)
SAFE_POSTS = {"/records/query", "/formula/run"}


READ_ONLY = Policy.read_only().bind_offline()


@given(METHODS, PATHS)
@example("POST", "/reports/1/run\n")
@example("POST", "/reports/١/run")
@example("POST", "/records/query/")
@example("POST", "/records/query/../../records")
@example("POST", "/RECORDS/QUERY")
@example("DELETE", "/records/query")
@example("post", "/records")
def test_read_only_allows_only_known_reads(method: str, path: str) -> None:
    allowed = method == "POST" and (
        path in SAFE_POSTS or re.fullmatch(r"/reports/[0-9]+/run", path, re.ASCII) is not None
    )
    # The transport sends a known read as it is, and asks the policy about anything else.
    assert is_read(method, path) == allowed
    if not allowed:
        assert not READ_ONLY.decide(describe_write(method, path, None)).allowed


@given(PATHS)
@example("//evil.example/x")
@example("/\\evil.example")
@example("/@evil.example")
@example("/x?token=1")
@example("/x#y")
def test_a_path_cant_send_the_token_to_another_host(path: str) -> None:
    base = "https://api.quickbase.com/v1"
    try:
        url = httpx.URL(build_url(base, path))
    except (ValueError, httpx.InvalidURL):
        return
    assert url.host == "api.quickbase.com"
    assert url.scheme == "https"
    assert not url.userinfo


@given(st.text(max_size=60))
@example("http://127.0.0.1.evil.example")
@example("http://localhost@evil.example")
@example("http://evil.example#localhost")
@example("HTTP://LOCALHOST")
def test_base_url_is_https_or_this_machine(base_url: str) -> None:
    try:
        checked = validate_base_url(base_url)
    except (ConfigurationError, ValueError):
        return
    url = httpx.URL(checked)
    assert url.scheme == "https" or url.host in ("localhost", "127.0.0.1", "::1")


@given(st.text(max_size=60))
@example("acme.quickbase.com\r\nX-Evil: 1")
@example("https://acme.quickbase.com/db/x")
@example("user:pass@acme.quickbase.com")
def test_a_realm_is_a_plain_hostname(realm: str) -> None:
    try:
        host = normalize_realm(realm)
    except ConfigurationError:
        return
    assert re.fullmatch(r"[a-z0-9.-]+", host)
    assert "." in host


@given(st.text(max_size=40))
def test_a_bad_token_never_appears_in_the_error(token: str) -> None:
    message = ""
    try:
        checked = validate_token(token)
    except ConfigurationError as exc:
        message = str(exc)
    else:
        assert checked
        assert not any(c.isspace() for c in checked)
    assert len(token.strip()) < 4 or token.strip() not in message


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=5),
    lambda inner: (
        st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=5), inner, max_size=3)
    ),
    max_leaves=6,
)
KEYS = st.sampled_from(
    [
        "fields",
        "data",
        "metadata",
        "id",
        "label",
        "type",
        "totalRecords",
        "skip",
        "3",
        "value",
        "name",
        "fieldType",
        "properties",
        "choices",
        "result",
        "resultType",
        "relationships",
        "parentTableId",
        "childTableId",
        "foreignKeyField",
        "lookupFields",
    ]
)
SHAPED = st.recursive(
    JSON,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(KEYS, inner, max_size=5),
    max_leaves=12,
)
PARSERS = [
    app_info_from_api,
    table_info_from_api,
    field_from_api,
    relationship_from_api,
    report_info_from_api,
    formula_result_from_api,
    page_from_api,
]


@pytest.mark.parametrize("parser", PARSERS, ids=lambda p: p.__name__)
@given(payload=SHAPED)
def test_any_response_parses_or_is_an_invalid_response(parser: Any, payload: Any) -> None:
    with contextlib.suppress(InvalidResponseError):
        parse(parser, payload, "test")


@given(st.text(max_size=20), st.sampled_from(["bool", "number", "null", "text", "date", "other"]))
@example("NaN", "number")
@example("Infinity", "number")
@example("1e999999", "number")
@example("sNaN", "number")
def test_a_formula_value_never_raises(text: str, kind: str) -> None:
    value = FormulaResult(text=text, type=kind).value
    if kind == "bool" and text in ("0", "1"):
        assert value is (text == "1")


@given(st.text(max_size=60))
@example("Fri, 31 Dec 99999 23:59:59 GMT")
@example("Mon, 01 Jan 0001 00:00:00 +1400")
@example("9" * 400)
@example("٣")
def test_any_retry_after_header_is_handled(value: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    seconds = parse_retry_after(value, now)
    assert seconds is None or seconds >= 0
    delay = RetryPolicy().rate_limit_delay(0, seconds)
    assert delay is None or 0 <= delay <= 60 or seconds is None


@given(
    st.lists(st.text(max_size=30), max_size=30),
    st.integers(min_value=1, max_value=12),
)
def test_batches_keep_every_row_in_order(rows: list[str], batch_size: int) -> None:
    built = [{"6": {"value": row}} for row in rows]
    ranges = list(split_batches(built, batch_size))
    covered = [index for start, end in ranges for index in range(start, end)]
    assert covered == list(range(len(built)))
    assert all(0 < end - start <= batch_size for start, end in ranges)


@given(
    st.lists(st.sampled_from([10, 1000, 2_000_000, 9_000_000, 30_000_000, 45_000_000]), max_size=12)
)
def test_batches_stay_under_the_size_limit(sizes: list[int]) -> None:
    built = [{"6": {"value": "x" * size}} for size in sizes]
    limit = 40 * 1024 * 1024
    for start, end in split_batches(built, 1000):
        total = sum(len(json.dumps(row, separators=(",", ":"))) + 1 for row in built[start:end])
        assert total <= limit or end - start == 1


@given(st.lists(st.lists(st.integers(1, 9), max_size=3), max_size=5))
def test_row_errors_keep_their_position_across_batches(failures: list[list[int]]) -> None:
    result, offset, expected = WriteResult(), 0, []
    for lines in failures:
        unique = sorted(set(lines))
        payload = {
            "metadata": {
                "createdRecordIds": [],
                "updatedRecordIds": [],
                "unchangedRecordIds": [],
                "lineErrors": {str(line): ["Bad"] for line in unique},
            }
        }
        result = merge_write_result(result, payload, offset)
        expected += [line - 1 + offset for line in unique]
        offset += 10
    assert [error.index for error in result.errors] == expected


@given(
    st.one_of(
        st.none(), st.just(""), st.just([]), st.text(max_size=3), st.integers(), st.booleans()
    )
)
def test_blank_rows_are_refused_and_others_arent(value: Any) -> None:
    rows = build_rows([{"Title": value}], INDEX)
    blank = value is None or value == "" or value == []
    if blank:
        with pytest.raises(InvalidValueError, match="no values"):
            refuse_blank_rows(rows)
    else:
        refuse_blank_rows(rows)


@given(st.text(max_size=30), st.binary(max_size=20))
def test_a_file_name_cant_hold_a_path_or_control_characters(name: str, data: bytes) -> None:
    try:
        upload = FileUpload(name, data)
    except InvalidValueError:
        return
    assert upload.file_name
    assert "/" not in upload.file_name
    assert "\\" not in upload.file_name
    assert all(ord(c) >= 32 and ord(c) != 127 for c in upload.file_name)
    assert str(data)[:6] not in repr(upload) or not data


@given(
    st.one_of(
        st.text(max_size=5),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.booleans(),
        st.none(),
    )
)
def test_a_written_value_is_valid_json(value: Any) -> None:
    json.dumps(to_api_value(value), allow_nan=False)


@given(
    st.text(
        alphabet=st.characters(exclude_categories=("Cs", "Cc", "Cn"), include_characters="\t\n")
    )
)
def test_any_page_text_survives_the_builder_and_the_parser(text: str) -> None:
    from pyquickbase._xml import build_body, parse_xml

    body = parse_xml(build_body({"pagebody": text}))
    assert body.text_of("pagebody") == (text or None)
