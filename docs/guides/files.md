# File attachments

The examples use the sync client. With the async client, use `await` on each call.

## Uploading

Set a file attachment field to a `FileUpload` in `insert()`, `update()`, or `upsert()`.

```python
from pathlib import Path
from pyquickbase import FileUpload

upload = FileUpload("spec.pdf", Path("spec.pdf").read_bytes())

tasks.insert([{"Title": "Spec", "Attachment": upload}])
tasks.update([{"Record ID#": 7, "Attachment": upload}])
```

A file field accepts only a `FileUpload`, and anything else raises `InvalidValueError`, including
`None`. To keep a field empty, leave it out. Quickbase would otherwise accept these mistakes without
an error: clearing with `None` or `""` changes nothing, and a value without a file name creates a
record with no file. A `FileUpload` sent to any other field raises `InvalidValueError` too, because
Quickbase would store the file's name and discard its contents.

A file name can't contain a path or control characters. Quickbase stores names as sent, and would
pass them on to whoever downloads the file.

### Versions

**Each upload adds a version, and Quickbase silently removes the oldest once a field reaches its
limit.** New fields keep 3 versions, set by `maxVersions` in the field's properties. Uploading
identical contents still adds a version, so repeating an upload can push out a version you wanted.

### Size limits

Quickbase rejects any request body over 40 MiB, with a 413 error that raises `APIError`. Base64 adds
a third, so **the largest file you can upload is about 31.4 MB**. Writes are split into several
requests to stay under this limit, whatever the `batch_size`, and a file too large to fit is left
for Quickbase to refuse. If a later request fails, `WriteError` reports the rows already written.

Files are held in memory, as bytes and as base64, while they're sent. A slow connection may need a
larger `timeout` on the client.

## Downloading

`download_file()` returns the latest version, or the one you ask for. It works on read-only clients.

```python
file = tasks.download_file(7, "Attachment")
older = tasks.download_file(7, "Attachment", version=2)

print(file.name, len(file.data), file.content_type)
```

It raises `NotFoundError` if the record, version, or file doesn't exist. `qb.request()` can't read
these responses, because they aren't JSON.

**Don't use `file.name` as a path.** Quickbase returns names exactly as they were uploaded by anyone
with access, including outside this SDK, so a name can be `../../.bashrc`. Keep only the last part,
and decide where the file goes yourself:

```python
from pathlib import Path, PureWindowsPath

name = PureWindowsPath(file.name).name  # handles both / and \\
if name in ("", ".", ".."):
    name = "download"
(Path("downloads") / name).write_bytes(file.data)
```

Likewise, `file.content_type` is based on the file's name, not its contents, and a file's contents
are whatever was uploaded. Treat both as untrusted, especially before opening or serving a file.

## Listing versions

Reading a file field in a query returns Quickbase's value unchanged. `url` is an empty string, and
`versions` an empty list, when the field has no file.

```python
task = next(iter(tasks.query(where="{3.EX.7}", select=["Attachment"])))

for version in task["Attachment"]["versions"]:
    print(version["versionNumber"], version["fileName"], version["uploaded"])
```

Each version also names its uploader in `creator`, including their email address, so take care when
logging these values or passing them on.

## Deleting a version

```python
tasks.delete_file_version(7, "Attachment", version=2)
```

`version` is required and must be at least 1, because Quickbase treats version 0 as the latest and
would delete it. Deleting the only version leaves the field empty and keeps the record. Version
numbers aren't reused or renumbered.
