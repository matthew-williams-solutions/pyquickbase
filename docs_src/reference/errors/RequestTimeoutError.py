from pyquickbase import (
    AsyncQuickbase,
    NetworkError,
    RetryPolicy,
)

no_retries = RetryPolicy(max_retries=0)
async with AsyncQuickbase.from_env(
    timeout=0.0001, retry=no_retries
) as qb:
    try:
        await qb.app("bq1234567").tables()
    except NetworkError as error:
        print(type(error).__name__)
