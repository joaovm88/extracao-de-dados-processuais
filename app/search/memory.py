"""Busca híbrida em memória (BM25 + cosseno, fusão RRF). Para dev/testes."""
import math
import threading
from collections import Counter
from typing import Optional

from .base import Hit, embed, tokenize


class MemoryStore:
    def __init__(self, dims: int = 256) -> None:
        self.dims = dims
        self._rows: list[dict] = []
        self._lock = threading.Lock()

    def index(self, doc_id: str, chunks: list) -> None:
        rows = [
            {"doc_id": doc_id, "page": c.page, "text": c.text, "tf": Counter(tokenize(c.text)),
             "vec": embed(c.text, self.dims)}
            for c in chunks
        ]
        with self._lock:
            self._rows.extend(rows)

    def delete(self, doc_id: str) -> None:
        with self._lock:
            self._rows = [r for r in self._rows if r["doc_id"] != doc_id]

    def search(self, query: str, doc_id: Optional[str], k: int = 6) -> list[Hit]:
        with self._lock:
            rows = [r for r in self._rows if doc_id is None or r["doc_id"] == doc_id]
        if not rows:
            return []
        q = tokenize(query)
        n = len(rows)
        avgdl = sum(sum(r["tf"].values()) for r in rows) / n or 1
        df = Counter(t for r in rows for t in set(r["tf"]) if t in q)
        bm25 = []
        for r in rows:
            dl = sum(r["tf"].values())
            s = 0.0
            for t in q:
                f = r["tf"].get(t, 0)
                if f:
                    idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                    s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * dl / avgdl))
            bm25.append(s)
        qv = embed(query, self.dims)
        cos = [sum(a * b for a, b in zip(qv, r["vec"])) for r in rows]

        def ranks(scores):
            order = sorted(range(n), key=lambda i: -scores[i])
            return {i: pos for pos, i in enumerate(order)}

        rb, rc = ranks(bm25), ranks(cos)
        fused = {i: (1 / (60 + rb[i]) if bm25[i] > 0 else 0) + 1 / (60 + rc[i]) for i in range(n)}
        top = sorted(fused, key=lambda i: -fused[i])[:k]
        return [Hit(rows[i]["doc_id"], rows[i]["page"], rows[i]["text"], fused[i]) for i in top]
