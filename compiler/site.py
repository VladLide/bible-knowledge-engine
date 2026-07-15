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


def _person_delta(pre: dict, post: dict) -> dict:
    """Persons whose record changed between two states → new full records."""
    out = {}
    for pid, rec in post["persons"].items():
        if pre["persons"].get(pid) != rec:
            out[pid] = copy.deepcopy(rec)   # snapshot; state keeps mutating after
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
            "changes": _person_delta(pre, state),
        })
    years = [f["year"] for f in frames]
    return {
        "model": model,
        "years": [min(years), max(years)] if years else [0, 0],
        "initial": initial_state(entities)["persons"],
        "frames": frames,
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

    # geometry layer: concatenate every .geojson into one FeatureCollection
    features = []
    for path in sorted((KNOW / "geometries").glob("*.geojson")):
        features += json.loads(path.read_text(encoding="utf-8")).get("features", [])
    (SITE_DATA / "places.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    return SITE_DATA
