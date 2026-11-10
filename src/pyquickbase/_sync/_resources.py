# Generated from the async code by scripts/generate_sync.py. Do not edit.
"""Apps and tables: labels instead of IDs, cached schema, and automatic paging."""

import difflib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, tzinfo
from functools import partial
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast, get_args

from .._bound import BoundPolicy, Snapshot, bind_snapshot
from .._confirm import ConfirmationKind, schema_binding
from .._confirm import binding as confirmation_binding
from .._confirm import check as check_confirmation
from .._confirm import issue as issue_confirmation
from .._decision import Decision
from .._derived import (
    check_filterable,
    check_formula_sources,
    check_lookup_parent,
    check_readable,
    limited_where,
)
from .._frames import DataFrame, to_pandas, to_polars
from .._links import (
    app_url,
    field_url,
    form_url,
    record_url,
    record_url_template,
    report_url,
    table_page,
)
from .._models import (
    RECORD_ID_FIELD,
    AppEvent,
    AppInfo,
    AppMember,
    DeletePreview,
    DeletePreviewPage,
    Field,
    FieldIndex,
    FieldsLeftOutPage,
    FieldUsage,
    FileDownload,
    FormulaResult,
    MemberRole,
    Page,
    Record,
    Relationship,
    ReportInfo,
    Role,
    TableInfo,
    TableSchema,
    app_event_from_api,
    app_info_from_api,
    extend,
    field_from_api,
    field_usage_from_api,
    file_download_from_response,
    find_report,
    formula_result_from_api,
    page_from_api,
    parse,
    relationship_from_api,
    report_info_from_api,
    require_summary_report,
    require_table_report,
    role_from_api,
    summary_rows_from_api,
    table_info_from_api,
    unknown_report_message,
)
from .._operations import (
    DEFAULT_MAX_ROWS,
    OperationName,
    OperationSpec,
    allowed_for,
    check_scope,
    copy_schema,
    describe_scope,
    make_spec,
    operation_names,
    refused_operation,
    scope_rows,
    unavailable,
)
from .._optional import require_library
from .._output import Count, DeletedCount, GroupPage
from .._policy import Policy, PolicyRule
from .._preview import (
    ConfirmedUpdatePreview,
    UpdatePreview,
    build_update_preview,
    id_batches,
    merge_rows,
)
from .._query import Expression, F, FieldRef, FieldRefs, build_query_body, compile_where
from .._schema import SchemaCache
from .._schema_operations import (
    DEFAULT_MAX_CHANGES,
    SchemaOperationName,
    schema_allowed,
    schema_operation_schema,
)
from .._schema_plan import (
    SUMMARY_FUNCTIONS,
    SchemaChange,
    SchemaChangeResult,
    SchemaPlan,
    canonical_changes,
    change_to_json,
    changes_from_json,
    quickbase_function,
    summary_function,
)
from .._summary import StatisticName, Summary, SummaryGroups, SummaryRow, sort_rows
from .._tabular import (
    DEFAULT_MAX_UNZIPPED_BYTES,
    WRITE_BATCH,
    ColumnMap,
    FileFormat,
    FileLike,
    FileRows,
    RecordWriter,
    check_read_limits,
    file_cells,
    file_format,
    given_rows,
    read_rows,
)
from .._write_request import UNSAFE_FORMULA_TYPES
from .._writes import (
    FIELD_SETTINGS,
    WriteResult,
    build_rows,
    custom_key_refusal,
    field_properties,
    field_settings,
    merge_write_result,
    refuse_blank_rows,
    refuse_field,
    refused_write,
    require_field,
    require_file_field,
    require_formula,
    require_id,
    split_batches,
    write_failure,
)
from .._xml import (
    APP_SCHEMA,
    ROLE_INFO,
    USER_ROLE,
    USER_ROLES,
    app_info_dict,
    member_roles_from_xml,
    members_from_xml,
    roles_from_xml,
)
from ..errors import (
    APIError,
    ConfigurationError,
    InvalidResponseError,
    InvalidValueError,
    PermissionDeniedError,
    PolicyRefusedError,
    QueryError,
    QuickbaseError,
    SchemaMismatchError,
    TooManyRecordsError,
    UnknownFieldError,
    UnknownReportError,
    UnknownTableError,
    UnsupportedOperationError,
    WriteError,
)
from ._planner import Planner, apply_checked, apply_plan
from ._transport import Transport, run_blocking

if TYPE_CHECKING:
    import pandas
    import polars

T = TypeVar("T")
DEFAULT_BATCH_SIZE = 1000


def _require_positive(name: str, value: int) -> None:
    if value < 1:
        raise QueryError(f"{name} must be at least 1.")


def _require_timeout(timeout: float | None) -> None:
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, int | float) or not timeout > 0  # pyright: ignore[reportUnnecessaryIsInstance]
    ):
        raise InvalidValueError("timeout must be a positive number of seconds.")


def _app_key(app_id: str) -> str:
    """Where the client's cache keeps an app's details."""
    return f"app:{app_id}"


def require_app_name(name: str, action: str) -> str:
    """Refuse an empty app name before anything is sent."""
    if not isinstance(name, str) or not name.strip():  # pyright: ignore[reportUnnecessaryIsInstance]
        raise InvalidValueError(f"An app needs a name to {action} it.")
    return name


def app_from_payload(transport: Transport, cache: SchemaCache, payload: Any) -> "App":
    """The `App` for one Quickbase just made, with its details cached."""
    info = parse(app_info_from_api, payload, "app")
    cache.set(_app_key(info.id), info)
    return App(transport, cache, info.id)


class App:
    """A Quickbase app. Create one with `qb.app(app_id)`. This makes no network calls."""

    def __init__(self, transport: Transport, cache: SchemaCache, app_id: str) -> None:
        self._transport = transport
        self._cache = cache
        self._id = app_id

    @property
    def id(self) -> str:
        """The app's ID, as passed to `qb.app()`."""
        return self._id

    def url(self) -> str:
        """Link to the app's home page in Quickbase. This makes no network calls.

        Returns:
            The URL, on the client's realm.
        """
        return app_url(self._transport.realm, self._id)

    def settings_url(self) -> str:
        """Link to the app's settings in Quickbase. This makes no network calls.

        Returns:
            The URL, on the client's realm.
        """
        return app_url(self._transport.realm, self._id, settings=True)

    def info(self) -> AppInfo:
        """Get the app's metadata, from the client's cache if it's there.

        A role that can't edit the app's structure can still read these, through Quickbase's older
        API, which costs one more request when nothing is cached.

        Returns:
            An `AppInfo`, with the app's `id`, `name`, `description`, `time_zone`, and
            `date_format`.

        Raises:
            AuthenticationError: Quickbase doesn't recognize the app ID, or the token can't reach
                it.
        """
        cached = self._cache.get(_app_key(self._id))
        if cached is not None:
            return cached
        try:
            payload = self._transport.request("GET", f"/apps/{self._id}")
        except PermissionDeniedError as denied:
            try:
                root = self._transport.xml.call(APP_SCHEMA, self._id)
            except UnsupportedOperationError:
                raise denied from None
            payload = parse(lambda found: app_info_dict(found, self._id), root, "app")
        info = parse(app_info_from_api, payload, "app")
        self._cache.set(_app_key(self._id), info)
        return info

    def tables(self, *, refresh: bool = False) -> list[TableInfo]:
        """List the app's tables, from the cache unless `refresh` is set.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `TableInfo` for each table the token can see.

        Raises:
            AuthenticationError: Quickbase doesn't recognize the app ID. It answers with a 401
                here, rather than a 404.
        """
        key = f"tables:{self._id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/tables", params={"appId": self._id})
        tables = parse(
            lambda rows: tuple(table_info_from_api(row) for row in rows), payload, "tables"
        )
        self._cache.set(key, tables)
        return list(tables)

    def roles(self, *, refresh: bool = False) -> list[Role]:
        """List the app's roles, from the cache unless `refresh` is set.

        A role that isn't an administrator can list these too, through Quickbase's older API,
        which costs one more request when nothing is cached. Quickbase has no API for reading or
        changing what a role can do.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `Role` for each role in the app.
        """
        key = f"roles:{self._id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        try:
            payload = self._transport.request("GET", f"/apps/{self._id}/roles")
        except PermissionDeniedError as denied:
            try:
                root = self._transport.xml.call(ROLE_INFO, self._id)
            except UnsupportedOperationError:
                raise denied from None
            roles = parse(roles_from_xml, root, "roles")
        else:
            roles = parse(lambda rows: tuple(role_from_api(row) for row in rows), payload, "roles")
        self._cache.set(key, roles)
        return list(roles)

    def members(self) -> list[AppMember]:
        """List the users and groups who hold a role in the app, with their last access.

        Returns:
            An `AppMember` for each user or group.

        Raises:
            PermissionDeniedError: The token isn't an administrator's.
            ConfigurationError: The realm isn't on `quickbase.com` or `quickbase.eu`, or the client
                has a custom `base_url`. Nothing was sent.
            PolicyRefusedError: The client has a policy. Nothing was sent.
        """
        root = self._transport.xml.call(USER_ROLES, self._id)
        return parse(members_from_xml, root, "members")

    def member_roles(
        self, member_id: str | None = None, *, include_groups: bool = True
    ) -> list[MemberRole]:
        """List one user's roles in the app, for the token's own user by default.

        Args:
            member_id: The user's ID, from `members()` or `find_user()`. Only an administrator can
                ask about someone else.
            include_groups: Include roles the user has through a group, and say how each was
                assigned.

        Returns:
            A `MemberRole` for each role.

        Raises:
            PermissionDeniedError: The token isn't an administrator's, and `member_id` is someone
                else's.
            ConfigurationError: The realm isn't on `quickbase.com` or `quickbase.eu`, or the client
                has a custom `base_url`. Nothing was sent.
            PolicyRefusedError: The client has a policy. Nothing was sent.
        """
        fields: dict[str, str | int] = {"inclgrps": 1} if include_groups else {}
        if member_id is not None:
            fields["userid"] = member_id
        root = self._transport.xml.call(USER_ROLE, self._id, fields)
        return parse(member_roles_from_xml, root, "member roles")

    def events(self) -> list[AppEvent]:
        """List the app's webhooks, Quickbase actions, notifications, subscriptions, reminders,
        and the automations that records trigger.

        Quickbase leaves out scheduled automations, and doesn't say which changes set each one
        off. With a policy, only events on tables it shows are listed, without their owners.

        Returns:
            An `AppEvent` for each, active or not.

        Raises:
            PermissionDeniedError: The token isn't an administrator's.
        """
        payload = self._transport.request("GET", f"/apps/{self._id}/events")
        return parse(lambda rows: [app_event_from_api(row) for row in rows], payload, "events")

    def ensure_table(
        self,
        name: str,
        *,
        description: str | None = None,
        single_record_name: str | None = None,
        plural_record_name: str | None = None,
    ) -> "Table":
        """Return the table with this name, creating it if it doesn't exist.

        This makes a setup script safe to run again. An existing table isn't changed.

        Args:
            name: The table's name, ignoring case when looking for an existing table.
            description: The new table's description.
            single_record_name: What the new table calls one record, such as `Project`.
            plural_record_name: What the new table calls several records, such as `Projects`.

        Returns:
            A `Table` for the existing or new table.

        Raises:
            AuthenticationError: Quickbase doesn't recognize the app ID, or the token can't reach
                it.
            BadRequestError: Quickbase refused to create the table.
            PolicyRefusedError: The client's policy doesn't allow creating a table. Nothing was
                created.
        """
        tables = self.tables(refresh=True)
        for item in tables:
            if item.name.casefold() == name.casefold():
                return self.table(item.id)
        return self.create_table(
            name,
            description=description,
            single_record_name=single_record_name,
            plural_record_name=plural_record_name,
        )

    def create_table(
        self,
        name: str,
        *,
        description: str | None = None,
        single_record_name: str | None = None,
        plural_record_name: str | None = None,
    ) -> "Table":
        """Create a table and return it.

        Args:
            name: The table's name.
            description: The table's description.
            single_record_name: What the table calls one record, such as `Project`.
            plural_record_name: What the table calls several records, such as `Projects`.

        Returns:
            A `Table` for the new table.

        Raises:
            BadRequestError: The app already has a table with this name.
        """
        optional = {
            "description": description,
            "singleRecordName": single_record_name,
            "pluralRecordName": plural_record_name,
        }
        body = {"name": name, **{k: v for k, v in optional.items() if v is not None}}
        payload = self._transport.request("POST", "/tables", params={"appId": self._id}, json=body)
        self._cache.delete(f"tables:{self._id}")
        table_id = parse(lambda data: table_info_from_api(data).id, payload, "table")
        self._transport.table_created(table_id, name)
        return self.table(table_id)

    def copy(
        self,
        name: str,
        *,
        description: str | None = None,
        keep_data: bool = False,
        keep_files: bool = False,
        keep_users: bool = False,
        assign_token: bool = True,
    ) -> "App":
        """Copy this app, with the same tables, fields, reports, and forms, and return the copy.

        Args:
            name: The copy's name. Quickbase allows several apps with the same name.
            description: The copy's description.
            keep_data: Copy the records too.
            keep_files: With `keep_data`, copy file attachments too.
            keep_users: Give the copy's users the roles they have here. Otherwise the roles are
                copied without anyone in them.
            assign_token: Let this client's token reach the copy, which a token limited to some
                apps can't otherwise do.

        Returns:
            An `App` for the copy.

        Raises:
            InvalidValueError: `name` is empty. Nothing was sent.
            PermissionDeniedError: The token's user can't copy this app.
            PolicyRefusedError: The client has a policy, which covers one app. Nothing was sent.
        """
        body = {
            "name": require_app_name(name, "copy"),
            **({} if description is None else {"description": description}),
            "properties": {
                "keepData": keep_data,
                "excludeFiles": not keep_files,
                "usersAndRoles": keep_users,
                "assignUserToken": assign_token,
            },
        }
        payload = self._transport.request("POST", f"/apps/{self._id}/copy", json=body)
        return app_from_payload(self._transport, self._cache, payload)

    def drop(self, *, confirm: str) -> None:
        """Delete this app, with all its tables and records. Quickbase can't undo this.

        Args:
            confirm: The app's exact name, so a wrong variable can't delete the wrong app.

        Raises:
            InvalidValueError: `confirm` isn't the app's exact name. Nothing was sent.
            PermissionDeniedError: The token's user can't delete this app.
            PolicyRefusedError: The client has a policy, which covers one app. Nothing was sent.
        """
        path = f"/apps/{self._id}"
        self._transport.check_write("DELETE", path, json={"name": confirm})
        info = self.info()
        if confirm != info.name:
            raise InvalidValueError(
                f"Not deleting app {info.name!r}: confirm must be its exact name."
            )
        self._transport.request("DELETE", path, json={"name": info.name})
        # Everything cached could belong to this app, and none of it is worth sorting out.
        self._cache.clear()

    def preview_schema_changes(self, changes: Sequence[SchemaChange]) -> SchemaPlan:
        """Work out what schema changes would do, without making them.

        Each change is compared with the app as it is now, in order, so a later one can use a
        table or field an earlier one makes. The plan says what each would set, what else it
        would do or affect, such as records that lose a deleted field's values, and anything that
        stops it, including the client's own limits. A change the app already has counts as
        done, so planning again after a failed apply shows only what's left.

        This only reads. It may count records, and look for duplicates before making a field
        unique, reading up to 10,000 records.

        Args:
            changes: The changes, such as `CreateField(table="Tasks", label="Due", type="date")`.

        Returns:
            A `SchemaPlan`, for `apply_schema_changes()`.

        Raises:
            InvalidValueError: `changes` is empty. Nothing was read.
        """
        if not changes:
            raise InvalidValueError("preview_schema_changes() needs at least one change.")
        return Planner(self).plan(changes)

    def apply_schema_changes(self, plan: SchemaPlan) -> SchemaChangeResult:
        """Make the changes in a plan, in order, if the app hasn't changed since it was made.

        The changes are planned again first, and nothing is sent unless that finds the same
        schema and nothing stopping them. Quickbase has no way to undo a change, so if one fails,
        the ones before it stay made, and the ones after it aren't attempted.

        Args:
            plan: From `preview_schema_changes()`.

        Returns:
            A `SchemaChangeResult`, with what was made and what was already there.

        Raises:
            SchemaMismatchError: The schema has changed since the plan was made. Nothing was sent.
            InvalidValueError: The plan has blockers, or is for another app. Nothing was sent.
            SchemaChangeError: A change failed. Its `result` says which were made.
        """
        return apply_plan(self, plan)

    def operation(
        self, name: SchemaOperationName, *, max_changes: int = DEFAULT_MAX_CHANGES
    ) -> "SchemaOperation":
        """Make a schema operation into a tool for a model, with its JSON Schema.

        `describe_schema` lists the tables and fields the client can read.
        `preview_schema_changes` says what a list of changes would do, without making them, and
        `apply_schema_changes` makes them, but only with the `confirmation` that
        `preview_schema_changes` returned for exactly those changes, within 15 minutes, while the
        schema is as it was then. Show the person each plan before they approve
        it.

        The schema offers only the kinds of change, tables, and field types the client's policy
        allows, and a plan reports anything it would refuse. Give the model a client with a
        policy, so that what it can change and see is limited, and a formula, lookup, or summary
        it adds can only show what it could already read.

        ```python
        plan = app.operation(
            "preview_schema_changes"
        )
        tool = {
            "name": "plan_changes",
            "input_schema": plan.schema,
        }
        result = plan.run(arguments)
        ```

        Args:
            name: `describe_schema`, `preview_schema_changes`, or `apply_schema_changes`.
            max_changes: The most changes one plan may hold.

        Returns:
            A `SchemaOperation`, with its `schema` and `run()`.

        Raises:
            ConfigurationError: `name` isn't a schema operation, `max_changes` isn't a positive
                whole number, or the client's policy allows no schema change for
                `preview_schema_changes` or `apply_schema_changes`.
        """
        if name not in get_args(SchemaOperationName):
            raise ConfigurationError(
                f"name must be one of: {', '.join(get_args(SchemaOperationName))}."
            )
        if type(max_changes) is not int or max_changes < 1:
            raise ConfigurationError("max_changes must be a positive whole number.")
        transport = self._transport
        tables = self.tables(refresh=True)
        allowed = schema_allowed(tables, transport.policy, app_id=self._id)
        schema = schema_operation_schema(
            name, allowed, max_changes=max_changes, tables=[item.name for item in tables]
        )
        return SchemaOperation(self, name, schema, max_changes)

    def _bind_policy(self, policy: Policy) -> BoundPolicy:
        """Read this app's schema, and bind a policy to it. See `Policy.bind()`."""
        tables = self.tables(refresh=True)
        fields: dict[str, tuple[Field, ...]] = {}
        relationships: dict[tuple[str, str, int], Relationship] = {}
        relationships_complete = True
        for info in tables:
            table = self.table(info.id)
            fields[info.id] = tuple(table.fields(refresh=True))
            try:
                for item in table.relationships(refresh=True):
                    relationships[(item.parent_table_id, item.child_table_id, item.id)] = item
            except PermissionDeniedError:
                # Only a role that can edit the app's structure may read these.
                relationships_complete = False
        wanted: set[str] = set()
        for rule in policy.rules:
            if isinstance(rule, PolicyRule) and rule.options.get("reports"):
                for selector in rule.tables or ():
                    found = _find_table(tables, str(selector.values[0]))
                    if found is not None:
                        wanted.add(found.id)
        reports = {
            table_id: tuple(self.table(table_id).reports(refresh=True))
            for table_id in sorted(wanted)
        }
        snapshot = Snapshot(
            app_id=self._id,
            tables=tuple(tables),
            fields=fields,
            relationships=tuple(relationships.values()),
            reports=reports,
            relationships_complete=relationships_complete,
        )
        return bind_snapshot(policy, snapshot)

    def table(self, name_or_id: str) -> "Table":
        """Get a table in this app by name or ID. It's checked when first used.

        This doesn't contact Quickbase, so a table that doesn't exist raises `UnknownTableError`
        on first use.

        Args:
            name_or_id: The table's name, or its ID.

        Returns:
            A `Table`.
        """
        return Table(self._transport, self._cache, self, name_or_id)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._id!r})"


def _same_filter(found: Field, clause: str | None, policy: BoundPolicy | None) -> bool:
    """Whether an existing summary has this filter. A policy hides a filter that uses a field the
    client can't read, so then it can't be compared, and only the rest of the summary is."""
    if policy is not None and found.summary_query is None and clause is not None:
        return True
    return (found.summary_query or None) == (clause or None)


def _same_property(key: str, current: Any, wanted: Any) -> bool:
    """Compare a field's property with what's wanted. Quickbase can reorder a field's choices."""
    if key == "choices":
        return set(current or ()) == set(wanted)
    return current == wanted


def _find_table(tables: Sequence[TableInfo], ref: str) -> TableInfo | None:
    return next((item for item in tables if item.id == ref), None) or next(
        (item for item in tables if item.name == ref), None
    )


class RecordIterator:
    """Yields records from a query, fetching further pages as needed.

    A class rather than an async generator, so stopping early with `break` needs no cleanup.
    """

    def __init__(
        self,
        load_body: Callable[[], Any],
        fetch_page: Callable[[dict[str, Any], int, int], Any],
        page_size: int,
        limit: int | None,
    ) -> None:
        self._load_body = load_body
        self._fetch_page = fetch_page
        self._page_size = page_size
        self._limit = limit
        self._body: dict[str, Any] | None = None
        self._buffer: Iterator[Record] = iter(())
        self._skip: int = 0
        self._yielded: int = 0
        self._done = False
        self._columns: tuple[str, ...] = ()

    def __iter__(self) -> Self:
        return self

    def _rest(self) -> list[Record]:
        """Fetch every remaining record."""
        records: list[Record] = []
        for record in self:
            records.append(record)
        return records

    def to_csv(
        self, destination: FileLike, *, spreadsheet_safe: bool = True, encoding: str = "utf-8-sig"
    ) -> int:
        """Write the remaining records to a CSV file as they're fetched, with a header row.

        Dates and timestamps are ISO 8601, with a timestamp's offset. A user is their email, or
        their ID if the client's policy hides emails, a file is its latest version's name, and a
        list's values are separated by `;`. `rows_from_file()` reads the file back.

        Args:
            destination: A path, or a file open for writing text.
            spreadsheet_safe: Write text that a spreadsheet would run as a formula, which starts
                with `=`, `+`, `-`, or `@`, with a leading `'`, so opening the file can't run it.
                `rows_from_file()` removes it again.
            encoding: The text encoding for a path. The default writes a byte order mark, which
                Excel needs to read UTF-8.

        Returns:
            How many records were written.
        """
        return self._write(destination, "csv", spreadsheet_safe=spreadsheet_safe, encoding=encoding)

    def to_excel(self, destination: FileLike, *, sheet: str = "Records") -> int:
        """Write the remaining records to an Excel file, with a header row. Needs
        `pyquickbase[excel]`.

        Dates and numbers are Excel's own, and a timestamp is written in UTC, as Excel has no time
        zones. Text is always text, so a value starting with `=` is never a formula. Values are
        written as for `to_csv()`.

        Args:
            destination: A path, or a file open for writing bytes.
            sheet: The worksheet's name.

        Returns:
            How many records were written.
        """
        require_library("openpyxl", "to_excel()")
        return self._write(destination, "xlsx", sheet=sheet)

    def _write(
        self,
        destination: FileLike,
        file_kind: FileFormat,
        *,
        spreadsheet_safe: bool = True,
        sheet: str = "Records",
        encoding: str = "utf-8-sig",
    ) -> int:
        writer = RecordWriter(destination, file_kind, spreadsheet_safe=spreadsheet_safe)
        run_blocking(partial(writer.begin, sheet=sheet, encoding=encoding))
        try:
            batch: list[Record] = []
            for record in self:
                batch.append(record)
                if len(batch) == WRITE_BATCH:
                    run_blocking(writer.add_all, batch, self._columns)
                    batch = []
            run_blocking(writer.add_all, batch, self._columns)
            run_blocking(writer.header, self._columns)
            run_blocking(writer.finish)
        except BaseException:
            writer.close()
            raise
        return writer.written

    def to_pandas(self) -> "pandas.DataFrame":
        """Fetch the remaining records into a pandas DataFrame, with a column for each field.

        A user field holds the user's email, or their ID if the client's policy hides emails, and
        a file field holds the latest version's file name. Needs `pyquickbase[pandas]`.

        Returns:
            A DataFrame, which has the columns even when nothing matched.
        """
        require_library("pandas", "to_pandas()")
        return to_pandas(self._rest(), self._columns)

    def to_polars(self) -> "polars.DataFrame":
        """Fetch the remaining records into a polars DataFrame. See `to_pandas()`. Needs
        `pyquickbase[polars]`.

        Returns:
            A DataFrame, which has the columns even when nothing matched.
        """
        require_library("polars", "to_polars()")
        return to_polars(self._rest(), self._columns)

    def __next__(self) -> Record:
        while True:
            record = next(self._buffer, None)
            if record is not None:
                self._yielded += 1
                return record
            if self._done or self._yielded == self._limit:
                raise StopIteration
            self._load_next_page()

    def _load_next_page(self) -> None:
        body = self._body
        if body is None:
            body = self._body = self._load_body()
        remaining = None if self._limit is None else self._limit - self._yielded
        top = self._page_size if remaining is None else min(self._page_size, remaining)
        page: Page = self._fetch_page(body, top, self._skip)
        if page.has_more and not page.records:
            raise InvalidResponseError(
                f"Quickbase returned an empty page at {self._skip} of {page.total} records."
            )
        if page.skip != self._skip:
            # Carrying on would return the same records forever.
            raise InvalidResponseError(
                f"Quickbase returned records from {page.skip} when asked to skip {self._skip}."
            )
        self._buffer = iter(page.records)
        self._columns = self._columns or page.columns
        self._skip = page.next_skip
        self._done = not page.has_more


class Table:
    """A table in an app. Create one with `app.table("Tasks")`."""

    def __init__(self, transport: Transport, cache: SchemaCache, app: App, ref: str) -> None:
        self._transport = transport
        self._cache = cache
        self._app = app
        self._ref = ref

    def info(self) -> TableInfo:
        """Get the table's metadata, from the client's cache of the app's tables.

        Returns:
            A `TableInfo`, with the table's `id` and `name`.

        Raises:
            UnknownTableError: This app has no such table, even after checking again.
        """
        tables = self._app.tables()
        found = _find_table(tables, self._ref)
        if found is None:
            # The table may be newer than the cache, so look once more before giving up.
            tables = self._app.tables(refresh=True)
            found = _find_table(tables, self._ref)
        if found is None:
            names = [item.name for item in tables]
            close = difflib.get_close_matches(self._ref, names, n=3)
            hint = f" Did you mean {' or '.join(repr(name) for name in close)}?" if close else ""
            raise UnknownTableError(f"App {self._app.id} has no table {self._ref!r}.{hint}")
        return found

    def fields(self, *, refresh: bool = False) -> list[Field]:
        """List the table's fields, from the cache unless `refresh` is set.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `Field` for each of the table's fields, including built-in ones like `Record ID#`.

        Raises:
            UnknownTableError: This app has no such table.
        """
        table_id = (self.info()).id
        key = f"fields:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/fields", params={"tableId": table_id})
        fields = parse(lambda rows: tuple(field_from_api(row) for row in rows), payload, "fields")
        self._cache.set(key, fields)
        return list(fields)

    def relationships(self, *, refresh: bool = False) -> list[Relationship]:
        """List the relationships in which this table is the child. A parent table lists none.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `Relationship` for each parent this table links to, or an empty list.

        Raises:
            UnknownTableError: This app has no such table.
            PermissionDeniedError: The token's role can't edit the app's structure.
        """
        table_id = (self.info()).id
        key = f"relationships:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        found: list[Relationship] = []
        while True:
            payload = self._transport.request(
                "GET", f"/tables/{table_id}/relationships", params={"skip": len(found)}
            )
            page, total = parse(self._parse_relationships, payload, "relationships")
            found.extend(page)
            if not page or len(found) >= total:
                break
        self._cache.set(key, tuple(found))
        return found

    @staticmethod
    def _parse_relationships(payload: Any) -> tuple[list[Relationship], int]:
        rows = [relationship_from_api(row) for row in payload["relationships"]]
        return rows, payload["metadata"]["totalRelationships"]

    def reports(self, *, refresh: bool = False) -> list[ReportInfo]:
        """List the table's reports, from the cache unless `refresh` is set.

        Args:
            refresh: Ask Quickbase instead of using the client's cache, which keeps metadata for
                five minutes by default. A change made through this client updates the cache, so
                this is for changes made elsewhere, such as in the Quickbase interface.

        Returns:
            A `ReportInfo` for each report, including ones Quickbase can't run through the API,
            whose `incomplete` is true.

        Raises:
            UnknownTableError: This app has no such table.
        """
        table_id = (self.info()).id
        key = f"reports:{table_id}"
        cached = None if refresh else self._cache.get(key)
        if cached is not None:
            return list(cached)
        payload = self._transport.request("GET", "/reports", params={"tableId": table_id})
        reports = parse(
            lambda rows: tuple(report_info_from_api(row) for row in rows), payload, "reports"
        )
        self._cache.set(key, reports)
        return list(reports)

    def report(self, name_or_id: str) -> "Report":
        """Get a report on this table by name or ID. It's checked when first used.

        This doesn't contact Quickbase, so a report that doesn't exist raises
        `UnknownReportError` on first use.

        Args:
            name_or_id: The report's exact name, or its ID, which takes priority over a name.

        Returns:
            A `Report`, for running it or getting its details.
        """
        return Report(self._transport, self, name_or_id)

    def url(self) -> str:
        """Link to the table's home page in Quickbase.

        This only contacts Quickbase to look up the table's ID, as any method does, and the
        client's cache usually has it.

        Returns:
            The URL, on the client's realm.

        Raises:
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        return self._page("td")

    def _page(self, action: str, query: dict[str, int | str] | None = None) -> str:
        table_id = (self.info()).id
        return table_page(self._transport.realm, self._app.id, table_id, action, query)

    def new_record_url(self) -> str:
        """Link to the page in Quickbase that adds a record to the table.

        It only contacts Quickbase to look up the table's ID, as `url()` does.

        Returns:
            The URL, on the client's realm.

        Raises:
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        return self._page("nwr")

    def fields_url(self) -> str:
        """Link to the table's list of fields in Quickbase.

        It only contacts Quickbase to look up the table's ID, as `url()` does.

        Returns:
            The URL, on the client's realm.

        Raises:
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        return self._page("listfields")

    def relationships_url(self) -> str:
        """Link to the table's relationships in Quickbase.

        It only contacts Quickbase to look up the table's ID, as `url()` does.

        Returns:
            The URL, on the client's realm.

        Raises:
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        return self._page("Relationships")

    def settings_url(self) -> str:
        """Link to the table's settings in Quickbase, such as its name and key field.

        It only contacts Quickbase to look up the table's ID, as `url()` does.

        Returns:
            The URL, on the client's realm.

        Raises:
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        return self._page("KeyProps")

    def form_url(self, form_id: int) -> str:
        """Link to the page in Quickbase that edits one of the table's forms.

        The API can't list forms, so this takes the form's ID, the `dfid` in its address in
        Quickbase, and doesn't check that the form exists. It only contacts Quickbase to look up
        the table's ID, as `url()` does.

        Args:
            form_id: The form's ID.

        Returns:
            The URL, on the client's realm.

        Raises:
            InvalidValueError: `form_id` isn't a positive integer.
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        require_id("form_id", form_id)
        table_id = (self.info()).id
        return form_url(self._transport.realm, self._app.id, table_id, form_id)

    def record_url(self, record_id: int, *, edit: bool = False) -> str:
        """Link to a record's page in Quickbase, for viewing it or editing it.

        This doesn't check that the record exists, so a link to one that doesn't opens an error
        page. It only contacts Quickbase to look up the table's ID, as `url()` does.

        Args:
            record_id: The record's Record ID#.
            edit: Link to the page that edits the record, rather than the one that shows it.

        Returns:
            The URL, on the client's realm.

        Raises:
            InvalidValueError: `record_id` isn't a positive integer.
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        require_id("record_id", record_id)
        table_id = (self.info()).id
        return record_url(self._transport.realm, self._app.id, table_id, record_id, edit=edit)

    def record_url_template(self, *, edit: bool = False) -> str:
        """Link to any record's page in Quickbase, with `{record_id}` where its ID goes.

        A tool can send this once, rather than a link for each record, and each record's
        `Record ID#` fills it in. The placeholder is text, not part of a working URL, so replace
        it with `template.replace("{record_id}", str(record_id))`. It only contacts Quickbase to
        look up the table's ID, as `url()` does.

        Args:
            edit: Link to the page that edits a record, rather than the one that shows it.

        Returns:
            The URL, on the client's realm, with `{record_id}` for the record's ID.

        Raises:
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        table_id = (self.info()).id
        return record_url_template(self._transport.realm, self._app.id, table_id, edit=edit)

    def field_url(self, field: FieldRef) -> str:
        """Link to the page in Quickbase that edits a field's settings.

        It only contacts Quickbase to look up the table's and field's IDs, as any method does, and
        the client's cache usually has them.

        Args:
            field: The field, by label or ID.

        Returns:
            The URL, on the client's realm.

        Raises:
            UnknownFieldError: The table has no such field, or the client's policy hides it.
            UnknownTableError: This app has no such table, or the client's policy hides it.
        """
        table_id = (self.info()).id
        field_id = (self.resolve_field_ids([field]))[0]
        return field_url(self._transport.realm, self._app.id, table_id, field_id)

    def compile_where(self, where: Expression | str) -> str:
        """Return a condition as the query text that a query of this table would send.

        Use it to show a person what a query asked for, as Quickbase's own `{6.EX.'Open'}` form.
        This only contacts Quickbase to look up the table's fields, which the client's cache
        usually has.

        Args:
            where: A condition built with `F` or `where_from_dict()`, or a hand-written string,
                which gets checked and returned without outer whitespace.

        Returns:
            The query text.

        Raises:
            UnknownFieldError: The table has no such field, or the client's policy hides it.
            QueryError: `where` can't be sent as written.
        """
        return self._with_fields(lambda fields: compile_where(where, FieldIndex(fields)))

    def _with_fields(self, build: Callable[[list[Field]], T]) -> T:
        try:
            return build(self.fields())
        except UnknownFieldError:
            # The field may be newer than the cache, so look once more before giving up.
            return build(self.fields(refresh=True))

    def _query_body(
        self,
        where: Expression | str | None,
        select: FieldRefs | None,
        order_by: FieldRefs | None,
    ) -> dict[str, Any]:
        table_id = (self.info()).id
        return self._with_fields(
            lambda fields: build_query_body(
                table_id, fields, where=where, select=select, order_by=order_by
            )
        )

    def _fetch_page(
        self, body: dict[str, Any], top: int, skip: int, *, summarizing: bool = False
    ) -> Page:
        payload = self._transport.request(
            "POST",
            "/records/query",
            json={**body, "options": {"top": top, "skip": skip}},
            summarizing=summarizing,
        )
        return parse(page_from_api, payload, "records query")

    def _fetch_summary_page(self, body: dict[str, Any], top: int, skip: int) -> Page:
        return self._fetch_page(body, top, skip, summarizing=True)

    def query_page(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRefs | None = None,
        order_by: FieldRefs | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> Page:
        """Fetch one page of records, for controlling paging yourself.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
            select: The fields to return, by label, ID, or field object. Defaults to the table's
                default fields. Record ID# is always included.
            order_by: The fields to sort by, by label, ID, or field object. Prefix a label with `-`,
                or negate an ID, to sort descending. Without this, records come back in Record ID#
                order.
            limit: The most records to return.
            skip: How many matching records to pass over first. Use the previous page's `next_skip`.

        Returns:
            A `Page` with `records`, `total`, `skip`, `has_more`, and `next_skip`. Skipping past the
            end returns no records rather than raising an error.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was queried.
            QueryError: An argument can't be sent as written. Nothing was queried.
        """
        _require_positive("limit", limit)
        if skip < 0:
            raise QueryError("skip can't be negative.")
        body = self._query_body(where, select, order_by)
        return self._fetch_page(body, limit, skip)

    def query(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRefs | None = None,
        order_by: FieldRefs | None = None,
        page_size: int = 1000,
        max_total: int | None = None,
    ) -> RecordIterator:
        """Iterate over matching records, fetching further pages as needed.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
            select: The fields to return, by label, ID, or field object. Defaults to the table's
                default fields. Record ID# is always included.
            order_by: The fields to sort by, by label, ID, or field object. Prefix a label with `-`,
                or negate an ID, to sort descending. Without this, records come back in Record ID#
                order.
            page_size: Records to request per call to Quickbase.
            max_total: Stop after this many records in all, across pages.

        Returns:
            An iterator of `Record`, which only contacts Quickbase once you start iterating.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was queried.
            QueryError: An argument can't be sent as written. Nothing was queried. `page_size` and
                `max_total` raise this right away, and the others on first use.
        """
        _require_positive("page_size", page_size)
        if max_total is not None:
            _require_positive("max_total", max_total)
        return RecordIterator(
            lambda: self._query_body(where, select, order_by),
            self._fetch_page,
            page_size,
            max_total,
        )

    def first(
        self,
        *,
        where: Expression | str | None = None,
        select: FieldRefs | None = None,
        order_by: FieldRefs | None = None,
    ) -> Record | None:
        """Get the first matching record, or None if there isn't one.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
            select: The fields to return, by label, ID, or field object. Defaults to the table's
                default fields.
            order_by: The fields to sort by, which decide which record is first. See `query`.

        Returns:
            The first matching `Record`, or None if nothing matches.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was queried.
            QueryError: An argument can't be sent as written. Nothing was queried.
        """
        page = self.query_page(where=where, select=select, order_by=order_by, limit=1)
        return page.records[0] if page.records else None

    def get(self, record_id: int, *, select: FieldRefs | None = None) -> Record | None:
        """Get a record by its Record ID#, or None if it doesn't exist.

        Args:
            record_id: The record's Record ID#.
            select: The fields to return, by label, ID, or field object. Defaults to the table's
                default fields.

        Returns:
            The `Record`, or None if no record has this ID.

        Raises:
            InvalidValueError: `record_id` isn't a positive integer. Nothing was sent.
            UnknownFieldError: The table has no such field. Nothing was queried.
        """
        require_id("record_id", record_id)
        return self.first(where=F(RECORD_ID_FIELD) == record_id, select=select)

    def count(self, where: Expression | str | None = None) -> int:
        """Count the matching records without fetching them.

        Args:
            where: A condition built with `F`, or a hand-written string, which gets checked.
                Defaults to every record.

        Returns:
            The number of matching records.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was queried.
            QueryError: `where` can't be sent as written. Nothing was queried.
        """
        body = self._query_body(where, None, None)
        return (self._fetch_page({**body, "select": [RECORD_ID_FIELD]}, 1, 0)).total

    def _write(
        self,
        rows: list[dict[str, Any]],
        merge_field_id: int | None,
        batch_size: int,
        timeout: float | None = None,
    ) -> WriteResult:
        info = self.info()
        table_id = info.id
        result = WriteResult()
        for start, end in split_batches(rows, batch_size):
            body: dict[str, Any] = {"to": table_id, "data": rows[start:end]}
            if merge_field_id is not None:
                body["mergeFieldId"] = merge_field_id
            try:
                payload = self._transport.request(
                    "POST", "/records", json=body, key_field_id=info.key_field_id, timeout=timeout
                )
                rows_sent = end - start if merge_field_id is None else None
                result = parse(
                    partial(merge_write_result, result, offset=start, rows=rows_sent),
                    payload,
                    "write",
                )
            except PermissionDeniedError as exc:
                raise refused_write(exc, result, start) from exc
            except QuickbaseError as exc:
                if start == 0:
                    raise
                raise WriteError(
                    f"Writing stopped at row {start}, after earlier rows were written: {exc}",
                    result=result,
                ) from exc
        if result.errors:
            raise write_failure(result)
        return result

    def insert(
        self,
        records: "Iterable[Mapping[str | int, Any]] | DataFrame | FileRows",
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float | None = None,
    ) -> WriteResult:
        """Create records from `{label or ID: value}` dictionaries. It never changes existing ones.

        So it refuses a table whose key field isn't `Record ID#`, where Quickbase would change the
        record when a row has an existing value in that field. Use `upsert()` on those tables.

        Args:
            records: The records to create, as `{label or ID: value}` dictionaries. A value of
                `None` leaves the field empty, but a row needs at least one real value, because
                Quickbase can silently skip a row without one. A pandas or polars DataFrame works
                too, with a column for each field, where a missing value leaves the field empty,
                and so does a file's rows from `rows_from_file()`.
            batch_size: The most records to send in one request.
            timeout: Seconds to allow for each step of each request, instead of the client's
                `timeout`, such as for a large file. It applies with your own `http_client` too.

        Returns:
            A `WriteResult`, whose `row_ids` give each row's new Record ID#.

        Raises:
            InvalidValueError: A row can't be sent, such as one that sets `Record ID#` or has no
                real values, or the table's key field isn't `Record ID#`. Nothing was written.
            UnknownFieldError: A row names a field the table doesn't have. Nothing was written.
            WriteError: A row failed. Other records are still written, and are listed in the error's
                `result`.
        """
        _require_positive("batch_size", batch_size)
        _require_timeout(timeout)
        given = given_rows(records)
        built = self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        key_field_id = (self.info()).key_field_id
        if key_field_id not in (None, RECORD_ID_FIELD):
            raise custom_key_refusal(self._with_fields(FieldIndex), key_field_id)
        refuse_field(built, RECORD_ID_FIELD, "which insert() can't do. Use update() instead")
        refuse_blank_rows(built)
        return self._write(built, None, batch_size, timeout)

    def update(
        self,
        records: "Iterable[Mapping[str | int, Any]] | DataFrame | FileRows",
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float | None = None,
    ) -> WriteResult:
        """Change existing records. Each row must set `Record ID#`.

        A record that doesn't exist is an error rather than being created.

        Args:
            records: The changes, as `{label or ID: value}` dictionaries that each set `Record ID#`.
                Fields left out of a row keep their values, and a value of `None` clears the field.
                A pandas or polars DataFrame works too, with a column for each field, where a
                missing value clears the field, and so does a file's rows from `rows_from_file()`.
            batch_size: The most records to send in one request.
            timeout: Seconds to allow for each step of each request, instead of the client's
                `timeout`, such as for a large file. It applies with your own `http_client` too.

        Returns:
            A `WriteResult`. A record whose values didn't change is in `unchanged_ids`, not
            `updated_ids`.

        Raises:
            InvalidValueError: A row can't be sent, such as one without `Record ID#`. Nothing was
                written.
            UnknownFieldError: A row names a field the table doesn't have. Nothing was written.
            WriteError: A row failed. Other records are still written, and are listed in the error's
                `result`.
        """
        _require_positive("batch_size", batch_size)
        _require_timeout(timeout)
        given = given_rows(records)
        built = self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        require_field(built, RECORD_ID_FIELD, "so update() can't tell which record to change")
        return self._write(built, RECORD_ID_FIELD, batch_size, timeout)

    def upsert(
        self,
        records: "Iterable[Mapping[str | int, Any]] | DataFrame | FileRows",
        *,
        merge_on: FieldRef,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float | None = None,
    ) -> WriteResult:
        """Update the record whose unique `merge_on` field matches, or create one if none does.

        Args:
            records: The records, as `{label or ID: value}` dictionaries that each set `merge_on`.
                Fields left out of a row keep their values on a record that already exists. A
                pandas or polars DataFrame works too, with a column for each field, where a
                missing value leaves the field empty, and so does a file's rows from
                `rows_from_file()`.
            merge_on: The field that identifies a record, by label or ID. Quickbase requires it to
                be marked as unique.
            batch_size: The most records to send in one request.
            timeout: Seconds to allow for each step of each request, instead of the client's
                `timeout`, such as for a large file. It applies with your own `http_client` too.

        Returns:
            A `WriteResult`, with new records in `created_ids`, changed ones in `updated_ids`, and
            ones that already matched in `unchanged_ids`.

        Raises:
            InvalidValueError: A row can't be sent, such as one without `merge_on`. Nothing was
                written.
            UnknownFieldError: `merge_on` or a row names a field the table doesn't have. Nothing was
                written.
            BadRequestError: `merge_on` isn't a unique field. Nothing was written.
            WriteError: A row failed. Other records are still written, and are listed in the error's
                `result`.
        """
        _require_positive("batch_size", batch_size)
        _require_timeout(timeout)
        given = given_rows(records)

        def build(fields: list[Field]) -> tuple[list[dict[str, Any]], int]:
            index = FieldIndex(fields)
            return build_rows(given, index), index.resolve(merge_on).id

        built, merge_field_id = self._with_fields(build)
        require_field(built, merge_field_id, "the merge_on field")
        refuse_blank_rows(built)
        return self._write(built, merge_field_id, batch_size, timeout)

    def delete(self, where: Expression | str) -> int:
        """Delete the matching records and return how many were deleted.

        Args:
            where: A condition built with `F`, or a hand-written string. It's required and is
                checked first, because Quickbase would otherwise treat a lowercase operator as
                "contains" and delete far more than you meant.

        Returns:
            The number of records deleted, which is 0 if none matched.

        Raises:
            QueryError: `where` can't be sent as written. Nothing was deleted.
            UnknownFieldError: `where` names a field the table doesn't have. Nothing was deleted.
            PolicyRefusedError: The client's policy doesn't allow deleting here, or more records
                match than its `max_records_per_delete`. Nothing was deleted.
        """
        table_id = (self.info()).id
        clause = self._with_fields(lambda fields: compile_where(where, FieldIndex(fields)))
        most = self._transport.delete_limit(table_id)
        if most is not None:
            found = self.count(clause)
            if found > most:
                raise PolicyRefusedError(
                    Decision(
                        allowed=False,
                        verb="delete",
                        reason=f"{found} records match, and the policy allows deleting at most "
                        f"{most} at once. Narrow the filter",
                        hint="raise allow.delete(..., max_records_per_delete=...)",
                        table=self._transport.policy._named_table(table_id),  # pyright: ignore[reportOptionalMemberAccess, reportPrivateUsage]
                    )
                )
        payload = self._transport.request(
            "DELETE", "/records", json={"from": table_id, "where": clause}, counted=True
        )
        return parse(lambda data: int(data["numberDeleted"]), payload, "delete")

    def summarize(
        self,
        group_by: SummaryGroups | None = None,
        *,
        where: Expression | str | None = None,
        sum: FieldRefs | None = None,
        avg: FieldRefs | None = None,
        median: FieldRefs | None = None,
        std_dev: FieldRefs | None = None,
        min: FieldRefs | None = None,
        max: FieldRefs | None = None,
        distinct_count: FieldRefs | None = None,
        compare_periods: bool = False,
        max_records_scanned: int | None = None,
        page_size: int | None = None,
    ) -> list[SummaryRow]:
        """Count, total, and compare records, overall or per group, without a saved report.

        A summary that only counts, with no `group_by` or statistics, asks Quickbase how many
        records match, in one request, and reads none. Any other summary reads every matching
        record, in pages of up to 25,000. Records can change between pages, so on a table in use,
        the result may not match any one moment. A field's values are added exactly, and returned
        as `float`.

        A median keeps every value of its field in memory until the summary ends, so give it
        `max_records_scanned` on a large table.

        Args:
            group_by: The fields to group by, and `Period` for dates. Without this, the result is
                one row for all the matching records.
            where: A condition built with `F` or `where_from_dict()`, or a hand-written string.
            sum: Numeric fields to add up. Empty values are skipped.
            avg: Numeric fields to average. Empty values are skipped.
            median: Numeric fields to find the middle value of. Empty values are skipped.
            std_dev: Numeric fields to find the sample standard deviation of. Empty values are
                skipped.
            min: Numeric, date, timestamp, or time of day fields to find the lowest of.
            max: Numeric, date, timestamp, or time of day fields to find the highest of.
            distinct_count: Fields to count the different values of, such as how many clients
                have records. Empty values are skipped.
            compare_periods: Say how each group changed since the period before, in its
                `change`. `group_by` must have exactly one `Period`, and the other fields in it
                stay the same, so `[Period("Due", "month"), "Owner"]` compares each owner's months.
                A filter that cuts a period short makes that period look smaller.
            max_records_scanned: Raise `TooManyRecordsError` if more records match than this,
                without reading them. A summary that only counts reads no records, so this doesn't
                limit it. It never returns a partial result. An agent's tool should always set
                this. A client's policy can set a limit of its own, `max_records_scanned`.
            page_size: Records to request per call to Quickbase. By default it's set by the fields
                the summary reads, from 25,000 for one field down to 1,000, so that a page takes
                about 25 MB of memory however wide it is.

        Returns:
            A `SummaryRow` for each group, sorted by the group's values with empty ones last. If
            nothing matches, that's an empty list, or without `group_by`, one row with a `count` of
            0.

        Raises:
            TooManyRecordsError: More records match than `max_records_scanned`. No records were
                read. It's a `QueryError`.
            QueryError: An argument can't be used, such as a text field in `sum`. No records were
                read.
            UnknownFieldError: The table has no such field. No records were read.
            PolicyRefusedError: More records match than the client's policy allows summarizing,
                or it doesn't allow a field. No records were read.
            InvalidResponseError: A value isn't what its field's type says it should be.
        """
        if page_size is not None:
            _require_positive("page_size", page_size)
        if max_records_scanned is not None:
            _require_positive("max_records_scanned", max_records_scanned)
        statistics: dict[StatisticName, FieldRefs | None] = {
            "sum": sum,
            "avg": avg,
            "median": median,
            "std_dev": std_dev,
            "min": min,
            "max": max,
            "distinct_count": distinct_count,
        }
        summary = self._with_fields(
            lambda fields: Summary(
                fields,
                group_by=group_by,
                statistics=statistics,
                compare_periods=compare_periods,
            )
        )
        if summary.counts_only:
            # Quickbase says how many records match, so counting needs none of them.
            return [SummaryRow(count=self.count(where))]
        allowed = self._transport.summary_limit((self.info()).id)
        if max_records_scanned is not None or allowed is not None:
            found = self.count(where)
            if max_records_scanned is not None and found > max_records_scanned:
                raise TooManyRecordsError(found, max_records_scanned)
            if allowed is not None and found > allowed:
                raise self._summary_refusal(
                    f"{found} records match, and this client's policy allows summarizing at most "
                    f"{allowed}. Narrow the filter"
                )
        records = RecordIterator(
            lambda: self._query_body(where, summary.select, None),
            self._fetch_summary_page,
            summary.page_size() if page_size is None else page_size,
            allowed,
        )
        for record in records:
            summary.add(record)
        if not records._done:  # pyright: ignore[reportPrivateUsage]
            raise self._summary_refusal(
                f"More records matched while summarizing than this client's policy allows, "
                f"{allowed}, so no partial result was returned. Narrow the filter"
            )
        return summary.rows()

    def _summary_refusal(self, reason: str) -> PolicyRefusedError:
        return PolicyRefusedError(
            Decision(
                allowed=False,
                verb="read",
                reason=reason,
                hint="raise allow.read(..., max_records_scanned=...)",
            )
        )

    def preview_delete(
        self,
        where: Expression | str,
        *,
        select: FieldRefs | None = None,
        order_by: FieldRefs | None = None,
        limit: int = 100,
    ) -> DeletePreview:
        """Show what `delete()` would delete with the same `where`, without deleting anything.

        This only reads, so it works on a read-only client. Records can change before a delete, so
        it can't promise what the delete will do, and it can't tell whether the token may delete.

        Args:
            where: A condition built with `F`, or a hand-written string, checked as `delete()`
                checks it. It's required, so that leaving it out can't look like a safe preview.
            select: The fields to return. Defaults to the table's default fields.
            order_by: Which matching records come first. See `query`.
            limit: The most records to return. `total` still counts every match.

        Returns:
            A `DeletePreview`: a page whose `total` is how many records would be deleted, and
            whose `events` are what deleting them may set off.

        Raises:
            QueryError: `where` can't be sent as written, or is None. Nothing was queried.
            UnknownFieldError: The table has no such field. Nothing was queried.
        """
        if where is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise QueryError("preview_delete() needs where, as delete() does.")
        page = self.query_page(where=where, select=select, order_by=order_by, limit=limit)
        return extend(page, DeletePreview, events=self._change_events())

    def preview_update(
        self, records: "Iterable[Mapping[str | int, Any]] | DataFrame | FileRows"
    ) -> UpdatePreview:
        """Show what `update()` would change with the same records, without changing anything.

        This only reads, so it works on a read-only client. Records can change before an update, so
        it can't promise what the update will do. It also can't tell whether Quickbase will accept
        a value, such as a role that can't edit a field, or one of the table's data rules.

        Args:
            records: The records you'd pass to `update()`. If a record is in several of them, a
                later one wins. `Record ID#` must be a positive integer here, even as a string
                works in `update()`. A DataFrame or a file's rows work too, as for `update()`.

        Returns:
            An `UpdatePreview`, with `changes`, `unchanged_ids`, `missing_ids`, and the `events`
            the update may set off. A value counts as a change whenever the comparison isn't
            certain, so `update()` may report a listed record as unchanged. A file upload is
            always a change.

        Raises:
            InvalidValueError: A row can't be sent, such as one without `Record ID#`. Nothing was
                read.
            UnknownFieldError: A row names a field the table doesn't have. Nothing was read.
        """
        given = given_rows(records)
        built = self._with_fields(lambda fields: build_rows(given, FieldIndex(fields)))
        require_field(built, RECORD_ID_FIELD, "so preview_update() can't tell which record")
        merged = merge_rows(built)
        current: dict[int, Record] = {}
        limit = self._transport.record_limit((self.info()).id)
        for ids in id_batches(list(merged), limit):
            select = sorted({field_id for record_id in ids for field_id in merged[record_id]})
            page = self.query_page(
                where=F(RECORD_ID_FIELD).is_in(ids), select=select, limit=len(ids)
            )
            current.update({record.id: record for record in page.records})
        preview = build_update_preview(merged, current, FieldIndex(self.fields()))
        return replace(preview, events=self._change_events())

    def rows_from_file(
        self,
        source: FileLike,
        *,
        columns: ColumnMap | None = None,
        skip_read_only: bool = False,
        format: FileFormat | None = None,
        sheet: str | None = None,
        tz: tzinfo = UTC,
        date_format: str | None = None,
        datetime_format: str | None = None,
        encoding: str = "utf-8-sig",
        delimiter: str | None = None,
        max_rows: int | None = None,
        max_unzipped_bytes: int | None = DEFAULT_MAX_UNZIPPED_BYTES,
    ) -> FileRows:
        """Read a CSV or Excel file's rows, converting each value for its field, to write them.

        The first row names each column's field, by label. Each value is converted by its field's
        type, from text such as `1,234.5`, `45%` for a percent, `yes`, `2027-01-15`, or a user's
        email, or from Excel's own dates and numbers. A value that can't be converted is an error
        with its row and column, and nothing is written while there are any. Quickbase would
        otherwise empty a number field given text it can't read, without an error.

        It reads the whole file into memory, and contacts Quickbase only for the table's fields.
        The async client reads it on a worker thread, so a large file doesn't stop the event loop.

        ```python
        rows = tasks.rows_from_file("tasks.csv")
        for error in rows.errors:
            print(error)
        tasks.insert(rows)
        ```

        Args:
            source: A path, or an open file: text for CSV, and binary for Excel.
            columns: Which field each column goes in, by label or ID, or None to skip a column,
                such as `{"Task": "Title", "Notes": None}`. Other columns go in the field with
                their exact name.
            skip_read_only: Skip columns for fields that can't be written, such as formulas and
                Date Created, instead of raising an error, as a file from `to_csv()` has them.
            format: `csv` or `xlsx`. By default it's `xlsx` for an `.xlsx` or `.xlsm` path or a
                binary file, and `csv` otherwise.
            sheet: For Excel, the worksheet's name. Defaults to the first.
            tz: The time zone of a timestamp without one, such as one from Excel.
            date_format: How dates are written, for `datetime.strptime()`, such as `%m/%d/%Y`.
                Defaults to ISO 8601, `2027-01-15`.
            datetime_format: How timestamps are written, as for `date_format`. Defaults to ISO
                8601.
            encoding: A CSV path's text encoding. The default also reads UTF-8 that starts with a
                byte order mark, as Excel writes it.
            delimiter: What separates a CSV's values. Defaults to a tab for a `.tsv` or `.tab`
                path, and a comma otherwise.
            max_rows: The most rows with values to read, or None for no limit. Set this for a
                file you didn't make, as reading stops at the limit. Blank rows don't count.
            max_unzipped_bytes: The most an Excel file may unpack to, or None for no limit. It's
                a zip, so a small file can unpack to something huge.

        Returns:
            A `FileRows`, with the `rows` to write, each one's row number, and every error.

        Raises:
            FileTooLargeError: The file is over `max_rows` or `max_unzipped_bytes`.
            InvalidValueError: The file can't be read as a table: it has no header row, names a
                column twice, has a column that isn't a field, or writes a field that can't be
                written. Fix these before anything else.
        """
        check_read_limits(max_rows, max_unzipped_bytes)
        file_kind = file_format(source, format)
        fields = self.fields()

        def parse() -> FileRows:
            cells = file_cells(
                source,
                file_kind,
                sheet=sheet,
                encoding=encoding,
                delimiter=delimiter,
                max_unzipped_bytes=max_unzipped_bytes,
            )
            return read_rows(
                cells,
                fields,
                columns=columns,
                skip_read_only=skip_read_only,
                tz=tz,
                date_format=date_format,
                datetime_format=datetime_format,
                max_rows=max_rows,
            )

        return run_blocking(parse)

    def events(self) -> list[AppEvent]:
        """List the webhooks, Quickbase actions, notifications, subscriptions, reminders, and
        record-triggered automations that watch this table. See `App.events()`.

        Returns:
            An `AppEvent` for each, active or not.

        Raises:
            PermissionDeniedError: The token isn't an administrator's.
        """
        table_id = (self.info()).id
        return [item for item in self._app.events() if item.table_id == table_id]

    def _change_events(self) -> tuple[AppEvent, ...] | None:
        """The active events on this table that changing records may set off, for a preview, or
        None if they can't be read, which shouldn't stop the preview."""
        try:
            found = self.events()
        except (APIError, InvalidResponseError, PolicyRefusedError):
            return None
        return tuple(item for item in found if item.active and item.runs_on_changes)

    def field_usage(self, fields: FieldRef | Sequence[FieldRef] | None = None) -> list[FieldUsage]:
        """Say where fields are used, such as in reports, forms, formulas, and pipelines.

        These are the counts on each field's Usage page in Quickbase, which help to judge what
        deleting or changing a field would break.

        Args:
            fields: One field or a list of them, by label or ID. Defaults to every field.

        Returns:
            A `FieldUsage` for each field, in the order asked for, or the table's order.

        Raises:
            UnknownFieldError: A field doesn't exist. Nothing was read.
            PermissionDeniedError: The token isn't an administrator's.
        """
        table_id = (self.info()).id
        if fields is None:
            wanted = [item.id for item in self.fields()]
            # One request usually covers the table. Ask for any it left out one by one, which
            # also covers a table larger than one response.
            found = {item.field_id: item for item in self._usage(table_id)}
        else:
            refs = [fields] if isinstance(fields, str | int) else list(fields)
            wanted = self.resolve_field_ids(refs)
            found = {}
        for field_id in wanted:
            if field_id not in found:
                for item in self._usage(table_id, field_id):
                    found[item.field_id] = item
        return [found[field_id] for field_id in wanted if field_id in found]

    def _usage(self, table_id: str, field_id: int | None = None) -> list[FieldUsage]:
        path = "/fields/usage" if field_id is None else f"/fields/usage/{field_id}"
        payload = self._transport.request("GET", path, params={"tableId": table_id})
        return parse(
            lambda rows: [field_usage_from_api(row) for row in rows], payload, "field usage"
        )

    def run_formula(self, formula: str, *, record_id: int | None = None) -> FormulaResult:
        """Run a formula against this table, without saving it.

        Don't run formula text you don't control: a formula can read other tables, whichever
        table you run it on.

        Args:
            formula: The formula's text, in Quickbase's formula language. It can't be blank.
            record_id: The record whose values the formula reads. It's required if the formula
                uses fields.

        Returns:
            A `FormulaResult`. Its `value` is the result as a Python value where that's safe,
            and `text` is exactly what Quickbase sent.

        Raises:
            InvalidValueError: `formula` is blank, or `record_id` isn't a positive integer.
                Nothing was sent.
            BadRequestError: Quickbase can't run the formula, and its message says why.
            NotFoundError: The record doesn't exist.
        """
        require_formula(formula)
        if record_id is not None:
            require_id("record_id", record_id)
        body: dict[str, Any] = {"formula": formula, "from": (self.info()).id}
        if record_id is not None:
            body["rid"] = record_id
        payload = self._transport.request("POST", "/formula/run", json=body)
        return parse(formula_result_from_api, payload, "formula")

    def _file_path(self, record_id: int, field: FieldRef, version: int) -> str:
        require_id("record_id", record_id)
        table_id = (self.info()).id

        def resolve(fields: list[Field]) -> int:
            item = FieldIndex(fields).resolve(field)
            require_file_field(item)
            return item.id

        field_id = self._with_fields(resolve)
        return f"/files/{table_id}/{record_id}/{field_id}/{version}"

    def download_file(
        self,
        record_id: int,
        field: FieldRef,
        *,
        version: int | None = None,
        timeout: float | None = None,
    ) -> FileDownload:
        """Download a file from a record, by default its latest version.

        Quickbase keeps a limited number of versions, so an older one may be gone.

        Args:
            record_id: The Record ID# of the record that holds the file.
            field: The file attachment field, by label or ID.
            version: The version to download, starting at 1. Defaults to the latest.
            timeout: Seconds to allow for each step of the request, instead of the client's
                `timeout`, such as for a large file. It applies with your own `http_client` too.

        Returns:
            A `FileDownload`, with the file's `name`, `data`, and `content_type`. Don't trust
            `name` as a path, or `content_type` as a description of what's in the file.

        Raises:
            InvalidValueError: `record_id` or `version` isn't a positive integer, `timeout` isn't
                a positive number, or `field` isn't a file attachment field. Nothing was sent.
            UnknownFieldError: The table has no such field. Nothing was sent.
            NotFoundError: The record, version, or file doesn't exist.
        """
        _require_timeout(timeout)
        if version is not None:
            require_id("version", version)
        # Version 0 means the latest to Quickbase.
        path = self._file_path(record_id, field, version or 0)
        response = self._transport.get_raw(path, timeout=timeout)
        return parse(file_download_from_response, response, "file")

    def delete_file_version(self, record_id: int, field: FieldRef, *, version: int) -> None:
        """Delete one version of a file. Deleting the only version leaves the field empty.

        The record stays, and the other versions keep their numbers.

        Args:
            record_id: The Record ID# of the record that holds the file.
            field: The file attachment field, by label or ID.
            version: The version to delete, starting at 1. It's required, because Quickbase
                treats version 0 as the latest and deletes it.

        Raises:
            InvalidValueError: `record_id` or `version` isn't a positive integer, or `field`
                isn't a file attachment field. Nothing was sent.
            UnknownFieldError: The table has no such field. Nothing was sent.
            NotFoundError: The record or version doesn't exist.
        """
        require_id("version", version)
        self._transport.request("DELETE", self._file_path(record_id, field, version))

    def _forget_fields(self, table_id: str) -> None:
        self._cache.delete(f"fields:{table_id}", f"relationships:{table_id}")

    def create_field(
        self,
        label: str,
        type: str,
        *,
        choices: Sequence[str] | None = None,
        required: bool | None = None,
        unique: bool | None = None,
        properties: Mapping[str, Any] | None = None,
        formula: str | None = None,
        help_text: str | None = None,
        bold: bool | None = None,
        no_wrap: bool | None = None,
        find_enabled: bool | None = None,
        appears_by_default: bool | None = None,
        add_to_forms: bool | None = None,
    ) -> Field:
        """Create a field and return it.

        Quickbase sets `required` and `unique` in a second request, so if that one fails the field
        exists without them. Use `ensure_field()` to repair it.

        Args:
            label: The new field's label. Another field can't have the same label, ignoring case.
            type: One of `text`, `text-multiple-choice`, `text-multi-line`, `rich-text`,
                `numeric`, `currency`, `rating`, `percent`, `multitext`, `email`, `url`, `duration`,
                `date`, `datetime`, `timestamp`, `timeofday`, `checkbox`, `user`, `multiuser`,
                `address`, `phone`, or `file`. A `datetime` field reports its type as `timestamp`.
            choices: The options of a `text-multiple-choice` or `multitext` field. Quickbase still
                accepts other values unless `properties` sets `allowNewChoices` to False.
            required: Whether the field must have a value. Quickbase refuses this if the table has
                records, because the new field is empty on all of them.
            unique: Whether values must be different on every record.
            properties: Other settings, sent as they are, such as `{"decimalPlaces": 2}`. Quickbase
                refuses a key it doesn't know.
            formula: Makes this a formula field. `type` must match the formula's result, and
                can't be `timestamp` or `text-multi-line`. Use `datetime` for a timestamp formula.
            help_text: The help Quickbase shows beside the field on forms.
            bold: Whether forms and reports show the field's values in bold.
            no_wrap: Whether forms and reports keep the field's values on one line.
            find_enabled: Whether the field's values are searched by Quickbase's search box.
            appears_by_default: Whether a query or report without chosen columns returns the field.
            add_to_forms: Whether Quickbase adds the field to the table's forms. Quickbase doesn't
                report this afterward.

        Returns:
            The new `Field`.

        Raises:
            BadRequestError: Quickbase refused the field, such as for an unknown type, a label
                that's already used, or `choices` on a type without them. If it refused `required`
                or `unique`, the field was still created, and the message says so.
            InvalidValueError: `formula` is blank, or a setting has the wrong type. Nothing was
                sent.
        """
        merged = field_properties(properties, formula)
        settings = field_settings(
            help_text=help_text,
            bold=bold,
            no_wrap=no_wrap,
            find_enabled=find_enabled,
            appears_by_default=appears_by_default,
            add_to_forms=add_to_forms,
        )
        table_id = (self.info()).id
        if choices is not None:
            merged["choices"] = list(choices)
        body: dict[str, Any] = {"label": label, "fieldType": type, **settings}
        if merged:
            body["properties"] = merged
        if "formula" in merged:
            self._check_formula(table_id, merged["formula"])
        payload = self._transport.request(
            "POST", "/fields", params={"tableId": table_id}, json=body, sources_checked=True
        )
        self._forget_fields(table_id)
        field = parse(field_from_api, payload, "field")
        if required is None and unique is None:
            return field
        try:
            return self.update_field(field.id, required=required, unique=unique)
        except QuickbaseError as exc:
            exc.args = (
                f"Field {field.id} was created, but setting required or unique failed, so it "
                f"exists without them. Use ensure_field() to repair it: {exc}",
            )
            exc._changed = True  # pyright: ignore[reportPrivateUsage]
            raise

    def update_field(
        self,
        field: FieldRef,
        *,
        label: str | None = None,
        required: bool | None = None,
        unique: bool | None = None,
        properties: Mapping[str, Any] | None = None,
        formula: str | None = None,
        help_text: str | None = None,
        bold: bool | None = None,
        no_wrap: bool | None = None,
        find_enabled: bool | None = None,
        appears_by_default: bool | None = None,
    ) -> Field:
        """Change a field's settings and return it. Anything left as None stays as it is.

        A field's type can't be changed here.

        Args:
            field: The field's label or ID.
            label: A new label.
            required: Whether the field must have a value. Quickbase refuses `True` if any record
                has this field empty.
            unique: Whether values must be different on every record. Quickbase refuses `True` if
                the field already holds duplicates.
            properties: Other settings, sent as they are. `{"choices": [...]}` replaces the whole
                list of choices.
            formula: New formula text, only for a formula field. It can't be blank, because
                Quickbase would accept that and empty the field's values.
            help_text: The help Quickbase shows beside the field on forms.
            bold: Whether forms and reports show the field's values in bold.
            no_wrap: Whether forms and reports keep the field's values on one line.
            find_enabled: Whether the field's values are searched by Quickbase's search box.
            appears_by_default: Whether a query or report without chosen columns returns the field.

        Returns:
            The `Field`, with its new settings.

        Raises:
            UnknownFieldError: The table has no such field. Nothing was sent.
            InvalidValueError: There's nothing to change, `formula` is blank, or a setting has the
                wrong type. Nothing was sent.
            BadRequestError: Quickbase refused the change, and the field is as it was.
        """
        merged = field_properties(properties, formula)
        settings = field_settings(
            help_text=help_text,
            bold=bold,
            no_wrap=no_wrap,
            find_enabled=find_enabled,
            appears_by_default=appears_by_default,
        )
        table_id = (self.info()).id
        target = self._with_fields(lambda fields: FieldIndex(fields).resolve(field))
        field_id = target.id
        if "formula" in merged:
            if self._transport.policy is not None and target.type in UNSAFE_FORMULA_TYPES:
                raise PolicyRefusedError(
                    Decision(
                        allowed=False,
                        verb="formula",
                        reason=f"A {target.type} formula makes something people click, which acts "
                        "as them",
                        table=self._transport.policy._named_table(table_id),  # pyright: ignore[reportPrivateUsage]
                    )
                )
            self._check_formula(table_id, merged["formula"])
        optional = {
            "label": label,
            "required": required,
            "unique": unique,
            "properties": merged or None,
            **settings,
        }
        body = {k: v for k, v in optional.items() if v is not None}
        if not body:
            raise InvalidValueError("update_field() needs at least one setting to change.")
        payload = self._transport.request(
            "POST",
            f"/fields/{field_id}",
            params={"tableId": table_id},
            json=body,
            sources_checked=True,
        )
        self._forget_fields(table_id)
        return parse(field_from_api, payload, "field")

    def _check_formula(self, table_id: str, formula: Any) -> None:
        """Refuse a formula that reads what this client can't, when it has a policy."""
        policy = self._transport.policy
        if policy is not None and isinstance(formula, str):
            check_formula_sources(formula, self.fields(), policy, table_id)

    def _check_parent_field(self, parent: "Table", field_id: int, what: str) -> None:
        """Refuse a lookup of a parent's field that this client can't read, or from a parent
        with a row limit."""
        policy = self._transport.policy
        if policy is not None:
            parent_id = (parent.info()).id
            check_lookup_parent(policy, parent_id, what)
            found = parent._with_fields(lambda fields: FieldIndex(fields).resolve(field_id))  # pyright: ignore[reportPrivateUsage]
            check_readable(found, policy, parent_id, what)

    def _check_own_fields(
        self, field_ids: Sequence[int | None], what: str, verb: Any = "lookup"
    ) -> None:
        """Refuse a summary or lookup that shows this table's fields, unless they're readable."""
        policy = self._transport.policy
        if policy is None:
            return
        table_id = (self.info()).id
        index = self._with_fields(FieldIndex)
        for field_id in field_ids:
            if field_id is None:
                raise PolicyRefusedError(
                    Decision(
                        allowed=False,
                        verb=verb,
                        layer="derived",
                        reason=f"{what} needs the relationship's foreign key, which this client "
                        "can't see",
                    )
                )
            check_readable(index.resolve(field_id), policy, table_id, what, verb)

    def delete_fields(self, fields: FieldRef | Sequence[FieldRef]) -> None:
        """Delete fields and all their data.

        Args:
            fields: One field or a list of them, by label or ID.

        Raises:
            UnknownFieldError: A field doesn't exist. Nothing was deleted.
            InvalidValueError: The list is empty. Nothing was sent.
            WriteError: A field couldn't be deleted, such as a built-in field. The others in the
                list were still deleted.
        """
        table_id = (self.info()).id
        refs = [fields] if isinstance(fields, str | int) else list(fields)
        if not refs:
            raise InvalidValueError("delete_fields() needs at least one field.")
        field_ids = self.resolve_field_ids(refs)
        payload = self._transport.request(
            "DELETE", "/fields", params={"tableId": table_id}, json={"fieldIds": field_ids}
        )
        self._forget_fields(table_id)
        errors, deleted = parse(
            lambda data: (list(data.get("errors") or ()), list(data.get("deletedFieldIds") or ())),
            payload,
            "delete fields",
        )
        if errors:
            error = WriteError(
                f"Some fields weren't deleted, and fields {deleted} were: "
                f"{'; '.join(map(str, errors))}"
            )
            error._changed = bool(deleted)  # pyright: ignore[reportPrivateUsage]
            raise error

    def create_relationship(
        self,
        parent: "Table",
        *,
        foreign_key: str,
        lookup_fields: Sequence[FieldRef] = (),
    ) -> Relationship:
        """Make this table a child of `parent`, with a new foreign key field on this table.

        Quickbase also adds two fields to the parent: a list of the child's records, and a link to
        add one. Calling this again makes another relationship, even with the same label, so use
        `ensure_relationship()` in a script that may run twice.

        Args:
            parent: The `Table` to link to, which becomes the parent.
            foreign_key: The label of the new field on this table, which holds the Record
                ID# of each record's parent.
            lookup_fields: Fields of the parent to show on this table, by label or ID. Quickbase
                names each new field after the foreign key and the field, such as
                `Project - Name`.

        Returns:
            The `Relationship`. Its `lookup_field_ids` are the new fields on this table, so don't
            pass them back as `lookup_fields`.

        Raises:
            UnknownFieldError: The parent has no such field. Nothing was sent.
        """
        table_id = (self.info()).id
        parent_id = (parent.info()).id
        lookup_ids = parent.resolve_field_ids(lookup_fields)
        for lookup_id in lookup_ids:
            self._check_parent_field(parent, lookup_id, "A lookup")
        payload = self._transport.request(
            "POST",
            f"/tables/{table_id}/relationship",
            json={
                "parentTableId": parent_id,
                "foreignKeyField": {"label": foreign_key},
                "lookupFieldIds": lookup_ids,
            },
            sources_checked=True,
        )
        self._forget_fields(table_id)
        self._forget_fields(parent_id)
        return parse(relationship_from_api, payload, "relationship")

    def ensure_field(
        self,
        label: str,
        type: str,
        *,
        choices: Sequence[str] | None = None,
        required: bool | None = None,
        unique: bool | None = None,
        properties: Mapping[str, Any] | None = None,
        formula: str | None = None,
        help_text: str | None = None,
        bold: bool | None = None,
        no_wrap: bool | None = None,
        find_enabled: bool | None = None,
        appears_by_default: bool | None = None,
    ) -> Field:
        """Return the field with this label, creating it or changing it to match what you pass.

        This makes a setup script safe to run again, and repairs a field whose `required` or
        `unique` didn't get set, because Quickbase sets those in a second request. The arguments
        are the same as for `create_field()`, and anything left as None is ignored on an existing
        field. `choices` are compared ignoring order.

        Args:
            label: The field's label, ignoring case.
            type: The field's type. `datetime` matches a field that reports `timestamp`.
            choices: The options of a `text-multiple-choice` or `multitext` field.
            required: Whether the field must have a value. Quickbase refuses `True` if any record
                has this field empty, which includes a new field on a table with records.
            unique: Whether values must be different on every record.
            properties: Other settings, sent as they are.
            formula: The text of a formula field.
            help_text: The help Quickbase shows beside the field on forms.
            bold: Whether forms and reports show the field's values in bold.
            no_wrap: Whether forms and reports keep the field's values on one line.
            find_enabled: Whether the field's values are searched by Quickbase's search box.
            appears_by_default: Whether a query or report without chosen columns returns the field.

        Returns:
            The `Field`, whether it was found, created, or changed.

        Raises:
            SchemaMismatchError: The field exists with a different type, because Quickbase can't
                always convert one. It wasn't changed.
            BadRequestError: Quickbase refused the field or the change.
        """
        given: dict[str, Any] = {
            "help_text": help_text,
            "bold": bold,
            "no_wrap": no_wrap,
            "find_enabled": find_enabled,
            "appears_by_default": appears_by_default,
        }
        settings = field_settings(**given)
        fields = self.fields(refresh=True)
        found = next((item for item in fields if item.label.casefold() == label.casefold()), None)
        if found is None:
            return self.create_field(
                label,
                type,
                choices=choices,
                required=required,
                unique=unique,
                properties=properties,
                formula=formula,
                **given,
            )
        reported_type = "timestamp" if type == "datetime" else type
        if found.type != reported_type:
            raise SchemaMismatchError(
                f"Field {found.label!r} is {found.type}, not {type}, so it wasn't changed."
            )
        wanted = field_properties(properties, formula)
        if choices is not None:
            wanted["choices"] = list(choices)
        current: Mapping[str, Any] = found.raw.get("properties") or {}
        changed = {
            key: value
            for key, value in wanted.items()
            if not _same_property(key, current.get(key), value)
        }
        new_required = required if required not in (None, found.required) else None
        new_unique = unique if unique not in (None, found.unique) else None
        api_names = {api: name for name, api in FIELD_SETTINGS.items()}
        new_settings = {
            api_names[key]: value
            for key, value in settings.items()
            if found.raw.get(key, "" if key == "fieldHelp" else None) != value
        }
        if not changed and new_required is None and new_unique is None and not new_settings:
            return found
        return self.update_field(
            found.id,
            required=new_required,
            unique=new_unique,
            properties=changed or None,
            **new_settings,
        )

    def ensure_relationship(self, parent: "Table", *, foreign_key: str) -> Relationship:
        """Return this table's relationship with `parent`, creating it if it doesn't exist.

        Use the result with `ensure_lookup_field()` and `ensure_summary_field()`.

        Args:
            parent: The `Table` to link to, which becomes the parent.
            foreign_key: The label of the field on this table that holds the Record ID# of
                each record's parent, ignoring case when looking for an existing relationship.

        Returns:
            The existing or new `Relationship`.

        Raises:
            UnknownTableError: This app has no such table, or no such parent.
            BadRequestError: Quickbase refused to create the relationship.
            PolicyRefusedError: The client's policy doesn't allow it. Nothing was created.
        """
        parent_id = (parent.info()).id
        labels = {item.id: item.label.casefold() for item in self.fields(refresh=True)}
        for item in self.relationships(refresh=True):
            label = labels.get(item.foreign_key_field_id or 0)
            if item.parent_table_id == parent_id and label == foreign_key.casefold():
                return item
        return self.create_relationship(parent, foreign_key=foreign_key)

    def ensure_lookup_field(
        self, relationship: Relationship, parent_field: FieldRef, *, label: str | None = None
    ) -> Field:
        """Return this table's lookup of `parent_field`, creating it if it doesn't exist.

        Call this on the child table, and the new field is on the child. It's found by what it
        points at, so renaming it doesn't make a script create another.

        Args:
            relationship: The relationship, from `ensure_relationship()`.
            parent_field: The parent's field to look up, by label or ID.
            label: A label for the lookup, since Quickbase names one after the foreign key and the
                field, such as `Project - Name`. An existing lookup is renamed to match.

        Returns:
            The lookup `Field`, on this table.

        Raises:
            UnknownFieldError: The parent has no such field. Nothing was sent.
        """
        parent = self._app.table(relationship.parent_table_id)
        target_id = (parent.resolve_field_ids([parent_field]))[0]
        found = self._find_lookup(relationship, target_id)
        if found is None:
            table_id = (self.info()).id
            self._check_parent_field(parent, target_id, "A lookup")
            self._check_own_fields([relationship.foreign_key_field_id], "A lookup")
            # This adds to the existing lookups, and takes the parent's field IDs, not the child's.
            self._transport.request(
                "POST",
                f"/tables/{table_id}/relationship/{relationship.id}",
                json={"lookupFieldIds": [target_id]},
                parent_table_id=relationship.parent_table_id,
                sources_checked=True,
            )
            self._forget_fields(table_id)
            self._forget_fields(relationship.parent_table_id)
            found = self._find_lookup(relationship, target_id)
            if found is None:
                raise self._created_but_unseen(f"A lookup of field {target_id}")
        if label is not None and found.label != label:
            return self.update_field(found.id, label=label)
        return found

    def _find_lookup(self, relationship: Relationship, target_id: int) -> Field | None:
        for item in self.fields(refresh=True):
            if (
                item.mode == "lookup"
                and item.lookup_reference_field_id == relationship.foreign_key_field_id
                and item.lookup_target_field_id == target_id
            ):
                return item
        return None

    def ensure_summary_field(
        self,
        relationship: Relationship,
        child_field: FieldRef,
        label: str,
        *,
        function: str = "sum",
        where: Expression | str | None = None,
    ) -> Field:
        """Return the summary of this table's `child_field` on the parent, creating it if needed.

        Call this on the child table, and the new field is on the parent. **A summary with no child
        records is empty, not 0**, so wrap it in `Nz()` in a formula if your code expects a number.
        To count the child records, use `ensure_count_field()`.

        Args:
            relationship: The relationship, from `ensure_relationship()`.
            child_field: This table's field to summarize, by label or ID.
            label: The label of the summary field on the parent.
            function: The calculation: `sum`, `avg`, `max`, `min`, `std_dev`, `distinct_count`,
                `combined_text`, or `combined_user`. Quickbase's names, such as `STD-DEV`, work
                too.
            where: Only combine the child records that match, as a condition on this table built
                with `F`, or a string such as `{7.EX.true}`. It's checked first, as Quickbase saves
                one it can't use and the summary is then always empty.

        Returns:
            The summary `Field`, on the parent.

        Raises:
            InvalidValueError: `function` isn't one of these, or is `count`, which takes no field.
                Nothing was sent.
            UnknownFieldError: This table has no such field. Nothing was sent.
            QueryError: `where` can't be sent as written. Nothing was sent.
            SchemaMismatchError: The label is already used on the parent by anything but this
                summary. It wasn't changed.
            BadRequestError: Quickbase can't apply `function` to this field, such as `sum` of a
                checkbox.
        """
        found = summary_function(function)
        if found is None:
            raise InvalidValueError(f"function must be one of: {', '.join(SUMMARY_FUNCTIONS)}.")
        if found == "count":
            raise InvalidValueError(
                "count counts the child records rather than summarizing a field, so use "
                "ensure_count_field()."
            )
        target_id = (self.resolve_field_ids([child_field]))[0]
        code = quickbase_function(found)
        return self._ensure_summary(relationship, label, code, target_id, where)

    def ensure_count_field(
        self, relationship: Relationship, label: str, *, where: Expression | str | None = None
    ) -> Field:
        """Return the count of this table's records for each parent, creating it if needed.

        Call this on the child table, and the new field is on the parent. Unlike other summaries,
        a parent with no child records counts 0, not empty.

        Args:
            relationship: The relationship, from `ensure_relationship()`.
            label: The label of the count field on the parent.
            where: Only count the records that match, as in `ensure_summary_field()`.

        Returns:
            The count `Field`, on the parent.

        Raises:
            SchemaMismatchError: The label is already used on the parent by anything but this
                count. It wasn't changed.
            UnknownFieldError: `where` uses a field this table doesn't have. Nothing was sent.
            QueryError: `where` can't be sent as written. Nothing was sent.
        """
        return self._ensure_summary(relationship, label, "COUNT", None, where)

    def _ensure_summary(
        self,
        relationship: Relationship,
        label: str,
        function: str,
        target_id: int | None,
        where: Expression | str | None,
    ) -> Field:
        """Find or create a summary on the parent. A count has no target, which Quickbase records
        as field 0. Under a policy, the child's row limit is ANDed into the filter, and an existing
        summary's filter is compared when the policy shows it, which it does when every field it
        uses is readable here."""
        table_id = (self.info()).id
        policy = self._transport.policy
        clause = None if where is None else self.compile_where(where)
        if clause is not None:
            check_filterable(clause, policy, table_id, "A summary")
        clause = limited_where(policy, table_id, clause)
        parent = self._app.table(relationship.parent_table_id)
        fields = parent.fields(refresh=True)
        found = next((item for item in fields if item.label.casefold() == label.casefold()), None)
        if found is not None:
            if (
                found.mode != "summary"
                or (found.summary_target_field_id or 0) != (target_id or 0)
                or found.summary_reference_field_id != relationship.foreign_key_field_id
                or found.summary_function != function
                or not _same_filter(found, clause, policy)
            ):
                raise SchemaMismatchError(
                    f"Field {found.label!r} on the parent isn't this summary, so it wasn't changed."
                )
            return found
        summary: dict[str, Any] = {"label": label, "accumulationType": function}
        if target_id is not None:
            summary["summaryFid"] = target_id
        if clause is not None:
            summary["where"] = clause
        shown = [relationship.foreign_key_field_id]
        self._check_own_fields(
            [*shown, target_id] if target_id is not None else shown, "A summary", "summary"
        )
        self._transport.request(
            "POST",
            f"/tables/{table_id}/relationship/{relationship.id}",
            json={"summaryFields": [summary]},
            parent_table_id=relationship.parent_table_id,
            sources_checked=True,
        )
        self._forget_fields(table_id)
        self._forget_fields(relationship.parent_table_id)
        fields = parent.fields(refresh=True)
        created = next((item for item in fields if item.label.casefold() == label.casefold()), None)
        if created is None:
            raise self._created_but_unseen(f"Summary {label!r}")
        return created

    def _created_but_unseen(self, what: str) -> QuickbaseError:
        """The error for a field Quickbase made that this client can't find afterward."""
        if self._transport.policy is not None:
            error: QuickbaseError = UnknownFieldError(
                f"{what} was created, but this client's policy hides new fields, so it can't be "
                "returned. Use new_fields='read', or bind the policy again."
            )
        else:
            error = InvalidResponseError(
                f"Quickbase accepted {what.lower()}, but it can't be found."
            )
        error._changed = True  # pyright: ignore[reportPrivateUsage]
        return error

    def resolve_field_ids(self, fields: Sequence[FieldRef]) -> list[int]:
        """Turn field labels or IDs into IDs.

        Args:
            fields: The fields, by label or ID, in any mix.

        Returns:
            The field IDs, in the same order as `fields`.

        Raises:
            UnknownFieldError: The table has no such field.
        """
        return self._with_fields(
            lambda known: [FieldIndex(known).resolve(ref).id for ref in fields]
        )

    def operation(
        self,
        name: OperationName,
        *,
        fields: Sequence[FieldRef] | None = None,
        field_descriptions: Mapping[FieldRef, str] | None = None,
        reports: Sequence[str] | None = None,
        report_descriptions: Mapping[str, str] | None = None,
        formula: str | None = None,
        max_records_per_call: int | None = None,
        max_records_per_write: int = DEFAULT_MAX_ROWS,
        scope: Expression | None = None,
        require_preview: bool = False,
        max_default_fields: int | None = None,
    ) -> "Operation":
        """Make one operation on this table into a tool for a model, with its JSON Schema.

        The schema describes the arguments a model may send, and `run()` checks them against the
        same lists before calling the table, so a model can't use anything it wasn't offered. The
        lists hold only what the client's policy allows, narrowed by `fields`.

        ```python
        search = tasks.operation(
            "query", fields=["Title", "Status"]
        )
        tool = {
            "name": "search_tasks",
            "input_schema": search.schema,
        }
        scope = F("Project") == project
        page = search.run(arguments, scope=scope)
        ```

        See [Using pyquickbase from an AI agent](agents/operations.md#giving-a-model-operations) for
        what each operation takes.

        Args:
            name: What the operation does, such as `query` or `insert`. Each runs the method of the
                same name, and `run_report` and `run_summary_report` run a report.
            fields: The only fields it may use, by label or ID. Defaults to every field the client
                may use for the operation.
            field_descriptions: What fields mean, as `{label or ID: text}`, which the schema shows
                the model. Each must be for a field the operation offers.
            reports: The reports `run_report` or `run_summary_report` may run, by ID. Defaults to
                every report of that kind the client may run.
            report_descriptions: What reports are for, as `{ID: text}`, which the schema shows the
                model. Each must be for a report the operation offers.
            formula: For `run_formula`, the formula's text, which your code fixes. The model only
                chooses the record, as a formula can read any table.
            max_records_per_call: The most records one call may return, or for `delete`, may
                match. A delete that matches more is refused. Give `preview_delete` the same
                number, and its result says when a delete would be refused. Defaults to 100, or for
                `summarize`, 10,000 records read. The client's policy can lower it, and its
                `max_paging_depth` also limits how far `skip` can page.
            max_records_per_write: The most records one `insert` or `update` may send.
            scope: A condition your code fixes, such as the user's project, that every record the
                operation reaches must match, as `run()`'s `scope` does. Given here, the schema
                also tells the model what it is, leaves its fields out of what a write may set,
                and refuses a write that sets one with a message that says why. An `insert`
                needs a scope of only `==` conditions joined with `&`, whose fields the client's
                policy lets it set. A report or formula can't take one.
            require_preview: For `update` and `delete`: run only with the `confirmation` that
                `preview_update` or `preview_delete` returns for exactly the same arguments,
                within 15 minutes, so a model can't skip the preview. A delete is also refused if
                how many records match has changed since. Make the preview with the same
                client and `scope`.
            max_default_fields: For `query`: the most of the table's default fields to return
                when the model doesn't name any in `select`. A table with more returns its first
                ones, and `tool_result()` says how many it left out. Defaults to every one.

        Returns:
            An `Operation`, with its `schema` and `run()`.

        Raises:
            PolicyRefusedError: The client's policy doesn't allow this operation at all.
            ConfigurationError: The table has nothing for it, such as no file fields, or
                `formula` is missing or given for another operation.
            UnknownFieldError: `fields` or `field_descriptions` names a field the client can't see,
                or one the operation doesn't offer.
            UnknownReportError: `reports` has an ID this table doesn't have, or
                `report_descriptions` has one the operation doesn't offer.
        """
        if name not in operation_names():
            raise ConfigurationError(f"name must be one of: {', '.join(operation_names())}.")
        transport = self._transport
        info = self.info()
        if isinstance(reports, Mapping):
            raise ConfigurationError(
                "reports takes report IDs. Describe them with report_descriptions={ID: text}."
            )
        report_ids = None if reports is None else [str(item) for item in reports]
        report_notes = {str(key): str(text) for key, text in (report_descriptions or {}).items()}
        needs_reports = name in ("run_report", "run_summary_report")
        allowed = allowed_for(
            self.fields(),
            self.reports() if needs_reports else (),
            table_id=info.id,
            key_field_id=info.key_field_id,
            policy=transport.policy,
            wanted=fields,
            wanted_reports=report_ids if needs_reports else None,
        )
        offered_reports = {item.id for item in (*allowed.reports, *allowed.summary_reports)}
        unknown = sorted(set(report_notes) - offered_reports)
        if unknown:
            raise UnknownReportError(
                f"report_descriptions has {', '.join(unknown)}, which this operation doesn't offer."
            )
        scope_text, scope_labels = "", frozenset[str]()
        if scope is not None:
            index = FieldIndex(self.fields())
            scope_labels = check_scope(name, scope, index, allowed)
            scope_text = describe_scope(scope, index)
        reason = unavailable(name, allowed, formula)
        if reason is not None:
            policy = transport.policy
            raise refused_operation(
                name,
                reason,
                limited=policy is not None,
                table=None if policy is None else policy._named_table(info.id),  # pyright: ignore[reportPrivateUsage]
            )
        spec = make_spec(
            name,
            allowed,
            descriptions=field_descriptions,
            report_descriptions=report_notes,
            max_records=max_records_per_call,
            max_rows=max_records_per_write,
            formula=formula,
            scope=scope,
            scope_text=scope_text,
            scope_labels=scope_labels,
            require_preview=require_preview,
            max_default_fields=max_default_fields,
        )
        return Operation(self, spec)

    def update_info(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        single_record_name: str | None = None,
        plural_record_name: str | None = None,
    ) -> TableInfo:
        """Change the table's name or descriptions, and return its new details. Anything left as
        None stays as it is.

        This `Table` keeps working after a rename, even if it was found by its old name.

        Args:
            name: A new name. Another table in the app can't have it.
            description: A new description.
            single_record_name: What the table calls one record, such as `Project`.
            plural_record_name: What the table calls several records, such as `Projects`.

        Returns:
            The table's `TableInfo`, with its new settings.

        Raises:
            InvalidValueError: There's nothing to change. Nothing was sent.
            BadRequestError: Quickbase refused the change, such as for a name another table has.
        """
        optional = {
            "name": name,
            "description": description,
            "singleRecordName": single_record_name,
            "pluralRecordName": plural_record_name,
        }
        body = {key: value for key, value in optional.items() if value is not None}
        if not body:
            raise InvalidValueError("update_info() needs at least one setting to change.")
        table_id = (self.info()).id
        payload = self._transport.request(
            "POST", f"/tables/{table_id}", params={"appId": self._app.id}, json=body
        )
        self._cache.delete(f"tables:{self._app.id}")
        self._ref = table_id  # The old name no longer finds it.
        return parse(table_info_from_api, payload, "table")

    def delete_relationship(self, relationship: Relationship) -> None:
        """Delete a relationship in which this table is the child.

        Quickbase also deletes its lookup fields on this table, and its summary fields and the
        fields that list and add child records on the parent. The foreign key field stays, with
        its values, as a plain number field.

        Args:
            relationship: The relationship, from `relationships()`.

        Raises:
            InvalidValueError: This table isn't the relationship's child. Nothing was sent.
            NotFoundError: The relationship doesn't exist.
        """
        table_id = (self.info()).id
        if relationship.child_table_id != table_id:
            raise InvalidValueError(
                f"Relationship {relationship.id} belongs to child table "
                f"{relationship.child_table_id}, so call delete_relationship() on that table."
            )
        self._transport.request(
            "DELETE",
            f"/tables/{table_id}/relationship/{relationship.id}",
            parent_table_id=relationship.parent_table_id,
        )
        self._forget_fields(table_id)
        self._forget_fields(relationship.parent_table_id)

    def drop(self, *, confirm: str) -> None:
        """Delete this table and all its records.

        Args:
            confirm: The table's exact name, so a wrong variable can't drop the wrong table.

        Raises:
            InvalidValueError: `confirm` isn't the table's exact name. Nothing was sent.
        """
        info = self.info()
        if confirm != info.name:
            raise InvalidValueError(
                f"Not dropping table {info.name!r}: confirm must be its exact name."
            )
        self._transport.request("DELETE", f"/tables/{info.id}", params={"appId": self._app.id})
        self._forget_fields(info.id)
        self._cache.delete(f"tables:{self._app.id}", f"reports:{info.id}")

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._ref!r}, app={self._app.id!r})"


class Report:
    """A report on a table. Create one with `table.report("Open tasks")`."""

    def __init__(self, transport: Transport, table: Table, ref: str) -> None:
        self._transport = transport
        self._table = table
        self._ref = ref

    def info(self) -> ReportInfo:
        """Get the report's metadata, from the table's cached list of reports.

        Returns:
            A `ReportInfo`.

        Raises:
            UnknownReportError: The table has no such report, or several share the name. An ID takes
                priority over a name.
        """
        reports = self._table.reports()
        found = find_report(reports, self._ref)
        if found is None:
            # The report may be newer than the cache, so look once more before giving up.
            reports = self._table.reports(refresh=True)
            found = find_report(reports, self._ref)
        if found is None:
            raise UnknownReportError(unknown_report_message(reports, self._ref))
        return found

    def url(self, *, edit: bool = False) -> str:
        """Link to the report in Quickbase, for viewing it or changing how it's built.

        It only contacts Quickbase to look up the report's and table's IDs, as `info()` does, and
        the client's cache usually has them.

        Args:
            edit: Link to the page that changes the report's settings, rather than the report.

        Returns:
            The URL, on the client's realm.

        Raises:
            UnknownReportError: The table has no such report, or several share the name, or the
                client's policy hides it.
            UnknownTableError: The app has no such table, or the client's policy hides it.
        """
        report_id = (self.info()).id
        table_id = (self._table.info()).id
        app_id = self._table._app.id  # pyright: ignore[reportPrivateUsage]
        return report_url(self._transport.realm, app_id, table_id, report_id, edit=edit)

    def _load_target(self) -> dict[str, Any]:
        info = self.info()
        require_table_report(info)
        return {"id": info.id, "tableId": (self._table.info()).id, "type": info.type}

    def _fetch_page(self, target: dict[str, Any], top: int, skip: int) -> Page:
        # Quickbase silently ignores top and skip in the body and returns every record.
        payload = self._transport.request(
            "POST",
            f"/reports/{target['id']}/run",
            params={"tableId": target["tableId"], "top": top, "skip": skip},
            report_type=target["type"],
        )
        return parse(page_from_api, payload, "report")

    def run_page(self, *, limit: int = 100, skip: int = 0) -> Page:
        """Fetch one page of the report's records, for controlling paging yourself.

        Records hold only the report's columns, and each run adds to the report's usage count in
        Quickbase.

        Args:
            limit: The most records to return.
            skip: How many records to pass over first. Use the previous page's `next_skip`.

        Returns:
            A `Page`, whose `total` counts every record in the report.

        Raises:
            UnknownReportError: The table has no such report, or several share the name.
            UnsupportedReportError: It isn't a complete table report, such as a summary, chart, or
                ask-the-user report.
        """
        _require_positive("limit", limit)
        if skip < 0:
            raise QueryError("skip can't be negative.")
        return self._fetch_page(self._load_target(), limit, skip)

    def run(self, *, page_size: int = 1000, max_total: int | None = None) -> RecordIterator:
        """Iterate over the report's records, fetching further pages as needed.

        Records hold only the report's columns, so `record.id` raises `KeyError` if Record ID#
        isn't one of them. Each run adds to the report's usage count in Quickbase.

        Args:
            page_size: Records to request per call to Quickbase.
            max_total: Stop after this many records in all, across pages.

        Returns:
            An iterator of `Record`, which only contacts Quickbase once you start iterating.

        Raises:
            UnknownReportError: The table has no such report, or several share the name.
            UnsupportedReportError: It isn't a complete table report, such as a summary, chart, or
                ask-the-user report. Their raw results are available through `qb.request()`.
        """
        _require_positive("page_size", page_size)
        if max_total is not None:
            _require_positive("max_total", max_total)
        return RecordIterator(self._load_target, self._fetch_page, page_size, max_total)

    def summary_rows(
        self, *, page_size: int = 1000, max_total: int | None = None
    ) -> list[dict[str, Any]]:
        """Get a summary or chart report's rows, as `{column label: value}`.

        Quickbase does the calculating, using the report as it was built in the UI. Values are what
        Quickbase returns, so a chart's dates are text in the app's format, such as `01-01-2026`.
        Each run adds to the report's usage count in Quickbase.

        Args:
            page_size: Rows to request per call to Quickbase.
            max_total: Stop after this many rows in all, across pages.

        Returns:
            The rows. A summary report's total column is labeled like `Hours (tot)`.

        Raises:
            UnknownReportError: The table has no such report, or several share the name.
            UnsupportedReportError: It's a table report, which `run()` reads as records. Or
                Quickbase can't run it, such as a report that asks the user for a value, or two of
                its columns have the same label.
            InvalidResponseError: Quickbase stopped returning rows before the end.
        """
        _require_positive("page_size", page_size)
        if max_total is not None:
            _require_positive("max_total", max_total)
        info = self.info()
        require_summary_report(info)
        table_id = (self._table.info()).id
        rows: list[dict[str, Any]] = []
        while True:
            top = page_size if max_total is None else min(page_size, max_total - len(rows))
            # Quickbase silently ignores top and skip in the body and returns every row.
            payload = self._transport.request(
                "POST",
                f"/reports/{info.id}/run",
                params={"tableId": table_id, "top": top, "skip": len(rows)},
                report_type=info.type,
            )
            page, total = parse(summary_rows_from_api, payload, "report")
            rows.extend(page)
            if len(rows) >= total or len(rows) == max_total:
                return rows
            if not page:
                raise InvalidResponseError(
                    f"Quickbase returned an empty page at {len(rows)} of {total} rows."
                )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._ref!r}, table={self._table!r})"


class Operation:
    """One operation on a table, for a model's tool call. Create one with `table.operation()`."""

    def __init__(self, table: Table, spec: OperationSpec) -> None:
        self._table = table
        self._spec = spec

    @property
    def name(self) -> OperationName:
        """What the operation does, such as `query`."""
        return self._spec.name

    @property
    def schema(self) -> dict[str, Any]:
        """The JSON Schema of the arguments a model may send, for a tool's input.

        It's self-contained, with no `$ref`, and a new dictionary each time, so you can add to it.
        """
        return copy_schema(self._spec.schema)

    def run(self, arguments: Mapping[str, Any], *, scope: Expression | None = None) -> Any:
        """Check a model's arguments, and run the operation.

        Arguments the schema doesn't allow are refused before anything is sent, with a message that
        says where the mistake is, and doesn't repeat the values sent. Spellings models tend to
        write for a closed value are read as the one they mean, such as `sort` for `order_by`,
        `"order": "DESC"` for `"direction": "desc"`, and `starts with` for `starts_with`. Labels
        must match exactly.

        Args:
            arguments: The arguments from the model's tool call.
            scope: A condition your code fixes, such as the user's project, that every record must
                match. It's added to every filter. An operation that names records, such as
                `update`, first checks that they all match it. A write can't set a field the scope
                uses, and `insert` fills those fields in from the scope, which must then be only
                `==` conditions joined with `&`. A report can't take one.

        Returns:
            What the table method returns, such as a `Page` for `query`, which `tool_result()`
            takes.

        Raises:
            QueryError: An argument can't be used, a record named isn't in `scope`, a write sets
                a field the scope uses, or a delete matches more than `max_records_per_call`.
                Nothing was sent, other than the query that checks the scope or counts a delete.
            InvalidValueError: A record or value can't be written. Nothing was sent.
        """
        # Read once in the schema's spelling, so a preview and its change sent with different
        # spellings of the same arguments share a confirmation.
        arguments = self._spec.normalize(arguments)
        fixed = self._spec.context.scope
        if fixed is not None:
            scope = fixed if scope is None else fixed & scope
        call = self._spec.plan(arguments, scope)
        table = self._table
        if scope is not None and "rows" in call.arguments:
            call = scope_rows(call, scope, FieldIndex(table.fields()))
        matching: int | None = None
        if call.most is not None:
            matching = table.count(call.arguments["where"])
            if matching > call.most:
                raise QueryError(
                    f"{matching} records match, and this tool may delete at most {call.most} at "
                    "once, so nothing was deleted. Narrow the filter."
                )
        if call.record_ids and scope is not None:
            ids = list(call.record_ids)
            found = table.count(scope & F(RECORD_ID_FIELD).is_in(ids))
            if found != len(ids):
                raise QueryError(
                    "Some of these records don't exist, or aren't ones this tool may use, so "
                    "nothing was done."
                )
        if self._spec.context.require_preview:
            if call.method == "delete" and matching is None:
                matching = table.count(call.arguments["where"])
            self._check_confirmation(arguments, scope, matching)
        if call.report_id is not None:
            report = table.report(call.report_id)
            return getattr(report, call.method)(**call.arguments)
        if call.method in ("insert", "update"):
            return getattr(table, call.method)(call.arguments["rows"])
        if call.method == "preview_update":
            preview = table.preview_update(call.arguments["rows"])
            return extend(
                preview,
                ConfirmedUpdatePreview,
                confirmation=self._confirmation("update", arguments, scope, None),
            )
        result = getattr(table, call.method)(**call.arguments)
        if call.method == "count":
            return Count(result)
        if call.method == "summarize":
            rows = sort_rows(result, call.group_sort)
            end = None if call.group_limit is None else call.group_skip + call.group_limit
            return GroupPage(
                rows=tuple(rows[call.group_skip : end]), total=len(rows), skip=call.group_skip
            )
        if call.method == "query_page" and call.fields_left_out:
            return extend(
                cast(Page, result), FieldsLeftOutPage, fields_left_out=call.fields_left_out
            )
        if call.method == "delete":
            return DeletedCount(result)
        if call.method == "preview_delete":
            preview = cast(DeletePreview, result)
            limit = self._spec.context.limit
            confirmation = None
            if preview.total <= limit:
                confirmation = self._confirmation("delete", arguments, scope, preview.total)
            return extend(
                preview, DeletePreviewPage, max_deletable=limit, confirmation=confirmation
            )
        return result

    def _confirmation(
        self,
        kind: ConfirmationKind,
        arguments: Mapping[str, Any],
        scope: Expression | None,
        matching: int | None,
    ) -> str:
        table_id = (self._table.info()).id
        transport = self._table._transport  # pyright: ignore[reportPrivateUsage]
        bound = confirmation_binding(
            kind, table_id, arguments, scope, matching, transport.policy_hash
        )
        return issue_confirmation(transport.confirmation_key, kind, bound)

    def _check_confirmation(
        self, arguments: Mapping[str, Any], scope: Expression | None, matching: int | None
    ) -> None:
        kind: ConfirmationKind = "delete" if self._spec.name == "delete" else "update"
        table_id = (self._table.info()).id
        transport = self._table._transport  # pyright: ignore[reportPrivateUsage]
        bound = confirmation_binding(
            kind, table_id, arguments, scope, matching, transport.policy_hash
        )
        check_confirmation(transport.confirmation_key, kind, arguments.get("confirmation"), bound)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._spec.name!r}, table={self._table!r})"


class SchemaOperation:
    """One schema operation, for a model's tool call. Create one with `app.operation()`."""

    def __init__(
        self,
        app: App,
        name: SchemaOperationName,
        schema: dict[str, Any],
        max_changes: int,
    ) -> None:
        self._app = app
        self._name: SchemaOperationName = name
        self._schema = schema
        self._max_changes = max_changes

    @property
    def name(self) -> SchemaOperationName:
        """What the operation does, such as `preview_schema_changes`."""
        return self._name

    @property
    def schema(self) -> dict[str, Any]:
        """The JSON Schema of the arguments a model may send, for a tool's input.

        It's self-contained, with no `$ref`, and a new dictionary each time, so you can add to it.
        """
        return copy_schema(self._schema)

    def run(self, arguments: Mapping[str, Any]) -> Any:
        """Check a model's arguments, and run the operation.

        Args:
            arguments: The arguments from the model's tool call.

        Returns:
            For `describe_schema`, a list of `TableSchema`. For `preview_schema_changes`, a
            `SchemaPlan`, whose `confirmation` `apply_schema_changes` needs, unless the plan has
            blockers or changes nothing. For `apply_schema_changes`, a `SchemaChangeResult`.
            `tool_result()` takes each.

        Raises:
            InvalidValueError: An argument can't be used. Nothing was sent.
            QueryError: `apply_schema_changes` has no confirmation, or one that doesn't match these
                changes and the schema as it is now. Nothing was changed.
            SchemaChangeError: A change failed while applying. Its `result` says which were made.
        """
        if not isinstance(arguments, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise InvalidValueError("The arguments must be an object.")
        allowed = set(self._schema["properties"])
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise InvalidValueError(f"This tool doesn't take: {', '.join(unknown)}.")
        if self._name == "describe_schema":
            return self._describe(arguments.get("tables"))
        changes = changes_from_json(arguments.get("changes"), most=self._max_changes)
        offered = {item["properties"]["action"]["const"] for item in self._actions()}
        for position, change in enumerate(changes):
            if change_to_json(change)["action"] not in offered:
                actions = ", ".join(sorted(offered))
                raise InvalidValueError(
                    f"changes[{position}].action isn't one this tool offers: {actions}."
                )
        plan = Planner(self._app).plan(changes)
        transport = self._app._transport  # pyright: ignore[reportPrivateUsage]
        bound = schema_binding(
            self._app.id, canonical_changes(changes), plan.fingerprint, transport.policy_hash
        )
        key = transport.confirmation_key
        if self._name == "preview_schema_changes":
            if plan.blockers or not plan.has_changes:
                return plan
            return replace(plan, confirmation=issue_confirmation(key, "schema", bound))
        check_confirmation(key, "schema", arguments.get("confirmation"), bound)
        return apply_checked(self._app, plan)

    def _actions(self) -> list[dict[str, Any]]:
        return self._schema["properties"]["changes"]["items"]["oneOf"]

    def _describe(self, wanted: Any) -> list[TableSchema]:
        tables = self._app.tables(refresh=True)
        if wanted is not None:
            if not isinstance(wanted, list) or not all(isinstance(item, str) for item in wanted):  # pyright: ignore[reportUnknownVariableType]
                raise InvalidValueError("tables must be a list of table names.")
            names = {item.casefold() for item in cast(list[str], wanted)}
            known = {item.name.casefold() for item in tables}
            missing = sorted(names - known)
            if missing:
                raise InvalidValueError(f"There's no table {missing[0]!r}.")
            tables = [item for item in tables if item.name.casefold() in names]
        found: list[TableSchema] = []
        for info in tables:
            table = self._app.table(info.id)
            found.append(
                TableSchema(
                    info=info,
                    fields=tuple(table.fields(refresh=True)),
                    relationships=tuple(table.relationships(refresh=True)),
                )
            )
        return found

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._name!r}, app={self._app.id!r})"
