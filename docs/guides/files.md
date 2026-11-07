# File attachments

This page covers uploading, downloading, listing, and deleting the files in file attachment fields. Every
method here is also on the async client, where you `await` it.

## Uploading

Set a file attachment field to a `FileUpload` in `insert()`, `update()`, or `upsert()`.

```python
from pathlib import Path

from pyquickbase import FileUpload, Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    upload = FileUpload("spec.pdf", Path("spec.pdf").read_bytes())
    tasks.insert([{"Title": "Spec", "Attachment": upload}])
    tasks.update([{"Record ID#": 7, "Attachment": upload}], timeout=120)
```

The other examples on this page use `tasks`, a table from a client made in the same way.

- **A file field takes only a `FileUpload`.** Anything else raises `InvalidValueError`, including
  `None`, so leave the field out to keep it empty. Quickbase would otherwise accept these mistakes
  without an error: `None` or `""` changes nothing, and a value without a file name creates a record
  with no file.
- **A `FileUpload` sent to any other field raises `InvalidValueError`**, because Quickbase would store
  the file's name and discard its contents.
- **A file name can't contain a path or control characters.** Quickbase stores names as sent, and
  passes them on to whoever downloads the file.
- `timeout` replaces the client's timeout for that call, for a large file or a slow connection.

### Versions

**Each upload adds a version, and Quickbase silently removes the oldest once a field reaches its
limit.** A new field keeps 3 versions, set by `maxVersions` in the field's properties. Uploading identical
contents still adds a version, so repeating an upload can push out a version you wanted.

| To keep | Pass to `create_field()` or `update_field()` |
|---|---|
| A different number | `properties={"maxVersions": 10}`, from 1 to 1,000 |
| Every version | `properties={"versionMode": "keepallversions"}`, which ignores `maxVersions` |

Lowering `maxVersions` removes nothing until the next upload, which then trims the field to the new
limit. Raising it doesn't bring back versions already removed.

### Size limits

Quickbase refuses any request body over 40 MiB, with a 413 that raises `APIError`. Base64 adds a third,
so **the largest file you can upload is about 31.4 MB**. Writes are split into several requests to stay
under this limit, whatever the `batch_size`, and a file too large to fit is left for Quickbase to refuse.
If a later request fails, `WriteError` reports the rows already written.

Files are held in memory, as bytes and as base64, while they're sent.

## Downloading

`download_file()` returns the latest version, or the one you ask for. It works on a read-only client.

```python
file = tasks.download_file(7, "Attachment")
older = tasks.download_file(7, "Attachment", version=2, timeout=120)

print(file.file_name, len(file.data), file.content_type)
```

`qb.request()` can't read these responses, because they aren't JSON.

**Don't use `file.file_name` as a path.** Quickbase returns names exactly as they were uploaded, by anyone with
access and through any tool, so a name can be `../../.bashrc`. Keep only the last part, and decide where
the file goes yourself:

```python
from pathlib import Path, PureWindowsPath

name = PureWindowsPath(file.file_name).name  # handles both / and \\
if name in ("", ".", ".."):
    name = "download"
(Path("downloads") / name).write_bytes(file.data)
```

Likewise, `file.content_type` is based on the file's name, not its contents, and the contents are
whatever was uploaded. Treat both as untrusted, especially before opening or serving a file.

## Listing versions

Reading a file field gives a `FileAttachment`, or None when the field has no file. Its `versions` are
`FileVersion` objects, oldest first, and `latest` is the newest.

```python
task = tasks.get(7, select=["Attachment"])

if task["Attachment"] is not None:
    for version in task["Attachment"].versions:
        print(version.number, version.file_name, version.uploaded)
```

Each version names its uploader in `creator`, a `User` with their email address, so take care when
logging these values or passing them on. A `User`'s `repr` leaves the email out, and a client's
[policy](../policies/index.md) leaves `creator` out unless a rule passes
`user_emails=True`.

[Try it in the playground](../playground-app/index.html#read-users-tags-and-files), with simulated data.

## Deleting a version

```python
tasks.delete_file_version(7, "Attachment", version=2)
```

`version` is required and must be at least 1, because Quickbase treats version 0 as the latest and would
delete it. Deleting the only version leaves the field empty and keeps the record. Version numbers aren't
reused or renumbered.

## When something is refused

| What happened | Exception | What's left behind |
|---|---|---|
| A file field set to anything but a `FileUpload`, or a `FileUpload` sent to another field | `InvalidValueError` | Nothing was sent |
| A file name with a path or control characters | `InvalidValueError` | The `FileUpload` wasn't created |
| A record ID or version that isn't a positive integer, a `timeout` that isn't a positive number, or a field that isn't a file field | `InvalidValueError` | Nothing was sent |
| A field label or ID that doesn't exist | `UnknownFieldError` | Nothing was sent |
| A request over 40 MiB | `APIError`, status 413 | Nothing in that request was written. Earlier batches were, and a `WriteError` says so |
| A record, version, or file that doesn't exist | `NotFoundError` | Nothing was changed |
| A download from a field the role can't see | `NotFoundError` | Nothing was changed |
| An upload the role can't do | `WritePermissionError` | Nothing in the refused rows changed |
| Deleting a version the role can't delete | `PermissionDeniedError` | Nothing was changed |
