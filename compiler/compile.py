"""Compiler orchestrator + CLI.

    YAML source ─▶ typed model ─▶ validate (schema, refs, structure, logic)
                 ─▶ reduce typed events to world state ─▶ keyframes ─▶ artifacts

Run:
    python -m compiler build            # validate + compile + write build/
    python -m compiler check            # validate only (exit 1 on error)
    python -m compiler state -2000      # world state at year -2000 (BC)
    python -m compiler state -2000 --model critical
"""
from __future__ import annotations
import copy
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from .model import Entity, Event, parse_edtf
from .events import REGISTRY

ROOT = Path(__file__).resolve().parent.parent
KNOW = ROOT / "knowledge"
SCHEMAS = ROOT / "schemas"
BUILD = ROOT / "build"

DEFAULT_MODEL = "conservative"
KEYFRAME_EVERY = 5      # snapshot cadence; snapshots are cache, never canonical
SCHEMA_VERSION = 1

REF_RE = re.compile(r"^reference\.([a-z0-9_]+)\.(\d+)\.(\d+)$")

# immutable relation keys whose values are person ids (scalar or list)
RELATION_KEYS = ("father_of", "mother_of", "married_to", "father", "mother")


class BuildError(Exception):
    pass


# ---------------------------------------------------------------- loading

def _load_yaml(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_entities() -> dict[str, Entity]:
    out: dict[str, Entity] = {}
    for path in sorted((KNOW / "entities").rglob("*.yaml")):
        d = _load_yaml(path)
        e = Entity(
            id=d["id"], type=d["type"], subtype=d.get("subtype"),
            geometry=d.get("geometry"), immutable=d.get("immutable") or {},
            sources=tuple(d.get("sources") or ()),
        )
        if e.id in out:
            raise BuildError(f"duplicate entity id: {e.id} ({path})")
        out[e.id] = e
    return out


def load_events() -> list[Event]:
    events: list[Event] = []
    seen: set[str] = set()
    for path in sorted((KNOW / "events").glob("*.yaml")):
        d = _load_yaml(path)
        base = {"id", "type", "time", "sources", "confidence", "models", "requires"}
        ev = Event(
            id=d["id"], type=d["type"], time_raw=d["time"], sources=d["sources"],
            confidence=d.get("confidence", "probable"),
            models=d.get("models"), requires=d.get("requires") or [],
            payload={k: v for k, v in d.items() if k not in base},
        )
        if ev.id in seen:
            raise BuildError(f"duplicate event id: {ev.id} ({path})")
        seen.add(ev.id)
        events.append(ev)
    return events


def load_canon() -> dict[str, dict[int, int]]:
    """The reference address space: the versification of the ONE source marked
    `canonical: true`. IDs are forever, so the flag must never move to a source
    with different numbering — divergent sources use verse_map instead."""
    canonical = [d for d in load_source_registry().values() if d.get("canonical")]
    if len(canonical) != 1:
        raise BuildError(f"exactly one source must have canonical: true (found {len(canonical)})")
    vers = canonical[0].get("versification") or {}
    return {book: {int(c): int(v) for c, v in chs.items()} for book, chs in vers.items()}


def load_translations() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in sorted((KNOW / "translations").glob("*.yaml")):
        d = _load_yaml(path)
        out[d["lang"]] = d.get("labels") or {}
    return out


def load_source_registry() -> dict[str, dict]:
    """Source registry: sources/<resource>/source.yaml, one folder per resource.

    A source is WHERE a fact comes from (a translation, Josephus, a dig report).
    Its folder holds the main file (link, license, its own versification guide,
    verse_map) and, in the future, the texts themselves. `location: remote`
    sources carry a url_template the client fetches verse text from. The
    canonical join key is always reference.<book>.<ch>.<v> — the address space
    is the versification of the single source marked `canonical: true`.
    """
    out: dict[str, dict] = {}
    src_dir = ROOT / "sources"
    if not src_dir.is_dir():
        return out
    for path in sorted(src_dir.glob("*/source.yaml")):
        d = _load_yaml(path)
        sid = d.get("id", "")
        if not sid.startswith("source."):
            raise BuildError(f"{path}: source id must start with 'source.' (got {sid!r})")
        if sid in out:
            raise BuildError(f"duplicate source id: {sid} ({path})")
        out[sid] = d
    return out


def load_geometry_ids() -> set[str]:
    ids: set[str] = set()
    for path in sorted((KNOW / "geometries").glob("*.geojson")):
        gj = json.loads(path.read_text(encoding="utf-8"))
        for feat in gj.get("features", []):
            if "id" in feat:
                ids.add(feat["id"])
    return ids


# ---------------------------------------------------------------- validation

def _validators():
    entity_v = Draft202012Validator(json.loads((SCHEMAS / "entity.schema.json").read_text()))
    event_v = Draft202012Validator(json.loads((SCHEMAS / "event.schema.json").read_text()))
    type_v = {
        p.stem.split(".")[0]: Draft202012Validator(json.loads(p.read_text()))
        for p in (SCHEMAS / "event-types").glob("*.schema.json")
    }
    return entity_v, event_v, type_v


def validate(entities, events, canon, translations, geometry_ids, sources=None):
    """Return (errors, warnings). Errors fail the build.
    `sources` = the source registry; None (tests) skips source.* id checks."""
    errors: list[str] = []
    warnings: list[str] = []
    entity_v, event_v, type_v = _validators()

    # 1. schema shape
    for e in entities.values():
        raw = {"id": e.id, "type": e.type}
        if e.subtype: raw["subtype"] = e.subtype
        if e.geometry: raw["geometry"] = e.geometry
        if e.immutable: raw["immutable"] = e.immutable
        if e.sources: raw["sources"] = list(e.sources)
        for err in entity_v.iter_errors(raw):
            errors.append(f"schema {e.id}: {err.message}")

    for ev in events:
        raw = {"id": ev.id, "type": ev.type, "time": ev.time_raw,
               "sources": ev.sources, "confidence": ev.confidence, **ev.payload}
        if ev.models is not None: raw["models"] = ev.models
        if ev.requires: raw["requires"] = ev.requires
        for err in event_v.iter_errors(raw):
            errors.append(f"schema {ev.id}: {err.message}")
        if ev.type not in REGISTRY:
            errors.append(f"{ev.id}: unknown event type '{ev.type}' (not in registry)")
            continue
        tv = type_v.get(ev.type)
        if tv:
            for err in tv.iter_errors(ev.payload):
                errors.append(f"schema {ev.id} [{ev.type}]: {err.message}")

    # 2. geometry references
    for e in entities.values():
        if e.geometry and e.geometry not in geometry_ids:
            errors.append(f"{e.id}: geometry {e.geometry} not found in any .geojson")

    # 3. entity references inside events + sources
    for ev in events:
        handler = REGISTRY.get(ev.type)
        for pid in (handler.persons(ev) if handler else []):
            if pid not in entities:
                errors.append(f"{ev.id}: references unknown entity {pid}")
        for key in ("place", "from", "to", "territory", "city", "land"):
            pid = ev.payload.get(key)
            if pid and pid not in entities:
                errors.append(f"{ev.id}: references unknown entity {pid}")
        for src in ev.sources:
            errors.extend(_check_source(ev.id, src, canon, sources))

    # 3b. entity relation targets must resolve (catches genealogy typos)
    for e in entities.values():
        for key in RELATION_KEYS:
            val = e.immutable.get(key)
            for target in (val if isinstance(val, list) else [val] if val else []):
                if target not in entities:
                    errors.append(f"{e.id}: relation '{key}' -> unknown entity {target}")
        for src in e.sources:                       # genealogy needs sources too
            errors.extend(_check_source(e.id, src, canon, sources))

    # 3c. verse_map targets must exist in the source's own versification
    for sid, rec in (sources or {}).items():
        vers = rec.get("versification") or {}
        for ref, tgt in (rec.get("verse_map") or {}).items():
            m = REF_RE.match(ref)
            if not m:
                errors.append(f"{sid}: verse_map key {ref} is not a reference id"); continue
            try:
                ch, v = (int(x) for x in str(tgt).split(":"))
            except ValueError:
                errors.append(f"{sid}: verse_map target {tgt!r} must be '<ch>:<v>'"); continue
            book_vers = {int(c): int(n) for c, n in (vers.get(m.group(1)) or {}).items()}
            if book_vers and not (1 <= v <= book_vers.get(ch, 0)):
                errors.append(f"{sid}: verse_map {ref} -> {tgt} outside its versification")

    # 4. translations — missing labels are warnings, not errors
    for lang, labels in translations.items():
        for e in entities.values():
            if e.id not in labels:
                warnings.append(f"missing {lang} translation for {e.id}")
    return errors, warnings


def _check_source(ev_id, src, canon, sources=None) -> list[str]:
    if src.startswith("source."):
        if sources is not None and src not in sources:
            return [f"{ev_id}: cites unknown source {src} (not in sources/)"]
        return []
    m = REF_RE.match(src)
    if not m:
        return [f"{ev_id}: malformed reference {src}"]
    book, ch, verse = m.group(1), int(m.group(2)), int(m.group(3))
    if book not in canon:
        return [f"{ev_id}: reference to unknown book '{book}' ({src})"]
    if ch not in canon[book]:
        return [f"{ev_id}: {book} chapter {ch} not in canon ({src})"]
    if not 1 <= verse <= canon[book][ch]:
        return [f"{ev_id}: {src} out of range (ch {ch} has {canon[book][ch]} verses)"]
    return []


# ------------------------------------------------- dependency graph

def dependency_graph(events, entities, model):
    """Derive edges from event semantics (+ explicit `requires`), then check
    for dangling requires, cycles, and time-order violations where dates are
    comparable. Returns (errors, warnings)."""
    errors, warnings = [], []
    by_id = {ev.id: ev for ev in events}
    born = {ev.payload["person"]: ev.id for ev in events if ev.type == "PersonBorn"}

    edges: dict[str, set[str]] = {ev.id: set() for ev in events}
    for ev in events:
        handler = REGISTRY[ev.type]
        for dep in handler.deps(ev, born):
            edges[ev.id].add(dep)
        for dep in ev.requires:
            if dep not in by_id:
                errors.append(f"{ev.id}: requires unknown event {dep}")
            else:
                edges[ev.id].add(dep)

    # cycles
    WHITE, GREY, BLACK = 0, 1, 2
    color = {eid: WHITE for eid in edges}

    def visit(n, stack):
        color[n] = GREY
        for m in edges[n]:
            if color.get(m) == GREY:
                errors.append(f"dependency cycle: {' -> '.join(stack + [m])}")
            elif color.get(m) == WHITE:
                visit(m, stack + [m])
        color[n] = BLACK

    for eid in edges:
        if color[eid] == WHITE:
            visit(eid, [eid])

    # time order: a dependency must not occur strictly after its dependent
    for ev in events:
        b = ev.time_for(model)
        for dep in edges[ev.id]:
            a = by_id[dep].time_for(model)
            if a.earliest is not None and b.latest is not None and a.earliest > b.latest:
                errors.append(
                    f"{ev.id} ({b.raw}) depends on {dep} ({a.raw}) which occurs later")
    return errors, warnings


# ---------------------------------------------------------------- reduction

def initial_state(entities) -> dict:
    persons, territories, places = {}, {}, {}
    for e in entities.values():
        if e.type == "person":
            presumed = bool(e.immutable.get("presumed_existing"))
            persons[e.id] = {"alive": presumed, "location": None, "covenants": [], "spouse": None}
        elif e.type == "place" and e.subtype == "region":
            territories[e.id] = {"active": False, "granted_to": None}
        elif e.type == "place":                       # settlements/plots carry destroyed/owner state
            places[e.id] = {"destroyed": False, "owner": None}
    return {"persons": persons, "territories": territories, "places": places}


def ordered_events(events, model) -> list[Event]:
    live = [ev for ev in events if ev.exists_in(model)]
    return sorted(live, key=lambda ev: ev.time_for(model).key())  # stable → file order breaks ties


def build_timeline(events, entities, model):
    """Full replay with constraint checks. Returns (final_state, keyframes, errors, warnings).
    keyframes[i] = state after i events applied (i is a multiple of KEYFRAME_EVERY, plus 0)."""
    errors, warnings = [], []
    evs = ordered_events(events, model)
    state = initial_state(entities)
    keyframes = {0: copy.deepcopy(state)}
    for i, ev in enumerate(evs):
        for msg in REGISTRY[ev.type].check(state, ev):
            (warnings if msg.startswith("WARN ") else errors).append(msg.removeprefix("WARN "))
        REGISTRY[ev.type].reduce(state, ev)
        if (i + 1) % KEYFRAME_EVERY == 0:
            keyframes[i + 1] = copy.deepcopy(state)
    return state, keyframes, errors, warnings


def state_at_year(events, entities, model, year):
    """World state at year T, resolved via nearest keyframe + replay (no re-check)."""
    evs = ordered_events(events, model)
    _, keyframes, _, _ = build_timeline(events, entities, model)
    k = sum(1 for ev in evs if ev.time_for(model).key() <= year)
    base = max(i for i in keyframes if i <= k)
    state = copy.deepcopy(keyframes[base])
    for ev in evs[base:k]:
        REGISTRY[ev.type].reduce(state, ev)
    return state


# ---------------------------------------------------------------- orchestration

def compile_all(model=DEFAULT_MODEL, strict=True):
    entities = load_entities()
    events = load_events()
    canon = load_canon()
    translations = load_translations()
    geometry_ids = load_geometry_ids()
    sources = load_source_registry()

    errors, warnings = validate(entities, events, canon, translations, geometry_ids, sources)
    de, dw = dependency_graph(events, entities, model)
    errors += de; warnings += dw

    final, keyframes, re_, rw = build_timeline(events, entities, model)
    errors += re_; warnings += rw

    for w in warnings:
        print(f"  warning: {w}", file=sys.stderr)
    if errors and strict:
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        raise BuildError(f"{len(errors)} error(s)")

    return {
        "entities": entities, "events": events, "canon": canon,
        "translations": translations, "geometry_ids": geometry_ids, "sources": sources,
        "final_state": final, "keyframes": keyframes,
        "errors": errors, "warnings": warnings, "model": model,
    }


def emit(result):
    BUILD.mkdir(exist_ok=True)
    model = result["model"]
    evs = ordered_events(result["events"], model)
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "entities": [
            {"id": e.id, "type": e.type, "subtype": e.subtype, "geometry": e.geometry}
            for e in result["entities"].values()
        ],
        "events_ordered": [
            {"id": ev.id, "type": ev.type, "year": ev.time_for(model).key(),
             "edtf": ev.time_for(model).raw, **ev.payload}
            for ev in evs
        ],
        "final_state": result["final_state"],
        "keyframes": {str(k): v for k, v in result["keyframes"].items()},
        "labels": result["translations"],
    }
    out = BUILD / "compiled.json"
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------- CLI

def _fmt_state(state, entities, labels):
    lines = []
    for pid, p in sorted(state["persons"].items()):
        name = labels.get("en", {}).get(pid, pid)
        # not alive + never had a location = never lived yet (vs. died)
        status = "alive" if p["alive"] else ("dead" if p["location"] else "unborn")
        loc = labels.get("en", {}).get(p["location"], p["location"]) if p["location"] else "—"
        cov = f" covenants={p['covenants']}" if p["covenants"] else ""
        wed = f" ⚭ {labels.get('en', {}).get(p['spouse'], p['spouse'])}" if p.get("spouse") else ""
        lines.append(f"  {name:<10} {status:<5} at {loc}{wed}{cov}")
    for tid, t in sorted(state.get("territories", {}).items()):
        if t["active"]:
            name = labels.get("en", {}).get(tid, tid)
            to = labels.get("en", {}).get(t["granted_to"], t["granted_to"])
            lines.append(f"  [territory] {name} granted to {to}")
    for pid, pl in sorted(state.get("places", {}).items()):
        if pl["destroyed"] or pl["owner"]:
            name = labels.get("en", {}).get(pid, pid)
            note = "destroyed" if pl["destroyed"] else \
                f"owned by {labels.get('en', {}).get(pl['owner'], pl['owner'])}"
            lines.append(f"  [place] {name} {note}")
    return "\n".join(lines)


def main(argv):
    cmd = argv[0] if argv else "build"
    model = DEFAULT_MODEL
    if "--model" in argv:
        model = argv[argv.index("--model") + 1]

    if cmd == "check":
        compile_all(model=model, strict=True)
        print("OK — validation passed")
        return 0

    if cmd == "build":
        result = compile_all(model=model, strict=True)
        out = emit(result)
        print(f"OK — compiled {len(result['events'])} events, "
              f"{len(result['entities'])} entities → {out.relative_to(ROOT)}")
        return 0

    if cmd == "site":
        compile_all(model=model, strict=True)          # never emit invalid data
        from .site import build_site_data
        out = build_site_data(model=model)
        print(f"OK — site data → {out.relative_to(ROOT)}")
        return 0

    if cmd == "state":
        year = int(argv[1])
        result = compile_all(model=model, strict=True)
        state = state_at_year(result["events"], result["entities"], model, year)
        print(f"World state at year {year} (model: {model}):")
        print(_fmt_state(state, result["entities"], result["translations"]))
        return 0

    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BuildError as e:
        print(f"BUILD FAILED: {e}", file=sys.stderr)
        sys.exit(1)
