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
from .worker import EXTRACT_PREFIX, READY_PREFIX, explain_llm_error, pages_path, run_worker

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ChatIn(BaseModel):
    question: str
    case_id: Optional[str] = None
    numero: Optional[str] = None  # processo (todas as partes); alternativa: doc_id de um PDF
    doc_id: Optional[str] = None


class TextIn(BaseModel):
    texto: str


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

    def recover_stuck_docs() -> None:
        """PDFs que ficaram 'queued/processing' porque o Docker foi parado no meio voltam para a fila.
        Fila em memória: tudo pendente é órfão (o worker reinicia junto). Redis: só 'processing' parado há >10 min."""
        now = time.time()
        for d in db.list():
            orphan = (d["status"] in ("queued", "processing") and s.queue_backend == "memory") or \
                     (d["status"] == "processing" and now - (d["updated_at"] or 0) > 600)
            if orphan:
                db.update(d["id"], status="queued", error=None)
                queue.enqueue(d["id"])

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        recover_stuck_docs()
        # Fila em memória: worker na mesma aplicação. Com Redis, rode `python -m app.run_worker`.
        if s.queue_backend == "memory":
            threading.Thread(target=run_worker, args=(queue, s, db, store, stop, llm), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Análise e Ingestão de Processos Judiciais", lifespan=lifespan)
    app.state.queue, app.state.db, app.state.store = queue, db, store

    @app.exception_handler(Exception)
    async def unhandled(request, exc: Exception):
        """Erro inesperado: registra no log e devolve JSON legível (a tela mostra a causa, não só 'Erro')."""
        logging.getLogger("api").exception("Erro em %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"Erro interno ({type(exc).__name__}): {exc}"})

    @app.get("/api/health")
    def health():
        return {"ok": True, "queue": s.queue_backend, "search": s.search_backend, "llm": llm.name if llm else None}

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
        return db.list_cases()

    def _case(cid: str) -> dict:
        c = db.get_case(cid)
        if not c:
            raise HTTPException(404, "Caso não encontrado.")
        return c

    def _estado(p: dict) -> str:
        if p.get("error") and not p.get("result"):
            return "erro"
        if p.get("stale") == 2:
            return "extraindo"
        if p.get("stale") == 1:
            return "desatualizado" if p.get("reviewed") else "aguardando"
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
        for k, v in (body.fields or {}).items():
            f = next((x for x in sheet.fields if x.key == k), None)
            if f is None:
                raise HTTPException(400, f"Campo desconhecido: {k}")
            if f.options and v is not None and v not in f.options:
                raise HTTPException(400, f"Valor inválido para '{f.header}'. Permitidos: {', '.join(f.options)}")
            res["fields"][k] = v
        if body.observacoes is not None:
            res["fields"]["observacoes"] = body.observacoes
        if body.bens is not None:
            res["bens"] = body.bens
        if body.fraudes is not None:
            res["fraudes"] = body.fraudes
        db.update_process(cid, numero, result=res, reviewed=int(body.reviewed))
        return {"ok": True}

    @app.post("/api/cases/{cid}/processes/{numero}/reextract", status_code=202)
    def reextract(cid: str, numero: str):
        """Refaz a extração juntando todas as partes (descarta edições da revisão)."""
        if not db.get_process(cid, numero):
            raise HTTPException(404, "Processo não encontrado.")
        db.update_process(cid, numero, reviewed=0)
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

    @app.delete("/api/documents/{doc_id}", status_code=202)
    def delete_doc(doc_id: str):
        """Exclui um PDF (ex.: duplicado ou travado). O caso é destravado e o processo, reextraído se preciso."""
        d = db.get(doc_id)
        if not d:
            raise HTTPException(404, "Documento não encontrado.")
        _remove_doc(d)
        if d["case_id"]:
            if d["numero_processo"] and db.get_process(d["case_id"], d["numero_processo"]):
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
    return app


app = create_app()
