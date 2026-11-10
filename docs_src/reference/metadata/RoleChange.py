from pyquickbase import RoleChange

change = RoleChange("123.abc", 11, 12)
print(change.from_role_id, change.to_role_id)
