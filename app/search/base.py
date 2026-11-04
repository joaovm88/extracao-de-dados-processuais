import hashlib
import math
import re
from dataclasses import dataclass
from typing import Optional, Protocol

from ..ingest.entities import norm

_TOKEN = re.compile(r"[a-z0-9]{2,}")
_STOP = {"de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "um", "uma", "que", "para", "por",
         "com", "se", "ao", "os", "as", "ou", "existe", "qual", "quais", "foi", "ser", "sao"}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(norm(text)) if t not in _STOP]


def embed(text: str, dims: int = 256) -> list[float]:
    """Embedding local por hashing de palavras e bigramas (sem dependências externas).
    Substituível por um modelo de embeddings real implementando a mesma assinatura."""
    vec = [0.0] * dims
    toks = tokenize(text)
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        vec[h % dims] += 1.0 if (h >> 64) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


@dataclass
class Hit:
    doc_id: str
    page: int
    text: str
    score: float


class SearchStore(Protocol):
    def index(self, doc_id: str, chunks: list) -> None: ...
    def search(self, query: str, doc_id: Optional[str], k: int) -> list[Hit]: ...
    def delete(self, doc_id: str) -> None: ...
