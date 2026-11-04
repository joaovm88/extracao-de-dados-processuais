"""Extração estruturada por LLM, guiada pelos prompts versionados em prompts/*.md."""
from pathlib import Path
from typing import Any, Optional

from .config import Settings
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
    props["citacoes"] = {"type": "object", "description": "Para cada campo preenchido, a citação de onde veio (ex.: 'VOL 001, pg. 160').",
                         "additionalProperties": {"type": "string"}}
    return {"name": "registrar_extracao", "description": "Registra os dados extraídos do processo.",
            "input_schema": {"type": "object", "properties": props, "required": ["observacoes"]}}


def select_context(pages: list[dict], budget: int, store=None, doc_id: Optional[str] = None) -> str:
    """pages: [{'page': abs, 'label': 'VOL 001, pg. 160', 'text': ...}]. Processos pequenos vão inteiros;
    grandes usam recuperação híbrida por campo, mantendo a ordem cronológica."""
    total = sum(len(p["text"]) for p in pages)
    chosen = pages
    if total > budget and store is not None:
        by_page = {p["page"]: p for p in pages}
        keep: set[int] = set()
        for q in _FIELD_QUERIES.values():
            for h in store.search(q, doc_id, 12):
                keep.add(h.page)
        chosen = [by_page[n] for n in sorted(keep) if n in by_page]
        # preenche o orçamento com as últimas páginas (estado atual do processo)
        used = sum(len(p["text"]) for p in chosen)
        for p in reversed(pages):
            if used > budget:
                break
            if p["page"] not in keep:
                chosen.append(p)
                used += len(p["text"])
        chosen.sort(key=lambda p: p["page"])
    out, used = [], 0
    for p in chosen:
        if used + len(p["text"]) > budget:
            break
        out.append(f"=== [{p['label']}] ===\n{p['text']}")
        used += len(p["text"])
    return "\n\n".join(out)


def extract_with_llm(sheet: Sheet, prompt_name: str, context: str, settings: Settings, client=None) -> dict:
    """Chama o LLM com tool-use forçado (saída JSON validada pelo schema)."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    system = (
        load_prompt(prompt_name)
        + "\n\n---\nREGRAS DE SAÍDA ESTRUTURADA:\n"
        f"- Preencha a ferramenta registrar_extracao. 'observacoes' recebe o relatório no formato do prompt acima.\n"
        f"- Cada trecho do processo vem rotulado como [Evento N, VOL N, pg. N, ID N]; use EXATAMENTE esses rótulos nas citações.\n"
        f"- Campos de lista fechada só aceitam os valores permitidos; se não houver base nos autos, use null.\n"
        f"- Nunca invente. Sem base no texto, use null (ou '{NAO_ENCONTRADO}' dentro de 'observacoes')."
    )
    msg = client.messages.create(
        model=settings.llm_model, max_tokens=8000, system=system,
        tools=[tool_schema(sheet)], tool_choice={"type": "tool", "name": "registrar_extracao"},
        messages=[{"role": "user", "content": f"Processo para análise:\n\n{context}"}],
    )
    for b in msg.content:
        if getattr(b, "type", "") == "tool_use":
            return validate(sheet, dict(b.input))
    raise RuntimeError("O modelo não devolveu a extração estruturada.")


def validate(sheet: Sheet, data: dict) -> dict:
    """Garante listas fechadas: valor fora da lista vira null e o campo entra em revisão."""
    review = []
    for f in sheet.ai_fields:
        v = data.get(f.key)
        if f.options and v is not None and v not in f.options:
            review.append(f.key)
            data[f.key] = None
    data["_revisar"] = review
    data.setdefault("bens", [])
    data.setdefault("fraudes", [])
    data.setdefault("citacoes", {})
    return data


def get_sheet(key: str) -> Sheet:
    return SHEETS[key]
