current = await page.read()
print(current.name, len(current.sha256))
