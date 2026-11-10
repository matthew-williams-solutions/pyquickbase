from pyquickbase import tool_result

found = await tool.run({"page": "pyquickbase-live-test.html"})
print(tool_result(found)["code_page"]["name"])
