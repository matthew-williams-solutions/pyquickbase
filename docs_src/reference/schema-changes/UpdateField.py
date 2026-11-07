from pyquickbase import UpdateField

plan = await app.plan_schema(
    [
        UpdateField(
            table="Job Tasks",
            field="Status",
            choices=("Open", "Done", "Blocked"),
        )
    ]
)
choices = plan.changes[0].settings["choices"]
print(choices.before, choices.after)
