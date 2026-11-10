# Working with apps

This page covers finding the apps a token can reach, seeing who has access to one, creating, copying, and
deleting whole apps, and listing what runs in one. Every method
here is also on the async client, where you `await` it.

## Listing apps

`apps()` lists the apps in the realm that the token can reach, with each one's name, even for a role that can't
read the app's details.

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    for found in qb.apps():
        print(found.id, found.name)
```

A token assigned to some apps lists only those, even if its user can reach more. So a listing shows whether the
token works, but an app that isn't listed either doesn't exist or isn't assigned to the token, and Quickbase can't
say which. `admin_only=True` lists only the apps the token's user administers.

## Who the token belongs to

`current_user()` returns the token's user, for any role. `find_user()` looks someone up by email address, which
is how you get the ID that [role changes](#changing-who-has-a-role) take.

```python
me = qb.current_user()
print(me.id, me.email)

colleague = qb.find_user("jordan@example.com")
```

`find_user()` raises `NotFoundError` if nobody has that address.

## Who has access to an app

`members()` lists the users and groups with a role in the app, with their names and when they last used it. Only
an administrator's token can read this.

```python
for member in qb.app("bq1234567").members():
    print(member.name, member.last_access, [role.name for role in member.roles])
```

`member_roles()` lists one user's roles, the token's own by default, which works for any role. Only an
administrator can ask about someone else. With `include_groups`, on by default, it also says whether each role
was given directly or through a group.

`roles()` and `info()` work for any role. Quickbase refuses both for a role that can't edit the app's structure, so
the SDK asks its older API instead, which costs one extra request and returns the same details. None of this
needs anything from you.

These methods use Quickbase's XML API, which sends the token to the realm's own host, so they need a realm on
`quickbase.com` or `quickbase.eu` and the default `base_url`. See
[Configuring clients](clients.md#using-another-api-host).

## Code pages

See [Code pages](code-pages.md) for reading and writing an app's pages.

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
