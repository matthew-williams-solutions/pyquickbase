from pyquickbase import parse_where

fields = await tasks.fields()
condition = parse_where("{7.EX.'Open'}", fields)[0]
print(condition.field_id, condition.operator, condition.value)
