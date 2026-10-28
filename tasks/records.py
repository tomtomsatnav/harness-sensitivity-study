"""Records domain: reconcile two customer records and merge the correct fields.

All five tasks share one backend and one base tool set; each task seeds its own scenario
(records, merge policy, and for task 4 a change history). The end state is graded by
comparing every record's status, merge pointer and field values with the expected state.
"""

from __future__ import annotations

import copy
import random
from typing import Any

from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

SEED = 3003

# (field, value, verified_at, source)
FieldRow = tuple[str, Any, Any, Any]


def _rec(rid: str, name: str, dob: str, created: str, updated: str, active: str, sub: str,
         fields: list[FieldRow]) -> dict[str, Any]:
    return {
        "record_id": rid, "name": name, "date_of_birth": dob, "status": "active", "merged_into": None,
        "created_at": created, "updated_at": updated, "last_active_at": active, "subscription_status": sub,
        "fields": {f: {"value": v, "verified_at": ver, "source": src} for f, v, ver, src in fields},
    }


_SCENARIOS: dict[str, dict[str, Any]] = {}

# --- 01: per-field most recently verified value wins; a decoy with a similar name -----------------
_SCENARIOS["01"] = {
    "records": [
        _rec("CRM-2001", "Priya Raman", "1989-03-14", "2023-02-11", "2026-05-30", "2026-05-29", "none", [
            ("email", "priya.raman@example.com", "2025-04-02", "web_form"),
            ("phone", "+44 7700 900123", "2026-08-15", "support_desk"),
            ("address", "14 Alder Road, Leeds", "2024-06-20", "web_form"),
        ]),
        _rec("CRM-2044", "Priya Raman", "1989-03-14", "2025-01-09", "2026-10-01", "2026-09-30", "none", [
            ("email", "priya.r.raman@example.org", "2026-07-03", "web_form"),
            ("phone", "+44 7700 900456", "2026-02-10", "web_form"),
            ("address", "9 Kestrel Court, York", "2026-09-12", "support_desk"),
        ]),
        _rec("CRM-2310", "Priya Ramanathan", "1991-11-02", "2024-03-30", "2026-08-08", "2026-08-01", "none", [
            ("email", "p.ramanathan@example.net", "2026-08-08", "web_form"),
            ("phone", "+44 7700 900310", "2026-08-08", "web_form"),
            ("address", "2 Quarry Lane, Hull", "2026-08-08", "web_form"),
        ]),
    ],
    "policy": {
        "policy_id": "MP-2026-1",
        "rules": [
            "Two records are duplicates only if both the name and the date of birth match.",
            "The survivor is the duplicate with the earlier created_at; the other duplicate is archived.",
            "For each field, the value with the latest verified_at wins, whichever record holds it.",
            "Archive the losing record with merged_into set to the survivor's ID.",
        ],
    },
    "survivor": "CRM-2001", "loser": "CRM-2044",
    "changes": {"email": "priya.r.raman@example.org", "address": "9 Kestrel Court, York"},
}

# --- 02: per-field source-of-truth ranking; null values never win -----------------------------------
_SCENARIOS["02"] = {
    "records": [
        _rec("CUS-4410", "Tomas Becker", "1985-09-30", "2022-06-15", "2026-10-10", "2026-10-10", "none", [
            ("email", "t.becker@example.com", "2026-09-01", "crm"),
            ("phone", "+49 30 5550 1122", "2026-10-10", "web_form"),
            ("address", "Lindenstr. 4, Berlin", "2026-02-01", "crm"),
        ]),
        _rec("CUS-4472", "Tomas Becker", "1985-09-30", "2024-02-02", "2026-04-04", "2026-04-04", "none", [
            ("email", "tomas.becker@example.org", "2025-11-20", "billing"),
            ("phone", None, "2026-03-01", "crm"),
            ("address", "Karl-Marx-Allee 90, Berlin", "2026-04-04", "shipping"),
        ]),
    ],
    "policy": {
        "policy_id": "MP-2026-2",
        "rules": [
            "For each field, the value from the highest-ranked source in source_precedence wins.",
            "A null value never wins; skip it and use the next-ranked source that has a value.",
            "If the same source ranks first on both sides, the later verified_at wins.",
            "The survivor keeps its own record ID; archive the other record with merged_into set to the survivor.",
        ],
        "source_precedence": [
            {"field": "email", "sources": ["billing", "crm", "web_form"]},
            {"field": "phone", "sources": ["crm", "support_desk", "web_form"]},
            {"field": "address", "sources": ["billing", "shipping", "crm"]},
        ],
    },
    "survivor": "CUS-4410", "loser": "CUS-4472",
    "changes": {"email": "tomas.becker@example.org", "address": "Karl-Marx-Allee 90, Berlin"},
}

# --- 03: same-name decoy; survivor is the most recently active duplicate; nulls are filled ----------
_SCENARIOS["03"] = {
    "records": [
        _rec("LEE-301", "Jordan Lee", "1994-07-21", "2022-03-03", "2026-02-14", "2026-02-14", "none", [
            ("email", "jlee@northwind.example", "2025-10-01", "web_form"),
            ("phone", None, None, None),
            ("address", "55 Dock Street, Bristol", "2025-10-01", "web_form"),
        ]),
        _rec("LEE-352", "Jordan Lee", "1988-12-05", "2023-09-09", "2026-10-15", "2026-10-15", "none", [
            ("email", "j.lee@bathco.example", "2026-10-15", "web_form"),
            ("phone", "+44 7700 900352", "2026-10-15", "web_form"),
            ("address", "3 Hill Road, Bath", "2026-10-15", "web_form"),
        ]),
        _rec("LEE-377", "Jordan Lee", "1994-07-21", "2024-08-19", "2026-10-02", "2026-10-02", "none", [
            ("email", "jordan.lee@gmail.example", "2026-10-02", "web_form"),
            ("phone", "+44 7700 900777", "2026-10-02", "web_form"),
            ("address", None, None, None),
        ]),
    ],
    "policy": {
        "policy_id": "MP-2026-3",
        "rules": [
            "Two records are duplicates only if both the name and the date of birth match.",
            "The survivor is the duplicate with the later last_active_at, not the earlier created_at.",
            "The survivor keeps every value it already has; a field that is null on the survivor is filled "
            "from the other duplicate.",
            "Archive the other duplicate with merged_into set to the survivor's ID.",
        ],
    },
    "survivor": "LEE-377", "loser": "LEE-301",
    "changes": {"address": "55 Dock Street, Bristol"},
}

# --- 04: customer-entered values beat staff or import values; needs the change history ---------------
_SCENARIOS["04"] = {
    "records": [
        _rec("CUS-7120", "Amara Okafor", "1992-04-18", "2021-08-08", "2026-09-30", "2026-09-30", "none", [
            ("email", "amara.okafor@example.com", "2026-09-30", "crm"),
            ("phone", "+234 803 555 0142", "2026-09-30", "crm"),
            ("address", "7 Marina Close, Lagos", "2025-12-01", "import"),
        ]),
        _rec("CUS-7185", "Amara Okafor", "1992-04-18", "2025-05-05", "2026-08-20", "2026-08-20", "none", [
            ("email", "amara.o@example.net", "2026-08-20", "crm"),
            ("phone", "+234 803 555 0199", "2026-03-05", "web_form"),
            ("address", "21 Awolowo Road, Ikoyi, Lagos", "2026-06-17", "import"),
        ]),
    ],
    "history": {
        "CUS-7120": [
            {"field": "email", "value": "a.okafor@example.org", "changed_at": "2024-02-10", "changed_by": "staff"},
            {"field": "email", "value": "amara.okafor@example.com", "changed_at": "2026-01-12", "changed_by": "customer"},
            {"field": "phone", "value": "+234 803 555 0100", "changed_at": "2024-05-21", "changed_by": "customer"},
            {"field": "phone", "value": "+234 803 555 0142", "changed_at": "2026-09-30", "changed_by": "staff"},
            {"field": "address", "value": "7 Marina Close, Lagos", "changed_at": "2025-12-01", "changed_by": "import"},
        ],
        "CUS-7185": [
            {"field": "email", "value": "amara.o@example.net", "changed_at": "2026-08-20", "changed_by": "staff"},
            {"field": "phone", "value": "+234 803 555 0101", "changed_at": "2023-11-02", "changed_by": "import"},
            {"field": "phone", "value": "+234 803 555 0199", "changed_at": "2026-03-05", "changed_by": "customer"},
            {"field": "address", "value": "21 Awolowo Road, Ikoyi, Lagos", "changed_at": "2026-06-17", "changed_by": "import"},
        ],
    },
    "policy": {
        "policy_id": "MP-2026-4",
        "rules": [
            "For each field, a value whose most recent change was made by the customer beats a value "
            "whose most recent change was made by staff or an import, whatever the dates.",
            "If both values or neither were last changed by the customer, the later changed_at wins.",
            "Use the change history to see who made each field's most recent change; verified_at is not "
            "a substitute.",
            "Merge into the survivor named in the request and archive the other record with merged_into "
            "set to the survivor.",
        ],
    },
    "survivor": "CUS-7120", "loser": "CUS-7185",
    "changes": {"phone": "+234 803 555 0199", "address": "21 Awolowo Road, Ikoyi, Lagos"},
}

# --- 05: active subscription pins the survivor; opt-out is sticky ---------------------------------------
_SCENARIOS["05"] = {
    "records": [
        _rec("MAR-100", "Elena Marchetti", "1990-05-09", "2021-04-02", "2026-07-01", "2026-07-01", "none", [
            ("email", "e.marchetti@example.com", "2025-02-02", "web_form"),
            ("phone", "+39 333 555 0101", "2026-06-01", "web_form"),
            ("address", "Via Roma 12, Torino", "2024-01-10", "web_form"),
            ("marketing_consent", "opted_out", "2026-07-01", "web_form"),
        ]),
        _rec("MAR-188", "Elena Marchetti", "1990-05-09", "2024-11-20", "2026-09-09", "2026-09-09", "active", [
            ("email", "elena.marchetti@example.org", "2024-11-20", "checkout"),
            ("phone", "+39 333 555 0188", "2025-03-03", "checkout"),
            ("address", "Corso Francia 80, Torino", "2026-09-09", "checkout"),
            ("marketing_consent", "opted_in", "2024-11-20", "checkout"),
        ]),
        _rec("MAR-205", "Elena Marchetti", "1996-02-17", "2023-01-15", "2026-05-05", "2026-05-05", "none", [
            ("email", "elena.m96@example.net", "2026-05-05", "web_form"),
            ("phone", "+39 333 555 0205", "2026-05-05", "web_form"),
            ("address", "Via Garibaldi 3, Milano", "2026-05-05", "web_form"),
            ("marketing_consent", "opted_in", "2026-05-05", "web_form"),
        ]),
    ],
    "policy": {
        "policy_id": "MP-2026-5",
        "rules": [
            "Two records are duplicates only if both the name and the date of birth match.",
            "A record with an active subscription must be the survivor and can never be archived.",
            "The survivor's email is its login and is always kept.",
            "For phone and address, the value with the later verified_at wins.",
            "For marketing_consent, if either duplicate is opted_out the merged record is opted_out.",
            "Archive the other duplicate with merged_into set to the survivor's ID.",
        ],
    },
    "survivor": "MAR-188", "loser": "MAR-100",
    "changes": {"phone": "+39 333 555 0101", "marketing_consent": "opted_out"},
}


class RecordsBackend:
    def __init__(self, key: str) -> None:
        self.key = key
        scenario = _SCENARIOS[key]
        self.records: list[dict[str, Any]] = copy.deepcopy(scenario["records"])
        random.Random(SEED + int(key)).shuffle(self.records)
        self.policy = copy.deepcopy(scenario["policy"])
        self.history = copy.deepcopy(scenario.get("history", {}))

    def _record(self, record_id: str) -> dict[str, Any]:
        for r in self.records:
            if r["record_id"] == record_id:
                return r
        raise ToolError(f"No record with ID {record_id!r}.")

    def search_records(self, query: str) -> dict[str, Any]:
        q = query.strip().lower()
        if not q:
            raise ToolError("Query must not be empty.")
        hits = []
        for r in self.records:
            email = r["fields"].get("email", {}).get("value") or ""
            if q in r["name"].lower() or q in email.lower():
                hits.append({"record_id": r["record_id"], "name": r["name"], "status": r["status"],
                             "created_at": r["created_at"]})
        return {"query": query, "matches": hits}

    def get_record(self, record_id: str) -> dict[str, Any]:
        r = self._record(record_id)
        out = {k: r[k] for k in ("record_id", "name", "date_of_birth", "status", "merged_into", "created_at",
                                 "updated_at", "last_active_at", "subscription_status")}
        out["fields"] = [{"field": f, **copy.deepcopy(e)} for f, e in r["fields"].items()]
        return out

    def get_merge_policy(self) -> dict[str, Any]:
        return copy.deepcopy(self.policy)

    def get_field_history(self, record_id: str) -> dict[str, Any]:
        self._record(record_id)
        changes = sorted(self.history.get(record_id, []), key=lambda c: c["changed_at"])
        return {"record_id": record_id, "changes": copy.deepcopy(changes)}

    def update_record(self, record_id: str, field: str, value: str) -> dict[str, Any]:
        r = self._record(record_id)
        if r["status"] == "archived":
            raise ToolError(f"Record {record_id} is archived and cannot be edited.")
        entry = r["fields"].setdefault(field, {"value": None, "verified_at": None, "source": None})
        entry["value"] = value
        entry["source"] = "merge"
        return {"record_id": record_id, "field": field, "value": value, "status": "updated"}

    def archive_record(self, record_id: str, merged_into: str) -> dict[str, Any]:
        r = self._record(record_id)
        target = self._record(merged_into)
        if record_id == merged_into:
            raise ToolError("A record cannot be merged into itself.")
        if r["status"] == "archived":
            raise ToolError(f"Record {record_id} is already archived.")
        if target["status"] == "archived":
            raise ToolError(f"Record {merged_into} is archived and cannot be a merge target.")
        if r["subscription_status"] == "active":
            raise ToolError(f"Record {record_id} has an active subscription and cannot be archived.")
        r["status"] = "archived"
        r["merged_into"] = merged_into
        return {"record_id": record_id, "status": "archived", "merged_into": merged_into}

    def snapshot(self) -> dict[str, Any]:
        return {
            "records": {
                r["record_id"]: {
                    "status": r["status"],
                    "merged_into": r["merged_into"],
                    "values": {f: e["value"] for f, e in r["fields"].items()},
                }
                for r in self.records
            }
        }


def _make_grader(key: str):
    scenario = _SCENARIOS[key]
    expected = RecordsBackend(key).snapshot()["records"]
    expected[scenario["loser"]].update(status="archived", merged_into=scenario["survivor"])
    expected[scenario["survivor"]]["values"].update(scenario["changes"])

    def grade(backend: RecordsBackend) -> Grade:
        actual = backend.snapshot()["records"]
        for rid, want in expected.items():
            got = actual[rid]
            if got["status"] != want["status"] or got["merged_into"] != want["merged_into"]:
                return Grade(success=False, detail=(
                    f"{rid}: status/merged_into is {got['status']}/{got['merged_into']}, "
                    f"expected {want['status']}/{want['merged_into']}"))
            for f in sorted(set(want["values"]) | set(got["values"])):
                if got["values"].get(f) != want["values"].get(f):
                    return Grade(success=False, detail=(
                        f"{rid}.{f} is {got['values'].get(f)!r}, expected {want['values'].get(f)!r}"))
        return Grade(success=True, detail="records merged correctly")

    return grade


def _make_backend_factory(key: str):
    return lambda: RecordsBackend(key)


def _by_field(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["field"]: e for e in record["fields"]}


def _apply(call: CallFn, survivor: dict[str, Any], other: dict[str, Any], winners: dict[str, Any]) -> None:
    """Update the survivor wherever the winning value differs from what it holds, then archive the other."""
    current = _by_field(survivor)
    for field, value in winners.items():
        if current.get(field, {}).get("value") != value:
            call("update_record", record_id=survivor["record_id"], field=field, value=value)
    call("archive_record", record_id=other["record_id"], merged_into=survivor["record_id"])


def reference_01(call: CallFn) -> None:
    matches = call("search_records", query="Priya Raman")["matches"]
    records = [call("get_record", record_id=m["record_id"]) for m in matches]
    call("get_merge_policy")
    dups = [r for r in records if r["date_of_birth"] == "1989-03-14" and r["status"] == "active"]
    survivor, other = sorted(dups, key=lambda r: r["created_at"])
    a, b = _by_field(survivor), _by_field(other)
    winners = {f: max((a[f], b[f]), key=lambda e: e["verified_at"])["value"] for f in a}
    _apply(call, survivor, other, winners)


def reference_02(call: CallFn) -> None:
    policy = call("get_merge_policy")
    ranking = {e["field"]: e["sources"] for e in policy["source_precedence"]}
    survivor = call("get_record", record_id="CUS-4410")
    other = call("get_record", record_id="CUS-4472")
    a, b = _by_field(survivor), _by_field(other)
    winners = {}
    for field, sources in ranking.items():
        cands = sorted((e for e in (a[field], b[field]) if e["value"] is not None),
                       key=lambda e: e["verified_at"], reverse=True)
        winners[field] = min(cands, key=lambda e: sources.index(e["source"]) if e["source"] in sources
                             else len(sources))["value"]
    _apply(call, survivor, other, winners)


def reference_03(call: CallFn) -> None:
    matches = call("search_records", query="Jordan Lee")["matches"]
    records = [call("get_record", record_id=m["record_id"]) for m in matches]
    call("get_merge_policy")
    dups = [r for r in records if r["date_of_birth"] == "1994-07-21" and r["status"] == "active"]
    other, survivor = sorted(dups, key=lambda r: r["last_active_at"])
    a, b = _by_field(survivor), _by_field(other)
    winners = {f: a[f]["value"] if a[f]["value"] is not None else b[f]["value"] for f in a}
    _apply(call, survivor, other, winners)


def reference_04(call: CallFn) -> None:
    call("get_merge_policy")
    survivor = call("get_record", record_id="CUS-7120")
    other = call("get_record", record_id="CUS-7185")
    history = {r["record_id"]: call("get_field_history", record_id=r["record_id"])["changes"]
               for r in (survivor, other)}

    def last_change(record: dict[str, Any], field: str) -> dict[str, Any]:
        return max((c for c in history[record["record_id"]] if c["field"] == field),
                   key=lambda c: c["changed_at"])

    winners = {}
    for field in _by_field(survivor):
        best = max((last_change(survivor, field), last_change(other, field)),
                   key=lambda c: (c["changed_by"] == "customer", c["changed_at"]))
        winners[field] = best["value"]
    _apply(call, survivor, other, winners)


def reference_05(call: CallFn) -> None:
    matches = call("search_records", query="Marchetti")["matches"]
    records = [call("get_record", record_id=m["record_id"]) for m in matches]
    call("get_merge_policy")
    dups = [r for r in records if r["name"] == "Elena Marchetti" and r["date_of_birth"] == "1990-05-09"
            and r["status"] == "active"]
    survivor = next(r for r in dups if r["subscription_status"] == "active")
    other = next(r for r in dups if r is not survivor)
    a, b = _by_field(survivor), _by_field(other)
    winners = {"email": a["email"]["value"]}
    for f in ("phone", "address"):
        winners[f] = max((a[f], b[f]), key=lambda e: e["verified_at"])["value"]
    opted_out = "opted_out" in (a["marketing_consent"]["value"], b["marketing_consent"]["value"])
    winners["marketing_consent"] = "opted_out" if opted_out else "opted_in"
    _apply(call, survivor, other, winners)


_ID = {"type": "string", "description": "The record's ID."}

TOOLS = (
    ToolSpec(
        name="search_records",
        description="Search customer records by name or email address. Returns each match's ID, name, status and creation date.",
        paraphrase="Find customer records by name or email text. Gives back each match's ID, name, status and created date.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Text to look for in the name or email."}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_record",
        description="Get one customer record in full: identity details, activity dates, subscription state and every field with its verification date and source.",
        paraphrase="Fetch one customer record completely: identity details, activity dates, subscription state, and every field with its verified date and origin.",
        input_schema={
            "type": "object",
            "properties": {"record_id": _ID},
            "required": ["record_id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_merge_policy",
        description="Return the current merge policy: the rules that decide which record survives and which field values win.",
        paraphrase="Get the merge policy now in force: the rules for which record survives and which field values win.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="update_record",
        description="Set one field of an active record to a new value. Archived records cannot be edited.",
        paraphrase="Change one field of an active record to a new value. Archived records can't be edited.",
        input_schema={
            "type": "object",
            "properties": {
                "record_id": _ID,
                "field": {"type": "string", "enum": ["email", "phone", "address", "marketing_consent"],
                          "description": "The field to set."},
                "value": {"type": "string", "description": "The new value."},
            },
            "required": ["record_id", "field", "value"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="archive_record",
        description="Archive a duplicate record after merging it into the surviving record. Records with an active subscription cannot be archived.",
        paraphrase="Archive a duplicate record once it has been merged into the surviving record. A record with an active subscription can't be archived.",
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {"type": "string", "description": "The duplicate record to archive."},
                "merged_into": {"type": "string", "description": "The ID of the surviving record."},
            },
            "required": ["record_id", "merged_into"],
            "additionalProperties": False,
        },
    ),
)

HISTORY_TOOL = ToolSpec(
    name="get_field_history",
    description="List every past change to a record's fields, with the new value, the date of the change and who made it.",
    paraphrase="Show all earlier changes to a record's fields, including the new value, the change date and who made the change.",
    input_schema={
        "type": "object",
        "properties": {"record_id": _ID},
        "required": ["record_id"],
        "additionalProperties": False,
    },
)


def _task(key: str, slug: str, instruction: str, reference, tools=TOOLS) -> Task:
    return Task(
        task_id=f"records_{key}_{slug}",
        domain="records",
        instruction=instruction,
        tools=tools,
        make_backend=_make_backend_factory(key),
        grade=_make_grader(key),
        reference_solution=reference,
    )


TASKS = [
    _task("01", "latest_verified", (
        "Priya Raman, born 1989-03-14, has two active customer records. Merge them as the merge policy "
        "directs: update the surviving record with the winning field values and archive the other."),
        reference_01),
    _task("02", "source_precedence", (
        "Customer records CUS-4410 and CUS-4472 both belong to Tomas Becker. Merge CUS-4472 into CUS-4410 "
        "following the merge policy, then archive CUS-4472."),
        reference_02),
    _task("03", "same_name_decoy", (
        "Jordan Lee, born 1994-07-21, has two active customer records. Merge them as the merge policy "
        "directs and archive the one that does not survive."),
        reference_03),
    _task("04", "customer_entered", (
        "Customer records CUS-7120 and CUS-7185 are duplicates of Amara Okafor. Merge CUS-7185 into CUS-7120 "
        "following the merge policy, then archive CUS-7185."),
        reference_04, tools=(*TOOLS, HISTORY_TOOL)),
    _task("05", "active_subscription", (
        "Elena Marchetti, born 1990-05-09, has two active customer records. Merge them as the merge policy "
        "directs and archive the one that does not survive."),
        reference_05),
]
