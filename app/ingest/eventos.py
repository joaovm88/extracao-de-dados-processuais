"""Linha do tempo DETERMINÍSTICA do eproc: lê as 'PÁGINAS DE SEPARAÇÃO' que o próprio sistema insere em cada evento
(nº do evento, data, movimento, usuário) e os códigos de documento dos rodapés. Completa e sem IA: a IA só descreve os
atos relevantes, e o sistema confere nº e data do que ela escrever."""
import re
from collections import defaultdict
from typing import Optional

from .entities import norm

_LABELS = {"tipo documento": "tipo_documento", "evento": "movimento", "data": "data", "usuario": "usuario",
           "processo": "processo", "sequencia evento": "sequencia"}
_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}:\d{2}:\d{2})")
_HEADER = re.compile(r"^Evento\s+(\d{1,5})$", re.IGNORECASE)
_DOCN = re.compile(r"^Documento\s+(\d+)$", re.IGNORECASE)

# relevância (deterministica) do ato, só para decidir de quais eventos o texto integral vai à IA
_FORTE = ("penhora", "bloqueio", "sisbajud", "bacenjud", "renajud", "infojud", "arresto", "avalia", "leilao", "hasta", "arremat",
          "alienacao", "adjudica", "citacao", "citado", "transit", "extin", "excecao", "embargos", "redirecion", "desconsider",
          "parcelament", "suspens", "arquivament", "prescri", "pagamento", "conversao", "alvara", "tutela", "liminar", "agravo",
          "apelacao", "recurso", "sentenc", "acord", "peticao inicial", "inic", "habilita", "indisponib")
_BAIXA = ("ato ordinatorio", "intimacao", "expedid", "remessa", "juntada", "conclus", "publicacao", "decurso", "certidao",
          "baixa", "autuacao", "recebimento", "ciencia", "vista", "remetid", "confirmad", "comunicacao", "cadastr", "lancamento", "decorrid")
_DECISAO = ("decisao", "despacho")
_ANEXOS = ("ESTATUTO", "PROC", "DOC", "ANEXO", "COMP", "OUT", "CONTR", "PROCUR", "SUBST")  # códigos de documento: anexos de baixo valor


def _is_separator(text: str) -> bool:
    return "SEPARA" in norm(text[:120]).upper()


def parse_separator(text: str) -> Optional[dict]:
    """Lê uma página de separação (formatos do eproc de 2º grau e de 1ª instância). None se não for uma."""
    if not _is_separator(text):
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    header = next((int(m.group(1)) for l in lines if (m := _HEADER.match(l))), None)  # 'Evento 14' (2º grau)
    first = next((i for i, l in enumerate(lines) if l.endswith(":") and norm(l[:-1]) in _LABELS), None)
    if first is None:
        return None
    keys, i = [], first
    while i < len(lines) and lines[i].endswith(":") and norm(lines[i][:-1]) in _LABELS:
        keys.append(_LABELS[norm(lines[i][:-1])])
        i += 1
    vals = dict(zip(keys, lines[i:i + len(keys)]))
    i += len(keys)
    extras, cur, doc_n = {}, None, None
    for l in lines[i:]:  # blocos opcionais: Complemento:, Prazo:, Agravante:…
        if m := _DOCN.match(l):
            doc_n = int(m.group(1))
        elif l.endswith(":"):
            cur = l[:-1].strip()
            extras[cur] = ""
        elif cur:
            extras[cur] = (extras[cur] + " " + l).strip()
    seq = re.sub(r"\D", "", vals.get("sequencia", ""))
    numero = int(seq) if seq else header
    if numero is None:
        return None
    d = _DATA.search(vals.get("data", ""))
    return {"evento": numero, "data": f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else None, "hora": d.group(4) if d else None,
            "movimento": vals.get("movimento", "").strip(), "tipo_documento": vals.get("tipo_documento", "").strip(),
            "usuario": vals.get("usuario", "").strip(), "complemento": extras.get("Complemento", ""), "extras": extras, "documento": doc_n}


def extract_events(pages: list[dict], doc_id: str) -> list[dict]:
    """pages: [{'page', 'text', 'refs'}] na ordem do PDF. Devolve os eventos (um por nº), com os códigos de documento
    e as páginas de cada um. Vazio se o PDF não tiver separadores (ex.: PJe, autos digitalizados)."""
    eventos: dict[int, dict] = {}
    atual: Optional[int] = None

    def get(n: int) -> dict:
        return eventos.setdefault(n, {"evento": n, "data": None, "hora": None, "movimento": "", "tipo_documento": "", "usuario": "",
                                      "complemento": "", "docs": [], "paginas": [], "caracteres": 0, "doc_id": doc_id})
    for p in pages:
        sep = parse_separator(p["text"])
        if sep:
            e = get(sep["evento"])
            for k in ("data", "hora", "movimento", "tipo_documento", "usuario", "complemento"):
                if sep[k] and not e[k]:
                    e[k] = sep[k]
            atual = sep["evento"]
            continue
        n = int(p["refs"]["evento"]) if p.get("refs", {}).get("evento") else atual
        if n is None or n not in eventos:
            continue
        e = eventos[n]
        e["paginas"].append(p["page"])
        e["caracteres"] += len(p["text"])
        code = p.get("refs", {}).get("tipo_doc")
        if code and code not in e["docs"]:
            e["docs"].append(code)
    return sorted(eventos.values(), key=lambda e: e["evento"])


def merge_events(listas: list[list[dict]]) -> list[dict]:
    """Junta os eventos de várias partes do mesmo processo (PARTE_1, PARTE_2…), um por nº, em ordem."""
    out: dict[int, dict] = {}
    for lista in listas:
        for e in lista:
            m = out.get(e["evento"])
            if not m:
                out[e["evento"]] = {**e, "paginas": [[e["doc_id"], p] for p in e["paginas"]], "docs": list(e["docs"])}
                continue
            m["paginas"] += [[e["doc_id"], p] for p in e["paginas"]]
            m["caracteres"] += e["caracteres"]
            m["docs"] += [c for c in e["docs"] if c not in m["docs"]]
            for k in ("data", "hora", "movimento", "tipo_documento", "usuario", "complemento"):
                m[k] = m[k] or e[k]
    for e in out.values():
        e["paginas"] = e["paginas"] if e["paginas"] and isinstance(e["paginas"][0], list) else [[e["doc_id"], p] for p in e["paginas"]]
    return sorted(out.values(), key=lambda e: (e["data"] or "9999", e["evento"]))


def relevancia(e: dict) -> str:
    """'alta' (texto integral vai à IA), 'media' (vai se couber) ou 'baixa' (só o índice: mero expediente).
    Decide pelo movimento, tipo e código do documento; o complemento só conta para termos fortes (penhora, sentença…)."""
    principal = norm(f"{e.get('movimento','')} {e.get('tipo_documento','')} {' '.join(e.get('docs', []))}")
    if any(k in principal or k in norm(e.get("complemento", "")) for k in _FORTE):
        return "alta"
    if any(k in principal for k in _BAIXA):
        return "baixa"
    if any(k in principal for k in _DECISAO):
        return "alta"
    return "media"


def fmt_data(iso: Optional[str]) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso else "data não informada"


def linha_base(e: dict) -> str:
    """Linha da cronologia feita só com o que o sistema registrou (sem IA): 'Evento N - dd/mm/aaaa - MOVIMENTO'."""
    mov = e["movimento"] or e["tipo_documento"] or "(sem descrição)"
    return f"Evento {e['evento']} - {fmt_data(e['data'])} - {mov}" + (f" ({e['complemento'][:120]})" if e.get("complemento") else "")


def indice(eventos: list[dict], com_texto: set) -> str:
    """Índice completo para a IA: uma linha por evento, indicando se o texto integral foi enviado."""
    out = []
    for e in eventos:
        docs = ", ".join(c for c in e["docs"] if not c.upper().startswith(_ANEXOS)) or "—"
        out.append(f"Evento {e['evento']} | {fmt_data(e['data'])} | {e['movimento'] or e['tipo_documento'] or '(sem descrição)'} | docs: {docs} | "
                   f"{len(e['paginas'])} pág. | texto integral: {'sim' if e['evento'] in com_texto else 'não'}")
    return "\n".join(out)


_LINHA = re.compile(r"^\s*(Evento\s+(\d+)(?:\s*,\s*pg\.\s*\d+)?)\s*[-–—]\s*(\d{2}/\d{2}/\d{4}|data não informada)\s*[-–—]\s*(.+)$", re.IGNORECASE)


def validar_cronologia(texto: Optional[str], eventos: list[dict]) -> tuple[Optional[str], dict]:
    """Confere a cronologia escrita pela IA contra os eventos reais: troca a data pela registrada no eproc,
    aponta eventos que não existem e põe as linhas em ordem de data. Linhas fora do padrão ficam como estão."""
    stats = {"linhas": 0, "datas_corrigidas": 0, "inexistentes": [], "ordenada": False}
    if not texto or not eventos:
        return texto, stats
    por_n = {e["evento"]: e for e in eventos}
    linhas = [l for l in texto.splitlines() if l.strip()]
    parsed, outras = [], []
    for l in linhas:
        m = _LINHA.match(l)
        if not m:
            outras.append(l)
            continue
        n, data = int(m.group(2)), m.group(3)
        ev = por_n.get(n)
        if ev is None:
            stats["inexistentes"].append(n)
        elif ev["data"] and fmt_data(ev["data"]) != data:
            stats["datas_corrigidas"] += 1
            data = fmt_data(ev["data"])
        parsed.append((ev["data"] if ev and ev["data"] else "9999", n, f"{m.group(1)} - {data} - {m.group(4).strip()}"))
    stats["linhas"] = len(parsed)
    if not parsed or outras and len(outras) > len(parsed):  # texto que não é uma lista de atos: não mexe
        return texto, stats
    ordenado = sorted(parsed, key=lambda x: (x[0], x[1]))
    stats["ordenada"] = [x[2] for x in ordenado] != [x[2] for x in parsed]
    return "\n".join([x[2] for x in ordenado] + outras), stats
