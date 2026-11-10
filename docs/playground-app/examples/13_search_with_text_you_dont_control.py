# Search with text you don't control
from pyquickbase import F, InvalidQueryError, Quickbase

searches = [
    "Renew O'Brien & Co retainer",
    "Review Terms AND Conditions",
    "_FID_6",
    "zz'}OR{3.GT.'0",
]

with Quickbase(realm="demo", token="demo") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    for text in searches:
        try:
            print(f"{text!r} matches {tasks.count(F('Title') == text)} task")
        except InvalidQueryError as error:
            print(f"{text!r} is refused: {error}")
