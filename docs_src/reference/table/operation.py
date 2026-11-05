count = await tasks.operation("count", fields=["Status"])
arguments = {
    "where": {"field": "Status", "op": "==", "value": "Open"}
}
print(await count.run(arguments))
