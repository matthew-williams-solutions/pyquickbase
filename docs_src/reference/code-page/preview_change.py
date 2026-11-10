current = await page.read()
preview = await page.preview_change(current.text)
print(preview.unchanged, repr(preview.diff))
