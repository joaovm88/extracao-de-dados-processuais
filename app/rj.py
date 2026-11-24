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
        rel["campos"][k] = {"valor": v, "origem": _s(c.get("origem")) if isinstance(c, dict) else None, "verificado": c.get("verificado") if isinstance(c, dict) else None}
    rel["empresas"] = [{"nome": _s(e.get("nome")), "cnpj": _s(e.get("cnpj")), "em_recuperacao_judicial": (_s(e.get("em_recuperacao_judicial")) or "").upper() or None,
                        "origem": _s(e.get("origem")), "verificado": e.get("verificado")} for e in (raw or {}).get("empresas") or [] if isinstance(e, dict) and (e.get("nome") or e.get("cnpj"))]
    rel["classes"] = [{"classe": _s(c.get("classe")), "proposta": _s(c.get("proposta")), "valor_sujeito": fmt_moeda_br(_s(c.get("valor_sujeito"))),
                       "desagio_percentual": _pct(c.get("desagio_percentual")), "origem": _s(c.get("origem")), "verificado": c.get("verificado")}
                      for c in (raw or {}).get("classes") or [] if isinstance(c, dict) and c.get("classe")]
    rel["bens"] = [{"tipo": _s(b.get("tipo")), "valor_mercado": fmt_moeda_br(_s(b.get("valor_mercado"))), "valor_liquidacao": fmt_moeda_br(_s(b.get("valor_liquidacao"))),
                    "localidade": _s(b.get("localidade")), "origem": _s(b.get("origem")), "verificado": b.get("verificado")} for b in (raw or {}).get("bens") or [] if isinstance(b, dict) and b.get("tipo")]
    rel["qgc"] = [{"fonte": _s(q.get("fonte")), "data": fmt_data_br(_s(q.get("data"))), "origem": _s(q.get("origem")), "verificado": q.get("verificado"), "total": fmt_moeda_br(_s(q.get("total"))),
                   "classes": [{"classe": _s(c.get("classe")), "valor": fmt_moeda_br(_s(c.get("valor")))} for c in q.get("classes") or [] if isinstance(c, dict) and c.get("classe")]}
                  for q in (raw or {}).get("qgc") or [] if isinstance(q, dict)]
    rel["observacoes"] = [{"data": fmt_data_br(_s(o.get("data"))), "texto": _s(o.get("texto")), "origem": _s(o.get("origem")), "verificado": o.get("verificado")}
                          for o in (raw or {}).get("observacoes") or [] if isinstance(o, dict) and o.get("texto")]
    return rel


def normalizar_salvo(rel: dict) -> dict:
    """Relatório já no formato guardado (editado na tela) -> padronizado de novo."""
    return normalizar({**((rel or {}).get("campos") or {}), **{k: (rel or {}).get(k) for k in ("empresas", "classes", "bens", "qgc", "observacoes")}})


# ---------------- complementar o relatório com PDFs novos (sem apagar o que já consta) ----------------
def _nk(s) -> str:
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


_CHAVES = {
    "empresas": lambda e: re.sub(r"\D", "", e.get("cnpj") or "") or _nk(e.get("nome")),
    "classes": lambda c: _nk(c.get("classe")),
    "bens": lambda b: (_nk(b.get("tipo")), _nk(b.get("localidade"))),
    "qgc": lambda q: (_nk(q.get("fonte")), q.get("data") or ""),
    "observacoes": lambda o: (o.get("data") or "", _nk(o.get("origem")) or _nk(o.get("texto"))[:40]),
}


def _mescla_lista(nome: str, antiga: list, nova: list, protegida: bool, info: dict, chave=None) -> list:
    """Linhas antigas ficam (nunca se apaga); linhas novas entram; em linhas que casam, o antigo vazio é preenchido e, se a
    tabela não foi editada pelo analista, o valor novo atualiza o antigo."""
    chave = chave or _CHAVES[nome]
    out = [dict(x) for x in antiga]
    idx = {chave(x): i for i, x in enumerate(out) if chave(x)}
    for r in nova:
        k = chave(r)
        if not k or k not in idx:
            out.append(dict(r))
            info["linhas_novas"] += 1
            continue
        row = out[idx[k]]
        for f, v in r.items():
            if v in (None, "", []):
                continue
            if f == "classes":  # QGC: classes dentro do quadro
                row[f] = _mescla_lista("qgc_classes", row.get(f) or [], v, protegida, info, lambda c: _nk(c.get("classe")))
            elif row.get(f) in (None, ""):
                row[f] = v
            elif not protegida and row[f] != v:
                row[f] = v
                info["atualizados"] += 1
    return out


def mesclar(antigo: dict, novo: dict) -> tuple[dict, dict]:
    """Relatório já existente + extração com PDFs novos -> relatório complementado. Garantias: nada que já constava some;
    o que o analista editou não é sobrescrito; o que estava em branco é preenchido."""
    ed = set(antigo.get("_editados") or [])
    info = {"preenchidos": 0, "atualizados": 0, "linhas_novas": 0, "mantidos_por_edicao": 0}
    out: dict = {"campos": {}, "_editados": sorted(ed)}
    for k in POR_CHAVE:
        o = (antigo.get("campos") or {}).get(k) or {"valor": None, "origem": None}
        n = (novo.get("campos") or {}).get(k) or {"valor": None, "origem": None}
        ov, nv = o.get("valor"), n.get("valor")
        if ov and f"campo:{k}" in ed:
            out["campos"][k] = dict(o)
            info["mantidos_por_edicao"] += bool(nv and nv != ov)
        elif not nv:
            out["campos"][k] = dict(o)
        elif not ov:
            out["campos"][k] = dict(n)
            info["preenchidos"] += 1
        elif nv != ov:
            if n.get("verificado") is False and o.get("verificado") is True:
                out["campos"][k] = dict(o)  # o novo não foi localizado nos documentos; o antigo foi: fica o confirmado
            else:
                out["campos"][k] = dict(n)
                info["atualizados"] += 1
        else:
            out["campos"][k] = {**o, "origem": o.get("origem") or n.get("origem"), "verificado": o.get("verificado") if o.get("verificado") is not None else n.get("verificado")}
    for nome in ("empresas", "classes", "bens", "qgc", "observacoes"):
        out[nome] = _mescla_lista(nome, antigo.get(nome) or [], novo.get(nome) or [], f"tabela:{nome}" in ed, info)
    return out, info


def contexto_atual(rel: dict) -> str:
    """Relatório atual em texto compacto, para a IA complementar (em vez de começar do zero)."""
    import json as _json

    campos = {k: {"valor": c.get("valor"), "origem": c.get("origem")} for k, c in (rel.get("campos") or {}).items() if c.get("valor")}
    editados = [POR_CHAVE[e.split(":", 1)[1]][1] for e in rel.get("_editados") or [] if e.startswith("campo:") and e.split(":", 1)[1] in POR_CHAVE]
    tabelas = [t for t in rel.get("_editados") or [] if t.startswith("tabela:")]
    corpo = {**campos, **{k: rel.get(k) for k in ("empresas", "classes", "bens", "qgc", "observacoes") if rel.get(k)}}
    return ("RELATÓRIO ATUAL (já preenchido a partir de PDFs anteriores e/ou revisado pelo analista). Complemente-o com os documentos abaixo: "
            "preserve tudo o que já consta; preencha o que estava em branco; só altere um dado existente quando os documentos abaixo trouxerem "
            "informação MAIS RECENTE ou que o contradiga, e nesse caso mencione a data da atualização no próprio texto. NUNCA apague informação. "
            "Devolva o relatório COMPLETO (todos os campos e tabelas, inclusive o que já existia), no mesmo formato de resposta.\n"
            + (f"Campos revisados pelo analista (NÃO altere): {'; '.join(editados)}.\n" if editados else "")
            + (f"Tabelas revisadas pelo analista (só acrescente linhas): {', '.join(t.split(':')[1] for t in tabelas)}.\n" if tabelas else "")
            + "```json\n" + _json.dumps(corpo, ensure_ascii=False) + "\n```\n\n")


def marcar_edicoes(antigo: dict, novo: dict) -> dict:
    """Quais campos e tabelas o analista mudou na tela (ficam protegidos quando novos PDFs forem enviados)."""
    ed = set(antigo.get("_editados") or [])
    for k in POR_CHAVE:
        if (novo["campos"].get(k) or {}) != ((antigo.get("campos") or {}).get(k) or {}) and (novo["campos"].get(k) or {}).get("valor"):
            ed.add(f"campo:{k}")
            novo["campos"][k]["verificado"] = None  # texto do analista: não é a IA
    for nome in ("empresas", "classes", "bens", "qgc", "observacoes"):
        if (novo.get(nome) or []) != (antigo.get(nome) or []):
            ed.add(f"tabela:{nome}")
    novo["_editados"] = sorted(ed)
    return novo


# ---------------- origens conferidas pelo código (contra a alucinação de ID e página) ----------------
_TIPOS_DOC = [
    ("constatacao", "relatório de constatação prévia", "situação das empresas, documentos apresentados, viabilidade"),
    ("laudo.*(bens|avalia)|avaliacao", "laudo de avaliação de bens", "tipos de bens, valor de mercado e de liquidação forçada, localidades vistoriadas, imóveis"),
    ("viabilidade|econ.mico", "laudo de viabilidade econômico-financeira", "situação financeira, projeções, lucro ou prejuízo, ativo e passivo"),
    ("plano|prj|aditivo", "plano de recuperação judicial (PRJ) ou aditivo", "classes, proposta de pagamento por classe (deságio, parcelas, carência, atualização), credores apoiadores e colaborativos, FGTS, alienação de bens, reestruturações, QGC do plano"),
    ("ata|assembleia|agc", "ata ou edital de assembleia geral de credores", "data e convocação, cenários de votação, aprovação do plano"),
    ("homolog|deferimento|processamento|decisao|despacho|sentenca", "decisão judicial", "datas de deferimento do processamento e de homologação/concessão, administrador judicial nomeado, determinações, situação atual"),
    ("edital", "edital (art. 7º, § 2º, da Lei 11.101/2005)", "data de publicação e prazos"),
    ("quadro|qgc|relacao|credores", "quadro ou relação de credores", "valores por classe, totais, data e fonte do quadro"),
    ("administrador|relatorio|aj", "relatório da administração judicial", "QGC da administração judicial, situação financeira, cumprimento do plano, pagamentos, fiscalização"),
    ("uniao|fazenda|pgfn|tribut|certidao", "manifestação da União/Fazenda Nacional ou certidão", "créditos tributários, certidão do art. 57, transação, pedidos"),
    ("inicial|pedido de recuperacao|recuperacao judicial", "petição inicial do pedido de recuperação judicial", "empresas e CNPJ, data de ajuizamento, causas da crise, grupo econômico, situação financeira"),
]


def dica_documento(nome_arquivo: str, inicio: str = "") -> str:
    """Instrução que acompanha CADA documento enviado à IA: o que ele costuma conter e que só vale o que ele comprova."""
    base = _nk(f"{nome_arquivo} {inicio[:400]}")
    achado = next(((d, f) for pad, d, f in _TIPOS_DOC if re.search(pad, base)), None)
    return (f"ESCOPO: este é UM documento do processo ('{nome_arquivo}')" + (f", que parece ser {achado[0]}. Em geral ele traz: {achado[1]}." if achado else ".")
            + " Preencha SOMENTE o que ESTE documento comprova; deixe null o resto (outros documentos serão analisados em seguida e o sistema junta tudo). "
              "NÃO responda 'NÃO' nem 'inexistente' só porque o documento não menciona o assunto. Cite a origem apenas com os rótulos entre colchetes deste documento.\n\n")


def _norm_pagina(t: str) -> str:
    return "".join((t or "").split()).replace(".", "").lower()


def _salientes(texto: str) -> list[str]:
    """Datas, valores, CNPJ/CPF, nº de processo e percentuais do texto, no formato de comparação (sem pontos nem espaços)."""
    t = texto or ""
    toks = re.findall(r"\d{2}/\d{2}/\d{4}", t) + re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", t) + re.findall(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}", t)
    toks += [m.group(1) for m in re.finditer(r"R\$\s*([\d.]+(?:,\d{2})?)", t)]
    return list(dict.fromkeys(_norm_pagina(x) for x in toks))


def _palavras(texto: str) -> list[str]:
    ws = [w for w in _nk(_ORIG.sub("", texto or "")).split() if len(w) >= 5]
    return list(dict.fromkeys(ws))[:14]


def _avalia(texto: str, pgs_norm: list[str]) -> tuple[int, float]:
    """(índice da melhor página, qualidade 0..1): fração dos dados do texto (datas, valores…) ou das palavras raras que a página contém."""
    toks = _salientes(texto)
    alvo = toks or _palavras(texto)
    if not alvo:
        return -1, 0.0
    melhor, q = -1, 0.0
    for i, pg in enumerate(pgs_norm):
        achou = sum(1 for a in alvo if a in pg)
        if achou / len(alvo) > q:
            melhor, q = i, achou / len(alvo)
        if q == 1.0:
            break
    # sem dados fortes (só palavras), exige mais evidência e um mínimo de palavras
    if not toks and len(alvo) < 3:
        return -1, 0.0
    return melhor, q


def _rotulos_validos(pages: list[dict]) -> set:
    return {re.sub(r"\s+", "", p["label"]).lower() for p in pages if p.get("label")}


def _inline_validas(texto: Optional[str], validos: set, ids: set) -> Optional[str]:
    """Tira de dentro do texto as citações '(ID 123, pg. 4)' que não existem nos documentos (alucinação)."""
    if not texto:
        return texto

    def troca(m):
        corpo = m.group(0)[1:-1]
        partes = [re.sub(r"\s+", "", x).lower() for x in re.split(r";", corpo)]
        ok = all(x in validos or re.sub(r",?pg\.?\d+(?:a\d+)?$", "", x) in ids for x in partes if x)
        return m.group(0) if ok else ""
    t = _ORIG.sub(troca, texto)
    return re.sub(r"\s{2,}", " ", re.sub(r"\s+([.,;])", r"\1", t)).strip()


def ancorar(rel: dict, pages: list[dict]) -> dict:
    """Confere CADA informação do relatório com o texto das páginas lidas: a origem (Evento/ID e página) passa a ser a da página que
    realmente contém os dados (datas, valores, CNPJ…); origem que a IA inventou é trocada; o que não se encontra nos documentos
    fica marcado como não localizado. Devolve contadores."""
    info = {"confirmados": 0, "corrigidos": 0, "nao_localizados": 0}
    pgs = [_norm_pagina(p["text"]) for p in pages]
    rot = [p.get("label") for p in pages]
    validos = _rotulos_validos(pages)
    ids = {re.sub(r"\s+", "", re.sub(r",?\s*pg\.?\s*\d+.*$", "", p["label"])).lower() for p in pages if p.get("label")}

    por_rotulo = {re.sub(r"\s+", "", r).lower(): i for i, r in enumerate(rot) if r}

    def confere(item: dict, texto: str, simples: bool = False):
        """item: dict com 'origem' e 'verificado' (campo ou linha de tabela)."""
        orig = item.get("origem")
        oi = por_rotulo.get(re.sub(r"\s+", "", orig).lower()) if orig else None
        if simples:  # SIM/NÃO e afins: sem dados para comparar; só vale a origem se ela existir
            item["origem"] = orig if oi is not None else None
            item["verificado"] = None
            return
        toks = _salientes(texto)
        limiar = 0.99 if toks else 0.5
        i, q = _avalia(texto, pgs)
        if q >= limiar or (len(toks) >= 3 and q >= 0.67):
            if oi is not None and _avalia(texto, [pgs[oi]])[1] >= min(limiar, q):
                info["confirmados"] += 1  # a origem que a IA deu realmente contém os dados
            else:
                info["corrigidos" if orig else "confirmados"] += 1
                item["origem"] = rot[i]
            item["verificado"] = True
        else:
            item["origem"] = orig if oi is not None else None
            item["verificado"] = False
            info["nao_localizados"] += 1

    for k, c in (rel.get("campos") or {}).items():
        v = c.get("valor")
        if not v:
            continue
        c["valor"] = _inline_validas(v, validos, ids)
        confere(c, c["valor"], simples=POR_CHAVE[k][2] == "simnao")
    for lista, campos_txt in (("empresas", ("cnpj", "nome")), ("classes", ("valor_sujeito", "classe")), ("bens", ("valor_mercado", "tipo")),
                              ("observacoes", ("data", "texto"))):
        for r in rel.get(lista) or []:
            texto = " ".join(str(r.get(x) or "") for x in campos_txt)
            if lista == "observacoes":
                r["texto"] = _inline_validas(r.get("texto"), validos, ids)
            if texto.strip():
                confere(r, texto)
    for q in rel.get("qgc") or []:
        texto = " ".join([q.get("total") or "", q.get("data") or ""] + [cl.get("valor") or "" for cl in q.get("classes") or []])
        if texto.strip():
            confere(q, texto)
    return info


def pedacos(pages: list[dict], limite: int) -> list[list[dict]]:
    """Páginas de UM documento em blocos de até `limite` caracteres (documento enorme = várias chamadas, nunca contexto cortado)."""
    out, cur, n = [], [], 0
    for p in pages:
        t = len(p["text"])
        if cur and n + t > limite:
            out.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += t
    if cur:
        out.append(cur)
    return out


def data_documento(pages: list[dict]) -> str:
    """Data de assinatura do documento (PJe) para pôr os documentos em ordem cronológica; sem data, vai para o fim."""
    for p in pages[:8]:
        m = re.search(r"Assinado eletronicamente por:[^\n]*?(\d{2})/(\d{2})/(\d{4})", p.get("text") or "")
        if m:
            return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return "9999"


def _chave_data(d: Optional[str]) -> str:
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else "9999"


def brl(x: float) -> str:
    return "R$ " + f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def brl_num(x: float) -> str:
    return brl(x)


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
        out["desconto_numero"] = sum(v * d / 100 for _, v, d in validas)
        out["linhas_desagio"] = [[c["classe"], brl(v), f"{d:g}%".replace(".", ","), brl(v * d / 100)] for c, v, d in validas] + \
                                [["Total / média ponderada", brl(total), f"{out['desagio']:g}%".replace(".", ","), brl(out["desconto_numero"])]]
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
# Paleta sóbria (documento formal): azul-marinho nos títulos, um azul de destaque e âmbar só para pontos de atenção.
NAVY, ACCENT, GRAY, LIGHT, AMBER_BG, AMBER, LINE = "0F2A4D", "1351B4", "5D6B7C", "EEF2F7", "FFF4D6", "C98A00", "C9D3E0"
_ORIG = re.compile(r"(\((?:Evento|ID|VOL)\b[^)]*\))")
_DATA = re.compile(r"\d{2}/\d{2}/\d{4}")


def segmentos(texto: str) -> list[tuple[str, bool]]:
    """Separa o texto das origens '(ID 123, pg. 4)', que o documento mostra em cinza e menor, para não poluir a leitura."""
    return [(p, bool(_ORIG.fullmatch(p))) for p in _ORIG.split(texto or "") if p]


def _lista_em_prosa(itens: list[str]) -> str:
    itens = [i.rstrip(".") if i.endswith("..") else i for i in itens]
    return itens[0] if len(itens) == 1 else (", ".join(itens[:-1]) + " e " + itens[-1]) if itens else ""


_ABREV = {"art", "arts", "n", "nº", "no", "pg", "pgs", "fls", "inc", "dr", "dra", "sr", "sra", "ltda", "s.a", "proc", "cf", "p", "v", "ex", "obs"}


def _frase(t: Optional[str], limite: int = 170) -> str:
    """Primeira frase do texto (sem as origens), sem cortar em abreviações como 'art.' ou 'nº'."""
    t = " ".join(_ORIG.sub("", t or "").split())
    t = re.sub(r"\s+([.,;:])", r"\1", t)
    f = t
    for m in re.finditer(r"[.!?](?=\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9]|$)", t):
        palavra = re.findall(r"[\wÀ-ÿ.]+$", t[:m.start()])
        if m.group(0) == "." and palavra and palavra[0].lower().rstrip(".") in _ABREV:
            continue
        f = t[:m.end()]
        break
    return f if len(f) <= limite else f[:limite - 1].rsplit(" ", 1)[0] + "…"


def _v(rel: dict, k: str) -> tuple[Optional[str], Optional[str]]:
    c = (rel.get("campos") or {}).get(k) or {}
    return c.get("valor"), c.get("origem")


def datas_chave(rel: dict) -> list[tuple[str, str, str]]:
    """(chave de ordenação, dd/mm/aaaa, rótulo) das datas que marcam a linha do tempo do processo."""
    itens = []
    for k, rot in (("data_ajuizamento", "Ajuizamento"), ("data_deferimento", "Deferimento do processamento"), ("data_plano", "Apresentação do plano"),
                   ("data_agc", "Assembleia de credores"), ("data_concessao", "Concessão da RJ"), ("data_encerramento", "Encerramento")):
        v, _ = _v(rel, k)
        if k == "data_concessao" and not v:
            v, _ = _v(rel, "data_homologacao")
        m = _DATA.search(v or "")
        if m:
            itens.append((_chave_data(m.group(0)), m.group(0), rot))
    return sorted(itens)


def resumo_linhas(rel: dict, numero: str, calc: dict) -> list[tuple[str, str, Optional[str]]]:
    """Quadro-resumo do topo: (rótulo, valor, tipo) com tipo 'sn' (SIM/NÃO), 'nc' (não consta) ou None."""
    def g(k):
        return _v(rel, k)[0]
    emp = [e["nome"] for e in rel.get("empresas") or [] if e.get("nome")]
    linhas = [("Processo", numero, None), ("Vara", g("vara"), None), ("Administração judicial", g("administrador_judicial"), None),
              ("Empresas recuperandas", (_lista_em_prosa(emp).rstrip(".") if emp else None), None),
              ("Plano aprovado em assembleia", g("aprovado_agc"), "sn"), ("Plano homologado", g("homologado"), "sn"),
              ("Recuperação encerrada", g("encerrada"), "sn"), ("Dívida sujeita à RJ", g("divida_total"), None),
              ("Deságio ponderado geral", (f"{str(calc['desagio']).replace('.', ',')}%" if calc.get("desagio") is not None else None), None),
              ("Créditos da União/INSS", _frase(g("valores_uniao"), 150) or None, None), ("Situação atual", _frase(g("situacao_atual"), 170) or None, None)]
    return [(r, v or NC, ("nc" if not v else t)) for r, v, t in linhas]


def pontos_atencao(rel: dict, calc: dict) -> list[str]:
    out = list(calc["avisos"])
    agc, homol = _v(rel, "aprovado_agc")[0], _v(rel, "homologado")[0]
    if agc == "SIM" and homol == "NÃO":
        out.append("O plano foi aprovado em assembleia, mas ainda não consta a sua homologação.")
    for k, rot in (("valores_uniao", "Créditos da União/INSS"), ("certidao_art57", "Certidão do art. 57"), ("convolacao", "Convolação em falência"), ("fraude", "Indício de fraude")):
        v = _v(rel, k)[0]
        if v and not re.match(r"(?i)\s*(n[ãa]o|at[ée] o presente|nenhum|sem )", v):
            out.append(f"{rot}: {_frase(v, 200)}")
    return out


def blocos(rel: dict, numero: str, hoje: Optional[date] = None) -> list[tuple]:
    """Documento como lista de blocos (a mesma para Word e PDF): titulo, resumo, atencao, timeline, secao, ficha, rot, tabela, cartoes,
    barras, cronologia e nota. Texto corrido, sem marcadores; tabelas só para números e datas."""
    hoje = hoje or date.today()
    c = rel.get("campos") or {}
    calc = calcular(rel)
    B: list[tuple] = [("titulo", "RELATÓRIO – RECUPERAÇÃO JUDICIAL", f"Processo nº {numero}", f"Elaborado em {hoje:%d/%m/%Y}"),
                      ("resumo", resumo_linhas(rel, numero, calc))]
    at = pontos_atencao(rel, calc)
    if at:
        B.append(("atencao", at))
    tl = datas_chave(rel)
    if len(tl) >= 2:
        B.append(("timeline", [(d, r) for _, d, r in tl]))

    faltantes: list[str] = []

    def ficha_rot(chaves_secao: str, pular=()):
        saida, grupo = [], []
        for k, rot, tipo, sec, _ in CAMPOS:
            if sec != chaves_secao or k in pular:
                continue
            campo = c.get(k) or {}
            valor, origem = campo.get("valor"), campo.get("origem")
            if not valor:  # visual limpo: o que não consta vai para uma única linha ao fim da seção
                faltantes.append(rot)
                continue
            if origem and campo.get("verificado") is False:
                origem = f"{origem}; a confirmar"
            if tipo == "paragrafo":
                if grupo:
                    saida.append(("ficha", grupo))
                    grupo = []
                saida.append(("rot", rot, valor, origem))
            else:
                grupo.append((rot, valor, origem))
        if grupo:
            saida.append(("ficha", grupo))
        return saida

    def fecha_secao():
        if faltantes:
            B.append(("nota", "Não consta nos documentos analisados: " + _lista_em_prosa(list(faltantes)).rstrip(".") + "."))
            faltantes.clear()

    n = 0
    for chave, titulo in SECOES:
        fecha_secao()
        n += 1
        B.append(("secao", n, titulo))
        if chave == "identificacao":
            emp = rel.get("empresas") or []
            if emp:
                nomes = [f"{e['nome'] or 'empresa sem nome'}" + (f" (CNPJ {e['cnpj']})" if e.get("cnpj") else "") for e in emp]
                B.append(("rot", "EMPRESAS RECUPERANDAS", _lista_em_prosa(nomes).rstrip(".") + ".", None))
                sim = [e["nome"] for e in emp if e.get("em_recuperacao_judicial") == "SIM" and e.get("nome")]
                nao = [e["nome"] for e in emp if e.get("em_recuperacao_judicial") == "NÃO" and e.get("nome")]
                if sim or nao:
                    partes = ([f"consta a expressão “em recuperação judicial” no nome de {_lista_em_prosa(sim)}"] if sim else []) + \
                             ([f"não consta no nome de {_lista_em_prosa(nao)}"] if nao else [])
                    frase = "; ".join(partes)
                    B.append(("rot", "EXPRESSÃO “EM RECUPERAÇÃO JUDICIAL”", f"{frase[0].upper()}{frase[1:]}".rstrip(".") + ".", None))
            else:
                B.append(("rot", "EMPRESAS RECUPERANDAS", NC, None))
            B += ficha_rot(chave)
        elif chave == "plano":
            fr = ficha_rot(chave)  # ficha das datas, parágrafo dos documentos, [tabela das classes], demais parágrafos
            B += fr[:2]
            if rel.get("classes"):
                rows = [[cl.get("classe") or "", cl.get("proposta") or "–", cl.get("valor_sujeito") or "–",
                         (f"{cl['desagio_percentual']:g}%".replace(".", ",") if cl.get("desagio_percentual") is not None else "–")] for cl in rel["classes"]]
                B.append(("tabela", ["Classe de credores", "Proposta de pagamento", "Valor sujeito", "Deságio"], rows, [27, 41, 19, 13], "llrr"))
            B += fr[2:]
        elif chave == "bens":
            cards = [("Dívida sujeita à RJ", (c.get("divida_total") or {}).get("valor") or "–", "valor total do passivo"),
                     ("Deságio ponderado geral", f"{str(calc['desagio']).replace('.', ',')}%" if calc.get("desagio") is not None else "–", "média ponderada pelo valor de cada classe"),
                     ("Bens: valor de mercado", calc.get("total_mercado") or "–", "soma do laudo de avaliação")]
            B.append(("cartoes", cards))
            lb = (c.get("laudo_bens") or {})
            B.append(("rot", "LAUDO DE BENS", lb.get("valor") or NC, lb.get("origem") if lb.get("valor") else None))
            if rel.get("bens"):
                rows = [[b.get("tipo") or "", b.get("valor_mercado") or "–", b.get("valor_liquidacao") or "–", b.get("localidade") or "–"] for b in rel["bens"]]
                if calc.get("total_mercado") or calc.get("total_liquidacao"):
                    rows.append(["Total", calc.get("total_mercado") or "–", calc.get("total_liquidacao") or "–", ""])
                B.append(("tabela", ["Bem avaliado", "Valor de mercado", "Liquidação forçada", "Localidade"], rows, [34, 22, 22, 22], "lrrl"))
            qgcs = rel.get("qgc") or []
            if qgcs:
                classes = list(dict.fromkeys(cl["classe"] for q in qgcs for cl in q.get("classes") or []))
                cab = ["Classe (QGC)"] + [f"{q.get('fonte') or 'QGC'}" + (f" · {q['data']}" if q.get("data") else "") for q in qgcs]
                rows = [[cl] + [next((x.get("valor") or "–" for x in q.get("classes") or [] if x.get("classe") == cl), "–") for q in qgcs] for cl in classes]
                rows.append(["Total"] + [q.get("total") or "–" for q in qgcs])
                B.append(("tabela", cab, rows, [34] + [66 // len(qgcs)] * len(qgcs), "l" + "r" * len(qgcs)))
                orig = [f"{q.get('fonte') or 'QGC'}: {q['origem']}" for q in qgcs if q.get("origem")]
                if orig:
                    B.append(("nota", "Origem dos quadros: " + "; ".join(orig) + "."))
            barras = [(cl["classe"], moeda_float(cl.get("valor_sujeito")), cl.get("valor_sujeito"),
                       (f"deságio {cl['desagio_percentual']:g}%".replace(".", ",") if cl.get("desagio_percentual") is not None else ""))
                      for cl in rel.get("classes") or [] if moeda_float(cl.get("valor_sujeito"))]
            if len(barras) >= 2:
                B.append(("barras", "Composição do passivo sujeito à RJ, por classe", barras))
            ed = (c.get("edital") or {})
            B.append(("ficha", [("PUBLICAÇÃO DO EDITAL", ed.get("valor") or NC, ed.get("origem") if ed.get("valor") else None)]))
            if calc.get("desagio") is not None:
                B.append(("rot", "COMO O DESÁGIO PONDERADO FOI CALCULADO",
                          "O deságio de cada classe é o percentual de redução do crédito previsto no plano. Para chegar ao índice geral, cada percentual é multiplicado "
                          "pelo valor da respectiva classe no quadro de credores (o que dá o desconto, em reais); soma-se o desconto de todas as classes e divide-se pela soma "
                          f"dos valores sujeitos à recuperação. Resultado: {brl_num(calc['desconto_numero'])} de desconto sobre {calc['base']}, ou seja, "
                          f"{str(calc['desagio']).replace('.', ',')}%. Classes sem valor ou sem percentual informado ficam fora da conta.", None))
                B.append(("tabela", ["Classe de credores", "Valor sujeito", "Deságio", "Desconto"], calc["linhas_desagio"], [38, 22, 14, 26], "lrrr"))
            else:
                B.append(("rot", "COMO O DESÁGIO PONDERADO FOI CALCULADO", "Não foi possível calcular: faltam o valor por classe ou o deságio de alguma classe.", None))
        elif chave == "observacoes":
            obs = sorted(rel.get("observacoes") or [], key=lambda o: _chave_data(o.get("data")))
            B.append(("cronologia", [(o.get("data") or "s/d", (o["texto"] or "").rstrip(" ."), o.get("origem")) for o in obs]) if obs else ("rot", "CRONOLOGIA", NC, None))
        else:
            B += ficha_rot(chave)
    fecha_secao()
    if calc["avisos"]:
        B.append(("nota", "Pontos a conferir: " + " ".join(calc["avisos"])))
    return B


def texto_plano(rel: dict, numero: str) -> str:
    """Todo o texto do documento, em ordem (para conferência e testes)."""
    def achata(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, (list, tuple)):
            for y in x:
                yield from achata(y)
    return "\n".join(achata(blocos(rel, numero)))


def _rot(r: str) -> str:
    return r if r.endswith("?") else r + ":"


def _fmt_pct_barra(v: float, total: float) -> float:
    return max(1.0, round(v / total * 100, 1)) if total else 0.0


# ---------------- Word ----------------
def gerar_docx(rel: dict, numero: str) -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    d = Document()
    sec = d.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin, sec.bottom_margin = Cm(2.2), Cm(2)
    LARG = 17.0
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    st.paragraph_format.space_after = Pt(4)
    rgb = lambda h: RGBColor.from_string(h)

    def shade(cell, fill):
        tc = cell._tc.get_or_add_tcPr()
        el = OxmlElement("w:shd")
        el.set(qn("w:val"), "clear")
        el.set(qn("w:color"), "auto")
        el.set(qn("w:fill"), fill)
        tc.append(el)

    def borders(cell, **kw):  # lado=(espessura em 1/8 pt, cor) ou None
        tc = cell._tc.get_or_add_tcPr()
        el = OxmlElement("w:tcBorders")
        for lado in ("top", "left", "bottom", "right"):
            b = OxmlElement(f"w:{lado}")
            spec = kw.get(lado)
            if spec:
                b.set(qn("w:val"), "single")
                b.set(qn("w:sz"), str(spec[0]))
                b.set(qn("w:color"), spec[1])
            else:
                b.set(qn("w:val"), "nil")
            el.append(b)
        tc.append(el)

    def margens(cell, v=60, h=100):
        tc = cell._tc.get_or_add_tcPr()
        el = OxmlElement("w:tcMar")
        for lado, val in (("top", v), ("left", h), ("bottom", v), ("right", h)):
            m = OxmlElement(f"w:{lado}")
            m.set(qn("w:w"), str(val))
            m.set(qn("w:type"), "dxa")
            el.append(m)
        tc.append(el)

    def run(p, texto, bold=False, italic=False, size=None, color=None):
        r = p.add_run(texto)
        r.bold, r.italic = bold, italic
        if size:
            r.font.size = Pt(size)
        if color:
            r.font.color.rgb = rgb(color)
        return r

    def corpo(p, texto, size=10.5, italic=False, cor=None):  # texto com as origens em cinza e menor
        for seg, orig in segmentos(texto):
            run(p, seg, size=8.5 if orig else size, italic=italic, color=GRAY if orig else cor)

    def tabela(larguras, linhas=0):
        t = d.add_table(rows=linhas, cols=len(larguras))
        t.autofit = False
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        pr = t._tbl.tblPr
        lay = OxmlElement("w:tblLayout")
        lay.set(qn("w:type"), "fixed")
        pr.append(lay)
        t._larg = larguras
        for i, col in enumerate(t.columns):  # o Word e o LibreOffice leem as larguras da grade (tblGrid), não só das células
            col.width = Cm(larguras[i])
        return t

    def nova_linha(t):
        r = t.add_row()
        for i, c in enumerate(r.cells):
            c.width = Cm(t._larg[i])
        return r

    def cel(c, fill=None, **b):
        if fill:
            shade(c, fill)
        borders(c, **b)
        margens(c)
        return c.paragraphs[0]

    def espaco(pt=4):
        p = d.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = Pt(pt)
        return p

    # cabeçalho e rodapé
    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run(hp, f"Recuperação Judicial · Processo nº {numero}", size=8, color=GRAY)
    pb = OxmlElement("w:pBdr")
    bt = OxmlElement("w:bottom")
    for k, v in (("val", "single"), ("sz", "4"), ("space", "4"), ("color", LINE)):
        bt.set(qn(f"w:{k}"), v)
    pb.append(bt)
    hp._p.get_or_add_pPr().append(pb)
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run(fp, "página ", size=8, color=GRAY)
    for campo in ("PAGE", None, "NUMPAGES"):
        if campo is None:
            run(fp, " de ", size=8, color=GRAY)
            continue
        r = fp.add_run()
        r.font.size, r.font.color.rgb = Pt(8), rgb(GRAY)
        for tipo, txt in (("begin", None), (None, campo), ("end", None)):
            if tipo:
                el = OxmlElement("w:fldChar")
                el.set(qn("w:fldCharType"), tipo)
            else:
                el = OxmlElement("w:instrText")
                el.set(qn("xml:space"), "preserve")
                el.text = txt
            r._r.append(el)

    for b in blocos(rel, numero):
        k = b[0]
        if k == "titulo":
            t = tabela([LARG], 1)
            p = cel(t.rows[0].cells[0], NAVY)
            t.rows[0].cells[0].width = Cm(LARG)
            run(p, b[1], bold=True, size=17, color="FFFFFF")
            p2 = t.rows[0].cells[0].add_paragraph()
            run(p2, f"{b[2]}  ·  {b[3]}", size=9.5, color=LINE)
            espaco(8)
        elif k == "resumo":
            p = d.add_paragraph()
            run(p, "RESUMO EXECUTIVO", bold=True, size=9, color=ACCENT)
            p.paragraph_format.keep_with_next = True
            t = tabela([5.0, 12.0])
            for rot, val, tipo in b[1]:
                r = nova_linha(t)
                run(cel(r.cells[0], LIGHT, bottom=(4, "FFFFFF")), rot, bold=True, size=9, color=NAVY)
                pv = cel(r.cells[1], None, bottom=(4, LINE))
                if tipo == "nc":
                    run(pv, val, italic=True, size=9.5, color=GRAY)
                elif tipo == "sn":
                    run(pv, val, bold=True, size=10, color=NAVY if val == "SIM" else "3A3A3A")
                else:
                    corpo(pv, val, size=10)
            espaco(8)
        elif k == "atencao":
            t = tabela([LARG], 1)
            p = cel(t.rows[0].cells[0], AMBER_BG, left=(24, AMBER))
            t.rows[0].cells[0].width = Cm(LARG)
            run(p, "PONTOS DE ATENÇÃO", bold=True, size=9, color="8A5A00")
            for item in b[1]:
                q = t.rows[0].cells[0].add_paragraph()
                q.paragraph_format.space_after = Pt(2)
                corpo(q, item, size=10)
            espaco(8)
        elif k == "timeline":
            n = len(b[1])
            t = tabela([LARG / n] * n, 2)
            for i, (dt, rot) in enumerate(b[1]):
                run(cel(t.rows[0].cells[i], None, top=(18, ACCENT)), dt, bold=True, size=10.5, color=NAVY).alignment = WD_ALIGN_PARAGRAPH.CENTER
                t.rows[0].cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                pr_ = cel(t.rows[1].cells[i], None)
                pr_.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run(pr_, rot, size=8.5, color=GRAY)
                t.rows[0].cells[i].width = t.rows[1].cells[i].width = Cm(LARG / n)
            espaco(6)
        elif k == "secao":
            p = d.add_paragraph()
            p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(14), Pt(6)
            p.paragraph_format.keep_with_next = True
            run(p, f"{b[1]}  ", bold=True, size=12.5, color=ACCENT)
            run(p, b[2].upper(), bold=True, size=11.5, color=NAVY)
            pb = OxmlElement("w:pBdr")
            bt = OxmlElement("w:bottom")
            for kk, vv in (("val", "single"), ("sz", "6"), ("space", "2"), ("color", ACCENT)):
                bt.set(qn(f"w:{kk}"), vv)
            pb.append(bt)
            p._p.get_or_add_pPr().append(pb)
        elif k == "ficha":
            t = tabela([5.0, 12.0])
            for rot, val, origem in b[1]:
                r = nova_linha(t)
                run(cel(r.cells[0], None, bottom=(4, LINE)), rot, bold=True, size=8.5, color=NAVY)
                pv = cel(r.cells[1], None, bottom=(4, LINE))
                if val == NC:
                    run(pv, val, italic=True, size=10, color=GRAY)
                else:
                    corpo(pv, val)
                    if origem:
                        run(pv, f"  ({origem})", size=8.5, color=GRAY)
            espaco(4)
        elif k == "rot":
            p = d.add_paragraph()
            p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(8), Pt(1)
            p.paragraph_format.keep_with_next = True
            run(p, b[1], bold=True, size=8.5, color=NAVY)
            q = d.add_paragraph()
            q.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            q.paragraph_format.space_after = Pt(4)
            if b[2] == NC:
                run(q, b[2], italic=True, size=10, color=GRAY)
            else:
                corpo(q, b[2])
                if b[3]:
                    run(q, f"  ({b[3]})", size=8.5, color=GRAY)
        elif k == "tabela":
            cab, rows, pct, al = b[1], b[2], b[3], b[4]
            larg = [LARG * x / sum(pct) for x in pct]
            t = tabela(larg)
            r = nova_linha(t)
            for i, h in enumerate(cab):
                p = cel(r.cells[i], NAVY)
                run(p, h, bold=True, size=9, color="FFFFFF")
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if al[i] == "r" else WD_ALIGN_PARAGRAPH.LEFT
            for j, row in enumerate(rows):
                tot = row[0] == "Total"
                r = nova_linha(t)
                for i, v in enumerate(row):
                    p = cel(r.cells[i], LIGHT if (j % 2 == 1 and not tot) else None, top=(8, NAVY) if tot else None, bottom=(4, LINE))
                    run(p, str(v), bold=tot, size=9.5)
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if al[i] == "r" else WD_ALIGN_PARAGRAPH.LEFT
            espaco(6)
        elif k == "cartoes":
            n = len(b[1])
            t = tabela([LARG / n] * n, 1)
            for i, (rot, val, nota) in enumerate(b[1]):
                c = t.rows[0].cells[i]
                c.width = Cm(LARG / n)
                p = cel(c, LIGHT, top=(18, ACCENT))
                run(p, rot.upper(), bold=True, size=7.5, color=GRAY)
                q = c.add_paragraph()
                run(q, val, bold=True, size=15, color=NAVY)
                q.paragraph_format.space_after = Pt(0)
                run(c.add_paragraph(), nota, size=8, color=GRAY)
            espaco(8)
        elif k == "barras":
            p = d.add_paragraph()
            run(p, b[1].upper(), bold=True, size=8.5, color=NAVY)
            p.paragraph_format.keep_with_next = True
            total = sum(x[1] for x in b[2])
            BAR = 7.4
            t = tabela([5.2, BAR + 0.4, 4.4])
            for rot, v, vs, extra in b[2]:
                r = nova_linha(t)
                run(cel(r.cells[0], None, bottom=(4, LINE)), rot, size=9, color=NAVY)
                pb_ = cel(r.cells[1], None, bottom=(4, LINE))  # barra = parágrafo sombreado com recuo à direita proporcional
                ratio = max(0.02, v / max(x[1] for x in b[2]))
                pb_.paragraph_format.right_indent = Cm(BAR * (1 - ratio))
                pb_.paragraph_format.space_before = pb_.paragraph_format.space_after = Pt(3)
                pb_.paragraph_format.line_spacing = Pt(8)
                sh = OxmlElement("w:shd")
                sh.set(qn("w:val"), "clear")
                sh.set(qn("w:color"), "auto")
                sh.set(qn("w:fill"), ACCENT)
                pb_._p.get_or_add_pPr().append(sh)
                run(pb_, " ", size=4)
                pv = cel(r.cells[2], None, bottom=(4, LINE))
                pv.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                run(pv, f"{vs}", bold=True, size=9, color=NAVY)
                run(pv, f"  {v / total * 100:.0f}%" + (f" · {extra}" if extra else ""), size=8, color=GRAY)
            espaco(6)
        elif k == "cronologia":
            t = tabela([2.6, 14.4])
            for data_, texto, origem in b[1]:
                r = nova_linha(t)
                run(cel(r.cells[0], None, bottom=(4, LINE)), data_, bold=True, size=9.5, color=ACCENT)
                pv = cel(r.cells[1], None, bottom=(4, LINE))
                corpo(pv, texto + ("." if not texto.endswith((".", ")")) else ""), size=10)
                if origem and origem not in texto:
                    run(pv, f" ({origem})", size=8.5, color=GRAY)
            espaco(4)
        elif k == "nota":
            q = d.add_paragraph()
            run(q, b[1], italic=True, size=8.5, color=GRAY)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------------- PDF ----------------
def gerar_pdf(rel: dict, numero: str) -> bytes:
    import html

    import pymupdf

    e = lambda x: html.escape(str(x))

    def corpo(texto, size="10pt"):
        return "".join(f'<span style="color:#{GRAY};font-size:8pt">{e(seg)}</span>' if orig else e(seg) for seg, orig in segmentos(texto))

    def td(conteudo, estilo="", **attrs):
        a = "".join(f' {k}="{v}"' for k, v in attrs.items())
        return f'<td style="{estilo}"{a}>{conteudo}</td>'

    h = ['<body style="font-family:sans-serif;font-size:10pt;color:#222222">']
    for b in blocos(rel, numero):
        k = b[0]
        if k == "titulo":
            h.append(f'<table style="width:100%" cellspacing="0" cellpadding="10"><tr>{td(f"<span style=\"color:#ffffff;font-size:17pt\"><b>{e(b[1])}</b></span><br><span style=\"color:#{LINE};font-size:9pt\">{e(b[2])} &nbsp;·&nbsp; {e(b[3])}</span>", f"background-color:#{NAVY}")}</tr></table><p style="font-size:4pt">&nbsp;</p>')
        elif k == "resumo":
            h.append(f'<p style="color:#{ACCENT};font-size:8.5pt;margin-bottom:2pt"><b>RESUMO EXECUTIVO</b></p><table style="width:100%" cellspacing="0" cellpadding="4">')
            for rot, val, tipo in b[1]:
                if tipo == "nc":
                    v = f'<i style="color:#{GRAY}">{e(val)}</i>'
                elif tipo == "sn":
                    v = f'<b style="color:#{NAVY if val == "SIM" else "3a3a3a"}">{e(val)}</b>'
                else:
                    v = corpo(val)
                h.append(f'<tr>{td(f"<b>{e(rot)}</b>", f"width:29%;background-color:#{LIGHT};color:#{NAVY};font-size:8.5pt;border-bottom:1px solid #ffffff")}{td(v, f"width:71%;border-bottom:1px solid #{LINE}")}</tr>')
            h.append('</table><p style="font-size:4pt">&nbsp;</p>')
        elif k == "atencao":
            itens = "".join(f'<p style="margin-top:2pt;margin-bottom:0">{corpo(i)}</p>' for i in b[1])
            h.append(f'<table style="width:100%" cellspacing="0" cellpadding="7"><tr>{td(f"<b style=\"color:#8a5a00;font-size:8.5pt\">PONTOS DE ATENÇÃO</b>{itens}", f"background-color:#{AMBER_BG};border-left:3px solid #{AMBER}")}</tr></table><p style="font-size:4pt">&nbsp;</p>')
        elif k == "timeline":
            n = len(b[1])
            cels = "".join(td(f'<b style="color:#{NAVY}">{e(dt)}</b><br><span style="color:#{GRAY};font-size:8pt">{e(rot)}</span>', f"width:{100 // n}%;text-align:center;border-top:3px solid #{ACCENT}") for dt, rot in b[1])
            h.append(f'<table style="width:100%" cellspacing="2" cellpadding="4"><tr>{cels}</tr></table><p style="font-size:4pt">&nbsp;</p>')
        elif k == "secao":
            h.append(f'<h2 style="font-size:11.5pt;color:#{NAVY};margin-top:12pt;margin-bottom:4pt;border-bottom:1px solid #{ACCENT}"><span style="color:#{ACCENT}">{b[1]}</span> &nbsp;{e(b[2].upper())}</h2>')
        elif k == "ficha":
            h.append('<table style="width:100%" cellspacing="0" cellpadding="4">')
            for rot, val, origem in b[1]:
                v = f'<i style="color:#{GRAY}">{e(val)}</i>' if val == NC else corpo(val) + (f' <span style="color:#{GRAY};font-size:8pt">({e(origem)})</span>' if origem else "")
                h.append(f'<tr>{td(f"<b>{e(rot)}</b>", f"width:29%;color:#{NAVY};font-size:8pt;border-bottom:1px solid #{LINE}")}{td(v, f"width:71%;border-bottom:1px solid #{LINE}")}</tr>')
            h.append('</table><p style="font-size:3pt">&nbsp;</p>')
        elif k == "rot":
            v = f'<i style="color:#{GRAY}">{e(b[2])}</i>' if b[2] == NC else corpo(b[2]) + (f' <span style="color:#{GRAY};font-size:8pt">({e(b[3])})</span>' if b[3] else "")
            h.append(f'<p style="margin-top:6pt;margin-bottom:0;color:#{NAVY};font-size:8pt"><b>{e(b[1])}</b></p><p style="text-align:justify;margin-top:1pt">{v}</p>')
        elif k == "tabela":
            cab, rows, pct, al = b[1], b[2], b[3], b[4]
            ali = lambda i: "right" if al[i] == "r" else "left"
            head = "".join(td(f'<b style="color:#ffffff;font-size:8.5pt">{e(c)}</b>', f"width:{p}%;background-color:#{NAVY};text-align:{ali(i)}") for i, (c, p) in enumerate(zip(cab, pct)))
            body = ""
            for j, row in enumerate(rows):
                tot = row[0] == "Total"
                fundo = f"background-color:#{LIGHT};" if (j % 2 == 1 and not tot) else ""
                body += "<tr>" + "".join(td(("<b>" + e(v) + "</b>") if tot else e(v), f"{fundo}font-size:9pt;text-align:{ali(i)};border-bottom:1px solid #{LINE}" + (f";border-top:1px solid #{NAVY}" if tot else "")) for i, v in enumerate(row)) + "</tr>"
            h.append(f'<table style="width:100%" cellspacing="0" cellpadding="4"><tr>{head}</tr>{body}</table><p style="font-size:4pt">&nbsp;</p>')
        elif k == "cartoes":
            n = len(b[1])
            cels = "".join(td(f'<span style="color:#{GRAY};font-size:7pt"><b>{e(r.upper())}</b></span><br><span style="color:#{NAVY};font-size:14pt"><b>{e(v)}</b></span><br><span style="color:#{GRAY};font-size:7.5pt">{e(nt)}</span>', f"width:{100 // n}%;background-color:#{LIGHT};border-top:3px solid #{ACCENT}") for r, v, nt in b[1])
            h.append(f'<table style="width:100%" cellspacing="4" cellpadding="6"><tr>{cels}</tr></table><p style="font-size:4pt">&nbsp;</p>')
        elif k == "barras":
            total, maior = sum(x[1] for x in b[2]), max(x[1] for x in b[2])
            h.append(f'<p style="color:#{NAVY};font-size:8pt;margin-bottom:2pt"><b>{e(b[1].upper())}</b></p><table style="width:100%" cellspacing="0" cellpadding="3">')
            for rot, v, vs, extra in b[2]:
                w = max(1.0, round(v / maior * 100, 1))
                barra = f'<table style="width:100%" cellspacing="0" cellpadding="0"><tr>{td("&nbsp;", f"width:{w}%;background-color:#{ACCENT};font-size:6pt")}{td("&nbsp;", f"background-color:#{LIGHT};font-size:6pt")}</tr></table>' if w < 100 else \
                        f'<table style="width:100%" cellspacing="0" cellpadding="0"><tr>{td("&nbsp;", f"background-color:#{ACCENT};font-size:6pt")}</tr></table>'
                h.append(f'<tr>{td(e(rot), f"width:30%;font-size:8.5pt;color:#{NAVY}")}{td(barra, "width:42%")}{td(f"<b style=\"color:#{NAVY}\">{e(vs)}</b><br><span style=\"color:#{GRAY};font-size:7.5pt\">{v / total * 100:.0f}%" + (f" · {e(extra)}" if extra else "") + "</span>", "width:28%;text-align:right;font-size:9pt")}</tr>')
            h.append('</table><p style="font-size:4pt">&nbsp;</p>')
        elif k == "cronologia":
            h.append('<table style="width:100%" cellspacing="0" cellpadding="4">')
            for data_, texto, origem in b[1]:
                v = corpo(texto + ("." if not texto.endswith((".", ")")) else "")) + (f' <span style="color:#{GRAY};font-size:8pt">({e(origem)})</span>' if origem and origem not in texto else "")
                h.append(f'<tr>{td(f"<b>{e(data_)}</b>", f"width:15%;color:#{ACCENT};border-bottom:1px solid #{LINE}")}{td(v, f"width:85%;border-bottom:1px solid #{LINE}")}</tr>')
            h.append('</table>')
        elif k == "nota":
            h.append(f'<p style="font-size:8pt;color:#{GRAY}"><i>{e(b[1])}</i></p>')
    h.append("</body>")
    story = pymupdf.Story(html="".join(h), em=10)
    buf = io.BytesIO()
    w = pymupdf.DocumentWriter(buf)
    folha = pymupdf.paper_rect("a4")
    mais = 1
    while mais:
        dev = w.begin_page(folha)
        mais, _ = story.place(folha + (54, 62, -54, -58))
        story.draw(dev)
        w.end_page()
    w.close()
    doc = pymupdf.open(stream=buf.getvalue())
    cinza = (0.36, 0.42, 0.49)
    for i, pg in enumerate(doc):  # cabeçalho discreto e rodapé com a página
        pg.insert_text((54, 36), f"Recuperação Judicial · Processo nº {numero}", fontsize=7.5, color=cinza)
        pg.draw_line((54, 41), (541, 41), color=(0.79, 0.83, 0.88), width=0.5)
        pg.insert_text((488, 822), f"página {i + 1} de {len(doc)}", fontsize=7.5, color=cinza)
    return doc.tobytes()
