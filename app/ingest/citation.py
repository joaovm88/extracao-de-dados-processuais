"""Citação no formato dos relatórios: 'Evento N, VOL N, pg. N, ID N'."""
import re
from typing import Optional

# Nome do arquivo ex.: 0341609-70.2005.8.13.0261_V001_001-151-180.pdf
_CNJ = re.compile(r"(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})")
_VOL = re.compile(r"_V(\d+)")
_RANGE = re.compile(r"(\d+)-(\d+)(?=\.pdf$|$)", re.IGNORECASE)

_ID = re.compile(r"\bNum\.\s*(\d{6,})\s*-\s*P[áa]g\.\s*(\d+)")  # rodapé PJe
_EVENTO = re.compile(r"\bEvento\s+(\d{1,4})\b", re.IGNORECASE)  # eproc
_ID2 = re.compile(r"\bID[.\s:]*(\d{7,})")


def parse_filename(name: str) -> dict:
    stem = name.rsplit("/", 1)[-1]
    cnj = _CNJ.search(stem)
    vol = _VOL.search(stem)
    rng = _RANGE.search(stem) if vol else None
    first = int(rng.group(1)) if rng and int(rng.group(1)) <= int(rng.group(2)) else None
    return {"numero_processo": cnj.group(1) if cnj else None,
            "volume": int(vol.group(1)) if vol else None,
            "pagina_inicial": first}


def page_refs(text: str) -> dict:
    """Marcadores de evento/ID encontrados no texto de uma página (quando o PDF os traz)."""
    out = {}
    if m := _ID.search(text):
        out["id"] = m.group(1)
    elif m := _ID2.search(text):
        out["id"] = m.group(1)
    if m := _EVENTO.search(text):
        out["evento"] = m.group(1)
    return out


def label(volume: Optional[int], page: int, refs: Optional[dict] = None) -> str:
    refs = refs or {}
    parts = []
    if refs.get("evento"):
        parts.append(f"Evento {refs['evento']}")
    if volume is not None:
        parts.append(f"VOL {volume:03d}")
    parts.append(f"pg. {page}")
    if refs.get("id"):
        parts.append(f"ID {refs['id']}")
    return ", ".join(parts)
