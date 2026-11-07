from pyquickbase import allow

rule = allow.update("Job Tasks", ["Status"])
print(rule, rule.effect)
