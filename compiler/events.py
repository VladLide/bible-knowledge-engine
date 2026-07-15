"""The event-type registry — the crown jewel.

Each typed event owns exactly one canonical reducer (how it changes world
state) and its logical constraints (what must be true to apply it). This is the
ONE place event semantics live; projections consume the resulting state, they
never re-interpret events. Adding a new kind of historical fact = adding one
entry here + one JSON Schema. Grow it from real data, not up front.

A handler exposes:
  schema           -> basename in schemas/event-types/ (structural contract)
  persons(ev)      -> person ids this event touches (for the derived dep graph)
  check(state, ev) -> list[str] constraint violations, tested against state
                      BEFORE the event applies
  reduce(state, ev)-> mutate world state in place
  deps(ev, born)   -> event ids this depends on (born = {person_id: birth_event_id})
"""
from __future__ import annotations
from .model import Event


def _person(state, pid):
    return state["persons"].setdefault(
        pid, {"alive": False, "location": None, "covenants": [], "spouse": None}
    )


def _territory(state, tid):
    return state["territories"].setdefault(tid, {"active": False, "granted_to": None})


def _place(state, pid):
    return state["places"].setdefault(pid, {"destroyed": False, "owner": None})


class PersonBorn:
    schema = "PersonBorn"

    @staticmethod
    def persons(ev: Event):
        return [ev.payload["person"]]

    @staticmethod
    def check(state, ev: Event):
        p = _person(state, ev.payload["person"])
        if p["alive"]:
            return [f"{ev.id}: {ev.payload['person']} is already alive (double birth)"]
        if p["location"] is not None:  # existed and died before → resurrection
            return [f"{ev.id}: {ev.payload['person']} born after already existing"]
        return []

    @staticmethod
    def reduce(state, ev: Event):
        p = _person(state, ev.payload["person"])
        p["alive"] = True
        p["location"] = ev.payload["place"]

    @staticmethod
    def deps(ev: Event, born):
        return []  # a birth depends on nothing


class PersonDied:
    schema = "PersonDied"

    @staticmethod
    def persons(ev: Event):
        return [ev.payload["person"]]

    @staticmethod
    def check(state, ev: Event):
        p = _person(state, ev.payload["person"])
        if not p["alive"]:
            return [f"{ev.id}: {ev.payload['person']} dies without being alive "
                    f"(needs a prior PersonBorn or presumed_existing)"]
        return []

    @staticmethod
    def reduce(state, ev: Event):
        p = _person(state, ev.payload["person"])
        p["alive"] = False
        p["location"] = ev.payload["place"]

    @staticmethod
    def deps(ev: Event, born):
        b = born.get(ev.payload["person"])
        return [b] if b else []


class Migration:
    schema = "Migration"

    @staticmethod
    def persons(ev: Event):
        return list(ev.payload["subjects"])

    @staticmethod
    def check(state, ev: Event):
        errs = []
        for s in ev.payload["subjects"]:
            p = _person(state, s)
            if not p["alive"]:
                errs.append(f"{ev.id}: {s} migrates while not alive")
            elif p["location"] is not None and p["location"] != ev.payload["from"]:
                # location mismatch is a warning, not a hard error (gaps happen)
                errs.append(f"WARN {ev.id}: {s} was at {p['location']}, "
                            f"not {ev.payload['from']}")
        return errs

    @staticmethod
    def reduce(state, ev: Event):
        for s in ev.payload["subjects"]:
            _person(state, s)["location"] = ev.payload["to"]

    @staticmethod
    def deps(ev: Event, born):
        return [born[s] for s in ev.payload["subjects"] if s in born]


class CovenantMade:
    schema = "CovenantMade"

    @staticmethod
    def persons(ev: Event):
        return list(ev.payload["parties"])

    @staticmethod
    def check(state, ev: Event):
        errs = []
        for party in ev.payload["parties"]:
            if not _person(state, party)["alive"]:
                errs.append(f"{ev.id}: covenant party {party} is not alive")
        return errs

    @staticmethod
    def reduce(state, ev: Event):
        for party in ev.payload["parties"]:
            _person(state, party)["covenants"].append(ev.payload.get("name", "covenant"))

    @staticmethod
    def deps(ev: Event, born):
        return [born[p] for p in ev.payload["parties"] if p in born]


class Marriage:
    schema = "Marriage"

    @staticmethod
    def persons(ev: Event):
        return list(ev.payload["spouses"])

    @staticmethod
    def check(state, ev: Event):
        errs = []
        for pid in ev.payload["spouses"]:
            if not _person(state, pid)["alive"]:
                errs.append(f"{ev.id}: spouse {pid} is not alive")
        return errs

    @staticmethod
    def reduce(state, ev: Event):
        a, b = ev.payload["spouses"]
        _person(state, a)["spouse"] = b
        _person(state, b)["spouse"] = a

    @staticmethod
    def deps(ev: Event, born):
        return [born[p] for p in ev.payload["spouses"] if p in born]


class TerritoryGranted:
    schema = "TerritoryGranted"

    @staticmethod
    def persons(ev: Event):
        return [ev.payload["grantee"]]

    @staticmethod
    def check(state, ev: Event):
        if not _person(state, ev.payload["grantee"])["alive"]:
            return [f"{ev.id}: grantee {ev.payload['grantee']} is not alive"]
        return []

    @staticmethod
    def reduce(state, ev: Event):
        t = _territory(state, ev.payload["territory"])
        t["active"] = True
        t["granted_to"] = ev.payload["grantee"]

    @staticmethod
    def deps(ev: Event, born):
        b = born.get(ev.payload["grantee"])
        return [b] if b else []


class CityDestroyed:
    schema = "CityDestroyed"

    @staticmethod
    def persons(ev: Event):
        a = ev.payload.get("agent")
        return [a] if a else []

    @staticmethod
    def check(state, ev: Event):
        if _place(state, ev.payload["city"])["destroyed"]:
            return [f"WARN {ev.id}: {ev.payload['city']} is already destroyed"]
        return []

    @staticmethod
    def reduce(state, ev: Event):
        _place(state, ev.payload["city"])["destroyed"] = True

    @staticmethod
    def deps(ev: Event, born):
        b = born.get(ev.payload.get("agent"))
        return [b] if b else []


class LandAcquired:
    schema = "LandAcquired"

    @staticmethod
    def persons(ev: Event):
        return [ev.payload["owner"]] + ([ev.payload["from"]] if ev.payload.get("from") else [])

    @staticmethod
    def check(state, ev: Event):
        if not _person(state, ev.payload["owner"])["alive"]:
            return [f"{ev.id}: acquirer {ev.payload['owner']} is not alive"]
        return []

    @staticmethod
    def reduce(state, ev: Event):
        _place(state, ev.payload["land"])["owner"] = ev.payload["owner"]

    @staticmethod
    def deps(ev: Event, born):
        return [born[p] for p in LandAcquired.persons(ev) if p in born]


class CreationAct:
    """Genesis 1 creation week: brings cosmic entities into existence. A created
    place gets `created = True`; the created person (mankind) becomes alive so
    later events on it (life/location) behave like any other person. `kinds` are
    categories of created life not tracked as entities and have no reducer effect
    (like an Occurrence tag). Depends on nothing — it is the beginning."""
    schema = "CreationAct"

    @staticmethod
    def persons(ev: Event):
        # every created entity id (persons + cosmic places); the validator only
        # checks these exist, so returning places here catches dangling `creates`.
        return list(ev.payload.get("creates", []))

    @staticmethod
    def check(state, ev: Event):
        errs = []
        for cid in ev.payload.get("creates", []):
            if cid.startswith("person."):
                if _person(state, cid)["alive"]:
                    errs.append(f"{ev.id}: {cid} created but already exists")
            elif _place(state, cid).get("created"):
                errs.append(f"WARN {ev.id}: {cid} already created")
        return errs

    @staticmethod
    def reduce(state, ev: Event):
        for cid in ev.payload.get("creates", []):
            if cid.startswith("person."):
                _person(state, cid)["alive"] = True
            else:
                _place(state, cid)["created"] = True

    @staticmethod
    def deps(ev: Event, born):
        return []  # creation depends on nothing


class Occurrence:
    """Escape hatch — records a cited event that changes no tracked state, so it
    still lives in the timeline and graph. No reducer effect by design."""
    schema = "Occurrence"

    @staticmethod
    def persons(ev: Event):
        return list(ev.payload.get("participants", []))

    @staticmethod
    def check(state, ev: Event):
        return [f"{ev.id}: participant {p} is not alive"
                for p in ev.payload.get("participants", [])
                if not _person(state, p)["alive"]]

    @staticmethod
    def reduce(state, ev: Event):
        pass  # by design: an Occurrence changes no world state

    @staticmethod
    def deps(ev: Event, born):
        return [born[p] for p in ev.payload.get("participants", []) if p in born]


REGISTRY = {
    "PersonBorn": PersonBorn,
    "PersonDied": PersonDied,
    "Migration": Migration,
    "CovenantMade": CovenantMade,
    "Marriage": Marriage,
    "TerritoryGranted": TerritoryGranted,
    "CityDestroyed": CityDestroyed,
    "LandAcquired": LandAcquired,
    "CreationAct": CreationAct,
    "Occurrence": Occurrence,
}
