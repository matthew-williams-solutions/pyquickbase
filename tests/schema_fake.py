"""A fake app that keeps its tables, fields, and relationships, for schema plans and operations."""

import json
import re
from typing import Any

import httpx

from tests.xml_fake import envelope, failure

APP = "bq1234567"
TASKS = "bqtasks"
PROJECTS = "bqprojects"


class FakeApp:
    """Keeps tables, fields, and relationships, and answers like Quickbase."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any]] = []
        self.tables: dict[str, dict[str, Any]] = {
            TASKS: {"id": TASKS, "name": "Tasks", "description": "", "keyFieldId": 3},
            PROJECTS: {"id": PROJECTS, "name": "Projects", "description": "", "keyFieldId": 3},
        }
        self.fields: dict[str, list[dict[str, Any]]] = {
            TASKS: [
                *self.built_in(),
                {"id": 6, "label": "Hours", "fieldType": "numeric"},
                {"id": 7, "label": "Status", "fieldType": "text-multiple-choice",
                 "properties": {"choices": ["Open", "Done"]}},
                {"id": 8, "label": "Code", "fieldType": "text"},
                {"id": 9, "label": "Project", "fieldType": "numeric",
                 "properties": {"foreignKey": True}},
                {"id": 10, "label": "Double", "fieldType": "numeric", "mode": "formula",
                 "properties": {"formula": "[Hours] * 2"}},
                {"id": 11, "label": "Project - Name", "fieldType": "text", "mode": "lookup",
                 "properties": {"lookupReferenceFieldId": 9, "lookupTargetFieldId": 6}},
            ],
            PROJECTS: [
                *self.built_in(),
                {"id": 6, "label": "Name", "fieldType": "text"},
                {"id": 7, "label": "Total Hours", "fieldType": "numeric", "mode": "summary",
                 "properties": {"summaryReferenceFieldId": 9, "summaryTargetFieldId": 6,
                                "summaryFunction": "SUM"}},
            ],
        }  # fmt: skip
        self.relationships: list[dict[str, Any]] = [
            {"id": 9, "parentTableId": PROJECTS, "childTableId": TASKS,
             "foreignKeyField": {"id": 9, "label": "Project"}},
        ]  # fmt: skip
        self.counts: dict[str, int] = {}
        # What a field holds, by table and field ID, for a query that reads its values.
        self.values: dict[tuple[str, int], list[Any]] = {}
        # Fields that hold the same value twice, which Quickbase won't make unique.
        self.duplicates: set[tuple[str, int]] = set()
        # The fields a relationship added to its parent, by relationship ID.
        self.parent_links: dict[int, list[int]] = {}
        # Where each field is used, as Quickbase counts it, by table and field ID.
        self.usage: dict[tuple[str, int], dict[str, int]] = {}
        self.wheres: list[str] = []
        self.fail: str | None = None
        self.fail_where: str | None = None
        self.next_id = 100

    def bind(self, *rules: Any, **options: Any) -> Any:
        """A policy bound to this app as it is now."""
        from .policy_helpers import bind

        tables = {
            table_id: (info["name"], self.fields[table_id])
            for table_id, info in self.tables.items()
        }
        return bind(*rules, tables=tables, relationships=self.relationships, app_id=APP, **options)

    @staticmethod
    def built_in() -> list[dict[str, Any]]:
        built_in = [("Date Created", "timestamp"), ("Date Modified", "timestamp"),
                    ("Record ID#", "recordid"), ("Record Owner", "user"),
                    ("Last Modified By", "user")]  # fmt: skip
        return [
            {"id": n + 1, "label": label, "fieldType": kind}
            for n, (label, kind) in enumerate(built_in)
        ]

    def writes(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _ in self.sent if method != "GET"
                and path != "/records/query"]  # fmt: skip

    def _xml(self, request: httpx.Request, path: str) -> httpx.Response:
        table_id = path.removeprefix("/db/")
        self.sent.append((request.method, path, request.content))
        if self.fail and f"POST {path}".startswith(self.fail):
            return httpx.Response(403, content=failure("API_SetKeyField", 3, "Denied"))
        found = re.search(rb"<fid>(\d+)</fid>", request.content)
        assert found is not None
        self.tables[table_id]["keyFieldId"] = int(found[1])
        return httpx.Response(200, content=envelope("API_SetKeyField"))

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    @staticmethod
    def _refuse(message: str, description: str = "") -> httpx.Response:
        return httpx.Response(400, json={"message": message, "description": description})

    def _name_taken(self, name: str, own: str | None = None) -> bool:
        """Quickbase compares table names ignoring case and surrounding spaces."""
        wanted = name.strip().casefold()
        return any(
            info["name"].strip().casefold() == wanted
            for table_id, info in self.tables.items()
            if table_id != own
        )

    def _label_taken(self, table: str, label: str, own: int | None = None) -> bool:
        """Quickbase compares labels ignoring case, and keeps their spaces."""
        return any(
            item["label"].casefold() == label.casefold() and item["id"] != own
            for item in self.fields[table]
        )

    def _query(self, body: dict[str, Any]) -> httpx.Response:
        where = body.get("where", "")
        self.wheres.append(where)
        if where == self.fail_where:
            return httpx.Response(400, json={"message": "Bad Request", "description": "No."})
        read = re.fullmatch(r"\{(\d+)\.XEX\.''\}", where)
        held = None if read is None else self.values.get((body["from"], int(read[1])))
        if read is None or held is None:
            total = self.counts.get(f"{body['from']} {where}", 0)
            meta = {"totalRecords": total, "numRecords": 0, "skip": 0}
            return httpx.Response(200, json={"data": [], "fields": [], "metadata": meta})
        field_id = int(read[1])
        options = body.get("options", {})
        skip = options.get("skip", 0)
        page = held[skip : skip + options.get("top", len(held))]
        item = next(entry for entry in self.fields[body["from"]] if entry["id"] == field_id)
        columns = [{"id": field_id, "label": item["label"], "type": item["fieldType"]}]
        columns.append({"id": 3, "label": "Record ID#", "type": "recordid"})
        rows = [
            {str(field_id): {"value": value}, "3": {"value": skip + n + 1}}
            for n, value in enumerate(page)
        ]
        meta = {"totalRecords": len(held), "numRecords": len(rows), "skip": skip}
        return httpx.Response(200, json={"data": rows, "fields": columns, "metadata": meta})

    def _create_field(self, table: str, body: dict[str, Any]) -> httpx.Response:
        if "unique" in body or "required" in body:
            return self._refuse("Bad Request", "Set these with POST /fields/{id}.")
        if self._label_taken(table, body["label"]):
            return self._refuse("Bad Request", "A field with this label exists.")
        kind = "timestamp" if body["fieldType"] == "datetime" else body["fieldType"]
        properties = body.get("properties", {})
        refused = {"text-multi-line", "multiuser", "timestamp"}
        if "formula" in properties and body["fieldType"] in refused:
            return self._refuse("Bad Request", "[formula] property is not supported")
        if "choices" in properties and kind not in ("text-multiple-choice", "multitext"):
            return self._refuse("Bad Request", "Extraneous key 'properties.choices'.")
        created = {"id": self.new_id(), "label": body["label"], "fieldType": kind,
                   "properties": properties}  # fmt: skip
        if "formula" in properties:
            created["mode"] = "formula"
        self.fields[table].append(created)
        return httpx.Response(200, json=created)

    def _update_field(self, table: str, field_id: int, body: dict[str, Any]) -> httpx.Response:
        found = next(item for item in self.fields[table] if item["id"] == field_id)
        if "label" in body and self._label_taken(table, body["label"], own=field_id):
            return self._refuse("Bad Request", "A field with this label exists.")
        new = body.get("properties", {})
        if "formula" in new and found.get("mode") != "formula":
            return self._refuse("Bad Request", "Field must be of formula mode.")
        if body.get("required") and self.counts.get(f"{table} {{{field_id}.EX.''}}", 0):
            return self._refuse("Bad Request", "field contains null values")
        if body.get("unique") and (table, field_id) in self.duplicates:
            return self._refuse("Bad Request", "field contains non-unique values")
        properties = {**found.get("properties", {}), **new}
        found.update({key: value for key, value in body.items() if key != "properties"})
        found["properties"] = properties
        return httpx.Response(200, json=found)

    def _delete_fields(self, table: str, ids: set[int]) -> httpx.Response:
        built_in = sorted(ids & {1, 2, 3, 4, 5})
        deleted = sorted(ids - set(built_in))
        self.fields[table] = [item for item in self.fields[table] if item["id"] not in deleted]
        errors = [f"Field {item} can't be deleted." for item in built_in]
        return httpx.Response(200, json={"deletedFieldIds": deleted, "errors": errors})

    def _create_relationship(self, child: str, body: dict[str, Any]) -> httpx.Response:
        parent = body["parentTableId"]
        key = {"id": self.new_id(), "label": body["foreignKeyField"]["label"],
               "fieldType": "numeric", "properties": {"foreignKey": True}}  # fmt: skip
        self.fields[child].append(key)
        created = {"id": key["id"], "parentTableId": parent,
                   "childTableId": child, "foreignKeyField": key}  # fmt: skip
        self.relationships.append(created)
        # Quickbase also gives the parent a link to the records and a formula to add one.
        name = self.tables[child]["name"]
        added = [
            {"id": self.new_id(), "label": f"{name} records", "fieldType": "dblink"},
            {"id": self.new_id(), "label": f"Add {name}", "fieldType": "url", "mode": "formula",
             "properties": {"formula": "URLRoot() & 'a=API_GenAddRecordForm'"}},
        ]  # fmt: skip
        self.fields[parent] += added
        self.parent_links[key["id"]] = [item["id"] for item in added]
        return httpx.Response(200, json=created)

    def _add_to_relationship(
        self, child: str, relationship_id: int, body: dict[str, Any]
    ) -> httpx.Response:
        found = next(item for item in self.relationships if item["id"] == relationship_id)
        parent = found["parentTableId"]
        key = next(item for item in self.fields[child] if item["id"] == relationship_id)
        for target in body.get("lookupFieldIds", []):
            shown = next(item for item in self.fields[parent] if item["id"] == target)
            if shown["fieldType"] in ("file", "dblink", "predecessor"):
                return self._refuse("Bad Request", "These field ids can not be lookup fields.")
            kind = "text" if shown["fieldType"] == "text-multiple-choice" else shown["fieldType"]
            self.fields[child].append(
                {"id": self.new_id(), "label": f"{key['label']} - {shown['label']}",
                 "fieldType": kind, "mode": "lookup",
                 "properties": {"lookupReferenceFieldId": relationship_id,
                                "lookupTargetFieldId": target}}
            )  # fmt: skip
        for summary in body.get("summaryFields", []):
            self.fields[parent].append(
                {"id": self.new_id(), "label": summary["label"], "fieldType": "numeric",
                 "mode": "summary", "properties": {
                     "summaryReferenceFieldId": relationship_id,
                     "summaryTargetFieldId": summary.get("summaryFid", 0),
                     "summaryFunction": summary["accumulationType"],
                     **({"summaryQuery": summary["where"]} if "where" in summary else {})}}
            )  # fmt: skip
        return httpx.Response(200, json=found)

    def _delete_relationship(self, child: str, relationship_id: int) -> httpx.Response:
        found = next((r for r in self.relationships if r["id"] == relationship_id), None)
        if found is None:
            return httpx.Response(404, json={"message": "Parent table not found."})
        parent = found["parentTableId"]
        self.relationships.remove(found)
        # The foreign key stays. What was built on it goes, on both tables.
        linked = set(self.parent_links.pop(relationship_id, ()))
        self.fields[child] = [
            item for item in self.fields[child]
            if item.get("properties", {}).get("lookupReferenceFieldId") != relationship_id
        ]  # fmt: skip
        self.fields[parent] = [
            item for item in self.fields[parent]
            if item["id"] not in linked
            and item.get("properties", {}).get("summaryReferenceFieldId") != relationship_id
        ]  # fmt: skip
        return httpx.Response(200, json={"relationshipId": relationship_id})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if path.startswith("/db/"):
            return self._xml(request, path)
        body = json.loads(request.content) if request.content else None
        self.sent.append((request.method, path, body))
        if self.fail and f"{request.method} {path}".startswith(self.fail):
            return httpx.Response(400, json={"message": "Bad Request", "description": "No."})
        table = request.url.params.get("tableId", "")
        method = request.method
        if (method, path) == ("GET", "/tables"):
            return httpx.Response(200, json=list(self.tables.values()))
        if (method, path) == ("GET", "/fields"):
            return httpx.Response(200, json=self.fields[table])
        if method == "GET" and path.startswith("/fields/usage"):
            wanted = path.removeprefix("/fields/usage").strip("/")
            skip = int(request.url.params.get("skip", 0))
            if not wanted and skip and skip >= len(self.fields[table]):
                return httpx.Response(500, json={"message": "Internal error"})
            rows = [
                {
                    "field": {"id": item["id"], "name": item["label"], "type": item["fieldType"]},
                    "usage": {
                        kind: {"count": count}
                        for kind, count in self.usage.get((table, item["id"]), {}).items()
                    },
                }
                for item in self.fields[table]
                if not wanted or item["id"] == int(wanted)
            ]
            return httpx.Response(200, json=rows[skip:])
        if method == "GET" and path.endswith("/relationships"):
            child = path.split("/")[2]
            rows = [item for item in self.relationships if item["childTableId"] == child]
            meta = {"totalRelationships": len(rows)}
            return httpx.Response(200, json={"relationships": rows, "metadata": meta})
        if (method, path) == ("POST", "/records/query"):
            return self._query(body)
        if (method, path) == ("POST", "/tables"):
            if not request.url.params.get("appId"):
                return self._refuse("Bad Request", "Required query 'appId' not found")
            if self._name_taken(body["name"]):
                return self._refuse("Invalid input", "Table name exists")
            table_id = f"bqnew{self.new_id()}"
            self.tables[table_id] = {"id": table_id, "description": "", "keyFieldId": 3, **body}
            self.fields[table_id] = self.built_in()
            return httpx.Response(200, json=self.tables[table_id])
        if method == "POST" and path.startswith("/tables/") and path.count("/") == 2:
            table_id = path.split("/")[2]
            if not request.url.params.get("appId"):
                return self._refuse("Bad Request", "Required query 'appId' not found")
            if "name" in body and self._name_taken(body["name"], own=table_id):
                return self._refuse("Invalid input", "Table name exists")
            self.tables[table_id].update(body)
            return httpx.Response(200, json=self.tables[table_id])
        if method == "DELETE" and path.startswith("/tables/") and path.count("/") == 2:
            if not request.url.params.get("appId"):
                return self._refuse("Bad Request", "Required query 'appId' not found")
            table_id = path.split("/")[2]
            del self.tables[table_id]
            return httpx.Response(200, json={"deletedTableId": table_id})
        if (method, path) == ("POST", "/fields"):
            return self._create_field(table, body)
        if method == "POST" and path.startswith("/fields/"):
            return self._update_field(table, int(path.rsplit("/", 1)[1]), body)
        if (method, path) == ("DELETE", "/fields"):
            return self._delete_fields(table, set(body["fieldIds"]))
        if method == "POST" and path.endswith("/relationship"):
            return self._create_relationship(path.split("/")[2], body)
        if method == "POST" and "/relationship/" in path:
            return self._add_to_relationship(path.split("/")[2], int(path.rsplit("/", 1)[1]), body)
        if method == "DELETE" and "/relationship/" in path:
            return self._delete_relationship(path.split("/")[2], int(path.rsplit("/", 1)[1]))
        return httpx.Response(404, json={"message": "Not found", "description": path})
