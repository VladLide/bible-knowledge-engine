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
   `reference.<book>.<ch>.<v>` (must exist in `canon/`). Other: `source.<id>`.
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
canon/protestant.yaml                     books: <book>: {<chapter>: <verse_count>}
```

Registered event types and their payloads (see `schemas/event-types/`):

| type | required payload | reducer effect |
|------|------------------|----------------|
| `PersonBorn` | `person`, `place`, opt `parents[]` | person alive, located at place |
| `PersonDied` | `person`, `place` | person dead, located at place |
| `Migration` | `subjects[]`, `from`, `to` | each living subject → `to` |
| `CovenantMade` | `parties[]`, opt `place`, `name` | records covenant on parties |
| `TerritoryGranted` | `territory`, `grantee` | region becomes active, held by grantee |

Immutable person relations: `father_of` / `mother_of` / `married_to` (lists),
`father` / `mother` (scalar). Every target must resolve to a real entity.
`presumed_existing: true` marks a person who must be "alive from the start"
because their birth isn't modelled (so `PersonDied`/`Migration` don't fail).

## Workflow

### 1. Read the source and list the facts
Extract only what the source actually states. For each fact note: who/what,
where, when, relationships, and the exact citation (book chapter:verse).

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

### 4. Choosing an event type
- **Fits a registered type** → use it.
- **Doesn't fit** → prefer adding a *new typed event* (it keeps data declarative):
  create `schemas/event-types/<Type>.schema.json`, a handler class in
  `compiler/events.py` (`persons`, `check`, `reduce`, `deps`) + `REGISTRY` entry,
  and one test. **This is code, not just data — call it out to the user.**
  Rule of three: a one-off oddity can wait; a pattern seen ~3× earns a type.
- Never invent a new payload field on an existing type to smuggle in a different
  meaning.

### 5. Write the YAML
- Entities: `id`, `type`, opt `subtype`, opt `geometry`, opt `immutable:` block.
- Events: `id`, `type`, `time:` (`edtf:` or `variants:`), `sources:` (≥1),
  `confidence:`, plus the type payload. Optional `models:`, `requires:`.
- Add uk/en/he labels for every new id. Add a geometry Feature for every new place
  (points `[lon, lat]`; regions as `Polygon`).
- Cite verses; if a chapter isn't in `canon/protestant.yaml`, add it with its real
  verse count.

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
(extend canon), death without birth (add birth or `presumed_existing`),
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
- [ ] `compiler check` passes (default **and** every declared model)
- [ ] `tests/test_slice.py` passes
- [ ] every new entity has uk/en/he labels; every new place has geometry
- [ ] every event cites ≥1 valid source; new chapters added to canon
- [ ] `compiler site` regenerates cleanly
- [ ] changes on a branch, PR opened for review
