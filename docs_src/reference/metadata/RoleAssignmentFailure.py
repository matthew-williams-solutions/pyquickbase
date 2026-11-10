from pyquickbase import RoleAssignment, RoleAssignmentFailure

failure = RoleAssignmentFailure(
    change=RoleAssignment("123.abc", 11),
    error="Trustee already has this role",
)
print(failure.error)
