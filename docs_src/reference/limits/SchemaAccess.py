from pyquickbase import (
    AsyncQuickbase,
    SchemaAccess,
    WriteAccess,
    WriteRefusedError,
    WriteRules,
)

schema = SchemaAccess(
    create_fields=True, field_types=["text"]
)
access = WriteAccess(schema=schema)
rules = WriteRules({"bqtasks123": access})
async with AsyncQuickbase.from_env(write_policy=rules) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    print((await tasks.create_field("Crew", "text")).label)
    try:
        await tasks.create_field("Cost", "currency")
    except WriteRefusedError:
        print("A currency field was refused")
