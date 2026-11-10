found = await qb.apps()
print(all(item.id and item.url for item in found))
