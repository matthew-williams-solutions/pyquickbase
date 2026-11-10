from pyquickbase import tool_result

change = {
    "action": "create_field",
    "table": "Job Tasks",
    "label": "Crew",
    "type": "text",
}
result = tool_result(await plan.run({"changes": [change]}))
print(result["changes"][0]["description"])
print("confirmation" in result)
