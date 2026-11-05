body = await qb.request("GET", "/apps/bq1234567")
print(body["id"])
