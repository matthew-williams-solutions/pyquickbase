from pyquickbase import parse_where

fields = await tasks.fields()
where = "{7.EX.'Open'}AND{8.GT.'5'}"
print([c.field_id for c in parse_where(where, fields)])
