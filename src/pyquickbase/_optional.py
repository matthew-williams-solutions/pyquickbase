"""Load a library that only some features need. No network access."""

import importlib
from types import ModuleType

# Each optional library, and the extra that installs it.
_EXTRAS = {"pandas": "pandas", "polars": "polars", "openpyxl": "excel"}


def require_library(name: str, caller: str) -> ModuleType:
    """Import an optional library, or say which extra installs it."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        raise ModuleNotFoundError(
            f"{caller} needs {name}. Install it with: pip install 'pyquickbase[{_EXTRAS[name]}]'",
            name=name,
        ) from None
