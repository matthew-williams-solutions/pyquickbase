# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **DataFrames.** `to_pandas()` and `to_polars()` on `query()`, a report's `run()`, and any `Page`
  fetch records into a DataFrame, with a column for each field, which a query with no matches still
  has. `insert()`, `update()`, `upsert()`, and `preview_update()` take a pandas or polars DataFrame
  as their rows, where a missing value leaves a field empty. Install `pyquickbase[pandas]` or
  `pyquickbase[polars]`. Neither library is loaded unless you use it.
- **`Table.field_usage()`** says where fields are used, such as in reports, forms, and pipelines, as a
  `FieldUsage` for each, from the counts on each field's Usage page. A plan that deletes a field
  lists these uses in its warnings.
- **`App.events()` and `Table.events()`** list webhooks, Quickbase actions, notifications,
  subscriptions, reminders, and record-triggered automations, as `AppEvent`s. Both previews of
  writes list the active ones on the table in `events`, since changing records may set them off, and
  `tool_result()` includes them, with a warning, in a preview operation's result.
- **`Quickbase.create_app()`, `App.copy()`, and `App.drop()`** create, copy, and delete apps. `drop()`
  needs the app's exact name. A client with a policy refuses all three, as a policy covers one app.
- `Page.columns`, the labels of the fields a page returned, in order.

- **`tool_result(max_chars=...)`** limits how many characters a result takes as compact JSON, so one
  call can't fill a model's context. Records and summary groups are included until the next wouldn't
  fit, and `page.has_more` and `page.next_skip` say where to carry on, so paging still reaches every
  one. At least one is always included, with its text cut shorter if it's too large on its own.
- **A `summarize` operation sorts and pages its groups.** `order_by` sorts them by `count`, by a
  `group` field, or by a `sum`, `avg`, `min`, or `max`, with empty values last, and `limit` and `skip`
  page through them. A question such as "which five clients have the most projects" no longer returns
  every client. The summary is still of every matching record.
- **`operation("query", max_default_fields=...)`** returns only the first of the table's default fields
  when the model doesn't name any in `select`, and `tool_result()` adds `fields_left_out` with how many
  it left out and a hint to name them.
- `TooManyRecordsError`, with `matched` and `limit`, and the tool error code `too_many_records`.

### Changed

- **`preview_delete()` returns a `DeletePreview`,** a `Page` with `events`.
- `ReadAction` adds `field_usage` and `app_events`, and `WriteAction` adds `create_app`, `copy_app`,
  and `delete_app`. A custom rule that tested for `other` on these requests no longer sees them.

- **Summary groups have a `page` in `tool_result()`,** with `returned`, `total`, `skip`, `next_skip`, and
  `has_more`, as records do. A `summarize` operation returns its page of groups, which reads as a list
  of `SummaryRow`, as before.
- **`summarize()` reads in large pages.** It used to read 1,000 records per request. Now `page_size`
  defaults to `None`, which sizes each page by the fields the summary reads, from 25,000 records for
  one field down to 1,000, so a page takes about 25 MB of memory. Totalling 250,000 records takes
  about 11 requests instead of 250. Passing a `page_size` still uses it.
- **A summary that only counts reads no records.** With no `group_by`, `sum`, `avg`, `min`, or `max`,
  `summarize()` asks Quickbase how many records match, in one request, at any size. It used to read
  every matching record, with the table's default fields. `max_records_scanned`, the argument and
  the read rule's, no longer limits it, as it reads nothing.
- **The API reference is reorganized.** Every page has each entry's code beside it and its contents
  in the left sidebar, and entries you call are titled by what they do. The menu groups the pages,
  and the overview lists every public name. "Limits and retries" is now three pages: `Policy` and
  `BoundPolicy` are under [Policies](https://matthew-williams-solutions.github.io/pyquickbase/reference/policies/),
  the rules under [Rules](https://matthew-williams-solutions.github.io/pyquickbase/reference/rules/),
  and `RetryPolicy` under [Retries](https://matthew-williams-solutions.github.io/pyquickbase/reference/retries/),
  so links to `reference/limits/` need updating.
- **A summary over `max_records_scanned` raises `TooManyRecordsError`,** and `tool_error()` gives it
  the code `too_many_records` with `matched` and `limit`. It was a `QueryError` with the code
  `invalid_arguments`. `TooManyRecordsError` is a `QueryError`, so `except QueryError` still catches
  it, but a tool that checks for `invalid_arguments` needs to check for `too_many_records` too.

### Removed

- **The `pyquickbase codegen` command,** and the `pyquickbase` console script that ran it. Use
  `app.table()` with field labels, or keep classes you've already generated as your own code.
- **Typed tables:** `App.typed()`, `TypedTable`, `TypedIterator`, and their async versions, with
  `TypedRecord`, `TypedField`, `TypedPage`, `Column`, `Row`, `Select`, `OrderBy`, `Unset`, and
  `UNSET`. `tool_result()` no longer takes a `TypedRecord` or `TypedPage`. A file generated by an
  earlier release no longer imports.

### Fixed

- **`Policy.bind()` is typed for the async client.** Binding with an `AsyncApp` returned `Any`, so a
  type checker didn't see the `BoundPolicy` after `await`. It now returns a coroutine of one, and
  `app` is typed `App | AsyncApp`.

## [1.0.0rc12] - 2026-11-07

This candidate replaces the permission classes with one `Policy`, and adds schema changes that are
planned before they're made, for scripts and for agents. **It breaks every client that used
`read_only`, `read_policy`, or `write_policy`,** with no compatibility layer, as listed below.

### Added

- **`Policy`, one argument for what a client may do.** `AsyncQuickbase(policy=...)` and
  `Quickbase(policy=...)` take a policy of `allow`, `deny`, and `limit` rules over tables and fields,
  by name and label, or a `fields` or `tables` selector. Anything no rule allows is refused, and a
  deny always wins. `Policy.bind(app)` looks up every name once, reports every problem in one
  `ConfigurationError`, and returns a frozen `BoundPolicy` keyed by ID.
- **One evaluator.** `BoundPolicy.decide()` is the only code that decides a request, and returns a
  `Decision` with the layer, the rule as written, a reason, and a hint, naming tables and fields by
  name and ID. `allows()` and `explain()` answer questions about it, and `offers()` asks it about
  each field and verb, which is what operations and schema operations offer, so a model is never
  offered what's refused.
- **Classes** name groups of fields, and carry over to every formula, lookup, and summary that reads
  one, through chains and across tables, including the fields a summary's filter uses. A formula that
  can't be read counts as reading its whole table.
- **`new_fields`** says what a field added after binding gets: `read`, `hidden`, or a class. It's never
  writable until the policy is bound again.
- **`limit.rows()`** ANDs a condition onto every query, summary, and delete the client sends,
  including into a new summary field's filter. It hides existing summaries that read past it, and
  lookups from a row-limited table, and refuses reports, downloads, and formulas there.
- **Schema verbs granted where a change lands:** `lookup` on the child, with `read` on the parent's
  field, `summary` on the parent, with `read` on the child's field and `filter` on its filter's fields,
  and `link` and `unlink` on both tables. Creating a table gives the client a new `BoundPolicy`, as
  `client.policy`, with the table in its `created` list for `tables.created()`.
- **Saving a bound policy** with `to_json()` and `BoundPolicy.from_json()`, and a `hash` of it.
- **Rules of your own**, as objects with `decide(request)`, which can only refuse.
- **`PlannedChange.policy_effects`**, which says who could read and write a new field, its classes,
  and which rules lose a deleted field or table. A rule a deletion leaves matching nothing is a
  blocker, and so is a label that a field the client can't see already uses.
- **Summaries of some child records.** `ensure_summary_field()`, `ensure_count_field()`, and
  `CreateSummaryField` take `where`, which is checked against the child's fields before it's sent, as
  Quickbase saves a filter it can't use and the summary then reads as empty. Under a policy, the
  filter may only use fields the client can filter on, and it's shown, and compared with an existing
  summary's, when every field it uses is readable on the child. `Field.summary_query` holds a
  summary's filter, and a plan warns when a deleted field is one a summary filters on.
- **`App.preview_schema_changes()` and `App.apply_schema_changes()`.** Changes such as `CreateField`, `UpdateField`,
  `DeleteField`, `CreateTable`, `CreateRelationship`, `CreateLookupField`, and `CreateSummaryField` are
  checked against the app as it is. Each `PlannedChange` says what it would set, its `risk`, what else it
  would do or affect, such as records that lose a deleted field's values or formulas that use it, and
  anything that blocks it, including the client's policy. Applying plans again first, and refuses if
  the touched tables and fields have changed. A failure raises `SchemaChangeError`, which says what was
  made, and planning again shows only what's left.
- **Schema operations for models:** `App.operation()` offers `describe_schema`,
  `preview_schema_changes`, and `apply_schema_changes`, which only runs with the confirmation its plan
  returned, for the same changes, while the schema is as it was. Each schema offers only what the
  client's policy allows. A change names what it does in `action`, such as `create_lookup_field`, a
  relationship's tables in `child_table` and `parent_table`, and a summary's `function` in lower
  case, such as `sum` or `std_dev`. `kind`, `child`, `parent`, `create_lookup`, and Quickbase's own
  function names are read too. Each planned change is `ready`, `already_done`, or `blocked`, and a
  plan's result has the same top-level `changes` and approval keys as an update preview.
- **Checks on what a new formula, lookup, or summary reads.** Under a policy, one may only use fields
  the client can read, and a formula may only call functions known to stay on the record. A formula
  can't make a link or rich text.
- **`Table.update_info()`** renames a table or changes its descriptions, and
  **`Table.delete_relationship()`** unlinks a child from its parent.
- **`confirmation_key`** on the client, so several processes accept each other's previews.
- **`TableSchema`**, and `tool_result()` for plans, results, and table schemas.
- **`ConfirmationError`**, with the tool error code `needs_confirmation`, for a change without the
  confirmation its preview or plan returned, or with one that's expired, for other arguments, from
  another client, or from a client with a different policy.
- **Operations and `where_from_dict()` read the spellings models tend to write** for a closed value,
  as the one value each means: `starts with` or `startsWith` for `starts_with`, `eq`, `=`, `ne`,
  `<>`, `lt`, `gte` and the like for the symbols, `and` and `or` for `all` and `any`, `sort` for
  `order_by`, and an `order_by` item as a bare label, a single item, or with `order`, `dir`, or
  `descending`. Labels must still match exactly. A preview and its change share a confirmation
  whichever spelling each used.
- **`F.has()` and `F.not_has()`**, for one exact value of a multi-select or list-user field.

### Changed

- **A `partial_write` whose `data_changed` is `unknown` has its own `hint`:** check what was written,
  then send only what's missing, as the batch that failed may have been written.
- **`Layer` no longer lists `confirmation`,** which no check made. A missing or mismatched
  confirmation raises `ConfirmationError`, not `PolicyRefusedError`.
- **A failed schema change has its own `hint`** in `tool_error()`: preview the same changes again,
  which shows only what's left, as it has no failed rows to fix.
- **Each limit is named for what it limits.** `Table.operation()` takes `max_records_per_call` and
  `max_records_per_write` for `max_records` and `max_rows`. `summarize()` takes
  `max_records_scanned`. `allow.read()` takes `max_paging_depth` and `max_records_scanned` for
  `max_records` and `max_summary_records`, and `allow.delete()` takes `max_records_per_delete`, as
  do the fields of `Offers`. `tool_result()` takes `max_text_chars` for `max_text`, and a delete
  preview's `most` is `max_deletable`.
- **Descriptions say what they describe.** `Table.operation()` and `where_schema()` take
  `field_descriptions`, and `Table.operation()` takes `report_descriptions={ID: text}`, with
  `reports` holding only IDs.
- **`create_field()` and `ensure_field()` take `type`**, not `field_type`, as `Field.type` and the
  schema changes do, and so does a generated class's `Column`.
- **`create_relationship()` and `ensure_relationship()` take `foreign_key`**, not
  `foreign_key_label`.
- **`FileUpload` and `FileDownload` hold `file_name`**, not `name`, as `FileVersion` and the JSON
  file value do, and a downloaded file in `tool_result()` has `file_name`.
- **`record_url_pattern()` is `record_url_template()`**, with `{record_id}` where the ID goes.
- **Paging has one word per meaning.** `limit` is the most records one call returns, everywhere: it
  replaces `top` in `query_page()`, `preview_delete()`, `Report.run_page()`, their typed versions,
  and `ReadRequest`. `max_total` replaces `limit` on `query()`, `Report.run()`, and
  `summary_rows()`, where it stops iterating across pages.
- **Writes take `records`**, not `rows`, in `insert()`, `update()`, `upsert()`, and
  `preview_update()`, as the operations do.
- **Aggregates are `sum`, `avg`, `min`, and `max`** in `summarize()`, on `SummaryRow`, in the
  `summarize` operation's arguments, and in `tool_result()`'s groups, as summary fields are.
  The operation still reads `total`, `average`, `minimum`, and `maximum`.
- **Results and errors say what they hold.** A `count` operation's result is `{"count": n}` and a
  `delete` operation's is `{"deleted_count": n}`, rather than `value`. An error's `changed` is
  `data_changed`, with `no`, `partly`, or `unknown` for `none`, `some`, or `unknown`. A shortened text
  value sets `text_truncated` rather than `text_shortened`, and a failed row's `kind` is `reason`.
- **Error codes:** `invalid_query` is `invalid_arguments`, as it covers every operation's arguments,
  `write_permission` is `write_permission_denied`, `configuration` is `misconfigured`, `network` is
  `network_error`, and `error` is `quickbase_error`.
- **`RowError.kind` is `RowError.reason`.**
- **The `summary_report` operation is `run_summary_report`**, to match `run_report`.
- **`ensure_summary_field()` takes `function` in lower case**, such as `sum` or `std_dev`, as schema
  changes do. Quickbase's names, such as `SUM`, still work, and an unknown one raises
  `InvalidValueError` before anything is sent.
- **`describe_schema` names each relationship's parent table** in `parent_table`, beside its ID.
- **`order_by` items take `"direction": "asc"` or `"desc"`**, defaulting to `asc`, in place of
  `"descending": true`, which is still read.
- **The filter operators `HAS` and `XHAS` are `has` and `not_has`** in `where_from_dict()` and the
  schemas. The capitals are still read.

- **Every refusal is a `PolicyRefusedError`**, holding its `Decision`, with a message of the form
  `Refused by <layer>: <what>. <reason>. (rule: <rule>) Nothing was sent. To allow it: <hint>.` A
  hidden field still raises `UnknownFieldError`. `tool_error()` keeps the codes `read_refused` and
  `write_refused`, chosen by the verb refused, and drops `refused`.
- **A confirmation starts with an ID for its key**, and is bound to the hash of the client's policy.
- **`ConfirmationError` is no longer a `QueryError`.**
- **A write whose field keys can't be read is refused**, even where every field may be written.
- **`WriteRequest.action` names each schema change**, such as `create_field`, rather than `other`, and
  `WriteRequest` has `app_id`, `parent_table_id`, `field_type`, and `derived`.
- **Previews share their approval keys:** `blockers`, `warnings`, `confirmation`, and `expires_at`. A
  delete preview over its limit lists a blocker, rather than `over_limit`.

### Removed

- **`read_only`, `read_policy`, and `write_policy`.** Pass `policy=Policy.read_only()` for
  `read_only=True`, and a bound `Policy` for the others.
- **`ReadRules`, `ReadAccess`, `WriteRules`, `WriteAccess`, `SchemaAccess`, and `all_of()`,** with
  their `new_tables` arguments, and the `ReadPolicy` and `WritePolicy` types. A policy function
  becomes a `Rule` of your own, which can only refuse.
- **`WriteRefusedError`, `ReadRefusedError`, and `RequestRefusedError`,** for `PolicyRefusedError`.

## [1.0.0rc11] - 2026-11-05

This candidate adds links to the Quickbase interface, so that a tool built on the SDK can show a
person where to check what a model read or changed. From testing an MCP server in Claude Desktop, an
operation can now require its preview, and describe its scope to the model. Nothing that worked
before has changed.

### Added

- **Links to the Quickbase interface**, so a tool can show a person where to check what a model read
  or changed. `App.url()`, `Table.url()`, and `Report.url()` link to an app, table, or report,
  `Table.record_url()` to a record's page for viewing or editing it, and `Table.field_url()` to the
  page that edits a field. `Table.record_url_pattern()` gives one link with `{Record ID#}` where
  each record's ID goes, so a tool needn't send a link per record. Names are looked up through the
  client's cache, nothing else is sent to Quickbase, and anything the client's `ReadRules` hide is
  refused as unknown. `App.url()` is a plain method in both clients, as it needs no lookup.
- **Links to the schema pages a model's changes send a person to:** `App.settings_url()`,
  `Report.url(edit=True)` for the report's settings, and on `Table`, `new_record_url()`,
  `fields_url()`, `relationships_url()`, `settings_url()`, and `form_url(form_id)`, which takes the
  form's ID because the API can't list forms.
- **`require_preview=True` on the `update` and `delete` operations.** In testing, a model updated a
  record with no preview, though told to preview first. With this, they only run with the
  `confirmation` their preview returned for exactly the same arguments, within 15 minutes, and a
  delete is refused if how many records match has changed since. `tool_result()` includes the
  confirmation. Confirmations are signed with a key each client makes, so nothing is stored.
- **`scope=` on `table.operation()`**, as well as on `run()`. In testing, a model that couldn't see
  a scope tried to set its field, was refused, and then told the person something untrue about why.
  Given to `operation()`, the scope is described in the schema, such as `Billable is true`, its
  fields are left out of a write's schema, a write that sets one is refused with a message saying
  why, and an `insert` scope that couldn't work is refused when the operation is made.
- **`Table.compile_where()`** returns a condition, from `F`, `where_from_dict()`, or a string, as
  the query text that a query of the table would send.

## [1.0.0rc10] - 2026-11-05

This candidate helps a model get its arguments right, from testing an MCP server built on the SDK
in Claude Desktop: report operations take a report the way models send one, schemas say what a
percent means, and refusals for a label or choice in the wrong case give the right one. Every call
that worked before still works, and only some refusal messages read differently.

### Fixed

- **The `run_report` and `summary_report` operations refused a report sent any way but its ID as
  text**, so a model that sent `8` rather than `"8"`, or the report's name, was told it couldn't run
  a report its tool listed. `run()` now also takes the ID as a number, or the report's exact name,
  ignoring case, of a report the tool lists. A name shared by two of them is refused, asking for
  the ID. The schema still lists the IDs, and its description says a name works too.
- **Nothing told a model that a percent is a fraction**, so asked for "25% done" it could write
  `25`, which Quickbase stores as 2,500%, or filter on `50`, which matches nothing. An operation's
  schema now says so for a percent field it writes, and `where_schema()`, given `Field` objects,
  says what a number means on a percent or duration field it offers.
- **A field label or choice in the wrong case gets a refusal that names the right one**, such as
  `select[0] isn't a field this tool may use there. Labels must match exactly, as in 'Title'.`
  It only names a field or choice the tool offers, and only when exactly one matches.
- **A report of the other kind gets a refusal that says what to do.** A table report sent to
  `summary_report`, or a chart sent to `run_report`, says which operation runs it, where the message
  only said it couldn't be run.

## [1.0.0rc9] - 2026-11-05

This candidate fixes filters on percent and duration fields, which matched the wrong records with no
error, and helps a model's tools say why a delete or write would be refused before anyone approves
it. Each was found by testing an MCP server built on the SDK, and checked against the live API.

**Upgrading.** A percent or duration filter that worked around the bug by sending Quickbase's own
units as a number, such as `F("Done") >= 50` for 50%, now means 5,000%. Send `0.5`, or the text
`"50"`, which is still sent as written.

### Fixed

- **Filters on `percent` and `duration` fields compared the wrong values, and matched the wrong
  records with no error.** A query compares a percent in whole percent, so `F("Done") >= 0.5` matched
  0.5% and up. A bare number on a duration matches nonsense, so `F("Budget") > 28800000` matched
  nothing. `F` and `where_from_dict()` now send a percent as whole percent and a duration as
  `N milliseconds`, which compares exactly, so a number means what it means when writing: a fraction,
  and milliseconds. Text is still sent as written. The same applies to a `scope` and to `delete()`.

### Added

- `F` takes a `timedelta` on a `duration` field.
- A `preview_delete` operation's result says, in `tool_result()`'s `over_limit`, when more records
  match than its `max_records`, so a model knows the delete would be refused before asking anyone
  to approve it.
- A write that sets a field the tool may read but not write now names the field. A field it can't
  read still isn't named.

### Changed

- The refusal for a read past `ReadAccess(max_records=...)` says that it limits how far paging can go,
  not only the size of a page, and so do the docs for `ReadAccess` and `table.operation()`.

## [1.0.0rc8] - 2026-11-05

This candidate holds the fixes from a review before 1.0. Most important, it closes gaps in what a model's
tools could do: an operation's `scope` limited what a write filtered but not what it wrote, and the
`delete` operation had no limit. It also reads user and file values as dataclasses, adds `select` to
typed tables, retries reads after more kinds of failure, and fills in what the API was missing, such as
`COUNT` summaries, duration writes, and field settings. Each was checked against the live API.

**Upgrading.** These changes can break working code:

- **User and file values are dataclasses, not dictionaries.** Read `owner.email` rather than
  `owner["email"]`, and `attachment.latest.file_name` rather than
  `attachment["versions"][-1]["fileName"]`. An empty file field is None, where it was
  `{"url": "", "versions": []}`.
- **A `duration` field takes whole milliseconds or a `timedelta`.** A fraction, such as `1.5`, or text,
  such as `"1:30:00"`, raises `InvalidValueError`.
- **An operation's write can't set a field its scope uses**, and `insert` needs a scope of `==`
  conditions joined with `&`.
- **httpx 0.28 or later is required.**

### Security

- An operation's `scope` now holds for what a write sets, not only for what it filters. Before, a model
  with an `insert` tool scoped to one project could create records in another, and an `update` tool could
  move a record out of its scope. `insert`, `update`, and `preview_update` now refuse a row that sets a
  field the scope uses, and `insert` fills those fields in from the scope. It refuses to run when the
  scope isn't only `==` conditions joined with `&`, as a new record couldn't be made to match it.
- The `delete` operation counts what its filter matches first, and refuses the call, deleting nothing,
  when that's more than `max_records`, 100 by default. Before, one call could delete every record in its
  scope. The tool's schema tells the model the limit.

### Added

Typed tables and values:

- `User`, `FileAttachment`, and `FileVersion`, the frozen dataclasses that user, `multiuser`, and file
  fields read as, in records and generated classes. A `User`'s `repr` leaves out the email address, and a
  `User` can be written to a user field or compared in `F`.
- `select` on a typed table's `query()`, `query_page()`, `first()`, and `get()`, which reads only the
  fields of the class you list. The other attributes are left unset, so reading one raises
  `AttributeError` saying it wasn't selected. `Select` is its type.
- Generated classes type `vCardButton` and `ICalendarButton` fields as read-only `str`, and `predecessor`
  fields as a writable `str`, rather than `Any`. Run `pyquickbase codegen` again to get them.

Writing:

- `WriteResult.row_ids`, which gives each row of an `insert()` its new Record ID#, in the order of your
  rows, with None for a row that failed. `created_ids` leaves failed rows out, so it only lines up with
  your rows when none failed.
- Operations can write `duration` fields, as whole milliseconds, and `predecessor` fields, as text.
  Table writes and generated classes also take a `timedelta` for a duration.
- `timeout` on `insert()`, `update()`, `upsert()`, and `download_file()`, and on a typed table's writes,
  which replaces the client's timeout for that call, such as for a large file. It applies with your own
  `http_client` too.

Schema:

- `ensure_count_field()`, which counts a child table's records on the parent, and counts 0 for a parent
  with none. `COUNT` takes no field, so `ensure_summary_field()` now raises `InvalidValueError` for it,
  where Quickbase refused every field it was sent.
- `help_text`, `bold`, `no_wrap`, `find_enabled`, and `appears_by_default` on `create_field()`,
  `update_field()`, and `ensure_field()`, and `add_to_forms` on `create_field()`, for the settings
  Quickbase takes beside `properties`. `Field.help_text` reads a field's help, and `ReadRules` keep it.

### Changed

- **Breaking:** user and file values are no longer dictionaries, as described under Upgrading.
  `tool_result()` writes a user as `{"id", "name", "email"}`, and a file version as
  `{"number", "file_name", "uploaded", "creator"}`.
- **Breaking:** a `duration` field refuses fractions and text, because Quickbase drops a fraction of a
  millisecond without saying so, and guesses at text.
- **Breaking:** pyquickbase needs httpx 0.28 or later, whose request bodies it measures to keep each
  batch under Quickbase's 40 MiB limit. httpx 0.27 sent larger bodies than were measured.
- Reads, meaning GET requests, record queries, report runs, and formula runs, are retried after a
  timeout, a dropped connection, or a 502, 503, or 504, as sending one again changes nothing. A write
  still isn't. Any request is also retried after httpx's `PoolTimeout`, as nothing was sent.
- The `preview_update` operation offers only fields the client may both read and change, so it no longer
  offers a write-only field that every call would be refused for.

### Fixed

- `summarize()` on a client with `ReadRules` raises `ReadRefusedError` if more records match while it
  reads than `max_summary_records`, rather than returning a summary of only some of them.
- `tool_error()` reports `changed: "some"` when a failed call still changed something: when
  `delete_fields()` deleted some of the fields, and when `create_field()` created the field but couldn't
  set `required` or `unique`. That error's message now says the field was created.
- `insert()`, `update()`, and `upsert()` raise `InvalidValueError` for a NaN or infinite number, rather
  than a `ValueError` from httpx.
- `Decimal` values, in writes and queries, and `float` values in queries, are written without an
  exponent, so `Decimal("1E+3")` is sent as `1000`. A NaN, infinite, or enormous number in a query raises
  `QueryError`.
- `preview_update()` reads in batches no larger than the client's `ReadRules` `max_records`, so a preview
  of more records than that is no longer refused.
- `UnknownReportError` says the report may be hidden from the token's role, as Quickbase hides one whose
  filter or sort uses a field the role can't see. Running such a report by ID through `qb.request()`
  gives `Internal error`, and that error now says why.

### Documentation

- The guides to errors, files, and formulas are rewritten by task, and the codegen and clients guides
  gain complete first examples. Each ends with a table of what each refusal raises and leaves behind.
- The playground simulates updates, deletes, and queries on user fields, with two new examples, and each
  guide section links to the example that shows it.
- The API notes record how Quickbase writes durations, counts child records, and takes field settings.

## [1.0.0rc7] - 2026-11-05

This candidate adds limits on what a client can read, output for a model's tool call, and table
operations offered to a model with their JSON Schema. It also makes the API reference a set of pages. It
has breaking changes: the write limits are renamed and `allowed_writes` is removed, so check the list
under Changed before upgrading.

### Added

- `read_policy` on `Quickbase` and `AsyncQuickbase`, which decides on each read before it's sent, and
  works with any of the limits on writes. `ReadRules` and `ReadAccess` list what a client may read from
  each table: which fields it may return, filter on, and sort by, which reports and file fields it may
  use, and how many records one query can reach. A table or field they don't list acts as if the token
  couldn't see it. They also leave email addresses out of user and file values unless asked, and keep
  only the metadata the SDK reads, so a field's formula and a report's filter aren't in `raw`. They
  refuse formulas, app details and roles, reports run through `request()`, and any change to tables or
  fields. A policy can also be your own function, which takes a `ReadRequest`.
- `ReadRefusedError`, raised for a read the policy refuses, and `RequestRefusedError`, the base of it and
  of `WriteRefusedError`.
- `tool_result()` and `tool_error()`, which turn results and errors into plain data for the result of a
  model's tool call, for any tool-calling framework. Results use the same shapes and value formats every
  time: dates as `YYYY-MM-DD`, timestamps in UTC, every empty value as null, and paging as `returned`,
  `total`, `skip`, `next_skip`, and `has_more`. `compact=True` sends each label once, and `max_records`
  and `max_text` cut a result while saying so. Errors carry a fixed `code` from `ToolErrorCode`, a
  `hint` for the model, whether a retry can help, and whether anything was `changed`, which tells a
  partial write from a complete failure. An exception from outside the SDK says nothing about itself.
- `table.operation()`, which turns one table operation into a tool for a model: an `Operation` with the
  JSON Schema of the arguments a model may send, and `run()`, which checks them against the same lists
  before calling the table. It covers queries, counts, summaries, inserts, updates, deletes, their
  previews, table and summary reports, formulas, and file downloads and deletes. The schema offers only
  what the client's `ReadRules` and `WriteRules` allow, and an operation the client can't run is refused
  when it's made. `run()` turns JSON values into dates, times, numbers, and file uploads by field type,
  and `scope` adds a condition your code fixes to every call. A formula's text is always fixed by your
  code. `descriptions` tells the model what fields mean.
- `tool_result()` takes table, field, and report metadata, without `raw`, and a downloaded file, with its
  text when it's UTF-8 text.
- `where_schema()` takes `descriptions`, and `where_from_dict()` takes `Field` objects in `fields`, so the
  same list works for both.

### Changed

- **Breaking:** renamed `TableRules` to `WriteRules`, `Allow` to `WriteAccess`, and `ReadOnlyError` to
  `WriteRefusedError`, so that the names for limiting writes match those for limiting reads. A refused
  write still raises the same error in the same places.
- **Breaking:** removed `allowed_writes`, which only did what a `write_policy` can. A policy that checks
  `request.method` and `request.path` does the same, as
  [Combining limits](https://matthew-williams-solutions.github.io/pyquickbase/guides/clients/#combining-limits) shows.
- `all_of()` combines read policies as well as write policies, but not the two kinds at once.
- A field or table that can't be found now says "this client can't see it" rather than "this token",
  as a read policy can hide it too.

- The message of `AuthenticationError` now says that a 401 may mean a wrong ID rather than a bad token.
  Quickbase uses the same wording for a bad token, an app or table that doesn't exist, and one the token
  can't reach, so the error's type stays the same.

### Documentation

- The API reference is now a set of pages, one for each class or topic, with a description of every
  argument and attribute and an example for both clients, which live tests run. Each page has a plain
  Markdown copy, listed in `llms.txt`.
- Links to anchors in the old reference page, such as `reference/#pyquickbase.Table`, no longer work.
- The reference page "Write limits and retries" is now "Limits and retries", and covers read limits.

## [1.0.0rc6] - 2026-11-05

This candidate adds what an AI agent or MCP server needs to use Quickbase safely: filters from plain
data, limits on what a client can write, previews of writes, and summaries. It has one breaking change,
to `insert()`, so check the list under Changed before upgrading.

### Added

- `where_from_dict()`, which builds a query condition from plain data, such as the JSON from an AI
  model's tool call or a web form. It refuses anything it doesn't recognize, and checks each value
  against its field's type, so `today` or `garbage` on a date field raises `QueryError`. Its `fields`
  argument limits which fields a filter may use.
- `where_schema()`, which describes that format as JSON Schema with no `$ref`, for a tool's input in
  MCP or any other LLM tool system.
- `write_policy` on `Quickbase` and `AsyncQuickbase`, which decides on each write before it's sent.
  `TableRules` and `Allow` list what a client may do to each table, by action and by field, and refuse
  everything else. A policy can also be your own function, which takes a `WriteRequest`, and `all_of()`
  requires several to pass. Only a return value of `True` lets a write through.
- `preview_delete()` and `preview_update()` on tables and typed tables, which show what a delete or
  update would do without changing anything, so that a person or model can check it first.
  `preview_update()` returns the new `UpdatePreview`, with each change's before and after values.
- `summarize()` on tables and typed tables, which counts records and finds totals, averages, minimums,
  and maximums, overall or per group, with `Period` to group dates by month and so on. It reads every
  matching record, and `max_records` refuses a scan larger than you expect, rather than returning a
  partial result.
- `Report.summary_rows()`, which reads a summary or chart report built in the UI as
  `{column label: value}` rows. These reports used to need `qb.request()`.

### Changed

- **Breaking:** `insert()` now raises `InvalidValueError`, and sends nothing, on a table whose key field
  isn't `Record ID#`. Before, a row with an existing value in that field silently changed that record
  rather than creating one, and `created_ids` could end up shorter than the rows. Use `upsert()` on those
  tables. Changing the key field in the UI can take as long as `schema_ttl` to be noticed.

## [1.0.0rc5] - 2026-11-04

This candidate closes gaps that would be hard to change after 1.0, and has four more breaking
changes, so check the list under Changed before upgrading.

### Added

- `Expression`, `FieldRef`, `FieldRefs`, and `OrderBy` are now exported, as they appear in public
  signatures.
- `batch_size` on `insert()`, `update()`, and `upsert()` of a typed table.
- `TypedTable.query_page()`, which returns a page of generated class instances as a new `TypedPage`,
  with `total`, `skip`, `has_more`, and `next_skip`. It runs the same schema check as other typed
  methods.

### Fixed

- A `datetime` with a fraction of a second can now be written, including `datetime.now(UTC)`. Before,
  Quickbase refused it as a row error, because Python's default of six fractional digits isn't
  accepted.
- `order_by` and `select` on an untyped table now accept a field from a generated class or `F`, such
  as `tasks.table.query_page(order_by=Tasks.fields.title)`. Before, it raised a bare `TypeError`. Any
  other unusable value now raises `QueryError`.
- `ensure_table()` now returns a table you can use when the name differs only in case from an
  existing one. Before, it found the table, then raised `UnknownTableError` on first use.

### Changed

- **Breaking:** `==` and `!=` on a `user` field now work, by email or user ID. Before, `==` silently
  matched nothing, and `!=` silently matched every record, including that user's, so a `delete()` with
  it could remove more than intended. Check any code that compares a `user` field.
- **Breaking:** `F` now raises `QueryError` for `_curuser_` on a `user` or `multiuser` field, because
  Quickbase reads it as the token's owner, so a value from someone else could match that person's
  records. A query string can still use it on purpose.
- **Breaking:** a typed table now raises `InvalidValueError` for a row made by a class for another
  table, whose field IDs would mean different fields. Before, only a type checker refused it.
- **Breaking:** `order_by` on a typed table now takes only what its signature says, a field of the
  generated class or its ID, alone or in a list. Anything else raises `QueryError`. Before, `["Title"]`
  and `[F("Title")]` happened to work, `"Title"` was read as one field name per letter, and
  `F("Title")` raised a bare `TypeError`. Use `Tasks.fields.title`.
- Fuller docstrings for the schema, query, and write methods, longer schema, reading, and writing
  guides, and a new field types page.
- `SchemaMismatchError`'s description now covers the `ensure_` methods, which also raise it.

## [1.0.0rc4] - 2026-11-04

These changes come from building a customer portal on the SDK.

### Changed

- **Breaking:** generated classes now read `currency` fields as `Decimal`, and foreign keys and
  `numeric` fields set to 0 decimal places as `int`. A fractional value in an `int` field raises
  `InvalidResponseError` rather than being rounded, because Quickbase only uses that setting for
  display. Generate your classes again, and update code that expects `float` for these fields.
- **Breaking:** `pyquickbase codegen` now leaves out the links Quickbase makes for each relationship,
  such as `Add Task` and `Task records`. Pass `--include-links` to keep them.
- **Breaking:** a write refused by the token's role now always raises `WritePermissionError`, which
  is both a `WriteError` and a `PermissionDeniedError`. Before, the same refusal raised one or the
  other depending on the role's other permissions.

### Added

- `RowError.kind` and `RowError.field_id`, so you can recognize a duplicate value, a refused row, or a
  row refused by a custom data rule, without reading Quickbase's wording.
- `App.ensure_table()`, `Table.ensure_field()`, `Table.ensure_relationship()`,
  `Table.ensure_lookup_field()`, and `Table.ensure_summary_field()`, for setup scripts that are safe
  to run again. The last two also add lookups to an existing relationship and create summary fields,
  which needed `request()` before.
- `first()` and `get()` on `Table` and `TypedTable`.
- `App.roles()`, and the new `Role` type.
- `Field` attributes for what a lookup or summary points at, plus `decimal_places` and
  `is_foreign_key`. `Relationship.summary_field_ids`.
- `from_env(token_env=...)` and `pyquickbase codegen --token-env`, for a program with several tokens.

## [1.0.0rc3] - 2026-11-02

### Added

- A guide to
  [using pyquickbase from an AI agent](https://matthew-williams-solutions.github.io/pyquickbase/agents/).
- [`llms.txt`](https://matthew-williams-solutions.github.io/pyquickbase/llms.txt) and
  [`llms-full.txt`](https://matthew-williams-solutions.github.io/pyquickbase/llms-full.txt) on the
  docs site, which hold a list of the docs and all of them in one file.
- The playground's sample app now has three linked tables, with users, tags, attachments, a lookup, a
  formula, and rollups, and four more examples.

### Fixed

- `pyquickbase codegen` now writes the same line endings on every system, so generating on Windows
  doesn't change every line of a file generated elsewhere.
- A `Retry-After` of 0 now waits using `RetryPolicy`'s backoff instead of retrying immediately.
  Quickbase can send it while still limiting, so immediate retries could be used up during a burst.
- If a later batch of a write got a successful response in the wrong shape, `InvalidResponseError` lost
  the results of the earlier batches. It's now a `WriteError`, like any other failure in a later batch.

## [1.0.0rc2] - 2026-11-02

### Security

- `F` no longer lets a value act as query syntax, which could match, or delete, records it shouldn't.
  `_FID_6` compares with field 6, so `F("Title") == "_FID_6"` matched every record, and it now raises
  `QueryError`. `a OR b` matched `a` and `b` rather than that text, and now matches that text.

### Fixed

- `insert()` and `upsert()` now raise `InvalidValueError` for a row with no values, because Quickbase
  can silently skip it, which also shifts `created_ids` against your rows.
- `pyquickbase codegen` now always reads and writes UTF-8, so labels like `日本語` no longer fail or
  produce a broken file on Windows.
- `table.relationships()` documents that it lists only relationships where the table is the child.
- A corrupt compressed response, or a redirect loop with your own `http_client`, now raises
  `NetworkError` instead of an `httpx` exception.
- Reading pages now raises `InvalidResponseError` if a response ignores `skip`, instead of returning
  the same records forever.

### Added

- A [playground](https://matthew-williams-solutions.github.io/pyquickbase/playground/) on the docs site,
  which runs the SDK in the browser against simulated data, with nothing to install.

## [1.0.0rc1] - 2026-11-02

### Added

- A stability and versioning policy, which applies from 1.0.

### Changed

- `TypedTable.check_schema()` now always fetches the table's fields again, instead of doing nothing
  after the first check. Other methods still check once, on first use, against cached metadata.
- A generated class no longer fails when a field becomes writable. Otherwise, supporting a new field
  type in a later version would break classes generated earlier.
- Generated names no longer depend on the Python version running `pyquickbase codegen`, so a label
  can't get a different name on a newer Python.
- The error from `TypedRecord.from_record()` now mentions that a report may have left out a field.
- `SchemaMismatchError` and `UnknownFieldError` now say a field may be hidden from the token, because
  Quickbase leaves out fields a role can't see, as if they didn't exist.

### Removed

- `from_api()` on `AppInfo`, `TableInfo`, `Field`, `Relationship`, `ReportInfo`, `FormulaResult`, and
  `Page`, `FileDownload.from_response()`, `FileUpload.to_api()`, and `WriteResult.merged()` and
  `summary()`. These were internal helpers, and they're now private so they can change freely.

## [0.2.0a1] - 2026-11-02

### Added

- A documentation site.
- Reports: `table.reports()`, `table.report()`, and `Report` with `run()`, `run_page()`, and
  `info()`, plus `ReportInfo`, `UnknownReportError`, and `UnsupportedReportError`. Only table reports
  can be read as records.
- File attachments: `FileUpload` for writes, plus `table.download_file()` and
  `table.delete_file_version()`.
- `pyquickbase codegen`, which generates a typed class for each table, and `app.typed()`, which reads
  and writes through those classes, including `query()`, `insert()`, `update()`, and `upsert()`. A
  class that no longer matches its table raises `SchemaMismatchError`.
- Formulas: `table.run_formula()`, which returns a `FormulaResult`, `formula=` on `create_field()` and
  `update_field()`, and `Field.formula`.

### Changed

- `read_only` and `allowed_writes` clients now allow running formulas, with `POST /formula/run`.
- A blank formula now raises `InvalidValueError` on `create_field()` and `update_field()`, including
  through `properties`, because Quickbase accepts it on a formula field and empties the field's values.
- A file attachment field now accepts only a `FileUpload`, and raises `InvalidValueError` for anything
  else, including `None` and hand-built dictionaries, because Quickbase silently accepts those and
  keeps or loses the file.
- Writes are also split by size, to stay under Quickbase's 40 MiB request limit, so `batch_size` is
  now an upper limit.
- `read_only` and `allowed_writes` clients now allow running reports, with
  `POST /reports/{id}/run`. This adds to the report's usage count in Quickbase, but changes no
  records.
- `Record.id` now explains what's missing when a report's records have no Record ID#. It still
  raises `KeyError`.

### Fixed

- A rate limit error now includes Quickbase's message, which arrives in a different format.
- A README example that was missing an import.

## [0.1.0a1] - 2026-11-02

First alpha release.

### Added

- `Quickbase` and `AsyncQuickbase` clients with `request()` for calling any endpoint.
- `RetryPolicy`, with retries for HTTP 429 and connection failures.
- The `QuickbaseError` exception hierarchy.
- `qb.app()` and `app.table()`, with app, table, field, and relationship metadata and a schema cache.
- `F` for building queries by field label, with values escaped for you.
- Checks that reject broken `where` strings that Quickbase would otherwise accept.
- `table.query()` for automatic paging, plus `query_page()` and `count()`.
- `Record`, with access by label or ID and date and timestamp conversion.
- `read_only=True` on the clients, which refuses any request that could change data.
- `allowed_writes` on the clients, which limits them to reads plus the writes you list.
- `parse_where()` for inspecting a hand-written `where` string.
- `table.insert()`, `update()`, `upsert()`, and `delete()`, with per-row error reporting in `WriteError`.
- `app.create_table()`, `table.create_field()`, `update_field()`, `delete_fields()`,
  `create_relationship()`, and `drop()`.
