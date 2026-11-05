sites = await app.create_table(
    "Job Sites",
    description="Where each job takes place.",
    single_record_name="Site",
    plural_record_name="Sites",
)
info = await sites.info()
print(info.name)
