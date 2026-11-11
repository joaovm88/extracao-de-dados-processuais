"""Relatório de Recuperação Judicial: estrutura, cálculos feitos pelo código (deságio ponderado, somas) e geração em Word e PDF.

A IA preenche os campos (cada um com a sua origem Evento/ID e página); aqui ficam o esquema, a padronização, os cálculos e a
montagem do documento, sem listas nem marcadores (o analista prefere texto corrido)."""
import io
import re
from datetime import date
from typing import Optional

from .schemas import fmt_data_br, fmt_moeda_br

NC = "Não consta nos documentos analisados."
SECOES = [("identificacao", "Identificação do processo"), ("plano", "Plano de Recuperação Judicial"),
          ("bens", "Bens e endividamento"), ("andamento", "Andamento do processo"), ("fazenda", "Interesse da Fazenda Nacional"),
          ("analise", "Análise adicional"), ("observacoes", "Observações")]
SIMNAO = ["SIM", "NÃO"]
# (chave, rótulo, tipo, seção, orientação para a IA); tipo: texto | data | moeda | simnao | paragrafo
CAMPOS = [
    ("numero_rj", "RECUPERAÇÃO JUDICIAL Nº", "texto", "identificacao", "Número CNJ do processo de recuperação judicial."),
    ("vara", "VARA", "texto", "identificacao", "Vara, comarca e tribunal onde tramita a RJ."),
    ("administrador_judicial", "ADMINISTRADOR JUDICIAL", "texto", "identificacao", "Nome e OAB do(a) administrador(a) judicial (e do responsável pelo processo, se outro)."),
    ("data_ajuizamento", "DATA DO AJUIZAMENTO", "data", "identificacao", "Data de ajuizamento do pedido de RJ (dd/mm/aaaa)."),
    ("data_deferimento", "DATA DO DEFERIMENTO DO PROCESSAMENTO DA RJ", "data", "identificacao", "Data da decisão que deferiu o processamento (dd/mm/aaaa)."),
    ("data_concessao", "DATA DA CONCESSÃO DA RJ", "data", "identificacao", "Data da decisão que homologou o plano e concedeu a RJ (dd/mm/aaaa)."),
    ("data_plano", "APRESENTAÇÃO DO PLANO DE RECUPERAÇÃO JUDICIAL", "data", "plano", "Data em que o PRJ foi apresentado (dd/mm/aaaa)."),
    ("aditivos", "ADITIVOS AO PLANO", "texto", "plano", "Datas de apresentação de aditivos ou modificativos ao PRJ, em frase corrida."),
    ("documentos_plano", "DOCUMENTOS QUE ACOMPANHARAM O PLANO", "paragrafo", "plano", "Laudos e demais documentos apresentados com o PRJ."),
    ("pagamento_trabalhista", "PAGAMENTO DOS CRÉDITOS TRABALHISTAS (CLASSE I)", "paragrafo", "plano",
     "Proposta para a Classe I: limite de salários-mínimos, parcela estritamente salarial, prazos, atualização. Em prosa."),
    ("pagamento_demais", "PAGAMENTO DAS DEMAIS CLASSES", "paragrafo", "plano",
     "Proposta para garantia real, quirografários e ME/EPP: deságio, parcelas, carência, indexador e juros. Em prosa."),
    ("propostas_complementares", "PROPOSTAS COMPLEMENTARES E CREDORES APOIADORES", "paragrafo", "plano",
     "Condições para credores apoiadores, colaborativos, parceiros e extraconcursais aderentes. Em prosa."),
    ("fgts", "DÍVIDAS DE FGTS NO PLANO", "paragrafo", "plano", "Se a dívida de FGTS está incluída no plano e em que condições."),
    ("laudo_bens", "LAUDO DE BENS", "paragrafo", "bens", "Tipos de bens avaliados, valores de mercado e de liquidação forçada, imóveis e localidades vistoriadas. Em prosa."),
    ("divida_total", "VALOR TOTAL DA DÍVIDA SUJEITA À RECUPERAÇÃO JUDICIAL", "moeda", "bens", "Valor total do passivo sujeito à RJ (R$)."),
    ("edital", "PUBLICAÇÃO DO EDITAL", "texto", "bens", "Data e ID da publicação do edital do art. 7º, § 2º, da Lei 11.101/2005."),
    ("aprovado_agc", "O PLANO FOI APROVADO EM ASSEMBLEIA?", "simnao", "andamento", "SIM ou NÃO."),
    ("data_agc", "ASSEMBLEIA GERAL DE CREDORES", "texto", "andamento", "Data, convocação e cenários de aprovação, em frase corrida."),
    ("homologado", "O PLANO FOI HOMOLOGADO PELO JUIZ?", "simnao", "andamento", "SIM ou NÃO."),
    ("data_homologacao", "DATA DA HOMOLOGAÇÃO", "data", "andamento", "dd/mm/aaaa."),
    ("cumprimento", "O PLANO ESTÁ SENDO CUMPRIDO?", "paragrafo", "andamento", "Informação mais recente sobre o cumprimento, com data."),
    ("situacao_atual", "SITUAÇÃO ATUAL DO PROCESSO", "paragrafo", "andamento", "Situação mais recente dos autos, com data."),
    ("pgto_trabalhistas", "OS CREDORES TRABALHISTAS ESTÃO SENDO PAGOS?", "paragrafo", "andamento", "Com base nos autos; sem prova de pagamento, diga que não consta."),
    ("pgto_quirografarios", "OS CREDORES QUIROGRAFÁRIOS ESTÃO SENDO PAGOS?", "paragrafo", "andamento", "Com base nos autos; sem prova de pagamento, diga que não consta."),
    ("encerrada", "A RJ FOI ENCERRADA?", "simnao", "andamento", "SIM ou NÃO."),
    ("data_encerramento", "DATA DO ENCERRAMENTO", "data", "andamento", "dd/mm/aaaa, se encerrada."),
    ("transito", "A DECISÃO TRANSITOU EM JULGADO?", "paragrafo", "andamento", "Qual decisão, e quando transitou."),
    ("valores_uniao", "CONSTAM VALORES DEVIDOS À UNIÃO/INSS?", "paragrafo", "fazenda", "Créditos tributários, previdenciários ou de FGTS devidos à União e valores."),
    ("manifestacoes_uniao", "MANIFESTAÇÕES DA UNIÃO NO PROCESSO", "paragrafo", "fazenda", "Datas, pedidos e débitos apresentados pela União."),
    ("certidao_art57", "CERTIDÃO DE REGULARIDADE FISCAL (ART. 57 DA LEI 11.101/2005)", "paragrafo", "fazenda", "Exigência, apresentação (CND/CPEND) e posição das recuperandas."),
    ("transacao", "TRANSAÇÃO OU PARCELAMENTO DO PASSIVO TRIBUTÁRIO", "paragrafo", "fazenda", "Propostas, adesões ou pedidos de transação ou parcelamento."),
    ("alienacoes", "ALIENAÇÃO DE BENS OU DE UNIDADES PRODUTIVAS", "paragrafo", "fazenda", "Alienações previstas ou realizadas e tratamento da sucessão tributária."),
    ("situacao_financeira", "SITUAÇÃO FINANCEIRA DAS EMPRESAS", "paragrafo", "analise", "Lucro ou prejuízo, ativo x passivo, empréstimos a sócios e outras informações relevantes."),
    ("fraude", "HÁ INDÍCIO DE FRAUDE?", "paragrafo", "analise", "Indícios de fraude, confusão patrimonial ou esvaziamento; se não há, diga que até o momento não consta."),
    ("grupo_economico", "HÁ GRUPO ECONÔMICO?", "paragrafo", "analise", "Existência de grupo econômico e as empresas que o compõem."),
    ("convolacao", "HÁ PEDIDO DE CONVOLAÇÃO EM FALÊNCIA?", "paragrafo", "analise", "Quem pediu, quando e o motivo; se não há, diga que até o momento não consta."),
    ("reestruturacoes", "PRINCIPAIS REESTRUTURAÇÕES", "paragrafo", "analise", "Reestruturações societárias, financeiras e operacionais realizadas."),
]
POR_CHAVE = {c[0]: c for c in CAMPOS}


def schema() -> dict:
    """Esquema JSON da resposta da IA."""
    str_n = {"type": ["string", "null"]}
    campo = lambda desc, enum=None: {"type": "object", "description": desc, "properties": {
        "valor": ({"type": ["string", "null"], "enum": [*enum, None]} if enum else str_n), "origem": {"type": ["string", "null"], "description": "Rótulo do trecho de onde saiu (ex.: 'ID 123, pg. 4')"}}}
    props = {k: campo(d, SIMNAO if t == "simnao" else None) for k, _, t, _, d in CAMPOS}
    props["empresas"] = {"type": "array", "description": "Todas as empresas do grupo recuperando, com CNPJ.", "items": {"type": "object", "properties": {
        "nome": str_n, "cnpj": str_n, "em_recuperacao_judicial": {"type": ["string", "null"], "enum": [*SIMNAO, None], "description": "Consta 'em recuperação judicial' no nome empresarial?"}, "origem": str_n}}}
    props["classes"] = {"type": "array", "description": "Uma linha por classe de credores prevista no plano (I trabalhista, II garantia real, III quirografários, IV ME/EPP).", "items": {"type": "object", "properties": {
        "classe": {"type": ["string", "null"], "description": "Ex.: 'Classe III – Credores Quirografários'"},
        "proposta": {"type": ["string", "null"], "description": "Resumo da proposta de pagamento em uma frase"},
        "valor_sujeito": {"type": ["string", "null"], "description": "Valor da classe no QGC mais recente (R$)"},
        "desagio_percentual": {"type": ["number", "null"], "description": "Deságio proposto, em % (0 quando o pagamento é integral); null se não constar"},
        "origem": str_n}}}
    props["bens"] = {"type": "array", "description": "Bens do laudo de avaliação, um por tipo (instalações, máquinas, veículos, imóveis…).", "items": {"type": "object", "properties": {
        "tipo": str_n, "valor_mercado": {"type": ["string", "null"], "description": "R$"}, "valor_liquidacao": {"type": ["string", "null"], "description": "Valor de liquidação forçada (R$), se constar"},
        "localidade": str_n, "origem": str_n}}}
    props["qgc"] = {"type": "array", "description": "Quadros gerais de credores: um item para o apresentado pelas recuperandas e outro para o da administração judicial.", "items": {"type": "object", "properties": {
        "fonte": {"type": ["string", "null"], "description": "'Recuperandas' ou 'Administração Judicial'"}, "data": str_n, "origem": str_n, "total": {"type": ["string", "null"], "description": "R$"},
        "classes": {"type": "array", "items": {"type": "object", "properties": {"classe": str_n, "valor": {"type": ["string", "null"], "description": "R$"}}}}}}}
    props["observacoes"] = {"type": "array", "description": "Cronologia dos atos relevantes, do mais antigo ao mais recente.", "items": {"type": "object", "properties": {
        "data": str_n, "texto": {"type": ["string", "null"], "description": "Uma frase: quem fez o quê"}, "origem": str_n}}}
    return {"type": "object", "properties": props}


# ---------------- padronização e cálculos ----------------
def moeda_float(v) -> Optional[float]:
    """'R$ 1.234,56' -> 1234.56 (None se não for um valor só com número)."""
    t = re.sub(r"(?i)^r\$\s*", "", str(v or "").strip())
    if not re.fullmatch(r"\d[\d.]*(,\d{1,2})?", t):
        return None
    return float(t.replace(".", "").replace(",", "."))


def _pct(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace("%", "").replace(",", ".").strip())
    except ValueError:
        return None


def _s(v) -> Optional[str]:
    v = str(v).strip() if v is not None else ""
    return v or None


def normalizar(raw: dict) -> dict:
    """Resposta da IA -> relatório padronizado (datas dd/mm/aaaa, valores em R$, SIM/NÃO)."""
    rel: dict = {"campos": {}}
    for k, _, tipo, _, _ in CAMPOS:
        c = (raw or {}).get(k) or {}
        v = _s(c.get("valor")) if isinstance(c, dict) else _s(c)
        if v and tipo == "data":
            v = fmt_data_br(v)
        elif v and tipo == "moeda":
            v = fmt_moeda_br(v)
        elif v and tipo == "simnao":
            u = v.upper().replace("Ã", "A")
            v = "SIM" if u in ("SIM", "S") else "NÃO" if u in ("NAO", "N") else None
        rel["campos"][k] = {"valor": v, "origem": _s(c.get("origem")) if isinstance(c, dict) else None}
    rel["empresas"] = [{"nome": _s(e.get("nome")), "cnpj": _s(e.get("cnpj")), "em_recuperacao_judicial": (_s(e.get("em_recuperacao_judicial")) or "").upper() or None,
                        "origem": _s(e.get("origem"))} for e in (raw or {}).get("empresas") or [] if isinstance(e, dict) and (e.get("nome") or e.get("cnpj"))]
    rel["classes"] = [{"classe": _s(c.get("classe")), "proposta": _s(c.get("proposta")), "valor_sujeito": fmt_moeda_br(_s(c.get("valor_sujeito"))),
                       "desagio_percentual": _pct(c.get("desagio_percentual")), "origem": _s(c.get("origem"))}
                      for c in (raw or {}).get("classes") or [] if isinstance(c, dict) and c.get("classe")]
    rel["bens"] = [{"tipo": _s(b.get("tipo")), "valor_mercado": fmt_moeda_br(_s(b.get("valor_mercado"))), "valor_liquidacao": fmt_moeda_br(_s(b.get("valor_liquidacao"))),
                    "localidade": _s(b.get("localidade")), "origem": _s(b.get("origem"))} for b in (raw or {}).get("bens") or [] if isinstance(b, dict) and b.get("tipo")]
    rel["qgc"] = [{"fonte": _s(q.get("fonte")), "data": fmt_data_br(_s(q.get("data"))), "origem": _s(q.get("origem")), "total": fmt_moeda_br(_s(q.get("total"))),
                   "classes": [{"classe": _s(c.get("classe")), "valor": fmt_moeda_br(_s(c.get("valor")))} for c in q.get("classes") or [] if isinstance(c, dict) and c.get("classe")]}
                  for q in (raw or {}).get("qgc") or [] if isinstance(q, dict)]
    rel["observacoes"] = [{"data": fmt_data_br(_s(o.get("data"))), "texto": _s(o.get("texto")), "origem": _s(o.get("origem"))}
                          for o in (raw or {}).get("observacoes") or [] if isinstance(o, dict) and o.get("texto")]
    return rel


def normalizar_salvo(rel: dict) -> dict:
    """Relatório já no formato guardado (editado na tela) -> padronizado de novo."""
    return normalizar({**((rel or {}).get("campos") or {}), **{k: (rel or {}).get(k) for k in ("empresas", "classes", "bens", "qgc", "observacoes")}})


def _chave_data(d: Optional[str]) -> str:
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else "9999"


def brl(x: float) -> str:
    return "R$ " + f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def calcular(rel: dict) -> dict:
    """Cálculos que não ficam com a IA: deságio ponderado, somas e conferências."""
    out: dict = {"desagio": None, "memoria": [], "avisos": []}
    pesos = [(c, moeda_float(c.get("valor_sujeito")), c.get("desagio_percentual")) for c in rel.get("classes") or []]
    validas = [(c, v, d) for c, v, d in pesos if v is not None and d is not None]
    for c, v, d in pesos:
        if (v is None or d is None) and c.get("classe"):
            faltam = [n for n, x in (("valor", v), ("deságio", d)) if x is None]
            out["avisos"].append(f"{c['classe']}: sem {' e '.join(faltam)} informado; ficou fora do deságio ponderado.")
    total = sum(v for _, v, _ in validas)
    if validas and total > 0:
        out["desagio"] = round(sum(v * d for _, v, d in validas) / total, 2)
        out["memoria"] = [f"{c['classe']}: {brl(v)} com deságio de {d:g}%, equivalente a {brl(v * d / 100)} de desconto" for c, v, d in validas]
        out["base"] = brl(total)
        out["desconto_total"] = brl(sum(v * d / 100 for _, v, d in validas))
    bens = rel.get("bens") or []
    if bens:
        mer = [moeda_float(b.get("valor_mercado")) for b in bens]
        liq = [moeda_float(b.get("valor_liquidacao")) for b in bens]
        out["total_mercado"] = brl(sum(x for x in mer if x is not None)) if any(x is not None for x in mer) else None
        out["total_liquidacao"] = brl(sum(x for x in liq if x is not None)) if any(x is not None for x in liq) else None
    soma_classes = sum(v for _, v, _ in pesos if v is not None)
    dt = moeda_float(((rel.get("campos") or {}).get("divida_total") or {}).get("valor"))
    if dt is not None and soma_classes and abs(dt - soma_classes) > 1:
        out["avisos"].append(f"O valor total da dívida ({brl(dt)}) difere da soma das classes ({brl(soma_classes)}). Confira.")
    for q in rel.get("qgc") or []:
        vs = [moeda_float(c.get("valor")) for c in q.get("classes") or []]
        tot = moeda_float(q.get("total"))
        if vs and all(v is not None for v in vs) and tot is not None and abs(sum(vs) - tot) > 1:
            out["avisos"].append(f"QGC ({q.get('fonte') or 'sem fonte'}): as classes somam {brl(sum(vs))}, mas o total informado é {brl(tot)}. Confira.")
    out["nao_consta"] = [POR_CHAVE[k][1] for k, c in (rel.get("campos") or {}).items() if k in POR_CHAVE and not c.get("valor")]
    return out


def textos(rel: dict) -> list[str]:
    """Todos os textos do relatório (para conferir as citações com o PDF)."""
    t = [v for c in (rel.get("campos") or {}).values() for v in (c.get("valor"), c.get("origem")) if v]
    for lista in ("empresas", "classes", "bens", "qgc", "observacoes"):
        for it in rel.get(lista) or []:
            t += [str(v) for k, v in it.items() if isinstance(v, str) and v]
    return t


# ---------------- montagem do documento (blocos) ----------------
def _com_origem(valor: Optional[str], origem: Optional[str]) -> str:
    if not valor:
        return NC
    if origem and origem not in valor:
        valor = valor.rstrip(" .") + f" ({origem})."
    return valor if valor.endswith((".", "?", "!", ")")) or re.search(r"\)\.?$", valor) else valor + "."


def _lista_em_prosa(itens: list[str]) -> str:
    itens = [i.rstrip(".") if i.endswith("..") else i for i in itens]
    return itens[0] if len(itens) == 1 else (", ".join(itens[:-1]) + " e " + itens[-1]) if itens else ""


def blocos(rel: dict, numero: str, hoje: Optional[date] = None) -> list[tuple]:
    """('titulo'|'sub'|'secao'|'rot'|'par'|'tabela'|'nota', ...). Nada de marcadores: rótulo em negrito + texto, parágrafos e tabelas."""
    hoje = hoje or date.today()
    c = rel.get("campos") or {}
    calc = calcular(rel)
    val = lambda k: _com_origem((c.get(k) or {}).get("valor"), (c.get(k) or {}).get("origem"))
    B: list[tuple] = [("titulo", "RELATÓRIO – RECUPERAÇÃO JUDICIAL"),
                      ("sub", f"Processo nº {numero} · elaborado em {hoje:%d/%m/%Y}")]
    for chave, titulo in SECOES:
        B.append(("secao", titulo))
        if chave == "identificacao":
            emp = rel.get("empresas") or []
            if emp:
                nomes = [f"{e['nome'] or 'empresa sem nome'}" + (f" (CNPJ {e['cnpj']})" if e.get("cnpj") else "") for e in emp]
                B.append(("rot", "EMPRESAS RECUPERANDAS", _lista_em_prosa(nomes).rstrip(".") + "."))
                sim = [e["nome"] for e in emp if e.get("em_recuperacao_judicial") == "SIM" and e.get("nome")]
                nao = [e["nome"] for e in emp if e.get("em_recuperacao_judicial") == "NÃO" and e.get("nome")]
                if sim or nao:
                    partes = ([f"consta a expressão “em recuperação judicial” no nome de {_lista_em_prosa(sim)}"] if sim else []) + \
                             ([f"não consta no nome de {_lista_em_prosa(nao)}"] if nao else [])
                    frase = "; ".join(partes)
                    B.append(("rot", "EXPRESSÃO “EM RECUPERAÇÃO JUDICIAL”", f"{frase[0].upper()}{frase[1:]}".rstrip(".") + "."))
            else:
                B.append(("rot", "EMPRESAS RECUPERANDAS", NC))
        if chave == "bens":
            for k in [x[0] for x in CAMPOS if x[3] == "bens" and x[0] == "laudo_bens"]:
                B.append(("rot", POR_CHAVE[k][1], val(k)))
            if rel.get("bens"):
                rows = [[b.get("tipo") or "", b.get("valor_mercado") or "–", b.get("valor_liquidacao") or "–", b.get("localidade") or "–"] for b in rel["bens"]]
                if calc.get("total_mercado") or calc.get("total_liquidacao"):
                    rows.append(["Total", calc.get("total_mercado") or "–", calc.get("total_liquidacao") or "–", ""])
                B.append(("tabela", ["Bem avaliado", "Valor de mercado", "Liquidação forçada", "Localidade"], rows, [34, 22, 22, 22]))
            qgcs = rel.get("qgc") or []
            if qgcs:
                classes = list(dict.fromkeys(cl["classe"] for q in qgcs for cl in q.get("classes") or []))
                cab = ["Classe"] + [f"{q.get('fonte') or 'QGC'}" + (f" ({q['data']})" if q.get("data") else "") for q in qgcs]
                rows = [[cl] + [next((x.get("valor") or "–" for x in q.get("classes") or [] if x.get("classe") == cl), "–") for q in qgcs] for cl in classes]
                rows.append(["Total"] + [q.get("total") or "–" for q in qgcs])
                B.append(("par", "Quadro geral de credores (QGC) apresentado pelas recuperandas e pela administração judicial, por classe de credores:"))
                B.append(("tabela", cab, rows, [34] + [66 // len(qgcs)] * len(qgcs)))
                orig = [f"{q.get('fonte') or 'QGC'}: {q['origem']}" for q in qgcs if q.get("origem")]
                if orig:
                    B.append(("nota", "Origem dos quadros: " + "; ".join(orig) + "."))
            for k in ("divida_total", "edital"):
                B.append(("rot", POR_CHAVE[k][1], val(k)))
            if calc.get("desagio") is not None:
                B.append(("rot", "DESÁGIO PONDERADO GERAL", f"{str(calc['desagio']).replace('.', ',')}% sobre uma base de {calc['base']}, o que corresponde a um desconto total de "
                                                             f"{calc['desconto_total']}. Cálculo: " + "; ".join(calc["memoria"]) + ". O deságio de cada classe é ponderado pelo respectivo valor no quadro de credores."))
            else:
                B.append(("rot", "DESÁGIO PONDERADO GERAL", "Não foi possível calcular: faltam o valor por classe ou o deságio de alguma classe."))
            continue
        if chave == "plano":
            for k in ("data_plano", "aditivos", "documentos_plano"):
                B.append(("rot", POR_CHAVE[k][1], val(k)))
            if rel.get("classes"):
                nomes = [cl["classe"] for cl in rel["classes"]]
                B.append(("rot", "CLASSES DE CREDORES", f"O plano prevê as seguintes classes: {_lista_em_prosa(nomes)}."))
        if chave == "observacoes":
            obs = sorted(rel.get("observacoes") or [], key=lambda o: _chave_data(o.get("data")))
            if not obs:
                B.append(("par", NC))
            for o in obs:
                txt = (o["texto"] or "").rstrip(" .")
                B.append(("par", (f"Em {o['data']}, " if o.get("data") else "") + (txt[0].lower() + txt[1:] if o.get("data") and txt[:1].isupper() else txt)
                          + (f" ({o['origem']})" if o.get("origem") and o["origem"] not in txt else "") + "."))
            continue
        for k, rot, _, sec, _ in CAMPOS:
            if sec == chave and not (chave in ("bens", "plano") and k in ("laudo_bens", "divida_total", "edital", "data_plano", "aditivos", "documentos_plano")):
                B.append(("rot", rot, val(k)))
    if calc["avisos"]:
        B.append(("nota", "Pontos a conferir: " + " ".join(calc["avisos"])))
    return B


def _rot(r: str) -> str:
    """Rótulo + dois-pontos (pergunta já termina em '?')."""
    return r if r.endswith("?") else r + ":"


def gerar_docx(rel: dict, numero: str) -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(11)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    sec = d.sections[0]
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Pt(62)
    p = sec.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f"Recuperação Judicial nº {numero} · página ")
    r.font.size = Pt(8)
    for tag, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        run = p.add_run()
        run.font.size = Pt(8)
        if tag:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), tag)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = txt
        run._r.append(el)
    for b in blocos(rel, numero):
        k = b[0]
        if k == "titulo":
            h = d.add_paragraph()
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
            x = h.add_run(b[1])
            x.bold, x.font.size = True, Pt(16)
        elif k == "sub":
            h = d.add_paragraph()
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
            x = h.add_run(b[1])
            x.font.size, x.font.color.rgb = Pt(10), RGBColor(0x55, 0x65, 0x77)
        elif k == "secao":
            h = d.add_heading(b[1].upper(), level=2)
            h.paragraph_format.space_before = Pt(14)
            for x in h.runs:
                x.font.name, x.font.size, x.font.color.rgb = "Calibri", Pt(12), RGBColor(0x0F, 0x1B, 0x2D)
        elif k == "rot":
            q = d.add_paragraph()
            q.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            q.paragraph_format.space_after = Pt(6)
            q.add_run(_rot(b[1]) + " ").bold = True
            x = q.add_run(b[2])
            if b[2] == NC:
                x.italic = True
        elif k == "par":
            q = d.add_paragraph(b[1])
            q.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            q.paragraph_format.space_after = Pt(6)
        elif k == "nota":
            q = d.add_paragraph()
            x = q.add_run(b[1])
            x.italic, x.font.size = True, Pt(9)
        elif k == "tabela":
            t = d.add_table(rows=1, cols=len(b[1]))
            t.style = "Table Grid"
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            for i, h in enumerate(b[1]):
                t.rows[0].cells[i].text = ""
                run = t.rows[0].cells[i].paragraphs[0].add_run(h)
                run.bold, run.font.size = True, Pt(9.5)
            for row in b[2]:
                cells = t.add_row().cells
                for i, v in enumerate(row):
                    run = cells[i].paragraphs[0].add_run(str(v))
                    run.font.size = Pt(9.5)
                    run.bold = row[0] == "Total"
            d.add_paragraph()
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def gerar_pdf(rel: dict, numero: str) -> bytes:
    import html

    import pymupdf

    e = lambda x: html.escape(str(x))
    h = []
    for b in blocos(rel, numero):
        k = b[0]
        if k == "titulo":
            h.append(f'<h1 style="font-size:16pt;text-align:center;color:#0f1b2d">{e(b[1])}</h1>')
        elif k == "sub":
            h.append(f'<p style="text-align:center;color:#556577;font-size:9pt">{e(b[1])}</p>')
        elif k == "secao":
            h.append(f'<h2 style="font-size:11.5pt;color:#0f1b2d;margin-top:12pt;border-bottom:1px solid #1351b4">{e(b[1].upper())}</h2>')
        elif k == "rot":
            txt = f"<i>{e(b[2])}</i>" if b[2] == NC else e(b[2])
            h.append(f'<p style="text-align:justify;font-size:10.5pt"><b>{e(_rot(b[1]))}</b> {txt}</p>')
        elif k == "par":
            h.append(f'<p style="text-align:justify;font-size:10.5pt">{e(b[1])}</p>')
        elif k == "nota":
            h.append(f'<p style="font-size:8.5pt;color:#556577"><i>{e(b[1])}</i></p>')
        elif k == "tabela":
            cab = "".join(f'<td style="width:{w}%;background-color:#eef2f7"><b style="font-size:8.5pt">{e(c)}</b></td>' for c, w in zip(b[1], b[3]))
            rows = "".join("<tr>" + "".join(f'<td style="font-size:8.5pt">{"<b>" + e(v) + "</b>" if r[0] == "Total" else e(v)}</td>' for v in r) + "</tr>" for r in b[2])
            h.append(f'<table style="width:100%" cellspacing="0" cellpadding="3"><tr>{cab}</tr>{rows}</table><p></p>')
    story = pymupdf.Story(html="".join(h), em=10)
    buf = io.BytesIO()
    w = pymupdf.DocumentWriter(buf)
    folha = pymupdf.paper_rect("a4")
    mais, n = 1, 0
    while mais:
        dev = w.begin_page(folha)
        mais, _ = story.place(folha + (56, 50, -56, -56))
        story.draw(dev)
        w.end_page()
        n += 1
    w.close()
    doc = pymupdf.open(stream=buf.getvalue())
    for i, pg in enumerate(doc):  # rodapé com o número e a página
        pg.insert_text((56, 820), f"Recuperação Judicial nº {numero}", fontsize=7.5, color=(0.35, 0.4, 0.47))
        pg.insert_text((500, 820), f"página {i + 1} de {len(doc)}", fontsize=7.5, color=(0.35, 0.4, 0.47))
    return doc.tobytes()
