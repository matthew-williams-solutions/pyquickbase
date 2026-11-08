import io

text = io.StringIO('Title,Hours\nA,"1,234.5"\nB,lots\n')
rows = await tasks.rows_from_file(text)
print(rows.rows[0]["Hours"], rows.ok)
print(rows.columns)
