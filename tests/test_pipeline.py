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
