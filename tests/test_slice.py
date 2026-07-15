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


def test_marriage():
    """After Gen 24, Isaac and Rebekah are each other's spouse in world state."""
    events, entities = load_events(), load_entities()
    at = lambda y: state_at_year(events, entities, "conservative", y)["persons"]
    assert at(-2030)["person.isaac"]["spouse"] is None          # before marriage
    end = at(-2020)
    assert end["person.isaac"]["spouse"] == "person.rebekah"    # after -2026
    assert end["person.rebekah"]["spouse"] == "person.isaac"


def test_rejects_marriage_to_unborn():
    entities = {"person.a": _ent("person.a", presumed_existing=True),
                "person.b": _ent("person.b")}   # b never born, not presumed
    ev = _ev("event.wed", "Marriage", {"edtf": "-2000"})
    ev.payload = {"spouses": ["person.a", "person.b"]}
    _, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert any("not alive" in e for e in errors), errors


def test_rejects_grant_to_unborn():
    entities = {"person.x": _ent("person.x"),
                "place.land": Entity("place.land", "place", "region", None, {})}
    ev = _ev("event.g", "TerritoryGranted", {"edtf": "-2000"},
             territory="place.land", grantee="person.x")   # x never born
    _, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert any("not alive" in e for e in errors), errors


def test_lot_separates_then_flees():
    """Gen 13 → Lot settles at Sodom; Gen 19 → he flees to Zoar before the end."""
    events, entities = load_events(), load_entities()
    at = lambda y: state_at_year(events, entities, "conservative", y)["persons"]
    assert at(-2075)["person.lot"]["location"] == "place.sodom"   # after separation, before flight
    end = at(-1991)
    assert end["person.lot"]["location"] == "place.zoar"          # fled the overthrow (Gen 19)
    assert end["person.abraham"]["location"] == "place.hebron"


def test_graph_relations():
    from compiler.site import build_graph
    g = build_graph(load_entities(), load_events())
    edges = {(e["source"], e["rel"], e["target"]) for e in g["edges"]}
    assert ("person.abraham", "parent_of", "person.isaac") in edges
    assert ("person.abraham", "spouse", "person.sarah") in edges     # deduped, ordered
    assert ("person.abraham", "granted", "place.promised_land") in edges
    assert ("person.lot", "traveled_to", "place.sodom") in edges       # separation edge
    assert ("person.lot", "died_at", "place.sodom") not in edges       # Lot never dies in slice


def test_graph_genealogy():
    """Sourced genealogy (no events) shows up in the graph."""
    from compiler.site import build_graph
    g = build_graph(load_entities(), load_events())
    edges = {(e["source"], e["rel"], e["target"]) for e in g["edges"]}
    for parent, child in [("person.abraham", "person.ishmael"),
                          ("person.hagar", "person.ishmael"),
                          ("person.bethuel", "person.laban"),
                          ("person.haran", "person.milcah"),
                          ("person.terah", "person.sarah")]:
        assert (parent, "parent_of", child) in edges, (parent, child)


def test_genealogy_stays_off_the_map():
    """Event-less people (not presumed_existing) never get a location → not on map."""
    events, entities = load_events(), load_entities()
    persons = state_at_year(events, entities, "conservative", -1900)["persons"]
    for pid in ["person.laban", "person.nahor", "person.haran"]:   # Ishmael now has a birth → on map
        assert persons[pid]["location"] is None, pid


def test_rejects_bad_entity_source():
    entities = {"person.x": Entity("person.x", "person", None, None, {},
                                   ("reference.genesis.99.1",))}
    errors, _ = _validate_only(entities, [])
    assert any("chapter 99" in e or "out of range" in e for e in errors), errors


def test_rejects_dangling_relation():
    entities = {"person.a": _ent("person.a", father_of=["person.ghost"])}
    errors, _ = _validate_only(entities, [])
    assert any("person.ghost" in e and "relation" in e for e in errors), errors


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


def _place_ent(id, subtype="city"):
    return Entity(id, "place", subtype, None, {})


def test_city_destroyed():
    entities = {"place.sodom": _place_ent("place.sodom")}
    ev = _ev("event.d", "CityDestroyed", {"edtf": "-2000"}, city="place.sodom")
    state, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert not errors, errors
    assert state["places"]["place.sodom"]["destroyed"] is True


def test_land_acquired():
    entities = {"person.abraham": _ent("person.abraham", presumed_existing=True),
                "place.machpelah": _place_ent("place.machpelah", "field")}
    ev = _ev("event.buy", "LandAcquired", {"edtf": "-2000"},
             land="place.machpelah", owner="person.abraham")
    state, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert not errors, errors
    assert state["places"]["place.machpelah"]["owner"] == "person.abraham"


def test_rejects_land_acquired_by_unborn():
    entities = {"person.x": _ent("person.x"),                 # never born, not presumed
                "place.machpelah": _place_ent("place.machpelah", "field")}
    ev = _ev("event.buy", "LandAcquired", {"edtf": "-2000"},
             land="place.machpelah", owner="person.x")
    _, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert any("not alive" in e for e in errors), errors


def test_occurrence_changes_no_state():
    """Escape-hatch event is recorded but touches no tracked world state."""
    entities = {"person.a": _ent("person.a", presumed_existing=True),
                "place.moriah": _place_ent("place.moriah", "mountain")}
    before = initial_state(entities)
    ev = _ev("event.akedah", "Occurrence", {"edtf": "-2000"}, kind="binding",
             participants=["person.a"], place="place.moriah")
    state, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert not errors, errors
    assert state["persons"] == before["persons"]
    assert state["places"] == before["places"]


def test_rejects_occurrence_with_unborn_participant():
    entities = {"person.x": _ent("person.x")}                 # never born
    ev = _ev("event.o", "Occurrence", {"edtf": "-2000"},
             kind="blessing", participants=["person.x"])
    _, _, errors, _ = build_timeline([ev], entities, "conservative")
    assert any("not alive" in e for e in errors), errors


def test_occurrence_enters_graph():
    """A stateless Occurrence still contributes a participant→place edge."""
    from compiler.site import build_graph
    entities = {"person.a": _ent("person.a"), "place.moriah": _place_ent("place.moriah", "mountain")}
    ev = _ev("event.akedah", "Occurrence", {"edtf": "-2000"}, kind="binding",
             participants=["person.a"], place="place.moriah")
    edges = {(e["source"], e["rel"], e["target"]) for e in build_graph(entities, [ev])["edges"]}
    assert ("person.a", "present_at", "place.moriah") in edges


def test_ishmael_born_and_relocates():
    """Gen 16:15 birth places Ishmael on the map; Gen 21 sends him to Beersheba."""
    events, entities = load_events(), load_entities()
    at = lambda y: state_at_year(events, entities, "conservative", y)["persons"]
    assert at(-2075)["person.ishmael"]["alive"]                       # born -2080
    assert at(-2075)["person.ishmael"]["location"] == "place.hebron"
    assert at(-1991)["person.ishmael"]["location"] == "place.beersheba"


def test_sodom_and_gomorrah_destroyed():
    events, entities = load_events(), load_entities()
    places = lambda y: state_at_year(events, entities, "conservative", y)["places"]
    assert places(-2075)["place.sodom"]["destroyed"] is False        # before Gen 19
    end = places(-1991)
    assert end["place.sodom"]["destroyed"] is True
    assert end["place.gomorrah"]["destroyed"] is True


def test_machpelah_owned_by_abraham():
    events, entities = load_events(), load_entities()
    places = lambda y: state_at_year(events, entities, "conservative", y)["places"]
    assert places(-2030)["place.machpelah"]["owner"] is None          # before the purchase
    assert places(-2028)["place.machpelah"]["owner"] == "person.abraham"


def test_binding_is_stateless_occurrence():
    """The Akedah is recorded on the timeline but changes no world state."""
    events, entities = load_events(), load_entities()
    before = state_at_year(events, entities, "conservative", -2051)
    after = state_at_year(events, entities, "conservative", -2049)   # binding at -2050
    assert before == after


def test_new_event_graph_edges():
    from compiler.site import build_graph
    edges = {(e["source"], e["rel"], e["target"]) for e in build_graph(load_entities(), load_events())["edges"]}
    assert ("person.abraham", "acquired", "place.machpelah") in edges
    assert ("person.abraham", "present_at", "place.moriah") in edges
    assert ("person.isaac", "present_at", "place.moriah") in edges


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
