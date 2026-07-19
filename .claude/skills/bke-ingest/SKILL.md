---
name: bke-ingest
description: >
  Turn a source (a Bible passage, Josephus, an article, archaeology) into
  canonical BKE YAML — entities, typed events, translations, geometry, sources —
  that passes `compiler check`. Use when the user provides a source and wants the
  knowledge base extended or filled. Triggers: "add <passage>", "ingest this
  source", "fill the database from …", "/bke-ingest".
---

# BKE Ingest — a source in, canonical YAML out

You are extending the Bible Knowledge Engine's canonical data from a source. The
data is *source code for a historical world*: every fact is an immutable entity
or a typed event, backed by a citation, expressed only with stable IDs. Your job
is to translate the source into that form **without breaking any invariant**, and
to prove it with the compiler.

Work on a branch. Nothing is done until `python -m compiler check` passes.

## Invariants (never violate)

1. **Stable IDs only, never free text.** `person.abraham`, `place.hebron`,
   `event.lot-separates`. Lowercase, `[a-z0-9_-]`. IDs are forever — never rename.
   Names live only in translations. `person.haran` (a man) and `place.haran` (a
   city) are different objects — collisions are a non-issue, that's the point.
2. **Every fact needs a source.** No source → the fact is invalid. Biblical:
   `reference.<book>.<ch>.<v>` (must exist in the canonical source's versification). Other: `source.<id>`.
3. **Entities are immutable; history lives only in events.** An entity carries
   timeless attributes (relations, geometry) — no dates, no location, no status.
4. **Events are typed.** Each event is one of the registered types with its own
   schema + reducer. If a fact doesn't fit, see "Choosing an event type".
5. **Reuse, never duplicate.** Check for an existing entity before creating one.
6. **Time is EDTF.** `-1446`, uncertain `-1446?`, range `-1300/-1200`, `unknown`.
   Scholarly disagreement → per-model `time.variants`, not duplicate events.

## The model, concretely

Files (one entity / one event per file):

```
knowledge/entities/people/<name>.yaml     type: person
knowledge/entities/places/<name>.yaml     type: place  (subtype: city|land|region|…)
knowledge/events/NN-<slug>.yaml           numeric prefix orders ties
knowledge/geometries/*.geojson            Feature id=geometry.<name>, properties.name_id=place.<id>
knowledge/translations/{uk,en,he}.yaml    labels: <id>: <text>
sources/<resource>/source.yaml            registry + versification (canonical: true = address space)
```

Registered event types and their payloads (see `schemas/event-types/`):

| type | required payload | reducer effect |
|------|------------------|----------------|
| `PersonBorn` | `person`, `place`, opt `parents[]` | person alive, located at place |
| `PersonDied` | `person`, `place` | person dead, located at place |
| `Migration` | `subjects[]`, `from`, `to` | each living subject → `to` |
| `Marriage` | `spouses[2]`, opt `place` | each spouse's `spouse` = the other |
| `CovenantMade` | `parties[]`, opt `place`, `name` | records covenant on parties |
| `TerritoryGranted` | `territory`, `grantee` | region becomes active, held by grantee |
| `CityDestroyed` | `city`, opt `agent` | place's `destroyed` = true |
| `LandAcquired` | `land`, `owner`, opt `from` | place's `owner` = acquirer |
| `Occurrence` | `kind`, `participants[]` and/or `place` | **none** (escape hatch — see below) |

Immutable person relations: `father_of` / `mother_of` / `married_to` (lists),
`father` / `mother` (scalar). Every target must resolve to a real entity.
`presumed_existing: true` marks a person who must be "alive from the start"
because their birth isn't modelled (so `PersonDied`/`Migration` don't fail).

## Workflow

### 1. Build the coverage ledger — extract *everything*, then decide

The failure mode this step exists to kill: silently dropping events because they
don't move a token on the map. **Knowledge-event ≠ map-delta.** A binding, a
blessing, a purchase, a rename, a city's fall all belong in the knowledge base
even when they change no tracked world state. Extract them all; the reducer
decides which produce a map delta, the rest still live in the graph and timeline.

Go through the passage **unit by unit** (pericope, or verse-range) and write a
ledger — one row per candidate fact, nothing skipped implicitly:

```
verse(s) | who / what happens | → decision
Gen 22:1-19 | God tests Abraham; he binds Isaac on Moriah | Occurrence(kind: binding) @ moriah
Gen 23:1-20 | Abraham buys the cave of Machpelah from Ephron | LandAcquired(machpelah, abraham, from: ephron)
Gen 24:29   | Laban, Rebekah's brother, is named          | entity person.laban (+ relation), no event
Gen 24:1-27 | the servant's oath and the sign at the well  | NOT-EVENT (narrative detail, no world fact)
```

Every row ends in exactly one decision: **a typed event**, an **`Occurrence`**
(escape hatch), a **new entity/relation**, **DEFER(reason)**, or
**NOT-EVENT(reason)**. A skip is only legitimate if it's a *written* row with a
reason. Also enumerate every **named person and place** and every **relationship**
stated in the passage — those are data too (this is how Laban and Ishmael's birth
got missed before). Keep the ledger; it goes in the PR as the coverage record.

### 2. Inventory what exists — never duplicate
```
ls knowledge/entities/people knowledge/entities/places
grep -rl "person.<name>\|place.<name>" knowledge/
```
Reuse existing IDs. New IDs must be unique and follow the naming pattern.

### 3. Map each fact to the model
- A person/place not yet present → a new entity file (+ translations, + geometry
  for places).
- A change in the world (birth, death, move, covenant, grant, …) → a typed event.
- A timeless relation (X is father of Y) → an immutable attribute on the entity.

### 4. Choosing an event type — nothing gets dropped
Decide in this order:
1. **Fits a registered type** → use it.
2. **Doesn't fit, but it changes tracked world state** (a person's life/location/
   spouse, a territory, a place's `destroyed`/`owner`) → add a *new typed event*:
   `schemas/event-types/<Type>.schema.json`, a handler in `compiler/events.py`
   (`persons`, `check`, `reduce`, `deps`) + `REGISTRY` entry, and one test.
   **This is code, not just data — call it out to the user.** Rule of three: a
   one-off can wait, a pattern seen ~3× earns a type.
3. **Doesn't fit and changes no tracked state** → `Occurrence` with a short
   `kind` (`binding`, `blessing`, `deliverance`, `rename`, `dream`, `famine`…).
   This is the escape hatch: it produces no map delta but keeps the fact in the
   timeline and graph. **Never DEFER a real, cited event to nothing — an
   `Occurrence` is always available.** When a `kind` recurs ~3× and clearly wants
   a reducer, graduate it to its own typed event (step 2).

Never invent a new payload field on an existing type to smuggle in a different
meaning.

### 5. Write the YAML
- Entities: `id`, `type`, opt `subtype`, opt `geometry`, opt `immutable:` block.
- Events: `id`, `type`, `time:` (`edtf:` or `variants:`), `sources:` (≥1),
  `confidence:`, plus the type payload. Optional `models:`, `requires:`.
- Add uk/en/he labels for every new id. Add a geometry Feature for every new place
  (points `[lon, lat]`; regions as `Polygon`).
- Cite verses; if a chapter isn't in the canonical source's `versification:`
  (`sources/cuv-uk/source.yaml`), add it with its real verse count.

### 5b. Sources are first-class — register them
Every ingest names its source(s) in `sources/<resource>/source.yaml` (one folder
per resource — the folder will also hold its texts later): `id: source.<id>`, `type`, `title`, `language`, `license`, `location`
(`remote` = texts fetched live from `url_template` + `books` map; `none` =
registry-only, e.g. copyrighted translations), and `verse_map` re-addressing any
cited canonical reference whose versification differs in that source
(`reference.genesis.32.24: "32:25"`). Verse TEXTS are not stored in this repo —
the client shows them live from the source; the canonical join key is always
`reference.<book>.<ch>.<v>`. A `source.*` citation that is not in the registry
is a build error. When two sources genuinely disagree about one event, keep one
event and record the divergent testimony (claims) — do not fork the event.

### 6. Decision rules to apply while writing
- **Grouped vs separate movement:** put all subjects in one `Migration` only if
  they truly moved together (a clan). Model separations/divergences as their own
  events — a missing separation is a real bug (cf. Lot in Gen 13).
- **Confidence:** be honest — most narrative facts are `tradition` unless
  archaeologically `confirmed`; use `possible`/`probable` for reconstructions.
- **Dates:** uncertain → EDTF qualifiers; disputed → `time.variants` per model,
  and keep the chain consistent (a birth must precede a migration in *every*
  model, or the build fails — that's the validator doing its job).

### 7. Validate and fix (nothing ships until this is green)
```
.venv/bin/python -m compiler check                 # all models must pass
.venv/bin/python -m compiler check --model critical # if you added variants
.venv/bin/python tests/test_slice.py               # 13+ self-checks
.venv/bin/python -m compiler state <year>          # eyeball the world state
```
Fix every error. Common ones: dangling reference/relation, verse out of range
(extend the canonical versification), death without birth (add birth or `presumed_existing`),
death-before-birth or a dependency cycle.

### 8. Regenerate the API and (optionally) look
```
.venv/bin/python -m compiler site                  # rebuild public/v1/
```
For anything visual (new place, route, region), spot-check with the headless
browser (see `memory: bke-browser-verify`) before finishing.

### 9. Commit on a branch, open a PR
Every contribution is a PR — that's how corrections stay visible in history.
Branch, commit the YAML (never the generated `public/`/`build/`), summarise what
was added and the sources, and open a PR for review. Scholarly claims are the
user's to approve — present, don't merge silently.

## Definition of done
- [ ] **coverage:** every verse-range of the source is a row in the ledger — mapped
      to an event/entity or explicitly DEFER/NOT-EVENT with a reason (no silent skips)
- [ ] `compiler check` passes (default **and** every declared model)
- [ ] `tests/test_slice.py` passes
- [ ] every new entity has uk/en/he labels; every new place has geometry
- [ ] every event cites ≥1 valid source; new chapters added to canon
- [ ] `compiler site` regenerates cleanly
- [ ] changes on a branch, PR opened for review
