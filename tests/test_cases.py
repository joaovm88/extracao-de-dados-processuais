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
    last_usage = {"entrada": 1000, "saida": 50, "raciocinio": None}
    last_call = {"tentativas": 1, "espera_s": 0.0, "modelo": "fake"}

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
    assert r.json() == {"importados": 2, "novos": 2, "atualizados": 0}

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
    assert "[ORIGEM] - [dd/mm/aaaa]" in llm.calls[0]["system"]
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
    assert wb["Outros Bens"].cell(2, 1).value is None  # o bem é só uma SUGESTÃO da IA: não vai à planilha sem autorização
    assert hdr[-2:] == ["Nº de ordem", "Situação da análise"] and ef[1][-2:] == [1, "Revisado"]
    assert wb["Relatório"].max_row == 3
    # o analista autoriza -> a aba Outros Bens é preenchida
    sheet = c.get(f"/api/cases/{case['id']}/sheet").json()
    assert sheet["bens"][0]["_st"] == "sugerido" and next(a for a in sheet["abas"] if a["key"] == "bens")["pendentes"] == 1
    assert c.put(f"/api/cases/{case['id']}/processes/{NUM}/itens", json={"lista": "bens", "acao": "autorizar"}).json()["alterados"] == 1
    wb2 = load_workbook(io.BytesIO(c.get(f"/api/cases/{case['id']}/spreadsheet").content))
    assert wb2["Outros Bens"].cell(2, 1).value == "28.749"


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


def test_anthropic_provider_calls_tool_with_auto_choice_and_effort_only_on_5x():
    from app.llm import AnthropicProvider

    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"observacoes": "x"})])

    cli = SimpleNamespace(messages=SimpleNamespace(create=create))
    out = AnthropicProvider("k", "m", cli, effort="low").extract("s", "u", {"type": "object", "properties": {}})
    assert out == {"observacoes": "x"} and seen["tool_choice"] == {"type": "auto"}  # modelos 5.x recusam tool_choice forçado
    assert "registrar_extracao" in seen["system"] and "output_config" not in seen  # modelo sem '-5': sem esforço
    AnthropicProvider("k", "claude-haiku-5-5", cli, effort="low").extract("s", "u", {"type": "object", "properties": {}})
    assert seen["output_config"] == {"effort": "low"}


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
    ("apelacao", "outras_acoes", "recursos", "Cronologia da Tramitação Recursal"),
    ("agravo", "outras_acoes", "recursos", "Cronologia da Tramitação Recursal"),
    ("embargos", "embargos", "outras_acoes", "Cronologia dos Atos Processuais"),
    ("execucao_fiscal", "execucoes_fiscais", "execucoes_fiscais", "Cronologia dos Atos Processuais"),
    ("outras_acoes", "outras_acoes", "outras_acoes", "Cronologia dos Atos Processuais"),
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


def test_corresponsaveis_are_formatted_one_per_line_with_cpf_or_cnpj():
    f = ex.format_corresponsaveis
    esperado = "João Victor - CPF: 529.982.247-25\nBETA COMERCIO LTDA - CNPJ: 11.222.333/0001-81"
    assert f("João Victor - CPF: 529.982.247-25\nBETA COMERCIO LTDA - CNPJ: 11.222.333/0001-81") == esperado  # já no formato
    assert f("João Victor (CPF 52998224725); BETA COMERCIO LTDA, inscrita no CNPJ sob o nº 11222333000181") == esperado  # texto corrido, só números
    assert f("1. João Victor - 529.982.247-25\n2) BETA COMERCIO LTDA CNPJ/MF 11.222.333/0001-81") == esperado  # numeração e rótulos
    assert f("• João Victor – CPF: 529.982.247-25\n- BETA COMERCIO LTDA – CNPJ: 11.222.333/0001-81") == esperado  # marcadores
    assert f(["João Victor 529.982.247-25", "BETA COMERCIO LTDA 11.222.333/0001-81"]) == esperado  # lista JSON
    assert f("João Victor - CPF: 529.982.247-25\nJoão Victor, CPF 529.982.247-25") == "João Victor - CPF: 529.982.247-25"  # repetido
    assert f("João Victor - CPF: 529.982.247-26") == "João Victor - CPF: 529.982.247-26 (conferir dígitos)"  # dígito inválido
    assert f("Maria Souza\nBETA LTDA - 11.222.333/0001-81") == "Maria Souza - documento não informado\nBETA LTDA - CNPJ: 11.222.333/0001-81"
    assert f("11.222.333/0001-81") == "(nome não informado) - CNPJ: 11.222.333/0001-81"
    assert f("Não há pedido de redirecionamento") == "Não há pedido de redirecionamento"  # sem documento: não mexe
    assert f(None) is None and f("   ") is None and f([]) is None


def test_validate_applies_corresponsaveis_format_to_llm_output():
    out = ex.validate(SHEETS["execucoes_fiscais"], {"observacoes": "x", "redirecionamento": "SIM",
                      "corresponsaveis": "João Victor CPF 52998224725; BETA LTDA CNPJ 11222333000181"})
    assert out["corresponsaveis"] == "João Victor - CPF: 529.982.247-25\nBETA LTDA - CNPJ: 11.222.333/0001-81"
    assert "UMA pessoa ou empresa POR LINHA" in ex.tool_schema(SHEETS["execucoes_fiscais"])["properties"]["corresponsaveis"]["description"]


def test_corresponsaveis_several_people_on_the_same_line():
    f = ex.format_corresponsaveis
    texto = "BETA COMERCIO LTDA, inscrita no CNPJ sob o nº 11222333000181; João Victor (CPF 52998224725) e MARIA SOUZA - 987.654.321-00"
    assert f(texto) == ("BETA COMERCIO LTDA - CNPJ: 11.222.333/0001-81\nJoão Victor - CPF: 529.982.247-25\nMARIA SOUZA - CPF: 987.654.321-00")
    assert f("Ana (CPF 52998224725), Bruno (CPF 11144477735) e Carla CNPJ 11222333000181") == (
        "Ana - CPF: 529.982.247-25\nBruno - CPF: 111.444.777-35\nCarla - CNPJ: 11.222.333/0001-81")


def test_context_info_reports_what_was_cut():
    from app.ingest.chunker import chunk_page
    from app.search.memory import MemoryStore

    pages = [{"doc_id": "d", "page": i, "label": f"pg. {i}", "text": ("texto neutro " * 50)} for i in range(1, 101)]
    st = MemoryStore()
    for p in pages:
        st.index("d", chunk_page(p["page"], p["text"]))
    _, inteiro = ex.select_context_info(pages, 10_000_000, st, ["d"])
    assert inteiro["cortado"] is False and inteiro["paginas_enviadas"] == 100 and inteiro["paginas_omitidas"] == 0
    txt, corte = ex.select_context_info(pages, 20_000, st, ["d"])
    assert corte["cortado"] is True and corte["paginas_total"] == 100 and corte["paginas_enviadas"] < 100
    assert corte["paginas_omitidas"] == 100 - corte["paginas_enviadas"] and corte["caracteres_enviados"] <= 20_000
    assert ex.select_context(pages, 20_000, st, ["d"]) == txt  # a versão antiga continua igual


def test_providers_expose_token_usage_per_call():
    from app.llm import AnthropicProvider, GeminiProvider

    class Models:
        def generate_content(self, model, contents, config):
            return SimpleNamespace(text='{"observacoes": "ok"}', usage_metadata=SimpleNamespace(
                prompt_token_count=25000, candidates_token_count=800, thoughts_token_count=300))

    g = GeminiProvider("k", "m", SimpleNamespace(models=Models()))
    assert g.last_usage is None
    g.extract("s", "u", ex.tool_schema(SHEETS["outras_acoes"]))
    assert g.last_usage == {"entrada": 25000, "saida": 800, "raciocinio": 300}

    a = AnthropicProvider("k", "m", SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input={"observacoes": "x"})], usage=SimpleNamespace(input_tokens=111, output_tokens=22)))))
    a.extract("s", "u", {"type": "object", "properties": {}})
    assert a.last_usage == {"entrada": 111, "saida": 22, "raciocinio": None}

    import threading
    visto = []
    t = threading.Thread(target=lambda: visto.append(g.last_usage))  # outra thread não enxerga o uso desta
    t.start(); t.join()
    assert visto == [None]


def test_pdf_pages_carry_reading_time_and_ocr_time(tmp_path, monkeypatch):
    import time as _t

    from app.ingest import pdf

    monkeypatch.setattr(pdf, "ocr_available", lambda: True)
    monkeypatch.setattr(pdf, "_ocr_page", lambda page, s: (_t.sleep(0.05), "DARF pago R$ 77,00 CNPJ 11.222.333/0001-81")[1])
    p = tmp_path / "a.pdf"
    make_pdf(p)  # 3 páginas de texto + 1 imagem (OCR)
    pages = list(pdf.iter_pages(str(p), Settings()))
    assert [x.method for x in pages] == ["text", "text", "text", "ocr"]
    assert all(x.seconds >= 0 for x in pages) and pages[3].seconds >= 0.05 > pages[0].seconds


def test_measurements_are_recorded_for_pdf_and_extraction_and_exposed_in_sheet(env):
    c, llm, tmp = env
    case = c.post("/api/cases", json={"nome": "Medições"}).json()
    pdf = tmp / f"{NUM}_V001_001-1-4.pdf"
    make_pdf(pdf, image_page=False)
    with pdf.open("rb") as f:
        up = c.post("/api/documents", data={"case_id": case["id"], "tipo": "execucao_fiscal"}, files={"file": (pdf.name, f, "application/pdf")}).json()
    d = wait(c, up["id"])
    m = d["result"]["medidas"]
    assert m["tokens"] == {"entrada": 1000, "saida": 50, "raciocinio": None}
    assert m["chamada"] == {"tentativas": 1, "espera_s": 0.0, "modelo": "fake"}
    assert m["contexto"]["cortado"] is False and m["contexto"]["paginas_total"] == 3 and m["contexto"]["paginas_enviadas"] == 3
    assert m["extracao_s"] >= 0
    t = d["metadata"]["tempos"]
    assert t["paginas"] == 3 and t["total_s"] >= 0 and t["ocr_paginas"] == 0
    linha = c.get(f"/api/cases/{case['id']}/sheet").json()["linhas"][0]
    assert linha["medidas"]["tokens"]["entrada"] == 1000 and linha["docs"][0]["tempos"]["paginas"] == 3


def test_provider_reports_attempts_wait_and_model_that_answered(monkeypatch):
    from app import llm
    from app.llm import GeminiProvider

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    class Models:
        def __init__(self, falha_modelo, vezes):
            self.f, self.v, self.n = falha_modelo, vezes, 0

        def generate_content(self, model, contents, config):
            self.n += 1
            if model == self.f and self.n <= self.v:
                raise RuntimeError("503 UNAVAILABLE high demand")
            return SimpleNamespace(text='{"observacoes": "ok"}')

    schema = ex.tool_schema(SHEETS["outras_acoes"])
    g = GeminiProvider("k", "p", SimpleNamespace(models=Models("p", 2)))
    assert g.last_call is None
    g.extract("s", "u", schema)
    c = g.last_call
    assert (c["tentativas"], c["espera_s"], c["modelo"]) == (3, 35.0, "p")  # 10 s + 25 s de espera antes do acerto
    assert [t["r"] for t in c["tempos"]] == ["503", "503", "ok"]  # duração e resultado de cada tentativa

    g2 = GeminiProvider("k", "p", SimpleNamespace(models=Models("p", 99)), fallback_model="r")
    g2.extract("s", "u", schema)
    assert g2.last_call["modelo"] == "r" and g2.last_call["tentativas"] == 5 and g2.last_call["espera_s"] == 85.0


def test_gemini_thinking_level_by_model_and_timeout_counts_as_transient(monkeypatch):
    from app import llm
    from app.llm import GeminiProvider

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    vistos = []

    class Models:
        n = 0

        def generate_content(self, model, contents, config):
            vistos.append((model, config.thinking_config))
            Models.n += 1
            if Models.n == 1:
                raise RuntimeError("ReadTimeout: The read operation timed out")  # tentativa travada: tenta de novo
            return SimpleNamespace(text='{"observacoes": "ok"}')

    g = GeminiProvider("k", "gemini-3.1-pro-preview", SimpleNamespace(models=Models()), thinking="low")
    g.extract("s", "u", ex.tool_schema(SHEETS["outras_acoes"]))
    assert g.last_call["tentativas"] == 2 and g.last_call["tempos"][0]["r"] == "timeout"
    assert vistos[0][1].thinking_level.value.lower() == "low"
    assert g._thinking_cfg("gemini-2.5-flash").thinking_budget == 1024 and g._thinking_cfg("outro-modelo") is None
    assert GeminiProvider("k", "m", SimpleNamespace(models=Models()))._thinking_cfg("gemini-3-flash") is None  # vazio = padrão


def test_execucao_fiscal_prompt_is_chronology_only():
    texto = ex.load_prompt("execucoes_fiscais")
    for velho in ("Resumo de Bens e Valores Constritos", "Resumo de Defesas e Incidentes", "Expropriações e Leilões", "Próximo Passo do Processo"):
        assert velho not in texto  # os quatro blocos de resumo saíram
    assert "ordem cronológica crescente" in texto and "[ORIGEM] - [dd/mm/aaaa]" in texto
    assert "Não informado no extrato" in texto and "mero expediente" in texto


def test_extraction_error_clears_on_retry_and_on_pdf_delete_and_times_are_recorded(env):
    c, llm, tmp = env
    cid = c.post("/api/cases", json={"nome": "G"}).json()["id"]
    make_pdf(tmp / "a.pdf")
    pdf = (tmp / "a.pdf").read_bytes()
    d = c.post("/api/documents", files={"file": (f"{NUM}_V001.pdf", pdf, "application/pdf")}, data={"case_id": cid}).json()
    w = wait(c, d["id"])
    doc = c.get(f"/api/documents/{d['id']}").json()
    assert doc["started_at"] and doc["finished_at"] and doc["finished_at"] >= doc["started_at"]  # leitura do PDF anotada
    proc = c.get(f"/api/cases/{cid}/processes/{NUM}").json()
    assert proc["ext_status"] == "ok" and proc["ext_started_at"] and proc["ext_finished_at"]

    def boom(system, user, schema):
        raise RuntimeError("503 UNAVAILABLE falha de teste")
    llm.extract = boom
    c.post(f"/api/cases/{cid}/processes/{NUM}/reextract")
    for _ in range(100):
        p = c.get(f"/api/cases/{cid}/processes/{NUM}").json()
        if p["stale"] == 0 and p["error"]:
            break
        time.sleep(0.1)
    assert p["error"] and p["ext_status"] == "erro" and p["ext_finished_at"]  # erro também tem fim registrado

    c.delete(f"/api/documents/{d['id']}")
    for _ in range(100):
        p = c.get(f"/api/cases/{cid}/processes/{NUM}").json()
        if p["stale"] == 0:
            break
        time.sleep(0.1)
    assert p["error"] is None  # o erro sai junto com o PDF



def test_gemini_minimal_downgrades_to_low_and_gen3_drops_temperature(monkeypatch):
    from app import llm
    from app.llm import GeminiProvider

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    vistos = []

    class Models:
        def generate_content(self, model, contents, config):
            nivel = config.thinking_config.thinking_level.value.lower() if config.thinking_config else None
            vistos.append((model, nivel, config.temperature))
            if model == "principal" and nivel == "minimal":
                raise RuntimeError("400 INVALID_ARGUMENT: thinking level minimal is not supported")
            return SimpleNamespace(text='{"observacoes": "ok"}')

    g = GeminiProvider("k", "gemini-3.5-flash-lite", SimpleNamespace(models=Models()), thinking="minimal",
                       fallback_model="gemini-3.8-flash", thinking_fallback="low")
    schema = ex.tool_schema(SHEETS["outras_acoes"])
    g._call(model="gemini-3.8-flash", contents="u", config=g._config("s"))  # reserva usa o nível próprio
    assert vistos[-1] == ("gemini-3.8-flash", "low", None)  # e a temperatura sai nos modelos da família 3
    g.model = "gemini-3.5-flash-lite"
    g.extract("s", "u", schema)
    assert vistos[-1][1] == "minimal" and vistos[-1][2] is None


def test_delete_case_removes_everything_and_list_has_summary(env):
    c, llm, tmp = env
    cid = c.post("/api/cases", json={"nome": "Apagar"}).json()["id"]
    outro = c.post("/api/cases", json={"nome": "Fica"}).json()["id"]
    make_pdf(tmp / "a.pdf")
    d = c.post("/api/documents", files={"file": (f"{NUM}_V001.pdf", (tmp / "a.pdf").read_bytes(), "application/pdf")}, data={"case_id": cid}).json()
    wait(c, d["id"])
    resumo = next(x for x in c.get("/api/cases").json() if x["id"] == cid)
    assert resumo["pdfs"] == 1 and resumo["processos"] == 1 and resumo["extraidos"] == 1 and resumo["atividade"]
    doc_path = c.get(f"/api/documents/{d['id']}").json()["path"]
    r = c.delete(f"/api/cases/{cid}")
    assert r.status_code == 200 and r.json()["documentos"] == 1
    assert [x["id"] for x in c.get("/api/cases").json()] == [outro]
    assert c.get(f"/api/documents/{d['id']}").status_code == 404 and not __import__("pathlib").Path(doc_path).exists()
    assert c.delete(f"/api/cases/{cid}").status_code == 404


def test_timeout_504_has_its_own_orientation():
    from app.worker import explain_llm_error
    msg = explain_llm_error(RuntimeError("504 DEADLINE_EXCEEDED. Deadline expired before operation could complete."))
    assert msg.startswith("O Gemini não respondeu a tempo (504)") and "Detalhe técnico:" in msg


def test_health_shows_version_and_model_and_static_is_not_cached(env):
    from app import APP_VERSION
    c, _, _ = env
    h = c.get("/api/health").json()
    assert h["versao"] == APP_VERSION and "modelo" in h and "raciocinio" in h
    r = c.get("/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


def test_compose_passes_the_llm_settings_to_the_containers():
    texto = open("docker-compose.yml", encoding="utf-8").read()
    for var in ("GEMINI_THINKING", "GEMINI_THINKING_FALLBACK", "GEMINI_TIMEOUT_S", "GEMINI_MODEL", "GEMINI_FALLBACK_MODEL", "CONTEXT_BUDGET_CHARS"):
        assert f"  {var}:" in texto  # sem isso, o valor do .env não chega ao container


def test_load_dotenv_reads_values_without_overriding(tmp_path, monkeypatch):
    from app.config import load_dotenv
    f = tmp_path / ".env"
    f.write_text('# comentário\nA_X=um\nB_X="dois três"\nC_X=quatro # fim\nJA_TEM=novo\n\nlinha-sem-igual\n', encoding="utf-8")
    for k in ("A_X", "B_X", "C_X"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JA_TEM", "original")
    assert load_dotenv(f) == 3
    import os
    assert (os.environ["A_X"], os.environ["B_X"], os.environ["C_X"], os.environ["JA_TEM"]) == ("um", "dois três", "quatro", "original")
    assert load_dotenv(tmp_path / "nao-existe") == 0


@pytest.mark.parametrize("nome", ["outras_acoes", "recursos"])
def test_other_prompts_are_chronology_only_like_the_execution_prompt(nome):
    texto = ex.load_prompt(nome)
    for velho in ("Resumo da Origem", "Status Atual (Último Parágrafo)", "Resumo do Recurso", "Pedidos da Parte Recorrente", "1. ", "2. ", "3. "):
        assert velho not in texto  # sem blocos de resumo numerados
    assert "ordem cronológica crescente" in texto and "[ORIGEM] - [dd/mm/aaaa]" in texto
    assert "Não informado no extrato" in texto and "mero expediente" in texto and "Eu fornecerei" in texto


def test_format_cronologia_one_act_per_line():
    t = "- Evento 1, pg. 1 – 12/03/2012 – Ajuizada. Evento 5, pg. 2 - 20/04/2012 - Citação;\n2. Evento 9 - 01/01/2013 - Sentença que\njulga procedente."
    assert "\n\n" in ex.format_cronologia(t) and ex.format_cronologia(ex.format_cronologia(t)) == ex.format_cronologia(t)
    assert [x for x in ex.format_cronologia(t).split("\n") if x] == [
        "Evento 1, pg. 1 - 12/03/2012 - Ajuizada.", "Evento 5, pg. 2 - 20/04/2012 - Citação",
        "Evento 9 - 01/01/2013 - Sentença que julga procedente."]
    assert ex.format_cronologia("Não informado no extrato") == "Não informado no extrato" and ex.format_cronologia(None) is None


def test_manual_flag_blocks_ai_writes_note_in_sheet_and_export(env):
    c, llm, tmp = env
    cid = c.post("/api/cases", json={"nome": "M"}).json()["id"]
    make_pdf(tmp / "a.pdf")
    d = c.post("/api/documents", files={"file": (f"{NUM}_V001.pdf", (tmp / "a.pdf").read_bytes(), "application/pdf")}, data={"case_id": cid}).json()
    wait(c, d["id"])
    n_chamadas = len(llm.calls)
    r = c.put(f"/api/cases/{cid}/processes/{NUM}/manual", json={"manual": True, "nota": "Feito no SAJ por mim"})
    assert r.status_code == 200
    sheet = c.get(f"/api/cases/{cid}/sheet").json()
    lin = next(x for x in sheet["linhas"] if x["numero"] == NUM)
    assert lin["estado"] == "manual" and lin["manual"]["ativo"] and lin["manual"]["nota"] == "Feito no SAJ por mim"
    assert c.post(f"/api/cases/{cid}/processes/{NUM}/reextract").status_code == 409  # a IA não refaz
    assert len(llm.calls) == n_chamadas
    # feito à mão: a planilha mostra só a marcação (sem valores da IA); o resultado da IA fica guardado
    assert lin["valores"]["observacoes"] == "Processo analisado manualmente. Feito no SAJ por mim" and not lin["citacoes"]
    p = c.get(f"/api/cases/{cid}/processes/{NUM}").json()
    assert p["manual"] == 1 and p["result"]
    r = c.put(f"/api/cases/{cid}/processes/{NUM}/manual", json={"manual": False})
    assert r.status_code == 200 and c.get(f"/api/cases/{cid}/processes/{NUM}").json()["manual"] == 0
    volta = next(x for x in c.get(f"/api/cases/{cid}/sheet").json()["linhas"] if x["numero"] == NUM)
    assert volta["estado"] == "pronto" and "analisado manualmente" not in (volta["valores"].get("observacoes") or "")  # o texto da IA volta
    assert c.put(f"/api/cases/{cid}/processes/NAO/manual", json={"manual": True}).status_code == 404


def test_manual_note_is_written_in_the_exported_spreadsheet(env):
    c, _, _ = env
    cid = c.post("/api/cases", json={"nome": "M2"}).json()["id"]
    c.post(f"/api/cases/{cid}/mcfly", files={"file": ("m.xlsx", mcfly_xlsx(), "application/octet-stream")})
    numero = c.get(f"/api/cases/{cid}").json()["processos"][0]["numero"]
    c.put(f"/api/cases/{cid}/processes/{numero}/manual", json={"manual": True, "nota": "Concluído em 05/11"})
    wb = load_workbook(io.BytesIO(c.get(f"/api/cases/{cid}/spreadsheet").content))
    achou = [cel.value for ws in wb.worksheets for row in ws.iter_rows() for cel in row if isinstance(cel.value, str) and "analisado manualmente" in cel.value]
    assert achou and "Concluído em 05/11" in achou[0]


def test_medicoes_pdf_endpoint_returns_a_pdf_without_the_word_caso_in_the_title(env):
    import pymupdf
    c, _, tmp = env
    cid = c.post("/api/cases", json={"nome": "Grupo X"}).json()["id"]
    make_pdf(tmp / "a.pdf")
    d = c.post("/api/documents", files={"file": (f"{NUM}_V001.pdf", (tmp / "a.pdf").read_bytes(), "application/pdf")}, data={"case_id": cid}).json()
    wait(c, d["id"])
    r = c.get(f"/api/cases/{cid}/medicoes.pdf?tz=180")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and "Medicoes" in r.headers["content-disposition"]
    texto = "".join(p.get_text() for p in pymupdf.open(stream=r.content))
    assert "Relatório de medições" in texto and "Grupo X" in texto and NUM in texto and "Registro de execução" in texto
    assert "Medições do caso" not in texto
    assert "Medições do caso" not in open("static/index.html", encoding="utf-8").read()
    assert c.get("/api/cases/inexistente/medicoes.pdf").status_code == 404


def _caso_com_pdf(c, tmp, nome="H"):
    cid = c.post("/api/cases", json={"nome": nome}).json()["id"]
    make_pdf(tmp / "a.pdf")
    d = c.post("/api/documents", files={"file": (f"{NUM}_V001.pdf", (tmp / "a.pdf").read_bytes(), "application/pdf")}, data={"case_id": cid}).json()
    wait(c, d["id"])
    return cid, d


def test_history_records_versions_and_restores_a_previous_one(env):
    c, _, tmp = env
    cid, _ = _caso_com_pdf(c, tmp)
    base = f"/api/cases/{cid}/processes/{NUM}"
    v0 = c.get(f"{base}/history").json()["versoes"]
    assert [v["origem"] for v in v0] == ["Extração pela IA"]
    original = c.get(base).json()["result"]["fields"]["observacoes"]
    assert c.put(f"{base}/review", json={"observacoes": "Primeira edição", "reviewed": False}).status_code == 200
    assert c.put(f"{base}/review", json={"observacoes": "Primeira edição", "reviewed": False}).status_code == 200  # sem mudança: não cria versão
    c.put(f"{base}/review", json={"observacoes": "Texto do analista", "reviewed": True})
    vs = c.get(f"{base}/history").json()["versoes"]
    assert len(vs) == 3 and vs[0]["origem"] == "Edição do analista" and "Observações" in vs[0]["resumo"] and "revisado" in vs[0]["resumo"]
    det = c.get(f"{base}/history/{vs[-1]['id']}").json()
    assert any(x["titulo"].startswith("Observações") for x in det["campos"]) and det["reviewed"] is False
    assert c.post(f"{base}/history/{vs[-1]['id']}/restore").status_code == 200  # volta à extração original
    p = c.get(base).json()
    assert p["result"]["fields"]["observacoes"] == original and p["reviewed"] == 0
    assert c.get(f"{base}/history").json()["versoes"][0]["origem"] == "Restauração"
    assert c.post(f"{base}/history/nao-existe/restore").status_code == 404


def test_hide_and_delete_a_single_process(env):
    c, _, tmp = env
    cid, d = _caso_com_pdf(c, tmp)
    c.post(f"/api/cases/{cid}/mcfly", files={"file": ("m.xlsx", mcfly_xlsx(), "application/octet-stream")})
    base = f"/api/cases/{cid}/processes/{NUM}"
    assert c.put(f"{base}/hidden", json={"oculto": True}).status_code == 200
    sheet = c.get(f"/api/cases/{cid}/sheet").json()
    assert next(l for l in sheet["linhas"] if l["numero"] == NUM)["oculto"] is True
    aba = next(l for l in sheet["linhas"] if l["numero"] == NUM)["aba"]
    nome = {"execucoes_fiscais": "Execuções Fiscais", "outras_acoes": "Outras Ações"}[aba]
    visiveis = next(a for a in sheet["abas"] if a["key"] == aba)["n"]
    wb = load_workbook(io.BytesIO(c.get(f"/api/cases/{cid}/spreadsheet").content))
    linhas_oculto = wb[nome].max_row
    assert wb["Relatório"].max_row == 2 and NUM not in [r[0].value for r in wb[nome].iter_rows()]  # o oculto não vai à planilha
    c.put(f"{base}/hidden", json={"oculto": False})
    wb = load_workbook(io.BytesIO(c.get(f"/api/cases/{cid}/spreadsheet").content))
    assert wb[nome].max_row == linhas_oculto + 1 and wb["Relatório"].max_row == 3
    assert next(a for a in c.get(f"/api/cases/{cid}/sheet").json()["abas"] if a["key"] == aba)["n"] == visiveis + 1
    r = c.delete(base)
    assert r.status_code == 200 and r.json()["documentos"] == 1
    assert c.get(base).status_code == 404 and c.get(f"/api/documents/{d['id']}").status_code == 404
    assert c.get(f"{base}/history").status_code == 404 and c.delete(base).status_code == 404
    assert len(c.get(f"/api/cases/{cid}").json()["processos"]) == 1  # o outro processo do caso continua


def test_fraud_items_are_suggestions_until_the_analyst_authorizes(env):
    c, llm, tmp = env
    llm.payload["fraudes"] = [{"tipo": "fraude a execucao", "analise": "deferido", "valor_causa": None, "resumo": "PGFN alegou; juiz deferiu", "citacao": "VOL 001, pg. 2"},
                              {"tipo": "blindagem societária", "analise": "talvez"}]
    cid, _ = _caso_com_pdf(c, tmp)
    c.post(f"/api/cases/{cid}/mcfly", files={"file": ("m.xlsx", mcfly_xlsx(), "application/octet-stream")})
    base = f"/api/cases/{cid}/processes/{NUM}"
    sheet = c.get(f"/api/cases/{cid}/sheet").json()
    fr = sheet["fraudes"]
    assert [f["tipo"] for f in fr] == ["FRAUDE À EXECUÇÃO", "OUTRAS FRAUDES"] and fr[0]["analise"] == "DEFERIDO" and fr[1]["analise"] is None
    assert all(f["_st"] == "sugerido" for f in fr) and fr[0]["classe"] == "Execução Fiscal (SIDA)" and "blindagem" in fr[1]["resumo"]
    assert sheet["listas"]["fraude_tipo"][0] == "FRAUDE À EXECUÇÃO" and next(l for l in sheet["linhas"] if l["numero"] == NUM)["sugestoes"] == 3
    aba = lambda: load_workbook(io.BytesIO(c.get(f"/api/cases/{cid}/spreadsheet").content))["Alegações de Fraude"]
    assert aba().max_row == 1  # nada vai à planilha sem autorização
    assert c.put(f"{base}/itens", json={"lista": "fraudes", "acao": "editar", "indices": [1], "valores": {"tipo": "INVENTADO"}}).status_code == 400
    assert c.put(f"{base}/itens", json={"lista": "fraudes", "acao": "editar", "indices": [1], "valores": {"tipo": "SIMULAÇÃO", "analise": "INDEFERIDO"}}).status_code == 200
    c.put(f"{base}/itens", json={"lista": "fraudes", "acao": "autorizar", "indices": [0]})
    c.put(f"{base}/itens", json={"lista": "fraudes", "acao": "descartar", "indices": [1]})
    ws = aba()
    assert ws.max_row == 2 and [x.value for x in ws[2]][:4] == [NUM, "Execução Fiscal (SIDA)", "FRAUDE À EXECUÇÃO", "DEFERIDO"]
    depois = c.get(f"/api/cases/{cid}/sheet").json()
    assert [f["_st"] for f in depois["fraudes"]] == ["autorizado"]  # o descartado some da aba
    assert next(a for a in depois["abas"] if a["key"] == "fraude")["n"] == 1
    assert c.put(f"{base}/itens", json={"lista": "x", "acao": "autorizar"}).status_code == 400


def test_bens_export_shows_matricula_only_on_the_first_row_of_each_property():
    from app.template import item_row
    p = {"numero": NUM, "mcfly": {}}
    a = {"matricula": "28.749 (CRI Formiga)", "caracteristicas": "Gleba", "proprietario": "Alfa", "ato": "R-14", "gravames": "Penhora", "_st": "autorizado", "citacao": "x"}
    b = {**a, "ato": "R-15"}
    assert item_row("bens", p, a)["matricula"].startswith("28.749") and "citacao" not in item_row("bens", p, a)
    segunda = item_row("bens", p, b, a)
    assert "matricula" not in segunda and "proprietario" not in segunda and segunda["ato"] == "R-15"


def test_template_optional_columns_get_order_number_and_analysis_status():
    from app import template as tpl
    wb = Workbook()
    ws = wb.active
    ws.title = "Execuções Fiscais"
    ws.append(["Nº", "Número do processo judicial", "EXIT. GARANTIA", "Situação da análise"])
    ws.append([None, NUM, None, None])
    buf = io.BytesIO()
    wb.save(buf)
    assert "dicas" not in tpl.analyze(buf.getvalue()) or not tpl.analyze(buf.getvalue())["dicas"]
    procs = [{"numero": NUM, "mcfly": {"classe": "Execução Fiscal"}, "manual": 1, "manual_note": "no SAJ", "result": None},
             {"numero": "0000009-99.2020.4.01.3811", "mcfly": {"classe": "Execução Fiscal"}, "reviewed": 1,
              "result": {"sheet": "execucoes_fiscais", "fields": {"garantia": "IMÓVEL"}, "bens": [], "fraudes": []}}]
    out = load_workbook(io.BytesIO(tpl.fill(buf.getvalue(), procs)))["Execuções Fiscais"]
    assert [c.value for c in out[2]] == [1, NUM, None, "Feito manualmente. no SAJ"]
    assert [c.value for c in out[3]] == [2, "0000009-99.2020.4.01.3811", "IMÓVEL", "Revisado"]
    # modelo sem as colunas: a análise avisa que elas são opcionais
    wb2 = Workbook()
    wb2.active.title = "Execuções Fiscais"
    wb2.active.append(["Número do processo judicial", "EXIT. GARANTIA"])
    b2 = io.BytesIO()
    wb2.save(b2)
    assert "Situação da análise" in tpl.analyze(b2.getvalue())["dicas"][0]


def test_dates_money_and_art40_list_are_standardized_by_code():
    from app.schemas import ARQUIVADA_40, fmt_arquivada, fmt_data_br, fmt_moeda_br
    assert [fmt_data_br(x) for x in ("2018-09-28", "28.9.18", "5-10-2006", "28 de setembro de 2018", "28/09/2018", "sem data")] == \
        ["28/09/2018", "28/09/2018", "05/10/2006", "28/09/2018", "28/09/2018", "sem data"]
    assert [fmt_moeda_br(x) for x in ("2627732.00", "R$2.627.732,00", "2,627,732.00", "1.250,4", "1.250", "746019", "R$ 0", "R$ 10 mil (aprox.)")] == \
        ["R$ 2.627.732,00", "R$ 2.627.732,00", "R$ 2.627.732,00", "R$ 1.250,40", "R$ 1.250,00", "R$ 746.019,00", "R$ 0,00", "R$ 10 mil (aprox.)"]
    assert ARQUIVADA_40 == ["Não", "< 3 anos", "> 3 anos"]
    assert [fmt_arquivada(x, True) for x in ("NÃO", "menos de 3 anos", "> 3 ANOS", "SIM", None)] == ["Não", "< 3 anos", "> 3 anos", None, None]
    sheet = SHEETS["execucoes_fiscais"]
    out = ex.validate(sheet, {"valor_garantia": "746019.6", "data_garantia": "2006-10-05", "arquivada_40": "SIM", "observacoes": "x"})
    assert out["valor_garantia"] == "R$ 746.019,60" and out["data_garantia"] == "05/10/2006"
    assert out["arquivada_40"] is None and "arquivada_40" in out["_revisar"]  # 'SIM' sem o prazo não cabe na lista: o analista escolhe
    assert next(f for f in sheet.fields if f.key == "arquivada_40").options == ARQUIVADA_40
    assert ex.normalize_fraude({"tipo": "SIMULAÇÃO", "valor_causa": "128400.11"})["valor_causa"] == "R$ 128.400,11"


def test_pgfn_guidelines_file_is_appended_to_the_prompt_without_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "PROMPTS_DIR", tmp_path)
    (tmp_path / "outras_acoes.md").write_text("PROMPT BASE", encoding="utf-8")
    assert ex.load_orientacoes() == ""
    (tmp_path / "orientacoes_pgfn.md").write_text("<!-- instruções\nde uso -->\nRegra da casa: X.\n", encoding="utf-8")
    assert ex.load_orientacoes() == "Regra da casa: X."
    visto = {}

    class L:
        def extract(self, system, user, schema):
            visto["s"] = system
            return {"observacoes": "ok"}

    ex.extract_with_llm(SHEETS["outras_acoes"], "outras_acoes", "ctx", L())
    assert "ORIENTAÇÕES DA PGFN" in visto["s"] and "Regra da casa: X." in visto["s"] and "instruções" not in visto["s"]
    assert "Datas SEMPRE no formato dd/mm/aaaa" in visto["s"] and "R$ 1.234,56" in visto["s"] and "Data de hoje:" in visto["s"]


def test_review_normalizes_typed_date_and_value(env):
    c, _, tmp = env
    cid = c.post("/api/cases", json={"nome": "N"}).json()["id"]
    c.post(f"/api/cases/{cid}/mcfly", files={"file": ("m.xlsx", mcfly_xlsx(), "application/octet-stream")})
    make_pdf(tmp / "a.pdf")
    d = c.post("/api/documents", files={"file": (f"{NUM}_V001.pdf", (tmp / "a.pdf").read_bytes(), "application/pdf")}, data={"case_id": cid}).json()
    wait(c, d["id"])
    base = f"/api/cases/{cid}/processes/{NUM}"
    lin = next(x for x in c.get(f"/api/cases/{cid}/sheet").json()["linhas"] if x["numero"] == NUM)
    assert lin["valores"]["data_garantia"] == "28/09/2018" and lin["valores"]["arquivada_40"] == "Não"  # a IA respondeu 2018-09-28 e NÃO
    assert c.put(f"{base}/review", json={"fields": {"data_garantia": "2020-01-31", "valor_garantia": "1500"}, "reviewed": False}).status_code == 200
    f = c.get(base).json()["result"]["fields"]
    assert f["data_garantia"] == "31/01/2020" and f["valor_garantia"] == "R$ 1.500,00"
    assert c.put(f"{base}/review", json={"fields": {"arquivada_40": "SIM"}, "reviewed": False}).status_code == 400
    assert c.put(f"{base}/review", json={"fields": {"arquivada_40": "> 3 anos"}, "reviewed": False}).status_code == 200


def _mcfly_bagunçado():
    wb = Workbook()
    ws = wb.active
    ws.title = "Mcfly"
    ws.append(["NÚMERO DO PROCESSO CNJ", "Polo ativo", "Polo passivo", "Classe SAJ", "Inscrições processo principal",
               "Somatório inscrições (processo principal e vinculados)"])
    ws.append([NUM, "FAZENDA NACIONAL", "FERDIL INTERMEDIACAO LTDA", "Execução Fiscal (SIDA)", "60 6 05 009561-42", 759527.01])
    ws.append([NUM, "FAZENDA NACIONAL", "FERDIL INTERMEDIACAO LTDA", "Execução Fiscal (SIDA)", "60 2 05 001111-11", 800000])  # repetido, valores diferentes
    ws.append(["0000002-00.2020.4.01.0000", "FAZENDA NACIONAL", "FERDIL INTERMEDIACAO LTDA", "Execução Fiscal (SIDA)", "60 6 06 000001-01", 1000.5])
    ws.append(["0000002-00.2020.4.01.0000", "FAZENDA NACIONAL", "FERDIL INTERMEDIACAO LTDA", "Execução Fiscal (SIDA)", "60 6 06 000001-01", 1000.5])  # idêntico
    ws.append(["0000003-00.2021.4.01.0000", "FAZENDA NACIONAL", "PADARIA DO JOAO ME", "Execução Fiscal (SIDA)", "60 6 07 000002-02", 50])  # por engano
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_mcfly_preview_flags_repeated_conflicting_and_foreign_processes_and_imports_the_selection(env):
    c, _, _ = env
    cid = c.post("/api/cases", json={"nome": "Grupo Ferdil"}).json()["id"]
    c.post(f"/api/cases/{cid}/mcfly", files={"file": ("m.xlsx", mcfly_xlsx(), "application/octet-stream")})  # NUM já está no caso
    r = c.post(f"/api/cases/{cid}/mcfly/preview", files={"file": ("x.xlsx", _mcfly_bagunçado(), "application/octet-stream")}).json()
    assert (r["linhas"], len(r["processos"]), r["repetidos"], r["conflitos"], r["suspeitos"], r["novos"]) == (5, 3, 2, 1, 1, 2)
    assert r["devedor"] == ["ferdil"] and r["fora_do_mcfly"] == ["0000001-00.2020.4.01.0000"]
    por = {p["numero"]: p for p in r["processos"]}
    a, b, estranho = por[NUM], por["0000002-00.2020.4.01.0000"], por["0000003-00.2021.4.01.0000"]
    assert a["linhas"] == 2 and a["inscricoes"] == "60 6 05 009561-42\n60 2 05 001111-11" and a["no_caso"] and a["marcar"]
    assert a["conflitos"] == {"somatorio": ["R$ 759.527,01", "R$ 800.000,00"]} and a["somatorio"] == "R$ 759.527,01"
    assert b["linhas"] == 2 and not b["conflitos"] and b["somatorio"] == "R$ 1.000,50" and b["marcar"] and not b["no_caso"]
    assert estranho["suspeito"] and not estranho["marcar"]  # novo e de outra parte: vem desmarcado
    assert r["processos"][0]["numero"] == estranho["numero"]  # suspeitos primeiro
    imp = c.post(f"/api/cases/{cid}/mcfly", files={"file": ("x.xlsx", _mcfly_bagunçado(), "application/octet-stream")},
                 data={"selecionados": '["%s", "0000002-00.2020.4.01.0000"]' % NUM, "escolhas": '{"%s": {"somatorio": "R$ 800.000,00"}}' % NUM}).json()
    assert imp == {"importados": 2, "novos": 1, "atualizados": 1}
    procs = {p["numero"]: p for p in c.get(f"/api/cases/{cid}").json()["processos"]}
    assert "0000003-00.2021.4.01.0000" not in procs and procs[NUM]["mcfly"]["somatorio"] == "R$ 800.000,00"
    assert procs[NUM]["mcfly"]["juizo"] == "02ª Vara - FORMIGA"  # o que o cadastro já tinha e o Mcfly novo não trouxe continua
    wb = load_workbook(io.BytesIO(c.get(f"/api/cases/{cid}/spreadsheet").content))
    ef = [[x.value for x in row] for row in wb["Execuções Fiscais"].iter_rows()]
    lin = dict(zip(ef[0], next(l for l in ef if l[0] == NUM)))
    assert lin["Inscrições processo principal"].startswith("60 6 05 009561-42") and lin["Somatório inscrições (processo principal e vinculados)"] == "R$ 800.000,00"


def test_mcfly_real_export_shape_dash_placeholders_party_groups_and_number_punctuation(env):
    """Formato do export real do SAJ: ' - ' em campo vazio, várias partes por célula ('NOME - CNPJ' por linha) e valor numérico."""
    from app.mcfly import analisar, read_mcfly
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatórios"
    ws.append(["Número do processo CNJ", "Polo ativo", "Polo passivo", "Classe SAJ", "Valor da causa", "Inscrições processo principal",
               "Somatório inscrições (processo principal e vinculados)"])
    A, B = "ALFA SIDERURGIA LTDA - 11.222.333/0001-81", "JOAO DA SILVA - 111.222.333-44"
    ws.append(["0000010-10.2020.4.01.3811", " - ", f"{A}\n{B}\n", "67 - Execução Fiscal (SIDA)", 3392.84, "60 2 99 000866-03 | ATIVA | R$ 10,00", "R$ 10,00"])
    ws.append(["0000011-11.2020.4.01.3811", " - ", f"BETA HOLDING LTDA - 22.333.444/0001-55\n{B}", "67 - Execução Fiscal (SIDA)", 10, " - ", "R$ 0,00"])
    ws.append(["0000012-12.2020.4.01.3811", "MINISTERIO DA FAZENDA - 00.394.460/0001-41", "OUTRA EMPRESA SA - 99.888.777/0001-66", "67 - Execução Fiscal (SIDA)", 5, " - ", "R$ 5,00"])
    buf = io.BytesIO()
    wb.save(buf)
    rows = read_mcfly(io.BytesIO(buf.getvalue()))
    assert len(rows) == 3 and "polo_ativo" not in rows[0] and rows[0]["valor_causa"] == "R$ 3.392,84" and "inscricoes" not in rows[1]
    r = analisar(io.BytesIO(buf.getvalue()), "Grupo Alfa", {"00000111120204013811": {"oculto": False}})  # já no caso, com outra pontuação
    assert [(g["processos"], g["do_caso"]) for g in r["grupos"]] == [(2, True), (1, False)]  # Alfa e Beta ligadas pelo sócio em comum
    assert r["grupos"][0]["cita_caso"] and r["grupos"][0]["no_caso"] == 1 and r["grupos"][1]["principais"] == ["OUTRA EMPRESA SA"]
    por = {p["numero"]: p for p in r["processos"]}
    assert por["0000011-11.2020.4.01.3811"]["no_caso"] and por["0000010-10.2020.4.01.3811"]["partes"] == ["ALFA SIDERURGIA LTDA", "JOAO DA SILVA"]
    assert por["0000012-12.2020.4.01.3811"]["suspeito"] and not por["0000012-12.2020.4.01.3811"]["marcar"] and r["fora_do_mcfly"] == []
    c, _, _ = env
    cid = c.post("/api/cases", json={"nome": "Grupo Alfa"}).json()["id"]
    imp = c.post(f"/api/cases/{cid}/mcfly", files={"file": ("x.xlsx", buf.getvalue(), "application/octet-stream")},
                 data={"selecionados": '["0000010-10.2020.4.01.3811"]'}).json()
    assert imp == {"importados": 1, "novos": 1, "atualizados": 0}


def test_chat_answer_becomes_chronology_lines():
    resp = ("Sim, existem valores bloqueados:\n\n* **R$ 1.125,61** em nome de Divino (Evento 68, pg. 3).\n"
            "* **R$ 233.644,19** em ativo escriturado (Evento 68, pgs. 4 a 5).\n* Sem origem nenhuma aqui.")
    linhas, modo = ex.atos_da_resposta("Existe valor bloqueado pelo SISBAJUD?", resp, None)
    assert modo == "regras" and linhas == ["Evento 68, pg. 3 - data não informada - R$ 1.125,61 em nome de Divino.",
                                           "Evento 68, pg. 4 - data não informada - R$ 233.644,19 em ativo escriturado."]

    class L:
        def generate(self, system, user, max_tokens=1000):
            assert "linhas de cronologia" in system and "SISBAJUD" in user
            return ("- **Evento 68, pg. 3** – 10/05/2024 – Bloqueio via Sisbajud de R$ 1.125,61 em nome de Divino.\n"
                    "Evento 99, pg. 1 - 01/01/2020 - Ato com origem que NÃO está na resposta.\nTexto solto.")
    linhas, modo = ex.atos_da_resposta("Existe valor bloqueado pelo SISBAJUD?", resp, L())
    assert modo == "ia" and linhas == ["Evento 68, pg. 3 - 10/05/2024 - Bloqueio via Sisbajud de R$ 1.125,61 em nome de Divino."]

    class Fora:
        def generate(self, *a, **k):
            raise RuntimeError("503 UNAVAILABLE")
    assert ex.atos_da_resposta("q", resp, Fora())[1] == "regras"  # IA fora do ar: cai para as regras
    assert ex.atos_da_resposta("q", "Não encontrei essa informação.", None) == ([], "regras")


def test_assistant_answer_goes_to_observations_in_chronology_format(env):
    c, llm, tmp = env
    cid, _ = _caso_com_pdf(c, tmp, "Obs")
    base = f"/api/cases/{cid}/processes/{NUM}"
    antes = c.get(base).json()["result"]["fields"]["observacoes"]
    r = c.post(f"{base}/observacoes/assistente", json={"pergunta": "Há bloqueio?", "resposta": "Sim: **R$ 48.320,15** bloqueados (VOL 001, pg. 2)."}).json()
    assert r["adicionadas"] == 1 and r["modo"] == "regras"  # o LLM de teste não devolve linhas no formato: valem as regras
    obs = c.get(base).json()["result"]["fields"]["observacoes"]
    assert antes.strip() in obs and obs.endswith("VOL 001, pg. 2 - data não informada - Sim: R$ 48.320,15 bloqueados.") and "**" not in obs
    assert "[Assistente" not in obs and c.get(base).json()["reviewed"] == 0
    assert c.post(f"{base}/observacoes/assistente", json={"pergunta": "Há bloqueio?", "resposta": "Sim: **R$ 48.320,15** bloqueados (VOL 001, pg. 2)."}).json()["adicionadas"] == 0  # não duplica
    assert c.post(f"{base}/observacoes/assistente", json={"pergunta": "x", "resposta": "Não encontrei."}).json()["adicionadas"] == 0
    assert c.get(f"{base}/history").json()["versoes"][0]["origem"] == "Resposta do assistente"


def test_standardize_list_converts_pasted_assistant_text_into_acts():
    t = ("Evento 307, pg. 1 - 23/09/2026 - Juntada de auto.\n\n[Assistente · Existe valor bloqueado?]\nSim, existem valores bloqueados:\n\n"
         "**R$ 1.125,61** em nome de Divino (Evento 68, pg. 3). **R$ 233.644,19** em ativo escriturado (Evento 68, pg. 4) e "
         "**R$ 5,00** (cumprida parcialmente) (Evento 68, pgs. 1 a 5).\n\nNota do analista, sem citação.")
    linhas = [x for x in ex.format_cronologia(t).split("\n") if x]
    assert linhas == ["Evento 307, pg. 1 - 23/09/2026 - Juntada de auto.",
                      "Evento 68, pg. 3 - data não informada - R$ 1.125,61 em nome de Divino.",
                      "Evento 68, pg. 4 - data não informada - R$ 233.644,19 em ativo escriturado.",
                      "Evento 68, pg. 1 - data não informada - R$ 5,00 (cumprida parcialmente).",
                      "Nota do analista, sem citação."]
    assert ex.format_cronologia("Evento 1 - 01/01/2020 - Sentença que\njulga procedente.") == "Evento 1 - 01/01/2020 - Sentença que julga procedente."


def test_chat_rebuilds_lost_index_and_reads_the_cited_event_directly(env, monkeypatch):
    from app import rag
    c, llm, tmp = env
    cid, d = _caso_com_pdf(c, tmp, "Chat")
    st = c.app.state if hasattr(c.app, "state") else None
    r1 = c.post("/api/chat", json={"question": "Existe valor bloqueado pelo SISBAJUD?", "case_id": cid, "numero": NUM}).json()
    assert r1["sources"]
    # simula a perda do índice (modo sem Docker reiniciado): a pergunta seguinte refaz o índice das páginas guardadas
    from app.search.memory import MemoryStore
    import gc
    for o in gc.get_objects():
        if isinstance(o, MemoryStore):
            o._rows.clear()
    r2 = c.post("/api/chat", json={"question": "Existe valor bloqueado pelo SISBAJUD?", "case_id": cid, "numero": NUM}).json()
    assert r2["sources"] and r2["mode"] != "none"
    # pergunta que cita um evento: as páginas dele vão direto para a IA
    pages = [{"page": 1, "text": "capa do processo", "refs": {}}, {"page": 2, "text": "Detalhamento da ordem judicial de bloqueio de valores", "refs": {"evento": "68", "pg": "1"}}]
    from app.config import Settings
    s = Settings()
    (s.data_dir / "pages").mkdir(parents=True, exist_ok=True)
    (s.data_dir / "pages" / "docx.json").write_text(__import__("json").dumps(pages), encoding="utf-8")
    hits = rag._citados("Pode avaliar o Evento 68?", [{"id": "docx"}], s)
    assert [h.page for h in hits] == [2] and rag._citados("Qual o último andamento?", [{"id": "docx"}], s) == []
