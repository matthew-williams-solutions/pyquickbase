describe = await app.operation("describe_schema")
(schema,) = await describe.run({"tables": ["Job Tasks"]})
print(schema.info.name, len(schema.fields))
