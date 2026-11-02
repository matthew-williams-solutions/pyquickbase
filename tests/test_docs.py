"""Keep the docs honest: examples must parse, and every public name must be documented."""

import ast
import re
import textwrap
from pathlib import Path

import pytest

import pyquickbase

ROOT = Path(__file__).resolve().parent.parent
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
FENCE = re.compile(r"^( *)```python\n(.*?)^\1```", re.DOTALL | re.MULTILINE)


def examples() -> list[tuple[str, str]]:
    return [
        (f"{page.relative_to(ROOT)}:{page.read_text()[: m.start()].count(chr(10)) + 1}", m[2])
        for page in PAGES
        for m in FENCE.finditer(page.read_text())
    ]


@pytest.mark.parametrize(("where", "code"), examples(), ids=[where for where, _ in examples()])
def test_examples_parse(where: str, code: str) -> None:
    source = textwrap.dedent(code)
    compile(source, where, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def test_there_are_examples_to_check() -> None:
    assert len(examples()) > 20


def test_every_public_name_is_in_the_reference() -> None:
    reference = (ROOT / "docs/reference.md").read_text()
    documented = set(re.findall(r"^::: pyquickbase\.(\w+)$", reference, re.MULTILINE))
    assert documented == set(pyquickbase.__all__) - {"__version__"}
