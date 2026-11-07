from pyquickbase import FileUpload

upload = FileUpload("spec.txt", b"Pour on Monday")
await tasks.update([{"Record ID#": 1, "Attachment": upload}])

download = await tasks.download_file(1, "Attachment")
print(download.file_name, download.data)
