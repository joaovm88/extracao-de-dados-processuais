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


def explain_pdf_error(e: Exception) -> str:
    """Falha ao ler um PDF, em linguagem do analista (o detalhe técnico vai junto, sem o caminho interno do arquivo)."""
    import re as _re

    raw = _re.sub(r"'[^']*[/\\]([^'/\\]+)'", r"'\1'", f"{type(e).__name__}: {e}")[:300]
    low = raw.lower()
    if "failed to open" in low or "cannot open" in low or "no objects found" in low or "broken" in low or "format error" in low:
        dica = "Não foi possível abrir este PDF: o arquivo está corrompido ou incompleto (download interrompido). Baixe-o de novo no sistema do tribunal e reenvie."
    elif "password" in low or "encrypt" in low or "senha" in low:
        dica = "Este PDF está protegido por senha. Gere uma cópia sem senha e reenvie."
    elif "tesseract" in low:
        dica = "O OCR (Tesseract) não está disponível para ler este PDF digitalizado. Confira a instalação do Tesseract."
    elif "memory" in low:
        dica = "Faltou memória para ler este PDF. Tente dividi-lo em partes menores."
    else:
        dica = "Não foi possível ler este PDF. Tente “Reprocessar”; se repetir, baixe o arquivo de novo e reenvie."
    return f"{dica} Detalhe técnico: {raw}"


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
        db.update(doc_id, status="error", error=explain_pdf_error(e), finished_at=time.time(), started_at=doc.get("started_at") or t_wall)
    finally:
        extract_ready(doc["case_id"], settings, db, store, llm_client)


def extract_ready(case_id: str, settings: Settings, db: MetadataDB, store: SearchStore, llm_client=None) -> None:
    """Extrai cada processo com material novo quando não há mais PDFs DELE em andamento (uma vez só).
    Um PDF parado na fila segura só o próprio processo (e PDFs de nº ainda desconhecido), não o caso inteiro."""
    if not case_id:
        return
    for proc in db.list_processes(case_id):
        if db.pending_docs(case_id, proc["numero"]) or proc.get("manual") or proc.get("hidden"):  # manual/oculto: a IA não mexe
            continue
        if proc.get("stale") == 1 and not proc.get("reviewed"):  # processo já revisado só reextrai sob pedido
            extract_process(case_id, proc["numero"], settings, db, store, llm_client)


def _extrair_rj(case_id, numero, docs, pages, proc, prompt, sistema, result, llm, settings, db, zero, classe, sheet_key) -> None:
    """RJ: cada documento (ou bloco dele) vai à IA sozinho, com o que ele costuma conter; o código confere as origens contra o texto das
    páginas e junta os resultados. Com relatório já existente, só os documentos NOVOS são lidos e o relatório é complementado."""
    from . import rj

    ant = proc.get("result") or {}
    antigo = None if zero else ant.get("relatorio")
    ja = set(ant.get("rj_docs") or []) if antigo else set()
    novos = [d for d in docs if d["id"] not in ja]
    por_doc = {d["id"]: [p for p in pages if p["doc_id"] == d["id"]] for d in docs}
    novos.sort(key=lambda d: (rj.data_documento(por_doc[d["id"]]), d.get("created_at") or 0))
    agora = time.time()
    if antigo and not novos:  # nada novo desde a última vez: não gasta IA
        ant["rj_merge"] = {**(ant.get("rj_merge") or {}), "sem_novos": True, "em": agora, "complemento": True}
        db.finish_extraction(case_id, numero, result=ant, ext_finished_at=agora, ext_status="ok", error=None)
        return
    rel, acum = antigo, {"preenchidos": 0, "atualizados": 0, "linhas_novas": 0, "mantidos_por_edicao": 0}
    anc = {"confirmados": 0, "corrigidos": 0, "nao_localizados": 0}
    t_ia, chamadas, enviadas, car_env, tok_in, tok_out = time.perf_counter(), 0, 0, 0, 0, 0
    plano = [(d, pc) for d in novos for pc in rj.pedacos(por_doc[d["id"]], settings.rj_chunk_chars)]
    for i, (d, pedaco) in enumerate(plano, 1):
        if True:
            db.update_process(case_id, numero, ext_progress=f"lendo {i} de {len(plano)}: {d['filename']}")  # a tela mostra o andamento
            ctx = "\n\n".join(f"=== [{p['label']}] ===\n{p['text']}" for p in pedaco)
            out = ex.extract_rj(prompt, ctx, llm, sistema, False, None, rj.dica_documento(d["filename"], pedaco[0]["text"]))
            for k, v in rj.ancorar(out, pedaco).items():
                anc[k] += v
            u = getattr(llm, "last_usage", None) or {}
            tok_in += u.get("entrada") or 0
            tok_out += (u.get("saida") or 0) + (u.get("raciocinio") or 0)
            if rel is None:
                rel = out
            else:
                rel, mi = rj.mesclar(rel, out)
                for k in acum:
                    acum[k] += mi[k]
            chamadas += 1
            enviadas += len(pedaco)
            car_env += len(ctx)
    rel.setdefault("_editados", [])
    result.update(fields={}, relatorio=rel, modo="llm", provedor=llm.name, rj_docs=sorted(ja | {d["id"] for d in novos}),
                  rj_merge={**acum, **anc, "em": agora, "documentos": len(novos), "chamadas": chamadas, "complemento": bool(antigo)})
    result["medidas"] = {"contexto": {"modo": "rj_por_documento", "documentos": len(novos), "chamadas": chamadas, "paginas_total": len(pages), "paginas_enviadas": enviadas,
                                      "paginas_omitidas": max(len(pages) - enviadas, 0), "caracteres_total": sum(len(p["text"]) for p in pages),
                                      "caracteres_enviados": car_env, "orcamento": settings.rj_chunk_chars, "cortado": False},
                         "tokens": {"entrada": tok_in, "saida": tok_out, "raciocinio": None}, "extracao_s": round(time.perf_counter() - t_ia, 1),
                         "chamada": getattr(llm, "last_call", None)}
    result["citacoes_invalidas"] = ex.check_citations(rj.textos(rel), pages, sistema)
    resumo = (f"{len(novos)} documento(s) em {chamadas} chamada(s); origens: {anc['confirmados']} confirmadas, {anc['corrigidos']} corrigidas, "
              f"{anc['nao_localizados']} não localizadas" + (f"; {acum['preenchidos']} campo(s) preenchido(s), {acum['atualizados']} atualizado(s), {acum['linhas_novas']} linha(s) nova(s)" if antigo else ""))
    db.finish_extraction(case_id, numero, result=result, classe=classe, sheet=sheet_key, sistema=sistema, reviewed=0, error=None,
                         ext_finished_at=time.time(), ext_status="ok")
    db.add_history(case_id, numero, "Complemento pela IA" if antigo else "Extração pela IA", "relatório de RJ · " + resumo, {"result": result, "reviewed": 0})


def extract_process(case_id: str, numero: str, settings: Settings, db: MetadataDB, store: SearchStore,
                    llm_client=None, zero: bool = False) -> None:
    if (db.get_process(case_id, numero) or {}).get("manual"):
        return
    if not db.claim_extraction(case_id, numero):
        return
    # nova tentativa: o erro anterior sai de cena e o registro de início/fim recomeça
    db.update_process(case_id, numero, error=None, ext_started_at=time.time(), ext_finished_at=None, ext_status="rodando", ext_progress="preparando a leitura")
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
        sheet = SHEETS.get(sheet_key)  # None: Recuperação Judicial (relatório próprio, sem planilha)
        result = {"sheet": sheet_key, "prompt": prompt, "fields": {"digitalizado": "SIM"}, "bens": [], "fraudes": [],
                  "citacoes": {}, "revisar": [], "modo": "sem_llm", "sistema": sistema, "citacoes_invalidas": [],
                  "documentos": [d["filename"] for d in docs], "paginas": len(pages)}
        llm = llm_client or get_llm(settings)
        if llm is not None and sheet is None:  # Recuperação Judicial: um documento por vez, com as origens conferidas pelo código
            _extrair_rj(case_id, numero, docs, pages, proc, prompt, sistema, result, llm, settings, db, zero, classe, sheet_key)
            return
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
            result["fields"]["observacoes"] = ex.format_cronologia(result["fields"].get("observacoes"))  # um ato por linha
            result.update(bens=out["bens"], fraudes=out["fraudes"], citacoes=out["citacoes"],
                          revisar=out["_revisar"], modo="llm", provedor=llm.name)
            if usar_eventos:  # confere a cronologia da IA com os eventos reais: data do eproc, ordem e eventos inexistentes
                obs, crono = (pje if sistema == "pje" else ev).validar_cronologia(result["fields"].get("observacoes"), evs)
                result["fields"]["observacoes"] = obs
                result["cronologia"] = {**crono, "eventos_base": len(evs)}
                result["fields"]["observacoes"] = ex.format_cronologia(obs)  # devolve a linha em branco entre os atos
            texts = [str(v) for v in result["fields"].values() if v] + list(result["citacoes"].values())
            texts += [str(v) for b in result["bens"] + result["fraudes"] for v in b.values() if v]
            result["citacoes_invalidas"] = ex.check_citations(texts, pages, sistema)
        elif sheet is None:
            raise RuntimeError("O relatório de Recuperação Judicial precisa da IA: configure GEMINI_API_KEY ou ANTHROPIC_API_KEY.")
        else:
            result["fields"]["observacoes"] = (
                f"{NAO_ENCONTRADO}: extração por IA indisponível (nenhuma chave de LLM configurada: GEMINI_API_KEY ou ANTHROPIC_API_KEY).")
        db.finish_extraction(case_id, numero, result=result, classe=classe, sheet=sheet_key, sistema=sistema,
                             reviewed=0, error=None, ext_finished_at=time.time(), ext_status="ok")
        db.add_history(case_id, numero, "Extração pela IA", f"{len(pages)} página(s) lidas", {"result": result, "reviewed": 0})
    except Exception as e:  # noqa: BLE001
        log.exception("falha na extração de %s", numero)
        db.finish_extraction(case_id, numero, error=explain_llm_error(e), ext_finished_at=time.time(), ext_status="erro")


def handle_job(job: str, settings: Settings, db: MetadataDB, store: SearchStore, llm_client=None) -> None:
    if job.startswith(EXTRACT_PREFIX):  # reextração sob pedido: "extract:<case_id>|<numero>"
        partes = job[len(EXTRACT_PREFIX):].split("|")  # "<case_id>|<numero>[|zero]": zero = refazer do zero (RJ)
        extract_process(partes[0], partes[1], settings, db, store, llm_client, zero=len(partes) > 2 and partes[2] == "zero")
    elif job.startswith(READY_PREFIX):  # destrava o caso (ex.: PDF excluído) e extrai o que estiver pendente
        extract_ready(job[len(READY_PREFIX):], settings, db, store, llm_client)
    else:
        process_document(job, settings, db, store, llm_client)


def run_worker(queue, settings: Settings, db: MetadataDB, store: SearchStore, stop: threading.Event, llm_client=None) -> None:
    queue.consume(lambda j: handle_job(j, settings, db, store, llm_client), stop)
