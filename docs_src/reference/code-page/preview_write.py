current = await page.read()
preview = await page.preview_write(current.text)
print(preview.unchanged, repr(preview.diff))
