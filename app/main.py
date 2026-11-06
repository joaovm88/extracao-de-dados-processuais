# app/main.py
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Dict, Any

from app.template import CORPORATE_COLUMNS, map_extraction_to_row
from app import db, services, export, rag
from app.schemas import ChatRequest, ChatResponse

app = FastAPI(
    title="Extração Processual & Copiloto RAG",
    description="Workspace jurídico para preenchimento automático de planilha corporativa e análise via RAG.",
    version="2.0.0",
)

# Monta a pasta estática (HTML5, CSS Grid e Vanilla JS)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    """Entrega a interface Single Page Application (SPA) leve."""
    return FileResponse("static/index.html")


@app.get("/api/template/columns")
async def get_template_schema():
    """
    Fornece ao Front-end as colunas oficiais da planilha da empresa.
    Assim, se você mudar uma coluna no Python (template.py), a tabela HTML se adapta sozinha!
    """
    return {"columns": list(CORPORATE_COLUMNS)}


@app.get("/api/processes")
async def list_processes():
    """Lista todos os processos já ingeridos, formatados para a tabela corporativa."""
    raw_processes = db.get_all_processes()
    rows = [map_extraction_to_row(p) for p in raw_processes]
    return {"processes": rows}


@app.post("/api/processes/upload")
async def upload_process(
    file: UploadFile = File(...),
    category: str = Form("execucoes_fiscais")  # Conecta com prompts/execucoes_fiscais.md
):
    """Recebe o PDF, registra no banco e despacha para o Worker assíncrono."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Envie um arquivo com extensão .pdf válido.")

    process_record = await services.ingest_pdf_upload(file=file, category=category)
    return map_extraction_to_row(process_record)


@app.patch("/api/processes/{process_id}/fields")
async def update_process_field(process_id: str, payload: Dict[str, str]):
    """
    Permite que o analista edite qualquer célula diretamente na tabela da tela (inline edit)
    antes de baixar a planilha Excel final.
    """
    updated = db.update_extracted_fields(process_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Processo não encontrado.")
    return map_extraction_to_row(updated)


@app.get("/api/processes/export/xlsx")
async def export_spreadsheet():
    """Gera e baixa a planilha corporativa preenchida (.xlsx)."""
    filepath = export.build_corporate_excel()
    return FileResponse(
        path=filepath,
        filename="planilha_corporativa_processos.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/processes/{process_id}/chat", response_model=ChatResponse)
async def chat_with_process(process_id: str, request: ChatRequest):
    """Consulta o motor RAG (Elasticsearch/Memória) sobre o processo ativo."""
    answer_data = rag.ask_question(process_id=process_id, question=request.question)
    return answer_data