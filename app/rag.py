"""Motor RAG: recupera trechos (híbrido) e responde com citação de página."""
from typing import Optional

from .config import Settings
from .llm import LLMProvider, get_llm
from .db import MetadataDB
from .ingest.entities import fmt_brl, norm
from .ingest.citation import label
from .search.base import Hit, SearchStore

SYSTEM = (
    "Você é um assistente de análise de processos judiciais. Responda SOMENTE com base nos trechos fornecidos. "
    "Cite sempre a origem usando o rótulo do trecho, ex.: (VOL 001, pg. 12). Se a informação não estiver nos trechos, diga claramente que não "
    "foi encontrada no documento. Não invente valores, nomes ou números. Responda em português."
)


def _structured_hint(question: str, doc: Optional[dict]) -> str:
    """Para perguntas sobre valores, injeta os valores já extraídos como contexto adicional."""
    if not doc or not doc.get("metadata"):
        return ""
    q = norm(question)
    if not any(w in q for w in ("valor", "bloque", "sisbajud", "darf", "custas", "honorario", "quanto")):
        return ""
    cats = {"sisbajud": "Bloqueio SISBAJUD", "bloque": "Bloqueio SISBAJUD", "darf": "DARF / Tributo",
            "custas": "Custas", "honorario": "Honorários"}
    wanted = {c for k, c in cats.items() if k in q}
    vals = [v for v in doc["metadata"]["valores"] if not wanted or v["categoria"] in wanted]
    lines = [f"- {v['valor_formatado']} ({v['categoria']}, {_lab(doc, v['pagina'])}): {v['contexto'][:160]}" for v in vals[:15]]
    return ("Valores extraídos automaticamente:\n" + "\n".join(lines)) if lines else ""


def _lab(doc: Optional[dict], page: int) -> str:
    rot = ((doc or {}).get("metadata") or {}).get("rotulos") or {}
    return rot.get(str(page)) or label((doc or {}).get("volume"), page)


def _extractive_answer(question: str, hits: list[Hit], hint_doc: Optional[dict]) -> str:
    if not hits:
        return "Não encontrei essa informação no(s) documento(s) analisado(s)."
    q = norm(question)
    parts = []
    if hint_doc and hint_doc.get("metadata") and any(w in q for w in ("valor", "bloque", "sisbajud", "darf", "quanto")):
        h = _structured_hint(question, hint_doc)
        if h:
            parts.append(h)
    parts.append("Trechos mais relevantes:")
    for x in hits[:3]:
        parts.append(f"({_lab(hint_doc, x.page)}) {' '.join(x.text.split())[:350]}")
    return "\n".join(parts)


def answer(question: str, doc_id: Optional[str], settings: Settings, db: MetadataDB, store: SearchStore,
           llm: Optional[LLMProvider] = None) -> dict:
    hits = store.search(question, doc_id, 6)
    doc = db.get(doc_id) if doc_id else None
    sources = [{"doc_id": h.doc_id, "page": h.page, "rotulo": _lab(doc, h.page), "snippet": " ".join(h.text.split())[:300]} for h in hits]
    if not hits:
        return {"answer": "Não encontrei essa informação no(s) documento(s) analisado(s).", "sources": [], "mode": "none"}

    llm = llm or get_llm(settings)
    if llm is None:
        return {"answer": _extractive_answer(question, hits, doc), "sources": sources, "mode": "extractive"}

    ctx = "\n\n".join(f"[{_lab(doc, h.page)}] {h.text}" for h in hits)
    hint = _structured_hint(question, doc)
    text = llm.generate(SYSTEM, f"Trechos do processo:\n{ctx}\n\n{hint}\n\nPergunta: {question}", 800)
    return {"answer": text, "sources": sources, "mode": "llm", "provedor": llm.name}
