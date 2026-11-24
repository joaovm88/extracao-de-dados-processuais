"""Linha do tempo do PJe: o índice 'Documentos' da capa (ID, data, nome, tipo) cruzado com os rodapés
'Num. <ID> - Pág. <n>' das páginas. Equivale ao índice de eventos do eproc (ingest/eventos.py)."""
import re
from typing import Optional

from . import eventos as ev
from .eventos import fmt_data

_NUM = re.compile(r"^\d{3,6}$")
_DATA_LINHA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s+(\d{2}:\d{2})\s+(.+)$")
_ID_LINHA = re.compile(r"^\d{9,}$")
_ASSIN = re.compile(r"Assinado eletronicamente por:\s*(.+?)\s*-\s*(\d{2})/(\d{2})/(\d{4})\s+(\d{2}:\d{2}:\d{2})")
_LINHA = re.compile(r"^\s*(ID\s+(\d{6,})(?:\s*,\s*pg\.\s*\d+)?)\s*[-–—]\s*(\d{2}/\d{2}/\d{4}|data não informada)\s*[-–—]\s*(.+)$", re.IGNORECASE)
_BAIXA_TIPO = ("certidao de processo migrado", "intimacao", "ato ordinatorio", "expediente", "aviso de recebimento", "comprovante de")


def parse_indice(text: str) -> list[dict]:
    """Lê a tabela 'Documentos' da capa. O ID vem quebrado em duas linhas ('78623' + '2960' = 786232960)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    starts = []  # (posição da linha da data, id)
    i = 0
    while i < len(lines):
        if _ID_LINHA.match(lines[i]) and i + 1 < len(lines) and _DATA_LINHA.match(lines[i + 1]):
            starts.append((i, i + 1, lines[i])); i += 1; continue
        if _NUM.match(lines[i]) and i + 2 < len(lines) and _NUM.match(lines[i + 1]) and _DATA_LINHA.match(lines[i + 2]):
            starts.append((i, i + 2, lines[i] + lines[i + 1])); i += 2; continue
        i += 1
    out = []
    for k, (ini, dl, id_) in enumerate(starts):
        fim = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        m = _DATA_LINHA.match(lines[dl])
        resto = lines[dl + 1:fim]
        tipo = resto[-1] if resto else ""
        nome = " ".join([m.group(5)] + resto[:-1]).strip()
        out.append({"id": id_, "data": f"{m.group(3)}-{m.group(2)}-{m.group(1)}", "hora": m.group(4), "movimento": nome, "tipo_documento": tipo})
    return out


def extract_docs(pages: list[dict], doc_id: str) -> list[dict]:
    """Documentos do PJe vistos neste PDF: os do índice da capa (mesmo sem páginas aqui) e os dos rodapés."""
    docs: dict[str, dict] = {}

    def get(i: str) -> dict:
        return docs.setdefault(i, {"id": i, "data": None, "hora": None, "movimento": "", "tipo_documento": "", "usuario": "",
                                   "complemento": "", "docs": [], "paginas": [], "caracteres": 0, "doc_id": doc_id, "no_indice": False})
    for p in pages:
        refs = p.get("refs", {})
        if not refs.get("pje"):
            if not any(q.get("refs", {}).get("pje") for q in pages[:p["page"] - 1]):  # só a capa (antes do 1º rodapé)
                for r in parse_indice(p["text"]):
                    d = get(r["id"]); d.update({k: r[k] for k in ("data", "hora", "movimento", "tipo_documento")}); d["no_indice"] = True
            continue
        d = get(refs["id"])
        d["paginas"].append(p["page"])
        d["caracteres"] += len(p["text"])
        if not d["usuario"] and (m := _ASSIN.search(p["text"])):
            d["usuario"] = m.group(1).strip()
            if not d["data"]:
                d["data"], d["hora"] = f"{m.group(4)}-{m.group(3)}-{m.group(2)}", m.group(5)[:5]
    return sorted(docs.values(), key=lambda d: (d["data"] or "9999", d["id"]))


def merge_docs(listas: list[list[dict]]) -> list[dict]:
    """Junta as partes do mesmo processo: um registro por ID, com as páginas de todos os arquivos."""
    out: dict[str, dict] = {}
    for lista in listas:
        for d in lista:
            m = out.setdefault(d["id"], {**d, "paginas": [], "caracteres": 0, "no_indice": False})
            m["paginas"] += [[d["doc_id"], p] for p in d["paginas"]]
            m["caracteres"] += d["caracteres"]
            m["no_indice"] = m["no_indice"] or d.get("no_indice", False)
            for k in ("data", "hora", "movimento", "tipo_documento", "usuario"):
                if d.get(k) and (k != "data" or d.get("no_indice") or not m[k]):
                    m[k] = d[k]
    return sorted(out.values(), key=lambda d: (d["data"] or "9999", d["id"]))


def e_volume(d: dict) -> bool:
    """Autos físicos digitalizados dentro de um único documento do PJe (processos migrados)."""
    return "volume" in ev.norm(f"{d.get('tipo_documento','')} {d.get('movimento','')}")


def relevancia(d: dict) -> str:
    t = ev.norm(f"{d.get('tipo_documento','')} {d.get('movimento','')}")
    if e_volume(d) or "peticao inicial" in t:
        return "alta"
    r = ev.relevancia({**d, "docs": []})
    if r == "media" and any(k in t for k in _BAIXA_TIPO):
        return "baixa"
    return r


def linha_base(d: dict) -> str:
    pg = f", pg. 1" if d["paginas"] else ""
    return f"ID {d['id']}{pg} - {fmt_data(d['data'])} - {d['movimento'] or d['tipo_documento'] or '(sem descrição)'}"


def indice(docs: list[dict], com_texto: set) -> str:
    return "\n".join(f"ID {d['id']} | {fmt_data(d['data'])} | {d['movimento'] or d['tipo_documento'] or '(sem descrição)'}"
                     f"{' | ' + d['tipo_documento'] if d['tipo_documento'] and d['tipo_documento'] != d['movimento'] else ''} | "
                     f"{len(d['paginas'])} pág. neste(s) PDF(s) | texto integral: {'sim' if d['id'] in com_texto else 'não'}" for d in docs)


def validar_cronologia(texto: Optional[str], docs: list[dict]) -> tuple[Optional[str], dict]:
    """Confere a cronologia da IA: ID inexistente é apontado; a data do índice vale para documentos comuns
    (dentro de um 'Volume' digitalizado a data é a do ato, que a IA lê do texto); linhas ficam em ordem de data."""
    stats = {"linhas": 0, "datas_corrigidas": 0, "inexistentes": [], "ordenada": False}
    if not texto or not docs:
        return texto, stats
    por_id = {d["id"]: d for d in docs}
    parsed, outras = [], []
    for l in (x for x in texto.splitlines() if x.strip()):
        m = _LINHA.match(l)
        if not m:
            outras.append(l); continue
        d, data = por_id.get(m.group(2)), m.group(3)
        if d is None:
            stats["inexistentes"].append(m.group(2))
        elif d["data"] and not e_volume(d) and fmt_data(d["data"]) != data:
            stats["datas_corrigidas"] += 1; data = fmt_data(d["data"])
        chave = f"{data[6:]}-{data[3:5]}-{data[:2]}" if re.match(r"\d{2}/\d{2}/\d{4}$", data) else "9999"
        parsed.append((chave, f"{m.group(1)} - {data} - {m.group(4).strip()}"))
    stats["linhas"] = len(parsed)
    if not parsed or len(outras) > len(parsed):
        return texto, stats
    ordenado = sorted(parsed, key=lambda x: x[0])
    stats["ordenada"] = [x[1] for x in ordenado] != [x[1] for x in parsed]
    return "\n".join([x[1] for x in ordenado] + outras), stats
