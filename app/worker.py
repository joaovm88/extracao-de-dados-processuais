"""Worker: ingestão de cada PDF (OCR, chunking, indexação) e extração estruturada POR PROCESSO.

Um processo pode vir em várias partes (volumes, 'PARTE_2'…). Cada PDF é ingerido isoladamente; quando
todos os PDFs do caso terminam, cada processo com material novo é extraído uma única vez, juntando as partes."""
import json
import logging
import threading
import time
from typing import Optional

from . import extract as ex
from .config import Settings
from .db import MetadataDB
from .ingest import citation, entities
from .ingest import eventos as ev
from .ingest import pje
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
    elif "504" in low or "deadline" in low or "timed out" in low or "timeout" in low:
        dica = ("O Gemini não respondeu a tempo (504). O processo é grande ou o serviço está lento. Tente novamente; se repetir, "
                "use um modelo mais rápido ou reduza o texto enviado (CONTEXT_BUDGET_CHARS).")
    elif "503" in low or "unavailable" in low or "overloaded" in low:
        dica = "O serviço do Gemini está indisponível ou lento no momento. Tente novamente em alguns minutos."
    elif "json" in low:
        dica = "A IA devolveu uma resposta fora do formato esperado. Tente novamente."
    else:
        dica = "Falha na chamada à IA."
    return f"{dica} Detalhe técnico: {raw[:400]}"


def pages_path(settings: Settings, doc_id: str):
    d = settings.data_dir / "pages"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{doc_id}.json"


def eventos_path(settings: Settings, doc_id: str):
    d = settings.data_dir / "eventos"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{doc_id}.json"


def load_eventos(settings: Settings, docs: list[dict]) -> list[dict]:
    """Linha do tempo do processo: eventos do eproc ou documentos do PJe (um registro por ID), de todas as partes."""
    listas = []
    for d in docs:
        p = eventos_path(settings, d["id"])
        if p.exists():
            listas.append(json.loads(p.read_text(encoding="utf-8")))
    if not listas:
        return []
    if any(r.get("id") and "evento" not in r for l in listas for r in l):
        return pje.merge_docs(listas)
    return ev.merge_events(listas)


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
    t_wall = time.time()
    try:
        t_ini = time.perf_counter()
        total = page_count(doc["path"])
        db.update(doc_id, status="processing", pages_total=total, pages_done=0, pages_ocr=0, error=None,
                  started_at=t_wall, finished_at=None)
        store.delete(doc_id)  # reprocessamento idempotente
        start = doc.get("page_start") or 1
        vol = doc.get("volume")
        info = citation.parse_filename(doc["filename"])
        file_refs = {k: v for k, v in (("evento", doc.get("evento") or info.get("evento")),) if v}
        last_refs: dict = {}
        pages: list[dict] = []
        methods: dict[int, str] = {}
        batch: list = []
        ocr_pages, ocr_s, texto_s = 0, 0.0, 0.0
        for pr in iter_pages(doc["path"], settings):
            absp = start + pr.number - 1  # página no volume/processo original
            own = citation.page_refs(pr.text)
            if "evento" in own or "id" in own:  # marcador novo: vale daqui em diante
                last_refs = {k: v for k, v in own.items() if k in ("evento", "id")}
            refs = {**file_refs, **last_refs, **own}  # páginas sem marcador herdam o ato anterior
            pages.append({"page": absp, "text": pr.text, "refs": refs})
            methods[absp] = pr.method
            ocr_pages += pr.method == "ocr"
            if pr.method == "ocr":
                ocr_s += pr.seconds
            else:
                texto_s += pr.seconds
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
        meta["tempos"] = {"total_s": round(time.perf_counter() - t_ini, 1), "ocr_s": round(ocr_s, 1), "ocr_paginas": ocr_pages,
                          "texto_s": round(texto_s, 1), "paginas": total}
        meta["metodo_por_pagina"] = {"texto": sum(m == "text" for m in methods.values()), "ocr": ocr_pages}
        numero = _resolve_numero(db, doc, meta)
        classe = doc.get("classe") or next(
            (c for c in (citation.classe_from_text(p["text"]) for p in pages[:6]) if c), "")
        pages_path(settings, doc_id).write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
        eventos_doc = (ev.extract_events(pages, doc_id) if sistema == "eproc"
                       else pje.extract_docs(pages, doc_id) if sistema == "pje" else [])  # linha do tempo fixa, lida do próprio PDF
        eventos_path(settings, doc_id).write_text(json.dumps(eventos_doc, ensure_ascii=False), encoding="utf-8")
        meta["n_eventos"] = len(eventos_doc)
        db.update(doc_id, status="done", finished_at=time.time(), pages_done=total, pages_ocr=ocr_pages, metadata=meta,
                  numero_processo=numero, classe=classe, sistema=sistema)
        if numero:
            db.mark_stale(doc["case_id"], numero)
    except Exception as e:  # noqa: BLE001
        log.exception("falha ao processar %s", doc_id)
        db.update(doc_id, status="error", error=str(e), finished_at=time.time(), started_at=doc.get("started_at") or t_wall)
    finally:
        extract_ready(doc["case_id"], settings, db, store, llm_client)


def extract_ready(case_id: str, settings: Settings, db: MetadataDB, store: SearchStore, llm_client=None) -> None:
    """Extrai cada processo com material novo quando não há mais PDFs DELE em andamento (uma vez só).
    Um PDF parado na fila segura só o próprio processo (e PDFs de nº ainda desconhecido), não o caso inteiro."""
    if not case_id:
        return
    for proc in db.list_processes(case_id):
        if db.pending_docs(case_id, proc["numero"]):
            continue
        if proc.get("stale") == 1 and not proc.get("reviewed"):  # processo já revisado só reextrai sob pedido
            extract_process(case_id, proc["numero"], settings, db, store, llm_client)


def extract_process(case_id: str, numero: str, settings: Settings, db: MetadataDB, store: SearchStore,
                    llm_client=None) -> None:
    if not db.claim_extraction(case_id, numero):
        return
    # nova tentativa: o erro anterior sai de cena e o registro de início/fim recomeça
    db.update_process(case_id, numero, error=None, ext_started_at=time.time(), ext_finished_at=None, ext_status="rodando")
    try:
        docs = db.docs_for_process(case_id, numero)
        if not docs:  # todos os PDFs do processo foram excluídos
            db.finish_extraction(case_id, numero, error=None, ext_started_at=None, ext_finished_at=None, ext_status=None)
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
            evs = load_eventos(settings, docs)
            usar_eventos = sistema in ("eproc", "pje") and sum(1 for e in evs if e["data"]) >= 3  # digitalizados sem sistema: contexto por páginas
            if usar_eventos and sistema == "pje":
                context, info = ex.select_context_pje(pages, evs, settings.context_budget_chars)
            elif usar_eventos:
                context, info = ex.select_context_eventos(pages, evs, settings.context_budget_chars)
            else:
                context, info = ex.select_context_info(pages, settings.context_budget_chars, store, [d["id"] for d in docs])
            t_ia = time.perf_counter()
            out = ex.extract_with_llm(sheet, prompt, context, llm, sistema, com_indice=usar_eventos)
            result["medidas"] = {"contexto": info, "tokens": getattr(llm, "last_usage", None),
                                 "extracao_s": round(time.perf_counter() - t_ia, 1),
                                 "chamada": getattr(llm, "last_call", None)}
            result["fields"].update({f.key: out.get(f.key) for f in sheet.ai_fields})
            result.update(bens=out["bens"], fraudes=out["fraudes"], citacoes=out["citacoes"],
                          revisar=out["_revisar"], modo="llm", provedor=llm.name)
            if usar_eventos:  # confere a cronologia da IA com os eventos reais: data do eproc, ordem e eventos inexistentes
                obs, crono = (pje if sistema == "pje" else ev).validar_cronologia(result["fields"].get("observacoes"), evs)
                result["fields"]["observacoes"] = obs
                result["cronologia"] = {**crono, "eventos_base": len(evs)}
            texts = [str(v) for v in result["fields"].values() if v] + list(result["citacoes"].values())
            texts += [str(v) for b in result["bens"] + result["fraudes"] for v in b.values() if v]
            result["citacoes_invalidas"] = ex.check_citations(texts, pages, sistema)
        else:
            result["fields"]["observacoes"] = (
                f"{NAO_ENCONTRADO}: extração por IA indisponível (nenhuma chave de LLM configurada: GEMINI_API_KEY ou ANTHROPIC_API_KEY).")
        db.finish_extraction(case_id, numero, result=result, classe=classe, sheet=sheet_key, sistema=sistema,
                             reviewed=0, error=None, ext_finished_at=time.time(), ext_status="ok")
    except Exception as e:  # noqa: BLE001
        log.exception("falha na extração de %s", numero)
        db.finish_extraction(case_id, numero, error=explain_llm_error(e), ext_finished_at=time.time(), ext_status="erro")


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
