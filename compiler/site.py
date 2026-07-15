"""Map/timeline projection → static-site data artifacts.

A projection consumes canonical world state + the event stream; it never
re-interprets events. Here we replay once (authoritative reducers) and capture
per-event state *deltas*. The browser applies deltas cumulatively to reach any
frame and uses each event's type+payload only to decide HOW to animate the
change — presentation, not world semantics.

Emits into site/data/ (git-ignored, regenerated in CI):
    timeline.json   initial state + ordered deltas + resolved years
    places.geojson  copy of the geometry layer
    labels.json     translations, all languages

ponytail: one timeline.json holds the whole slice (KBs). At 100k events this
splits into per-era chunks (deltas already key cleanly by year) — build that
when a file actually gets heavy, not before.
"""
from __future__ import annotations
import copy
import json
import shutil

from .compile import (
    ROOT, KNOW, load_entities, load_events, load_translations,
    ordered_events, initial_state,
)
from .events import REGISTRY

SITE_DATA = ROOT / "site" / "data"


NAMESPACES = ("persons", "territories")


def _delta(pre: dict, post: dict) -> dict:
    """Records that changed between two states, per namespace → new full records."""
    out = {}
    for ns in NAMESPACES:
        changed = {k: copy.deepcopy(v) for k, v in post[ns].items() if pre[ns].get(k) != v}
        if changed:
            out[ns] = changed              # snapshot; state keeps mutating after
    return out


def build_timeline(events, entities, model):
    evs = ordered_events(events, model)
    state = initial_state(entities)
    frames = []
    for ev in evs:
        pre = copy.deepcopy(state)
        REGISTRY[ev.type].reduce(state, ev)
        t = ev.time_for(model)
        frames.append({
            "event": ev.id,
            "type": ev.type,
            "year": t.key(),
            "edtf": t.raw,
            "payload": ev.payload,          # for animation (from/to, place, person…)
            "changes": _delta(pre, state),
        })
    years = [f["year"] for f in frames]
    return {
        "model": model,
        "years": [min(years), max(years)] if years else [0, 0],
        "initial": initial_state(entities),   # {persons, territories}
        "frames": frames,
    }


def build_graph(entities, events):
    """Knowledge-graph projection: nodes = entities, edges = relations.

    Edges come from two sources — immutable entity relations (genealogy) and
    event participation — but the graph itself re-interprets nothing; it reads
    the same canonical data the map does. Undirected relations are de-duplicated
    by ordering the endpoints.
    """
    nodes = [{"id": e.id, "type": e.type, "subtype": e.subtype} for e in entities.values()]
    edges = set()  # (source, target, rel)

    for e in entities.values():
        for child in (e.immutable.get("father_of") or []) + (e.immutable.get("mother_of") or []):
            edges.add((e.id, child, "parent_of"))
        for key in ("father", "mother"):
            if e.immutable.get(key):
                edges.add((e.immutable[key], e.id, "parent_of"))
        for spouse in (e.immutable.get("married_to") or []):
            a, b = sorted((e.id, spouse))          # symmetric → canonical order dedupes
            edges.add((a, b, "spouse"))

    verb = {"PersonBorn": ("person", "place", "born_at"),
            "PersonDied": ("person", "place", "died_at"),
            "TerritoryGranted": ("grantee", "territory", "granted")}
    for ev in events:
        if ev.type in verb:
            src_k, dst_k, rel = verb[ev.type]
            edges.add((ev.payload[src_k], ev.payload[dst_k], rel))
        if ev.type == "Migration":
            for s in ev.payload["subjects"]:           # deduped per (person, place)
                edges.add((s, ev.payload["to"], "traveled_to"))

    return {
        "nodes": nodes,
        "edges": [{"source": s, "target": t, "rel": r} for s, t, r in sorted(edges)],
    }


def build_site_data(model="conservative"):
    entities = load_entities()
    events = load_events()
    translations = load_translations()

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "timeline.json").write_text(
        json.dumps(build_timeline(events, entities, model), ensure_ascii=False, indent=2),
        encoding="utf-8")
    (SITE_DATA / "labels.json").write_text(
        json.dumps(translations, ensure_ascii=False, indent=2), encoding="utf-8")
    (SITE_DATA / "graph.json").write_text(
        json.dumps(build_graph(entities, events), ensure_ascii=False, indent=2), encoding="utf-8")

    # geometry layer: concatenate every .geojson into one FeatureCollection
    features = []
    for path in sorted((KNOW / "geometries").glob("*.geojson")):
        features += json.loads(path.read_text(encoding="utf-8")).get("features", [])
    (SITE_DATA / "places.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    return SITE_DATA
