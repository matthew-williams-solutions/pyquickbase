from pyquickbase import where_schema

schema = where_schema(fields=["Status", "Hours"], max_depth=1)
print(type(schema).__name__, "$ref" in str(schema))
