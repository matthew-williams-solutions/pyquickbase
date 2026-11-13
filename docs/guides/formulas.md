---
description: "Running formulas and creating formula fields, and why not to run formula text you don't control."
---

# Formulas

This page covers running a formula without saving it, and creating formula fields. Every method here is
also on the async client, where you `await` it.

## Running a formula

`run_formula()` runs a formula against a table without saving anything. Pass `record_id` if the formula
uses fields, and leave it out if it doesn't. It works on a read-only client.

```python
from pyquickbase import Quickbase

with Quickbase(realm="acme", token="your_user_token") as qb:
    tasks = qb.app("bq1234567").table("Tasks")

    result = tasks.run_formula("[Amount] > 1", record_id=7)
    print(result.value)  # True
    print(result.text)  # 1
    print(result.type)  # bool

    print(tasks.run_formula("Today()").text)
```

The other examples on this page use `tasks`, a table from a client made in the same way.

Quickbase always returns the result as a string, so `"0"` would count as true in Python. Use
`result.value` unless you need the original text.

| `type` | `value` |
|---|---|
| `bool` | `True` or `False`, or None if empty |
| `number` | A `Decimal`, or None if empty |
| `text` | The string |
| `null` | None |
| `date`, `date/time`, `timeofday`, `duration`, `textlist`, `user`, and anything else | The display text, unchanged |

- **Dates and timestamps stay as text**, because they use the app's display format, and timestamps have
  no time zone: `2026-03-04T05:06:07Z` can come back as `03-03-2026 09:06 PM`. To get real dates, create a
  [formula field](#formula-fields) and read it with `query()`, which returns `date` and `datetime`
  objects.
- **Numbers are rounded for display**, so `1/3` returns `0.3333333`. A formula field keeps its full
  precision when you query it.
- `Today()` follows Quickbase's date, not your computer's, which can differ near midnight.

!!! warning "Don't run formula text you don't control"

    A formula can read other tables with `GetRecord()`, `GetRecords()`, `GetRecordByUniqueField()`,
    `GetFieldValues()`, and `SumValues()`, whichever table you run it on, and can return the realm with
    `URLRoot()` and the token owner's email with `UserToEmail(User())`. Treat formula text from users or other systems
    like code. A client with a [policy](../policies/index.md) refuses
    formulas, unless it's `Policy.read_only()`.

    It can't read a table in another app, even with a token that can reach both, or a table the token's
    role has no access to. **That isn't an error:** the result is empty, as it is for a table that doesn't
    exist, so an empty result doesn't mean there's no data.

## Formula fields

Pass `formula` to `create_field()`. The field's type must match what the formula returns.

```python
total = tasks.create_field("Total", "numeric", formula="[Amount] * 2")
print(total.mode, total.formula)

tasks.update_field("Total", formula="[Amount] * 3")
tasks.ensure_field("Total", "numeric", formula="[Amount] * 3")  # Changes nothing if it matches
```

Quickbase checks a field's formula when you save it, and values change straight away. Renaming a field
updates the formulas that use it.

### Which types take a formula

| Field type | With a formula |
|---|---|
| `text`, `numeric`, `currency`, `percent`, `rating`, `date`, `checkbox`, `duration`, `timeofday`, `url`, `email`, `phone`, `user`, `multitext`, `rich-text` | Works |
| `datetime` | Works, and the field then reports its type as `timestamp` |
| `timestamp` | Refused. Create the field as `datetime` instead |
| `text-multi-line` and `multiuser` | Refused |

```python
tasks.create_field("Checked", "datetime", formula="Now()")
tasks.create_field("Next Day", "date", formula="[Due] + Days(1)")
```

## When something is refused

| What happened | Exception | What's left behind |
|---|---|---|
| A blank formula, to run or to save | `InvalidValueError` | Nothing was sent. Quickbase would otherwise empty a field's values |
| A syntax error, unknown field or function, wrong result type, or `1/0` | `BadRequestError`, with Quickbase's explanation | Nothing was run, and a field keeps its old formula |
| A formula that uses fields, run without `record_id` | `BadRequestError` | Nothing was run |
| A `record_id` that doesn't exist | `NotFoundError` | Nothing was run |
| A formula on a plain field, or on a type that doesn't take one | `BadRequestError` | The field is unchanged |
| A formula using a field the role can't see | `PermissionDeniedError` | Nothing was run |
| A client with a policy other than `Policy.read_only()` | `PolicyRefusedError` | Nothing was sent |
| Writing to a formula field | `WriteError`, as a row error | The other rows were written |

Quickbase's formula error messages can include HTML, such as a `javascript:` link, so escape them before
showing them in a web page.
