# API reference

Everything listed here is public, as described in [Stability and versioning](stability.md). Modules
starting with `_` are private and may change at any time.

## Clients

`Quickbase` is documented in full here. `AsyncQuickbase`, `AsyncApp`, `AsyncTable`, `AsyncReport`,
`AsyncRecordIterator`, `AsyncTypedTable`, and `AsyncTypedIterator` have the same methods, which you
`await`. The one difference is that `AsyncQuickbase` closes with `aclose()`, not `close()`.

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

::: pyquickbase.AsyncTypedTable
    options:
      members: false

::: pyquickbase.AsyncTypedIterator
    options:
      members: false

::: pyquickbase.RetryPolicy

## Write policies

::: pyquickbase.TableRules

::: pyquickbase.Allow

::: pyquickbase.all_of

::: pyquickbase.WriteRequest

::: pyquickbase.WriteAction

::: pyquickbase.WritePolicy

## Generated classes

These support the classes from `pyquickbase codegen`, and you rarely need them directly.

::: pyquickbase.TypedTable

::: pyquickbase.TypedIterator

::: pyquickbase.TypedPage

::: pyquickbase.OrderBy

::: pyquickbase.TypedRecord

::: pyquickbase.TypedField

::: pyquickbase.Row

::: pyquickbase.Column

::: pyquickbase.Unset

::: pyquickbase.UNSET

## Queries

::: pyquickbase.F

::: pyquickbase.Expression

::: pyquickbase.FieldRef

::: pyquickbase.FieldRefs

::: pyquickbase.parse_where

::: pyquickbase.WhereCondition

::: pyquickbase.where_from_dict

::: pyquickbase.where_schema

## Records and metadata

::: pyquickbase.Record

::: pyquickbase.Page

::: pyquickbase.WriteResult

::: pyquickbase.RowError

::: pyquickbase.FileUpload

::: pyquickbase.FileDownload

::: pyquickbase.FormulaResult

::: pyquickbase.AppInfo

::: pyquickbase.TableInfo

::: pyquickbase.Field

::: pyquickbase.Relationship

::: pyquickbase.Role

::: pyquickbase.ReportInfo

## Exceptions

::: pyquickbase.QuickbaseError

::: pyquickbase.ConfigurationError

::: pyquickbase.QueryError

::: pyquickbase.InvalidValueError

::: pyquickbase.SchemaMismatchError

::: pyquickbase.UnknownTableError

::: pyquickbase.UnknownFieldError

::: pyquickbase.UnknownReportError

::: pyquickbase.UnsupportedReportError

::: pyquickbase.ReadOnlyError

::: pyquickbase.WriteError

::: pyquickbase.WritePermissionError

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
