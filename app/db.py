"""Banco de metadados: casos (devedor), processos (1 linha de relatório cada) e documentos (PDFs/partes)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

_JSON_COLS = ("metadata", "result", "mcfly")
# stale: 0 = extração em dia | 1 = há material novo a extrair | 2 = extraindo agora
_PROC_COLS = {"result": "TEXT", "reviewed": "INTEGER DEFAULT 0", "stale": "INTEGER DEFAULT 0", "classe": "TEXT",
              "sheet": "TEXT", "tipo": "TEXT", "sistema": "TEXT", "error": "TEXT"}


# Colunas de documents adicionadas ao longo das versões: bancos antigos (volume do Docker) são migrados na abertura.
_DOC_COLS = {"case_id": "TEXT", "numero_processo": "TEXT", "volume": "INTEGER", "evento": "TEXT", "tipo": "TEXT",
             "sistema": "TEXT", "parte": "INTEGER", "page_start": "INTEGER DEFAULT 1", "classe": "TEXT",
             "pages_total": "INTEGER DEFAULT 0", "pages_done": "INTEGER DEFAULT 0", "pages_ocr": "INTEGER DEFAULT 0",
             "error": "TEXT", "metadata": "TEXT", "created_at": "REAL", "updated_at": "REAL", "sha256": "TEXT"}


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
                    status TEXT NOT NULL, numero_processo TEXT, volume INTEGER, evento TEXT, tipo TEXT, sistema TEXT,
                    parte INTEGER, page_start INTEGER DEFAULT 1, classe TEXT, pages_total INTEGER DEFAULT 0,
                    pages_done INTEGER DEFAULT 0, pages_ocr INTEGER DEFAULT 0, error TEXT, metadata TEXT,
                    created_at REAL, updated_at REAL, sha256 TEXT);
                """
            )
            have = {r["name"] for r in self._conn.execute("PRAGMA table_info(processes)")}
            for col, typ in _PROC_COLS.items():
                if col not in have:
                    self._conn.execute(f"ALTER TABLE processes ADD COLUMN {col} {typ}")
            have = {r["name"] for r in self._conn.execute("PRAGMA table_info(documents)")}
            for col, typ in _DOC_COLS.items():
                if col not in have:
                    self._conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {typ}")
            self._conn.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        for c in _JSON_COLS:
            if c in d:
                d[c] = json.loads(d[c]) if d[c] else None
        return d

    def _update(self, table: str, where: str, args: tuple, **fields: Any) -> None:
        for c in _JSON_COLS:
            if c in fields and not isinstance(fields[c], (str, type(None))):
                fields[c] = json.dumps(fields[c], ensure_ascii=False)
        if table == "documents":
            fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE {table} SET {cols} WHERE {where}", (*fields.values(), *args))
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

    def avulsos_case(self) -> dict:
        """Caso padrão para PDFs enviados sem escolher um caso."""
        with self._lock:
            r = self._conn.execute("SELECT * FROM cases WHERE nome='Avulsos'").fetchone()
        return dict(r) if r else self.create_case("Avulsos")

    def list_cases(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT * FROM cases ORDER BY created_at DESC")]

    # ---- processos
    def upsert_process(self, case_id: str, numero: str, mcfly: Optional[dict]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO processes (id, case_id, numero, mcfly) VALUES (?,?,?,?) "
                "ON CONFLICT(case_id, numero) DO UPDATE SET mcfly=COALESCE(excluded.mcfly, processes.mcfly)",
                (uuid.uuid4().hex, case_id, numero, json.dumps(mcfly, ensure_ascii=False) if mcfly is not None else None))
            self._conn.commit()

    def get_process(self, case_id: str, numero: str) -> Optional[dict]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM processes WHERE case_id=? AND numero=?", (case_id, numero)).fetchone()
        return self._row(r) if r else None

    def list_processes(self, case_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM processes WHERE case_id=? ORDER BY rowid", (case_id,)).fetchall()
        return [self._row(r) for r in rows]

    def update_process(self, case_id: str, numero: str, **fields: Any) -> None:
        self._update("processes", "case_id=? AND numero=?", (case_id, numero), **fields)

    def mark_stale(self, case_id: str, numero: str) -> None:
        self.upsert_process(case_id, numero, None)
        with self._lock:
            self._conn.execute("UPDATE processes SET stale=1 WHERE case_id=? AND numero=?", (case_id, numero))
            self._conn.commit()

    def claim_extraction(self, case_id: str, numero: str) -> bool:
        """Reserva atômica: só um worker extrai cada processo."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE processes SET stale=2 WHERE case_id=? AND numero=? AND stale=1", (case_id, numero))
            self._conn.commit()
            return cur.rowcount == 1

    def finish_extraction(self, case_id: str, numero: str, **fields: Any) -> None:
        """Grava o resultado; se chegou material novo durante a extração (stale voltou a 1), continua desatualizado."""
        self._update("processes", "case_id=? AND numero=?", (case_id, numero), **fields)
        with self._lock:
            self._conn.execute("UPDATE processes SET stale=0 WHERE case_id=? AND numero=? AND stale=2", (case_id, numero))
            self._conn.commit()

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
        self._update("documents", "id=?", (doc_id,), **fields)

    def delete_doc(self, doc_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self._conn.commit()

    def get(self, doc_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, case_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            if case_id:
                rows = self._conn.execute("SELECT * FROM documents WHERE case_id=? ORDER BY created_at", (case_id,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def pending_docs(self, case_id: str, numero: Optional[str] = None) -> int:
        """PDFs do caso ainda na fila/processando. Com `numero`: só os que podem ser desse processo
        (mesmo nº ou nº ainda desconhecido); PDF de OUTRO processo não o segura."""
        sql = "SELECT COUNT(*) FROM documents WHERE case_id=? AND status IN ('queued','processing')"
        args: tuple = (case_id,)
        if numero is not None:
            sql += " AND (numero_processo IS NULL OR numero_processo=?)"
            args = (case_id, numero)
        with self._lock:
            return self._conn.execute(sql, args).fetchone()[0]

    def docs_for_process(self, case_id: str, numero: str) -> list[dict]:
        """Partes do processo, na ordem dos autos (volume, parte, página inicial)."""
        docs = [d for d in self.list(case_id) if d["numero_processo"] == numero and d["status"] == "done"]
        return sorted(docs, key=lambda d: (d["volume"] or 0, d["parte"] or 0, d["page_start"] or 0, d["created_at"]))
