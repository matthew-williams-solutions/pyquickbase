# See who can reach an app
from pyquickbase import Quickbase

with Quickbase(realm="demo", token="demo") as qb:
    for found in qb.apps():
        print("App:", found.id, found.name)

    me = qb.current_user()
    print("Token belongs to:", me.email)

    app = qb.app("bq1234567")
    for member in app.members():
        print("Member:", member.name, [role.name for role in member.roles])
