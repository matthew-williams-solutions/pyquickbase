---
description: "What a policy can't protect against."
---

# What a policy doesn't do

- **It can't see copies of a value.** An address field shows its subfields, and a value pasted into
  another field is just text. Check what each allowed field holds.
- **A write can reveal a value.** An update reports a record as unchanged when a field already held
  the value it was given, so a client that may write a field can test what it holds.
- **It limits this client, not the token.** Code that can create another client, or call the API
  directly, can get around it. To limit that, the token's role has to do it.
- **It doesn't cover what Quickbase does next.** A pipeline or automation can still change other
  tables after an allowed write.
- **It can't limit updates by record.** An update only says which record to change, so checking its
  contents would mean reading it first, and it could change in between. Use a role with
  [record-level rules](../api-notes.md#record-level-rules), which Quickbase enforces itself.

## From an agent's side

- **A policy complements the token's role, and doesn't replace it.** Quickbase still enforces the
  role on everything it allows.
- **A value can be copied without a formula.** A tag follows formulas, lookups, and summaries, but
  not a value someone pasted into another field, or an address's subfields.
- **Filtering and sorting reveal values.** A model that may filter on a field can find out what it
  holds by seeing which records match, even if the field isn't returned, so `allow.read()` takes its
  own `filter` and `sort`.
- **Limits apply per query.** `max_paging_depth` stops one query from returning a whole table, but a
  model can still make more queries. The policy's `max_paging_depth` is how far paging can reach,
  counting `skip`, while an operation's `max_records_per_call` is the size of each page, so set the
  policy's number as high as you want a model to page.
- **`Policy.read_only()` promises only that it changes no records.** It still runs reports, which adds
  to their usage count in Quickbase, and formulas.
- **Formulas reach further than the table you run them on.** A formula can read other tables with
  `GetRecord()` and `GetRecords()`, and can return the realm and the token owner's email. Don't run
  formula text from a model or another person, even on a read-only client. See the [warning in
  Formulas](../guides/formulas.md#running-a-formula).
- **Row limits check updates, but can't lock records.** `limit.rows()` checks that an update's records
  match before sending it, but a record someone else changes in between can still slip through. Use a
  role with [record-level rules](../api-notes.md#record-level-rules) for a firm boundary.
- **They aren't a sandbox.** They apply to one client. If an agent can run code of its own, it can
  create another client with the same token, call the API directly, or reach inside the client to
  remove them.
