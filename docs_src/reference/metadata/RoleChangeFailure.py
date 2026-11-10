from pyquickbase import RoleAssignment, RoleChangeFailure

failure = RoleChangeFailure(
    change=RoleAssignment("123.abc", 11),
    error="Trustee already has this role",
)
print(failure.error)
