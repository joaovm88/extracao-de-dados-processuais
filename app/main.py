import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import rag
from .config import Settings
from .services import make_db, make_queue, make_store
from .spreadsheet import build_xlsx
from .worker import run_worker


class ChatIn(BaseModel):
    question: str
    doc_id: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings()
    db, store, queue = make_db(s), make_store(s), make_queue(s)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Fila em memória: worker na mesma aplicação. Com Redis, rode `python -m app.run_worker`.
        if s.queue_backend == "memory":
            threading.Thread(target=run_worker, args=(queue, s, db, store, stop), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Análise e Ingestão de Processos Judiciais", lifespan=lifespan)
    app.state.queue, app.state.db, app.state.store = queue, db, store

    @app.get("/api/health")
    def health():
        return {"ok": True, "queue": s.queue_backend, "search": s.search_backend,
                "llm": bool(s.anthropic_api_key)}

    @app.post("/api/documents", status_code=202)
    async def upload(file: UploadFile = File(...)):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Envie um arquivo PDF.")
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
        db.create(doc_id, Path(file.filename).name, str(dest))
        queue.enqueue(doc_id)
        return {"id": doc_id, "status": "queued"}

    @app.get("/api/documents")
    def list_docs():
        return [{k: v for k, v in d.items() if k != "metadata"} for d in db.list()]

    @app.get("/api/documents/{doc_id}")
    def get_doc(doc_id: str):
        d = db.get(doc_id)
        if not d:
            raise HTTPException(404, "Documento não encontrado.")
        return d

    @app.get("/api/documents/{doc_id}/spreadsheet")
    def spreadsheet(doc_id: str):
        d = db.get(doc_id)
        if not d:
            raise HTTPException(404, "Documento não encontrado.")
        if d["status"] != "done":
            raise HTTPException(409, f"Processamento ainda não concluído (status: {d['status']}).")
        name = Path(d["filename"]).stem + ".xlsx"
        return Response(
            build_xlsx(d),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/chat")
    def chat(body: ChatIn):
        if body.doc_id:
            d = db.get(body.doc_id)
            if not d:
                raise HTTPException(404, "Documento não encontrado.")
            if d["status"] != "done":
                raise HTTPException(409, "O documento ainda está sendo processado.")
        return rag.answer(body.question, body.doc_id, s, db, store)

    static = Path(__file__).resolve().parent.parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


app = create_app()
