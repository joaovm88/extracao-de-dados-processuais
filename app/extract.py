"""Extração estruturada por LLM, guiada pelos prompts versionados em prompts/*.md."""
import re
from pathlib import Path
from typing import Any, Optional

from .ingest import eventos as ev
from .ingest.entities import valid_cnpj, valid_cpf
from .schemas import FRAUDE_ANALISES, FRAUDE_TIPOS, fmt_moeda_br, normalize_fields, NAO_ENCONTRADO, SHEETS, Sheet

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_FIELD_QUERIES = {
    "garantia": "penhora bloqueio sisbajud bacenjud imóvel matrícula depósito garantia fiança seguro",
    "valor_garantia": "valor da avaliação laudo de avaliação reavaliação depósito bloqueio R$",
    "tent_alien": "hasta pública leilão alienação judicial arrematação leiloeiro",
    "excecao_pendente": "exceção de pré-executividade",
    "arquivada_40": "art. 40 lei 6.830 arquivamento suspensão prescrição intercorrente",
    "redirecionamento": "redirecionamento responsabilidade solidária sócio corresponsável grupo econômico",
    "transito": "trânsito em julgado certidão",
    "honorarios": "honorários advocatícios sucumbência condenação",
    "observacoes": "sentença decisão despacho acórdão penhora bloqueio recurso apelação agravo",
    "bens": "matrícula registro de imóveis ônus hipoteca penhora avaliação veículo placa renavam",
    "fraudes": "fraude à execução fraude contra credores simulação grupo econômico decisão deferiu",
}


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def load_orientacoes() -> str:
    """Orientações próprias do analista/PGFN (prompts/orientacoes_pgfn.md), acrescentadas a todos os prompts de extração.
    Comentários <!-- ... --> não são enviados. Arquivo vazio ou ausente: nada é acrescentado."""
    p = PROMPTS_DIR / "orientacoes_pgfn.md"
    if not p.is_file():
        return ""
    return re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8"), flags=re.DOTALL).strip()


def tool_schema(sheet: Sheet) -> dict:
    props: dict[str, Any] = {}
    for f in sheet.ai_fields:
        p: dict[str, Any] = {"type": ["string", "null"], "description": f.desc or f.header}
        if f.options:
            p["enum"] = [*f.options, None]
        props[f.key] = p
    props["bens"] = {"type": "array", "description": (
        "SOMENTE imóveis (matrículas) para os quais os autos trazem o AUTO/TERMO DE PENHORA e/ou o REGISTRO da penhora na matrícula. "
        "Um item por ato registral da matrícula (R./Av.), repetindo 'matricula' em todos os itens do mesmo imóvel. Sem auto de penhora nem registro, devolva lista vazia."),
                     "items": {"type": "object", "properties": {
                         "matricula": {"type": ["string", "null"], "description": "Nº da matrícula e cartório, ex.: '28.749 (Registro de Imóveis de Formiga - MG)'"},
                         "caracteristicas": {"type": ["string", "null"], "description": "Descrição do imóvel (área, local)"},
                         "proprietario": {"type": ["string", "null"], "description": "Nome e CNPJ/CPF"},
                         "ato": {"type": ["string", "null"], "description": "Ex.: 'R-14', 'Av-07'"},
                         "gravames": {"type": ["string", "null"], "description": "Ex.: 'Penhora', 'Hipoteca de 1º Grau', 'Compra e Venda'"},
                         "resumo": {"type": ["string", "null"], "description": "Credor/processo, juízo e valor do ato"},
                         "situacao": {"type": ["string", "null"], "description": "'Ativo' ou 'Cancelado (Av. X)'"},
                         "citacao": {"type": ["string", "null"], "description": "Rótulo do trecho onde está o auto de penhora ou o registro"}}}}
    props["fraudes"] = {"type": "array", "description": (
        "Alegações de fraude feitas pela Fazenda/procurador nos autos (fraude à execução, simulação, fraude patrimonial, sucessão de fato, "
        "desconsideração por fraude), com o resultado da análise do juiz. Sem alegação de fraude, devolva lista vazia."),
                        "items": {"type": "object", "properties": {
                            "tipo": {"type": ["string", "null"], "enum": [*FRAUDE_TIPOS, None]},
                            "analise": {"type": ["string", "null"], "enum": [*FRAUDE_ANALISES, None], "description": "Resultado do pedido"},
                            "valor_causa": {"type": ["string", "null"], "description": "Valor da causa (R$), se constar"},
                            "resumo": {"type": ["string", "null"], "description": "Uma frase: quem alegou o quê e o que o juiz decidiu"},
                            "citacao": {"type": ["string", "null"], "description": "Rótulo do trecho da alegação ou da decisão"}}}}
    props["citacoes"] = {"type": "array", "description": "Para cada campo preenchido, a citação de onde veio (ex.: 'Evento 14, pg. 1, ID 6000006508').",
                         "items": {"type": "object", "properties": {"campo": {"type": "string"}, "citacao": {"type": "string"}},
                                   "required": ["campo", "citacao"]}}
    return {"type": "object", "properties": props, "required": ["observacoes"]}


def select_context_info(pages: list[dict], budget: int, store=None, doc_ids: Optional[list[str]] = None) -> tuple[str, dict]:
    """pages: [{'doc_id', 'page', 'label', 'text'}] na ordem dos autos (todas as partes juntas).
    Processos pequenos vão inteiros; grandes usam recuperação híbrida por campo + as páginas finais
    (estado atual), sempre em ordem cronológica. Devolve (texto, medidas do corte)."""
    total = sum(len(p["text"]) for p in pages)
    chosen = pages
    if total > budget and store is not None:
        index = {(p["doc_id"], p["page"]): i for i, p in enumerate(pages)}
        keep: set[int] = set()
        for q in _FIELD_QUERIES.values():
            for h in store.search(q, doc_ids, 12):
                if (h.doc_id, h.page) in index:
                    keep.add(index[(h.doc_id, h.page)])
        used = sum(len(pages[i]["text"]) for i in keep)
        for i in range(len(pages) - 1, -1, -1):  # preenche o orçamento com as últimas páginas
            if used > budget:
                break
            if i not in keep:
                keep.add(i)
                used += len(pages[i]["text"])
        chosen = [pages[i] for i in sorted(keep)]
    out, used = [], 0
    for p in chosen:
        if used + len(p["text"]) > budget:
            break
        out.append(f"=== [{p['label']}] ===\n{p['text']}")
        used += len(p["text"])
    info = {"paginas_total": len(pages), "paginas_enviadas": len(out), "paginas_omitidas": len(pages) - len(out),
            "caracteres_total": total, "caracteres_enviados": used, "orcamento": budget, "cortado": len(out) < len(pages)}
    return "\n\n".join(out), info


def select_context(pages: list[dict], budget: int, store=None, doc_ids: Optional[list[str]] = None) -> str:
    return select_context_info(pages, budget, store, doc_ids)[0]


def select_context_eventos(pages: list[dict], eventos: list[dict], budget: int) -> tuple[str, dict]:
    """Contexto POR EVENTOS (eproc): índice completo de todos os eventos + texto integral dos atos relevantes
    (alta, depois média relevância) até o orçamento. Mero expediente entra só no índice, de propósito."""
    por_pg = {(p["doc_id"], p["page"]): p for p in pages}
    ate_indice = len(ev.indice(eventos, set()))
    orcamento_texto = max(budget - ate_indice - 6000, budget // 3)
    com_texto: dict[int, str] = {}
    usado, omitidos_altos = 0, 0
    for nivel in ("alta", "media"):
        for e in [x for x in eventos if ev.relevancia(x) == nivel]:
            blocos = []
            for doc_id, pg in e["paginas"]:
                p = por_pg.get((doc_id, pg))
                if p and not str(p.get("refs", {}).get("tipo_doc", "")).upper().startswith(ev._ANEXOS):  # anexos (estatuto, procuração…) ficam de fora
                    blocos.append(f"=== [{p['label']}] ===\n{p['text']}")
            texto = "\n\n".join(blocos)[:9000]
            if not texto:
                continue
            if usado + len(texto) > orcamento_texto:
                omitidos_altos += nivel == "alta"
                continue
            com_texto[e["evento"]] = texto
            usado += len(texto)
    capa = [f"=== [{p['label']}] ===\n{p['text']}" for p in pages if not p.get("refs", {}).get("evento")][:2]  # capa do processo
    ordem = sorted(com_texto, key=lambda n: next((e["data"] or "9999", n) for e in eventos if e["evento"] == n))
    ctx = ("ÍNDICE COMPLETO DE EVENTOS (fonte: páginas de separação do eproc; 'texto integral: sim' = o ato vem transcrito abaixo)\n"
           + ev.indice(eventos, set(com_texto)) + ("\n\nCAPA DO PROCESSO\n" + "\n\n".join(capa) if capa else "")
           + "\n\nTEXTO INTEGRAL DOS ATOS\n" + "\n\n".join(com_texto[n] for n in ordem))
    enviadas = sum(1 for e in eventos if e["evento"] in com_texto for _ in e["paginas"])
    info = {"modo": "eventos", "eventos_total": len(eventos), "eventos_com_texto": len(com_texto),
            "eventos_relevantes": sum(1 for e in eventos if ev.relevancia(e) != "baixa"), "paginas_total": len(pages),
            "paginas_enviadas": enviadas + len(capa), "paginas_omitidas": len(pages) - enviadas - len(capa),
            "caracteres_total": sum(len(p["text"]) for p in pages), "caracteres_enviados": len(ctx), "orcamento": budget,
            "cortado": omitidos_altos > 0, "atos_relevantes_omitidos": omitidos_altos}
    return ctx, info


def select_context_pje(pages: list[dict], docs: list[dict], budget: int) -> tuple[str, dict]:
    """Contexto do PJe: índice completo de documentos (ID, data, nome) + texto integral dos relevantes. Autos digitalizados
    ('Volume') ocupam o que sobrar do orçamento: início e, principalmente, as páginas mais recentes."""
    from .ingest import pje
    por_pg = {(p["doc_id"], p["page"]): p for p in pages}
    ate_indice = len(pje.indice(docs, set()))
    orcamento = max(budget - ate_indice - 6000, budget // 3)
    com_texto: dict[str, str] = {}
    usado, omitidos = 0, 0
    volumes = [d for d in docs if pje.e_volume(d) and len(d["paginas"]) > 3]
    for nivel in ("alta", "media"):
        for d in [x for x in docs if pje.relevancia(x) == nivel and x not in volumes]:
            texto = "\n\n".join(f"=== [{p['label']}] ===\n{p['text']}" for dp in d["paginas"] if (p := por_pg.get(tuple(dp))))[:9000]
            if not texto:
                continue
            if usado + len(texto) > orcamento:
                omitidos += nivel == "alta"
                continue
            com_texto[d["id"]] = texto
            usado += len(texto)
    paginas_vol = 0
    for d in volumes:
        pgs = [p for dp in d["paginas"] if (p := por_pg.get(tuple(dp))) and len(p["text"].strip()) >= 80]
        sobra = max(orcamento - usado, 0)
        escolhidas = set(range(min(3, len(pgs))))
        gasto = sum(len(pgs[i]["text"]) for i in escolhidas)
        for i in range(len(pgs) - 1, -1, -1):  # preenche com as páginas mais recentes
            if gasto + len(pgs[i]["text"]) > sobra:
                break
            if i not in escolhidas:
                escolhidas.add(i); gasto += len(pgs[i]["text"])
        if len(escolhidas) < len(pgs):
            omitidos += 1
        com_texto[d["id"]] = "\n\n".join(f"=== [{pgs[i]['label']}] ===\n{pgs[i]['text']}" for i in sorted(escolhidas))
        usado += gasto
        paginas_vol += len(escolhidas)
    capa = []
    for p in pages:  # capa: páginas antes do primeiro rodapé de documento
        if p.get("refs", {}).get("pje"):
            break
        capa.append(f"=== [{p['label']}] ===\n{p['text']}")
    capa = capa[:2]
    ordem = [d["id"] for d in docs if d["id"] in com_texto]
    ctx = ("ÍNDICE COMPLETO DE DOCUMENTOS (fonte: capa do PJe; 'texto integral: sim' = o documento vem transcrito abaixo)\n"
           + pje.indice(docs, set(com_texto)) + ("\n\nCAPA DO PROCESSO\n" + "\n\n".join(capa) if capa else "")
           + "\n\nTEXTO DOS DOCUMENTOS\n" + "\n\n".join(com_texto[i] for i in ordem))
    enviadas = paginas_vol + sum(len(d["paginas"]) for d in docs if d["id"] in com_texto and d not in volumes)
    info = {"modo": "eventos", "sistema": "pje", "eventos_total": len(docs), "eventos_com_texto": len(com_texto),
            "eventos_relevantes": sum(1 for d in docs if pje.relevancia(d) != "baixa"), "paginas_total": len(pages),
            "paginas_enviadas": enviadas + len(capa), "paginas_omitidas": max(len(pages) - enviadas - len(capa), 0),
            "caracteres_total": sum(len(p["text"]) for p in pages), "caracteres_enviados": len(ctx), "orcamento": budget,
            "cortado": omitidos > 0, "atos_relevantes_omitidos": omitidos}
    return ctx, info


def citation_rule(sistema) -> str:
    if sistema == "eproc":
        return ("- SISTEMA: eproc. Cite os atos SOMENTE como 'Evento N, pg. P' (ou 'Evento N'). NUNCA use 'ID' nem 'VOL' neste processo, "
                "mesmo que o formato do prompt os mencione.\n")
    if sistema == "pje":
        return ("- SISTEMA: PJe. Cite os atos SOMENTE como 'ID N, pg. P', onde N é o ID do documento e P a página DENTRO desse documento. "
                "NUNCA use 'Evento' nem 'VOL' neste processo.\n")
    return "- Use apenas rótulos que existam nos trechos; omita as partes do formato (VOL/ID/Evento) que não existirem.\n"


def extract_with_llm(sheet: Sheet, prompt_name: str, context: str, llm, sistema=None, com_indice: bool = False) -> dict:
    """Chama o LLM (Gemini ou Anthropic) com saída JSON restrita ao schema da aba."""
    import datetime

    orient = load_orientacoes()
    system = (
        load_prompt(prompt_name)
        + (f"\n\n---\nORIENTAÇÕES DA PGFN (procedimentos e critérios de análise; aplique-os ao interpretar os autos):\n{orient}" if orient else "")
        + "\n\n---\nREGRAS DE SAÍDA ESTRUTURADA:\n"
        f"- Data de hoje: {datetime.date.today():%d/%m/%Y}. Datas SEMPRE no formato dd/mm/aaaa. Valores SEMPRE em reais, no formato 'R$ 1.234,56'.\n"
        "- Devolva os campos pedidos no schema. 'observacoes' recebe o relatório no formato do prompt acima.\n"
        "- Cada trecho do processo vem com um rótulo entre colchetes; copie EXATAMENTE esses rótulos nas citações. NUNCA invente nº de evento, ID ou página.\n"
        + citation_rule(sistema) +
        "- Campos de lista fechada só aceitam os valores permitidos; se não houver base nos autos, use null.\n"
        f"- Nunca invente. Sem base no texto, use null (ou '{NAO_ENCONTRADO}' dentro de 'observacoes')."
        + ("\n- O contexto começa com um ÍNDICE COMPLETO DE DOCUMENTOS, tirado da capa do PJe: é a fonte AUTORITATIVA do ID, da data de assinatura e do nome de cada documento. "
           "Só os documentos marcados 'texto integral: sim' vêm transcritos; para os demais NÃO presuma o conteúdo: descreva só o que o índice informa ou omita. "
           "Documentos do tipo 'Volume' são os autos físicos digitalizados: dentro deles, cada ato (despacho, mandado, certidão…) tem a data que consta no próprio texto. "
           "Na cronologia, escreva cada ato como 'ID N, pg. P - dd/mm/aaaa - descrição', onde N é o ID do documento e P a página dentro dele (rótulo do trecho)."
           if com_indice and sistema == "pje" else "")
        + ("\n- O contexto começa com um ÍNDICE COMPLETO DE EVENTOS, que vem do próprio sistema (eproc) e é a fonte AUTORITATIVA do número, da data e do tipo de cada evento. "
           "Só os atos marcados 'texto integral: sim' vêm transcritos; para os demais NÃO presuma o conteúdo: descreva apenas o que o índice informa ou omita o ato. "
           "Na cronologia, cite cada ato como 'Evento N, pg. P' e use a data do índice." if com_indice and sistema != "pje" else "")
    )
    return validate(sheet, llm.extract(system, f"Processo para análise:\n\n{context}", tool_schema(sheet)))


def validate(sheet: Sheet, data: dict) -> dict:
    """Garante listas fechadas: valor fora da lista vira null e o campo entra em revisão."""
    review = []
    antes = dict(data)
    data.update(normalize_fields(sheet, {f.key: data.get(f.key) for f in sheet.ai_fields}, estrito=True))  # dd/mm/aaaa, R$, art. 40
    review += [f.key for f in sheet.ai_fields if antes.get(f.key) not in (None, "") and data.get(f.key) is None]
    for f in sheet.ai_fields:
        v = data.get(f.key)
        if f.options and v is not None and v not in f.options:
            review.append(f.key)
            data[f.key] = None
    if "corresponsaveis" in data:
        data["corresponsaveis"] = format_corresponsaveis(data.get("corresponsaveis"))
    data["_revisar"] = review
    data["bens"] = [{**b, "_st": "sugerido"} for b in (data.get("bens") or []) if isinstance(b, dict) and any(b.values())]
    data["fraudes"] = [{**normalize_fraude(f), "_st": "sugerido"} for f in (data.get("fraudes") or []) if isinstance(f, dict) and any(f.values())]
    cit = data.get("citacoes") or []
    data["citacoes"] = ({c["campo"]: c["citacao"] for c in cit if isinstance(c, dict) and "campo" in c and "citacao" in c}
                        if isinstance(cit, list) else dict(cit))
    return data


def _sem_acento(t) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(t or "")) if unicodedata.category(c) != "Mn").upper().strip()


def normalize_fraude(item: dict) -> dict:
    """Encaixa 'tipo' e 'analise' nas listas fechadas da planilha (aceita variação de acento/caixa). Tipo desconhecido vira
    'OUTRAS FRAUDES' (o texto original vai para o resumo); análise desconhecida fica vazia para o analista escolher."""
    out = dict(item)
    out["valor_causa"] = fmt_moeda_br(out.get("valor_causa"))
    tipos = {_sem_acento(t): t for t in FRAUDE_TIPOS}
    t = _sem_acento(out.get("tipo"))
    if t and t not in tipos:
        achado = next((v for k, v in tipos.items() if k in t or t in k), None)
        if not achado:
            out["resumo"] = f"{out.get('tipo')}. {out.get('resumo') or ''}".strip()
        out["tipo"] = achado or "OUTRAS FRAUDES"
    elif t:
        out["tipo"] = tipos[t]
    analises = {_sem_acento(a): a for a in FRAUDE_ANALISES}
    a = _sem_acento(out.get("analise"))
    if a:
        out["analise"] = analises.get(a) or next((v for k, v in analises.items() if a.startswith(k[:6]) and "PARCIAL" not in a), None) \
            or ("PARCIALMENTE DEFERIDO" if "PARCIAL" in a else None)
    return out


def get_sheet(key: str) -> Sheet:
    return SHEETS[key]


_PAIR = re.compile(r"\b(Evento|ID)[\s.:]*(\d+)\s*,\s*(?:VOL\s*\d+\s*,\s*)?p[áa]?g\.?\s*(\d+)", re.IGNORECASE)
_CIT = re.compile(r"\b(Evento|ID|pg\.?|p[áa]g(?:ina)?\.?)[\s.:]*(\d+(?:\s*[/\-]\s*\d+)*)", re.IGNORECASE)


def check_citations(texts: list[str], pages: list[dict], sistema) -> list[str]:
    """Confere as citações do texto gerado com o que existe no PDF. Devolve as suspeitas
    (Evento/ID/página inexistentes, ou mistura de sistema: 'ID' no eproc, 'Evento' no PJe)."""
    eventos = {str(p["refs"]["evento"]) for p in pages if p["refs"].get("evento")}
    ids = {str(p["refs"]["id"]) for p in pages if p["refs"].get("id")}
    pgs = {str(p["page"]) for p in pages} | {str(p["refs"]["pg"]) for p in pages if p["refs"].get("pg")}
    pairs: dict[tuple[str, str], set] = {}
    for p in pages:
        r = p["refs"]
        for kind, key in (("evento", "evento"), ("id", "id")):
            if r.get(key):
                pairs.setdefault((kind, str(r[key])), set()).add(str(r.get("pg") or p["page"]))
    bad: list[str] = []
    for t in texts:
        for m in _PAIR.finditer(t or ""):  # "Evento 14, pg. 7": a página tem de existir DENTRO desse ato
            kind, n, pg = m.group(1).lower(), m.group(2).lstrip("0") or "0", m.group(3).lstrip("0") or "0"
            known = pairs.get((kind, n))
            if known is not None and pg not in known and not (sistema == "eproc" and kind == "id"):
                bad.append(f"{m.group(1)} {m.group(2)}, pg. {m.group(3)}")
        t = _PAIR.sub(lambda m: f"{m.group(1)} {m.group(2)}", t or "")  # a página do par já foi conferida acima
        for m in _CIT.finditer(t):
            kind = m.group(1).lower()
            nums = [n.lstrip("0") or "0" for n in re.findall(r"\d+", m.group(2).split("-")[0].replace("/", " "))]
            if kind == "evento":
                ok = sistema != "pje" and all(n in eventos for n in nums[:1])
            elif kind == "id":
                ok = sistema != "eproc" and all(n in ids or any(i.endswith(n) for i in ids) for n in nums[:1])
            else:
                ok = all(n in pgs for n in nums)
            if not ok:
                bad.append(f"{m.group(1)} {m.group(2).strip()}")
    return sorted(set(bad))


_DOC = re.compile(r"(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}|\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)")
_BULLET = re.compile(r"^\s*(?:[-•*·–]+|\d{1,2}[.)])\s+")


def _fmt_doc(d: str) -> tuple[str, str, bool]:
    """(tipo, documento com máscara, dígitos válidos?) a partir de 11 (CPF) ou 14 (CNPJ) dígitos."""
    n = re.sub(r"\D", "", d)
    if len(n) == 14:
        m = f"{n[:2]}.{n[2:5]}.{n[5:8]}/{n[8:12]}-{n[12:]}"
        return "CNPJ", m, valid_cnpj(m)
    m = f"{n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}"
    return "CPF", m, valid_cpf(m)


_BULLET_LISTA = re.compile(r"^\s*(?:[-•*·–—]+|\d{1,3}[.)])\s+")
_ATO = re.compile(
    r"(?:Evento\s+\d+|ID\s+\d{6,}|VOL\s+\d+)(?:\s*,\s*(?:VOL\s+\d+|pg\.\s*\d+|ID\s+\d+|Evento\s+\d+))*"
    r"\s*[-–—]\s*(?:\d{2}/\d{2}/\d{4}|data não informada)\s*[-–—]\s*", re.IGNORECASE)


def format_cronologia(value) -> Optional[str]:
    """Deixa a cronologia como lista: um ato por linha, '<origem> - dd/mm/aaaa - descrição'. Separa atos colados na mesma
    linha, tira marcadores/numeração e traços diferentes, e junta continuações de frase. Texto que não é lista (ex.: 'Não
    informado no extrato', resposta do assistente) fica como está."""
    if value is None:
        return None
    texto = str(value).replace("\r", "")
    if not _ATO.search(texto):
        return texto.strip() or None
    atual: list[str] = []
    for linha in texto.split("\n"):
        linha = _BULLET_LISTA.sub("", linha).strip()
        if not linha:
            continue
        marcas = [m.start() for m in _ATO.finditer(linha)]
        if not marcas:
            if atual and not linha.startswith("["):  # continuação do ato anterior (frase quebrada em duas linhas)
                atual[-1] += " " + linha
            else:
                atual.append(linha)
            continue
        if marcas[0] > 0:  # texto antes do primeiro ato (ex.: um título)
            atual.append(linha[:marcas[0]].strip())
        for ini, fim in zip(marcas, marcas[1:] + [len(linha)]):
            atual.append(linha[ini:fim].strip())
    out = []
    for l in atual:
        l = re.sub(r"\s*[–—]\s*", " - ", l) if _ATO.match(l) else l
        out.append(re.sub(r"\s+", " ", l).strip().rstrip(";"))
    return "\n\n".join(x for x in out if x)  # um ato por linha, com uma linha em branco entre eles (leitura mais fácil)


_ORIGEM = re.compile(r"(?:Evento\s+\d+|ID\s+\d{6,}|VOL\s+\d+)(?:\s*,\s*(?:VOL\s+\d+|pg\.\s*\d+|ID\s+\d+))*", re.IGNORECASE)
_PGS = re.compile(r"\bp[áa]?gs?\.?\s*(\d+)(?:\s+(?:a|e)\s+\d+)*", re.IGNORECASE)  # 'pgs. 1 a 5' -> 'pg. 1'
ATOS_SYSTEM = (
    "Você converte a resposta de um assistente jurídico em linhas de cronologia processual. Devolva SOMENTE as linhas, uma por ato ou "
    "fato citado, no formato exato:\n<ORIGEM> - <dd/mm/aaaa ou 'data não informada'> - <descrição>\n"
    "- <ORIGEM> é a citação que acompanha o fato na resposta, copiada como está (ex.: 'Evento 68, pg. 3', 'ID 3160597, pg. 2', 'VOL 001, pg. 12'). "
    "Se a citação for um intervalo de páginas, use a primeira página.\n"
    "- A data só entra se constar da resposta; senão escreva 'data não informada'. Nunca invente data, evento, ID, página ou valor.\n"
    "- A descrição é uma frase curta e completa, que se entende sozinha: traga o contexto da pergunta (ex.: 'Bloqueio via Sisbajud de "
    "R$ 1.125,61 em nome de Fulano'). Preserve valores (R$) e nomes literalmente.\n"
    "- Fato sem citação de origem: omita. Vários fatos com a mesma origem: uma linha para cada um.\n"
    "- Sem markdown, sem marcadores, sem título, sem comentários. Se a resposta disser que a informação não foi encontrada, devolva vazio."
)


def _limpa_md(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`#]+", "", t)).strip()


def _atos_por_regras(resposta: str) -> list[str]:
    """Sem IA: cada item da resposta que traz uma citação vira uma linha '<origem> - data não informada - <texto>'."""
    out = []
    for item in re.split(r"\n+", resposta or ""):
        item = _PGS.sub(lambda m: f"pg. {m.group(1)}", _limpa_md(_BULLET_LISTA.sub("", item)))
        m = _ORIGEM.search(item)
        if not m:
            continue
        texto = re.sub(r"\s*\((?:[^()]*?)" + re.escape(m.group(0)) + r"[^()]*\)", "", item)  # tira o '(Evento 68, pg. 3)'
        texto = _ORIGEM.sub("", texto) if texto == item else texto
        texto = re.sub(r"\(\s*\)", "", texto).strip(" ;,.-–—:") or "ato citado pelo assistente"
        out.append(f"{re.sub(r'\s+', ' ', m.group(0))} - data não informada - {texto[0].upper() + texto[1:]}.")
    return out


def atos_da_resposta(pergunta: str, resposta: str, llm=None) -> tuple[list[str], str]:
    """Resposta do chat -> linhas de cronologia ('origem - data - ato'). Usa a IA para redigir; se ela não estiver disponível,
    falhar ou não devolver nenhuma linha válida, cai para a conversão por regras. Devolve (linhas, 'ia' | 'regras')."""
    if llm is not None:
        try:
            txt = llm.generate(ATOS_SYSTEM, f"PERGUNTA DO ANALISTA:\n{pergunta}\n\nRESPOSTA DO ASSISTENTE:\n{resposta}", max_tokens=1500)
            linhas = []
            for l in (txt or "").splitlines():
                l = _PGS.sub(lambda m: f"pg. {m.group(1)}", _limpa_md(_BULLET_LISTA.sub("", l)))
                l = re.sub(r"\s*[–—]\s*", " - ", l)
                if _ATO.match(l) and _ORIGEM.match(l) and _ORIGEM.match(l).group(0).lower() in _PGS.sub(lambda m: f"pg. {m.group(1)}", resposta).lower():
                    linhas.append(l)  # só aceita origem que realmente está na resposta (a IA não cria citação)
            if linhas:
                return linhas, "ia"
        except Exception:  # noqa: BLE001  limite de uso, serviço fora: a conversão por regras resolve
            pass
    return _atos_por_regras(resposta), "regras"


def format_corresponsaveis(value) -> Optional[str]:
    """Padroniza a lista de corresponsáveis: uma pessoa/empresa por linha, 'NOME - CPF: …' ou 'NOME - CNPJ: …'.
    Aceita texto corrido, linhas com marcadores/numeração, ';' como separador, documento só com números ou lista JSON.
    Documento com dígito verificador inválido (comum em erro de OCR) recebe '(conferir dígitos)'.
    Sem nenhum documento no texto (ex.: 'Não há'), devolve o texto original."""
    if value is None:
        return None
    itens = [str(x) for x in value] if isinstance(value, (list, tuple)) else re.split(r"[\n;]+", str(value))
    itens = [i.strip() for i in itens if i and i.strip()]
    if not itens:
        return None
    if not any(_DOC.search(i) for i in itens):
        return "\n".join(itens)
    # uma linha com vários documentos ("A (CPF x) e B - CNPJ y") vira um trecho por documento
    trechos = []
    for it in itens:
        txt = _BULLET.sub("", it)
        ms = list(_DOC.finditer(txt))
        if len(ms) <= 1:
            trechos.append(txt)
            continue
        ini = 0
        for k, mm in enumerate(ms):
            fecha = re.match(r"\s*\)", txt[mm.end():])  # o ')' que fecha "(CPF x)" fica com este trecho
            fim = len(txt) if k == len(ms) - 1 else mm.end() + (fecha.end() if fecha else 0)
            trechos.append(txt[ini:fim])
            ini = fim
    linhas, vistos = [], set()
    for txt in trechos:
        m = _DOC.search(txt)
        if not m:
            nome = re.sub(r"\s+", " ", txt).strip(" -–:,;()")
            linhas.append(f"{nome} - documento não informado") if nome else None
            continue
        tipo, doc, ok = _fmt_doc(m.group(1))
        chave = re.sub(r"\D", "", doc)
        if chave in vistos:
            continue
        vistos.add(chave)
        nome = txt[:m.start()] + " " + txt[m.end():]
        nome = re.sub(r"(?i)\b(cpf|cnpj|c\.?p\.?f|c\.?n\.?p\.?j)(?:\s*/\s*(?:mf|mef|me))?\b\.?\s*[:/]?|\b(inscrit[oa]|sob o n[ºo°]?|n[ºo°])\b\.?", " ", nome)
        nome = re.sub(r"\(\s*\)", " ", nome)  # parênteses que ficaram vazios
        nome = re.sub(r"(?i)^\s*(?:e|and)\s+", "", re.sub(r"\s+", " ", nome).strip(" -–:,;()/"))
        nome = nome.strip(" -–:,;()/")
        linhas.append(f"{nome or '(nome não informado)'} - {tipo}: {doc}{'' if ok else ' (conferir dígitos)'}")
    return "\n".join(linhas)
