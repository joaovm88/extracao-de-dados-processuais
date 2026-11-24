from app.ingest import citation, pje

CAPA = """Número: 0012088-30.2014.4.01.3811
Documentos
Id.
Data da
Assinatura
Documento
Tipo
78623
2960
22/10/2021 12:33 Petição Inicial
Petição inicial
78793
5461
25/10/2021 07:50 0012088-30.2014.4.01.3811_V001
Volume
15363
19886
18/10/2024 19:56 MATRÍCULA Nº 1.023
Documento Comprobatório
"""
FOOT = "texto\nNum. {i} - Pág. {n}\nAssinado eletronicamente por: FULANO - 25/10/2021 07:50:18\nNúmero do documento: 1"


def _pages():
    ps = [{"page": 1, "text": CAPA, "refs": citation.page_refs(CAPA), "doc_id": "a"}]
    for n, (i, k) in enumerate([("787935461", 1), ("787935461", 2), ("1536319886", 1)], start=2):
        t = FOOT.format(i=i, n=k)
        ps.append({"page": n, "text": t, "refs": citation.page_refs(t), "doc_id": "a"})
    return ps


def test_indice_junta_id_quebrado():
    r = pje.parse_indice(CAPA)
    assert [d["id"] for d in r] == ["786232960", "787935461", "1536319886"]
    assert r[0]["data"] == "2021-10-22" and r[1]["tipo_documento"] == "Volume"


def test_docs_cruzam_indice_e_rodape():
    docs = pje.merge_docs([pje.extract_docs(_pages(), "a")])
    vol = next(d for d in docs if d["id"] == "787935461")
    assert len(vol["paginas"]) == 2 and pje.e_volume(vol) and pje.relevancia(vol) == "alta"
    assert pje.relevancia(next(d for d in docs if d["id"] == "786232960")) == "alta"


def test_validar_cronologia_pje():
    docs = pje.merge_docs([pje.extract_docs(_pages(), "a")])
    txt = ("ID 1536319886, pg. 1 - 01/01/2020 - Matrícula\nID 787935461, pg. 2 - 03/05/2012 - Penhora (dentro do volume)\n"
           "ID 999999999, pg. 1 - 01/01/2021 - Inventado")
    out, st = pje.validar_cronologia(txt, docs)
    assert st["inexistentes"] == ["999999999"] and st["datas_corrigidas"] == 1
    linhas = out.splitlines()
    assert linhas[0].startswith("ID 787935461") and "03/05/2012" in linhas[0]  # volume: mantém a data do ato
    assert "18/10/2024" in linhas[-1]


def test_nome_arquivo_com_faixa_e_label_pje_sem_vol():
    f = citation.parse_filename("0012088-30.2014.4.01.3811_1-91-180.pdf")
    assert f["volume"] == 1 and f["pagina_inicial"] == 91
    assert citation.label(1, 5, {"id": "787935461", "pg": "38", "pje": True}, "pje") == "ID 787935461, pg. 38"
