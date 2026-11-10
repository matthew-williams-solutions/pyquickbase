# Debugging refusals

## When something is refused

A refusal's message says what was refused, by name, which rule refused it, and what would allow it:

```
Refused by policy: updating Labor Cost (6) in Job Tasks (bqtasks). No rule allows it. (rule: none)
Nothing was sent. To allow it: add "Labor Cost" to an allow.update("Job Tasks", ...) rule.
```

| What happened | Exception | What's left behind |
|---|---|---|
| A request the policy doesn't allow | `PolicyRefusedError`, whose `decision` says why | Nothing was sent |
| A table or field the policy hides is named | `UnknownTableError` or `UnknownFieldError` | Nothing was sent |
| A later batch of a write was refused | `WriteError` | **Earlier batches were written**, and are in `error.result` |
| A policy that names a table, field, or tag that doesn't exist | `ConfigurationError`, listing every one | It wasn't bound |
| A `Policy` that names tables passed to a client without binding | `ConfigurationError` | The client wasn't created |
| A realm, token, or `base_url` that can't be used, such as `http://` to another host | `ConfigurationError` | The client wasn't created |
| `from_env()` with a variable missing or empty | `ConfigurationError` | The client wasn't created |

## Asking the policy

A bound policy answers questions without sending anything:

```python
from pyquickbase import Policy, Quickbase, allow

policy = Policy(rules=[allow.read("Tasks"), allow.update("Tasks", ["Status", "Notes"])])
with Quickbase.from_env() as setup:
    bound = policy.bind(setup.app("bq1234567"))

bound.allows("update", "Tasks", field="Amount").allowed  # False
print(bound.explain("lookup", "Tasks", source=("Tasks", "Amount")))
bound.offers("Tasks").updatable  # What an operation would offer
```

Each returns or explains a `PolicyDecision`, with `allowed`, the `layer` that decided, the `rule` as
written, a `reason`, and a `hint` saying what would allow it. `decide()` is the only code that
decides anything, and what operations offer comes from asking it about each field and verb, so a
model is never offered what the policy refuses.
