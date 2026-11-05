from pyquickbase import APIError

try:
    await qb.request(
        "GET",
        "/tables/bzzzzzzzz",
        params={"appId": "bq1234567"},
    )
except APIError as error:
    print(error.status_code, error.method)
    print(error.message)
