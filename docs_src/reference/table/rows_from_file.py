import io

text = io.StringIO(
    "Title,Hours,Due Date\nPour footings,1,2027-03-01\n"
)
rows = await tasks.rows_from_file(text)
print(rows.ok, len(rows), rows.row_numbers)
result = await tasks.insert(rows)
print(len(result.created_ids))
