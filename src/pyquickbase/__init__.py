"""pyquickbase: an unofficial, typed Python SDK for the Quickbase API."""

from ._async._client import AsyncQuickbase
from ._async._resources import AsyncApp, AsyncRecordIterator, AsyncTable
from ._models import AppInfo, Field, Page, Record, Relationship, TableInfo
from ._query import F, WhereCondition, parse_where
from ._retry import RetryPolicy
from ._sync._client import Quickbase
from ._sync._resources import App, RecordIterator, Table
from ._version import __version__
from ._writes import RowError, WriteResult
from .errors import (
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    InvalidResponseError,
    InvalidValueError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    QueryError,
    QuickbaseError,
    RateLimitError,
    ReadOnlyError,
    RequestTimeoutError,
    ServerError,
    UnknownFieldError,
    UnknownTableError,
    WriteError,
)

__all__ = [
    "APIError",
    "App",
    "AppInfo",
    "AsyncApp",
    "AsyncQuickbase",
    "AsyncRecordIterator",
    "AsyncTable",
    "AuthenticationError",
    "BadRequestError",
    "ConfigurationError",
    "F",
    "Field",
    "InvalidResponseError",
    "InvalidValueError",
    "NetworkError",
    "NotFoundError",
    "Page",
    "PermissionDeniedError",
    "QueryError",
    "Quickbase",
    "QuickbaseError",
    "RateLimitError",
    "ReadOnlyError",
    "Record",
    "RecordIterator",
    "Relationship",
    "RequestTimeoutError",
    "RetryPolicy",
    "RowError",
    "ServerError",
    "Table",
    "TableInfo",
    "UnknownFieldError",
    "UnknownTableError",
    "WhereCondition",
    "WriteError",
    "WriteResult",
    "__version__",
    "parse_where",
]
