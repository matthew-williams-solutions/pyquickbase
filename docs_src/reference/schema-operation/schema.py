changes = plan.schema["properties"]["changes"]
kinds = [
    item["properties"]["action"]["const"]
    for item in changes["items"]["oneOf"]
]
print(kinds[:3])
