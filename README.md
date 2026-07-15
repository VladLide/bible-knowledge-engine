# Bible Knowledge Engine (BKE)

**BKE is a compiler for a historical world.**

**Live demo:** https://vladlide.github.io/bible-knowledge-engine/ — the Abraham
slice as a map + timeline. GitHub Actions validates the canon, compiles state
deltas, and deploys the static site; the browser only applies precompiled
deltas — it never re-interprets events.

The canonical YAML files under [`knowledge/`](knowledge/) are not a database and
not website content. They are *source code* describing the biblical world in a
declarative language. The compiler checks it for structural and logical
correctness, reduces typed events to canonical world state, and emits build
artifacts — a map, a timeline, a knowledge graph, search indices, an API. Every
interface is just one projection of the same canonical model.

This repository currently contains a **vertical slice** — the Abraham narrative
(Ur → Haran → Canaan → Egypt → Hebron) — that exercises every core architectural
decision on live data before the corpus grows.

## Principles (non-negotiable)

- **Git is the single source of truth.** No runtime database. Every contribution
  is a pull request; every historical correction is visible in the git history.
- **YAML is canonical; JSON is build output** and never committed (see `build/`).
- **Event sourcing.** Entities are immutable and carry no historical state.
  All history lives in typed events. `world_state(T) = initial_state + events ≤ T`.
- **Stable, language-independent IDs** (`person.abraham`, `place.jerusalem`).
  IDs never change; only translations do. Free-text names are banned in canon —
  see [`person.haran`](knowledge/entities/people/haran.yaml) vs
  [`place.haran`](knowledge/entities/places/haran.yaml).
- **Every fact needs a source.** A fact with no source is invalid.

## Layout

```
knowledge/entities/    immutable objects (people, places, …), one file each
knowledge/events/      typed events — the only place history lives
knowledge/geometries/  GeoJSON, separate from entities
knowledge/translations one file per language: id → label
canon/                 versification for reference IDs (reference.book.ch.v)
schemas/               JSON Schema: entity, event base, one per event type
compiler/              the compiler (below)
site/                  static-site shell (source); site/data/ is generated
tests/                 self-checks for the slice
build/                 generated artifacts — gitignored, regenerable
```

## The compiler

```
YAML ─▶ typed model ─▶ validate ─▶ reduce events to world state ─▶ keyframes ─▶ artifacts
```

- [`model.py`](compiler/model.py) — typed internal model + EDTF time parser.
- [`events.py`](compiler/events.py) — **the event-type registry (the crown
  jewel).** Each typed event owns exactly one reducer (how it changes world
  state) and its constraints (what must hold to apply it). Projections consume
  the resulting state; they never re-interpret events. Add a new kind of history
  = add one entry here + one JSON Schema.
- [`compile.py`](compiler/compile.py) — loading, validation (schema → references
  → structure → logic → dependency graph), reduction with keyframe snapshots,
  world-state-at-T, and the CLI.

### Design decisions this slice proves

- **Typed events, not generic `changes:`** — the reducer + constraints live in
  code, so the data stays declarative and a contributor writes *what happened*.
- **Keyframes** every N events make timeline scrubbing fast; they are cache,
  never canonical (a test asserts keyframe replay == full replay).
- **EDTF** (ISO 8601-2) for time, including per-model dating *variants* on a
  single event (conservative vs critical) — not duplicate events.
- **Canon module** validates verse references without materialising 31,000 files.
- **Layered validation**: unknown reference, out-of-range verse, death without
  birth, death dated before birth, and dependency cycles are all build errors.

## Usage

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m compiler check                 # validate only
.venv/bin/python -m compiler build                 # validate + compile → build/
.venv/bin/python -m compiler state -2000           # world state at 2000 BC
.venv/bin/python -m compiler state -2140 --model critical
.venv/bin/python tests/test_slice.py               # self-checks
```
