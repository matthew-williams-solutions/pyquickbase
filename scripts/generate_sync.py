"""Generate the sync client and its tests from the async code.

uv run scripts/generate_sync.py          # rewrite src/**/_sync and tests/_sync
uv run scripts/generate_sync.py --check  # fail if the generated files are out of date
"""

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = [
    (ROOT / "src/pyquickbase/_async", ROOT / "src/pyquickbase/_sync"),
    (ROOT / "tests/_async", ROOT / "tests/_sync"),
]
HEADER = "# Generated from the async code by scripts/generate_sync.py. Do not edit.\n"
SUBSTITUTIONS = [
    (r"^pytestmark = pytest\.mark\.anyio\n", ""),
    (r"\basync (def|with|for)\b", r"\1"),
    (r"\bawait ", ""),
    (r"\bAsync([A-Z])", r"\1"),
    (r"__aenter__", "__enter__"),
    (r"__aexit__", "__exit__"),
    (r"\baclose\b", "close"),
    (r"\b_async\b", "_sync"),
    (r"\banyio\.sleep\b", "time.sleep"),
    (r"^import anyio$", "import time"),
]


def _ruff(*args: str, source: str, path: Path) -> str:
    command = [sys.executable, "-m", "ruff", *args, "--stdin-filename", str(path), "-"]
    return subprocess.run(command, input=source, text=True, check=True, capture_output=True).stdout


def render(source: str, path: Path) -> str:
    for pattern, replacement in SUBSTITUTIONS:
        source = re.sub(pattern, replacement, source, flags=re.MULTILINE)
    # Dropping keywords changes line lengths and import order, so normalize the result.
    source = _ruff("check", "--fix-only", "--select", "I", source=source, path=path)
    source = _ruff("format", source=source, path=path)
    return HEADER + source if source.strip() else source


def expected_files() -> dict[Path, str]:
    return {
        target / file.relative_to(source_dir): render(
            file.read_text(), target / file.relative_to(source_dir)
        )
        for source_dir, target in PACKAGES
        for file in sorted(source_dir.rglob("*.py"))
    }


def existing_files() -> set[Path]:
    return {file for _, target in PACKAGES for file in target.rglob("*.py")}


def check(expected: dict[Path, str]) -> int:
    stale = 0
    for path in sorted(existing_files() - expected.keys()):
        print(f"{path.relative_to(ROOT)}: no longer generated, should be removed")
        stale += 1
    for path, content in expected.items():
        current = path.read_text() if path.exists() else ""
        if current != content:
            name = str(path.relative_to(ROOT))
            sys.stdout.writelines(
                difflib.unified_diff(
                    current.splitlines(True), content.splitlines(True), name, f"{name} (expected)"
                )
            )
            stale += 1
    if stale:
        print(f"{stale} generated file(s) out of date. Run: uv run scripts/generate_sync.py")
    return 1 if stale else 0


def write(expected: dict[Path, str]) -> None:
    for path in existing_files() - expected.keys():
        path.unlink()
    for path, content in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()
    expected = expected_files()
    if args.check:
        return check(expected)
    write(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
