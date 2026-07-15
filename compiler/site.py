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
import os

from .compile import (
    ROOT, KNOW, SCHEMA_VERSION, load_entities, load_events, load_translations,
    ordered_events, initial_state,
)
from .events import REGISTRY

# The published data API (repo A's GitHub Pages root). Versioned so a breaking
# schema change becomes /v2 without disturbing /v1 consumers.
DIST = ROOT / "public" / "v1"

# Years per timeline chunk. The format supports arbitrarily many chunks; we emit
# only those the data fills. Clients load chunks via the manifest, so growing to
# 100k events across hundreds of chunks needs no client change — only lazy
# per-era loading, which is a client optimisation for when chunk count is large.
ERA_SIZE = 1000


def _era_bucket(year) -> int:
    return (int(year) // ERA_SIZE) * ERA_SIZE


NAMESPACES = ("persons", "territories", "places")


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
            "sources": list(ev.sources),    # for the click-info panel
            "confidence": ev.confidence,
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
    nodes = [{"id": e.id, "type": e.type, "subtype": e.subtype,
              "sources": list(e.sources)} for e in entities.values()]
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
            "TerritoryGranted": ("grantee", "territory", "granted"),
            "LandAcquired": ("owner", "land", "acquired")}
    for ev in events:
        if ev.type in verb:
            src_k, dst_k, rel = verb[ev.type]
            edges.add((ev.payload[src_k], ev.payload[dst_k], rel))
        if ev.type == "Migration":
            for s in ev.payload["subjects"]:           # deduped per (person, place)
                edges.add((s, ev.payload["to"], "traveled_to"))
        if ev.type == "Occurrence" and ev.payload.get("place"):
            for p in ev.payload.get("participants", []):   # escape-hatch events still enter the graph
                edges.add((p, ev.payload["place"], "present_at"))

    return {
        "nodes": nodes,
        "edges": [{"source": s, "target": t, "rel": r} for s, t, r in sorted(edges)],
    }


def _write(rel: str, obj) -> None:
    path = DIST / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def build_site_data(model="conservative"):
    """Emit the versioned, chunk-ready data API into public/v1/.

    Layout (all listed by manifest.json — the single entry point clients read):
        manifest.json
        timeline/initial.json      world state before any event
        timeline/<era>.json         per-era frame chunks (deltas)
        graph.json  places.geojson  labels.json
    """
    entities = load_entities()
    events = load_events()
    translations = load_translations()
    tl = build_timeline(events, entities, model)   # {model, years, initial, frames}

    _write("timeline/initial.json", tl["initial"])
    eras_by_bucket: dict[int, list] = {}
    for f in tl["frames"]:
        eras_by_bucket.setdefault(_era_bucket(f["year"]), []).append(f)
    eras = []
    for bucket in sorted(eras_by_bucket):
        rel = f"timeline/{bucket}.json"
        _write(rel, eras_by_bucket[bucket])
        eras.append({"from": bucket, "to": bucket + ERA_SIZE, "file": rel})

    _write("graph.json", build_graph(entities, events))
    _write("labels.json", translations)

    features = []
    for path in sorted((KNOW / "geometries").glob("*.geojson")):
        features += json.loads(path.read_text(encoding="utf-8")).get("features", [])
    _write("places.geojson", {"type": "FeatureCollection", "features": features})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "years": tl["years"],
        "timeline": {"initial": "timeline/initial.json", "eras": eras},
        "graph": "graph.json",
        "geometry": "places.geojson",
        "labels": "labels.json",
    }
    rev = os.environ.get("BKE_BUILD_REV")   # CI stamps the commit; absent → deterministic
    if rev:
        manifest["rev"] = rev
    _write("manifest.json", manifest)

    # landing page at the API root, so a bare visit to repo A's Pages is informative
    (DIST.parent / "index.html").write_text(_LANDING, encoding="utf-8")
    return DIST


_LANDING = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bible Knowledge Engine — Data API</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:44rem;margin:3rem auto;padding:0 1rem;
background:#14171c;color:#e8e6e1}a{color:#4a9eff}code{background:#232833;padding:.1em .3em;border-radius:4px}
h1{font-size:1.5rem}li{margin:.3rem 0}</style></head><body>
<h1>Bible Knowledge Engine — Data API</h1>
<p>Canonical, precompiled data for the Bible Knowledge Engine: a static,
versioned, CORS-enabled JSON API. Any application may consume it — the web app
below is just one client.</p>
<ul>
<li>Entry point: <a href="v1/manifest.json"><code>v1/manifest.json</code></a> — read it first, then fetch the artifacts it lists.</li>
<li>Web app (map · timeline · knowledge graph): <a href="https://vladlide.github.io/bke-web/">bke-web</a></li>
<li>Source &amp; documentation: <a href="https://github.com/VladLide/bible-knowledge-engine">GitHub</a></li>
</ul>
<p>The data is generated from canonical YAML by the compiler; nothing here is
hand-edited. Breaking schema changes move to <code>/v2</code>, leaving
<code>/v1</code> stable for existing consumers.</p>
</body></html>
"""
