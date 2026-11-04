"""Citação no formato dos relatórios: 'Evento N, VOL N, pg. N, ID N'."""
import re
from typing import Optional

# Nome do arquivo ex.: 0341609-70.2005.8.13.0261_V001_001-151-180.pdf
_CNJ = re.compile(r"(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})")
_VOL = re.compile(r"_V(\d+)")
_RANGE = re.compile(r"(\d+)-(\d+)(?=\.pdf$|$)", re.IGNORECASE)

_ID = re.compile(r"\bNum\.\s*(\d{6,})\s*-\s*P[áa]g\.\s*(\d+)")  # rodapé PJe: ID do documento + página nele
_EVENTO = re.compile(r"\bEvento\s+(\d{1,4})\b", re.IGNORECASE)  # eproc
_ID2 = re.compile(r"\bID[.\s:]*(\d{7,})")
_EPROC_ID = re.compile(r":: (\d{8,}) - eproc|c[óo]digo verificador (\d{8,})v\d")  # documentos baixados do eproc
_EVT_FILE = re.compile(r"Evento[_\s-]*0*(\d{1,4})", re.IGNORECASE)
_ID_FILE = re.compile(r"(?<!\d)(\d{9,})(?!\d)(?=_-_eproc|\s*-\s*eproc)")


def parse_filename(name: str) -> dict:
    stem = name.rsplit("/", 1)[-1]
    cnj = _CNJ.search(stem)
    vol = _VOL.search(stem)
    rng = _RANGE.search(stem) if vol else None
    first = int(rng.group(1)) if rng and int(rng.group(1)) <= int(rng.group(2)) else None
    evt = _EVT_FILE.search(stem)
    return {"numero_processo": cnj.group(1) if cnj else None,
            "volume": int(vol.group(1)) if vol else None,
            "pagina_inicial": first,
            "evento": evt.group(1) if evt else None}


def page_refs(text: str) -> dict:
    """Marcadores de evento/ID encontrados no texto de uma página (quando o PDF os traz)."""
    out = {}
    if m := _ID.search(text):
        out["id"], out["pg"] = m.group(1), m.group(2)
        out["pje"] = True
    elif m := _EPROC_ID.search(text):
        out["id"] = m.group(1) or m.group(2)
    elif m := _ID2.search(text):
        out["id"] = m.group(1)
    if m := _EVENTO.search(text):
        out["evento"] = m.group(1)
    return out


def detect_system(pages_refs: list[dict], texts: list[str]) -> Optional[str]:
    """'pje' se as páginas trazem rodapé 'Num. X - Pág. N'; 'eproc' se houver marcadores do eproc."""
    n = max(len(pages_refs), 1)
    if sum(1 for r in pages_refs if r.get("pje")) / n > 0.3:
        return "pje"
    if any("eproc" in t.lower() for t in texts) or any(r.get("evento") for r in pages_refs):
        return "eproc"
    return None


def fill_forward(refs_list: list[dict]) -> list[dict]:
    """Páginas sem marcador herdam o evento/ID da página anterior (um ato ocupa várias páginas)."""
    last: dict = {}
    out = []
    for r in refs_list:
        if "evento" in r or "id" in r:
            last = {k: v for k, v in r.items() if k in ("evento", "id")}
        out.append({**last, **r})
    return out


def label(volume: Optional[int], page: int, refs: Optional[dict] = None, sistema: Optional[str] = None) -> str:
    """eproc: 'Evento N, pg. P' (nunca ID). PJe: 'ID N, pg. P' (nunca Evento). Digitalizado: 'VOL N, pg. P'."""
    refs = refs or {}
    parts = []
    if sistema != "pje" and refs.get("evento"):
        parts.append(f"Evento {refs['evento']}")
    if sistema == "pje" and refs.get("id"):
        parts.append(f"ID {refs['id']}")
    if volume is not None:
        parts.append(f"VOL {volume:03d}")
    parts.append(f"pg. {refs.get('pg') or page}" if sistema == "pje" else f"pg. {page}")
    if sistema is None and refs.get("id"):
        parts.append(f"ID {refs['id']}")
    return ", ".join(parts)
