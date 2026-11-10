# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Code pages: reading, previewing, writing, and the operations a model is given."""

import re
from typing import Any

import httpx
import pytest

from pyquickbase import (
    BadRequestError,
    CodePageChangedError,
    ConfigurationError,
    InvalidConfirmationError,
    InvalidValueError,
    NotFoundError,
    Policy,
    PolicyRefusedError,
    Quickbase,
    allow,
    deny,
    tool_result,
)
from pyquickbase._decision import CODE_PAGE_WARNING
from tests.xml_fake import APP, REALM, TOKEN, envelope, failure


def field(body: bytes, tag: str) -> str | None:
    found = re.search(rf"<{tag}>(.*?)</{tag}>".encode(), body, re.DOTALL)
    return None if found is None else found[1].decode()


class FakePages:
    """Holds a page, and answers the page calls as Quickbase does."""

    def __init__(self, text: str | None = "<h1>Hi</h1>\n") -> None:
        self.text = text
        self.sent: list[httpx.Request] = []
        self.writes = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        action = request.headers["QUICKBASE-ACTION"]
        body = request.content
        if action == "API_ListDBPages":
            pages = (
                '<pages><page id="6" type="Text">a.html</page>'
                '<page id="1" type="Home Page">Dashboard</page></pages>'
            )
            return httpx.Response(200, content=envelope(action, pages))
        if action == "API_GetDBPage":
            if field(body, "pagename") == "Dashboard":
                return httpx.Response(400, content=failure(action, 23, "Home page"))
            if self.text is None or field(body, "pageID") == "99":
                return httpx.Response(
                    400, content=failure(action, 81, "Not found", "Page not found")
                )
            shown = self.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if "\n" in shown:
                shown = "\r\n" + "<BR/>\n".join(shown.split("\n")) + "\r\n\t"
            return httpx.Response(200, content=envelope(action, f"<pagebody>{shown}</pagebody>"))
        assert action == "API_AddReplaceDBPage"
        self.writes += 1
        new = field(body, "pagebody") or ""
        self.text = new.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        return httpx.Response(200, content=envelope(action, "<pageID>6</pageID>"))


def client(handler: Any, **options: Any) -> Quickbase:
    return Quickbase(
        realm=REALM,
        token=TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **options,
    )


def test_a_page_is_read_by_name_or_id() -> None:
    fake = FakePages()
    with client(fake) as qb:
        by_name = qb.app(APP).code_page("a.html").read()
        by_id = qb.app(APP).code_page(6).read()
    assert by_name.text == "<h1>Hi</h1>\n"
    assert by_name.name == "a.html"
    assert by_id.id == 6
    assert b"<pagename>a.html</pagename>" in fake.sent[0].content
    assert b"<pageID>6</pageID>" in fake.sent[1].content


def test_pages_are_listed() -> None:
    with client(FakePages()) as qb:
        pages = qb.app(APP).code_pages()
    assert [page.type for page in pages] == ["Text", "Home Page"]


def test_a_home_page_cant_be_read() -> None:
    with client(FakePages()) as qb:
        with pytest.raises(BadRequestError, match="home page"):
            qb.app(APP).code_page("Dashboard").read()


def test_a_preview_shows_a_diff_without_writing() -> None:
    fake = FakePages()
    with client(fake) as qb:
        preview = qb.app(APP).code_page("a.html").preview_change("<h1>Bye</h1>\n")
    assert "+<h1>Bye</h1>" in preview.diff
    assert preview.current is not None
    assert fake.writes == 0


def test_writing_replaces_the_page_and_returns_what_was_there() -> None:
    fake = FakePages()
    with client(fake) as qb:
        result = qb.app(APP).code_page("a.html").write("new\r\ntext")
    assert not result.created
    assert result.previous is not None
    assert result.previous.text == "<h1>Hi</h1>\n"
    assert result.content.text == "new\ntext"
    assert result.content.id == 6
    assert b"<pagetype>1</pagetype>" in fake.sent[1].content
    assert fake.text == "new\ntext"


def test_a_name_that_isnt_in_use_makes_a_page() -> None:
    fake = FakePages(text=None)
    with client(fake) as qb:
        result = qb.app(APP).code_page("new.html").write("hello")
    assert result.created
    assert result.previous is None


def test_an_id_that_doesnt_exist_is_refused() -> None:
    fake = FakePages()
    with client(fake) as qb:
        with pytest.raises(NotFoundError):
            qb.app(APP).code_page(99).write("hello")
    assert fake.writes == 0


def test_a_stale_hash_writes_nothing() -> None:
    fake = FakePages()
    with client(fake) as qb:
        with pytest.raises(CodePageChangedError):
            qb.app(APP).code_page("a.html").write("x", expected_sha256="0" * 64)
    assert fake.writes == 0


def test_a_matching_hash_writes() -> None:
    fake = FakePages()
    with client(fake) as qb:
        page = qb.app(APP).code_page("a.html")
        current = page.read()
        page.write("x", expected_sha256=current.sha256)
    assert fake.writes == 1


def test_a_home_page_cant_be_overwritten() -> None:
    fake = FakePages()
    with client(fake) as qb:
        with pytest.raises(BadRequestError):
            qb.app(APP).code_page("Dashboard").write("x")
    assert fake.writes == 0


def test_a_page_is_always_escaped() -> None:
    fake = FakePages(text=None)
    with client(fake) as qb:
        qb.app(APP).code_page("new.html").write("<a>&</a>")
    assert b"<pagebody>&lt;a&gt;&amp;&lt;/a&gt;</pagebody>" in fake.sent[1].content


@pytest.mark.parametrize("policy", [Policy.read_only(), Policy()], ids=["read_only", "empty"])
def test_a_policy_without_the_grant_refuses_code_pages(policy: Policy) -> None:
    fake = FakePages()
    with client(fake, policy=policy) as qb:
        with pytest.raises(PolicyRefusedError):
            qb.app(APP).code_pages()
        with pytest.raises(PolicyRefusedError):
            qb.app(APP).code_page("a.html").write("x")
    assert not fake.sent


def test_reading_needs_no_write_grant_but_writing_does() -> None:
    fake = FakePages()
    with client(fake, policy=Policy(rules=[allow.code_pages()])) as qb:
        assert qb.app(APP).code_pages()
        with pytest.raises(PolicyRefusedError) as refused:
            qb.app(APP).code_page("a.html").write("x")
    assert CODE_PAGE_WARNING in str(refused.value)
    assert fake.writes == 0


def test_writing_is_allowed_with_the_grant() -> None:
    fake = FakePages()
    with client(fake, policy=Policy(rules=[allow.code_pages(write=True)])) as qb:
        assert (qb.app(APP).code_page("a.html").write("x")).created is False
    assert fake.writes == 1


def test_a_deny_wins() -> None:
    fake = FakePages()
    policy = Policy(rules=[allow.code_pages(write=True), deny.code_pages()])
    with client(fake, policy=policy) as qb:
        with pytest.raises(PolicyRefusedError):
            qb.app(APP).code_pages()
    assert not fake.sent


def test_a_grant_for_one_app_doesnt_reach_another() -> None:
    from tests.policy_helpers import bind

    bound = bind(allow.code_pages(write=True), app_id=APP)
    fake = FakePages()
    with client(fake, policy=bound) as qb:
        assert qb.app(APP).code_pages()
        fake.sent.clear()
        with pytest.raises(PolicyRefusedError):
            qb.app("bqother").code_pages()
    assert not fake.sent


def test_a_write_decision_says_what_a_page_can_do() -> None:
    policy = Policy(rules=[allow.code_pages(write=True)]).bind_offline()
    decision = policy.allows("write_code_pages", APP)
    assert decision.allowed
    from pyquickbase._read_request import ReadRequest
    from pyquickbase._write_request import WriteRequest

    read = ReadRequest(action="code_pages", app_id=APP, method="POST", path="/db/x")
    assert policy.decide(read).allowed
    write = WriteRequest(
        action="write_code_page",
        table_id=None,
        field_ids=None,
        row_count=None,
        method="POST",
        path="/db/x",
        app_id=APP,
    )
    assert CODE_PAGE_WARNING in policy.decide(write).reason


def test_a_refusal_says_what_would_allow_it() -> None:
    with client(FakePages(), policy=Policy()) as qb:
        with pytest.raises(PolicyRefusedError) as refused:
            qb.app(APP).code_pages()
    assert "allow.code_pages()" in str(refused.value)


@pytest.mark.parametrize("name", ["preview_code_page_change", "apply_code_page_change"])
def test_a_write_operation_needs_the_write_grant(name: str) -> None:
    with client(FakePages(), policy=Policy(rules=[allow.code_pages()])) as qb:
        with pytest.raises(ConfigurationError, match=r"write=True"):
            qb.app(APP).operation(name)  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_a_read_operation_needs_a_grant() -> None:
    with client(FakePages(), policy=Policy()) as qb:
        with pytest.raises(ConfigurationError, match=r"allow\.code_pages"):
            qb.app(APP).operation("read_code_page")


def test_operations_list_and_read() -> None:
    fake = FakePages()
    with client(fake, policy=Policy(rules=[allow.code_pages()])) as qb:
        listed = (qb.app(APP).operation("list_code_pages")).run({})
        read = qb.app(APP).operation("read_code_page")
        found = read.run({"code_page": "a.html"})
    assert tool_result(listed)["code_pages"][0]["name"] == "a.html"
    shown = tool_result(found)["code_page"]
    assert (shown["name"], shown["text"]) == ("a.html", "<h1>Hi</h1>\n")


@pytest.mark.parametrize(
    "arguments",
    [
        {"code_page": ""},
        {"code_page": True},
        {"code_page": 0},
        {},
        {"code_page": "a.html", "extra": 1},
    ],
    ids=["blank", "bool", "zero", "missing", "unknown"],
)
def test_a_bad_argument_sends_nothing(arguments: dict[str, Any]) -> None:
    fake = FakePages()
    with client(fake, policy=Policy(rules=[allow.code_pages()])) as qb:
        read = qb.app(APP).operation("read_code_page")
        with pytest.raises(InvalidValueError):
            read.run(arguments)
    assert not fake.sent


def _operations(qb: Quickbase) -> tuple[Any, Any]:
    return (
        qb.app(APP).operation("preview_code_page_change"),
        qb.app(APP).operation("apply_code_page_change"),
    )


WRITE = {"policy": Policy(rules=[allow.code_pages(write=True)]), "confirmation_key": b"k" * 32}


def test_a_model_applies_a_change_it_previewed() -> None:
    fake = FakePages()
    with client(fake, **WRITE) as qb:
        preview, apply = _operations(qb)
        shown = tool_result(preview.run({"code_page": "a.html", "text": "new"}))
        assert CODE_PAGE_WARNING in shown["warnings"][0]
        assert fake.writes == 0
        arguments = {"code_page": "a.html", "text": "new", "confirmation": shown["confirmation"]}
        result = tool_result(apply.run(arguments))
    assert fake.writes == 1
    assert result["created"] is False
    assert result["previous_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        {"text": "other"},
        {"code_page": "b.html"},
        {"confirmation": "nope"},
        {"confirmation": None},
    ],
    ids=["text", "page", "invalid", "missing"],
)
def test_a_change_needs_its_own_confirmation(change: dict[str, Any]) -> None:
    fake = FakePages()
    with client(fake, **WRITE) as qb:
        preview, apply = _operations(qb)
        shown = tool_result(preview.run({"code_page": "a.html", "text": "new"}))
        arguments = {"code_page": "a.html", "text": "new", "confirmation": shown["confirmation"]}
        arguments.update(change)
        with pytest.raises(InvalidConfirmationError):
            apply.run(arguments)
    assert fake.writes == 0


def test_a_confirmation_stops_working_if_the_page_changes() -> None:
    fake = FakePages()
    with client(fake, **WRITE) as qb:
        preview, apply = _operations(qb)
        shown = tool_result(preview.run({"code_page": "a.html", "text": "new"}))
        fake.text = "someone else's edit"
        with pytest.raises(InvalidConfirmationError, match="page has changed"):
            apply.run({"code_page": "a.html", "text": "new", "confirmation": shown["confirmation"]})
    assert fake.writes == 0


def test_an_unchanged_page_needs_no_confirmation() -> None:
    fake = FakePages()
    with client(fake, **WRITE) as qb:
        preview, _ = _operations(qb)
        shown = tool_result(preview.run({"code_page": "a.html", "text": "<h1>Hi</h1>\n"}))
    assert shown["unchanged"] is True
    assert "confirmation" not in shown


def test_previewing_a_new_page_is_allowed() -> None:
    fake = FakePages(text=None)
    with client(fake, **WRITE) as qb:
        preview, apply = _operations(qb)
        shown = tool_result(preview.run({"code_page": "new.html", "text": "hello"}))
        assert shown["exists"] is False
        written = apply.run(
            {"code_page": "new.html", "text": "hello", "confirmation": shown["confirmation"]}
        )
    assert written.created


def test_a_model_may_write_page_for_code_page() -> None:
    with client(FakePages(), policy=Policy(rules=[allow.code_pages()])) as qb:
        read = qb.app(APP).operation("read_code_page")
        assert (read.run({"page": "a.html"})).name == "a.html"


def test_only_the_writing_tools_say_a_new_name_makes_a_page() -> None:
    with client(FakePages(), policy=Policy(rules=[allow.code_pages(write=True)])) as qb:
        read = qb.app(APP).operation("read_code_page")
        preview = qb.app(APP).operation("preview_code_page_change")
    assert "new name" not in read.input_schema["properties"]["code_page"]["description"]
    assert "new name" in preview.input_schema["properties"]["code_page"]["description"]


def test_a_code_page_link_needs_no_network() -> None:
    with client(FakePages()) as qb:
        assert qb.app(APP).code_page("a.html").url().startswith("https://acme.quickbase.com/")
