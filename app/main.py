import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import rag
from .config import Settings
from .export import build_case_xlsx
from .llm import get_llm
from .ingest.citation import parse_filename
from .mcfly import read_mcfly
from .schemas import SHEETS
from .services import make_db, make_queue, make_store
from .worker import run_worker

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ChatIn(BaseModel):
    question: str
    doc_id: Optional[str] = None


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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Fila em memória: worker na mesma aplicação. Com Redis, rode `python -m app.run_worker`.
        if s.queue_backend == "memory":
            threading.Thread(target=run_worker, args=(queue, s, db, store, stop, llm), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Análise e Ingestão de Processos Judiciais", lifespan=lifespan)
    app.state.queue, app.state.db, app.state.store = queue, db, store

    @app.get("/api/health")
    def health():
        return {"ok": True, "queue": s.queue_backend, "search": s.search_backend, "llm": llm.name if llm else None}

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

    @app.get("/api/cases/{cid}")
    def get_case(cid: str):
        c = _case(cid)
        return {**c, "processos": db.list_processes(cid),
                "documentos": [{k: v for k, v in d.items() if k not in ("metadata",)} for d in db.list(cid)]}

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

    @app.get("/api/cases/{cid}/spreadsheet")
    def case_spreadsheet(cid: str):
        c = _case(cid)
        data = build_case_xlsx(c, db.list_processes(cid), db.list(cid))
        return Response(data, media_type=XLSX,
                        headers={"Content-Disposition": f'attachment; filename="Relatorio - {c["nome"]}.xlsx"'.encode("latin-1", "replace").decode("latin-1")})

    # ---- documentos
    @app.post("/api/documents", status_code=202)
    async def upload(file: UploadFile = File(...), case_id: Optional[str] = Form(None),
                     volume: Optional[int] = Form(None), pagina_inicial: Optional[int] = Form(None),
                     numero_processo: Optional[str] = Form(None)):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Envie um arquivo PDF.")
        if case_id:
            _case(case_id)
        doc_id = uuid.uuid4().hex
        dest = s.upload_dir / f"{doc_id}.pdf"
        size, limit = 0, s.max_upload_mb * 1024 * 1024
        with dest.open("wb") as out:  # grava em streaming, sem carregar tudo em memória
            while chunk := await file.read(1024 * 1024):
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
        db.create(doc_id, Path(file.filename).name, str(dest), case_id,
                  numero_processo=numero_processo or info["numero_processo"],
                  volume=volume if volume is not None else info["volume"], evento=info["evento"],
                  page_start=pagina_inicial or info["pagina_inicial"] or 1)
        queue.enqueue(doc_id)
        return {"id": doc_id, "status": "queued", **info}

    @app.get("/api/documents")
    def list_docs(case_id: Optional[str] = None):
        return [{k: v for k, v in d.items() if k not in ("metadata", "result")} for d in db.list(case_id)]

    @app.get("/api/documents/{doc_id}")
    def get_doc(doc_id: str):
        d = db.get(doc_id)
        if not d:
            raise HTTPException(404, "Documento não encontrado.")
        return d

    @app.put("/api/documents/{doc_id}/review")
    def review(doc_id: str, body: ReviewIn):
        d = db.get(doc_id)
        if not d or not d.get("result"):
            raise HTTPException(404, "Documento sem extração para revisar.")
        res = d["result"]
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
        db.update(doc_id, result=res, reviewed=int(body.reviewed))
        return {"ok": True}

    @app.post("/api/chat")
    def chat(body: ChatIn):
        if body.doc_id:
            d = db.get(body.doc_id)
            if not d:
                raise HTTPException(404, "Documento não encontrado.")
            if d["status"] != "done":
                raise HTTPException(409, "O documento ainda está sendo processado.")
        return rag.answer(body.question, body.doc_id, s, db, store, llm)

    static = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


app = create_app()
