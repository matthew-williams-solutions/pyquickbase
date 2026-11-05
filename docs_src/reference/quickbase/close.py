from pyquickbase import AsyncQuickbase

client = AsyncQuickbase(realm="acme", token="your_user_token")
try:
    print(client.realm)
finally:
    await client.aclose()
