"""Minimal local web editor over the SQLite canon.

    python -m compiler edit          → http://127.0.0.1:8100

Search / view / edit items and statements; buttons run validation and the text
dump in-process. Localhost only, stdlib only, one file. Deliberately small:
the reducers/validators remain the single guard — the editor never bypasses
`compiler check`, it just makes the canon browsable and editable by a human.
"""
from __future__ import annotations
import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db

CSS = """
body{margin:0;background:#14171c;color:#e8e6e1;font:15px/1.55 system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:18px 20px}
a{color:#4a9eff;text-decoration:none} a:hover{text-decoration:underline}
h1{font-size:1.25rem;margin:0 0 12px} h1 a{color:#e8e6e1}
input,select,button{background:#232a36;border:1px solid #2a3140;color:#e8e6e1;
  border-radius:5px;padding:6px 9px;font-size:.9rem}
button{cursor:pointer} button:hover{border-color:#4a9eff}
button.danger:hover{border-color:#e0719e;color:#e0719e}
table{border-collapse:collapse;width:100%;font-size:.88rem;margin:8px 0}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:#8b93a1;text-align:left}
th,td{padding:6px 8px;border-bottom:1px solid #2a3140;vertical-align:middle}
.mono{font-family:ui-monospace,Consolas,monospace;font-variant-numeric:tabular-nums}
.muted{color:#8b93a1} .pill{background:#232a36;border-radius:12px;padding:1px 9px;font-size:.78rem}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}
.ok{color:#7bd88f}.err{color:#e0719e;white-space:pre-wrap;font-size:.85rem}
form.inline{display:inline}
"""

def _page(title, body):
    return (f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style><div class='wrap'>"
            f"<h1><a href='/'>BKE редактор</a> · {html.escape(title)}</h1>{body}</div>").encode()

def _esc(v): return html.escape(str(v if v is not None else ""))


def index(con, q, kind):
    where, args = [], []
    if q:
        where.append("(i.id LIKE ? OR EXISTS(SELECT 1 FROM labels l WHERE l.item_id=i.id AND l.value LIKE ?))")
        args += [f"%{q}%", f"%{q}%"]
    if kind:
        where.append("i.kind=?"); args.append(kind)
    sql = ("SELECT i.id, i.kind, i.subtype, "
           "(SELECT value FROM labels WHERE item_id=i.id AND lang='uk') "
           f"FROM items i {'WHERE ' + ' AND '.join(where) if where else ''} "
           "ORDER BY i.kind, i.id LIMIT 300")
    rows = con.execute(sql, args).fetchall()
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    opts = "".join(f"<option value='{k}' {'selected' if k==kind else ''}>{k}</option>"
                   for k in ("", "person", "place", "event"))
    body = (f"<form class='row' method='get'>"
            f"<input name='q' value='{_esc(q)}' placeholder='Пошук: id або назва…' autofocus>"
            f"<select name='kind'>{opts}</select><button>Знайти</button>"
            f"<span class='muted'>показано {len(rows)} з {total}</span>"
            f"<a href='/new' style='margin-left:auto'>+ новий item</a>"
            f"<form class='inline' method='post' action='/check'><button>Перевірити базу</button></form>"
            f"<form class='inline' method='post' action='/dump'><button>Зберегти дамп</button></form>"
            f"</form><table><tr><th>id</th><th>вид</th><th>підтип</th><th>назва (uk)</th></tr>")
    for iid, k, sub, uk in rows:
        body += (f"<tr><td class='mono'><a href='/item?id={urllib.parse.quote(iid)}'>{_esc(iid)}</a></td>"
                 f"<td>{_esc(k)}</td><td>{_esc(sub)}</td><td>{_esc(uk)}</td></tr>")
    return _page("огляд", body + "</table>")


def item_page(con, iid, msg=""):
    row = con.execute("SELECT kind, subtype, confidence, pos FROM items WHERE id=?", (iid,)).fetchone()
    if not row:
        return _page("не знайдено", f"<p class='err'>Немає item {_esc(iid)}</p>")
    kind, subtype, confidence, pos = row
    qid = urllib.parse.quote(iid)
    b = [f"<div class='row'><span class='pill'>{_esc(kind)}</span>"
         f"<span class='pill'>{_esc(subtype or '—')}</span>"
         + (f"<span class='pill'>confidence: {_esc(confidence)}</span>" if confidence else "")
         + f"<span class='muted mono'>pos {pos}</span>"
         f"<form class='inline' method='post' action='/del-item' "
         f"onsubmit=\"return confirm('Видалити {_esc(iid)}?')\">"
         f"<input type='hidden' name='id' value='{_esc(iid)}'>"
         f"<button class='danger' style='margin-left:12px'>Видалити item</button></form></div>"]
    if msg:
        b.append(f"<p class='{ 'err' if msg.startswith('ПОМИЛК') else 'ok'}'>{_esc(msg)}</p>")
    # labels
    b.append("<h3 class='muted'>Назви</h3><table><tr><th>мова</th><th>значення</th><th></th></tr>")
    for lang, val in con.execute("SELECT lang, value FROM labels WHERE item_id=? ORDER BY lang", (iid,)):
        b.append(f"<tr><td class='mono'>{_esc(lang)}</td><td>"
                 f"<form class='inline' method='post' action='/set-label'>"
                 f"<input type='hidden' name='id' value='{_esc(iid)}'>"
                 f"<input type='hidden' name='lang' value='{_esc(lang)}'>"
                 f"<input name='value' value='{_esc(val)}' size='34'><button>Зберегти</button></form></td>"
                 f"<td></td></tr>")
    b.append(f"<tr><td><form class='inline' method='post' action='/set-label'>"
             f"<input type='hidden' name='id' value='{_esc(iid)}'>"
             f"<input name='lang' size='3' placeholder='uk'></td>"
             f"<td><input name='value' size='34' placeholder='нова назва'>"
             f"<button>Додати</button></form></td><td></td></tr></table>")
    # statements
    b.append("<h3 class='muted'>Statements</h3>"
             "<table><tr><th>властивість</th><th>значення</th><th>ранг</th><th>ord</th>"
             "<th>моделі</th><th>джерела</th><th></th></tr>")
    for st in db._statements(con, iid):
        refs = [r for (r,) in con.execute(
            "SELECT ref FROM stmt_refs WHERE statement_id=?", (st["id"],))]
        val = st["value"]
        vh = (f"<a href='/item?id={urllib.parse.quote(val)}' class='mono'>{_esc(val)}</a>"
              if con.execute("SELECT 1 FROM items WHERE id=?", (val,)).fetchone()
              else f"<span class='mono'>{_esc(val)}</span>")
        b.append(f"<tr><td class='mono'>{_esc(st['prop'])}</td><td>{vh}</td>"
                 f"<td>{_esc(st['rank'] if st['rank']!='normal' else '')}</td>"
                 f"<td class='mono'>{st['ord'] or ''}</td>"
                 f"<td class='mono'>{_esc(','.join(st['models']))}</td>"
                 f"<td class='mono'>{_esc(', '.join(refs))}</td>"
                 f"<td><form class='inline' method='post' action='/del-stmt'>"
                 f"<input type='hidden' name='id' value='{_esc(iid)}'>"
                 f"<input type='hidden' name='sid' value='{st['id']}'>"
                 f"<button class='danger'>×</button></form></td></tr>")
    b.append(f"</table><form class='row' method='post' action='/add-stmt'>"
             f"<input type='hidden' name='id' value='{_esc(iid)}'>"
             f"<input name='prop' size='14' placeholder='властивість' required>"
             f"<input name='value' size='26' placeholder='значення' required>"
             f"<input name='rank' size='8' placeholder='ранг'>"
             f"<input name='models' size='12' placeholder='моделі (кома)'>"
             f"<input name='refs' size='26' placeholder='джерела (кома)'>"
             f"<button>Додати statement</button></form>")
    # item refs
    b.append("<h3 class='muted'>Джерела item</h3><table>")
    for ref, o in con.execute("SELECT ref, ord FROM item_refs WHERE item_id=? ORDER BY ord", (iid,)):
        b.append(f"<tr><td class='mono'>{_esc(ref)}</td>"
                 f"<td><form class='inline' method='post' action='/del-ref'>"
                 f"<input type='hidden' name='id' value='{_esc(iid)}'>"
                 f"<input type='hidden' name='ref' value='{_esc(ref)}'>"
                 f"<button class='danger'>×</button></form></td></tr>")
    b.append(f"</table><form class='row' method='post' action='/add-ref'>"
             f"<input type='hidden' name='id' value='{_esc(iid)}'>"
             f"<input name='ref' size='34' placeholder='reference.book.ch.v або source.id' required>"
             f"<button>Додати джерело</button></form>")
    return _page(iid, "".join(b))


def run_check():
    from .compile import compile_all, BuildError
    try:
        r = compile_all(strict=False)
        errs = r["errors"]
        return ("OK — база валідна" if not errs
                else "ПОМИЛКИ:\n" + "\n".join(errs[:20]))
    except BuildError as e:
        return f"ПОМИЛКА ЗБІРКИ: {e}"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, body, code=200, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, loc):
        self.send_response(303); self.send_header("Location", loc); self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        con = db.connect()
        try:
            if u.path == "/":
                self._send(index(con, qs.get("q", [""])[0], qs.get("kind", [""])[0]))
            elif u.path == "/item":
                self._send(item_page(con, qs.get("id", [""])[0], qs.get("msg", [""])[0]))
            elif u.path == "/new":
                self._send(_page("новий item", """
<form class='row' method='post' action='/new-item'>
<input name='id' size='30' placeholder='person.xxx / place.xxx / event.xxx' required>
<select name='kind'><option>person</option><option>place</option><option>event</option></select>
<input name='subtype' size='14' placeholder='підтип/тип події'>
<input name='uk' size='22' placeholder='назва uk'>
<button>Створити</button></form>
<p class='muted'>ID вічний: [a-z0-9_-], з простором імен. Для події subtype = тип
(PersonBorn, Migration, Occurrence…), решта — statements на сторінці item.</p>"""))
            else:
                self._send(b"not found", 404)
        finally:
            con.close()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        f = lambda k: form.get(k, [""])[0].strip()
        iid = f("id")
        con = db.connect()
        try:
            p = self.path
            if p == "/set-label" and f("lang"):
                con.execute("INSERT OR REPLACE INTO labels VALUES(?,?,?)",
                            (iid, f("lang"), f("value")))
            elif p == "/add-stmt":
                cur = con.execute(
                    "INSERT INTO statements(item_id,prop,value,rank,ord) VALUES(?,?,?,?,0)",
                    (iid, f("prop"), f("value"), f("rank") or "normal"))
                for m in filter(None, (x.strip() for x in f("models").split(","))):
                    con.execute("INSERT INTO stmt_models VALUES(?,?)", (cur.lastrowid, m))
                for r in filter(None, (x.strip() for x in f("refs").split(","))):
                    con.execute("INSERT INTO stmt_refs VALUES(?,?)", (cur.lastrowid, r))
            elif p == "/del-stmt":
                sid = f("sid")
                con.execute("DELETE FROM stmt_models WHERE statement_id=?", (sid,))
                con.execute("DELETE FROM stmt_refs WHERE statement_id=?", (sid,))
                con.execute("DELETE FROM statements WHERE id=?", (sid,))
            elif p == "/add-ref":
                o = con.execute("SELECT COALESCE(MAX(ord),-1)+1 FROM item_refs WHERE item_id=?",
                                (iid,)).fetchone()[0]
                con.execute("INSERT INTO item_refs VALUES(?,?,?)", (iid, f("ref"), o))
            elif p == "/del-ref":
                con.execute("DELETE FROM item_refs WHERE item_id=? AND ref=?", (iid, f("ref")))
            elif p == "/new-item":
                db.put_item(con, {"id": iid, "kind": f("kind"), "subtype": f("subtype") or None,
                                  "labels": ({"uk": f("uk")} if f("uk") else None)})
            elif p == "/del-item":
                db.delete_item(con, iid)
                con.commit(); self._redirect("/"); return
            elif p == "/dump":
                con.commit()
                n = db.dump(con)
                self._redirect(f"/?q=&kind=&msg=dumped+{n}"); return
            elif p == "/check":
                con.commit()
                msg = run_check()
                self._send(_page("перевірка", f"<p class='{'ok' if msg.startswith('OK') else 'err'}'>"
                                              f"{html.escape(msg)}</p><p><a href='/'>← назад</a></p>"))
                return
            con.commit()
            self._redirect(f"/item?id={urllib.parse.quote(iid)}" if iid else "/")
        finally:
            con.close()


def serve(port=8100):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"BKE редактор → http://127.0.0.1:{port}  (Ctrl+C — вихід)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        return 0
