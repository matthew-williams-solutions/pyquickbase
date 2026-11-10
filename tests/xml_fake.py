"""A fake of Quickbase's XML endpoint, with real responses captured on 2026-11-09 and sanitized."""

from typing import Any

import httpx

APP = "bq1234567"
TOKEN = "b3xyz_fake_token"
REALM = "acme"


def envelope(action: str, body: str = "", code: int = 0, text: str = "No error") -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8" ?>\n<qdbapi>\n'
        f"\t<action>{action}</action>\n\t<errcode>{code}</errcode>\n\t<errtext>{text}</errtext>\n"
        f"{body}\n</qdbapi>\n"
    ).encode()


def failure(action: str, code: int, text: str, detail: str | None = None) -> bytes:
    extra = "" if detail is None else f"\t<errdetail>{detail}</errdetail>"
    return envelope(action, extra, code, text)


GRANTED = """\t<databases>
\t\t<dbinfo>
\t\t\t<dbname>Field Jobs</dbname>
\t\t\t<dbid>bq1234567</dbid>
\t\t\t<ancestorappid></ancestorappid>
\t\t\t<oldestancestorappid></oldestancestorappid>
\t\t</dbinfo>
\t\t<dbinfo>
\t\t\t<dbname>Field Jobs (Staging)</dbname>
\t\t\t<dbid>bq7654321</dbid>
\t\t\t<ancestorappid>bq1234567</ancestorappid>
\t\t\t<oldestancestorappid>bq1234567</oldestancestorappid>
\t\t</dbinfo>
\t</databases>"""
USER = """\t<user id="58390212.abcd">
\t\t<firstName>Jordan</firstName>
\t\t<lastName>Rivera</lastName>
\t\t<login>jordan@example.com</login>
\t\t<email>jordan@example.com</email>
\t\t<screenName></screenName>
\t\t<isVerified>1</isVerified>
\t\t<externalAuth>0</externalAuth>
\t</user>"""
ROLES = """\t<roles>
\t\t<role id="10">
\t\t\t<name>Viewer</name>
\t\t\t<access id="3">Basic Access</access>
\t\t</role>
\t\t<role id="12">
\t\t\t<name>Administrator</name>
\t\t\t<access id="1">Administrator</access>
\t\t</role>
\t</roles>"""
SCHEMA = """    <time_zone>(UTC-08:00) Pacific Time (US &amp; Canada)</time_zone>
    <date_format>MM-DD-YYYY</date_format>
    <table>
      <name>Field Jobs</name>
      <desc>Crews, sites, and tasks</desc>
      <original>
        <table_id>bq1234567</table_id>
        <app_id>bq1234567</app_id>
      </original>
      <chdbids>
        <chdbid name="_dbid_tasks">bqtasks12</chdbid>
      </chdbids>
      <fields>
      </fields>
    </table>"""
MEMBER_ROLE = """\t<user id="58390212.abcd">
\t\t<name>Jordan Rivera</name>
\t\t<roles>
\t\t\t<role id="10">
\t\t\t\t<name>Viewer</name>
\t\t\t\t<access id="3">Basic Access</access>
\t\t\t\t<member type="user">Jordan Rivera</member>
\t\t\t</role>
\t\t</roles>
\t</user>"""
USER_ROLES = """\t<users>
\t\t<user type="user" id="58390212.abcd">
\t\t\t<name>Jordan Rivera</name>
\t\t\t<lastAccess>1793727139259</lastAccess>
\t\t\t<lastAccessAppLocal>11-09-2026 01:12 PM</lastAccessAppLocal>
\t\t\t<firstName>Jordan</firstName>
\t\t\t<lastName>Rivera</lastName>
\t\t\t<roles>
\t\t\t\t<role id="12">
\t\t\t\t\t<name>Administrator</name>
\t\t\t\t\t<access id="1">Administrator</access>
\t\t\t\t</role>
\t\t\t</roles>
\t\t</user>
\t\t<user type="group" id="9.bcde">
\t\t\t<name>Crew Leads</name>
\t\t\t<lastAccess>0</lastAccess>
\t\t\t<roles></roles>
\t\t</user>
\t</users>"""

RESPONSES = {
    "API_GrantedDBs": GRANTED,
    "API_GetUserInfo": USER,
    "API_GetRoleInfo": ROLES,
    "API_GetSchema": SCHEMA,
    "API_GetUserRole": MEMBER_ROLE,
    "API_UserRoles": USER_ROLES,
    "API_ListDBPages": '<pages><page id="6" type="Text">a.html</page></pages>',
    "API_GetDBPage": "<pagebody>plain</pagebody>",
    "API_AddReplaceDBPage": "<pageID>6</pageID>",
    "API_SetKeyField": "",
}


class FakeXml:
    """Answers the XML calls, and the JSON API with `json_status` for anything else."""

    def __init__(
        self,
        *,
        responses: dict[str, str] | None = None,
        json_status: int = 403,
        json_body: Any = None,
    ) -> None:
        self.sent: list[httpx.Request] = []
        self.responses = {**RESPONSES, **(responses or {})}
        self.json_status = json_status
        self.json_body = json_body if json_body is not None else {"message": "Access denied"}

    def actions(self) -> list[str | None]:
        return [item.headers.get("QUICKBASE-ACTION") for item in self.sent]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        if not request.url.path.startswith("/db/"):
            return httpx.Response(self.json_status, json=self.json_body)
        action = request.headers["QUICKBASE-ACTION"]
        return httpx.Response(
            200, content=envelope(action, self.responses[action]), headers={"tid": "abc"}
        )
