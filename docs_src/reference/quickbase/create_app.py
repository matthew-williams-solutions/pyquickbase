app = await qb.create_app(
    "Field Jobs", description="Crews, sites, and tasks"
)
print((await app.info()).name)
