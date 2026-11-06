import io
import time

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app import template as tpl
from app.config import Settings
from app.main import create_app
from tests.make_pdf import make_pdf


def modelo():
    """Planilha do caso com layout próprio: cabeçalhos variantes, coluna desconhecida, abas manuais e aba em blocos."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório"
    ws.append(["Número do processo judicial", "Polo Ativo", "Polo Passivo", "Classe", "Juízo", "Procuradoria Responsável", "Data do Protocolo"])
    ws.append(["0341609-70.2005.8.13.0261", "FAZENDA NACIONAL", "EMPRESA ALFA", "Execução Fiscal (SIDA)", "02ª Vara - FORMIGA", "PRFN6", "01/09/2005"])
    ef = wb.create_sheet("Execuções Fiscais ")
    ef.append(["Número do processo judicial", "Polo Passivo", "Classe", "PROCESSO DIGITALIZADO", "EXIT. GARANTIA",
               "VALOR AVALIAÇÃO OU DEPÓSITO", "TENT. ALIEN.", "Coluna Nova do Analista", "Observações relavantes sobre o atual estágio da EF"])
    ef.append(["0341609-70.2005.8.13.0261", None, None, None, "IMÓVEL", "R$ 999,00 (digitado)", None, "anotação minha", None])
    dje = wb.create_sheet("DJE-SDJ")
    dje.append(["Ident. Contribuinte", "Conta Corrente", "Valor Depósito"])
    dje.append(["65123804000123", "975280005019985", 9574.3])
    bens = wb.create_sheet("Outros Bens")
    bens.append(["Matrícula nº 20.753 – CRI Três Corações/MG"])  # layout em blocos: não é tabela
    bens.append(["Ato/Data/Registro", "Partes/Interessados", "Valor"])
    wb.create_sheet("Recuperação Judicial").append(["Recuperação Judicial nº 5005360-43.2023.8.13.0693"])
    wb.create_sheet("Mcfly").append(["Número do processo CNJ", "Classe SAJ"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def procs():
    return [
        {"numero": "0341609-70.2005.8.13.0261", "classe": "Execução Fiscal (SIDA)", "reviewed": 0,
         "mcfly": {"polo_passivo": "EMPRESA ALFA", "classe": "Execução Fiscal (SIDA)", "juizo": "02ª Vara - FORMIGA"},
         "result": {"sheet": "execucoes_fiscais", "fields": {"digitalizado": "SIM", "garantia": "BACEN C/DJE", "valor_garantia": "R$ 1,00",
                                                            "tent_alien": "SIM", "observacoes": "Evento 14, pg. 1 - Penhora."},
                    "bens": [{"matricula": "28.749", "caracteristicas": "Gleba"}], "fraudes": []}},
        {"numero": "0000002-00.2020.4.01.0000", "classe": "Execução Fiscal (SIDA)", "reviewed": 1,
         "mcfly": {"polo_passivo": "BETA", "classe": "Execução Fiscal (SIDA)"},
         "result": {"sheet": "execucoes_fiscais", "fields": {"digitalizado": "SIM", "garantia": "IMÓVEL"}, "bens": [], "fraudes": []}},
        {"numero": "0000003-00.2020.4.01.0000", "classe": None, "reviewed": 0,
         "mcfly": {"polo_passivo": "GAMA", "classe": "Mandado de Segurança"}, "result": None},
    ]


def test_analyze_reports_recognized_unrecognized_and_untouched_sheets():
    a = tpl.analyze(modelo())
    abas = {x["aba"]: x for x in a["abas"]}
    assert abas["Relatório"]["preenchida"] and "Número do processo judicial" in abas["Relatório"]["reconhecidas"]
    ef = abas["Execuções Fiscais "]
    assert ef["nao_reconhecidas"] == ["Coluna Nova do Analista"]
    assert "EXIT. GARANTIA" in ef["reconhecidas"]
    assert abas["Outros Bens"]["preenchida"] is False and "layout diferente" in abas["Outros Bens"]["motivo"]
    assert set(a["ignoradas"]) == {"DJE-SDJ", "Recuperação Judicial", "Mcfly"}
    assert set(a["ausentes"]) == {"Outras Ações", "Embargos à Execução", "Alegações de Fraude"}


def test_fill_updates_only_empty_cells_appends_new_rows_and_keeps_other_tabs():
    out = load_workbook(io.BytesIO(tpl.fill(modelo(), procs())))
    ef = [[c.value for c in r] for r in out["Execuções Fiscais "].iter_rows()]
    h = ef[0]
    existente = dict(zip(h, ef[1]))
    assert existente["EXIT. GARANTIA"] == "IMÓVEL"                          # digitado: preservado
    assert existente["VALOR AVALIAÇÃO OU DEPÓSITO"] == "R$ 999,00 (digitado)"  # digitado: preservado
    assert existente["Coluna Nova do Analista"] == "anotação minha"
    assert existente["PROCESSO DIGITALIZADO"] == "SIM" and existente["TENT. ALIEN."] == "SIM"  # vazias: preenchidas
    assert existente["Observações relavantes sobre o atual estágio da EF"].startswith("Evento 14")
    novo = dict(zip(h, ef[2]))
    assert novo["Número do processo judicial"] == "0000002-00.2020.4.01.0000" and novo["EXIT. GARANTIA"] == "IMÓVEL"
    assert len(ef) == 3  # o processo 3 (Mandado de Segurança) não pertence a esta aba
    # processo sem PDF entra no Relatório (capa) mesmo sem extração
    rel = [r[0].value for r in out["Relatório"].iter_rows(min_row=2)]
    assert rel == ["0341609-70.2005.8.13.0261", "0000002-00.2020.4.01.0000", "0000003-00.2020.4.01.0000"]
    # abas manuais e em blocos intactas
    assert [[c.value for c in r] for r in out["DJE-SDJ"].iter_rows()][1] == ["65123804000123", "975280005019985", 9574.3]
    assert out["Recuperação Judicial"]["A1"].value.startswith("Recuperação Judicial nº")
    assert out["Outros Bens"].max_row == 2
    # células escritas de processo não revisado ficam em amarelo; revisado, não
    assert out["Execuções Fiscais "].cell(2, 4).fill.fgColor.rgb.endswith("FFF3CD")
    assert not out["Execuções Fiscais "].cell(3, 5).fill.fgColor.rgb.endswith("FFF3CD")


def test_api_template_flow_warns_and_export_uses_the_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("QUEUE_BACKEND", "memory")
    monkeypatch.setenv("SEARCH_BACKEND", "memory")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with TestClient(create_app(Settings())) as c:
        case = c.post("/api/cases", json={"nome": "Alfa"}).json()
        assert c.get(f"/api/cases/{case['id']}/template").json() == {"ativo": False}
        r = c.post(f"/api/cases/{case['id']}/template", files={"file": ("m.xlsx", modelo())}).json()
        assert any(a["nao_reconhecidas"] == ["Coluna Nova do Analista"] for a in r["abas"])
        assert c.post(f"/api/cases/{case['id']}/template", files={"file": ("m.xlsx", b"nao e xlsx")}).status_code == 400
        pdf = tmp_path / "0341609-70.2005.8.13.0261_V001_001-1-4.pdf"
        make_pdf(pdf, image_page=False)
        with pdf.open("rb") as f:
            up = c.post("/api/documents", data={"case_id": case["id"], "tipo": "execucao_fiscal"}, files={"file": (pdf.name, f, "application/pdf")}).json()
        for _ in range(100):
            d = c.get(f"/api/documents/{up['id']}").json()
            if d["status"] == "done":
                break
            time.sleep(0.1)
        time.sleep(0.5)
        wb = load_workbook(io.BytesIO(c.get(f"/api/cases/{case['id']}/spreadsheet").content))
        assert "Recuperação Judicial" in wb.sheetnames and "Mcfly" in wb.sheetnames  # abas do modelo preservadas
        assert wb["Execuções Fiscais "]["E2"].value == "IMÓVEL"  # célula digitada preservada
        assert c.delete(f"/api/cases/{case['id']}/template").json() == {"ativo": False}
        wb2 = load_workbook(io.BytesIO(c.get(f"/api/cases/{case['id']}/spreadsheet").content))
        assert "Recuperação Judicial" not in wb2.sheetnames  # sem modelo: layout padrão


def test_template_registers_the_cases_processes_without_a_separate_mcfly_import(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with TestClient(create_app(Settings())) as c:
        # 1) sem aba Mcfly com dados: os processos vêm da aba Relatório
        case = c.post("/api/cases", json={"nome": "A"}).json()
        r = c.post(f"/api/cases/{case['id']}/template", files={"file": ("m.xlsx", modelo())}).json()
        assert r["processos_importados"] == 1
        p = c.get(f"/api/cases/{case['id']}").json()["processos"]
        assert p[0]["numero"] == "0341609-70.2005.8.13.0261" and p[0]["mcfly"]["classe"] == "Execução Fiscal (SIDA)"
        assert p[0]["estado"] == "sem_pdf"

        # 2) com aba Mcfly preenchida: ela tem prioridade (export do SAJ)
        wb = load_workbook(io.BytesIO(modelo()))
        mc = wb["Mcfly"]
        mc.append(["0000009-00.2021.4.01.0000", "Embargos à Execução Fiscal"])
        mc.append(["0000010-00.2021.4.01.0000", "Apelação"])
        buf = io.BytesIO()
        wb.save(buf)
        case2 = c.post("/api/cases", json={"nome": "B"}).json()
        r2 = c.post(f"/api/cases/{case2['id']}/template", files={"file": ("m.xlsx", buf.getvalue())}).json()
        assert r2["processos_importados"] == 2
        nums = {x["numero"]: x["mcfly"]["classe"] for x in c.get(f"/api/cases/{case2['id']}").json()["processos"]}
        assert nums == {"0000009-00.2021.4.01.0000": "Embargos à Execução Fiscal", "0000010-00.2021.4.01.0000": "Apelação"}


def test_tipos_endpoint_exposes_field_titles_and_closed_lists_for_the_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    with TestClient(create_app(Settings())) as c:
        t = c.get("/api/tipos").json()
    g = t["campos"]["execucoes_fiscais"]["garantia"]
    assert g["titulo"] == "EXIT. GARANTIA" and "IMÓVEL" in g["opcoes"]
    assert t["campos"]["execucoes_fiscais"]["observacoes"]["opcoes"] is None


def test_sheet_endpoint_columns_follow_the_template_and_rows_carry_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with TestClient(create_app(Settings())) as c:
        case = c.post("/api/cases", json={"nome": "A"}).json()
        padrao = c.get(f"/api/cases/{case['id']}/sheet").json()
        ef = next(a for a in padrao["abas"] if a["key"] == "execucoes_fiscais")
        assert [x["key"] for x in ef["colunas"]][:2] == ["numero", "polo_passivo"] and len(ef["colunas"]) == 17
        assert next(x for x in ef["colunas"] if x["key"] == "garantia")["opcoes"]  # lista fechada
        assert next(x for x in ef["colunas"] if x["key"] == "polo_passivo")["fonte"] == "mcfly"  # capa: só leitura

        c.post(f"/api/cases/{case['id']}/template", files={"file": ("m.xlsx", modelo())})
        t = c.get(f"/api/cases/{case['id']}/sheet").json()
        ef2 = next(a for a in t["abas"] if a["key"] == "execucoes_fiscais")
        titulos = [x["titulo"] for x in ef2["colunas"]]
        assert "Coluna Nova do Analista" not in titulos and len(ef2["colunas"]) == 8  # só as reconhecidas, na ordem do modelo
        assert titulos[0] == "Número do processo judicial"
        linha = t["linhas"][0]
        assert linha["numero"] == "0341609-70.2005.8.13.0261" and linha["aba"] == "execucoes_fiscais"
        assert linha["estado"] == "sem_pdf" and linha["valores"]["classe"] == "Execução Fiscal (SIDA)"
        assert ef2["n"] == 1 and t["bens"] == [] and t["documentos"] == []
