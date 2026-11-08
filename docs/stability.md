# Stability and versioning

This policy applies from version 1.0. Until then, anything may change in any release.

From 1.0, pyquickbase follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Breaking
changes come only in a major version, and anything removed first gets a `DeprecationWarning` in a
minor release.

## What's covered

- Every name in `pyquickbase.__all__` and `pyquickbase.errors`, with its documented methods,
  signatures, return types, and defaults.
- The `pyquickbase` command and its options.
- The promise that a client with `Policy.read_only()` changes no records. It can still run reports,
  which adds to their usage count in Quickbase, and formulas.
- The attributes of `User`, `FileAttachment`, and `FileVersion`, which file and user fields read as. A
  value a policy leaves out, such as an email address, is None.
- A `Policy` refusing whatever its rules don't allow, a deny winning over any allow, and the policy
  leaving out of a response whatever it hides. A later version can add a new `Verb`, `WriteAction`, or
  `ReadAction`, or a new attribute on `Decision`, `WriteRequest`, or `ReadRequest`, so have a rule of
  your own ignore an action it doesn't recognize.
- The keys of what `tool_result()` and `tool_error()` return, how they write each kind of value, and the
  codes in `ToolErrorCode`, along with each code's `changed` and `retryable`. A later version can add a
  key or a code, so have your tool handle a code it doesn't recognize. The wording of `message` and
  `hint` isn't covered.
- The operation names in `OperationName`, the arguments each one's schema takes, and how `run()` reads
  them. A later version can add an operation or an optional argument. The schema's descriptions aren't
  covered.

## What isn't covered

- Modules and names starting with `_`.
- Constructors of objects the client creates for you, such as `App`, `Table`, and `Report`. Use
  `qb.app()`, `app.table()`, and `table.report()` instead.
- The wording of error messages and `repr`s, so don't parse them.
- Quickbase's own behavior, including any change it makes to its responses, and the contents of `raw`
  attributes.
- Which metadata keys a policy keeps, as Quickbase's responses aren't covered either.

## Minor and patch releases

A minor release can add features, optional arguments, and support for more field types.

A patch release only fixes bugs.

## Dependencies and Python versions

A Python version is dropped only after it reaches end of life, and only in a minor release. pip
won't install a release on a Python version it doesn't support, so older Python versions keep the last
release that worked.

`httpx` stays below version 1 until a major version of pyquickbase, because `http_client` exposes it.
