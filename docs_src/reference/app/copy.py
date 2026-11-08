staging = await app.copy("Jobs (staging)")
print(staging.id != app.id)
