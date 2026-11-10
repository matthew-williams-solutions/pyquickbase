print(sorted(search.input_schema["properties"]))
select = search.input_schema["properties"]["select"]
print(select["items"]["enum"])
