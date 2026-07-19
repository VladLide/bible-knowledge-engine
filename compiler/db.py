"""SQLite canon — the Wikidata-style item/statement store.

The database `bke.sqlite` (repo root, committed) is the CANONICAL knowledge
store: humans edit it through the local web editor, Claude through SQL/CLI.
A deterministic text dump (`dump/items.jsonl`, committed) mirrors it for
longevity, readable git history, and recovery; `verify` proves they match.

The typed core is unchanged: loaders here reconstruct the same Entity/Event
objects the YAML loaders produced, so reducers, validation, and projections
did not move. Statement-level rank/models/refs columns exist now so divergent
multi-source testimony lands on ONE event as parallel statements.

Geometries and the source registry stay as files (few, rarely edited).
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

from .model import Entity, Event

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "bke.sqlite"
DUMP_PATH = ROOT / "dump" / "items.jsonl"

# payload properties that are lists (order matters, kept via ord)
LIST_PROPS = ("subjects", "parents", "spouses", "parties", "participants",
              "creates", "kinds", "requires")
INT_PROPS = ("day",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,            -- person | place | event (| group | work | period)
  subtype TEXT,                  -- person/place role; for events: the event TYPE
  confidence TEXT,               -- events only
  pos INTEGER NOT NULL DEFAULT 0 -- stable order (tie-break for same-dated events)
);
CREATE TABLE IF NOT EXISTS labels(
  item_id TEXT NOT NULL, lang TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY(item_id, lang)
);
CREATE TABLE IF NOT EXISTS statements(
  id INTEGER PRIMARY KEY,
  item_id TEXT NOT NULL,
  prop TEXT NOT NULL,            -- father | point-in-time | place | subjects | ...
  value TEXT NOT NULL,
  rank TEXT NOT NULL DEFAULT 'normal',
  ord INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stmt_models(statement_id INTEGER NOT NULL, model TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stmt_refs(statement_id INTEGER NOT NULL, ref TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS item_refs(
  item_id TEXT NOT NULL, ref TEXT NOT NULL, ord INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS item_models(item_id TEXT NOT NULL, model TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_stmt_item ON statements(item_id);
CREATE INDEX IF NOT EXISTS idx_stmt_prop ON statements(prop, value);
CREATE INDEX IF NOT EXISTS idx_labels_val ON labels(value);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------- loaders

def _statements(con, item_id):
    rows = con.execute(
        "SELECT id, prop, value, rank, ord FROM statements WHERE item_id=? "
        "ORDER BY prop, ord, id", (item_id,)).fetchall()
    out = []
    for sid, prop, value, rank, ord_ in rows:
        models = [m for (m,) in con.execute(
            "SELECT model FROM stmt_models WHERE statement_id=? ORDER BY model", (sid,))]
        out.append({"id": sid, "prop": prop, "value": value, "rank": rank,
                    "ord": ord_, "models": models})
    return out


def load_entities_db(con=None) -> dict[str, Entity]:
    own = con is None
    con = con or connect()
    out: dict[str, Entity] = {}
    for iid, kind, subtype in con.execute(
            "SELECT id, kind, subtype FROM items WHERE kind != 'event' ORDER BY id"):
        imm: dict = {}
        geometry = None
        for st in _statements(con, iid):
            p, v = st["prop"], st["value"]
            if p == "geometry":
                geometry = v
            elif p == "presumed-existing":
                imm["presumed_existing"] = True
            elif p in ("father_of", "mother_of", "married_to"):
                imm.setdefault(p, []).append(v)
            else:                                   # father, mother, same_as, ...
                imm[p] = v
        sources = tuple(r for (r,) in con.execute(
            "SELECT ref FROM item_refs WHERE item_id=? ORDER BY ord", (iid,)))
        out[iid] = Entity(id=iid, type=kind, subtype=subtype,
                          geometry=geometry, immutable=imm, sources=sources)
    if own:
        con.close()
    return out


def load_events_db(con=None) -> list[Event]:
    own = con is None
    con = con or connect()
    events: list[Event] = []
    for iid, subtype, confidence in con.execute(
            "SELECT id, subtype, confidence FROM items WHERE kind='event' ORDER BY pos, id"):
        payload: dict = {}
        time_rows = []
        for st in _statements(con, iid):
            p, v = st["prop"], st["value"]
            if p == "point-in-time":
                time_rows.append(st)
            elif p in LIST_PROPS:
                payload.setdefault(p, []).append(v)
            elif p in INT_PROPS:
                payload[p] = int(v)
            else:
                payload[p] = v
        requires = payload.pop("requires", [])
        if any(st["models"] for st in time_rows):
            variants = []
            for st in sorted(time_rows, key=lambda s: s["ord"]):
                for m in (st["models"] or ["conservative"]):
                    variants.append({"model": m, "edtf": st["value"]})
            time_raw = {"variants": variants}
        else:
            time_raw = {"edtf": time_rows[0]["value"]} if time_rows else {"edtf": "unknown"}
        sources = [r for (r,) in con.execute(
            "SELECT ref FROM item_refs WHERE item_id=? ORDER BY ord", (iid,))]
        models = [m for (m,) in con.execute(
            "SELECT model FROM item_models WHERE item_id=? ORDER BY model", (iid,))] or None
        events.append(Event(id=iid, type=subtype, time_raw=time_raw, sources=sources,
                            confidence=confidence or "probable", models=models,
                            requires=requires, payload=payload))
    if own:
        con.close()
    return events


def load_labels_db(con=None) -> dict[str, dict[str, str]]:
    own = con is None
    con = con or connect()
    out: dict[str, dict[str, str]] = {}
    for iid, lang, value in con.execute("SELECT item_id, lang, value FROM labels"):
        out.setdefault(lang, {})[iid] = value
    if own:
        con.close()
    return out


# ---------------------------------------------------------------- writing

def put_item(con, rec: dict) -> None:
    """Insert/replace one item from its dump-record shape (see dump())."""
    iid = rec["id"]
    con.execute("DELETE FROM labels WHERE item_id=?", (iid,))
    con.execute("DELETE FROM stmt_models WHERE statement_id IN "
                "(SELECT id FROM statements WHERE item_id=?)", (iid,))
    con.execute("DELETE FROM stmt_refs WHERE statement_id IN "
                "(SELECT id FROM statements WHERE item_id=?)", (iid,))
    con.execute("DELETE FROM statements WHERE item_id=?", (iid,))
    con.execute("DELETE FROM item_refs WHERE item_id=?", (iid,))
    con.execute("DELETE FROM item_models WHERE item_id=?", (iid,))
    con.execute("DELETE FROM items WHERE id=?", (iid,))
    if rec.get("pos") is None:
        rec["pos"] = (con.execute("SELECT COALESCE(MAX(pos),0)+1 FROM items").fetchone()[0])
    con.execute("INSERT INTO items(id,kind,subtype,confidence,pos) VALUES(?,?,?,?,?)",
                (iid, rec["kind"], rec.get("subtype"), rec.get("confidence"), rec["pos"]))
    for lang, value in (rec.get("labels") or {}).items():
        con.execute("INSERT INTO labels VALUES(?,?,?)", (iid, lang, value))
    for st in rec.get("statements") or []:
        cur = con.execute(
            "INSERT INTO statements(item_id,prop,value,rank,ord) VALUES(?,?,?,?,?)",
            (iid, st["prop"], str(st["value"]), st.get("rank", "normal"), st.get("ord", 0)))
        sid = cur.lastrowid
        for m in st.get("models") or []:
            con.execute("INSERT INTO stmt_models VALUES(?,?)", (sid, m))
        for r in st.get("refs") or []:
            con.execute("INSERT INTO stmt_refs VALUES(?,?)", (sid, r))
    for i, r in enumerate(rec.get("refs") or []):
        con.execute("INSERT INTO item_refs VALUES(?,?,?)", (iid, r, i))
    for m in rec.get("models") or []:
        con.execute("INSERT INTO item_models VALUES(?,?)", (iid, m))


def delete_item(con, iid: str) -> None:
    put_item(con, {"id": iid, "kind": "person", "pos": 0})   # clears children
    con.execute("DELETE FROM items WHERE id=?", (iid,))


# ---------------------------------------------------------------- dump / restore

def item_record(con, iid: str) -> dict:
    kind, subtype, confidence, pos = con.execute(
        "SELECT kind, subtype, confidence, pos FROM items WHERE id=?", (iid,)).fetchone()
    rec = {"id": iid, "kind": kind, "pos": pos}
    if subtype: rec["subtype"] = subtype
    if confidence: rec["confidence"] = confidence
    labels = dict(con.execute(
        "SELECT lang, value FROM labels WHERE item_id=? ORDER BY lang", (iid,)))
    if labels: rec["labels"] = labels
    stmts = []
    for st in _statements(con, iid):
        s = {"prop": st["prop"], "value": st["value"]}
        if st["rank"] != "normal": s["rank"] = st["rank"]
        if st["ord"]: s["ord"] = st["ord"]
        if st["models"]: s["models"] = st["models"]
        refs = [r for (r,) in con.execute(
            "SELECT ref FROM stmt_refs WHERE statement_id=? ORDER BY ref", (st["id"],))]
        if refs: s["refs"] = refs
        stmts.append(s)
    if stmts: rec["statements"] = stmts
    refs = [r for (r,) in con.execute(
        "SELECT ref FROM item_refs WHERE item_id=? ORDER BY ord", (iid,))]
    if refs: rec["refs"] = refs
    models = [m for (m,) in con.execute(
        "SELECT model FROM item_models WHERE item_id=? ORDER BY model", (iid,))]
    if models: rec["models"] = models
    return rec


def dump(con=None, path: Path = DUMP_PATH) -> int:
    """Deterministic text mirror: one sorted JSON line per item."""
    own = con is None
    con = con or connect()
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = [i for (i,) in con.execute("SELECT id FROM items ORDER BY id")]
    with open(path, "w", encoding="utf-8") as f:
        for iid in ids:
            f.write(json.dumps(item_record(con, iid), ensure_ascii=False,
                               sort_keys=True) + "\n")
    if own:
        con.close()
    return len(ids)


def restore(path: Path = DUMP_PATH, db_path: Path = DB_PATH) -> int:
    """Rebuild the database from the text dump (recovery / fresh clone)."""
    if db_path.exists():
        db_path.unlink()
    con = connect(db_path)
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                put_item(con, json.loads(line)); n += 1
    con.commit(); con.close()
    return n


def verify(con=None, path: Path = DUMP_PATH) -> bool:
    """True when the committed dump matches the database exactly."""
    own = con is None
    con = con or connect()
    want = []
    for (iid,) in con.execute("SELECT id FROM items ORDER BY id"):
        want.append(json.dumps(item_record(con, iid), ensure_ascii=False, sort_keys=True))
    if own:
        con.close()
    have = [l.rstrip("\n") for l in open(path, encoding="utf-8")] if path.exists() else []
    return want == [h for h in have if h.strip()]
