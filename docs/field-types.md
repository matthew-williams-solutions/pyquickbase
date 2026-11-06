# Field types

These are the values `create_field()` and `ensure_field()` accept as `field_type`, and what each type
takes and returns. Quickbase refuses any other type with `BadRequestError`. To create fields, see
[Changing the schema](guides/schema.md).

```python
tasks = qb.app("bq1234567").table("Tasks")

tasks.create_field("Start Date", "date")
tasks.create_field("Priority", "text-multiple-choice", choices=["Low", "High"])
```

## Text types

| `field_type` | Holds | Write | Reads as | Empty reads as | Takes a formula |
|---|---|---|---|---|---|
| `text` | One line of text | `str` | `str` | `""` | Yes |
| `text-multiple-choice` | One choice | `str` | `str` | `""` | Not tested |
| `text-multi-line` | Several lines of text | `str` | `str` | `""` | No |
| `rich-text` | Text containing HTML | `str` | `str` | `""` | Not tested |
| `multitext` | Several choices | `list[str]` | `list[str]` | `[]` | Not tested |
| `email` | An email address | `str` | `str` | `""` | Yes |
| `url` | A link | `str` | `str` | `""` | Yes |
| `phone` | A phone number | `str` | `str`, formatted by Quickbase: `5551234567` reads as `(555) 123-4567` | `""` | Not tested |

**An empty text field reads as `""`, not `None`**, so check for it with `if not value`.

## Number types

| `field_type` | Holds | Write | Reads as | Empty reads as | Takes a formula |
|---|---|---|---|---|---|
| `numeric` | A number | `float` or `int` | `float` | `None` | Yes |
| `currency` | An amount of money | `float` or `int` | `float` | `None` | Yes |
| `percent` | A percentage: write `0.5` and it reads as `0.5` | `float` or `int` | `float` | `None` | Yes |
| `rating` | A rating | `float` or `int` | `float` | `None` | Yes |
| `duration` | A length of time, in milliseconds | Whole milliseconds as an `int`, or a `timedelta`. A fraction raises `InvalidValueError`, as Quickbase would drop it | `int` | `None` | Yes |

[Generated classes](guides/codegen.md) read these more strictly, such as `currency` as `Decimal`.

**In a query, numbers mean the same as when writing**, though Quickbase reads them differently, so
`F` converts them: `F("Done") >= 0.5` is 50%, and `F("Budget") > timedelta(hours=8)` or
`> 28_800_000` is 8 hours. A hand-written string is sent as written, where Quickbase compares a percent
in whole percent, `{f.GTE.'50'}`, and needs a duration as text, `{f.GT.'8 hours'}`.

**A blank number can read as 0 in a formula**, so `IsNull([Hours])` was false for an empty Hours
field made through the API. See [API notes](api-notes.md#formulas).

## Date and time types

| `field_type` | Holds | Write | Reads as | Empty reads as | Takes a formula |
|---|---|---|---|---|---|
| `date` | A date | `date` | `date` | `None` | Yes |
| `datetime` | A date and time. The field then reports its type as `timestamp` | Timezone-aware `datetime` | Timezone-aware `datetime` | `None` | Yes |
| `timestamp` | The same as `datetime` | Timezone-aware `datetime` | Timezone-aware `datetime` | `None` | No: use `datetime` |
| `timeofday` | A time of day | `str`, such as `13:30:00` | `str` | `""` | Yes |

## Other types

| `field_type` | Holds | Write | Reads as | Empty reads as | Takes a formula |
|---|---|---|---|---|---|
| `checkbox` | True or false | `bool` | `bool` | `False`, so empty and unchecked look the same | Yes |
| `user` | One Quickbase user | A `User`, or `{"id": ...}` | `User` | `None` | Not tested |
| `multiuser` | Several users | A list of `User`, or of `{"id": ...}` | A list of `User` | `[]` | Not tested |
| `file` | An attachment | [`FileUpload`](guides/files.md) | `FileAttachment` | `None` | Not tested |
| `address` | An address, as display text | Read-only: write its subfields | `str` | `""` | Not tested |

A query only returns a table's default fields unless you pass `select`, and built-in fields such as
`Record Owner` may not be among them.

## Types you can't create

Fields of these types exist, but `create_field()` refuses them:

- `recordid` is the built-in `Record ID#` field.
- `dblink` is the list of child records that a [relationship](guides/schema.md#linking-tables-with-a-relationship)
  adds to the parent.

## Field settings

`properties` holds any other settings, and is sent as it is. Quickbase refuses a key it doesn't know
with `BadRequestError`, rather than ignoring it. These were accepted and kept, and Quickbase has more:

| Key | Tested on | Example |
|---|---|---|
| `decimalPlaces` | `numeric` | `2` |
| `doesTotal` | `numeric` | `True` |
| `currencySymbol` | `currency` | `"€"` |
| `maxLength` | `text` | `10` |
| `defaultValue` | `text` | `"Open"` |
| `comments` | `text` | `"A note about this field"` |
| `numLines` | `text-multi-line` | `3` |
| `appearsAs` | `url` | `"Link"` |
| `allowNewChoices` | `text-multiple-choice` | `False`, so a value outside `choices` is refused. It's on by default |
| `sortAsGiven` | `text-multiple-choice` | `True` |

```python
tasks = qb.app("bq1234567").table("Tasks")

budget = tasks.create_field("Budget", "numeric", properties={"decimalPlaces": 2})
print(budget.decimal_places, budget.raw["properties"])
```

`decimalPlaces` only changes how a number is displayed: Quickbase still stores `1.5` in a field set to
0 places. `Field.raw` holds everything Quickbase returned for a field, including settings the SDK has
no attribute for.

A few settings go beside `properties` rather than inside it, and Quickbase refuses them inside it.
`create_field()`, `update_field()`, and `ensure_field()` take them as arguments:

| Argument | Quickbase's name | What it does |
|---|---|---|
| `help_text` | `fieldHelp` | Help shown beside the field on forms. `Field.help_text` reads it back |
| `bold` | `bold` | Shows the field's values in bold |
| `no_wrap` | `noWrap` | Keeps the field's values on one line |
| `find_enabled` | `findEnabled` | Includes the field in Quickbase's search box |
| `appears_by_default` | `appearsByDefault` | Returns the field from a query without `select` |
| `add_to_forms` | `addToForms` | Adds the field to the table's forms. Only `create_field()` takes it, as Quickbase never reports it |

```python
tasks.create_field("Priority", "text", help_text="High, Medium, or Low", bold=True)
tasks.update_field("Priority", find_enabled=False)
```
