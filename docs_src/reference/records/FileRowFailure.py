import io

text = io.StringIO("Title,Due Date\nA,next week\n")
rows = await tasks.rows_from_file(text)
for error in rows.failed_rows:
    print(error.row, error.column)
    print(error.message)
