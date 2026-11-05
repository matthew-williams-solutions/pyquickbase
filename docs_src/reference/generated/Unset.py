from pyquickbase import UNSET

print(UNSET)
print(sorted(JobTasks.change(1, status=None).items()))
print(sorted(JobTasks.change(1, status=UNSET).items()))
