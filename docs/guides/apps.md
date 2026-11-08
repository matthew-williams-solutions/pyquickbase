# Working with apps

This page covers creating, copying, and deleting whole apps, and listing what runs in one. Every method
here is also on the async client, where you `await` it.

## Creating an app

`create_app()` makes an empty app and returns it, ready for
[tables and fields](schema.md#creating-a-table-and-its-fields).

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    app = qb.create_app("Field Jobs", description="Crews, sites, and tasks")
    tasks = app.create_table("Tasks")
```

The token's user needs permission to create apps in the realm. Quickbase allows several apps with the
same name, so running this twice makes two apps.

`assign_token`, on by default, lets the client's token reach the new app. A token limited to some apps
can't otherwise reach it, so leave it on unless the token can reach every app.

## Copying an app

`copy()` makes a new app with the same tables, fields, reports, and forms, such as a staging copy to try
changes on.

```python
staging = qb.app("bq1234567").copy("Jobs (staging)", keep_data=True)
```

| Argument | Meaning |
|---|---|
| `keep_data` | Copy the records too. Off by default |
| `keep_files` | With `keep_data`, copy file attachments too |
| `keep_users` | Give the copy's users the roles they have in the original. Otherwise the roles are copied with no one in them |
| `assign_token` | Let the client's token reach the copy, as for `create_app()` |

## Deleting an app

`drop()` deletes the app, with all its tables and records, and Quickbase can't undo it. It requires the
app's exact name, so a wrong variable can't delete the wrong app.

```python
staging.drop(confirm="Jobs (staging)")
```

## What runs in an app

`events()` lists the app's webhooks, Quickbase actions, email notifications, subscriptions, reminders,
and the automations that records trigger. On a table, it lists that table's.

```python
for event in qb.app("bq1234567").table("Tasks").events():
    print(event.type, event.name, event.active)
```

Only an app administrator's token can list them. Quickbase leaves out scheduled automations, and doesn't name automations, so an automation has a `url`
instead. A [preview of a write](writing.md#previewing-a-write) lists the active ones that changing
records may set off.

## Clients with a policy

A client with a [policy](../policies/index.md) never creates, copies, or deletes an app, because a policy
covers one app's tables. Copying with `keep_data` would also read every record. Use a client without a
policy for these. `events()` still works, and lists only events on tables the policy shows, without their
owners.
