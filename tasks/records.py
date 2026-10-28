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
         fields: list[FieldRow], status: str = "active", merged_into: str | None = None) -> dict[str, Any]:
    return {
        "record_id": rid, "name": name, "date_of_birth": dob, "status": status, "merged_into": merged_into,
        "created_at": created, "updated_at": updated, "last_active_at": active, "subscription_status": sub,
        "fields": {f: {"value": v, "verified_at": ver, "source": src} for f, v, ver, src in fields},
    }


# --- filler: CRM metadata that no merge rule refers to, and look-alike records ------------------------
# Everything here is derived from fixed seeds, so every run sees the same data.

_REGIONS = ["emea_north", "emea_south", "emea_central", "amer_east", "apac_west"]
_LOCALES = ["en_GB", "de_DE", "en_NG", "it_IT", "en_US"]
_CREATORS = ["import_job", "web_signup", "agent:kboyle", "agent:mreyes", "agent:dnkemelu"]
_SYSTEMS = ["salesforce", "hubspot", "legacy_crm", "shopify", "zendesk"]
_OWNERS = ["k.boyle", "m.reyes", "d.nkemelu", "s.oduya", "unassigned"]
_TAGS = ["newsletter", "vip", "gdpr_request", "trade_show_2025", "wholesale", "returning", "price_sensitive"]
_NOTES = [
    "Prefers email contact.", "Asked for invoice copies in March.", "Do not call before 10:00.",
    "Bounced email on last campaign.", "Opened a support case about delivery.", "No notes.",
    "Requested a paper statement.", "Attended the 2025 trade show.",
]
_STREETS = ["Station Road", "Mill Lane", "Church Street", "Park Avenue", "Harbour View", "Orchard Close",
            "Victoria Terrace", "Kingsway", "Bridge Street", "Elm Grove"]
_DOMAINS = ["example.com", "example.org", "example.net", "mail.example"]
_SOURCES = ["web_form", "support_desk", "crm", "billing", "import", "checkout", "shipping"]


def _decorate(record: dict[str, Any]) -> dict[str, Any]:
    """Attach the metadata columns every record carries. Deterministic in the record ID."""
    rng = random.Random(f"{SEED}:{record['record_id']}")
    record["internal_ref"] = f"{rng.choice(['SF', 'HS', 'LG'])}-{rng.randint(10000, 99999)}"
    record["region"] = rng.choice(_REGIONS)
    record["locale"] = rng.choice(_LOCALES)
    record["created_by"] = rng.choice(_CREATORS)
    record["source_system"] = rng.choice(_SYSTEMS)
    record["account_owner"] = rng.choice(_OWNERS)
    record["tags"] = sorted(rng.sample(_TAGS, rng.randint(0, 3)))
    record["notes"] = rng.choice(_NOTES)
    return record


def _date(rng: random.Random, lo: int, hi: int) -> str:
    return f"{rng.randint(lo, hi)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _distractors(key: str, prefix: str, numbers: list[int], variants: list[tuple[str, int]],
                 taken: set[tuple[str, str]], city_pool: list[str], phone_prefix: str,
                 with_consent: bool = False) -> list[dict[str, Any]]:
    """Look-alike records: same or similar names with other dates of birth, some already archived.

    `taken` holds the (name, date of birth) pairs of the scenario's own records; no distractor ever
    reuses one, so the merge rules match exactly the pair the task is about.
    """
    rng = random.Random(SEED * 10 + int(key))
    ids = iter(rng.sample(numbers, len(numbers)))
    seen = set(taken)
    out: list[dict[str, Any]] = []
    for name, count in variants:
        for _ in range(count):
            while True:
                dob = _date(rng, 1958, 2004)
                if (name, dob) not in seen:
                    break
            seen.add((name, dob))
            created = _date(rng, 2019, 2024)
            updated = _date(rng, 2025, 2026)
            active = _date(rng, 2025, 2026)
            first, last = name.split(" ", 1)
            handle = f"{first.lower()}.{last.lower().replace(' ', '').replace('-', '')}{rng.randint(1, 99)}"
            fields: list[FieldRow] = [
                ("email", f"{handle}@{rng.choice(_DOMAINS)}", _date(rng, 2024, 2026), rng.choice(_SOURCES)),
                ("phone", f"{phone_prefix}{rng.randint(10000, 99999)}", _date(rng, 2024, 2026), rng.choice(_SOURCES)),
                ("address", f"{rng.randint(1, 120)} {rng.choice(_STREETS)}, {rng.choice(city_pool)}",
                 _date(rng, 2024, 2026), rng.choice(_SOURCES)),
            ]
            if with_consent:
                fields.append(("marketing_consent", rng.choice(["opted_in", "opted_out"]), _date(rng, 2024, 2026),
                               rng.choice(_SOURCES)))
            # Exact-name look-alikes never hold an active subscription, so that rule reads cleanly.
            sub = "none" if name == variants[0][0] else rng.choice(["none", "none", "lapsed", "active"])
            rid = f"{prefix}{next(ids)}"
            out.append(_rec(rid, name, dob, created, updated, active, sub, fields))
    # Two look-alikes were themselves merged earlier: an archived twin pointing at the active record.
    for twin_of in rng.sample(out[:variants[0][1]], 2):
        rid = f"{prefix}{next(ids)}"
        fields = [(f, e["value"], e["verified_at"], e["source"]) for f, e in twin_of["fields"].items()]
        out.append(_rec(rid, twin_of["name"], twin_of["date_of_birth"], _date(rng, 2019, 2022),
                        twin_of["created_at"], twin_of["created_at"], "none", fields,
                        status="archived", merged_into=twin_of["record_id"]))
    return out


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
            "The survivor is the duplicate with the earlier creation date; the other duplicate is archived.",
            "For each field, the value with the most recently verified value wins, whichever record holds it.",
            "Archive the losing record with merged_into set to the survivor's ID.",
        ],
    },
    "survivor": "CRM-2001", "loser": "CRM-2044",
    "changes": {"email": "priya.r.raman@example.org", "address": "9 Kestrel Court, York"},
    "distractors": _distractors(
        "01", "CRM-", list(range(2050, 2300)),
        [("Priya Raman", 9), ("Priya Ramanathan", 3), ("Priya Ramanan", 2), ("Priya Ramani", 2),
         ("Supriya Raman", 2), ("Priya Raman-Iyer", 1), ("Priya Ramanujan", 1)],
        {("Priya Raman", "1989-03-14"), ("Priya Ramanathan", "1991-11-02")},
        ["Leeds", "York", "Hull", "Sheffield", "Bradford", "Harrogate"], "+44 7700 9"),
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
            "For each field, the value from the highest-ranked source in this policy's source ranking wins.",
            "A null value never wins; skip it and use the next-ranked source that has a value.",
            "If the same source ranks first on both sides, the more recently verified value wins.",
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
    "distractors": _distractors(
        "02", "CUS-", [n for n in range(4400, 4500) if n not in (4410, 4472)],
        [("Tomas Becker", 7), ("Thomas Becker", 4), ("Anna Becker", 2), ("Jonas Becker", 2), ("Lena Becker", 2),
         ("Tomas Beckert", 1), ("Tom Becker", 1)],
        {("Tomas Becker", "1985-09-30")},
        ["Berlin", "Hamburg", "Potsdam", "Leipzig", "Dresden"], "+49 30 5550 "),
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
            "The survivor is the duplicate with the later last-active date, not the earlier creation date.",
            "The survivor keeps every value it already has; a field that is null on the survivor is filled "
            "from the other duplicate.",
            "Archive the other duplicate with merged_into set to the survivor's ID.",
        ],
    },
    "survivor": "LEE-377", "loser": "LEE-301",
    "changes": {"address": "55 Dock Street, Bristol"},
    "distractors": _distractors(
        "03", "LEE-", [n for n in range(300, 400) if n not in (301, 352, 377)],
        [("Jordan Lee", 9), ("Jordan Lee-Walker", 2), ("Jordan Leech", 2), ("Jordan Lees", 2), ("Jordan Leeds", 1),
         ("Jordan Leeson", 1), ("Jordan Leeming", 1)],
        {("Jordan Lee", "1994-07-21"), ("Jordan Lee", "1988-12-05")},
        ["Bristol", "Bath", "Cardiff", "Exeter", "Swindon"], "+44 7700 9"),
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
    # Only each field's most recent change decides the merge; the older rows are the record's ordinary
    # audit trail and never outrank it.
    "history": {
        "CUS-7120": [
            {"field": "email", "value": "amara.okafor@legacy.example", "changed_at": "2021-08-08", "changed_by": "import"},
            {"field": "email", "value": "aokafor@example.com", "changed_at": "2022-06-14", "changed_by": "staff"},
            {"field": "email", "value": "amara.ok@example.com", "changed_at": "2023-03-30", "changed_by": "customer"},
            {"field": "email", "value": "a.okafor@example.org", "changed_at": "2024-02-10", "changed_by": "staff"},
            {"field": "email", "value": "amara.okafor@example.com", "changed_at": "2026-01-12", "changed_by": "customer"},
            {"field": "phone", "value": "+234 803 555 0007", "changed_at": "2021-08-08", "changed_by": "import"},
            {"field": "phone", "value": "+234 803 555 0088", "changed_at": "2022-09-19", "changed_by": "staff"},
            {"field": "phone", "value": "+234 803 555 0100", "changed_at": "2024-05-21", "changed_by": "customer"},
            {"field": "phone", "value": "+234 803 555 0120", "changed_at": "2025-07-02", "changed_by": "staff"},
            {"field": "phone", "value": "+234 803 555 0142", "changed_at": "2026-09-30", "changed_by": "staff"},
            {"field": "address", "value": "3 Broad Street, Lagos", "changed_at": "2021-08-08", "changed_by": "import"},
            {"field": "address", "value": "12 Adeola Odeku Street, Lagos", "changed_at": "2022-11-05", "changed_by": "customer"},
            {"field": "address", "value": "12 Adeola Odeku St, Victoria Island, Lagos", "changed_at": "2024-08-14", "changed_by": "staff"},
            {"field": "address", "value": "7 Marina Close, Lagos", "changed_at": "2025-12-01", "changed_by": "import"},
        ],
        "CUS-7185": [
            {"field": "email", "value": "amara.okafor92@example.net", "changed_at": "2025-05-05", "changed_by": "import"},
            {"field": "email", "value": "amara.o92@example.net", "changed_at": "2025-09-18", "changed_by": "customer"},
            {"field": "email", "value": "amara.o@example.net", "changed_at": "2026-08-20", "changed_by": "staff"},
            {"field": "phone", "value": "+234 803 555 0101", "changed_at": "2023-11-02", "changed_by": "import"},
            {"field": "phone", "value": "+234 803 555 0150", "changed_at": "2025-06-30", "changed_by": "staff"},
            {"field": "phone", "value": "+234 803 555 0199", "changed_at": "2026-03-05", "changed_by": "customer"},
            {"field": "address", "value": "5 Kofo Abayomi Street, Lagos", "changed_at": "2025-05-05", "changed_by": "import"},
            {"field": "address", "value": "5 Kofo Abayomi St, Victoria Island, Lagos", "changed_at": "2025-10-22", "changed_by": "staff"},
            {"field": "address", "value": "21 Awolowo Road, Ikoyi, Lagos", "changed_at": "2026-06-17", "changed_by": "import"},
        ],
    },
    "policy": {
        "policy_id": "MP-2026-4",
        "rules": [
            "For each field, a value whose most recent change was made by the customer beats a value "
            "whose most recent change was made by staff or an import, whatever the dates.",
            "If both values or neither were last changed by the customer, the later change date wins.",
            "Use the change history to see who made each field's most recent change; the verification date is not "
            "a substitute.",
            "Merge into the survivor named in the request and archive the other record with merged_into "
            "set to the survivor.",
        ],
    },
    "survivor": "CUS-7120", "loser": "CUS-7185",
    "changes": {"phone": "+234 803 555 0199", "address": "21 Awolowo Road, Ikoyi, Lagos"},
    "distractors": _distractors(
        "04", "CUS-", [n for n in range(7100, 7200) if n not in (7120, 7185)],
        [("Amara Okafor", 7), ("Amaka Okafor", 2), ("Chidi Okafor", 2), ("Ngozi Okafor", 2), ("Emeka Okafor", 2),
         ("Amara Okafor-Eze", 1), ("Amarachi Okafor", 1)],
        {("Amara Okafor", "1992-04-18")},
        ["Lagos", "Abuja", "Ibadan", "Enugu", "Port Harcourt"], "+234 803 555 "),
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
            "For phone and address, the value with the more recently verified value wins.",
            "For marketing_consent, if either duplicate is opted_out the merged record is opted_out.",
            "Archive the other duplicate with merged_into set to the survivor's ID.",
        ],
    },
    "survivor": "MAR-188", "loser": "MAR-100",
    "changes": {"phone": "+39 333 555 0101", "marketing_consent": "opted_out"},
    "distractors": _distractors(
        "05", "MAR-", [n for n in range(101, 300) if n not in (188, 205)],
        [("Elena Marchetti", 8), ("Marco Marchetti", 2), ("Giulia Marchetti", 2), ("Luca Marchetti", 2),
         ("Andrea Marchetti", 2), ("Elena Marchetti-Rossi", 1), ("Elena Marchettini", 1)],
        {("Elena Marchetti", "1990-05-09"), ("Elena Marchetti", "1996-02-17")},
        ["Torino", "Milano", "Genova", "Bologna", "Verona"], "+39 333 555 ", with_consent=True),
}


# --- h1: three-way merge; the active subscription pins the newest record as survivor ------------------
# Survivor NH-533 was created last, so "earliest created" is the trap. Phone comes from NH-410 (most
# recently verified; NH-472's is null), address from NH-472 (NH-533's is null), consent is opted_out
# because NH-472 opted out, and the survivor's email stays. NH-601 shares the name but not the birth date.
_SCENARIOS["h1"] = {
    "records": [
        _rec("NH-410", "Noor Haddad", "1987-06-23", "2020-02-14", "2026-09-01", "2026-09-01", "none", [
            ("email", "noor.haddad@example.com", "2025-01-10", "web_form"),
            ("phone", "+44 7700 900410", "2026-09-01", "support_desk"),
            ("address", "4 Lime Walk, Oxford", "2024-03-03", "web_form"),
            ("marketing_consent", "opted_in", "2025-06-06", "web_form"),
        ]),
        _rec("NH-472", "Noor Haddad", "1987-06-23", "2023-07-30", "2026-08-08", "2026-08-08", "lapsed", [
            ("email", "n.haddad@example.org", "2026-08-08", "billing"),
            ("phone", None, None, None),
            ("address", "17 Bell Lane, Reading", "2026-07-07", "shipping"),
            ("marketing_consent", "opted_out", "2026-02-02", "web_form"),
        ]),
        _rec("NH-533", "Noor Haddad", "1987-06-23", "2025-03-12", "2026-09-30", "2026-09-30", "active", [
            ("email", "noor.h@fernhill.example", "2025-03-12", "checkout"),
            ("phone", "+44 7700 900533", "2025-03-12", "checkout"),
            ("address", None, None, None),
            ("marketing_consent", "opted_in", "2026-09-30", "checkout"),
        ]),
        _rec("NH-601", "Noor Haddad", "1991-02-02", "2024-06-01", "2026-10-05", "2026-10-05", "none", [
            ("email", "noor.haddad91@example.net", "2026-10-05", "web_form"),
            ("phone", "+44 7700 900601", "2026-10-05", "web_form"),
            ("address", "8 Castle Row, Windsor", "2026-10-05", "web_form"),
            ("marketing_consent", "opted_in", "2026-10-05", "web_form"),
        ]),
    ],
    "policy": {
        "policy_id": "MP-2026-6",
        "rules": [
            "Two records are duplicates only if both the name and the date of birth match; a customer may have "
            "more than two duplicates.",
            "A record with an active subscription must be the survivor and can never be archived. If no duplicate "
            "has an active subscription, the survivor is the duplicate with the earliest creation date.",
            "The survivor's email is its login and is always kept.",
            "For phone and address, the most recently verified value across all the duplicates wins, whichever "
            "record holds it; a null value never wins.",
            "For marketing_consent, if any duplicate is opted_out the merged record is opted_out.",
            "Archive every other duplicate with merged_into set to the survivor's ID.",
        ],
    },
    "survivor": "NH-533", "losers": ["NH-410", "NH-472"],
    "changes": {"phone": "+44 7700 900410", "address": "17 Bell Lane, Reading", "marketing_consent": "opted_out"},
    "distractors": _distractors(
        "11", "NH-", [n for n in range(400, 700) if n not in (410, 472, 533, 601)],
        [("Noor Haddad", 8), ("Nour Haddad", 2), ("Noora Haddad", 2), ("Omar Haddad", 2), ("Noor Haddadi", 1),
         ("Noor Haddad-Khan", 1), ("Noor Hadad", 1)],
        {("Noor Haddad", "1987-06-23"), ("Noor Haddad", "1991-02-02")},
        ["Oxford", "Reading", "Windsor", "Swindon", "Slough"], "+44 7700 9", with_consent=True),
}

# --- h2: source ranking, overridden per field by whoever made the most recent change -------------------
# Email: ranking favours billing (9277) but 9210's last change was by the customer, so it stays. Phone:
# both last changed by staff, so the ranking decides (support_desk beats web_form) and 9277's wins; the
# older customer-made change in 9210's history is a trap. Address: both last changed by the customer,
# so the later change (9277) wins even though the ranking favours billing (9210).
_SCENARIOS["h2"] = {
    "records": [
        _rec("CUS-9210", "Felix Moreau", "1983-11-02", "2021-05-05", "2026-03-03", "2026-03-03", "none", [
            ("email", "felix.moreau@example.com", "2026-03-03", "crm"),
            ("phone", "+33 6 55 01 02 10", "2026-01-15", "web_form"),
            ("address", "12 Rue Lepic, Paris", "2025-11-11", "billing"),
        ]),
        _rec("CUS-9277", "Felix Moreau", "1983-11-02", "2024-09-09", "2026-08-08", "2026-08-08", "none", [
            ("email", "f.moreau@example.org", "2026-06-06", "billing"),
            ("phone", "+33 6 55 01 02 77", "2026-05-05", "support_desk"),
            ("address", "3 Allee des Tilleuls, Lyon", "2026-08-08", "shipping"),
        ]),
    ],
    "history": {
        "CUS-9210": [
            {"field": "email", "value": "felix.moreau@legacy.example", "changed_at": "2021-05-05", "changed_by": "import"},
            {"field": "email", "value": "fmoreau@example.com", "changed_at": "2023-02-02", "changed_by": "staff"},
            {"field": "email", "value": "felix.moreau@example.com", "changed_at": "2026-03-03", "changed_by": "customer"},
            {"field": "phone", "value": "+33 6 55 01 00 01", "changed_at": "2021-05-05", "changed_by": "import"},
            {"field": "phone", "value": "+33 6 55 01 02 09", "changed_at": "2024-04-04", "changed_by": "customer"},
            {"field": "phone", "value": "+33 6 55 01 02 10", "changed_at": "2026-01-15", "changed_by": "staff"},
            {"field": "address", "value": "1 Rue de Rivoli, Paris", "changed_at": "2021-05-05", "changed_by": "import"},
            {"field": "address", "value": "12 Rue Lepic, Paris", "changed_at": "2025-11-11", "changed_by": "customer"},
        ],
        "CUS-9277": [
            {"field": "email", "value": "felix.m@example.org", "changed_at": "2024-09-09", "changed_by": "import"},
            {"field": "email", "value": "f.moreau@example.org", "changed_at": "2026-06-06", "changed_by": "staff"},
            {"field": "phone", "value": "+33 6 55 01 02 70", "changed_at": "2024-09-09", "changed_by": "import"},
            {"field": "phone", "value": "+33 6 55 01 02 77", "changed_at": "2026-05-05", "changed_by": "staff"},
            {"field": "address", "value": "5 Place Bellecour, Lyon", "changed_at": "2024-09-09", "changed_by": "import"},
            {"field": "address", "value": "7 Place Bellecour, Lyon", "changed_at": "2026-02-02", "changed_by": "staff"},
            {"field": "address", "value": "3 Allee des Tilleuls, Lyon", "changed_at": "2026-08-08", "changed_by": "customer"},
        ],
    },
    "policy": {
        "policy_id": "MP-2026-7",
        "rules": [
            "Unless a later rule says otherwise, for each field the value from the highest-ranked source in this "
            "policy's source ranking wins.",
            "A null value never wins; skip it and use the next-ranked source that has a value.",
            "A value whose most recent change in the change history was made by the customer beats a value whose "
            "most recent change was made by staff or an import, whatever the source ranking says. Only each "
            "field's most recent change counts; earlier changes do not.",
            "If both values were last changed by the customer, the later change date wins.",
            "Merge into the survivor named in the request and archive the other record with merged_into set to "
            "the survivor.",
        ],
        "source_precedence": [
            {"field": "email", "sources": ["billing", "crm", "web_form"]},
            {"field": "phone", "sources": ["crm", "support_desk", "web_form"]},
            {"field": "address", "sources": ["billing", "shipping", "crm"]},
        ],
    },
    "survivor": "CUS-9210", "loser": "CUS-9277",
    "changes": {"phone": "+33 6 55 01 02 77", "address": "3 Allee des Tilleuls, Lyon"},
    "distractors": _distractors(
        "12", "CUS-", [n for n in range(9200, 9300) if n not in (9210, 9277)],
        [("Felix Moreau", 7), ("Felix Moreaux", 2), ("Felicie Moreau", 2), ("Felix Morel", 2), ("Jules Moreau", 2),
         ("Felix Moreau-Dubois", 1)],
        {("Felix Moreau", "1983-11-02")},
        ["Paris", "Lyon", "Lille", "Nantes", "Bordeaux"], "+33 6 55 01 "),
}


_POLICY_META = {
    "version": 4, "effective_from": "2026-01-01", "owner": "data_governance", "last_reviewed_at": "2026-09-15",
    "applies_to": ["customer_records", "lead_records"],
    "notes": "Exceptions need written approval from the data-governance owner.",
}

_CHANNELS = {"customer": ["web_portal", "mobile_app"], "staff": ["agent_console", "phone_call"], "import": ["batch_import"]}


def _policy_with_meta(policy: dict[str, Any]) -> dict[str, Any]:
    """The policy as the tool returns it: identifier and rules first, housekeeping mixed in after."""
    out = {"policy_id": policy["policy_id"], "version": _POLICY_META["version"],
           "effective_from": _POLICY_META["effective_from"], "rules": policy["rules"]}
    if "source_precedence" in policy:
        out["source_precedence"] = policy["source_precedence"]
    out.update({k: _POLICY_META[k] for k in ("owner", "applies_to", "last_reviewed_at", "notes")})
    return out


class RecordsBackend:
    def __init__(self, key: str) -> None:
        self.key = key
        scenario = _SCENARIOS[key]
        self.records: list[dict[str, Any]] = [
            _decorate(r) for r in copy.deepcopy(scenario["records"] + scenario["distractors"])]
        random.Random(SEED + int(key) if key.isdigit() else f"{SEED}:{key}").shuffle(self.records)
        self.policy = _policy_with_meta(copy.deepcopy(scenario["policy"]))
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
            address = r["fields"].get("address", {}).get("value")
            if q in r["name"].lower() or q in email.lower():
                hits.append({
                    "record_id": r["record_id"], "name": r["name"], "date_of_birth": r["date_of_birth"],
                    "email": email or None, "city": address.rsplit(", ", 1)[-1] if address else None,
                    "status": r["status"], "region": r["region"], "created_at": r["created_at"],
                    "last_active_at": r["last_active_at"], "source_system": r["source_system"],
                    "internal_ref": r["internal_ref"],
                })
        return {"query": query, "matches": hits}

    def get_record(self, record_id: str) -> dict[str, Any]:
        r = self._record(record_id)
        out = {k: r[k] for k in (
            "record_id", "internal_ref", "name", "date_of_birth", "status", "merged_into", "region", "locale",
            "created_at", "created_by", "updated_at", "last_active_at", "source_system", "subscription_status",
            "account_owner", "tags", "notes")}
        out["fields"] = [{"field": f, "entry_id": f"{record_id}-{f.upper()}", "value": e["value"],
                          "verified_at": e["verified_at"], "source": e["source"], "last_synced_at": r["updated_at"]}
                         for f, e in r["fields"].items()]
        return out

    def get_merge_policy(self) -> dict[str, Any]:
        return copy.deepcopy(self.policy)

    def get_field_history(self, record_id: str) -> dict[str, Any]:
        self._record(record_id)
        changes = sorted(self.history.get(record_id, []), key=lambda c: (c["changed_at"], c["field"]))
        previous: dict[str, Any] = {}
        rows = []
        for i, c in enumerate(changes, 1):
            rng = random.Random(f"{record_id}:{c['field']}:{c['changed_at']}")
            rows.append({"change_id": f"CH-{record_id[-4:]}-{i:02d}", "field": c["field"],
                         "previous_value": previous.get(c["field"]), "value": c["value"],
                         "changed_at": c["changed_at"], "changed_by": c["changed_by"],
                         "channel": rng.choice(_CHANNELS[c["changed_by"]])})
            previous[c["field"]] = c["value"]
        return {"record_id": record_id, "changes": rows}

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


def _losers(scenario: dict[str, Any]) -> list[str]:
    """The records a scenario archives: one for the study tasks, possibly several for the hard ones."""
    return scenario["losers"] if "losers" in scenario else [scenario["loser"]]


def _make_grader(key: str):
    scenario = _SCENARIOS[key]
    expected = RecordsBackend(key).snapshot()["records"]
    for loser in _losers(scenario):
        expected[loser].update(status="archived", merged_into=scenario["survivor"])
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


def _candidates(call: CallFn, query: str, name: str, dob: str) -> list[dict[str, Any]]:
    """Fetch the active records whose search summary matches the name and date of birth exactly."""
    matches = call("search_records", query=query)["matches"]
    return [call("get_record", record_id=m["record_id"]) for m in matches
            if m["name"] == name and m["date_of_birth"] == dob and m["status"] == "active"]


def reference_01(call: CallFn) -> None:
    dups = _candidates(call, "Priya Raman", "Priya Raman", "1989-03-14")
    call("get_merge_policy")
    survivor, other = sorted(dups, key=lambda r: r["created_at"])
    a, b = _by_field(survivor), _by_field(other)
    winners = {f: max((a[f], b[f]), key=lambda e: e["verified_at"])["value"] for f in a}
    _apply(call, survivor, other, winners)


def reference_02(call: CallFn) -> None:
    call("search_records", query="Becker")
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
    dups = _candidates(call, "Jordan Lee", "Jordan Lee", "1994-07-21")
    call("get_merge_policy")
    other, survivor = sorted(dups, key=lambda r: r["last_active_at"])
    a, b = _by_field(survivor), _by_field(other)
    winners = {f: a[f]["value"] if a[f]["value"] is not None else b[f]["value"] for f in a}
    _apply(call, survivor, other, winners)


def reference_04(call: CallFn) -> None:
    call("search_records", query="Okafor")
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
    dups = _candidates(call, "Marchetti", "Elena Marchetti", "1990-05-09")
    call("get_merge_policy")
    survivor = next(r for r in dups if r["subscription_status"] == "active")
    other = next(r for r in dups if r is not survivor)
    a, b = _by_field(survivor), _by_field(other)
    winners = {"email": a["email"]["value"]}
    for f in ("phone", "address"):
        winners[f] = max((a[f], b[f]), key=lambda e: e["verified_at"])["value"]
    opted_out = "opted_out" in (a["marketing_consent"]["value"], b["marketing_consent"]["value"])
    winners["marketing_consent"] = "opted_out" if opted_out else "opted_in"
    _apply(call, survivor, other, winners)


def reference_h1(call: CallFn) -> None:
    dups = _candidates(call, "Noor Haddad", "Noor Haddad", "1987-06-23")
    call("get_merge_policy")
    with_sub = [r for r in dups if r["subscription_status"] == "active"]
    survivor = with_sub[0] if with_sub else min(dups, key=lambda r: r["created_at"])
    fields = [_by_field(r) for r in dups]
    winners = {"email": _by_field(survivor)["email"]["value"]}
    for f in ("phone", "address"):
        held = [fl[f] for fl in fields if fl[f]["value"] is not None]
        winners[f] = max(held, key=lambda e: e["verified_at"])["value"]
    opted_out = any(fl["marketing_consent"]["value"] == "opted_out" for fl in fields)
    winners["marketing_consent"] = "opted_out" if opted_out else "opted_in"
    current = _by_field(survivor)
    for f, value in winners.items():
        if current[f]["value"] != value:
            call("update_record", record_id=survivor["record_id"], field=f, value=value)
    for other in dups:
        if other is not survivor:
            call("archive_record", record_id=other["record_id"], merged_into=survivor["record_id"])


def reference_h2(call: CallFn) -> None:
    policy = call("get_merge_policy")
    ranking = {e["field"]: e["sources"] for e in policy["source_precedence"]}
    survivor = call("get_record", record_id="CUS-9210")
    other = call("get_record", record_id="CUS-9277")
    history = {r["record_id"]: call("get_field_history", record_id=r["record_id"])["changes"]
               for r in (survivor, other)}
    a, b = _by_field(survivor), _by_field(other)

    def last_change(record: dict[str, Any], field: str) -> dict[str, Any]:
        return max((c for c in history[record["record_id"]] if c["field"] == field), key=lambda c: c["changed_at"])

    winners = {}
    for field, sources in ranking.items():
        ca, cb = last_change(survivor, field), last_change(other, field)
        by_customer = [(e, c) for e, c in ((a[field], ca), (b[field], cb))
                       if c["changed_by"] == "customer" and e["value"] is not None]
        if by_customer:
            winners[field] = max(by_customer, key=lambda ec: ec[1]["changed_at"])[0]["value"]
        else:
            held = [e for e in (a[field], b[field]) if e["value"] is not None]
            winners[field] = min(held, key=lambda e: sources.index(e["source"]) if e["source"] in sources
                                 else len(sources))["value"]
    _apply(call, survivor, other, winners)


_ID = {"type": "string", "description": "The record's ID."}

TOOLS = (
    ToolSpec(
        name="search_records",
        description="Search customer records by name or email address. Returns a summary of each match: ID, name, date of birth, email, city, status and key dates.",
        paraphrase="Find customer records by name or email text. Gives back a summary per match: ID, name, birth date, email, city, status and main dates.",
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

HARD_TASKS = [
    _task("h1", "three_way_merge", (
        "Noor Haddad, born 1987-06-23, has three active customer records. Merge them as the merge policy "
        "directs: update the surviving record with the winning field values and archive the others."),
        reference_h1),
    _task("h2", "history_overrides_ranking", (
        "Customer records CUS-9210 and CUS-9277 both belong to Felix Moreau. Merge CUS-9277 into CUS-9210 "
        "following the merge policy, then archive CUS-9277."),
        reference_h2, tools=(*TOOLS, HISTORY_TOOL)),
]
