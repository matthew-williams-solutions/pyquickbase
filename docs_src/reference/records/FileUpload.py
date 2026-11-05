from pyquickbase import FileUpload

upload = FileUpload("spec.txt", b"Pour on Monday")
result = await tasks.update(
    [{"Record ID#": 1, "Attachment": upload}]
)
print(result.updated_ids)
