"""Worker de ingestão: OCR + texto nato-digital + quebra semântica + indexação + extração de dados."""
import logging
import threading

from .config import Settings
from .db import MetadataDB
from .ingest import entities
from .ingest.chunker import chunk_page
from .ingest.pdf import iter_pages, page_count
from .search.base import SearchStore

log = logging.getLogger("worker")


def process_document(doc_id: str, settings: Settings, db: MetadataDB, store: SearchStore) -> None:
    doc = db.get(doc_id)
    if not doc:
        return
    try:
        total = page_count(doc["path"])
        db.update(doc_id, status="processing", pages_total=total, pages_done=0, pages_ocr=0, error=None)
        store.delete(doc_id)  # reprocessamento idempotente
        texts: list[tuple[int, str]] = []
        methods: dict[int, str] = {}
        batch: list = []
        ocr_pages = 0
        for pr in iter_pages(doc["path"], settings):
            texts.append((pr.number, pr.text))
            methods[pr.number] = pr.method
            ocr_pages += pr.method == "ocr"
            batch.extend(chunk_page(pr.number, pr.text))
            if len(batch) >= 200:  # indexa em lotes: processos com milhares de páginas
                store.index(doc_id, batch)
                batch = []
            if pr.number % 10 == 0 or pr.number == total:
                db.update(doc_id, pages_done=pr.number, pages_ocr=ocr_pages)
        if batch:
            store.index(doc_id, batch)
        meta = entities.extract(texts)
        meta["paginas_sem_texto"] = sorted(n for n, m in methods.items() if m in ("ocr_unavailable", "empty"))
        meta["metodo_por_pagina"] = {"texto": sum(m == "text" for m in methods.values()), "ocr": ocr_pages}
        db.update(doc_id, status="done", pages_done=total, pages_ocr=ocr_pages, metadata=meta)
    except Exception as e:  # noqa: BLE001
        log.exception("falha ao processar %s", doc_id)
        db.update(doc_id, status="error", error=str(e))


def run_worker(queue, settings: Settings, db: MetadataDB, store: SearchStore, stop: threading.Event) -> None:
    queue.consume(lambda d: process_document(d, settings, db, store), stop)
