print(sorted(search.schema["properties"]))
select = search.schema["properties"]["select"]
print(select["items"]["enum"])
