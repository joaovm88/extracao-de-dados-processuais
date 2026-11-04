"""Worker de ingestão: OCR + texto nato-digital + quebra semântica + indexação + extração estruturada."""
import logging
import threading
from typing import Optional

from . import extract as ex
from .config import Settings
from .db import MetadataDB
from .llm import get_llm
from .ingest import citation, entities
from .ingest.chunker import chunk_page
from .ingest.pdf import iter_pages, page_count
from .schemas import NAO_ENCONTRADO, SHEETS, classify
from .search.base import SearchStore

log = logging.getLogger("worker")
CONTEXT_BUDGET = 400_000  # caracteres enviados ao LLM por processo (~100k tokens)


def _find_classe(db: MetadataDB, doc: dict) -> str:
    if doc.get("classe"):
        return doc["classe"]
    if doc.get("case_id") and doc.get("numero_processo"):
        for p in db.list_processes(doc["case_id"]):
            if p["numero"] == doc["numero_processo"]:
                return (p["mcfly"] or {}).get("classe") or ""
    return ""


def _resolve_numero(db: MetadataDB, doc: dict, meta: dict) -> Optional[str]:
    """Nº informado/do nome do arquivo; senão o mais citado no texto, priorizando os do cadastro (Mcfly) do caso."""
    if doc.get("numero_processo"):
        return doc["numero_processo"]
    cands = meta.get("numeros_processo") or []  # ordem de primeira aparição
    if doc.get("case_id"):
        known = {p["numero"] for p in db.list_processes(doc["case_id"])}
        cands = [c for c in cands if c["valor"] in known] or cands
    if not cands:
        return None
    return max(cands, key=lambda c: len(c["paginas"]))["valor"]  # empate: o que apareceu primeiro


def process_document(doc_id: str, settings: Settings, db: MetadataDB, store: SearchStore, llm_client=None) -> None:
    doc = db.get(doc_id)
    if not doc:
        return
    try:
        total = page_count(doc["path"])
        db.update(doc_id, status="processing", pages_total=total, pages_done=0, pages_ocr=0, error=None)
        store.delete(doc_id)  # reprocessamento idempotente
        start = doc.get("page_start") or 1
        vol = doc.get("volume")
        info = citation.parse_filename(doc["filename"])
        file_refs = {k: v for k, v in (("evento", doc.get("evento") or info.get("evento")),) if v}
        pages: list[dict] = []
        methods: dict[int, str] = {}
        batch: list = []
        ocr_pages = 0
        for pr in iter_pages(doc["path"], settings):
            absp = start + pr.number - 1  # página no volume/processo original
            refs = {**(file_refs), **citation.page_refs(pr.text)}
            pages.append({"page": absp, "text": pr.text, "refs": refs, "label": citation.label(vol, absp, refs)})
            methods[absp] = pr.method
            ocr_pages += pr.method == "ocr"
            batch.extend(chunk_page(absp, pr.text))
            if len(batch) >= 200:  # indexa em lotes: processos com milhares de páginas
                store.index(doc_id, batch)
                batch = []
            if pr.number % 10 == 0 or pr.number == total:
                db.update(doc_id, pages_done=pr.number, pages_ocr=ocr_pages)
        if batch:
            store.index(doc_id, batch)

        meta = entities.extract((p["page"], p["text"]) for p in pages)
        meta["paginas_sem_texto"] = sorted(n for n, m in methods.items() if m in ("ocr_unavailable", "empty"))
        meta["paginas_texto_degradado"] = sorted(n for n, m in methods.items() if m == "text_degraded")
        meta["metodo_por_pagina"] = {"texto": sum(m == "text" for m in methods.values()), "ocr": ocr_pages}
        numero = _resolve_numero(db, doc, meta)

        classe = _find_classe(db, doc)
        sheet_key, prompt = classify(classe)
        sheet = SHEETS[sheet_key]
        result = {"sheet": sheet_key, "prompt": prompt, "fields": {"digitalizado": "SIM"}, "bens": [], "fraudes": [],
                  "citacoes": {}, "revisar": [], "modo": "sem_llm"}
        llm = llm_client or get_llm(settings)
        if llm is not None:
            context = ex.select_context(pages, CONTEXT_BUDGET, store, doc_id)
            out = ex.extract_with_llm(sheet, prompt, context, llm)
            result["fields"].update({f.key: out.get(f.key) for f in sheet.ai_fields})
            result.update(bens=out["bens"], fraudes=out["fraudes"], citacoes=out["citacoes"],
                          revisar=out["_revisar"], modo="llm", provedor=llm.name)
        else:
            result["fields"]["observacoes"] = (
                f"{NAO_ENCONTRADO}: extração por IA indisponível (nenhuma chave de LLM configurada: GEMINI_API_KEY ou ANTHROPIC_API_KEY).")
        db.update(doc_id, status="done", pages_done=total, pages_ocr=ocr_pages, metadata=meta,
                  numero_processo=numero, classe=classe, sheet=sheet_key, result=result, reviewed=0)
    except Exception as e:  # noqa: BLE001
        log.exception("falha ao processar %s", doc_id)
        db.update(doc_id, status="error", error=str(e))


def run_worker(queue, settings: Settings, db: MetadataDB, store: SearchStore, stop: threading.Event, llm_client=None) -> None:
    queue.consume(lambda d: process_document(d, settings, db, store, llm_client), stop)
