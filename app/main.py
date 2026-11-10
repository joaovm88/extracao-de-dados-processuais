import hashlib
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import rag
from .config import Settings
from . import template as tpl
from . import view
from .export import build_case_xlsx
from .llm import get_llm
from .ingest.citation import parse_filename
from .mcfly import read_mcfly
from .schemas import SHEETS, SISTEMAS, TIPOS
from .services import make_db, make_queue, make_store
from .ingest import eventos as ev
from .ingest import pje
from . import APP_VERSION
from .worker import EXTRACT_PREFIX, READY_PREFIX, explain_llm_error, eventos_path, load_eventos, pages_path, run_worker

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ChatIn(BaseModel):
    question: str
    case_id: Optional[str] = None
    numero: Optional[str] = None  # processo (todas as partes); alternativa: doc_id de um PDF
    doc_id: Optional[str] = None


class TextIn(BaseModel):
    texto: str


class HiddenIn(BaseModel):
    oculto: bool


class ItensIn(BaseModel):
    lista: str  # "bens" | "fraudes"
    acao: str  # "autorizar" | "descartar" | "sugerir" | "editar"
    indices: Optional[list[int]] = None  # None = todos os que estão como sugestão
    valores: Optional[dict] = None  # só para "editar" (um índice)


class ManualIn(BaseModel):
    manual: bool
    nota: Optional[str] = None


class CaseIn(BaseModel):
    nome: str


class ReviewIn(BaseModel):
    fields: Optional[dict[str, Any]] = None
    observacoes: Optional[str] = None
    bens: Optional[list[dict]] = None
    fraudes: Optional[list[dict]] = None
    reviewed: bool = True


def create_app(settings: Settings | None = None, llm_client=None) -> FastAPI:
    s = settings or Settings()
    db, store, queue = make_db(s), make_store(s), make_queue(s)
    stop = threading.Event()
    llm = llm_client or get_llm(s)

    def recover_stuck_docs() -> int:
        """Devolve à fila os PDFs cujo pedido se perdeu (Docker reiniciado, worker caído). Roda na partida e a cada minuto.
        - 'queued' há mais de 90 s e AUSENTE da fila  -> reenfileira;
        - 'processing' sem andamento há mais de 10 min -> reenfileira;
        - fila em memória, na partida: tudo pendente é órfão (o worker reinicia junto)."""
        now, n = time.time(), 0
        for d in db.list():
            idade = now - (d["updated_at"] or 0)
            perdido = (d["status"] == "queued" and idade > 90 and not queue.contains(d["id"])) or \
                      (d["status"] == "processing" and idade > 600)
            if perdido:
                db.update(d["id"], status="queued", error=None)
                queue.enqueue(d["id"])
                n += 1
        for case_id, numero in db.release_stuck_extractions(1800):  # extração "rodando" há 30 min: o worker caiu, refaz
            queue.enqueue(f"{READY_PREFIX}{case_id}")
            n += 1
        return n

    def watchdog() -> None:
        while not stop.wait(60):
            try:
                if n := recover_stuck_docs():
                    logging.getLogger("api").warning("Recuperados %s PDF(s) que estavam parados na fila", n)
            except Exception:  # noqa: BLE001  o vigia nunca derruba a API
                logging.getLogger("api").exception("Falha no vigia da fila")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if s.queue_backend == "memory":  # o worker em memória reinicia junto com a API: tudo pendente é órfão
            for d in db.list():
                if d["status"] in ("queued", "processing"):
                    db.update(d["id"], status="queued", error=None)
                    queue.enqueue(d["id"])
        else:
            recover_stuck_docs()
        threading.Thread(target=watchdog, daemon=True).start()
        # Fila em memória: worker na mesma aplicação. Com Redis, rode `python -m app.run_worker`.
        if s.queue_backend == "memory":
            threading.Thread(target=run_worker, args=(queue, s, db, store, stop, llm), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Análise e Ingestão de Processos Judiciais", lifespan=lifespan)
    app.state.queue, app.state.db, app.state.store, app.state.recover = queue, db, store, recover_stuck_docs

    @app.exception_handler(Exception)
    async def unhandled(request, exc: Exception):
        """Erro inesperado: registra no log e devolve JSON legível (a tela mostra a causa, não só 'Erro')."""
        logging.getLogger("api").exception("Erro em %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"Erro interno ({type(exc).__name__}): {exc}"})

    @app.get("/api/health")
    def health():
        modelo = (s.gemini_model if llm and llm.name == "gemini" else s.llm_model if llm else None)
        return {"ok": True, "queue": s.queue_backend, "search": s.search_backend, "llm": llm.name if llm else None,
                "versao": APP_VERSION, "modelo": modelo, "reserva": s.gemini_fallback_model or None,
                "raciocinio": s.gemini_thinking if llm and llm.name == "gemini" else s.llm_effort if llm else None,
                "contexto_chars": s.context_budget_chars}

    @app.post("/api/format/observacoes")
    def format_observacoes(body: TextIn):
        """Deixa a cronologia como lista (um ato por linha), sem gastar IA."""
        from .extract import format_cronologia

        return {"texto": format_cronologia(body.texto) or ""}

    @app.post("/api/format/corresponsaveis")
    def format_corresponsaveis(body: TextIn):
        """Padroniza uma lista de corresponsáveis (uma pessoa/empresa por linha, com CPF/CNPJ), sem gastar IA."""
        from .extract import format_corresponsaveis as fmt

        return {"texto": fmt(body.texto) or ""}

    @app.get("/api/tipos")
    def tipos():
        campos = {k: {f.key: {"titulo": f.header.strip(), "opcoes": f.options} for f in sh.ai_fields} for k, sh in SHEETS.items()}
        return {"tipos": {k: v[0] for k, v in TIPOS.items()}, "sistemas": SISTEMAS, "campos": campos}

    # ---- casos (devedor)
    @app.post("/api/cases", status_code=201)
    def create_case(body: CaseIn):
        if not body.nome.strip():
            raise HTTPException(400, "Informe o nome do caso.")
        return db.create_case(body.nome.strip())

    @app.get("/api/cases")
    def list_cases():
        """Casos com um resumo (processos, PDFs, extraídos, com erro e última atividade) para a tela inicial."""
        out = []
        for c in db.list_cases():
            procs, docs = db.list_processes(c["id"]), db.list(c["id"])
            out.append({**c, "processos": len(procs), "pdfs": len(docs),
                        "extraidos": sum(1 for p in procs if p.get("result")),
                        "erros": sum(1 for p in procs if _estado(p) == "erro") + sum(1 for d in docs if d["status"] == "error"),
                        "em_andamento": sum(1 for d in docs if d["status"] in ("queued", "processing")) + sum(1 for p in procs if p.get("stale") == 2),
                        "atividade": max([d.get("updated_at") or 0 for d in docs] + [c.get("created_at") or 0])})
        return out

    @app.delete("/api/cases/{cid}")
    def delete_case(cid: str):
        """Exclui o caso inteiro: processos, PDFs (arquivos, páginas lidas, índice de busca) e planilha-modelo. Não tem volta."""
        _case(cid)
        docs = db.list(cid)
        for d in docs:
            _remove_doc(d)
        _tpl_path(cid).unlink(missing_ok=True)
        db.delete_case(cid)
        return {"status": "deleted", "documentos": len(docs)}

    def _case(cid: str) -> dict:
        c = db.get_case(cid)
        if not c:
            raise HTTPException(404, "Caso não encontrado.")
        return c

    def _estado(p: dict) -> str:
        if p.get("manual"):  # feito manualmente: vale mais que qualquer outro estado
            return "manual"
        if p.get("stale") == 2:  # em andamento ou à espera de nova tentativa: vale mais que um erro antigo
            return "extraindo"
        if p.get("stale") == 1:
            return "desatualizado" if p.get("reviewed") else "aguardando"
        if p.get("error") and not p.get("result"):
            return "erro"
        return "pronto" if p.get("result") else "sem_pdf"

    @app.get("/api/cases/{cid}")
    def get_case(cid: str):
        c = _case(cid)
        docs = [{k: v for k, v in d.items() if k != "metadata"} for d in db.list(cid)]
        procs = []
        for p in db.list_processes(cid):
            res = p.get("result")
            procs.append({**{k: p.get(k) for k in ("numero", "mcfly", "classe", "sheet", "sistema", "tipo", "reviewed", "stale", "error")},
                          "estado": _estado(p), "modelo": res["prompt"] if res else None,
                          "documentos": [d["id"] for d in docs if d["numero_processo"] == p["numero"]]})
        return {**c, "processos": procs, "documentos": docs}

    @app.get("/api/cases/{cid}/sheet")
    def case_sheet(cid: str):
        """Tudo que a 'planilha viva' precisa numa chamada: abas, colunas (do modelo, se houver) e uma linha por processo."""
        _case(cid)
        tp = _tpl_path(cid)
        return view.build(db.list_processes(cid), db.list(cid), tp.read_bytes() if tp.exists() else None, _estado)

    @app.get("/api/cases/{cid}/medicoes.pdf")
    def medicoes_pdf(cid: str, tz: int = 180):
        """Relatório em PDF das medições (tokens, tempos, cortes e registro de execução). `tz`: fuso do navegador (minutos)."""
        from .report import gerar_pdf

        c = _case(cid)
        tp = _tpl_path(cid)
        data = view.build(db.list_processes(cid), db.list(cid), tp.read_bytes() if tp.exists() else None, _estado)
        pdf = gerar_pdf(c["nome"], data["linhas"], data["documentos"], tz)
        nome = f'Medicoes - {c["nome"]}.pdf'.encode("latin-1", "replace").decode("latin-1").replace('"', "")
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{nome}"'})

    @app.get("/api/cases/{cid}/processes/{numero}/eventos")
    def process_events(cid: str, numero: str):
        """Linha do tempo fixa (eproc: páginas de separação; PJe: índice da capa e rodapés), sem IA."""
        if not db.get_process(cid, numero):
            raise HTTPException(404, "Processo não encontrado.")
        evs = load_eventos(s, db.docs_for_process(cid, numero))
        def item(e):
            if "evento" not in e:  # PJe: documento identificado pelo ID
                return {"evento": e["id"], "rotulo": f"ID {e['id']}", "data": e["data"], "hora": e["hora"], "movimento": e["movimento"],
                        "tipo_documento": e["tipo_documento"], "usuario": e["usuario"], "complemento": "", "docs": [],
                        "data_fmt": ev.fmt_data(e["data"]), "paginas": len(e["paginas"]), "relevancia": pje.relevancia(e), "linha": pje.linha_base(e)}
            return {**{k: e[k] for k in ("evento", "data", "hora", "movimento", "tipo_documento", "usuario", "complemento", "docs")},
                    "rotulo": f"Evento {e['evento']}", "data_fmt": ev.fmt_data(e["data"]), "paginas": len(e["paginas"]),
                    "relevancia": ev.relevancia(e), "linha": ev.linha_base(e)}
        return {"total": len(evs), "eventos": [item(e) for e in evs]}

    @app.get("/api/cases/{cid}/processes/{numero}")
    def get_process(cid: str, numero: str):
        p = db.get_process(cid, numero)
        if not p:
            raise HTTPException(404, "Processo não encontrado.")
        return {**p, "estado": _estado(p)}

    @app.put("/api/cases/{cid}/processes/{numero}/review")
    def review(cid: str, numero: str, body: ReviewIn):
        p = db.get_process(cid, numero)
        if not p or not p.get("result"):
            raise HTTPException(404, "Processo sem extração para revisar.")
        res = p["result"]
        sheet = SHEETS[res["sheet"]]
        _baseline(cid, numero, p)
        mudou: list[str] = []
        for k, v in (body.fields or {}).items():
            f = next((x for x in sheet.fields if x.key == k), None)
            if f is None:
                raise HTTPException(400, f"Campo desconhecido: {k}")
            if f.options and v is not None and v not in f.options:
                raise HTTPException(400, f"Valor inválido para '{f.header}'. Permitidos: {', '.join(f.options)}")
            if res["fields"].get(k) != v:
                mudou.append(f.header.split("(")[0].strip()[:40])
            res["fields"][k] = v
        if body.observacoes is not None:
            if (res["fields"].get("observacoes") or "") != body.observacoes:
                mudou.append("Observações")
            res["fields"]["observacoes"] = body.observacoes
            res.pop("cronologia", None)  # a conferência valia para o texto da IA; o analista passou a ser o autor
        if body.bens is not None:
            res["bens"] = body.bens
        if body.fraudes is not None:
            res["fraudes"] = body.fraudes
        if bool(p.get("reviewed")) != bool(body.reviewed):
            mudou.append("marcado como revisado" if body.reviewed else "voltou para “a revisar”")
        db.update_process(cid, numero, result=res, reviewed=int(body.reviewed))
        if mudou:
            db.add_history(cid, numero, "Edição do analista", "; ".join(mudou), {"result": res, "reviewed": int(body.reviewed)})
        return {"ok": True}

    def _baseline(cid: str, numero: str, p: dict) -> None:
        """Processos anteriores ao histórico: guarda o estado atual antes da primeira mudança, para dar para voltar."""
        if p.get("result") and not db.list_history(cid, numero):
            db.add_history(cid, numero, "Estado anterior", "como estava antes de o histórico existir",
                           {"result": p["result"], "reviewed": int(p.get("reviewed") or 0)})

    @app.get("/api/cases/{cid}/processes/{numero}/history")
    def history(cid: str, numero: str):
        if not db.get_process(cid, numero):
            raise HTTPException(404, "Processo não encontrado.")
        return {"versoes": db.list_history(cid, numero)}

    @app.get("/api/cases/{cid}/processes/{numero}/history/{hid}")
    def history_item(cid: str, numero: str, hid: str):
        h = db.get_history(cid, numero, hid)
        if not h:
            raise HTTPException(404, "Versão não encontrada.")
        res = h["snapshot"].get("result") or {}
        sheet = SHEETS.get(res.get("sheet") or "")
        titulos = {f.key: f.header for f in sheet.fields} if sheet else {}
        return {**{k: h[k] for k in ("id", "at", "origem", "resumo")}, "reviewed": bool(h["snapshot"].get("reviewed")),
                "campos": [{"titulo": titulos.get(k, k), "valor": v} for k, v in (res.get("fields") or {}).items() if v not in (None, "")],
                "bens": len(res.get("bens") or []), "fraudes": len(res.get("fraudes") or [])}

    @app.post("/api/cases/{cid}/processes/{numero}/history/{hid}/restore")
    def history_restore(cid: str, numero: str, hid: str):
        """Volta o processo a uma versão anterior (a versão atual continua no histórico)."""
        p = db.get_process(cid, numero)
        h = db.get_history(cid, numero, hid)
        if not p or not h or not h["snapshot"].get("result"):
            raise HTTPException(404, "Versão não encontrada.")
        if p.get("manual"):
            raise HTTPException(409, "Processo marcado como feito manualmente. Desfaça a marcação antes de restaurar uma versão.")
        _baseline(cid, numero, p)
        snap = h["snapshot"]
        db.update_process(cid, numero, result=snap["result"], reviewed=int(snap.get("reviewed") or 0), error=None)
        quando = time.strftime("%d/%m/%Y %H:%M", time.localtime(h["at"]))
        db.add_history(cid, numero, "Restauração", f"voltou à versão “{h['origem']}” de {quando}", snap)
        return {"ok": True}

    @app.put("/api/cases/{cid}/processes/{numero}/itens")
    def itens(cid: str, numero: str, body: ItensIn):
        """Outros Bens / Alegações de Fraude: a IA sugere, o analista autoriza (só o autorizado vai para as abas e a planilha)."""
        from .extract import normalize_fraude
        from .schemas import FRAUDE_ANALISES, FRAUDE_TIPOS, item_status

        p = db.get_process(cid, numero)
        if not p or not p.get("result"):
            raise HTTPException(404, "Processo sem extração.")
        if body.lista not in ("bens", "fraudes") or body.acao not in ("autorizar", "descartar", "sugerir", "editar"):
            raise HTTPException(400, "Use lista 'bens' ou 'fraudes' e ação 'autorizar', 'descartar', 'sugerir' ou 'editar'.")
        res = p["result"]
        lista = res.get(body.lista) or []
        idx = body.indices if body.indices is not None else [i for i, it in enumerate(lista) if item_status(it) == "sugerido"]
        if any(i < 0 or i >= len(lista) for i in idx):
            raise HTTPException(400, "Item inexistente.")
        _baseline(cid, numero, p)
        if body.acao == "editar":
            if len(idx) != 1 or not body.valores:
                raise HTTPException(400, "Para editar, informe um item e os valores.")
            permitidos = ("tipo", "analise", "valor_causa") if body.lista == "fraudes" else (
                "matricula", "caracteristicas", "proprietario", "ato", "gravames", "resumo", "situacao")
            novo = {k: (str(v).strip() or None) if v is not None else None for k, v in body.valores.items() if k in permitidos}
            if body.lista == "fraudes":
                if novo.get("tipo") and novo["tipo"] not in FRAUDE_TIPOS:
                    raise HTTPException(400, f"Tipo de fraude inválido. Permitidos: {', '.join(FRAUDE_TIPOS)}")
                if novo.get("analise") and novo["analise"] not in FRAUDE_ANALISES:
                    raise HTTPException(400, f"Análise inválida. Permitidos: {', '.join(FRAUDE_ANALISES)}")
            lista[idx[0]] = {**lista[idx[0]], **novo}
            resumo = "item editado"
        else:
            st = {"autorizar": "autorizado", "descartar": "descartado", "sugerir": "sugerido"}[body.acao]
            for i in idx:
                lista[i] = {**(normalize_fraude(lista[i]) if body.lista == "fraudes" else lista[i]), "_st": st}
            resumo = f"{len(idx)} item(ns): {st}"
        res[body.lista] = lista
        db.update_process(cid, numero, result=res)
        nome = "Outros Bens" if body.lista == "bens" else "Alegações de Fraude"
        if idx:
            db.add_history(cid, numero, f"{nome}", resumo, {"result": res, "reviewed": int(p.get("reviewed") or 0)})
        return {"ok": True, "alterados": len(idx)}

    @app.put("/api/cases/{cid}/processes/{numero}/hidden")
    def set_hidden(cid: str, numero: str, body: HiddenIn):
        """Oculta (ou reexibe) um processo: ele sai da tabela e da planilha exportada, sem apagar nada."""
        if not db.get_process(cid, numero):
            raise HTTPException(404, "Processo não encontrado.")
        db.update_process(cid, numero, hidden=int(body.oculto))
        if not body.oculto:
            queue.enqueue(f"{READY_PREFIX}{cid}")  # se havia material novo esperando, extrai agora
        return {"ok": True, "oculto": body.oculto}

    @app.delete("/api/cases/{cid}/processes/{numero}")
    def delete_process(cid: str, numero: str):
        """Exclui o processo do caso, com os seus PDFs e o histórico. Não tem volta. Se o processo constar da planilha-modelo,
        ele volta (vazio) quando o modelo for enviado de novo."""
        if not db.get_process(cid, numero):
            raise HTTPException(404, "Processo não encontrado.")
        docs = [d for d in db.list(cid) if d["numero_processo"] == numero]
        for d in docs:
            _remove_doc(d)
        db.delete_process(cid, numero)
        return {"status": "deleted", "documentos": len(docs)}

    @app.put("/api/cases/{cid}/processes/{numero}/manual")
    def set_manual(cid: str, numero: str, body: ManualIn):
        """Marca (ou desmarca) o processo como feito manualmente: a IA deixa de extraí-lo e a planilha registra isso."""
        p = db.get_process(cid, numero)
        if not p:
            raise HTTPException(404, "Processo não encontrado.")
        if body.manual:
            db.update_process(cid, numero, manual=1, manual_note=(body.nota or "").strip() or None, manual_at=time.time(),
                              error=None, stale=0)
        else:
            db.update_process(cid, numero, manual=0, manual_note=None, manual_at=None)
            if db.docs_for_process(cid, numero) and not p.get("result"):  # volta ao fluxo normal: extrai o que já foi enviado
                db.mark_stale(cid, numero)
                queue.enqueue(f"{READY_PREFIX}{cid}")
        return {"ok": True, "manual": body.manual}

    @app.post("/api/cases/{cid}/processes/{numero}/reextract", status_code=202)
    def reextract(cid: str, numero: str):
        """Refaz a extração juntando todas as partes (descarta edições da revisão)."""
        p0 = db.get_process(cid, numero)
        if not p0:
            raise HTTPException(404, "Processo não encontrado.")
        if p0.get("manual"):
            raise HTTPException(409, "Este processo está marcado como feito manualmente. Desfaça a marcação para extrair com a IA.")
        db.update_process(cid, numero, reviewed=0, error=None)  # o erro antigo não fica na tela enquanto refaz
        db.mark_stale(cid, numero)
        queue.enqueue(f"{EXTRACT_PREFIX}{cid}|{numero}")
        return {"status": "queued"}

    @app.post("/api/cases/{cid}/mcfly")
    async def import_mcfly(cid: str, file: UploadFile = File(...)):
        _case(cid)
        import io

        try:
            rows = read_mcfly(io.BytesIO(await file.read()))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Planilha inválida: {e}")
        for r in rows:
            db.upsert_process(cid, r["numero"], r)
        return {"importados": len(rows)}

    def _tpl_path(cid: str) -> Path:
        d = s.data_dir / "templates"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{cid}.xlsx"

    @app.post("/api/cases/{cid}/template")
    async def upload_template(cid: str, file: UploadFile = File(...)):
        """Planilha-modelo do caso: o relatório final é uma cópia dela, preenchida."""
        _case(cid)
        data = await file.read()
        try:
            analysis = tpl.analyze(data)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Planilha inválida: {e}")
        _tpl_path(cid).write_bytes(data)
        try:  # os processos do caso vêm da própria planilha (aba Mcfly ou Relatório)
            rows = tpl.registry(data)
        except Exception:  # noqa: BLE001
            rows = []
        for r in rows:
            db.upsert_process(cid, r["numero"], r)
        return {**analysis, "processos_importados": len(rows)}

    @app.get("/api/cases/{cid}/template")
    def get_template(cid: str):
        _case(cid)
        p = _tpl_path(cid)
        return {"ativo": p.exists(), **(tpl.analyze(p.read_bytes()) if p.exists() else {})}

    @app.delete("/api/cases/{cid}/template")
    def delete_template(cid: str):
        _case(cid)
        _tpl_path(cid).unlink(missing_ok=True)
        return {"ativo": False}

    @app.get("/api/cases/{cid}/spreadsheet")
    def case_spreadsheet(cid: str):
        c = _case(cid)
        procs = db.list_processes(cid)
        data = tpl.fill(_tpl_path(cid).read_bytes(), procs) if _tpl_path(cid).exists() else build_case_xlsx(c, procs)
        return Response(data, media_type=XLSX,
                        headers={"Content-Disposition": f'attachment; filename="Relatorio - {c["nome"]}.xlsx"'.encode("latin-1", "replace").decode("latin-1")})

    # ---- documentos
    @app.post("/api/documents", status_code=202)
    async def upload(file: UploadFile = File(...), case_id: Optional[str] = Form(None),
                     volume: Optional[int] = Form(None), pagina_inicial: Optional[int] = Form(None),
                     numero_processo: Optional[str] = Form(None), tipo: Optional[str] = Form(None),
                     sistema: Optional[str] = Form(None)):
        tipo, sistema = tipo or None, sistema or None  # "" = automático
        if tipo and tipo not in TIPOS:
            raise HTTPException(400, f"Tipo inválido. Use: {', '.join(TIPOS)}")
        if sistema and sistema not in SISTEMAS:
            raise HTTPException(400, f"Sistema inválido. Use: {', '.join(SISTEMAS)}")
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Envie um arquivo PDF.")
        case_id = _case(case_id)["id"] if case_id else db.avulsos_case()["id"]
        doc_id = uuid.uuid4().hex
        dest = s.upload_dir / f"{doc_id}.pdf"
        size, limit = 0, s.max_upload_mb * 1024 * 1024
        sha = hashlib.sha256()
        with dest.open("wb") as out:  # grava em streaming, sem carregar tudo em memória
            while chunk := await file.read(1024 * 1024):
                sha.update(chunk)
                size += len(chunk)
                if size > limit:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, f"Arquivo maior que {s.max_upload_mb} MB.")
                out.write(chunk)
        with dest.open("rb") as f:
            head = f.read(5)
        if head != b"%PDF-":
            dest.unlink(missing_ok=True)
            raise HTTPException(400, "Arquivo não é um PDF válido.")
        info = parse_filename(Path(file.filename).name)  # nº CNJ, volume e página inicial vêm do nome do arquivo
        vol = volume if volume is not None else info["volume"]
        ini = pagina_inicial or info["pagina_inicial"] or 1
        num = numero_processo or info["numero_processo"]
        digest = sha.hexdigest()
        try:
            for old in db.list(case_id):  # mesmo arquivo (conteúdo) ou mesmo trecho reenviado: substitui, não duplica
                same_part = (num and old["numero_processo"] == num and old["volume"] == vol
                             and old["page_start"] == ini and old["parte"] == info["parte"])
                if old.get("sha256") == digest or same_part:
                    _remove_doc(old)
            db.create(doc_id, Path(file.filename).name, str(dest), case_id,
                      numero_processo=num, volume=vol, evento=info["evento"], parte=info["parte"], tipo=tipo,
                      sistema=sistema, page_start=ini, sha256=digest)
            queue.enqueue(doc_id)
        except Exception:
            # nunca deixar um registro "na fila" sem estar de fato na fila (travava o caso inteiro)
            db.delete_doc(doc_id)
            dest.unlink(missing_ok=True)
            raise
        return {"id": doc_id, "status": "queued", **info}

    def _remove_doc(d: dict) -> None:
        store.delete(d["id"])
        db.delete_doc(d["id"])
        Path(d["path"]).unlink(missing_ok=True)
        pages_path(s, d["id"]).unlink(missing_ok=True)
        eventos_path(s, d["id"]).unlink(missing_ok=True)

    @app.delete("/api/documents/{doc_id}", status_code=202)
    def delete_doc(doc_id: str):
        """Exclui um PDF (ex.: duplicado ou travado). O caso é destravado e o processo, reextraído se preciso."""
        d = db.get(doc_id)
        if not d:
            raise HTTPException(404, "Documento não encontrado.")
        _remove_doc(d)
        if d["case_id"]:
            if d["numero_processo"] and db.get_process(d["case_id"], d["numero_processo"]):
                db.update_process(d["case_id"], d["numero_processo"], error=None)  # o erro era deste material: sai junto com o PDF
                db.mark_stale(d["case_id"], d["numero_processo"])
            queue.enqueue(f"{READY_PREFIX}{d['case_id']}")
        return {"status": "deleted"}

    @app.get("/api/documents")
    def list_docs(case_id: Optional[str] = None):
        return [{k: v for k, v in d.items() if k not in ("metadata", "result")} for d in db.list(case_id)]

    @app.post("/api/documents/{doc_id}/retry", status_code=202)
    def retry(doc_id: str):
        """Reprocessa um PDF que deu erro ou travou."""
        d = db.get(doc_id)
        if not d:
            raise HTTPException(404, "Documento não encontrado.")
        db.update(doc_id, status="queued", error=None, pages_done=0, pages_ocr=0)
        queue.enqueue(doc_id)
        return {"status": "queued"}

    @app.get("/api/documents/{doc_id}")
    def get_doc(doc_id: str):
        d = db.get(doc_id)
        if not d:
            raise HTTPException(404, "Documento não encontrado.")
        return d

    @app.post("/api/chat")
    def chat(body: ChatIn):
        if body.numero:
            docs = db.docs_for_process(_case(body.case_id)["id"], body.numero) if body.case_id else []
            if not docs:
                raise HTTPException(404, "Processo sem PDFs processados.")
        elif body.doc_id:
            d = db.get(body.doc_id)
            if not d:
                raise HTTPException(404, "Documento não encontrado.")
            if d["status"] != "done":
                raise HTTPException(409, "O documento ainda está sendo processado.")
            docs = [d]
        else:
            docs = []
        try:
            return rag.answer(body.question, docs, s, store, llm)
        except Exception as e:  # noqa: BLE001  falha do provedor de IA: mensagem clara, sem 'Erro interno'
            from .llm import _is_transient

            raise HTTPException(503 if _is_transient(e) else 502, explain_llm_error(e))

    static = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="static")

    @app.middleware("http")
    async def no_cache_static(request: Request, call_next):
        resp = await call_next(request)
        if not request.url.path.startswith("/api"):  # a tela sempre revalida: nada de JavaScript antigo preso no navegador
            resp.headers["Cache-Control"] = "no-cache"
        return resp
    return app


app = create_app()
