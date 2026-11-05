from pyquickbase import User

task = await tasks.get(1, select=["Record Owner"])
owner = task["Record Owner"]
print(isinstance(owner, User), bool(owner.email))
