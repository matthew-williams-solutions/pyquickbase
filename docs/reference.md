# API reference

Everything listed here is public. Modules starting with `_` are private and may change at any time.

## Clients

`Quickbase` is documented in full here. `AsyncQuickbase`, `AsyncApp`, `AsyncTable`, `AsyncReport`,
and `AsyncRecordIterator` have the same methods, which you `await`.

::: pyquickbase.Quickbase

::: pyquickbase.App

::: pyquickbase.Table

::: pyquickbase.Report

::: pyquickbase.RecordIterator

::: pyquickbase.AsyncQuickbase
    options:
      members: false

::: pyquickbase.AsyncApp
    options:
      members: false

::: pyquickbase.AsyncTable
    options:
      members: false

::: pyquickbase.AsyncReport
    options:
      members: false

::: pyquickbase.AsyncRecordIterator
    options:
      members: false

::: pyquickbase.RetryPolicy

## Queries

::: pyquickbase.F

::: pyquickbase.parse_where

::: pyquickbase.WhereCondition

## Records and metadata

::: pyquickbase.Record

::: pyquickbase.Page

::: pyquickbase.WriteResult

::: pyquickbase.RowError

::: pyquickbase.AppInfo

::: pyquickbase.TableInfo

::: pyquickbase.Field

::: pyquickbase.Relationship

::: pyquickbase.ReportInfo

## Exceptions

::: pyquickbase.QuickbaseError

::: pyquickbase.ConfigurationError

::: pyquickbase.QueryError

::: pyquickbase.InvalidValueError

::: pyquickbase.UnknownTableError

::: pyquickbase.UnknownFieldError

::: pyquickbase.UnknownReportError

::: pyquickbase.UnsupportedReportError

::: pyquickbase.ReadOnlyError

::: pyquickbase.WriteError

::: pyquickbase.NetworkError

::: pyquickbase.RequestTimeoutError

::: pyquickbase.InvalidResponseError

::: pyquickbase.APIError

::: pyquickbase.BadRequestError

::: pyquickbase.AuthenticationError

::: pyquickbase.PermissionDeniedError

::: pyquickbase.NotFoundError

::: pyquickbase.RateLimitError

::: pyquickbase.ServerError
