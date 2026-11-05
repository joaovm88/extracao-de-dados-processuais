import io
import time

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.config import Settings
from app.ingest import entities
from app.ingest.chunker import chunk_page
from app.main import create_app
from tests.make_pdf import make_pdf


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUEUE_BACKEND", "memory")
    monkeypatch.setenv("SEARCH_BACKEND", "memory")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with TestClient(create_app(Settings())) as c:
        yield c


def _upload_and_wait(client, tmp_path):
    pdf = tmp_path / "proc.pdf"
    make_pdf(pdf)
    with pdf.open("rb") as f:
        r = client.post("/api/documents", files={"file": ("proc.pdf", f, "application/pdf")})
    assert r.status_code == 202
    doc_id = r.json()["id"]
    for _ in range(100):
        d = client.get(f"/api/documents/{doc_id}").json()
        if d["status"] in ("done", "error"):
            return d
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_entities():
    assert entities.valid_cnpj("11.222.333/0001-81")
    assert not entities.valid_cnpj("11.222.333/0001-82")
    assert entities.valid_cpf("529.982.247-25")
    assert entities.parse_brl("1.250,40") == 1250.40
    assert entities.categorize("bloqueado via SISBAJUD") == "Bloqueio SISBAJUD"


def test_chunker_keeps_page():
    chunks = chunk_page(7, "Parágrafo um.\n\n" + "Frase longa. " * 200)
    assert all(c.page == 7 for c in chunks) and len(chunks) > 1


def test_full_pipeline(client, tmp_path):
    d = _upload_and_wait(client, tmp_path)
    assert d["status"] == "done", d.get("error")
    m = d["metadata"]
    assert d["pages_total"] == 4
    assert m["numero_processo"] == "1234567-89.2023.8.26.0100"
    assert m["cnpjs"][0]["valido"] is True
    assert {p["papel"] for p in m["partes"]} >= {"Exequente", "Executado"}
    sis = [v for v in m["valores"] if v["categoria"] == "Bloqueio SISBAJUD"]
    assert sis and sis[0]["valor"] == 48320.15 and sis[0]["pagina"] == 2
    assert any(v["categoria"] == "DARF / Tributo" and v["valor"] == 1250.40 for v in m["valores"])

    r = client.post("/api/chat", json={"question": "Existe valor bloqueado pelo SISBAJUD?", "doc_id": d["id"]}).json()
    assert "48.320,15" in r["answer"] and "pg. 2" in r["answer"]
    assert 2 in [s["page"] for s in r["sources"]]


def test_rejects_non_pdf(client, tmp_path):
    r = client.post("/api/documents", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400
    r = client.post("/api/documents", files={"file": ("x.txt", b"x", "text/plain")})
    assert r.status_code == 400


def test_unknown_resources(client):
    assert client.get("/api/documents/nope").status_code == 404
    assert client.get("/api/cases/nope/spreadsheet").status_code == 404


def test_image_page_goes_through_ocr(tmp_path, monkeypatch):
    from app.ingest import pdf

    monkeypatch.setattr(pdf, "ocr_available", lambda: True)
    monkeypatch.setattr(pdf, "_ocr_page", lambda page, s: "DARF pago R$ 77,00 CNPJ 11.222.333/0001-81")
    p = tmp_path / "a.pdf"
    make_pdf(p)
    pages = list(pdf.iter_pages(str(p), Settings()))
    assert [x.method for x in pages] == ["text", "text", "text", "ocr"]
    assert "77,00" in pages[3].text


def test_image_page_without_tesseract_is_flagged(tmp_path, monkeypatch):
    from app.ingest import pdf

    monkeypatch.setattr(pdf, "ocr_available", lambda: False)
    p = tmp_path / "a.pdf"
    make_pdf(p)
    assert list(pdf.iter_pages(str(p), Settings()))[3].method == "ocr_unavailable"


def test_unexpected_error_returns_readable_json(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    app = create_app(Settings())
    monkeypatch.setattr(app.state.db, "create", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("banco indisponível")))
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/documents", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 500 and "banco indisponível" in r.json()["detail"]


def test_old_database_schema_is_migrated(tmp_path):
    """Banco criado por versão antiga (sem tipo/sistema/evento…) não pode quebrar o envio de PDFs."""
    import sqlite3

    from app.db import MetadataDB

    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT NOT NULL, path TEXT NOT NULL, status TEXT NOT NULL,
                                pages_total INTEGER DEFAULT 0, pages_done INTEGER DEFAULT 0, pages_ocr INTEGER DEFAULT 0,
                                error TEXT, metadata TEXT, created_at REAL, updated_at REAL);
        CREATE TABLE processes (id TEXT PRIMARY KEY, case_id TEXT NOT NULL, numero TEXT NOT NULL, mcfly TEXT,
                                UNIQUE(case_id, numero));
        INSERT INTO documents (id, filename, path, status) VALUES ('velho', 'a.pdf', '/x', 'done');
    """)
    con.commit()
    con.close()
    db = MetadataDB(path)
    case = db.create_case("c")
    db.create("novo", "b.pdf", "/y", case["id"], tipo="execucao_fiscal", sistema="eproc", parte=2, evento="14",
              numero_processo="1", volume=1, page_start=3)
    d = db.get("novo")
    assert (d["tipo"], d["sistema"], d["parte"], d["evento"]) == ("execucao_fiscal", "eproc", 2, "14")
    assert db.get("velho")["status"] == "done"  # dados antigos preservados
    db.mark_stale(case["id"], "1")  # tabela processes também migrada
    assert db.get_process(case["id"], "1")["stale"] == 1


def test_stuck_documents_are_recovered_on_startup_and_can_be_retried(tmp_path, monkeypatch):
    """Docker parado no meio do processamento deixava PDFs 'processing' para sempre e o caso nunca era extraído."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("QUEUE_BACKEND", "memory")
    monkeypatch.setenv("SEARCH_BACKEND", "memory")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    s = Settings()
    app = create_app(s)
    pdf = tmp_path / "x.pdf"
    make_pdf(pdf, image_page=False)
    case = app.state.db.create_case("c")
    app.state.db.create("preso", "x.pdf", str(pdf), case["id"], numero_processo="1234567-89.2023.8.26.0100")
    app.state.db.update("preso", status="processing")  # worker morreu aqui
    with TestClient(app) as c:
        for _ in range(100):
            d = c.get("/api/documents/preso").json()
            if d["status"] == "done":
                break
            time.sleep(0.1)
        assert d["status"] == "done"
        # reprocessar manualmente um PDF com erro
        app.state.db.update("preso", status="error", error="falha antiga")
        assert c.post("/api/documents/preso/retry").status_code == 202
        for _ in range(100):
            d = c.get("/api/documents/preso").json()
            if d["status"] == "done":
                break
            time.sleep(0.1)
        assert d["status"] == "done" and d["error"] is None
        assert c.post("/api/documents/nope/retry").status_code == 404


def _env_app(tmp_path, monkeypatch, llm=None):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("QUEUE_BACKEND", "memory")
    monkeypatch.setenv("SEARCH_BACKEND", "memory")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    return create_app(Settings(), llm_client=llm)


def test_deleting_a_stuck_pdf_unblocks_the_case_and_triggers_extraction(tmp_path, monkeypatch):
    app = _env_app(tmp_path, monkeypatch)
    pdf = tmp_path / "x.pdf"
    make_pdf(pdf, image_page=False)
    case = app.state.db.create_case("c")
    # PDF "na fila" órfão (resto de upload que falhou) bloqueava o caso
    app.state.db.create("orfao", "x.pdf", str(pdf), case["id"])
    with TestClient(app) as c:
        app.state.db.update("orfao", status="queued")
        with pdf.open("rb") as f:
            up = c.post("/api/documents", data={"case_id": case["id"]}, files={"file": ("y.pdf", f, "application/pdf")}).json()
        for _ in range(100):
            if c.get(f"/api/documents/{up['id']}").json()["status"] == "done":
                break
            time.sleep(0.1)
        time.sleep(0.3)
        num = c.get(f"/api/documents/{up['id']}").json()["numero_processo"]
        assert c.get(f"/api/cases/{case['id']}/processes/{num}").json()["estado"] in ("aguardando", "pronto")
        assert c.delete("/api/documents/orfao").status_code == 202
        for _ in range(100):
            p = c.get(f"/api/cases/{case['id']}/processes/{num}").json()
            if p["estado"] == "pronto":
                break
            time.sleep(0.1)
        assert p["estado"] == "pronto" and p["result"]["modo"] == "sem_llm"
        assert c.delete("/api/documents/nope").status_code == 404


def test_failed_upload_leaves_no_orphan_row_and_same_content_is_not_duplicated(tmp_path, monkeypatch):
    app = _env_app(tmp_path, monkeypatch)
    pdf = tmp_path / "x.pdf"
    make_pdf(pdf, image_page=False)
    case = app.state.db.create_case("c")
    with TestClient(app, raise_server_exceptions=False) as c:
        original = app.state.queue.enqueue
        app.state.queue.enqueue = lambda *a: (_ for _ in ()).throw(RuntimeError("redis fora"))
        with pdf.open("rb") as f:
            r = c.post("/api/documents", data={"case_id": case["id"]}, files={"file": ("a.pdf", f, "application/pdf")})
        assert r.status_code == 500 and app.state.db.list(case["id"]) == []  # sem registro órfão
        app.state.queue.enqueue = original
        for nome in ("a.pdf", "a_compressed.pdf"):  # mesmo conteúdo, nomes diferentes
            with pdf.open("rb") as f:
                assert c.post("/api/documents", data={"case_id": case["id"]}, files={"file": (nome, f, "application/pdf")}).status_code == 202
        assert len(app.state.db.list(case["id"])) == 1


def test_llm_errors_are_explained_to_the_analyst():
    from app.worker import explain_llm_error

    assert "Limite de uso" in explain_llm_error(RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"))
    assert "GEMINI_API_KEY" in explain_llm_error(RuntimeError("403 PERMISSION_DENIED: API key not valid"))
    assert "GEMINI_MODEL" in explain_llm_error(RuntimeError("404 NOT_FOUND: models/xyz is not found"))
    assert "indisponível" in explain_llm_error(RuntimeError("503 UNAVAILABLE"))
    assert "Detalhe técnico" in explain_llm_error(ValueError("outra coisa"))


def test_failed_extraction_shows_reason_and_can_be_retried(tmp_path, monkeypatch):
    class Quebra:
        name = "q"
        def extract(self, *a): raise RuntimeError("429 RESOURCE_EXHAUSTED")
        def generate(self, *a, **k): return ""

    app = _env_app(tmp_path, monkeypatch, llm=Quebra())
    pdf = tmp_path / "x.pdf"
    make_pdf(pdf, image_page=False)
    case = app.state.db.create_case("c")
    with TestClient(app) as c:
        with pdf.open("rb") as f:
            up = c.post("/api/documents", data={"case_id": case["id"]}, files={"file": ("x.pdf", f, "application/pdf")}).json()
        for _ in range(150):
            cs = c.get(f"/api/cases/{case['id']}").json()
            if cs["processos"] and cs["processos"][0]["estado"] == "erro":
                break
            time.sleep(0.1)
        p = cs["processos"][0]
        assert p["estado"] == "erro" and "Limite de uso do Gemini" in p["error"]
        assert c.post(f"/api/cases/{case['id']}/processes/{p['numero']}/reextract").status_code == 202
