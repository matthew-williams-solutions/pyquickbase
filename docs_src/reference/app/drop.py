staging = await app.copy("Jobs (staging)")
await staging.drop(confirm="Jobs (staging)")
