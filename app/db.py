"""Banco de metadados (capa, status, partes) em SQLite."""
import json
import sqlite3
import threading
import time
from typing import Any, Optional


class MetadataDB:
    def __init__(self, path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pages_total INTEGER DEFAULT 0,
                    pages_done INTEGER DEFAULT 0,
                    pages_ocr INTEGER DEFAULT 0,
                    error TEXT,
                    metadata TEXT,
                    created_at REAL,
                    updated_at REAL
                )"""
            )
            self._conn.commit()

    def create(self, doc_id: str, filename: str, path: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents (id, filename, path, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (doc_id, filename, path, "queued", now, now),
            )
            self._conn.commit()

    def update(self, doc_id: str, **fields: Any) -> None:
        if "metadata" in fields and not isinstance(fields["metadata"], str):
            fields["metadata"] = json.dumps(fields["metadata"], ensure_ascii=False)
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE documents SET {cols} WHERE id=?", (*fields.values(), doc_id))
            self._conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else None
        return d

    def get(self, doc_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]
