# Formulas

The examples use the sync client. With the async client, use `await` on each call.

## Running a formula

`run_formula()` runs a formula against a table without saving anything. Pass `record_id` if the
formula uses fields, and leave it out if it doesn't. Read-only clients can run formulas.

```python
result = tasks.run_formula("[Amount] > 1", record_id=7)

print(result.value)  # True
print(result.text)  # 1
print(result.type)  # bool

print(tasks.run_formula("Today()").text)
```

Quickbase always returns the result as a string, so `"0"` would count as true in Python. Use
`result.value` unless you need the original text.

| `type` | `value` |
|---|---|
| `bool` | `True` or `False`, or None if empty |
| `number` | A `Decimal`, or None if empty |
| `text` | The string |
| `null` | None |
| `date`, `date/time`, `timeofday`, `duration`, `textlist`, `user`, and anything else | The display text, unchanged |

Dates and timestamps stay as text because they use the app's display format, and timestamps have no
time zone: `2026-03-04T05:06:07Z` can come back as `03-03-2026 09:06 PM`. To get real dates, create a
[formula field](#formula-fields) and read it with `query()`, which returns `date` and `datetime`
objects.

Numbers are rounded for display, so `1/3` returns `0.3333333`. A formula field keeps its own precision
when you query it.

Invalid formulas raise `BadRequestError` with Quickbase's explanation, and a missing record raises
`NotFoundError`. A blank formula raises `InvalidValueError` before anything is sent.

!!! warning "Don't run formula text you don't control"

    A formula can read other tables with `GetRecord()` and `GetRecords()`, whichever table you run it
    on, and can return the realm and the token owner's email. Treat formula text from users or other
    systems like code.

## Formula fields

Pass `formula` to `create_field()`. The field's type must match what the formula returns.

```python
total = tasks.create_field("Total", "numeric", formula="[Amount] * 2")
print(total.mode, total.formula)

tasks.update_field("Total", formula="[Amount] * 3")
```

A formula field for a timestamp must be created as `"datetime"`, because Quickbase refuses a formula
on `"timestamp"`. It then reports the field's type as `timestamp`, and `query()` returns `datetime`
objects. `text-multi-line` doesn't accept a formula, and a `multitext` field needs a formula that
returns a list.

```python
tasks.create_field("Checked", "datetime", formula="Now()")
tasks.create_field("Next Day", "date", formula="[Due] + Days(1)")
```

Quickbase checks a field's formula when you save it. A syntax error, unknown field, or result of the
wrong type raises `BadRequestError`, and a failed update keeps the old formula. Values change straight
away, and renaming a field updates the formulas that use it.

A formula can't be added to a plain field, which raises `BadRequestError`, and it can't be blank,
which raises `InvalidValueError`. Quickbase would otherwise accept a blank formula and empty the
field's values. Formula fields can't be written to, so `insert()` and `update()` report a row error.
