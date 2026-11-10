pages = await app.code_pages()
print(sorted({item.type for item in pages}))
