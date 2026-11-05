from pyquickbase import SchemaMismatchError

try:
    await tasks.ensure_field("Title", "numeric")
except SchemaMismatchError:
    print("Title isn't numeric, and wasn't changed")
