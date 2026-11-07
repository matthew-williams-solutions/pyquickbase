"""Validation and normalization of client settings. No network access."""

import platform
import re
from urllib.parse import urlsplit

import httpx

from ._version import __version__
from .errors import ConfigurationError

DEFAULT_BASE_URL = "https://api.quickbase.com/v1"
DEFAULT_TIMEOUT = 30.0
ENV_REALM = "QB_REALM"
ENV_TOKEN = "QB_USER_TOKEN"

_HOSTNAME = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_READ_ONLY_POSTS = {"/records/query", "/formula/run"}
_REPORT_RUN = re.compile(r"/reports/[0-9]+/run")


def normalize_realm(realm: str) -> str:
    """Return the full realm hostname from `acme`, a hostname, or a realm URL."""
    host = re.sub(r"^https?://", "", realm.strip().lower()).split("/", 1)[0]
    if host and "." not in host:
        host = f"{host}.quickbase.com"
    if not _HOSTNAME.fullmatch(host):
        raise ConfigurationError(f"Invalid realm {realm!r}. Expected e.g. 'acme.quickbase.com'.")
    return host


def validate_token(token: str) -> str:
    """Reject values that can't be a token. The token is never included in the error."""
    token = token.strip()
    if not token or any(c.isspace() or not c.isprintable() for c in token):
        raise ConfigurationError("The token is empty or contains whitespace or control characters.")
    return token


def validate_base_url(base_url: str) -> str:
    """Require HTTPS so a token can't be sent in clear text, except to the local machine."""
    parts = urlsplit(base_url)
    if parts.scheme != "https" and not (
        parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS
    ):
        raise ConfigurationError(f"base_url must use https, got {base_url!r}.")
    return base_url.rstrip("/")


def build_url(base_url: str, path: str) -> str:
    """Join a path to the base URL, refusing anything that could send the token to another host."""
    if not path.startswith("/") or path.startswith("//") or any(c in path for c in "?#\\"):
        raise ValueError(f"path must look like '/tables/abc', without a query string: {path!r}")
    return f"{base_url}{path}"


def is_report_run(path: str) -> bool:
    return bool(_REPORT_RUN.fullmatch(path))


def is_read(method: str, path: str) -> bool:
    """Whether a request is a known read. Queries are POSTs, so method alone isn't enough."""
    return method == "GET" or (
        method == "POST" and (path in _READ_ONLY_POSTS or bool(_REPORT_RUN.fullmatch(path)))
    )


def build_user_agent(prefix: str | None) -> str:
    agent = (
        f"pyquickbase/{__version__} python/{platform.python_version()} httpx/{httpx.__version__}"
    )
    return f"{prefix} {agent}" if prefix else agent


def read_env(environ: dict[str, str] | None = None, token_env: str = ENV_TOKEN) -> tuple[str, str]:
    """Read the realm and token from the environment, naming any that are missing."""
    import os

    env = os.environ if environ is None else environ
    missing = [name for name in (ENV_REALM, token_env) if not env.get(name, "").strip()]
    if missing:
        raise ConfigurationError(f"Missing environment variables: {', '.join(missing)}")
    return env[ENV_REALM], env[token_env]
