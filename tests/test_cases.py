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
    """Provedor falso: devolve o payload e registra as chamadas."""
    name = "fake"

    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def extract(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return dict(self.payload)

    def generate(self, system, user, max_tokens=1000):
        self.calls.append({"system": system, "user": user})
        return "Sim, há bloqueio de R$ 48.320,15 (VOL 001, pg. 2)."


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
    monkeypatch.setenv("GEMINI_API_KEY", "")
    llm = FakeLLM({
        "garantia": "IMÓVEL", "valor_garantia": "R$ 2.627.732,00", "data_garantia": "2018-09-28", "tent_alien": "SIM",
        "excecao_pendente": "NÃO", "arquivada_40": "NÃO", "redirecionamento": "NÃO", "corresponsaveis": None,
        "observacoes": "VOL 001, pg. 2 - Penhora online via SISBAJUD.",
        "bens": [{"matricula": "28.749", "caracteristicas": "Gleba", "proprietario": "Alfa", "ato": "R-01",
                  "gravames": "Penhora", "resumo": "Auto de penhora", "situacao": "Ativo"}],
        "fraudes": [], "citacoes": [{"campo": "garantia", "citacao": "VOL 001, pg. 2"}],
    })
    with TestClient(create_app(Settings(), llm_client=llm)) as c:
        yield c, llm, tmp_path


def wait(c, doc_id):
    """Espera a ingestão do PDF e a extração do processo; devolve o documento + result/sheet do processo."""
    for _ in range(150):
        d = c.get(f"/api/documents/{doc_id}").json()
        if d["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    else:
        raise AssertionError("timeout (ingestão)")
    if d["status"] == "done" and d["numero_processo"]:
        for _ in range(150):
            p = c.get(f"/api/cases/{d['case_id']}/processes/{d['numero_processo']}").json()
            if p.get("stale") == 0 and (p.get("result") or p.get("error")):
                d.update(result=p["result"], sheet=p["sheet"], process=p)
                break
            time.sleep(0.1)
        else:
            raise AssertionError("timeout (extração)")
    return d


def test_filename_and_citation():
    i = citation.parse_filename(f"{NUM}_V001_001-151-180.pdf")
    assert i == {"numero_processo": NUM, "volume": 1, "pagina_inicial": 151, "evento": None, "parte": None}
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
    sch = ex.tool_schema(SHEETS["execucoes_fiscais"])["properties"]
    assert "numero" not in sch and "digitalizado" not in sch
    assert "IMÓVEL" in sch["garantia"]["enum"] and None in sch["garantia"]["enum"]


def test_select_context_large_doc_prefers_relevant_and_stays_in_budget():
    from app.ingest.chunker import chunk_page
    from app.search.memory import MemoryStore

    pages = [{"doc_id": "d", "page": i, "label": f"VOL 001, pg. {i}", "text": ("texto neutro " * 50) + (" penhora imóvel matrícula" if i == 40 else "")}
             for i in range(1, 101)]
    st = MemoryStore()
    for p in pages:
        st.index("d", chunk_page(p["page"], p["text"]))
    ctx = ex.select_context(pages, 20_000, st, ["d"])
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
    assert d["sheet"] == "execucoes_fiscais" and d["process"]["classe"].startswith("Execução Fiscal")
    assert d["result"]["fields"]["garantia"] == "IMÓVEL" and d["result"]["modo"] == "llm"
    # páginas absolutas e rótulo no contexto enviado ao LLM
    assert "[VOL 001, pg. 152]" in llm.calls[0]["user"]
    assert "Evento [Número], VOL [Número]" in llm.calls[0]["system"]
    assert d["result"]["provedor"] == "fake" and d["result"]["citacoes"] == {"garantia": "VOL 001, pg. 2"}

    # revisão: lista fechada é imposta
    bad = c.put(f"/api/cases/{case['id']}/processes/{NUM}/review", json={"fields": {"garantia": "CASA"}})
    assert bad.status_code == 400
    ok = c.put(f"/api/cases/{case['id']}/processes/{NUM}/review", json={"fields": {"valor_garantia": "R$ 1,00"}, "reviewed": True})
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
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with TestClient(create_app(Settings())) as c:
        pdf = tmp_path / "x.pdf"
        make_pdf(pdf, image_page=False)
        with pdf.open("rb") as f:
            up = c.post("/api/documents", files={"file": ("x.pdf", f, "application/pdf")}).json()
        d = wait(c, up["id"])
        assert d["result"]["modo"] == "sem_llm" and "indisponível" in d["result"]["fields"]["observacoes"]


def test_eproc_filenames_and_markers():
    assert citation.parse_filename("Evento_14.pdf")["evento"] == "14"
    assert citation.parse_filename("Evento 01.pdf")["evento"] == "1"
    assert citation.page_refs("Poder Judiciário\n:: 60000006508 - eproc - ::\nhttps://x")["id"] == "60000006508"
    assert citation.page_refs("código verificador 60000345054v1 e do código CRC")["id"] == "60000345054"
    assert citation.label(None, 1, {"evento": "14", "id": "60000006508"}) == "Evento 14, pg. 1, ID 60000006508"


def test_numero_resolved_from_text_preferring_case_registry(env):
    c, llm, tmp = env
    case = c.post("/api/cases", json={"nome": "Beta"}).json()
    wb = Workbook()
    wb.active.title = "Mcfly"
    wb.active.append(["Número do processo CNJ", "Classe SAJ"])
    wb.active.append(["1234567-89.2023.8.26.0100", "Execução Fiscal (SIDA)"])
    buf = io.BytesIO()
    wb.save(buf)
    c.post(f"/api/cases/{case['id']}/mcfly", files={"file": ("m.xlsx", buf.getvalue())})
    pdf = tmp / "Evento_14.pdf"
    make_pdf(pdf, image_page=False)  # texto cita 1234567-89.2023.8.26.0100
    with pdf.open("rb") as f:
        up = c.post("/api/documents", data={"case_id": case["id"]}, files={"file": (pdf.name, f, "application/pdf")}).json()
    d = wait(c, up["id"])
    assert d["numero_processo"] == "1234567-89.2023.8.26.0100" and d["evento"] == "14"
    assert "[Evento 14, pg. 1]" in llm.calls[0]["user"]


def test_gemini_provider_structured_output_and_schema_fallback():
    from app.llm import GeminiProvider

    class Models:
        def __init__(self, fail_first):
            self.fail, self.calls = fail_first, []

        def generate_content(self, model, contents, config):
            self.calls.append(config)
            if self.fail and len(self.calls) == 1:
                raise RuntimeError("400 INVALID_ARGUMENT: response schema not supported")
            return SimpleNamespace(text='```json\n{"observacoes": "ok", "garantia": "IMÓVEL"}\n```')

    schema = ex.tool_schema(SHEETS["execucoes_fiscais"])
    m = Models(False)
    out = GeminiProvider("k", "gemini-x", SimpleNamespace(models=m)).extract("sys", "user", schema)
    assert out["garantia"] == "IMÓVEL" and m.calls[0].response_mime_type == "application/json"
    assert m.calls[0].response_json_schema == schema and m.calls[0].system_instruction == "sys"
    m2 = Models(True)
    out2 = GeminiProvider("k", "gemini-x", SimpleNamespace(models=m2)).extract("sys", "user", schema)
    assert out2["observacoes"] == "ok" and len(m2.calls) == 2 and m2.calls[1].response_json_schema is None
    with pytest.raises(RuntimeError):
        GeminiProvider("k", "m", SimpleNamespace(models=SimpleNamespace(
            generate_content=lambda **k: SimpleNamespace(text="não é json")))).extract("s", "u", schema)


def test_anthropic_provider_uses_forced_tool():
    from app.llm import AnthropicProvider

    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"observacoes": "x"})])

    out = AnthropicProvider("k", "m", SimpleNamespace(messages=SimpleNamespace(create=create))).extract("s", "u", {"type": "object", "properties": {}})
    assert out == {"observacoes": "x"} and seen["tool_choice"]["name"] == "registrar_extracao"


def test_provider_selection(monkeypatch):
    from app.llm import AnthropicProvider, GeminiProvider, get_llm

    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    assert get_llm(Settings()) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert isinstance(get_llm(Settings()), AnthropicProvider)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert isinstance(get_llm(Settings()), GeminiProvider)  # auto prefere Gemini
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert isinstance(get_llm(Settings()), AnthropicProvider)


def _pdf_with_footers(path, footers):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    for i, ft in enumerate(footers, start=1):
        c.drawString(60, 800, f"Despacho da página {i}. Determino a penhora do imóvel de matrícula 28.749 e a avaliação.")
        c.drawString(60, 780, "Processo 1234567-89.2023.8.26.0100 em andamento perante o juízo da execução fiscal.")
        if ft:
            c.drawString(60, 40, ft)
        c.showPage()
    c.save()


def _upload(c, tmp, name, footers, **form):
    pdf = tmp / name
    _pdf_with_footers(pdf, footers)
    with pdf.open("rb") as f:
        up = c.post("/api/documents", data=form, files={"file": (name, f, "application/pdf")})
    assert up.status_code == 202, up.text
    return wait(c, up.json()["id"])


def test_pje_cites_id_and_page_within_document_never_evento(env):
    c, llm, tmp = env
    d = _upload(c, tmp, "proc.pdf", ["Num. 3160597 - Pág. 1", "Num. 3160597 - Pág. 2", "Num. 3160609 - Pág. 1"])
    assert d["sistema"] == "pje"
    labels = d["metadata"]["rotulos"]
    assert labels == {"1": "ID 3160597, pg. 1", "2": "ID 3160597, pg. 2", "3": "ID 3160609, pg. 1"}
    assert "Evento" not in llm.calls[0]["user"]
    assert "SISTEMA: PJe" in llm.calls[0]["system"] and "NUNCA use 'Evento'" in llm.calls[0]["system"]


def test_eproc_cites_evento_never_id_and_inherits_across_pages(env):
    c, llm, tmp = env
    d = _upload(c, tmp, "Evento_28.pdf", [":: 60000006508 - eproc - ::", None], sistema="eproc")
    assert d["sistema"] == "eproc"
    assert d["metadata"]["rotulos"] == {"1": "Evento 28, pg. 1", "2": "Evento 28, pg. 2"}
    assert "SISTEMA: eproc" in llm.calls[0]["system"] and "NUNCA use 'ID'" in llm.calls[0]["system"]


@pytest.mark.parametrize("tipo,sheet,prompt,titulo", [
    ("apelacao", "outras_acoes", "recursos", "Relatório de Tramitação Recursal"),
    ("agravo", "outras_acoes", "recursos", "Relatório de Tramitação Recursal"),
    ("embargos", "embargos", "outras_acoes", "Relatório de Status Processual"),
    ("execucao_fiscal", "execucoes_fiscais", "execucoes_fiscais", "Relatório de Status de Execução e Penhoras"),
    ("outras_acoes", "outras_acoes", "outras_acoes", "Relatório de Status Processual"),
])
def test_tipo_chosen_in_upload_selects_sheet_and_prompt(env, tipo, sheet, prompt, titulo):
    c, llm, tmp = env
    d = _upload(c, tmp, "a.pdf", [None], tipo=tipo)
    assert d["sheet"] == sheet and d["result"]["prompt"] == prompt
    assert titulo in llm.calls[-1]["system"]
    assert c.get("/api/tipos").json()["tipos"]["execucao_fiscal"] == "Execução Fiscal"


def test_invalid_tipo_or_sistema_rejected(env):
    c, _, tmp = env
    for form in ({"tipo": "xpto"}, {"sistema": "projudi"}):
        r = c.post("/api/documents", data=form, files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
        assert r.status_code == 400


def test_check_citations_flags_invented_and_system_mixups():
    pages = [{"page": 1, "refs": {"evento": "28"}}, {"page": 2, "refs": {"evento": "28"}}]
    ok = ex.check_citations(["Evento 28, pg. 2 - Penhora"], pages, "eproc")
    assert ok == []
    bad = ex.check_citations(["Evento 99 - x; ID 123456789 - y; pg. 77"], pages, "eproc")
    assert set(bad) == {"Evento 99", "ID 123456789", "pg. 77"}
    pje = [{"page": 5, "refs": {"id": "3160597", "pg": "1"}}]
    assert ex.check_citations(["ID 3160597, pg. 1"], pje, "pje") == []
    assert ex.check_citations(["Evento 4 - x"], pje, "pje") == ["Evento 4"]
    assert ex.check_citations(["ID.5896152995, pg. 09/10"], [{"page": 9, "refs": {"id": "5896152995"}}, {"page": 10, "refs": {}}], "pje") == []


def test_invented_citation_from_llm_is_flagged_in_result(env):
    c, llm, tmp = env
    llm.payload = {**llm.payload, "observacoes": "Evento 99 - Penhora (pg. 1)"}
    d = _upload(c, tmp, "Evento_28.pdf", [None, None], sistema="eproc")
    assert "Evento 99" in d["result"]["citacoes_invalidas"]


def test_eproc_full_process_pdf_separators_footers_and_capa_class(env):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c, llm, tmp = env
    pages = [
        ["Processos relacionados:", "Nº do processo 1234567-89.2023.8.26.0100", "Classe da ação:", " Agravo de Instrumento",
         "Competência Tributário/Administrativo"],
        ["PÁGINA DE SEPARAÇÃO", "(Gerada automaticamente pelo sistema.)", "Evento 14", "Evento:", "Data:",
         "DESPACHO 03/04/2024 18:44:02", "Complemento: Ref. ao evento 3 do processo originário."],
        ["Despacho do relator. Determino a penhora do imóvel e a avaliação do bem para o leilão judicial.",
         "Processo 1234567-89.2023.8.26.0100/TRF6, Evento 14, DESPADEC1, Página 1"],
        ["Intime-se a parte agravada para manifestação sobre o pedido de redirecionamento da execução.",
         "Processo 1234567-89.2023.8.26.0100/TRF6, Evento 14, DESPADEC1, Página 2"],
        ["PÁGINA DE SEPARAÇÃO", "(Gerada automaticamente pelo sistema.)", "Evento 15", "Evento:", "Data:", "PETICAO 05/04/2024"],
    ]
    pdf = tmp / "TRF6-123_PARTE_2.PDF"
    cv = canvas.Canvas(str(pdf), pagesize=A4)
    for lines in pages:
        y = 800
        for ln in lines:
            cv.drawString(50, y, ln)
            y -= 20
        cv.showPage()
    cv.save()
    with pdf.open("rb") as f:
        up = c.post("/api/documents", files={"file": (pdf.name, f, "application/pdf")}).json()
    d = wait(c, up["id"])
    assert d["sistema"] == "eproc" and d["classe"] == "Agravo de Instrumento"
    assert d["result"]["prompt"] == "recursos"  # classe da capa define o prompt sem o analista escolher
    assert d["numero_processo"] == "1234567-89.2023.8.26.0100"
    # menção a "evento 3" no corpo da separação não vira marcador; pg. é a página dentro do ato
    assert d["metadata"]["rotulos"] == {"1": "pg. 1", "2": "Evento 14", "3": "Evento 14, pg. 1",
                                         "4": "Evento 14, pg. 2", "5": "Evento 15"}


def test_citation_with_page_outside_the_act_is_flagged():
    pages = [{"page": 3, "refs": {"evento": "14", "pg": "1"}}, {"page": 4, "refs": {"evento": "14", "pg": "2"}}]
    assert ex.check_citations(["Evento 14, pg. 2 - Despacho"], pages, "eproc") == []
    assert ex.check_citations(["Evento 14, pg. 9 - Despacho"], pages, "eproc") == ["Evento 14, pg. 9"]


def _make_part(path, first_page, lines_per_page):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    cv = canvas.Canvas(str(path), pagesize=A4)
    for i, lines in enumerate(lines_per_page):
        y = 800
        for ln in [f"Folha original {first_page + i}."] + lines:
            cv.drawString(50, y, ln)
            y -= 20
        cv.showPage()
    cv.save()


def test_several_parts_of_one_process_become_one_extraction_and_one_row(env):
    c, llm, tmp = env
    case = c.post("/api/cases", json={"nome": "Alfa"}).json()
    c.post(f"/api/cases/{case['id']}/mcfly", files={"file": ("m.xlsx", mcfly_xlsx())})
    parts = [("001-1-2", 1, ["Petição inicial da execução fiscal contra a empresa devedora.", "Citação realizada por oficial de justiça."]),
             ("001-3-4", 3, ["Auto de penhora do imóvel de matrícula 28.749 e depósito.", "Laudo de avaliação do imóvel penhorado."]),
             ("001-5-6", 5, ["Hasta pública designada para leilão do bem penhorado.", "Despacho final aguardando praça."])]
    ids = []
    for suf, first, lines in parts:
        pdf = tmp / f"{NUM}_V{suf}.pdf"
        _make_part(pdf, first, [[x] for x in lines])
        with pdf.open("rb") as f:
            ids.append(c.post("/api/documents", data={"case_id": case["id"]}, files={"file": (pdf.name, f, "application/pdf")}).json()["id"])
    # reenviar a mesma parte substitui a anterior (não duplica)
    pdf = tmp / f"{NUM}_V001-3-4.pdf"
    with (tmp / f"{NUM}_V001-3-4.pdf").open("rb") as f:
        ids.append(c.post("/api/documents", data={"case_id": case["id"]}, files={"file": (pdf.name, f, "application/pdf")}).json()["id"])
    d = wait(c, ids[-1])
    for i in ids[:3]:
        if i != ids[1]:
            wait(c, i)
    cs = c.get(f"/api/cases/{case['id']}").json()
    assert len(cs["documentos"]) == 3  # a parte reenviada substituiu a antiga
    procs = {p["numero"]: p for p in cs["processos"]}
    assert set(procs) == {NUM, "0000001-00.2020.4.01.0000"}  # um processo, não um por PDF
    assert procs[NUM]["estado"] == "pronto" and len(procs[NUM]["documentos"]) == 3

    extracts = [x for x in llm.calls if "schema" in x]
    assert len(extracts) == 1, "uma extração por processo, não uma por PDF"
    ctx = extracts[0]["user"]
    order = [ctx.index(f"[VOL 001, pg. {n}]") for n in range(1, 7)]
    assert order == sorted(order), "partes juntadas na ordem dos autos"
    assert "Hasta pública" in ctx and "Petição inicial" in ctx

    res = c.get(f"/api/cases/{case['id']}/processes/{NUM}").json()["result"]
    assert len(res["documentos"]) == 3 and res["paginas"] == 6

    chat = c.post("/api/chat", json={"question": "Houve hasta pública no leilão do bem?", "case_id": case["id"], "numero": NUM}).json()
    assert any("VOL 001, pg. 5" == s["rotulo"] or "VOL 001, pg. 6" == s["rotulo"] for s in chat["sources"])

    wb = load_workbook(io.BytesIO(c.get(f"/api/cases/{case['id']}/spreadsheet").content))
    assert wb["Relatório"].max_row == 3 and wb["Execuções Fiscais"].max_row == 2  # 1 linha por processo


def test_new_part_after_review_marks_process_outdated_until_reextract(env):
    c, llm, tmp = env
    case = c.post("/api/cases", json={"nome": "Beta"}).json()
    pdf = tmp / f"{NUM}_V001_001-1-1.pdf"
    _make_part(pdf, 1, [["Penhora do imóvel."]])
    with pdf.open("rb") as f:
        d = wait(c, c.post("/api/documents", data={"case_id": case["id"], "tipo": "execucao_fiscal"}, files={"file": (pdf.name, f, "application/pdf")}).json()["id"])
    assert c.put(f"/api/cases/{case['id']}/processes/{NUM}/review", json={"fields": {"valor_garantia": "R$ 7,00"}}).status_code == 200
    n_before = len([x for x in llm.calls if "schema" in x])
    pdf2 = tmp / f"{NUM}_V001_001-2-2.pdf"
    _make_part(pdf2, 2, [["Hasta pública."]])
    with pdf2.open("rb") as f:
        new_id = c.post("/api/documents", data={"case_id": case["id"], "tipo": "execucao_fiscal"}, files={"file": (pdf2.name, f, "application/pdf")}).json()["id"]
    for _ in range(100):  # só a ingestão: processo revisado não é reextraído sozinho
        if c.get(f"/api/documents/{new_id}").json()["status"] == "done":
            break
        time.sleep(0.1)
    time.sleep(0.3)
    p = c.get(f"/api/cases/{case['id']}/processes/{NUM}").json()
    assert p["estado"] == "desatualizado" and p["result"]["fields"]["valor_garantia"] == "R$ 7,00"  # edição preservada
    assert len([x for x in llm.calls if "schema" in x]) == n_before  # não gastou LLM sem pedido
    assert c.post(f"/api/cases/{case['id']}/processes/{NUM}/reextract").status_code == 202
    for _ in range(100):
        p = c.get(f"/api/cases/{case['id']}/processes/{NUM}").json()
        if p["estado"] == "pronto" and p["reviewed"] == 0:
            break
        time.sleep(0.1)
    assert p["result"]["paginas"] == 2 and p["result"]["fields"]["valor_garantia"] == "R$ 2.627.732,00"


def test_elastic_store_waits_for_elasticsearch_instead_of_crashing(monkeypatch):
    from app.search import elastic

    calls = {"n": 0}

    class FakeIndices:
        def exists(self, index): return True

    class FakeCluster:
        def health(self, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("ainda subindo")

    class FakeES:
        def __init__(self, url): self.cluster, self.indices = FakeCluster(), FakeIndices()

    monkeypatch.setattr(elastic, "Elasticsearch", FakeES)
    monkeypatch.setattr(elastic.time, "sleep", lambda s: None)
    elastic.ElasticStore("http://es:9200", "idx")
    assert calls["n"] == 3  # tentou 3 vezes e seguiu quando o ES respondeu

    calls["n"] = -10**6  # nunca responde
    monkeypatch.setattr(elastic.time, "monotonic", iter(range(0, 10**6, 100)).__next__)
    with pytest.raises(RuntimeError, match="indisponível"):
        elastic.ElasticStore("http://es:9200", "idx", wait_seconds=200)


def test_elastic_store_fails_fast_on_version_mismatch(monkeypatch):
    """Cliente 9.x contra servidor 8.x gera HTTP 400: esperar não resolve, tem de falhar logo e claro."""
    from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig
    from elasticsearch import ApiError

    from app.search import elastic

    meta = ApiResponseMeta(status=400, http_version="1.1", headers=HttpHeaders(), duration=0.0,
                           node=NodeConfig("http", "es", 9200))

    class FakeCluster:
        def health(self, **kw):
            raise ApiError("media_type_header_exception", meta, {"error": "Accept version must be either version 8 or 7"})

    class FakeES:
        def __init__(self, url): self.cluster = FakeCluster()

    monkeypatch.setattr(elastic, "Elasticsearch", FakeES)
    monkeypatch.setattr(elastic.time, "sleep", lambda s: pytest.fail("não deveria esperar"))
    with pytest.raises(RuntimeError, match="recusou a conexão"):
        elastic.ElasticStore("http://es:9200", "idx")


def test_gemini_retries_per_minute_limit_but_not_daily_quota(monkeypatch):
    from app import llm
    from app.llm import GeminiProvider

    esperas = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: esperas.append(s))

    class Models:
        def __init__(self, falhas, msg):
            self.falhas, self.msg, self.n = falhas, msg, 0

        def generate_content(self, model, contents, config):
            self.n += 1
            if self.n <= self.falhas:
                raise RuntimeError(self.msg)
            return SimpleNamespace(text='{"observacoes": "ok"}')

    schema = ex.tool_schema(SHEETS["outras_acoes"])
    m = Models(2, "429 RESOURCE_EXHAUSTED: Please retry in 30.2s")
    assert GeminiProvider("k", "m", SimpleNamespace(models=m)).extract("s", "u", schema) == {"observacoes": "ok"}
    assert m.n == 3 and esperas == [32, 32]  # esperou o prazo informado pelo Google
    m2 = Models(9, "429 RESOURCE_EXHAUSTED: quota exceeded, check your plan")  # sem prazo: cota diária/plano
    with pytest.raises(RuntimeError):
        GeminiProvider("k", "m", SimpleNamespace(models=m2)).extract("s", "u", schema)
    assert m2.n == 1  # não insiste
    m3 = Models(9, "429 RESOURCE_EXHAUSTED: retry in 600s")  # espera longa demais: falha logo
    with pytest.raises(RuntimeError):
        GeminiProvider("k", "m", SimpleNamespace(models=m3)).extract("s", "u", schema)
    assert m3.n == 1


def test_context_budget_is_configurable(env, monkeypatch):
    c, llm, tmp = env
    monkeypatch.setenv("CONTEXT_BUDGET_CHARS", "10")
    assert Settings().context_budget_chars == 10
    monkeypatch.delenv("CONTEXT_BUDGET_CHARS")
    assert Settings().context_budget_chars == 400000


def test_gemini_retries_503_overload_then_falls_back_to_reserve_model(monkeypatch):
    from app import llm
    from app.llm import GeminiProvider

    esperas = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: esperas.append(s))
    schema = ex.tool_schema(SHEETS["outras_acoes"])

    class Models:
        def __init__(self, falha_ate, so_modelo=None):
            self.falha_ate, self.so_modelo, self.chamadas = falha_ate, so_modelo, []

        def generate_content(self, model, contents, config):
            self.chamadas.append(model)
            if len(self.chamadas) <= self.falha_ate and (self.so_modelo is None or model == self.so_modelo):
                raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
            return SimpleNamespace(text='{"observacoes": "ok"}')

    m = Models(2)  # passa na 3ª tentativa
    assert GeminiProvider("k", "principal", SimpleNamespace(models=m)).extract("s", "u", schema) == {"observacoes": "ok"}
    assert m.chamadas == ["principal"] * 3 and esperas == [10, 25]

    esperas.clear()
    m2 = Models(99, so_modelo="principal")  # principal nunca responde; reserva sim
    out = GeminiProvider("k", "principal", SimpleNamespace(models=m2), fallback_model="reserva").extract("s", "u", schema)
    assert out == {"observacoes": "ok"} and m2.chamadas == ["principal"] * 4 + ["reserva"] and esperas == [10, 25, 50]

    m3 = Models(99)  # sem reserva: 4 tentativas e falha com a mensagem original
    with pytest.raises(RuntimeError, match="high demand"):
        GeminiProvider("k", "principal", SimpleNamespace(models=m3)).extract("s", "u", schema)
    assert len(m3.chamadas) == 4

    m4 = Models(99)  # erro que esperar não resolve (chave inválida) sobe na hora, sem esperar nem usar reserva
    m4.generate_content = lambda **k: (_ for _ in ()).throw(RuntimeError("403 PERMISSION_DENIED api key"))
    esperas.clear()
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        GeminiProvider("k", "principal", SimpleNamespace(models=m4), fallback_model="reserva").extract("s", "u", schema)
    assert esperas == []


def test_chat_is_impatient_but_extraction_is_patient(monkeypatch):
    from app import llm
    from app.llm import GeminiProvider

    esperas = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: esperas.append(s))

    class Models:
        def __init__(self, msg):
            self.msg, self.modelos = msg, []

        def generate_content(self, model, contents, config):
            self.modelos.append(model)
            raise RuntimeError(self.msg)

    m = Models("503 UNAVAILABLE high demand")
    with pytest.raises(RuntimeError):
        GeminiProvider("k", "p", SimpleNamespace(models=m)).generate("s", "u")
    assert m.modelos == ["p", "p"] and esperas == [5]  # chat: 2 tentativas, 5 s (não 85 s)

    esperas.clear()
    m2 = Models("503 UNAVAILABLE high demand")
    with pytest.raises(RuntimeError):
        GeminiProvider("k", "p", SimpleNamespace(models=m2), fallback_model="r").generate("s", "u")
    assert m2.modelos == ["p", "p", "r", "r"]  # depois tenta o modelo reserva

    m3 = Models("429 RESOURCE_EXHAUSTED: Please retry in 32s")  # prazo longo: no chat não espera
    esperas.clear()
    with pytest.raises(RuntimeError):
        GeminiProvider("k", "p", SimpleNamespace(models=m3)).generate("s", "u")
    assert len(m3.modelos) == 1 and esperas == []
