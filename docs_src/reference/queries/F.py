from pyquickbase import F

where = (F("Status") == "Open") & (F("Hours") >= 10)
async for task in tasks.query(where=where, select=["Title"]):
    print(task["Title"])

print(await tasks.count(F("Title").contains("wall")))
print(await tasks.count(F("Title").starts_with("Pour")))
print(await tasks.count(F("Status").is_in(["Open", "Done"])))
print(await tasks.count(F("Due Date") == None))
print(await tasks.count(F("Title").op("XCT", "wall")))
