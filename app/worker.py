"""Worker: ingestão de cada PDF (OCR, chunking, indexação) e extração estruturada POR PROCESSO.

Um processo pode vir em várias partes (volumes, 'PARTE_2'…). Cada PDF é ingerido isoladamente; quando
todos os PDFs do caso terminam, cada processo com material novo é extraído uma única vez, juntando as partes."""
import json
import logging
import threading
from typing import Optional

from . import extract as ex
from .config import Settings
from .db import MetadataDB
from .ingest import citation, entities
from .ingest.chunker import chunk_page
from .ingest.pdf import iter_pages, page_count
from .llm import get_llm
from .schemas import NAO_ENCONTRADO, SHEETS, resolve_tipo
from .search.base import SearchStore

log = logging.getLogger("worker")
EXTRACT_PREFIX = "extract:"
READY_PREFIX = "ready:"


def explain_llm_error(e: Exception) -> str:
    """Traduz falhas comuns da API de IA em orientação para o analista (a mensagem original segue junto)."""
    raw = f"{type(e).__name__}: {e}"
    low = raw.lower()
    if "429" in low or "resource_exhausted" in low or "quota" in low or "rate limit" in low:
        dica = ("Limite de uso do Gemini atingido (comum no nível gratuito, com processos grandes). Aguarde alguns "
                "minutos e use 'Tentar novamente', ou troque GEMINI_MODEL por um modelo mais leve (veja os disponíveis no README).")
    elif "api key" in low or "api_key" in low or "401" in low or "403" in low or "permission_denied" in low or "unauthenticated" in low:
        dica = "Chave do Gemini inválida, expirada ou sem permissão. Confira GEMINI_API_KEY no arquivo .env."
    elif "404" in low or "not_found" in low or "is not found" in low or "not supported" in low:
        dica = ("Modelo do Gemini indisponível para a sua conta (modelos antigos saem do ar para contas novas). Ajuste GEMINI_MODEL "
                "no .env para um modelo listado no AI Studio, ou liste os disponíveis com o comando do README, e reinicie com docker compose up.")
    elif "503" in low or "unavailable" in low or "overloaded" in low or "timeout" in low or "deadline" in low:
        dica = "O serviço do Gemini está indisponível ou lento no momento. Tente novamente em alguns minutos."
    elif "json" in low:
        dica = "A IA devolveu uma resposta fora do formato esperado. Tente novamente."
    else:
        dica = "Falha na extração por IA."
    return f"{dica} Detalhe técnico: {raw[:400]}"


def pages_path(settings: Settings, doc_id: str):
    d = settings.data_dir / "pages"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{doc_id}.json"


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
    """Ingere um PDF. A extração do processo só roda quando o caso não tem mais PDFs na fila."""
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
        last_refs: dict = {}
        pages: list[dict] = []
        methods: dict[int, str] = {}
        batch: list = []
        ocr_pages = 0
        for pr in iter_pages(doc["path"], settings):
            absp = start + pr.number - 1  # página no volume/processo original
            own = citation.page_refs(pr.text)
            if "evento" in own or "id" in own:  # marcador novo: vale daqui em diante
                last_refs = {k: v for k, v in own.items() if k in ("evento", "id")}
            refs = {**file_refs, **last_refs, **own}  # páginas sem marcador herdam o ato anterior
            pages.append({"page": absp, "text": pr.text, "refs": refs})
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

        sistema = doc.get("sistema") or citation.detect_system([p["refs"] for p in pages], [p["text"] for p in pages[:20]])
        for p in pages:
            p["label"] = citation.label(vol, p["page"], p["refs"], sistema)
        meta = entities.extract((p["page"], p["text"]) for p in pages)
        meta["paginas_sem_texto"] = sorted(n for n, m in methods.items() if m in ("ocr_unavailable", "empty"))
        meta["paginas_texto_degradado"] = sorted(n for n, m in methods.items() if m == "text_degraded")
        meta["rotulos"] = {str(p["page"]): p["label"] for p in pages}
        meta["metodo_por_pagina"] = {"texto": sum(m == "text" for m in methods.values()), "ocr": ocr_pages}
        numero = _resolve_numero(db, doc, meta)
        classe = doc.get("classe") or next(
            (c for c in (citation.classe_from_text(p["text"]) for p in pages[:6]) if c), "")
        pages_path(settings, doc_id).write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
        db.update(doc_id, status="done", pages_done=total, pages_ocr=ocr_pages, metadata=meta,
                  numero_processo=numero, classe=classe, sistema=sistema)
        if numero:
            db.mark_stale(doc["case_id"], numero)
    except Exception as e:  # noqa: BLE001
        log.exception("falha ao processar %s", doc_id)
        db.update(doc_id, status="error", error=str(e))
    finally:
        extract_ready(doc["case_id"], settings, db, store, llm_client)


def extract_ready(case_id: str, settings: Settings, db: MetadataDB, store: SearchStore, llm_client=None) -> None:
    """Quando não há mais PDFs do caso em andamento, extrai cada processo com material novo (uma vez só)."""
    if not case_id or db.pending_docs(case_id):
        return
    for proc in db.list_processes(case_id):
        if proc.get("stale") == 1 and not proc.get("reviewed"):  # processo já revisado só reextrai sob pedido
            extract_process(case_id, proc["numero"], settings, db, store, llm_client)


def extract_process(case_id: str, numero: str, settings: Settings, db: MetadataDB, store: SearchStore,
                    llm_client=None) -> None:
    if not db.claim_extraction(case_id, numero):
        return
    try:
        docs = db.docs_for_process(case_id, numero)
        if not docs:  # todos os PDFs do processo foram excluídos
            db.finish_extraction(case_id, numero, error=None)
            return
        pages: list[dict] = []
        for d in docs:  # partes na ordem dos autos
            for p in json.loads(pages_path(settings, d["id"]).read_text(encoding="utf-8")):
                pages.append({**p, "doc_id": d["id"]})
        proc = db.get_process(case_id, numero) or {}
        classe = (proc.get("mcfly") or {}).get("classe") or next((d["classe"] for d in docs if d.get("classe")), "")
        tipo = proc.get("tipo") or next((d["tipo"] for d in docs if d.get("tipo")), None)
        sistema = next((d["sistema"] for d in docs if d.get("sistema")), None)
        sheet_key, prompt = resolve_tipo(tipo, classe)
        sheet = SHEETS[sheet_key]
        result = {"sheet": sheet_key, "prompt": prompt, "fields": {"digitalizado": "SIM"}, "bens": [], "fraudes": [],
                  "citacoes": {}, "revisar": [], "modo": "sem_llm", "sistema": sistema, "citacoes_invalidas": [],
                  "documentos": [d["filename"] for d in docs], "paginas": len(pages)}
        llm = llm_client or get_llm(settings)
        if llm is not None:
            context = ex.select_context(pages, settings.context_budget_chars, store, [d["id"] for d in docs])
            out = ex.extract_with_llm(sheet, prompt, context, llm, sistema)
            result["fields"].update({f.key: out.get(f.key) for f in sheet.ai_fields})
            result.update(bens=out["bens"], fraudes=out["fraudes"], citacoes=out["citacoes"],
                          revisar=out["_revisar"], modo="llm", provedor=llm.name)
            texts = [str(v) for v in result["fields"].values() if v] + list(result["citacoes"].values())
            texts += [str(v) for b in result["bens"] + result["fraudes"] for v in b.values() if v]
            result["citacoes_invalidas"] = ex.check_citations(texts, pages, sistema)
        else:
            result["fields"]["observacoes"] = (
                f"{NAO_ENCONTRADO}: extração por IA indisponível (nenhuma chave de LLM configurada: GEMINI_API_KEY ou ANTHROPIC_API_KEY).")
        db.finish_extraction(case_id, numero, result=result, classe=classe, sheet=sheet_key, sistema=sistema,
                             reviewed=0, error=None)
    except Exception as e:  # noqa: BLE001
        log.exception("falha na extração de %s", numero)
        db.finish_extraction(case_id, numero, error=explain_llm_error(e))


def handle_job(job: str, settings: Settings, db: MetadataDB, store: SearchStore, llm_client=None) -> None:
    if job.startswith(EXTRACT_PREFIX):  # reextração sob pedido: "extract:<case_id>|<numero>"
        case_id, numero = job[len(EXTRACT_PREFIX):].split("|", 1)
        extract_process(case_id, numero, settings, db, store, llm_client)
    elif job.startswith(READY_PREFIX):  # destrava o caso (ex.: PDF excluído) e extrai o que estiver pendente
        extract_ready(job[len(READY_PREFIX):], settings, db, store, llm_client)
    else:
        process_document(job, settings, db, store, llm_client)


def run_worker(queue, settings: Settings, db: MetadataDB, store: SearchStore, stop: threading.Event, llm_client=None) -> None:
    queue.consume(lambda j: handle_job(j, settings, db, store, llm_client), stop)
