changes = plan.schema["properties"]["changes"]
kinds = [
    item["properties"]["kind"]["const"]
    for item in changes["items"]["oneOf"]
]
print(kinds[:3])
