# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""What happens when the network or the server misbehaves, using a real socket on this machine."""

import json
import socketserver
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler
from typing import Any

import httpx
import pytest

from pyquickbase import (
    APIError,
    InvalidResponseError,
    NetworkError,
    NotFoundError,
    Quickbase,
    QuickbaseError,
    RateLimitError,
    RequestTimeoutError,
    RetryPolicy,
    ServerError,
    WriteError,
)

TOKEN = "fake-user-token"
FIELDS = [
    {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    {"id": 6, "label": "Title", "fieldType": "text"},
    {"id": 7, "label": "Attachment", "fieldType": "file"},
]
FAST = RetryPolicy(max_retries=2, base_delay=0, max_delay=0)
Reply = tuple[int, dict[str, str], bytes] | str


def reply(body: Any, status: int = 200, **headers: str) -> Reply:
    return status, {k.replace("_", "-"): v for k, v in headers.items()}, json.dumps(body).encode()


def page(start: int, count: int, total: int) -> Reply:
    rows = [
        {"3": {"value": n}, "6": {"value": f"T{n}"}} for n in range(start + 1, start + count + 1)
    ]
    fields = [{"id": 3, "label": "Record ID#", "type": "recordid"}]
    fields.append({"id": 6, "label": "Title", "type": "text"})
    return reply(
        {"fields": fields, "data": rows, "metadata": {"totalRecords": total, "skip": start}}
    )


def written(*created: int, line_errors: dict[str, list[str]] | None = None) -> Reply:
    metadata: dict[str, Any] = {
        "createdRecordIds": list(created),
        "updatedRecordIds": [],
        "unchangedRecordIds": [],
    }
    if line_errors:
        metadata["lineErrors"] = line_errors
    return reply({"data": [], "metadata": metadata}, 207 if line_errors else 200)


class Server(socketserver.ThreadingTCPServer):
    """Answers schema lookups, then plays back scripted replies for everything else."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.script: list[Reply] = []
        self.sent: list[tuple[str, str, dict[str, str], bytes]] = []
        self.release = threading.Event()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1"

    def count(self, path: str) -> int:
        return sum(1 for _, seen, _, _ in self.sent if seen == f"/v1{path}")


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _answer(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length)
        path = self.path.split("?")[0]
        self.server.sent.append((self.command, path, dict(self.headers), body))
        if path == "/v1/tables":
            return self._send(reply([{"id": "bqtasks", "name": "Tasks"}]))
        if path == "/v1/fields":
            return self._send(reply(FIELDS))
        if path.endswith("/reports") or path == "/v1/reports":
            return self._send(reply([]))
        return self._send(self.server.script.pop(0))

    do_GET = do_POST = do_DELETE = _answer

    def _send(self, action: Reply) -> None:
        if action == "drop":
            self.close_connection = True
            self.connection.close()
            return
        if action == "hang":
            self.server.release.wait(10)
            self.close_connection = True
            return
        if isinstance(action, str):
            # A response that stops partway through, with these raw bytes as its body so far.
            head = "HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n"
            self.wfile.write(head.encode() + action.encode())
            self.wfile.flush()
            self.close_connection = True
            self.connection.close()
            return
        status, headers, body = action
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def start() -> tuple[Server, Callable[[], None]]:
    server = Server()
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()

    def stop() -> None:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)

    return server, stop


@pytest.fixture
def server() -> Iterator[Server]:
    running, stop = start()
    yield running
    stop()


def client(server: Server, **kwargs: Any) -> Quickbase:
    settings: dict[str, Any] = {"retry": FAST, "timeout": 5, **kwargs}
    return Quickbase(realm="acme", token=TOKEN, base_url=server.url, **settings)


def table(qb: Quickbase) -> Any:
    return qb.app("bq1234567").table("Tasks")


def read_all(source: Any, seen: list[int]) -> None:
    for record in source.query(page_size=2):
        seen.append(record.id)


@pytest.mark.parametrize("failure", ["drop", '{"fields": [{"id": 3'], ids=["dropped", "truncated"])
def test_a_failure_on_a_later_page_raises_after_the_earlier_records(
    server: Server, failure: str
) -> None:
    server.script = [page(0, 2, 4), failure]
    seen: list[int] = []
    with client(server) as qb:
        with pytest.raises(NetworkError) as caught:
            read_all(table(qb), seen)
    assert seen == [1, 2]
    assert TOKEN not in str(caught.value)
    assert server.count("/records/query") == 2  # A read could be retried, but nothing hides this.


def test_every_record_arrives_once_across_pages(server: Server) -> None:
    server.script = [page(0, 2, 5), page(2, 2, 5), page(4, 1, 5)]
    with client(server) as qb:
        assert [r.id for r in table(qb).query(page_size=2)] == [1, 2, 3, 4, 5]


def test_a_short_page_advances_by_what_arrived(server: Server) -> None:
    server.script = [page(0, 1, 3), page(1, 1, 3), page(2, 1, 3)]
    with client(server) as qb:
        assert [r.id for r in table(qb).query(page_size=100)] == [1, 2, 3]
    skips = [json.loads(sent[3])["options"]["skip"] for sent in server.sent if sent[3]]
    assert skips == [0, 1, 2]


def test_an_empty_page_before_the_end_raises_instead_of_looping(server: Server) -> None:
    server.script = [page(0, 2, 5), page(2, 0, 5)]
    with client(server) as qb:
        with pytest.raises(InvalidResponseError, match="empty page"):
            [r for r in table(qb).query(page_size=2)]


def test_a_page_that_ignores_skip_raises_instead_of_looping(server: Server) -> None:
    server.script = [page(0, 2, 5), page(0, 2, 5), page(0, 2, 5), page(0, 2, 5)]
    seen: list[int] = []
    with client(server) as qb:
        with pytest.raises(InvalidResponseError, match="skip"):
            read_all(table(qb), seen)
    assert seen == [1, 2]


@pytest.mark.parametrize(
    "bad",
    [
        (200, {}, b"<html>Bad gateway</html>"),
        (200, {}, b'{"fields": ['),
        (200, {}, b"\xff\xfe not text"),
        (200, {}, b"[]"),
        (200, {}, b"null"),
        (200, {}, b"{}"),
        (200, {}, b'{"fields": [], "data": [{"3": "not a cell"}], "metadata": {}}'),
        (200, {}, b'{"fields": [{"id": 3}], "data": [], "metadata": {"skip": 0}}'),
        (200, {}, b'{"fields": 7, "data": 7, "metadata": 7}'),
    ],
    ids=["html", "cut-json", "binary", "list", "null", "empty", "bad-cell", "no-total", "numbers"],
)
def test_a_bad_success_body_is_an_invalid_response(server: Server, bad: Reply) -> None:
    server.script = [bad]
    with client(server) as qb:
        with pytest.raises(InvalidResponseError):
            table(qb).query_page()


def test_an_empty_success_body_is_an_invalid_response(server: Server) -> None:
    server.script = [(200, {}, b"")]
    with client(server) as qb:
        with pytest.raises(InvalidResponseError):
            table(qb).query_page()


# Under Trio, httpx leaves a stream for the garbage collector when decoding fails, which warns.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_a_corrupt_compressed_body_is_a_quickbase_error(server: Server) -> None:
    server.script = [(200, {"Content-Encoding": "gzip"}, b"this is not gzip")]
    with client(server) as qb:
        with pytest.raises(QuickbaseError) as caught:
            table(qb).query_page()
    assert isinstance(caught.value, InvalidResponseError | NetworkError)


def test_a_write_that_times_out_is_not_retried(server: Server) -> None:
    server.script = ["hang"]
    with client(server, timeout=0.3) as qb:
        with pytest.raises(RequestTimeoutError) as caught:
            table(qb).insert([{"Title": "a"}])
    assert server.count("/records") == 1
    assert TOKEN not in str(caught.value)


def test_a_write_dropped_after_sending_is_not_retried(server: Server) -> None:
    server.script = ["drop", written(1)]
    with client(server) as qb:
        with pytest.raises(NetworkError):
            table(qb).insert([{"Title": "a"}])
    assert server.count("/records") == 1


def test_a_refused_connection_is_retried_and_then_raises() -> None:
    running, stop = start()
    url = running.url
    stop()
    with Quickbase(realm="acme", token=TOKEN, base_url=url, retry=FAST) as qb:
        with pytest.raises(NetworkError) as caught:
            qb.request("POST", "/records", json={})
    assert not isinstance(caught.value, RequestTimeoutError)
    assert TOKEN not in str(caught.value)


def test_a_batch_failing_outright_reports_what_was_written(server: Server) -> None:
    server.script = [written(1, 2), reply({"message": "Boom"}, 500)]
    with client(server) as qb:
        with pytest.raises(WriteError) as caught:
            table(qb).insert([{"Title": str(n)} for n in range(4)], batch_size=2)
    assert caught.value.result.created_ids == (1, 2)
    assert "row 2" in str(caught.value)
    assert isinstance(caught.value.__cause__, ServerError)
    assert server.count("/records") == 2


def test_a_failure_on_the_first_batch_is_the_plain_error(server: Server) -> None:
    server.script = [reply({"message": "Boom"}, 500)]
    with client(server) as qb:
        with pytest.raises(ServerError):
            table(qb).insert([{"Title": "a"}])


def test_a_timeout_on_a_later_batch_keeps_what_was_written(server: Server) -> None:
    server.script = [written(1), "hang"]
    with client(server, timeout=0.3) as qb:
        with pytest.raises(WriteError) as caught:
            table(qb).insert([{"Title": "a"}, {"Title": "b"}], batch_size=1)
    assert caught.value.result.created_ids == (1,)
    assert isinstance(caught.value.__cause__, RequestTimeoutError)


def test_row_errors_are_numbered_across_batches(server: Server) -> None:
    server.script = [
        written(1, line_errors={"2": ["Bad first"]}),
        written(3, line_errors={"1": ["Bad second"]}),
    ]
    with client(server) as qb:
        with pytest.raises(WriteError) as caught:
            table(qb).insert([{"Title": str(n)} for n in range(4)], batch_size=2)
    result = caught.value.result
    assert [(e.index, e.messages) for e in result.errors] == [
        (1, ("Bad first",)),
        (2, ("Bad second",)),
    ]
    assert result.created_ids == (1, 3)


def test_rate_limits_are_retried_for_writes_too(server: Server) -> None:
    limited = reply({"error": "Quota has been exceeded"}, 429, retry_after="0")
    server.script = [limited, limited, written(1)]
    with client(server) as qb:
        assert (table(qb).insert([{"Title": "a"}])).created_ids == (1,)
    assert server.count("/records") == 3


def test_rate_limit_retries_run_out(server: Server) -> None:
    limited = reply({"error": "Quota has been exceeded"}, 429, retry_after="0")
    server.script = [limited, limited, limited]
    with client(server) as qb:
        with pytest.raises(RateLimitError) as caught:
            table(qb).insert([{"Title": "a"}])
    assert caught.value.retry_after == 0
    assert "Quota has been exceeded" in str(caught.value)
    assert server.count("/records") == 3


@pytest.mark.parametrize("wait", ["3600", "Fri, 31 Dec 2100 23:59:59 GMT", "9" * 400])
def test_a_long_rate_limit_wait_raises_straight_away(server: Server, wait: str) -> None:
    server.script = [reply({"error": "Quota"}, 429, retry_after=wait)]
    with client(server) as qb:
        with pytest.raises(RateLimitError) as caught:
            table(qb).insert([{"Title": "a"}])
    assert server.count("/records") == 1
    assert caught.value.retry_after is not None
    assert caught.value.retry_after > 60


@pytest.mark.parametrize("wait", ["soon", "", "-5", "1.5.5"])
def test_an_unreadable_rate_limit_wait_backs_off(server: Server, wait: str) -> None:
    limited = reply({"error": "Quota"}, 429, retry_after=wait)
    server.script = [limited, written(1)]
    with client(server) as qb:
        assert (table(qb).insert([{"Title": "a"}])).created_ids == (1,)


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (404, b"<html>nope</html>", NotFoundError),
        (500, b"", ServerError),
        (503, b"Service Unavailable", ServerError),
        (502, b"[1, 2]", ServerError),
        (418, b'{"message": 7}', APIError),
        (302, b"", APIError),
    ],
)
def test_odd_error_bodies(server: Server, status: int, body: bytes, kind: type) -> None:
    server.script = [(status, {}, body)]
    with client(server) as qb:
        with pytest.raises(kind) as caught:
            table(qb).query_page()
    assert caught.value.status_code == status
    assert TOKEN not in str(caught.value)


def test_a_redirect_is_not_followed_so_the_token_stays_here(server: Server) -> None:
    other, stop = start()
    try:
        server.script = [(302, {"Location": f"{other.url}/records/query"}, b"")]
        with client(server) as qb:
            with pytest.raises(APIError):
                table(qb).query_page()
        assert other.sent == []
    finally:
        stop()


def test_a_followed_redirect_doesnt_send_the_token_elsewhere(server: Server) -> None:
    other, stop = start()
    try:
        other.script = [page(0, 1, 1)]
        server.script = [(307, {"Location": f"{other.url}/records/query"}, b"")]
        with httpx.Client(follow_redirects=True) as http:
            with client(server, http_client=http, timeout=5) as qb:
                table(qb).query_page()
        assert len(other.sent) == 1
        lowered = {name.lower(): value for name, value in other.sent[0][2].items()}
        assert TOKEN not in str(lowered)
    finally:
        stop()


def test_a_redirect_loop_is_a_quickbase_error(server: Server) -> None:
    loop = (307, {"Location": f"{server.url}/records/query"}, b"")
    server.script = [loop] * 25
    with httpx.Client(follow_redirects=True, max_redirects=3) as http:
        with client(server, http_client=http) as qb:
            with pytest.raises(QuickbaseError):
                table(qb).query_page()


@pytest.mark.parametrize(
    "bad",
    [
        (200, {"Content-Disposition": "attachment; filename*=utf-8''a.txt"}, b"!!!not base64"),
        (200, {}, b"eA=="),
        (200, {"Content-Disposition": "attachment"}, b"eA=="),
    ],
    ids=["not-base64", "no-disposition", "no-file-name"],
)
def test_a_bad_download_is_an_invalid_response(server: Server, bad: Reply) -> None:
    server.script = [bad]
    with client(server) as qb:
        with pytest.raises(InvalidResponseError):
            table(qb).download_file(1, "Attachment")


def test_a_dropped_download_is_a_network_error(server: Server) -> None:
    server.script = ["drop"]
    with client(server) as qb:
        with pytest.raises(NetworkError):
            table(qb).download_file(1, "Attachment")
