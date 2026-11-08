# API reference

Each page below covers part of the SDK, with a tested example for both clients, and has a plain
Markdown copy for AI tools. Everything listed is public, as described in
[Stability and versioning](stability.md). Modules starting with `_` are private and may change at any
time.

The async classes have the same methods as the sync ones, which you `await`. They're described with
their sync versions.

| Page | What it covers | Public names |
|---|---|---|
| [Quickbase](reference/quickbase.md) | Creating and closing a client, and calling any endpoint | `Quickbase`, `AsyncQuickbase` |
| [App](reference/app.md) | An app's details, getting its tables, and changing its schema with a plan | `App`, `AsyncApp` |
| [Table](reference/table.md) | Reading and writing records, files, and changing a table's fields | `Table`, `AsyncTable` |
| [Report](reference/report.md) | Running reports built in the Quickbase interface | `Report`, `AsyncReport` |
| [Operation](reference/operation.md) | One table operation offered to a model, with its JSON Schema | `Operation`, `AsyncOperation` |
| [SchemaOperation](reference/schema-operation.md) | One schema operation offered to a model, with its JSON Schema | `SchemaOperation`, `AsyncSchemaOperation` |
| [TypedTable](reference/typed-table.md) | Reading and writing through a generated class | `TypedTable`, `AsyncTypedTable` |
| [Queries](reference/queries.md) | Building conditions, including from plain data | `F`, `Expression`, `where_from_dict`, `where_schema`, `parse_where`, `WhereCondition`, `FieldRef`, `FieldRefs` |
| [Records and results](reference/records.md) | What reads and writes return, files, previews, and summaries | `Record`, `RecordIterator`, `AsyncRecordIterator`, `Page`, `User`, `WriteResult`, `RowError`, `FileUpload`, `FileDownload`, `FileAttachment`, `FileVersion`, `FormulaResult`, `UpdatePreview`, `RecordChange`, `FieldChange`, `SummaryRow`, `Period`, `PeriodUnit`, `SummaryGroups` |
| [Metadata](reference/metadata.md) | What describes apps, tables, fields, relationships, and reports | `AppInfo`, `Role`, `TableInfo`, `ReportInfo`, `Field`, `Relationship`, `TableSchema` |
| [Schema changes](reference/schema-changes.md) | Changes to tables, fields, and relationships, and the plans that check them | `CreateTable`, `UpdateTable`, `DeleteTable`, `CreateField`, `UpdateField`, `DeleteField`, `CreateRelationship`, `CreateLookupField`, `CreateSummaryField`, `DeleteRelationship`, `SummaryFunction`, `SchemaPlan`, `PlannedChange`, `SettingChange`, `SchemaChangeResult`, `Risk`, `SchemaChange`, `SchemaAction` |
| [Limits and retries](reference/limits.md) | What a client may read and change, as one policy, and how it retries | `Policy`, `BoundPolicy`, `Decision`, `Named`, `Offers`, `allow`, `deny`, `limit`, `PolicyRule`, `Verb`, `SchemaVerb`, `Layer`, `fields`, `tables`, `FieldSelector`, `TableSelector`, `Rule`, `WriteRequest`, `ReadRequest`, `WriteAction`, `DerivedKind`, `ReadAction`, `RetryPolicy` |
| [Tools for models](reference/tools.md) | Results and errors as plain data for a model's tool call, and the operations a tool can offer | `tool_result`, `tool_error`, `ToolErrorCode`, `OperationName`, `SchemaOperationName` |
| [Generated classes](reference/generated.md) | What supports the classes from `pyquickbase codegen` | `TypedRecord`, `Column`, `TypedField`, `OrderBy`, `Select`, `TypedIterator`, `AsyncTypedIterator`, `TypedPage`, `Row`, `Unset`, `UNSET` |
| [Errors](reference/errors.md) | Everything the SDK raises, and what each one holds | `QuickbaseError`, `ConfigurationError`, `QueryError`, `TooManyRecordsError`, `ConfirmationError`, `InvalidValueError`, `PolicyRefusedError`, `UnknownTableError`, `UnknownFieldError`, `UnknownReportError`, `UnsupportedReportError`, `SchemaMismatchError`, `WriteError`, `WritePermissionError`, `SchemaChangeError`, `APIError`, `BadRequestError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `RateLimitError`, `ServerError`, `NetworkError`, `RequestTimeoutError`, `InvalidResponseError` |
