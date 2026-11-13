---
description: "Each Quickbase API call the SDK makes, and the methods that make it, for finding the method behind an endpoint you know."
---

# Quickbase API calls

Each call to Quickbase's [REST API](https://developer.quickbase.com/) that the SDK makes, and the
methods that make it, so you can find the method for an endpoint you already know. Anything not here
can still be called with [`qb.request()`](reference/client.md#Quickbase-request), which sends any request with the client's token, retries, and
policy.

A method may make other calls first, to look up a table's or a field's ID, which the client then
caches. See [How pyquickbase works](guides/how-it-works.md#the-metadata-cache).

## Apps

| Call | Made by |
|---|---|
| `POST /apps` | [`Quickbase.create_app()`](reference/apps.md#Quickbase-create_app) |
| `GET /apps/{appId}` | [`App.info()`](reference/apps.md#App-info) |
| `DELETE /apps/{appId}` | [`App.drop()`](reference/apps.md#App-drop) |
| `POST /apps/{appId}/copy` | [`App.copy()`](reference/apps.md#App-copy) |
| `GET /apps/{appId}/roles` | [`App.roles()`](reference/apps.md#App-roles) |
| `GET /apps/{appId}/events` | [`App.triggers()`](reference/apps.md#App-triggers), [`Table.triggers()`](reference/apps.md#Table-triggers), which keeps the table's own, and [`Table.preview_update()`](reference/writes.md#Table-preview_update) and [`preview_delete()`](reference/writes.md#Table-preview_delete), which list what a change would set off |
| `POST /app/{appId}/trustees` | [`App.create_role_assignments()`](reference/apps.md#App-create_role_assignments) |
| `DELETE /app/{appId}/trustees` | [`App.delete_role_assignments()`](reference/apps.md#App-delete_role_assignments) |
| `PATCH /app/{appId}/trustees` | [`App.update_role_assignments()`](reference/apps.md#App-update_role_assignments) |

## Tables

| Call | Made by |
|---|---|
| `GET /tables` | [`App.tables()`](reference/tables.md#App-tables), and the first call on a table, to find its ID |
| `POST /tables` | [`App.create_table()`](reference/tables.md#App-create_table), [`App.ensure_table()`](reference/tables.md#App-ensure_table), and a [`CreateTable`](reference/schema-plans.md#CreateTable) in a schema plan |
| `POST /tables/{tableId}` | [`Table.update_info()`](reference/tables.md#Table-update_info), and an [`UpdateTable`](reference/schema-plans.md#UpdateTable) in a schema plan |
| `DELETE /tables/{tableId}` | [`Table.drop()`](reference/tables.md#Table-drop), and a [`DeleteTable`](reference/schema-plans.md#DeleteTable) in a schema plan |

## Fields and relationships

| Call | Made by |
|---|---|
| `GET /fields` | [`Table.fields()`](reference/fields.md#Table-fields), and any method that looks up a field by label |
| `POST /fields` | [`Table.create_field()`](reference/fields.md#Table-create_field), [`Table.ensure_field()`](reference/fields.md#Table-ensure_field), and a [`CreateField`](reference/schema-plans.md#CreateField) in a schema plan |
| `POST /fields/{fieldId}` | [`Table.update_field()`](reference/fields.md#Table-update_field), `Table.ensure_field()`, and an [`UpdateField`](reference/schema-plans.md#UpdateField) in a schema plan |
| `DELETE /fields` | [`Table.delete_fields()`](reference/fields.md#Table-delete_fields), and a [`DeleteField`](reference/schema-plans.md#DeleteField) in a schema plan |
| `GET /fields/usage`, `GET /fields/usage/{fieldId}` | [`Table.field_usage()`](reference/fields.md#Table-field_usage) |
| `GET /tables/{tableId}/relationships` | [`Table.relationships()`](reference/fields.md#Table-relationships) |
| `POST /tables/{tableId}/relationship` | [`Table.create_relationship()`](reference/fields.md#Table-create_relationship), [`Table.ensure_relationship()`](reference/fields.md#Table-ensure_relationship), and a [`CreateRelationship`](reference/schema-plans.md#CreateRelationship) in a schema plan |
| `POST /tables/{tableId}/relationship/{relationshipId}` | [`Table.ensure_lookup_field()`](reference/fields.md#Table-ensure_lookup_field), [`Table.ensure_summary_field()`](reference/fields.md#Table-ensure_summary_field), [`Table.ensure_count_field()`](reference/fields.md#Table-ensure_count_field), and a [`CreateLookupField`](reference/schema-plans.md#CreateLookupField) or [`CreateSummaryField`](reference/schema-plans.md#CreateSummaryField) in a schema plan |
| `DELETE /tables/{tableId}/relationship/{relationshipId}` | [`Table.delete_relationship()`](reference/fields.md#Table-delete_relationship), and a [`DeleteRelationship`](reference/schema-plans.md#DeleteRelationship) in a schema plan |

## Records

| Call | Made by |
|---|---|
| `POST /records/query` | [`Table.query()`](reference/records.md#Table-query), [`query_page()`](reference/records.md#Table-query_page), [`first()`](reference/records.md#Table-first), [`get()`](reference/records.md#Table-get), [`count()`](reference/records.md#Table-count), [`summarize()`](reference/summaries.md#Table-summarize), [`preview_update()`](reference/writes.md#Table-preview_update), and [`preview_delete()`](reference/writes.md#Table-preview_delete) |
| `POST /records` | [`Table.insert()`](reference/writes.md#Table-insert), [`update()`](reference/writes.md#Table-update), and [`upsert()`](reference/writes.md#Table-upsert) |
| `DELETE /records` | [`Table.delete()`](reference/writes.md#Table-delete) |
| `POST /formula/run` | [`Table.run_formula()`](reference/summaries.md#Table-run_formula) |

## Reports and files

| Call | Made by |
|---|---|
| `GET /reports` | [`Table.reports()`](reference/reports.md#Table-reports), and [`Report.info()`](reference/reports.md#Report-info) |
| `POST /reports/{reportId}/run` | [`Report.run()`](reference/reports.md#Report-run), [`run_page()`](reference/reports.md#Report-run_page), and [`run_summary()`](reference/reports.md#Report-run_summary) |
| `GET /files/{tableId}/{recordId}/{fieldId}/{versionNumber}` | [`Table.download_file()`](reference/files.md#Table-download_file) |
| `DELETE /files/{tableId}/{recordId}/{fieldId}/{versionNumber}` | [`Table.delete_file_version()`](reference/files.md#Table-delete_file_version) |

## Quickbase's XML API

Some things are only in Quickbase's older XML API, which these methods use. Each call is a `POST` to
`/db/{appId}`, or `/db/main`, on the realm's own host. See
[Working with apps](guides/apps.md) and [Code pages](guides/code-pages.md).

| Call | Made by |
|---|---|
| `API_GrantedDBs` | [`Quickbase.apps()`](reference/apps.md#Quickbase-apps) |
| `API_GetUserInfo` | [`Quickbase.current_user()`](reference/apps.md#Quickbase-current_user) and [`Quickbase.find_user()`](reference/apps.md#Quickbase-find_user) |
| `API_UserRoles` | [`App.members()`](reference/apps.md#App-members) |
| `API_GetUserRole` | [`App.member_roles()`](reference/apps.md#App-member_roles) |
| `API_GetSchema` | [`App.info()`](reference/apps.md#App-info), when the token's role can't use `GET /apps/{appId}` |
| `API_GetRoleInfo` | [`App.roles()`](reference/apps.md#App-roles), when the token's role can't use `GET /apps/{appId}/roles` |
| `API_ListDBPages` | [`App.code_pages()`](reference/code-pages.md#App-code_pages) |
| `API_GetDBPage` | [`CodePage.read()`](reference/code-pages.md#CodePage-read), [`CodePage.preview_change()`](reference/code-pages.md#CodePage-preview_change), and [`CodePage.write()`](reference/code-pages.md#CodePage-write), which reads the page first |
| `API_AddReplaceDBPage` | `CodePage.write()` |
| `API_SetKeyField` | A [`SetKeyField`](reference/schema-plans.md#SetKeyField) in a schema plan, as the REST API can't change a table's key field |
