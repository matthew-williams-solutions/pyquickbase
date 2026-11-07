from pyquickbase import AsyncQuickbase, Policy

async with AsyncQuickbase(
    realm="acme",
    token="your_user_token",
    policy=Policy.read_only(),
) as qb:
    print(qb.realm)
