from pyquickbase import AsyncQuickbase

async with AsyncQuickbase(
    realm="acme",
    token="your_user_token",
    read_only=True,
) as qb:
    print(qb.realm)
