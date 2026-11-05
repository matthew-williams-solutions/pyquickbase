from pyquickbase import FileUpload

upload = FileUpload("plan.txt", b"v1")
await tasks.update([{"Record ID#": 2, "Attachment": upload}])
task = await tasks.get(2, select=["Attachment"])
attachment = task["Attachment"]
print(attachment.latest.file_name, len(attachment.versions))
