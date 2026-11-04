"""Motor RAG: recupera trechos (híbrido) de todas as partes do processo e responde citando a origem."""
from typing import Optional

from .config import Settings
from .ingest.citation import label
from .ingest.entities import norm
from .llm import LLMProvider, get_llm
from .search.base import Hit, SearchStore

SYSTEM = (
    "Você é um assistente de análise de processos judiciais. Responda SOMENTE com base nos trechos fornecidos. "
    "Cite sempre a origem usando EXATAMENTE o rótulo do trecho, ex.: (Evento 14, pg. 1), (ID 3160597, pg. 2) ou "
    "(VOL 001, pg. 12). Nunca invente evento, ID ou página. Se a informação não estiver nos trechos, diga claramente "
    "que não foi encontrada no documento. Não invente valores, nomes ou números. Responda em português."
)
_VALUE_WORDS = ("valor", "bloque", "sisbajud", "darf", "custas", "honorario", "quanto")
_CATS = {"sisbajud": "Bloqueio SISBAJUD", "bloque": "Bloqueio SISBAJUD", "darf": "DARF / Tributo",
         "custas": "Custas", "honorario": "Honorários"}


def _lab(docs: dict, doc_id: str, page: int) -> str:
    d = docs.get(doc_id) or {}
    rot = (d.get("metadata") or {}).get("rotulos") or {}
    return rot.get(str(page)) or label(d.get("volume"), page)


def _structured_hint(question: str, docs: dict) -> str:
    """Para perguntas sobre valores, injeta os valores já extraídos (regex) como contexto adicional."""
    q = norm(question)
    if not any(w in q for w in _VALUE_WORDS):
        return ""
    wanted = {c for k, c in _CATS.items() if k in q}
    lines = []
    for did, d in docs.items():
        for v in ((d.get("metadata") or {}).get("valores") or []):
            if not wanted or v["categoria"] in wanted:
                lines.append(f"- {v['valor_formatado']} ({v['categoria']}, {_lab(docs, did, v['pagina'])}): {v['contexto'][:160]}")
    return ("Valores extraídos automaticamente:\n" + "\n".join(lines[:15])) if lines else ""


def _extractive_answer(question: str, hits: list[Hit], docs: dict) -> str:
    if not hits:
        return "Não encontrei essa informação no(s) documento(s) analisado(s)."
    parts = []
    if h := _structured_hint(question, docs):
        parts.append(h)
    parts.append("Trechos mais relevantes:")
    for x in hits[:3]:
        parts.append(f"({_lab(docs, x.doc_id, x.page)}) {' '.join(x.text.split())[:350]}")
    return "\n".join(parts)


def answer(question: str, docs: list[dict], settings: Settings, store: SearchStore,
           llm: Optional[LLMProvider] = None) -> dict:
    """docs: PDFs (partes) do processo consultado; vazio = busca em tudo."""
    dmap = {d["id"]: d for d in docs}
    hits = store.search(question, list(dmap) or None, 6)
    sources = [{"doc_id": h.doc_id, "page": h.page, "rotulo": _lab(dmap, h.doc_id, h.page),
                "snippet": " ".join(h.text.split())[:300]} for h in hits]
    if not hits:
        return {"answer": "Não encontrei essa informação no(s) documento(s) analisado(s).", "sources": [], "mode": "none"}
    llm = llm or get_llm(settings)
    if llm is None:
        return {"answer": _extractive_answer(question, hits, dmap), "sources": sources, "mode": "extractive"}
    ctx = "\n\n".join(f"[{_lab(dmap, h.doc_id, h.page)}] {h.text}" for h in hits)
    text = llm.generate(SYSTEM, f"Trechos do processo:\n{ctx}\n\n{_structured_hint(question, dmap)}\n\nPergunta: {question}", 800)
    return {"answer": text, "sources": sources, "mode": "llm", "provedor": llm.name}
