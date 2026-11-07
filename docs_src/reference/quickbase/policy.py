from pyquickbase import AsyncQuickbase, Policy

read_only = Policy.read_only()
async with AsyncQuickbase.from_env(policy=read_only) as qb:
    print(qb.policy.created)
