"""«package» persistence - Repository and AuditLog (report Figure 2.1).

Repository maps each domain class to its own SQLite table. Column names come
from the class's fields, so the tables in nsp_data.db mirror the class diagram.
A User hierarchy is stored in one table with a `class_name` column (single-table
inheritance). Composed parts (RegisterEntry, FollowUpAction, KpiIndicator) are
stored inside their owner's row, so they are saved and deleted with the whole.
"""
import dataclasses
import datetime as dt
import enum
import json
import re
import sqlite3
import threading
import typing
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type

SCHEMA_VERSION = "2"


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _plain(v: Any) -> Any:
    """Turn domain values into JSON-friendly values."""
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _plain(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


_HINTS: Dict[type, Dict[str, Any]] = {}


def _hints(cls: type) -> Dict[str, Any]:
    if cls not in _HINTS:
        _HINTS[cls] = typing.get_type_hints(cls)
    return _HINTS[cls]


def _convert(tp: Any, v: Any) -> Any:
    """Turn a stored value back into the type declared on the domain class."""
    if v is None:
        return None
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin is typing.Union:
        return _convert(next(a for a in args if a is not type(None)), v)
    if origin is list:
        items = json.loads(v) if isinstance(v, str) else v
        return [_convert(args[0], x) for x in items] if args else items
    if origin is dict or tp is dict:
        return json.loads(v) if isinstance(v, str) else v
    if dataclasses.is_dataclass(tp):
        return build(tp, v)
    if isinstance(tp, type):
        if issubclass(tp, enum.Enum):
            return tp(v)
        if tp is dt.datetime:
            return dt.datetime.fromisoformat(v)
        if tp is dt.date:
            return dt.date.fromisoformat(v)
        if tp is bool:
            return bool(v)
        if tp is float:
            return float(v)
        if tp is int:
            return int(v)
    return v


def build(cls: type, data: Dict[str, Any]) -> Any:
    hints = _hints(cls)
    return cls(**{f.name: _convert(hints[f.name], data[f.name])
                  for f in dataclasses.fields(cls) if f.name in data})


def _column_value(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (list, dict)) or (dataclasses.is_dataclass(v) and not isinstance(v, type)):
        return json.dumps(_plain(v))
    return _plain(v)


def _sql_type(tp: Any) -> str:
    if typing.get_origin(tp) is typing.Union:
        tp = next(a for a in typing.get_args(tp) if a is not type(None))
    return "INTEGER" if tp in (int, bool) else "REAL" if tp is float else "TEXT"


class Repository:
    """Generic SQLite repository: save / get / find / delete for any registered domain class."""

    def __init__(self, path: str):
        self.con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._tables: Dict[type, str] = {}            # root class -> table
        self._kinds: Dict[type, Dict[str, type]] = {}  # root class -> {kind name: class}

    # ---- schema
    def register(self, root: type, *subclasses: type) -> None:
        classes = (root,) + subclasses
        table = _snake(root.__name__)
        cols: Dict[str, str] = {}
        for c in classes:
            h = _hints(c)
            for f in dataclasses.fields(c):
                if f.name != "id" and f.name not in cols:
                    cols[f.name] = _sql_type(h[f.name])
        self._tables[root] = table
        self._kinds[root] = {c.__name__: c for c in classes}
        ddl = ", ".join('"%s" %s' % (n, t) for n, t in cols.items())
        self.con.execute('CREATE TABLE IF NOT EXISTS "%s" (id INTEGER PRIMARY KEY AUTOINCREMENT, class_name TEXT NOT NULL, %s)'
                         % (table, ddl))

    def schema_ok(self) -> bool:
        try:
            row = self.con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            return bool(row) and row[0] == SCHEMA_VERSION
        except sqlite3.Error:
            return False

    def drop_all(self) -> None:
        with self.lock:
            for (name,) in self.con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                self.con.execute('DROP TABLE "%s"' % name)
            self.con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            self.con.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))

    def set_meta(self, key: str, value: str) -> None:
        self.con.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_meta(self, key: str) -> Optional[str]:
        row = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    # ---- CRUD
    def _root_of(self, cls: type) -> type:
        """The table owner: the class itself or the registered ancestor (e.g. Teacher -> User)."""
        for c in cls.__mro__:
            if c in self._tables:
                return c
        raise KeyError(cls.__name__ + " is not registered")

    def _table_of(self, cls: type) -> str:
        return self._tables[self._root_of(cls)]

    def save(self, obj: Any) -> Any:
        cls = type(obj)
        table = self._table_of(cls)
        data = {f.name: _column_value(getattr(obj, f.name)) for f in dataclasses.fields(obj) if f.name != "id"}
        data["class_name"] = cls.__name__
        with self.lock:
            exists = obj.id and self.con.execute('SELECT 1 FROM "%s" WHERE id=?' % table, (obj.id,)).fetchone()
            if exists:
                sets = ", ".join('"%s"=?' % k for k in data)
                self.con.execute('UPDATE "%s" SET %s WHERE id=?' % (table, sets), list(data.values()) + [obj.id])
            else:
                if obj.id:                      # explicit id, e.g. imported from EduTizim
                    data["id"] = obj.id
                names = ", ".join('"%s"' % k for k in data)
                marks = ", ".join("?" for _ in data)
                cur = self.con.execute('INSERT INTO "%s" (%s) VALUES (%s)' % (table, names, marks), list(data.values()))
                obj.id = cur.lastrowid
        return obj

    def _row_to_obj(self, root: type, row: sqlite3.Row) -> Any:
        cls = self._kinds[root][row["class_name"]]
        data = dict(row)
        return build(cls, data)

    def get(self, cls: type, obj_id: Optional[int]) -> Optional[Any]:
        if obj_id is None:
            return None
        root = self._root_of(cls)
        row = self.con.execute('SELECT * FROM "%s" WHERE id=?' % self._tables[root], (obj_id,)).fetchone()
        if not row:
            return None
        obj = self._row_to_obj(root, row)
        return obj if isinstance(obj, cls) else None

    def find(self, cls: type, order: str = "id", **where: Any) -> List[Any]:
        """Return every stored object of `cls` (including subclasses) matching the equality filters."""
        root = self._root_of(cls)
        kinds = [n for n, c in self._kinds[root].items() if issubclass(c, cls)]
        sql = 'SELECT * FROM "%s" WHERE class_name IN (%s)' % (self._tables[root], ",".join("?" * len(kinds)))
        params: List[Any] = list(kinds)
        for k, v in where.items():
            if v is None:
                sql += ' AND "%s" IS NULL' % k
            else:
                sql += ' AND "%s"=?' % k
                params.append(_column_value(v))
        sql += ' ORDER BY "%s"' % order.lstrip("-") + (" DESC" if order.startswith("-") else "")
        return [self._row_to_obj(root, r) for r in self.con.execute(sql, params).fetchall()]

    def first(self, cls: type, **where: Any) -> Optional[Any]:
        found = self.find(cls, **where)
        return found[0] if found else None

    def delete(self, obj: Any) -> None:
        with self.lock:
            self.con.execute('DELETE FROM "%s" WHERE id=?' % self._table_of(type(obj)), (obj.id,))


# --------------------------------------------------------------------------- audit
@dataclass
class AuditEntry:
    id: int = 0
    at: Optional[dt.datetime] = None
    user_id: Optional[int] = None
    user_name: str = ""
    action: str = ""
    entity: str = ""
    old_value: str = ""
    new_value: str = ""


class AuditLog:
    """NFR-01.4: who changed what, when, with the old and new values."""

    def __init__(self, repo: Repository):
        self.repo = repo
        repo.register(AuditEntry)

    def record(self, actor: Any, action: str, entity: str, old: Any = "", new: Any = "") -> None:
        fmt = lambda v: v if isinstance(v, str) else json.dumps(_plain(v))
        self.repo.save(AuditEntry(at=dt.datetime.now().replace(microsecond=0),
                                  user_id=getattr(actor, "id", None),
                                  user_name=getattr(actor, "full_name", "System"),
                                  action=action, entity=entity, old_value=fmt(old), new_value=fmt(new)))

    def recent(self, limit: int = 200) -> List[AuditEntry]:
        return self.repo.find(AuditEntry, order="-id")[:limit]
