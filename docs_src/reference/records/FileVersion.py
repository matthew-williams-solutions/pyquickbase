from pyquickbase import FileUpload

upload = FileUpload("notes.txt", b"v1")
await tasks.update([{"Record ID#": 3, "Attachment": upload}])
task = await tasks.get(3, select=["Attachment"])
latest = task["Attachment"].latest
print(latest.number, latest.file_name)
