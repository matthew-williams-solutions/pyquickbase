template = await tasks.record_url_template()
print(template)
print(template.replace("{record_id}", "2"))
