import io
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app import extract as ex
from app.config import Settings
from app.ingest import citation, quality
from app.main import create_app
from app.schemas import SHEETS, classify
from tests.make_pdf import make_pdf

NUM = "0341609-70.2005.8.13.0261"


class FakeLLM:
    """Imita o cliente Anthropic devolvendo uma chamada de ferramenta."""

    def __init__(self, payload):
        self.payload, self.calls = payload, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=dict(self.payload))])


def mcfly_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "Mcfly"
    ws.append(["Número do processo CNJ", "Polo ativo", "Polo passivo", "Classe SAJ", "Juízo", "Procuradoria responsável",
               "Data do protocolo", "Inscrições processo principal", "Somatório inscrições (processo principal e vinculados)"])
    ws.append([NUM, "FAZENDA NACIONAL", "EMPRESA ALFA LTDA - 11.222.333/0001-81", "Execução Fiscal (SIDA)", "02ª Vara - FORMIGA",
               "PRFN6 (Sede)", "2005-09-01", "60 6 05 009561-42 | R$ 759.527,01", "R$ 759.527,01"])
    ws.append(["0000001-00.2020.4.01.0000", "ALFA", "FAZENDA NACIONAL", "Apelação", "Gab. 16 - BH", "PRFN6 (Sede)", None, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUEUE_BACKEND", "memory")
    monkeypatch.setenv("SEARCH_BACKEND", "memory")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    llm = FakeLLM({
        "garantia": "IMÓVEL", "valor_garantia": "R$ 2.627.732,00", "data_garantia": "2018-09-28", "tent_alien": "SIM",
        "excecao_pendente": "NÃO", "arquivada_40": "NÃO", "redirecionamento": "NÃO", "corresponsaveis": None,
        "observacoes": "VOL 001, pg. 2 - Penhora online via SISBAJUD.",
        "bens": [{"matricula": "28.749", "caracteristicas": "Gleba", "proprietario": "Alfa", "ato": "R-01",
                  "gravames": "Penhora", "resumo": "Auto de penhora", "situacao": "Ativo"}],
        "fraudes": [], "citacoes": {"garantia": "VOL 001, pg. 2"},
    })
    with TestClient(create_app(Settings(), llm_client=llm)) as c:
        yield c, llm, tmp_path


def wait(c, doc_id):
    for _ in range(100):
        d = c.get(f"/api/documents/{doc_id}").json()
        if d["status"] in ("done", "error"):
            return d
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_filename_and_citation():
    i = citation.parse_filename(f"{NUM}_V001_001-151-180.pdf")
    assert i == {"numero_processo": NUM, "volume": 1, "pagina_inicial": 151}
    assert citation.parse_filename("qualquer.pdf")["numero_processo"] is None
    assert citation.label(1, 160, {"evento": "16", "id": "5896152995"}) == "Evento 16, VOL 001, pg. 160, ID 5896152995"
    assert citation.page_refs("Num. 123456789 - Pág. 3")["id"] == "123456789"


def test_text_quality_detects_corrupted_ocr_layer():
    bad = "Sucede, entretanto que, па exordial, desde аЬ initio, fora plei~.eada а tutela антecipada, о feito ао Juiz"
    good = "Sucede, entretanto que, na exordial, desde ab initio, fora pleiteada a tutela antecipada, o feito ao Juiz"
    assert quality.is_degraded(bad) and not quality.is_degraded(good)
    assert "na" in quality.repair_cyrillic("па")


def test_classify():
    assert classify("Execução Fiscal (SIDA)") == ("execucoes_fiscais", "execucoes_fiscais")
    assert classify("Embargos à Execução Fiscal")[0] == "embargos"
    assert classify("Apelação") == ("outras_acoes", "recursos")
    assert classify("Mandado de Segurança") == ("outras_acoes", "outras_acoes")


def test_validate_rejects_value_outside_closed_list():
    out = ex.validate(SHEETS["execucoes_fiscais"], {"garantia": "CASA NA PRAIA", "tent_alien": "SIM", "observacoes": "x"})
    assert out["garantia"] is None and out["_revisar"] == ["garantia"]


def test_tool_schema_has_enums_and_ai_fields_only():
    sch = ex.tool_schema(SHEETS["execucoes_fiscais"])["input_schema"]["properties"]
    assert "numero" not in sch and "digitalizado" not in sch
    assert "IMÓVEL" in sch["garantia"]["enum"] and None in sch["garantia"]["enum"]


def test_select_context_large_doc_prefers_relevant_and_stays_in_budget():
    from app.ingest.chunker import chunk_page
    from app.search.memory import MemoryStore

    pages = [{"page": i, "label": f"VOL 001, pg. {i}", "text": ("texto neutro " * 50) + (" penhora imóvel matrícula" if i == 40 else "")}
             for i in range(1, 101)]
    st = MemoryStore()
    for p in pages:
        st.index("d", chunk_page(p["page"], p["text"]))
    ctx = ex.select_context(pages, 20_000, st, "d")
    assert len(ctx) <= 21_000 and "[VOL 001, pg. 40]" in ctx


def test_case_flow_end_to_end(env):
    c, llm, tmp = env
    case = c.post("/api/cases", json={"nome": "Alfa"}).json()
    r = c.post(f"/api/cases/{case['id']}/mcfly", files={"file": ("m.xlsx", mcfly_xlsx())})
    assert r.json() == {"importados": 2}

    pdf = tmp / f"{NUM}_V001_001-151-180.pdf"
    make_pdf(pdf, image_page=False)
    with pdf.open("rb") as f:
        up = c.post("/api/documents", data={"case_id": case["id"]}, files={"file": (pdf.name, f, "application/pdf")}).json()
    assert up["numero_processo"] == NUM and up["pagina_inicial"] == 151
    d = wait(c, up["id"])
    assert d["status"] == "done", d.get("error")
    assert d["sheet"] == "execucoes_fiscais" and d["classe"].startswith("Execução Fiscal")
    assert d["result"]["fields"]["garantia"] == "IMÓVEL" and d["result"]["modo"] == "llm"
    # páginas absolutas e rótulo no contexto enviado ao LLM
    assert "[VOL 001, pg. 152]" in llm.calls[0]["messages"][0]["content"]
    assert "Evento [Número], VOL [Número]" in llm.calls[0]["system"]
    assert llm.calls[0]["tool_choice"] == {"type": "tool", "name": "registrar_extracao"}

    # revisão: lista fechada é imposta
    bad = c.put(f"/api/documents/{d['id']}/review", json={"fields": {"garantia": "CASA"}})
    assert bad.status_code == 400
    ok = c.put(f"/api/documents/{d['id']}/review", json={"fields": {"valor_garantia": "R$ 1,00"}, "reviewed": True})
    assert ok.status_code == 200

    x = c.get(f"/api/cases/{case['id']}/spreadsheet")
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Relatório", "DJE-SDJ", "DJE-SIEF", "Execuções Fiscais", "Outras Ações",
                             "Embargos à Execução", "Outros Bens", "Alegações de Fraude"]
    ef = [[c_.value for c_ in r] for r in wb["Execuções Fiscais"].iter_rows()]
    hdr = ef[0]
    row = dict(zip(hdr, ef[1]))
    assert row["Número do processo judicial"] == NUM and row["PROCESSO DIGITALIZADO"] == "SIM"
    assert row["EXIT. GARANTIA"] == "IMÓVEL" and row["VALOR AVALIAÇÃO OU DEPÓSITO"] == "R$ 1,00"
    assert row["Inscrições processo principal"].startswith("60 6 05")
    assert wb["Execuções Fiscais"].cell(2, 1).fill.fgColor.rgb != "00FFF3CD"  # revisado: sem destaque
    # processo do Mcfly sem PDF entra como não digitalizado, na aba da sua classe
    oa = [[c_.value for c_ in r] for r in wb["Outras Ações"].iter_rows()]
    assert oa[1][0] == "0000001-00.2020.4.01.0000" and oa[1][6] == "NÃO"
    assert wb["Outros Bens"].cell(2, 1).value == "28.749"
    assert wb["Relatório"].max_row == 3


def test_without_llm_key_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with TestClient(create_app(Settings())) as c:
        pdf = tmp_path / "x.pdf"
        make_pdf(pdf, image_page=False)
        with pdf.open("rb") as f:
            up = c.post("/api/documents", files={"file": ("x.pdf", f, "application/pdf")}).json()
        d = wait(c, up["id"])
        assert d["result"]["modo"] == "sem_llm" and "indisponível" in d["result"]["fields"]["observacoes"]
