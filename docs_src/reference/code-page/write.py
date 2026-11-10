current = await page.read()
result = await page.write(
    current.text, expected_sha256=current.sha256
)
print(result.created, result.previous.name == current.name)
