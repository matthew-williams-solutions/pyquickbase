"""Fail when the public API changes, so a breaking change can't slip into a 1.x release."""

import difflib
import inspect
import os
import re
from pathlib import Path
from typing import Any

import pyquickbase
from pyquickbase import errors

SNAPSHOT = Path(__file__).parent / "fixtures" / "public_api.txt"

# Operators and protocols people rely on. Other special methods can vary with the Python version.
SPECIAL_METHODS = frozenset(
    f"__{name}__"
    for name in [
        "aenter",
        "aexit",
        "aiter",
        "and",
        "anext",
        "bool",
        "call",
        "contains",
        "enter",
        "eq",
        "exit",
        "ge",
        "getitem",
        "gt",
        "init",
        "iter",
        "le",
        "len",
        "lt",
        "ne",
        "next",
        "or",
    ]
)

# docs/stability.md leaves these constructors out, because the client creates these objects.
CLIENT_MADE = frozenset(
    name + kind for name in ("", "Async") for kind in ("App", "RecordIterator", "Report", "Table")
)


def clean(text: str) -> str:
    """Remove what changes with the module layout or the run, rather than with the API."""
    text = re.sub(r"\bpyquickbase(\.\w+)*\.(?=[A-Za-z])", "", text)
    text = re.sub(r"\b(?:typing|collections\.abc)\.", "", text)
    # Before Python 3.14, `R | None` with a TypeVar prints as `Optional[~R]`. They mean the same.
    text = re.sub(r"\bOptional\[([^\[\]]+)\]", r"\1 | None", text)
    text = pipes(text)
    return re.sub(r" at 0x[0-9a-fA-F]+", "", text)


def pipes(text: str) -> str:
    """Before Python 3.14, a union with a `Literal` prints as `Union[A, B]`. It means `A | B`."""
    while found := re.search(r"\bUnion\[", text):
        parts: list[str] = []
        depth, start = 1, found.end()
        index = start
        while depth:
            char = text[index]
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
            if depth == 0 or (depth == 1 and char == ","):
                parts.append(text[start:index].strip())
                start = index + 1
            index += 1
        text = text[: found.start()] + " | ".join(parts) + text[index:]
    return text


def signature(function: Any) -> str:
    return clean(str(inspect.signature(function)))


def member(name: str, value: Any) -> str | None:
    """Describe one class attribute, or return None if it isn't part of the API."""
    if isinstance(value, property):
        return f"property {name}{signature(value.fget).split('(self)', 1)[-1]}"
    kind = {staticmethod: "staticmethod ", classmethod: "classmethod "}.get(type(value), "")
    function = getattr(value, "__func__", value)
    if inspect.isroutine(function):
        prefix = "async " if inspect.iscoroutinefunction(function) else ""
        return f"{kind}{prefix}def {name}{signature(function)}"
    if name.startswith("__") or inspect.ismemberdescriptor(value):
        return None  # A dataclass field with slots is listed from its annotation instead.
    return f"attribute {name} = {clean(repr(value))}"


def class_lines(name: str, item: type) -> list[str]:
    bases = ", ".join(base.__name__ for base in item.__bases__)
    found: dict[str, str] = {}
    annotations: dict[str, str] = {}
    for owner in reversed(item.__mro__):
        if not owner.__module__.startswith("pyquickbase"):
            continue
        for attribute, annotation in inspect.get_annotations(owner).items():
            plain = annotation.__name__ if isinstance(annotation, type) else str(annotation)
            annotations[attribute] = clean(plain)
        for attribute, value in vars(owner).items():
            public = not attribute.startswith("_") or attribute in SPECIAL_METHODS
            if public and not (attribute == "__init__" and name in CLIENT_MADE):
                line = member(attribute, value)
                if line:
                    found[attribute] = line
    for attribute, annotation in annotations.items():
        if not attribute.startswith("_") and attribute not in found:
            found[attribute] = f"field {attribute}: {annotation}"
    return [f"class {name}({bases})", *(f"    {found[key]}" for key in sorted(found))]


def public_api() -> str:
    names = {name: getattr(pyquickbase, name) for name in pyquickbase.__all__}
    del names["__version__"]
    names.update(
        (name, item)
        for name, item in vars(errors).items()
        if inspect.isclass(item) and item.__module__ == errors.__name__ and name[0] != "_"
    )
    lines: list[str] = []
    for name, item in sorted(names.items()):
        if inspect.isclass(item):
            lines += class_lines(name, item)
        elif inspect.isroutine(item):
            lines.append(f"def {name}{signature(item)}")
        else:
            lines.append(f"value {name} = {clean(repr(item))}")
    return "\n".join(lines) + "\n"


def test_public_api_matches_the_snapshot() -> None:
    current = public_api()
    if os.environ.get("QB_UPDATE_PUBLIC_API"):
        SNAPSHOT.write_text(current, encoding="utf-8", newline="\n")
    expected = SNAPSHOT.read_text(encoding="utf-8")
    difference = "\n".join(
        difflib.unified_diff(expected.splitlines(), current.splitlines(), lineterm="", n=1)
    )
    assert not difference, (
        "The public API changed. A removal or incompatible change needs a major version: see "
        "docs/stability.md. If it's intended, run "
        f"QB_UPDATE_PUBLIC_API=1 uv run pytest tests/test_public_api.py\n{difference}"
    )


def test_snapshot_covers_the_whole_api() -> None:
    expected = SNAPSHOT.read_text(encoding="utf-8")
    missing = [
        name
        for name in set(pyquickbase.__all__) - {"__version__"}
        if not re.search(rf"^(class|def|value) {name}\b", expected, re.MULTILINE)
    ]
    assert not missing
