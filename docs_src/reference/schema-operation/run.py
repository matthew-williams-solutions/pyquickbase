from pyquickbase import tool_result

change = {
    "kind": "create_field",
    "table": "Job Tasks",
    "label": "Crew",
    "type": "text",
}
result = tool_result(await plan.run({"changes": [change]}))
print(result["plan"]["changes"][0]["summary"])
print("confirmation" in result)
