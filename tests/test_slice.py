"""Self-checks for the Abraham vertical slice. Runnable two ways:
    .venv/bin/python -m pytest tests/          # if pytest installed
    .venv/bin/python tests/test_slice.py       # plain, no framework

Covers the load-bearing invariants: the canon data compiles clean under every
declared model, world-state-at-T is correct, keyframe replay equals full replay,
and each validation layer rejects the mistake it exists to catch.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.compile import (  # noqa: E402
    compile_all, load_entities, load_events, load_canon, load_translations,
    load_geometry_ids, validate, dependency_graph, build_timeline,
    ordered_events, state_at_year, initial_state,
)
from compiler.model import Entity, Event  # noqa: E402
from compiler.events import REGISTRY  # noqa: E402


def _ent(id, **imm):
    return Entity(id=id, type="person", subtype=None, geometry=None, immutable=imm)


def _ev(id, type, time, **payload):
    return Event(id=id, type=type, time_raw=time, sources=["reference.genesis.12.1"],
                 confidence="probable", models=None, requires=[], payload=payload)


def test_canon_builds_green_all_models():
    for model in ("conservative", "critical"):
        r = compile_all(model=model, strict=False)
        assert not r["errors"], f"{model}: {r['errors']}"


def test_state_progression():
    events, entities = load_events(), load_entities()
    at = lambda y: state_at_year(events, entities, "conservative", y)["persons"]

    assert not at(-2170)["person.abraham"]["alive"]        # not yet born
    assert at(-2160)["person.abraham"]["alive"]            # born -2166 in Ur
    assert at(-2160)["person.abraham"]["location"] == "place.ur"
    assert at(-2095)["person.abraham"]["location"] == "place.haran"
    assert at(-2080)["person.terah"]["alive"] is False     # died -2091
    assert at(-2000)["person.isaac"]["alive"]              # born -2066
    assert at(-2000)["person.sarah"]["alive"] is False     # died -2029
    assert at(-1900)["person.abraham"]["alive"] is False   # died -1991
    assert at(-1900)["person.abraham"]["location"] == "place.hebron"
    assert "call" in at(-2000)["person.abraham"]["covenants"]


def test_keyframes_equal_full_replay():
    """The keyframe optimisation must be transparent: nearest-keyframe+replay
    yields byte-identical state to replaying every event from zero."""
    events, entities = load_events(), load_entities()
    evs = ordered_events(events, "conservative")
    for year in range(-2200, -1900, 7):
        fast = state_at_year(events, entities, "conservative", year)
        # naive: replay all events with representative year <= T from scratch
        k = sum(1 for ev in evs if ev.time_for("conservative").key() <= year)
        naive = initial_state(entities)
        for ev in evs[:k]:
            REGISTRY[ev.type].reduce(naive, ev)
        assert fast == naive, f"keyframe mismatch at {year}"


def _validate_only(entities, events):
    return validate(entities, events, load_canon(), {}, load_geometry_ids())


def test_rejects_dangling_reference():
    entities = load_entities()
    bad = _ev("event.x", "Migration", {"edtf": "-2000"})
    bad.payload = {"subjects": ["person.nobody"], "from": "place.ur", "to": "place.haran"}
    errors, _ = _validate_only(entities, [bad])
    assert any("person.nobody" in e for e in errors), errors


def test_rejects_bad_verse_reference():
    entities = load_entities()
    ev = _ev("event.x", "PersonBorn", {"edtf": "-2000"},
             person="person.isaac", place="place.ur")
    ev.sources = ["reference.genesis.12.999"]  # ch 12 has 20 verses
    errors, _ = _validate_only(entities, [ev])
    assert any("out of range" in e for e in errors), errors


def test_rejects_death_without_birth():
    """PersonDied for someone never born and not presumed_existing = error."""
    entities = {"person.ghost": _ent("person.ghost")}  # no presumed_existing
    ev = _ev("event.x", "PersonDied", {"edtf": "-2000"},
             person="person.ghost", place="place.ur")
    _, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert any("without being alive" in e for e in errors), errors


def test_rejects_death_before_birth():
    """Dependency time-order check: death dated earlier than birth."""
    entities = {"person.x": _ent("person.x")}
    born = _ev("event.born", "PersonBorn", {"edtf": "-2000"},
               person="person.x", place="place.ur")
    died = _ev("event.died", "PersonDied", {"edtf": "-2050"},  # 50y BEFORE birth
               person="person.x", place="place.ur")
    errors, _ = dependency_graph([born, died], entities, "conservative")
    assert any("occurs later" in e for e in errors), errors


def test_territory_granted():
    """The Promised Land is inactive until the grant event, then held by Abraham."""
    events, entities = load_events(), load_entities()
    terr = lambda y: state_at_year(events, entities, "conservative", y)["territories"]
    assert terr(-2100)["place.promised_land"]["active"] is False   # before grant
    granted = terr(-2075)["place.promised_land"]                   # after -2081 grant
    assert granted["active"] is True
    assert granted["granted_to"] == "person.abraham"


def test_rejects_grant_to_unborn():
    entities = {"person.x": _ent("person.x"),
                "place.land": Entity("place.land", "place", "region", None, {})}
    ev = _ev("event.g", "TerritoryGranted", {"edtf": "-2000"},
             territory="place.land", grantee="person.x")   # x never born
    _, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert any("not alive" in e for e in errors), errors


def test_detects_dependency_cycle():
    entities = {"person.x": _ent("person.x", presumed_existing=True)}
    a = _ev("event.a", "Migration", {"edtf": "-2000"})
    a.payload = {"subjects": ["person.x"], "from": "place.ur", "to": "place.haran"}
    a.requires = ["event.b"]
    b = _ev("event.b", "Migration", {"edtf": "-2000"})
    b.payload = {"subjects": ["person.x"], "from": "place.haran", "to": "place.ur"}
    b.requires = ["event.a"]
    errors, _ = dependency_graph([a, b], entities, "conservative")
    assert any("cycle" in e for e in errors), errors


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
