"""Scheduling domain: book a slot that satisfies three constraints.

All five tasks share one backend (a one-day office calendar) and a pool of six tools; each task
offers the subset it needs. Meeting times are in headquarters time (UTC-5). Each person's
calendar is reported in that person's local time together with their UTC offset.

Each scenario is built so that exactly one (start, room) pair satisfies every constraint.
`valid_options` computes that set from the scenario's rules; the graders and the tests use it.
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

DATE = "2026-11-03"
HQ_UTC_OFFSET = -5
SLOT_MINUTES = 30


def _m(hhmm: str) -> int:
    """'HH:MM' to minutes after midnight."""
    return int(hhmm[0:2]) * 60 + int(hhmm[3:5])


def _ts(minutes: int) -> str:
    return f"{DATE}T{minutes // 60:02d}:{minutes % 60:02d}"


def _from_ts(ts: str) -> int:
    return int(ts[11:13]) * 60 + int(ts[14:16])


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


# ---------------------------------------------------------------- scenario data

def _room(room_id: str, name: str, building: str, floor: int, wing: str, capacity: int, extension: int,
          equipment: list[str], catering: bool, accessible: bool, contact: str) -> dict[str, Any]:
    """A room as list_rooms reports it. Only capacity, floor and equipment ever matter to a booking rule."""
    return {"room_id": room_id, "name": name, "building": building, "floor": floor, "wing": wing,
            "capacity": capacity, "phone_extension": extension, "equipment": equipment, "catering": catering,
            "accessible": accessible, "booking_contact": contact}


# The six original rooms plus twelve more. Every added room fails at least one of each scenario's room
# requirements (capacity, equipment, floor) or is booked through the attendees' free windows; the
# uniqueness test in tests/test_tasks.py checks the result.
_ROOMS = {r["room_id"]: r for r in (
    _room("R-EMBER", "Ember", "North", 0, "east", 6, 4010, ["video_conference", "projector"], True, True, "facilities-north"),
    _room("R-NOOK", "Nook", "North", 0, "west", 2, 4011, [], False, True, "facilities-north"),
    _room("R-BIRCH", "Birch", "North", 1, "east", 4, 4110, ["video_conference", "whiteboard"], False, True, "facilities-north"),
    _room("R-DUNE", "Dune", "North", 1, "west", 6, 4111, ["whiteboard"], True, True, "facilities-north"),
    _room("R-HAVEN", "Haven", "North", 1, "west", 3, 4112, ["video_conference"], False, False, "facilities-north"),
    _room("R-ATLAS", "Atlas", "North", 2, "east", 8, 4210, ["video_conference", "projector"], True, True, "facilities-north"),
    _room("R-FERN", "Fern", "North", 2, "west", 4, 4211, ["whiteboard"], False, True, "facilities-north"),
    _room("R-CEDAR", "Cedar", "North", 3, "east", 12, 4310, ["projector", "whiteboard"], True, True, "facilities-north"),
    _room("R-GROVE", "Grove", "North", 3, "west", 10, 4311, ["video_conference", "projector", "whiteboard"], True, True, "facilities-north"),
    _room("R-MAPLE", "Maple", "South", 0, "east", 6, 5010, ["projector"], True, True, "facilities-south"),
    _room("R-OPAL", "Opal", "South", 0, "west", 2, 5011, ["video_conference"], False, True, "facilities-south"),
    _room("R-JUNO", "Juno", "South", 1, "east", 2, 5110, ["video_conference"], False, False, "facilities-south"),
    _room("R-PINE", "Pine", "South", 1, "west", 4, 5111, ["projector"], False, True, "facilities-south"),
    _room("R-IRIS", "Iris", "South", 2, "east", 1, 5210, [], False, False, "facilities-south"),
    _room("R-LARK", "Lark", "South", 2, "east", 5, 5211, ["whiteboard", "projector"], True, True, "facilities-south"),
    _room("R-QUAY", "Quay", "South", 2, "west", 8, 5212, ["projector"], True, True, "facilities-south"),
    _room("R-KITE", "Kite", "South", 3, "east", 16, 5310, ["projector"], True, True, "facilities-south"),
    _room("R-REEF", "Reef", "South", 3, "west", 3, 5311, [], False, False, "facilities-south"),
)}

_ALL_ROOMS = tuple(_ROOMS)

# (name, email, department, IANA time zone)
_PEOPLE = {
    "P-ANA": ("Ana Ruiz", "ana.ruiz@example.com", "product", "America/New_York"),
    "P-BEN": ("Ben Okafor", "ben.okafor@example.com", "engineering", "America/New_York"),
    "P-CHL": ("Chloe Marsh", "chloe.marsh@example.com", "design", "America/New_York"),
    "P-DEV": ("Dev Patel", "dev.patel@example.com", "engineering", "America/New_York"),
    "P-ELI": ("Eli Novak", "eli.novak@example.com", "finance", "America/New_York"),
    "P-PRI": ("Priya Nair", "priya.nair@example.com", "product", "America/New_York"),
    "P-TOM": ("Tom Hale", "tom.hale@example.com", "sales", "Europe/London"),
    "P-SVE": ("Sven Berg", "sven.berg@example.com", "engineering", "Europe/Berlin"),
}

_ORGANIZERS = ["Ana Ruiz", "Ben Okafor", "Maya Lindqvist", "Omar Haddad", "Facilities", "Chloe Marsh", "HR team"]
_VENUES = ["Zoom", "Teams", "Phone", "Cafeteria", "Desk", "Offsite", "Client site"]
_RECURRENCES = ["none", "none", "none", "weekly", "fortnightly"]
_CATERING = ["none", "none", "coffee", "lunch_boxes", "water_only"]


def _person(pid: str, busy: list[tuple[str, str, str]], offset: int = HQ_UTC_OFFSET) -> dict[str, Any]:
    """Working hours are 09:00-17:00 local. `busy` entries are (start, end, subject) in local time.

    The calendar-sync columns (event IDs, organisers, venues) are fixed by seed and carry no rule.
    """
    name, email, department, time_zone = _PEOPLE[pid]
    blocks = []
    for a, b, subject in busy:
        rng = random.Random(f"{pid}:{a}:{subject}")
        blocks.append({"event_id": f"EV-{rng.randint(10000, 99999)}", "subject": subject,
                       "organizer": rng.choice(_ORGANIZERS), "start": a, "end": b,
                       "venue": rng.choice(_VENUES), "show_as": "busy"})
    return {
        "name": name, "email": email, "department": department, "time_zone": time_zone,
        "utc_offset_hours": offset, "work_start": "09:00", "work_end": "17:00",
        "calendar_id": f"cal-{pid.lower()}", "busy": blocks,
    }


def _booking_extras(booking_id: str, organizer_id: str) -> dict[str, Any]:
    rng = random.Random(f"booking:{booking_id}")
    return {"organizer_id": organizer_id, "recurrence": rng.choice(_RECURRENCES),
            "cost_centre": f"CC-{rng.randint(1000, 1999)}", "catering_order": rng.choice(_CATERING),
            "created_at": f"2026-10-{rng.randint(1, 28):02d}"}


def _booking(booking_id: str, subject: str, room_id: str, start: str, end: str, attendees: list[str]) -> dict[str, Any]:
    extras = _booking_extras(booking_id, attendees[0])
    return {"booking_id": booking_id, "subject": subject, "organizer_id": extras["organizer_id"], "room_id": room_id,
            "start": _ts(_m(start)), "end": _ts(_m(end)), "attendee_ids": attendees,
            "recurrence": extras["recurrence"], "cost_centre": extras["cost_centre"],
            "catering_order": extras["catering_order"], "created_at": extras["created_at"], "status": "confirmed"}


# Other teams' bookings, present in every scenario. They sit in rooms that no scenario's room
# requirements admit (too small, missing equipment, wrong floor) or, for Grove, block the whole day.
_FILLER = (
    _booking("BK-8101", "Leadership offsite", "R-GROVE", "08:30", "17:30", ["P-OPS", "P-KAI", "P-LIA", "P-MAX"]),
    _booking("BK-8102", "Payroll review", "R-KITE", "09:00", "10:00", ["P-NIA", "P-OWE"]),
    _booking("BK-8103", "Recruiter screen", "R-JUNO", "09:30", "10:00", ["P-RAJ"]),
    _booking("BK-8104", "Vendor onboarding", "R-PINE", "10:00", "11:30", ["P-UMA", "P-WES"]),
    _booking("BK-8105", "All-hands rehearsal", "R-KITE", "11:00", "12:00", ["P-OPS", "P-YAS"]),
    _booking("BK-8106", "Focus block", "R-IRIS", "10:30", "12:30", ["P-NIA"]),
    _booking("BK-8107", "Legal check-in", "R-REEF", "11:30", "12:00", ["P-KAI", "P-LIA"]),
    _booking("BK-8108", "Partner call", "R-OPAL", "12:30", "13:00", ["P-MAX"]),
    _booking("BK-8109", "Design review", "R-QUAY", "13:00", "14:30", ["P-OWE", "P-RAJ", "P-UMA"]),
    _booking("BK-8110", "Security briefing", "R-KITE", "14:00", "15:30", ["P-WES", "P-YAS", "P-NIA"]),
    _booking("BK-8111", "Phone screen", "R-NOOK", "14:30", "15:00", ["P-LIA"]),
    _booking("BK-8112", "Data sync", "R-PINE", "15:00", "16:00", ["P-KAI"]),
    _booking("BK-8113", "Investor prep", "R-QUAY", "15:30", "17:00", ["P-OPS", "P-MAX"]),
    _booking("BK-8114", "Wrap-up", "R-REEF", "16:30", "17:00", ["P-YAS"]),
)


@dataclass(frozen=True)
class Scenario:
    people: dict[str, dict[str, Any]]
    rooms: tuple[str, ...]
    bookings: tuple[dict[str, Any], ...]
    policy: dict[str, Any] | None
    attendees: tuple[str, ...]
    duration: int
    guests: int = 0
    equipment: tuple[str, ...] = ()
    guest_window: tuple[str, str] | None = None
    moved_subject: str | None = None  # a booking with this subject must be cancelled and replaced
    subject: str = ""  # subject of the meeting to book


# 01: five people, video conference, capacity. Common free windows are 09:30, 13:00-14:30 and 16:00.
# No video-conference room is free at 09:30 or 16:00 and 13:00 overlaps both; Cedar (big) and Birch
# (free) are decoys that fail on equipment and capacity.
_S01 = Scenario(
    people={
        "P-ANA": _person("P-ANA", [("09:00", "09:30", "Standup"), ("11:00", "13:00", "Offsite prep"), ("14:30", "16:00", "Design crit"), ("17:15", "18:00", "School run")]),
        "P-BEN": _person("P-BEN", [("09:00", "09:30", "Standup"), ("10:30", "12:30", "Vendor call"), ("14:30", "16:00", "Sprint planning")]),
        "P-CHL": _person("P-CHL", [("09:00", "09:30", "Standup"), ("10:30", "13:00", "Customer visit"), ("14:30", "16:00", "Hiring sync")]),
        "P-DEV": _person("P-DEV", [("09:00", "09:30", "Standup"), ("10:30", "11:00", "1:1"), ("11:30", "13:00", "Deep work"), ("14:30", "16:00", "Incident review")]),
        "P-ELI": _person("P-ELI", [("08:00", "08:45", "Gym"), ("09:00", "09:30", "Standup"), ("10:30", "13:00", "Training"), ("14:30", "15:00", "1:1"), ("15:00", "16:00", "Budget review")]),
    },
    rooms=_ALL_ROOMS,
    bookings=(
        _booking("BK-9101", "Finance sync", "R-ATLAS", "09:00", "11:00", ["P-OPS"]),
        _booking("BK-9102", "Roadmap review", "R-ATLAS", "13:30", "17:00", ["P-OPS"]),
        _booking("BK-9103", "Support huddle", "R-EMBER", "09:00", "10:30", ["P-OPS"]),
        _booking("BK-9104", "Hiring panel", "R-EMBER", "12:00", "13:30", ["P-OPS"]),
        _booking("BK-9105", "Team lunch", "R-EMBER", "15:00", "17:00", ["P-OPS"]),
        _booking("BK-9106", "Board prep", "R-CEDAR", "13:00", "14:00", ["P-OPS"]),
        _booking("BK-9641", "Ops planning", "R-HAVEN", "13:00", "14:00", ["P-RAJ"]),
        _booking("BK-9642", "Sprint demo", "R-LARK", "09:30", "10:30", ["P-UMA", "P-WES"]),
        _booking("BK-9643", "Facilities walk-through", "R-MAPLE", "13:30", "14:30", ["P-OPS", "P-NIA"]),
        *_FILLER,
    ),
    policy=None,
    attendees=("P-ANA", "P-BEN", "P-CHL", "P-DEV", "P-ELI"),
    duration=60,
    equipment=("video_conference",),
)

# 02: three time zones, video conference. Working hours overlap only from 09:00 to 11:00 HQ time.
_S02 = Scenario(
    people={
        "P-PRI": _person("P-PRI", [("08:15", "08:45", "Commute"), ("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch"), ("14:00", "15:00", "Vendor call")], -5),
        "P-TOM": _person("P-TOM", [("10:00", "11:30", "Planning"), ("12:00", "13:00", "Lunch"), ("17:30", "18:30", "Football")], 0),
        "P-SVE": _person("P-SVE", [("09:00", "12:00", "Workshop"), ("12:30", "13:30", "Lunch"), ("15:30", "16:00", "Standup")], 1),
    },
    rooms=_ALL_ROOMS,
    bookings=(
        _booking("BK-9201", "Quarterly review", "R-ATLAS", "09:30", "11:30", ["P-OPS"]),
        _booking("BK-9202", "Candidate interview", "R-BIRCH", "10:00", "12:00", ["P-OPS"]),
        _booking("BK-9203", "Ops sync", "R-EMBER", "11:00", "12:00", ["P-OPS"]),
        _booking("BK-9204", "Design crit", "R-EMBER", "08:00", "09:30", ["P-OPS"]),
        _booking("BK-9601", "Compliance training", "R-HAVEN", "09:30", "11:30", ["P-UMA", "P-WES"]),
        _booking("BK-9602", "Marketing stand-up", "R-LARK", "10:00", "10:30", ["P-NIA"]),
        _booking("BK-9603", "Supplier audit", "R-MAPLE", "13:00", "15:00", ["P-KAI", "P-OWE"]),
        *_FILLER,
    ),
    policy=None,
    attendees=("P-PRI", "P-TOM", "P-SVE"),
    duration=60,
    equipment=("video_conference",),
)

# 03: whiteboard workshop of 90 minutes with a 15-minute room buffer. Several slots are free on
# paper but sit back to back with another booking in the same room.
_S03 = Scenario(
    people={
        "P-ANA": _person("P-ANA", [("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch"), ("14:30", "15:30", "Review")]),
        "P-BEN": _person("P-BEN", [("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch"), ("14:30", "16:00", "Sprint planning")]),
        "P-CHL": _person("P-CHL", [("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch"), ("14:30", "15:00", "1:1")]),
        "P-DEV": _person("P-DEV", [("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch"), ("14:30", "17:00", "On-call handover"), ("17:00", "17:30", "Commute")]),
    },
    rooms=_ALL_ROOMS,
    bookings=(
        _booking("BK-9301", "Ops sync", "R-BIRCH", "09:00", "10:15", ["P-OPS"]),
        _booking("BK-9302", "Finance sync", "R-BIRCH", "12:00", "13:00", ["P-OPS"]),
        _booking("BK-9303", "Vendor demo", "R-DUNE", "09:30", "11:00", ["P-OPS"]),
        _booking("BK-9304", "Customer call", "R-DUNE", "14:00", "15:00", ["P-OPS"]),
        _booking("BK-9305", "Standup overflow", "R-CEDAR", "09:00", "10:00", ["P-OPS"]),
        _booking("BK-9306", "Board prep", "R-CEDAR", "14:00", "15:00", ["P-OPS"]),
        _booking("BK-9611", "Analytics sync", "R-FERN", "09:00", "10:30", ["P-KAI", "P-OWE"]),
        _booking("BK-9612", "Vendor lunch", "R-FERN", "12:30", "13:30", ["P-RAJ", "P-UMA"]),
        _booking("BK-9613", "Pricing review", "R-LARK", "10:00", "11:00", ["P-MAX", "P-NIA", "P-WES"]),
        _booking("BK-9614", "Content planning", "R-LARK", "13:30", "14:00", ["P-LIA"]),
        _booking("BK-9615", "Supplier audit", "R-MAPLE", "11:00", "12:00", ["P-KAI"]),
        *_FILLER,
    ),
    policy={
        "policy_id": "SP-2026-4",
        "version": 2,
        "effective_from": "2026-04-01",
        "rules": [
            "Rooms need a turnaround gap between bookings: a new booking may not start or end within the buffer of another booking in the same room.",
            "Attendees may be double-booked only by explicit exception, which this assistant cannot grant.",
        ],
        "room_buffer_minutes": 15,
        "owner": "facilities",
        "applies_to": ["North", "South"],
        "last_reviewed_at": "2026-10-01",
        "contact": "facilities@example.com",
    },
    attendees=("P-ANA", "P-BEN", "P-CHL", "P-DEV"),
    duration=90,
    equipment=("whiteboard",),
)

# 04: client demo with one external guest. The policy puts guests on the ground floor and keeps
# 12:00-13:00 free. The ground-floor Nook is too small and the early slots are in upper-floor rooms.
_S04 = Scenario(
    people={
        "P-ANA": _person("P-ANA", [("08:30", "09:00", "Commute"), ("14:30", "16:00", "Roadmap review")]),
        "P-BEN": _person("P-BEN", [("09:00", "09:30", "Standup"), ("13:00", "13:30", "1:1"), ("17:00", "17:30", "Commute")]),
    },
    rooms=_ALL_ROOMS,
    bookings=(
        _booking("BK-9401", "Support huddle", "R-EMBER", "09:30", "11:30", ["P-OPS"]),
        _booking("BK-9402", "Hiring panel", "R-EMBER", "14:30", "16:00", ["P-OPS"]),
        _booking("BK-9621", "Partner onboarding", "R-MAPLE", "09:30", "12:00", ["P-OPS", "P-UMA"]),
        _booking("BK-9622", "Visitor briefing", "R-MAPLE", "13:00", "14:30", ["P-KAI"]),
        _booking("BK-9623", "Hiring debrief", "R-HAVEN", "11:00", "11:30", ["P-RAJ", "P-WES"]),
        _booking("BK-9624", "Pricing review", "R-LARK", "10:00", "11:00", ["P-MAX", "P-NIA"]),
        *_FILLER,
    ),
    policy={
        "policy_id": "SP-2026-5",
        "version": 3,
        "effective_from": "2026-06-01",
        "rules": [
            "Meetings with external guests must be held in a ground-floor room (floor 0).",
            "No meetings may overlap the midday blackout window.",
        ],
        "blackout_start": "12:00",
        "blackout_end": "13:00",
        "external_floor": 0,
        "owner": "facilities",
        "applies_to": ["North", "South"],
        "last_reviewed_at": "2026-10-01",
        "contact": "facilities@example.com",
    },
    attendees=("P-ANA", "P-BEN"),
    duration=60,
    guests=1,
    guest_window=("10:00", "15:00"),
)

# 05: Q4 planning gains a fifth attendee who is busy at the old time, so it must move to a
# whiteboard room that seats five. Birch (the old room) is too small and Atlas has no whiteboard.
_S05 = Scenario(
    people={
        "P-ANA": _person("P-ANA", [("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch"), ("14:00", "15:00", "Budget review")]),
        "P-BEN": _person("P-BEN", [("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch")]),
        "P-CHL": _person("P-CHL", [("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch"), ("16:30", "17:00", "Handover")]),
        "P-DEV": _person("P-DEV", [("09:00", "09:30", "Standup"), ("12:00", "13:00", "Lunch")]),
        "P-ELI": _person("P-ELI", [("08:00", "08:45", "Gym"), ("10:00", "12:00", "Training"), ("12:00", "13:00", "Lunch")]),
    },
    rooms=_ALL_ROOMS,
    bookings=(
        _booking("BK-7731", "Q4 planning", "R-BIRCH", "10:00", "11:30", ["P-ANA", "P-BEN", "P-CHL", "P-DEV"]),
        _booking("BK-9501", "Vendor demo", "R-DUNE", "13:00", "14:00", ["P-OPS"]),
        _booking("BK-9502", "Board prep", "R-CEDAR", "14:00", "15:00", ["P-OPS"]),
        _booking("BK-9503", "Town hall rehearsal", "R-CEDAR", "15:30", "17:00", ["P-OPS"]),
        _booking("BK-9631", "Roadmap workshop", "R-LARK", "14:30", "16:30", ["P-OPS", "P-WES", "P-YAS"]),
        _booking("BK-9632", "Retro", "R-FERN", "15:00", "16:00", ["P-NIA"]),
        _booking("BK-9633", "Supplier audit", "R-MAPLE", "09:30", "11:00", ["P-KAI", "P-OWE"]),
        *_FILLER,
    ),
    policy=None,
    attendees=("P-ANA", "P-BEN", "P-CHL", "P-DEV", "P-ELI"),
    duration=90,
    equipment=("whiteboard",),
    moved_subject="Q4 planning",
)


# ---------------------------------------------------------------- constraint logic

def _options(
    people: list[tuple[int, int, list[tuple[int, int]]]],
    rooms: list[dict[str, Any]],
    room_busy: dict[str, list[tuple[int, int]]],
    *,
    duration: int,
    headcount: int,
    equipment: tuple[str, ...],
    guests: int,
    guest_window: tuple[int, int] | None,
    policy: dict[str, Any],
) -> list[tuple[int, str]]:
    """Every (start minute in HQ time, room_id) satisfying availability, room and policy rules.

    `people` holds (work_start, work_end, busy intervals), all in HQ-time minutes.
    """
    buffer = policy.get("room_buffer_minutes", 0)
    blackout = (_m(policy["blackout_start"]), _m(policy["blackout_end"])) if "blackout_start" in policy else None
    out = []
    for start in range(0, 24 * 60, SLOT_MINUTES):
        end = start + duration
        if end > 24 * 60:
            continue
        if guest_window and not (guest_window[0] <= start and end <= guest_window[1]):
            continue
        if blackout and _overlap(start, end, *blackout):
            continue
        if not all(
            ws <= start and end <= we and not any(_overlap(start, end, bs, be) for bs, be in busy)
            for ws, we, busy in people
        ):
            continue
        for room in rooms:
            if room["capacity"] < headcount or not set(equipment) <= set(room["equipment"]):
                continue
            if guests and "external_floor" in policy and room["floor"] != policy["external_floor"]:
                continue
            if any(_overlap(start, end, bs - buffer, be + buffer) for bs, be in room_busy.get(room["room_id"], [])):
                continue
            out.append((start, room["room_id"]))
    return out


def valid_options(sc: Scenario) -> list[tuple[int, str]]:
    """The (start minute, room_id) pairs that satisfy every constraint, from the scenario's initial state."""
    live = [b for b in sc.bookings if b["subject"] != sc.moved_subject]
    people = []
    for pid in sc.attendees:
        p = sc.people[pid]
        shift = (p["utc_offset_hours"] - HQ_UTC_OFFSET) * 60  # local = HQ + shift
        busy = [(_m(b["start"]) - shift, _m(b["end"]) - shift) for b in p["busy"]]
        busy += [(_from_ts(b["start"]), _from_ts(b["end"])) for b in live if pid in b["attendee_ids"]]
        people.append((_m(p["work_start"]) - shift, _m(p["work_end"]) - shift, busy))
    room_busy: dict[str, list[tuple[int, int]]] = {}
    for b in live:
        room_busy.setdefault(b["room_id"], []).append((_from_ts(b["start"]), _from_ts(b["end"])))
    window = (_m(sc.guest_window[0]), _m(sc.guest_window[1])) if sc.guest_window else None
    return _options(
        people, [_ROOMS[r] for r in sc.rooms], room_busy,
        duration=sc.duration, headcount=len(sc.attendees) + sc.guests, equipment=sc.equipment,
        guests=sc.guests, guest_window=window, policy=sc.policy or {},
    )


# ---------------------------------------------------------------- backend

class SchedulingBackend:
    def __init__(self, sc: Scenario) -> None:
        self.sc = sc
        self.people = copy.deepcopy(sc.people)
        self.rooms = [copy.deepcopy(_ROOMS[r]) for r in sc.rooms]
        self.bookings = copy.deepcopy(list(sc.bookings))
        self._next = 9900

    def _active(self) -> list[dict[str, Any]]:
        return [b for b in self.bookings if b["status"] == "confirmed"]

    def list_rooms(self) -> dict[str, Any]:
        return {"rooms": copy.deepcopy(self.rooms)}

    def list_bookings(self, date: str) -> dict[str, Any]:
        rows = [] if date != DATE else [
            {k: copy.deepcopy(v) for k, v in b.items() if k != "status"}
            for b in sorted(self._active(), key=lambda b: (b["start"], b["booking_id"]))
        ]
        return {"date": date, "bookings": rows}

    def get_calendar(self, person_id: str, date: str) -> dict[str, Any]:
        person = self.people.get(person_id)
        if person is None:
            raise ToolError(f"No person with ID {person_id!r}.")
        shift = (person["utc_offset_hours"] - HQ_UTC_OFFSET) * 60
        busy: list[dict[str, Any]] = []
        if date == DATE:
            busy = [{**b, "start": _ts(_m(b["start"])), "end": _ts(_m(b["end"]))} for b in person["busy"]]
            for b in self._active():
                if person_id in b["attendee_ids"]:
                    organizer = _PEOPLE.get(b["organizer_id"], (b["organizer_id"],))[0]
                    busy.append({"event_id": b["booking_id"], "subject": b["subject"], "organizer": organizer,
                                 "start": _ts(_from_ts(b["start"]) + shift), "end": _ts(_from_ts(b["end"]) + shift),
                                 "venue": _ROOMS[b["room_id"]]["name"], "show_as": "busy"})
            busy.sort(key=lambda b: b["start"])
        return {
            "person_id": person_id, "name": person["name"], "email": person["email"],
            "department": person["department"], "date": date, "time_zone": person["time_zone"],
            "utc_offset_hours": person["utc_offset_hours"],
            "work_start": person["work_start"], "work_end": person["work_end"],
            "calendar_id": person["calendar_id"], "busy": busy,
        }

    def get_policy(self) -> dict[str, Any]:
        if self.sc.policy is None:
            raise ToolError("No room-booking policy is configured.")
        return copy.deepcopy(self.sc.policy)

    def book_meeting(self, subject: str, room_id: str, start: str, end: str, attendee_ids: list[str]) -> dict[str, Any]:
        if not any(r["room_id"] == room_id for r in self.rooms):
            raise ToolError(f"No room with ID {room_id!r}.")
        for pid in attendee_ids:
            if pid not in self.people:
                raise ToolError(f"No person with ID {pid!r}.")
        try:
            if not (start.startswith(DATE) and end.startswith(DATE)) or len(start) != 16 or len(end) != 16:
                raise ValueError
            s, e = _from_ts(start), _from_ts(end)
        except ValueError:
            raise ToolError(f"Times must look like '{DATE}T14:00' and fall on {DATE}.") from None
        if e <= s:
            raise ToolError("The meeting must end after it starts.")
        if s % SLOT_MINUTES:
            raise ToolError("Meetings must start on the hour or half hour.")
        for b in self._active():
            if b["room_id"] == room_id and _overlap(s, e, _from_ts(b["start"]), _from_ts(b["end"])):
                raise ToolError(f"Room {room_id} is already booked from {b['start']} to {b['end']}.")
        for pid in attendee_ids:
            person = self.people[pid]
            shift = (person["utc_offset_hours"] - HQ_UTC_OFFSET) * 60
            for blk in person["busy"]:
                if _overlap(s, e, _m(blk["start"]) - shift, _m(blk["end"]) - shift):
                    raise ToolError(f"{pid} is busy during that time ({blk['subject']}).")
            for b in self._active():
                if pid in b["attendee_ids"] and _overlap(s, e, _from_ts(b["start"]), _from_ts(b["end"])):
                    raise ToolError(f"{pid} is already in a meeting from {b['start']} to {b['end']}.")
        self._next += 1
        booking_id = f"BK-{self._next}"
        extras = _booking_extras(booking_id, attendee_ids[0] if attendee_ids else "P-OPS")
        booking = {"booking_id": booking_id, "subject": subject, "organizer_id": extras["organizer_id"],
                   "room_id": room_id, "start": start, "end": end, "attendee_ids": list(attendee_ids),
                   "recurrence": "none", "cost_centre": extras["cost_centre"], "catering_order": "none",
                   "created_at": "2026-11-02", "status": "confirmed"}
        self.bookings.append(booking)
        return copy.deepcopy(booking)

    def cancel_booking(self, booking_id: str) -> dict[str, Any]:
        for b in self.bookings:
            if b["booking_id"] == booking_id:
                if b["status"] == "cancelled":
                    raise ToolError(f"Booking {booking_id} is already cancelled.")
                b["status"] = "cancelled"
                return {"booking_id": booking_id, "status": "cancelled"}
        raise ToolError(f"No booking with ID {booking_id!r}.")

    def snapshot(self) -> dict[str, Any]:
        return {"bookings": copy.deepcopy(self.bookings)}


# ---------------------------------------------------------------- grading

def grade(sc: Scenario, backend: SchedulingBackend) -> Grade:
    initial = {b["booking_id"]: b for b in sc.bookings}
    for b in backend.bookings:
        if b["booking_id"] in initial:
            want = "cancelled" if b["subject"] == sc.moved_subject else "confirmed"
            if b["status"] != want:
                return Grade(success=False, detail=f"booking {b['booking_id']} is {b['status']}, expected {want}")
    new = [b for b in backend.bookings if b["booking_id"] not in initial and b["status"] == "confirmed"]
    if len(new) != 1:
        return Grade(success=False, detail=f"expected exactly 1 new booking, found {len(new)}")
    b = new[0]
    if sorted(b["attendee_ids"]) != sorted(sc.attendees):
        return Grade(success=False, detail=f"attendees {sorted(b['attendee_ids'])}, expected {sorted(sc.attendees)}")
    start, end = _from_ts(b["start"]), _from_ts(b["end"])
    if end - start != sc.duration:
        return Grade(success=False, detail=f"meeting lasts {end - start} minutes, expected {sc.duration}")
    if (start, b["room_id"]) not in valid_options(sc):
        return Grade(success=False, detail=f"{b['room_id']} at {b['start']} breaks a constraint")
    return Grade(success=True, detail="correct booking")


# ---------------------------------------------------------------- reference solution

def _reference(sc: Scenario, call: CallFn, *, use_policy: bool) -> None:
    """Solve from observations only. From the task text it takes the date, HQ offset, people, length and needs."""
    rooms = call("list_rooms")["rooms"]
    bookings = call("list_bookings", date=DATE)["bookings"]
    policy = call("get_policy") if use_policy else {}
    old = next((b for b in bookings if b["subject"] == sc.moved_subject), None) if sc.moved_subject else None
    live = [b for b in bookings if b is not old]
    people = []
    for pid in sc.attendees:
        cal = call("get_calendar", person_id=pid, date=DATE)
        shift = (cal["utc_offset_hours"] - HQ_UTC_OFFSET) * 60
        busy = [(_from_ts(x["start"]) - shift, _from_ts(x["end"]) - shift)
                for x in cal["busy"] if old is None or x["subject"] != old["subject"]]
        people.append((_m(cal["work_start"]) - shift, _m(cal["work_end"]) - shift, busy))
    room_busy: dict[str, list[tuple[int, int]]] = {}
    for b in live:
        room_busy.setdefault(b["room_id"], []).append((_from_ts(b["start"]), _from_ts(b["end"])))
    window = (_m(sc.guest_window[0]), _m(sc.guest_window[1])) if sc.guest_window else None
    options = _options(
        people, rooms, room_busy, duration=sc.duration, headcount=len(sc.attendees) + sc.guests,
        equipment=sc.equipment, guests=sc.guests, guest_window=window, policy=policy,
    )
    if len(options) != 1:
        raise AssertionError(f"expected exactly one valid slot, found {len(options)}")
    start, room_id = options[0]
    if old is not None:
        call("cancel_booking", booking_id=old["booking_id"])
    call("book_meeting", subject=sc.subject, room_id=room_id, start=_ts(start),
         end=_ts(start + sc.duration), attendee_ids=list(sc.attendees))


# ---------------------------------------------------------------- tools

_DATE_ARG = {"type": "string", "description": "The date, formatted YYYY-MM-DD."}

LIST_ROOMS = ToolSpec(
    name="list_rooms",
    description="List every bookable meeting room with its ID, name, seating capacity, floor and installed equipment.",
    paraphrase="Return all bookable meeting rooms, with each one's ID, name, seat capacity, floor and fitted equipment.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
LIST_BOOKINGS = ToolSpec(
    name="list_bookings",
    description="List all confirmed room bookings on a date, with each booking's ID, subject, room, start, end and attendees.",
    paraphrase="Show every confirmed room booking on a date, with its ID, subject, room, start time, end time and attendees.",
    input_schema={"type": "object", "properties": {"date": _DATE_ARG}, "required": ["date"], "additionalProperties": False},
)
GET_CALENDAR = ToolSpec(
    name="get_calendar",
    description=(
        "Get one person's calendar for a date: their UTC offset, working hours and busy blocks. "
        "All times are in that person's local time."
    ),
    paraphrase=(
        "Fetch a person's calendar for one date: UTC offset, working hours and busy blocks. "
        "Every time shown is in that person's own local time."
    ),
    input_schema={
        "type": "object",
        "properties": {"person_id": {"type": "string", "description": "The person's ID."}, "date": _DATE_ARG},
        "required": ["person_id", "date"],
        "additionalProperties": False,
    },
)
GET_POLICY = ToolSpec(
    name="get_policy",
    description="Return the current room-booking policy rules, with the numeric limits that apply to every new booking.",
    paraphrase="Get the room-booking policy now in force, including the numeric limits that every new booking must respect.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
BOOK_MEETING = ToolSpec(
    name="book_meeting",
    description=(
        "Book a room for a meeting. Times are in headquarters time. "
        "Fails if the room or any attendee is already busy during the slot."
    ),
    paraphrase=(
        "Reserve a room for a meeting, using headquarters time. "
        "The call is refused when the room or an attendee is already occupied then."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "The meeting subject."},
            "room_id": {"type": "string", "description": "The room to book."},
            "start": {"type": "string", "description": "Start time, e.g. 2026-11-03T14:00."},
            "end": {"type": "string", "description": "End time, e.g. 2026-11-03T15:00."},
            "attendee_ids": {"type": "array", "items": {"type": "string"}, "description": "IDs of the people attending."},
        },
        "required": ["subject", "room_id", "start", "end", "attendee_ids"],
        "additionalProperties": False,
    },
)
CANCEL_BOOKING = ToolSpec(
    name="cancel_booking",
    description="Cancel a confirmed room booking by its ID, which frees the room and the attendees' time.",
    paraphrase="Cancel a confirmed room booking using its ID, releasing the room and the attendees' time.",
    input_schema={
        "type": "object",
        "properties": {"booking_id": {"type": "string", "description": "The booking's ID."}},
        "required": ["booking_id"],
        "additionalProperties": False,
    },
)

_BASE_TOOLS = (LIST_ROOMS, LIST_BOOKINGS, GET_CALENDAR, BOOK_MEETING)


def _task(task_id: str, instruction: str, sc: Scenario, subject: str, tools: tuple[ToolSpec, ...], use_policy: bool = False) -> Task:
    sc = replace(sc, subject=subject)
    return Task(
        task_id=task_id,
        domain="scheduling",
        instruction=instruction + " Meetings start on the hour or half hour.",
        tools=tools,
        make_backend=partial(SchedulingBackend, sc),
        grade=partial(grade, sc),
        reference_solution=partial(_reference, sc, use_policy=use_policy),
    )


TASKS = [
    _task(
        "scheduling_01_vc_room_for_five",
        "Today is 2026-11-02. Book a 60-minute 'Launch retro' on 2026-11-03 for Ana Ruiz (P-ANA), Ben Okafor (P-BEN), "
        "Chloe Marsh (P-CHL), Dev Patel (P-DEV) and Eli Novak (P-ELI), all based at headquarters. Everyone must be free "
        "during their working hours, the room must seat all five, and it must have video conferencing for a remote "
        "stakeholder. Use headquarters time.",
        _S01, "Launch retro", _BASE_TOOLS,
    ),
    _task(
        "scheduling_02_three_time_zones",
        "Today is 2026-11-02. Book a 60-minute 'Cross-site sync' on 2026-11-03 for Priya Nair (P-PRI, New York), Tom Hale "
        "(P-TOM, London) and Sven Berg (P-SVE, Berlin). Meeting times are in headquarters time, which is New York "
        "(UTC-5). All three must be free and inside their own local 09:00-17:00 working hours, and the room must "
        "seat all three and have video conferencing.",
        _S02, "Cross-site sync", _BASE_TOOLS,
    ),
    _task(
        "scheduling_03_workshop_room_buffer",
        "Today is 2026-11-02. Book a 90-minute 'Process workshop' on 2026-11-03 for Ana Ruiz (P-ANA), Ben Okafor (P-BEN), "
        "Chloe Marsh (P-CHL) and Dev Patel (P-DEV). All four must be free within their working hours, the room must "
        "have a whiteboard and seat four, and the booking must follow the room-booking policy. Use headquarters time.",
        _S03, "Process workshop", (*_BASE_TOOLS, GET_POLICY), use_policy=True,
    ),
    _task(
        "scheduling_04_client_demo_policy",
        "Today is 2026-11-02. Book a 60-minute 'Northwind demo' on 2026-11-03 for Ana Ruiz (P-ANA), Ben Okafor (P-BEN) "
        "and one external guest from Northwind, who can only attend between 10:00 and 15:00 headquarters time. The room "
        "must seat all three and the booking must follow the room-booking policy. Book only the two employees as "
        "attendees; the guest has no calendar here.",
        _S04, "Northwind demo", (*_BASE_TOOLS, GET_POLICY), use_policy=True,
    ),
    _task(
        "scheduling_05_reschedule_planning",
        "Today is 2026-11-02. The 'Q4 planning' meeting on 2026-11-03 must now include Eli Novak (P-ELI) as well as its "
        "current attendees, and Eli cannot make the current time. Move the meeting the same day by cancelling the "
        "existing booking and booking a new one with the same 90-minute length and all five people. Everyone must be "
        "free within their working hours, the room must have a whiteboard and seat all five. Use headquarters time.",
        _S05, "Q4 planning", (*_BASE_TOOLS, CANCEL_BOOKING),
    ),
]
