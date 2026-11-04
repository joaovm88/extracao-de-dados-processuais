"""Quebra semântica: respeita parágrafos e mantém a página de origem de cada trecho."""
import re
from dataclasses import dataclass


@dataclass
class Chunk:
    page: int
    seq: int
    text: str


def chunk_page(page: int, text: str, max_chars: int = 900) -> list[Chunk]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9][^\n]{0,80}:)", text) if p.strip()]
    chunks: list[Chunk] = []
    buf = ""
    for p in paragraphs:
        p = re.sub(r"[ \t]+", " ", p)
        # parágrafo gigante: divide por sentenças
        parts = [p] if len(p) <= max_chars else re.split(r"(?<=[.;])\s+", p)
        for part in parts:
            if buf and len(buf) + len(part) + 1 > max_chars:
                chunks.append(Chunk(page, len(chunks), buf))
                buf = ""
            buf = f"{buf}\n{part}".strip()
    if buf:
        chunks.append(Chunk(page, len(chunks), buf))
    return chunks
