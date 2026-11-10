first = (await app.code_pages())[0]
print(first.type in ("Text", "Home Page"))
