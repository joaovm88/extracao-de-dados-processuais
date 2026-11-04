"""Banco de metadados: casos (devedor), processos (cadastro Mcfly) e documentos (PDFs)."""
import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

_JSON_COLS = ("metadata", "result", "mcfly")


class MetadataDB:
    def __init__(self, path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, nome TEXT NOT NULL, created_at REAL);
                CREATE TABLE IF NOT EXISTS processes (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL, numero TEXT NOT NULL, mcfly TEXT,
                    UNIQUE(case_id, numero));
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, case_id TEXT, filename TEXT NOT NULL, path TEXT NOT NULL,
                    status TEXT NOT NULL, numero_processo TEXT, volume INTEGER, evento TEXT, tipo TEXT, sistema TEXT, page_start INTEGER DEFAULT 1,
                    classe TEXT, sheet TEXT, pages_total INTEGER DEFAULT 0, pages_done INTEGER DEFAULT 0,
                    pages_ocr INTEGER DEFAULT 0, error TEXT, metadata TEXT, result TEXT,
                    reviewed INTEGER DEFAULT 0, created_at REAL, updated_at REAL);
                """
            )
            self._conn.commit()

    # ---- util
    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        for c in _JSON_COLS:
            if c in d:
                d[c] = json.loads(d[c]) if d[c] else None
        return d

    def _update(self, table: str, key: str, **fields: Any) -> None:
        for c in _JSON_COLS:
            if c in fields and not isinstance(fields[c], (str, type(None))):
                fields[c] = json.dumps(fields[c], ensure_ascii=False)
        if table == "documents":
            fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE {table} SET {cols} WHERE id=?", (*fields.values(), key))
            self._conn.commit()

    # ---- casos
    def create_case(self, nome: str) -> dict:
        cid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute("INSERT INTO cases VALUES (?,?,?)", (cid, nome, time.time()))
            self._conn.commit()
        return self.get_case(cid)

    def get_case(self, cid: str) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def list_cases(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT * FROM cases ORDER BY created_at DESC")]

    # ---- processos (cadastro)
    def upsert_process(self, case_id: str, numero: str, mcfly: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO processes (id, case_id, numero, mcfly) VALUES (?,?,?,?) "
                "ON CONFLICT(case_id, numero) DO UPDATE SET mcfly=excluded.mcfly",
                (uuid.uuid4().hex, case_id, numero, json.dumps(mcfly, ensure_ascii=False)))
            self._conn.commit()

    def list_processes(self, case_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM processes WHERE case_id=? ORDER BY rowid", (case_id,)).fetchall()
        return [self._row(r) for r in rows]

    # ---- documentos
    def create(self, doc_id: str, filename: str, path: str, case_id: Optional[str] = None, **extra: Any) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents (id, case_id, filename, path, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (doc_id, case_id, filename, path, "queued", now, now))
            self._conn.commit()
        if extra:
            self.update(doc_id, **extra)

    def update(self, doc_id: str, **fields: Any) -> None:
        self._update("documents", doc_id, **fields)

    def get(self, doc_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, case_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            if case_id:
                rows = self._conn.execute("SELECT * FROM documents WHERE case_id=? ORDER BY created_at DESC", (case_id,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]
