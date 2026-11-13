"""Put what the docs playground needs into docs/playground-app, ready for `zensical build`.

uv run scripts/build_playground.py

Downloads pinned copies of Pyodide and CodeMirror, so the site serves them itself, builds the SDK's
wheel from this checkout, and runs each example to save its output. None of this is committed.
"""

import asyncio
import contextlib
import hashlib
import importlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "docs/playground-app"
VENDOR = APP / "vendor"
STAMP = VENDOR / ".versions"
DATA = APP / "playground-data.json"

PYODIDE_VERSION = "314.0.7"
PYODIDE_SHA256 = "66b7949e5a0cba2250b586d7e368bf12fb482800748d182c4618254b685ffaa0"
CODEMIRROR_VERSION = "5.65.21"
CODEMIRROR_SHA256 = "b26d9d7ae0f42e34b44a8f7667d29a3ab0d3df88d0e79c0dad39dc03ba43f6dc"
PYODIDE_FILES = [
    "pyodide.mjs",
    "pyodide.asm.mjs",
    "pyodide.asm.wasm",
    "python_stdlib.zip",
    "pyodide-lock.json",
]
CODEMIRROR_FILES = {
    "lib/codemirror.js": "codemirror.js",
    "lib/codemirror.css": "codemirror.css",
    "mode/python/python.js": "python.js",
}
PYTHON_PACKAGES = ["httpx", "anyio"]


def download(url: str, sha256: str) -> bytes:
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        content: bytes = response.read()
    actual = hashlib.sha256(content).hexdigest()
    if actual != sha256:
        raise SystemExit(f"{url} has SHA-256 {actual}, expected {sha256}")
    return content


def unpack(url: str, sha256: str, files: dict[str, str], target: Path) -> None:
    """Copy named files out of an npm package, skipping the download if they're all present."""
    if all((target / name).exists() for name in files.values()):
        return
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(download(url, sha256)), mode="r:gz") as archive:
        for member, name in files.items():
            source = archive.extractfile(f"package/{member}")
            if source is None:
                raise SystemExit(f"{url} has no {member}")
            (target / name).write_bytes(source.read())


def vendor_python_packages(target: Path) -> None:
    """Fetch the SDK's dependencies as Pyodide builds them, checked against Pyodide's lock file."""
    packages: dict[str, Any] = json.loads((target / "pyodide-lock.json").read_text())["packages"]
    needed: list[str] = list(PYTHON_PACKAGES)
    for name in needed:
        needed.extend(dep for dep in packages[name]["depends"] if dep not in needed)
    for name in needed:
        package = packages[name]
        path = target / package["file_name"]
        if not path.exists():
            base = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full"
            path.write_bytes(download(f"{base}/{package['file_name']}", package["sha256"]))


def build_wheel() -> str:
    """Build the SDK's wheel from this checkout, and return its file name."""
    for old in VENDOR.glob("pyquickbase-*.whl"):
        old.unlink()
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            ["uv", "build", "--quiet", "--wheel", "--out-dir", out], cwd=ROOT, check=True
        )
        wheel = next(Path(out).glob("pyquickbase-*.whl"))
        shutil.copy(wheel, VENDOR / wheel.name)
        return wheel.name


def build_data(wheel: str) -> dict[str, Any]:
    """Run each example here, so the saved output can't drift from the code."""
    sys.path.insert(0, str(APP / "python"))
    runner = importlib.import_module("runner")
    simulator = importlib.import_module("simulator")
    examples: list[dict[str, str]] = []
    for path in sorted((APP / "examples").glob("*.py")):
        code = path.read_text()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            error = asyncio.run(runner.run(code))
        if error:
            raise SystemExit(f"{path.name} failed:\n{error}")
        examples.append(
            {
                "id": path.stem.split("_", 1)[1].replace("_", "-"),
                "title": code.splitlines()[0].removeprefix("# "),
                "code": code,
                "output": stdout.getvalue(),
            }
        )
    return {"wheel": wheel, "examples": examples, "sample": simulator.sample_tables()}


def main() -> None:
    versions = f"{PYODIDE_VERSION} {CODEMIRROR_VERSION}\n"
    if STAMP.exists() and STAMP.read_text() != versions:
        shutil.rmtree(VENDOR)
    registry = "https://registry.npmjs.org"
    unpack(
        f"{registry}/pyodide/-/pyodide-{PYODIDE_VERSION}.tgz",
        PYODIDE_SHA256,
        {name: name for name in PYODIDE_FILES},
        VENDOR / "pyodide",
    )
    vendor_python_packages(VENDOR / "pyodide")
    unpack(
        f"{registry}/codemirror/-/codemirror-{CODEMIRROR_VERSION}.tgz",
        CODEMIRROR_SHA256,
        CODEMIRROR_FILES,
        VENDOR / "codemirror",
    )
    STAMP.write_text(versions)
    DATA.write_text(json.dumps(build_data(build_wheel())))
    print(f"Playground ready in {APP.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
