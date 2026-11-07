from pyquickbase import UpdateField

plan = await app.plan_schema(
    [UpdateField(table="Job Tasks", field=6, label="Task")]
)
setting = plan.changes[0].settings["label"]
print(setting.before, "->", setting.after)
