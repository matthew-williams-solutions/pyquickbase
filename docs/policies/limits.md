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
