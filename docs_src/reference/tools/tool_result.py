import json

from pyquickbase import AsyncQuickbase, Policy, tool_result

async with AsyncQuickbase.from_env(
    policy=Policy.read_only()
) as qb:
    tasks = qb.app("bq1234567").table("bqtasks123")
    page = await tasks.query_page(
        select=["Title", "Due Date"], top=2
    )
    result = tool_result(page, compact=True)
    print(json.dumps(result, indent=2))
