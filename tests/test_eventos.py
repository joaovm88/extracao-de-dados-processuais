import time

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app import extract as ex
from app.config import Settings
from app.ingest import eventos as ev
from app.ingest.citation import page_refs
from tests.test_cases import FakeLLM, env, wait  # noqa: F401  (fixture e utilitários)

SEP_A = ("PÁGINA DE SEPARAÇÃO\n(Gerada automaticamente pelo sistema.)\nEvento 14\nEvento: \nData: \nUsuário: \nProcesso: \nSequência Evento: \n"
         "PENHORA REALIZADA\n03/04/2024 18:44:02\nTR72 - FULANO DE TAL - SERVIDOR\n1234567-89.2023.8.26.0100/TRF6\n14\nComplemento: \nRef. ao evento 9.")
SEP_B = ("PÁGINA DE SEPARAÇÃO\n(Gerada automaticamente pelo sistema.)\nTipo documento: \nEvento: \nData: \nUsuário: \nProcesso: \nSequência Evento: \n"
         "PETIÇÃO INICIAL\nPETIÇÃO - PETIÇÃO INICIAL\n11/02/2021 07:09:18\nSECJF - SISTEMA DE PROCESSO ELETRÔNICO\n0000118-08.2010.4.01.3800\n56\nDocumento 1")


def pg(n, text, **refs):
    return {"page": n, "text": text, "refs": refs, "label": f"pg. {n}", "doc_id": "d1"}


def test_parse_separator_both_eproc_formats():
    a = ev.parse_separator(SEP_A)
    assert (a["evento"], a["data"], a["hora"], a["movimento"]) == (14, "2024-04-03", "18:44:02", "PENHORA REALIZADA")
    assert a["usuario"].startswith("TR72") and a["complemento"] == "Ref. ao evento 9."
    b = ev.parse_separator(SEP_B)  # 1ª instância: sem 'Evento N' no topo; o nº vem de 'Sequência Evento'
    assert (b["evento"], b["data"], b["tipo_documento"], b["movimento"], b["documento"]) == (56, "2021-02-11", "PETIÇÃO INICIAL", "PETIÇÃO - PETIÇÃO INICIAL", 1)
    assert ev.parse_separator("Despacho do relator. Determino a penhora do imóvel.") is None
    assert page_refs(SEP_B)["evento"] == "56" and page_refs(SEP_B)["sep"] is True  # o rótulo da página passa a ser 'Evento 56'


def test_extract_events_collects_documents_pages_and_chars():
    pages = [pg(1, "Capa do processo " * 5), pg(2, SEP_B), pg(3, "Petição inicial. " * 20, evento="56", tipo_doc="INIC1", pg="1"),
             pg(4, "Anexo estatuto social " * 10, evento="56", tipo_doc="ESTATUTO4", pg="1"), pg(5, SEP_A.replace("Evento 14", "Evento 60").replace("\n14\n", "\n60\n")),
             pg(6, "Auto de penhora do imóvel de matrícula 28.749. " * 5)]  # sem marcador próprio: herda o evento do separador
    es = ev.extract_events(pages, "d1")
    assert [e["evento"] for e in es] == [56, 60]
    assert es[0]["docs"] == ["INIC1", "ESTATUTO4"] and es[0]["paginas"] == [3, 4] and es[0]["caracteres"] > 300
    assert es[1]["movimento"] == "PENHORA REALIZADA" and es[1]["paginas"] == [6]


def test_merge_events_across_parts_keeps_one_per_number_in_date_order():
    p1 = [{"evento": 2, "data": "2024-04-02", "hora": None, "movimento": "X", "tipo_documento": "", "usuario": "", "complemento": "", "docs": ["A"], "paginas": [3], "caracteres": 10, "doc_id": "d1"}]
    p2 = [{"evento": 1, "data": "2024-03-07", "hora": None, "movimento": "Y", "tipo_documento": "", "usuario": "", "complemento": "", "docs": [], "paginas": [], "caracteres": 0, "doc_id": "d2"},
          {"evento": 2, "data": None, "hora": None, "movimento": "", "tipo_documento": "", "usuario": "", "complemento": "", "docs": ["B"], "paginas": [1], "caracteres": 5, "doc_id": "d2"}]
    m = ev.merge_events([p1, p2])
    assert [e["evento"] for e in m] == [1, 2] and m[1]["docs"] == ["A", "B"] and m[1]["paginas"] == [["d1", 3], ["d2", 1]] and m[1]["data"] == "2024-04-02"


def test_relevance_separates_acts_from_routine_expedient():
    r = lambda mov, **k: ev.relevancia({"movimento": mov, "tipo_documento": "", "complemento": k.get("c", ""), "docs": k.get("docs", [])})
    assert r("PENHORA REALIZADA") == "alta" and r("SENTENÇA") == "alta" and r("TRANSITADO EM JULGADO (Data: 06/06/2024)") == "alta"
    assert r("EXPEDIDACERTIFICADA A INTIMACAO ELETRONICA  DESPACHODECISAO") == "baixa"
    assert r("REMETIDOS OS AUTOS COM DECISAODESPACHO") == "baixa" and r("DECORRIDO PRAZO") == "baixa"
    assert r("DISTRIBUIDO POR SORTEIO", c="Ref. ao Despacho/Decisão do evento 17") == "media"  # o complemento não promove
    assert r("PETIÇÃO") == "media" and r("DECISÃO INTERLOCUTÓRIA") == "alta"


def test_validate_chronology_fixes_dates_flags_unknown_events_and_orders():
    eventos = [{"evento": 2, "data": "2024-04-02", "movimento": "x"}, {"evento": 14, "data": "2024-05-01", "movimento": "y"}]
    texto = ("Evento 14, pg. 1 - 15/01/2020 - Prejudicado o recurso.\n"
             "Evento 2, pg. 1 - 02/04/2024 - Concedida a tutela provisória.\n"
             "Evento 99, pg. 1 - 01/01/2024 - Ato que não existe.")
    novo, st = ev.validar_cronologia(texto, eventos)
    assert st == {"linhas": 3, "datas_corrigidas": 1, "inexistentes": [99], "ordenada": True}
    assert novo.splitlines()[0].startswith("Evento 2, pg. 1 - 02/04/2024") and "Evento 14, pg. 1 - 01/05/2024" in novo  # data do eproc, em ordem
    assert ev.validar_cronologia("Não informado no extrato", eventos)[0] == "Não informado no extrato"
    assert ev.validar_cronologia("Parágrafo livre sem formato de lista.", eventos)[1]["linhas"] == 0


def _eventos(n_alta=2):
    base = lambda n, mov, pgs: {"evento": n, "data": f"2024-04-{n:02d}", "hora": None, "movimento": mov, "tipo_documento": "", "usuario": "", "complemento": "",
                                "docs": [], "paginas": [["d1", p] for p in pgs], "caracteres": 0, "doc_id": "d1"}
    return [base(1, "PENHORA REALIZADA", [3]), base(2, "INTIMACAO ELETRONICA", [4]), base(3, "SENTENÇA", [5, 6]), base(4, "JUNTADA", [7])][: 3 + (n_alta - 2)]


def test_event_context_has_full_index_but_text_only_for_relevant_acts():
    pages = [pg(1, "Capa do processo. " * 5), pg(3, "Auto de penhora do imóvel. " * 10), pg(4, "Intimação expedida automaticamente. " * 10),
             pg(5, "Sentença julgando procedente. " * 10), pg(6, "Sentença, continuação. " * 10, tipo_doc="ESTATUTO3")]
    ctx, info = ex.select_context_eventos(pages, _eventos(), 100_000)
    assert ctx.count("Evento ") >= 3 and "texto integral: sim" in ctx and "texto integral: não" in ctx  # índice com todos
    assert "Auto de penhora" in ctx and "Sentença julgando" in ctx and "Intimação expedida" not in ctx  # expediente: só no índice
    assert "Sentença, continuação" not in ctx  # anexo (estatuto) fica de fora
    assert info["modo"] == "eventos" and info["eventos_total"] == 3 and info["eventos_com_texto"] == 2 and info["cortado"] is False
    _, curto = ex.select_context_eventos(pages, _eventos(), 600)  # sem espaço: atos relevantes ficam sem texto integral
    assert curto["cortado"] is True and curto["atos_relevantes_omitidos"] >= 1


def _pdf_eproc(path, proc="1234567-89.2023.8.26.0100"):
    def sep(n, mov, data):
        return ["PÁGINA DE SEPARAÇÃO", "(Gerada automaticamente pelo sistema.)", f"Evento {n}", "Evento:", "Data:", "Usuário:", "Processo:", "Sequência Evento:",
                mov, f"{data} 10:00:00", "TR72 - FULANO - SERVIDOR", f"{proc}/TRF6", str(n)]
    paginas = [["Processos relacionados:", f"Nº do processo {proc}", "Classe da ação:", " Execução Fiscal", "Tributário"],
               sep(1, "DISTRIBUIDO POR SORTEIO", "07/03/2024"), sep(2, "PENHORA REALIZADA", "02/04/2024"),
               ["Auto de penhora do imóvel de matrícula 28.749, avaliado em R$ 746.019,60, em nome da executada.", f"Processo {proc}/TRF6, Evento 2, DESPADEC1, Página 1"],
               sep(3, "EXPEDIDACERTIFICADA A INTIMACAO ELETRONICA", "03/04/2024"), sep(4, "TRANSITADO EM JULGADO", "10/06/2024")]
    cv = canvas.Canvas(str(path), pagesize=A4)
    for linhas in paginas:
        y = 800
        for ln in linhas:
            cv.drawString(50, y, ln)
            y -= 18
        cv.showPage()
    cv.save()


def test_eproc_pdf_end_to_end_events_index_chronology_check_and_endpoint(env):
    c, llm, tmp = env
    llm.payload = {**llm.payload, "observacoes": "Evento 4, pg. 1 - 01/01/2020 - Trânsito em julgado.\nEvento 2, pg. 1 - 15/03/2023 - Penhora do imóvel de matrícula 28.749, avaliado em R$ 746.019,60."}
    case = c.post("/api/cases", json={"nome": "Eventos"}).json()
    pdf = tmp / "proc_eproc.pdf"
    _pdf_eproc(pdf)
    with pdf.open("rb") as f:
        up = c.post("/api/documents", data={"case_id": case["id"], "tipo": "execucao_fiscal"}, files={"file": (pdf.name, f, "application/pdf")}).json()
    d = wait(c, up["id"])
    assert d["sistema"] == "eproc" and d["metadata"]["n_eventos"] == 4
    chamada = [x for x in llm.calls if "schema" in x][0]
    assert "ÍNDICE COMPLETO DE EVENTOS" in chamada["user"] and "Evento 2 | 02/04/2024 | PENHORA REALIZADA" in chamada["user"]
    assert "texto integral: sim" in chamada["user"] and "AUTORITATIVA" in chamada["system"]
    assert "Auto de penhora do imóvel" in chamada["user"]
    res = d["result"]
    assert res["medidas"]["contexto"]["modo"] == "eventos" and res["medidas"]["contexto"]["eventos_total"] == 4
    assert "\n\n" in res["fields"]["observacoes"]  # uma linha em branco entre os atos
    linhas = [x for x in res["fields"]["observacoes"].splitlines() if x]
    assert linhas[0].startswith("Evento 2, pg. 1 - 02/04/2024") and linhas[1].startswith("Evento 4, pg. 1 - 10/06/2024")  # datas do eproc, em ordem
    assert res["cronologia"] == {"linhas": 2, "datas_corrigidas": 2, "inexistentes": [], "ordenada": True, "eventos_base": 4}
    lista = c.get(f"/api/cases/{case['id']}/processes/{d['numero_processo']}/eventos").json()
    assert lista["total"] == 4 and [e["evento"] for e in lista["eventos"]] == [1, 2, 3, 4]
    e2 = lista["eventos"][1]
    assert e2["data_fmt"] == "02/04/2024" and e2["relevancia"] == "alta" and e2["linha"] == "Evento 2 - 02/04/2024 - PENHORA REALIZADA" and e2["docs"] == ["DESPADEC1"]
    assert lista["eventos"][2]["relevancia"] == "baixa"
    assert c.get(f"/api/cases/{case['id']}/sheet").json()["linhas"][0]["n_eventos"] == 4
    assert c.get(f"/api/cases/{case['id']}/processes/nope/eventos").status_code == 404


def test_editing_observations_drops_the_stale_chronology_check(env):
    c, llm, tmp = env
    llm.payload = {**llm.payload, "observacoes": "Evento 99, pg. 1 - 01/01/2024 - Ato que não existe."}
    case = c.post("/api/cases", json={"nome": "Edição"}).json()
    pdf = tmp / "p.pdf"
    _pdf_eproc(pdf)
    with pdf.open("rb") as f:
        up = c.post("/api/documents", data={"case_id": case["id"], "tipo": "execucao_fiscal"}, files={"file": (pdf.name, f, "application/pdf")}).json()
    d = wait(c, up["id"])
    num = d["numero_processo"]
    assert c.get(f"/api/cases/{case['id']}/sheet").json()["linhas"][0]["cronologia"]["inexistentes"] == [99]
    c.put(f"/api/cases/{case['id']}/processes/{num}/review", json={"fields": {"valor_garantia": "R$ 1,00"}})  # outro campo: mantém
    assert c.get(f"/api/cases/{case['id']}/sheet").json()["linhas"][0]["cronologia"] is not None
    c.put(f"/api/cases/{case['id']}/processes/{num}/review", json={"observacoes": "Evento 2 - 02/04/2024 - PENHORA REALIZADA"})
    assert c.get(f"/api/cases/{case['id']}/sheet").json()["linhas"][0]["cronologia"] is None
