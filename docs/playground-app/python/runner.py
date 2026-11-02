"""Run a visitor's code against the simulated API, and report errors in terms of their code."""

import ast
import inspect
import os
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest import mock

import httpx
from simulator import SimulatedQuickbase

import pyquickbase

FILENAME = "playground.py"


@contextmanager
def simulated_clients() -> Iterator[None]:
    """Make the real clients talk to a fresh simulator, so examples read like normal SDK code."""
    transport = httpx.MockTransport(SimulatedQuickbase())

    class Quickbase(pyquickbase.Quickbase):
        def __init__(self, *, realm: str, token: str, **options: Any) -> None:
            options["http_client"] = httpx.Client(transport=transport)
            super().__init__(realm=realm, token=token, **options)

    class AsyncQuickbase(pyquickbase.AsyncQuickbase):
        def __init__(self, *, realm: str, token: str, **options: Any) -> None:
            options["http_client"] = httpx.AsyncClient(transport=transport)
            super().__init__(realm=realm, token=token, **options)

    with (
        mock.patch.object(pyquickbase, "Quickbase", Quickbase),
        mock.patch.object(pyquickbase, "AsyncQuickbase", AsyncQuickbase),
        mock.patch.dict(os.environ, {"QB_REALM": "demo", "QB_USER_TOKEN": "demo"}),
    ):
        yield


def format_error(error: BaseException, code: str) -> str:
    """Show only the visitor's own lines, then the error, without the SDK's internals."""
    lines = code.splitlines()
    frames = [
        f"Line {frame.lineno}: {lines[frame.lineno - 1].strip()}"
        for frame in traceback.extract_tb(error.__traceback__)
        if frame.filename == FILENAME and frame.lineno and frame.lineno <= len(lines)
    ]
    summary = "".join(traceback.format_exception_only(error))
    return "\n".join([*frames, summary]).rstrip()


def _no_input(prompt: str = "") -> str:
    raise RuntimeError("The playground can't ask for input. Set the value in your code instead.")


async def run(code: str) -> str | None:
    """Run the code, allowing top-level `await`. Returns the error text, or None if it worked."""
    try:
        compiled = compile(code, FILENAME, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        with simulated_clients():
            result = eval(compiled, {"__name__": "__main__", "input": _no_input})
            if inspect.iscoroutine(result):
                await result
    except (Exception, SystemExit) as error:
        return format_error(error, code)
    finally:
        # Show a line that didn't end in a newline now, not at the start of the next run.
        sys.stdout.flush()
        sys.stderr.flush()
    return None
