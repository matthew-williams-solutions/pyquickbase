"""A fake app that keeps its tables, fields, and relationships, for schema plans and operations."""

import json
from typing import Any

import httpx

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
        self.wheres: list[str] = []
        self.fail: str | None = None
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
        names = ["Date Created", "Date Modified", "Record ID#", "Record Owner", "Last Modified By"]
        return [{"id": n + 1, "label": name, "fieldType": "text"} for n, name in enumerate(names)]

    def writes(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path, _ in self.sent if method != "GET"
                and path != "/records/query"]  # fmt: skip

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
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
        if method == "GET" and path.endswith("/relationships"):
            child = path.split("/")[2]
            rows = [item for item in self.relationships if item["childTableId"] == child]
            meta = {"totalRelationships": len(rows)}
            return httpx.Response(200, json={"relationships": rows, "metadata": meta})
        if (method, path) == ("POST", "/records/query"):
            self.wheres.append(body.get("where", ""))
            total = self.counts.get(f"{body['from']} {body.get('where', '')}", 0)
            meta = {"totalRecords": total, "numRecords": 0, "skip": 0}
            return httpx.Response(200, json={"data": [], "fields": [], "metadata": meta})
        if (method, path) == ("POST", "/tables"):
            table_id = f"bqnew{self.new_id()}"
            self.tables[table_id] = {"id": table_id, "description": "", "keyFieldId": 3, **body}
            self.fields[table_id] = self.built_in()
            return httpx.Response(200, json=self.tables[table_id])
        if method == "POST" and path.startswith("/tables/") and path.count("/") == 2:
            table_id = path.split("/")[2]
            self.tables[table_id].update(body)
            return httpx.Response(200, json=self.tables[table_id])
        if method == "DELETE" and path.startswith("/tables/") and path.count("/") == 2:
            del self.tables[path.split("/")[2]]
            return httpx.Response(200, json={"deletedTableId": path})
        if (method, path) == ("POST", "/fields"):
            created = {"id": self.new_id(), "label": body["label"], "fieldType": body["fieldType"],
                       "properties": body.get("properties", {})}  # fmt: skip
            if "formula" in created["properties"]:
                created["mode"] = "formula"
            self.fields[table].append(created)
            return httpx.Response(200, json=created)
        if method == "POST" and path.startswith("/fields/"):
            field_id = int(path.rsplit("/", 1)[1])
            found = next(item for item in self.fields[table] if item["id"] == field_id)
            properties = {**found.get("properties", {}), **body.pop("properties", {})}
            found.update(body, properties=properties)
            return httpx.Response(200, json=found)
        if (method, path) == ("DELETE", "/fields"):
            ids = set(body["fieldIds"])
            self.fields[table] = [item for item in self.fields[table] if item["id"] not in ids]
            return httpx.Response(200, json={"deletedFieldIds": sorted(ids), "errors": []})
        if method == "POST" and path.endswith("/relationship"):
            child = path.split("/")[2]
            key = {"id": self.new_id(), "label": body["foreignKeyField"]["label"],
                   "fieldType": "numeric", "properties": {"foreignKey": True}}  # fmt: skip
            self.fields[child].append(key)
            created = {"id": key["id"], "parentTableId": body["parentTableId"],
                       "childTableId": child, "foreignKeyField": key}  # fmt: skip
            self.relationships.append(created)
            return httpx.Response(200, json=created)
        if method == "POST" and "/relationship/" in path:
            child, relationship_id = path.split("/")[2], int(path.rsplit("/", 1)[1])
            found = next(item for item in self.relationships if item["id"] == relationship_id)
            for target in body.get("lookupFieldIds", []):
                self.fields[child].append(
                    {"id": self.new_id(), "label": f"Lookup {target}", "fieldType": "text",
                     "mode": "lookup", "properties": {"lookupReferenceFieldId": relationship_id,
                                                      "lookupTargetFieldId": target}}
                )  # fmt: skip
            for summary in body.get("summaryFields", []):
                self.fields[found["parentTableId"]].append(
                    {"id": self.new_id(), "label": summary["label"], "fieldType": "numeric",
                     "mode": "summary", "properties": {
                         "summaryReferenceFieldId": relationship_id,
                         "summaryTargetFieldId": summary.get("summaryFid", 0),
                         "summaryFunction": summary["accumulationType"],
                         **({"summaryQuery": summary["where"]} if "where" in summary
                            else {})}}
                )  # fmt: skip
            return httpx.Response(200, json=found)
        if method == "DELETE" and "/relationship/" in path:
            relationship_id = int(path.rsplit("/", 1)[1])
            self.relationships = [r for r in self.relationships if r["id"] != relationship_id]
            return httpx.Response(200, json={"relationshipId": relationship_id})
        return httpx.Response(404, json={"message": "Not found", "description": path})
