"""Extração estruturada por LLM, guiada pelos prompts versionados em prompts/*.md."""
import re
from pathlib import Path
from typing import Any, Optional

from .schemas import NAO_ENCONTRADO, SHEETS, Sheet

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


def tool_schema(sheet: Sheet) -> dict:
    props: dict[str, Any] = {}
    for f in sheet.ai_fields:
        p: dict[str, Any] = {"type": ["string", "null"], "description": f.desc or f.header}
        if f.options:
            p["enum"] = [*f.options, None]
        props[f.key] = p
    props["bens"] = {"type": "array", "description": "Imóveis/veículos dados em garantia, penhorados ou avaliados nos autos. Um item por ato registral relevante (R./Av.).",
                     "items": {"type": "object", "properties": {k: {"type": ["string", "null"]} for k in (
                         "matricula", "caracteristicas", "proprietario", "ato", "gravames", "resumo", "situacao")}}}
    props["fraudes"] = {"type": "array", "description": "Somente quando o procurador alegou fraude E o juiz deferiu/analisou o pedido.",
                        "items": {"type": "object", "properties": {k: {"type": ["string", "null"]} for k in (
                            "tipo", "analise", "valor_causa")}}}
    props["citacoes"] = {"type": "array", "description": "Para cada campo preenchido, a citação de onde veio (ex.: 'Evento 14, pg. 1, ID 6000006508').",
                         "items": {"type": "object", "properties": {"campo": {"type": "string"}, "citacao": {"type": "string"}},
                                   "required": ["campo", "citacao"]}}
    return {"type": "object", "properties": props, "required": ["observacoes"]}


def select_context(pages: list[dict], budget: int, store=None, doc_ids: Optional[list[str]] = None) -> str:
    """pages: [{'doc_id', 'page', 'label', 'text'}] na ordem dos autos (todas as partes juntas).
    Processos pequenos vão inteiros; grandes usam recuperação híbrida por campo + as páginas finais
    (estado atual), sempre em ordem cronológica."""
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
    return "\n\n".join(out)


def citation_rule(sistema) -> str:
    if sistema == "eproc":
        return ("- SISTEMA: eproc. Cite os atos SOMENTE como 'Evento N, pg. P' (ou 'Evento N'). NUNCA use 'ID' nem 'VOL' neste processo, "
                "mesmo que o formato do prompt os mencione.\n")
    if sistema == "pje":
        return ("- SISTEMA: PJe. Cite os atos SOMENTE como 'ID N, pg. P', onde N é o ID do documento e P a página DENTRO desse documento. "
                "NUNCA use 'Evento' nem 'VOL' neste processo.\n")
    return "- Use apenas rótulos que existam nos trechos; omita as partes do formato (VOL/ID/Evento) que não existirem.\n"


def extract_with_llm(sheet: Sheet, prompt_name: str, context: str, llm, sistema=None) -> dict:
    """Chama o LLM (Gemini ou Anthropic) com saída JSON restrita ao schema da aba."""
    system = (
        load_prompt(prompt_name)
        + "\n\n---\nREGRAS DE SAÍDA ESTRUTURADA:\n"
        "- Devolva os campos pedidos no schema. 'observacoes' recebe o relatório no formato do prompt acima.\n"
        "- Cada trecho do processo vem com um rótulo entre colchetes; copie EXATAMENTE esses rótulos nas citações. NUNCA invente nº de evento, ID ou página.\n"
        + citation_rule(sistema) +
        "- Campos de lista fechada só aceitam os valores permitidos; se não houver base nos autos, use null.\n"
        f"- Nunca invente. Sem base no texto, use null (ou '{NAO_ENCONTRADO}' dentro de 'observacoes')."
    )
    return validate(sheet, llm.extract(system, f"Processo para análise:\n\n{context}", tool_schema(sheet)))


def validate(sheet: Sheet, data: dict) -> dict:
    """Garante listas fechadas: valor fora da lista vira null e o campo entra em revisão."""
    review = []
    for f in sheet.ai_fields:
        v = data.get(f.key)
        if f.options and v is not None and v not in f.options:
            review.append(f.key)
            data[f.key] = None
    data["_revisar"] = review
    data["bens"] = data.get("bens") or []
    data["fraudes"] = data.get("fraudes") or []
    cit = data.get("citacoes") or []
    data["citacoes"] = ({c["campo"]: c["citacao"] for c in cit if isinstance(c, dict) and "campo" in c and "citacao" in c}
                        if isinstance(cit, list) else dict(cit))
    return data


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
