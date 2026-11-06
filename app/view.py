"""Dados da 'planilha viva': colunas por aba (da planilha-modelo, se houver) e uma linha por processo."""
import io
from typing import Optional

from openpyxl import load_workbook

from . import template as tpl
from .schemas import SHEETS, classify

ABAS = [("execucoes_fiscais", "Execuções Fiscais"), ("outras_acoes", "Outras Ações"), ("embargos", "Embargos"),
        ("bens", "Outros Bens"), ("fraude", "Fraudes")]
BENS_COLS = [("matricula", "Matrícula"), ("caracteristicas", "Características do Imóvel"), ("proprietario", "Proprietário"),
             ("ato", "Ato (Nº R./Av.)"), ("gravames", "Gravames"), ("resumo", "Resumo"), ("situacao", "Situação")]
FRAUDE_COLS = [("tipo", "Tipo de fraude alegada"), ("analise", "Análise do pedido"), ("valor_causa", "Valor da causa")]
_MCFLY_KEYS = set(tpl._MCFLY_SYN.values())


def _col(key: str, titulo: str, kind: str) -> dict:
    """Coluna editável só se a IA a preenche; capa (Mcfly) e 'digitalizado' são somente leitura."""
    f = next((x for x in SHEETS[kind].fields if x.key == key), None) if kind in SHEETS else None
    fonte = f.source if f else ("mcfly" if key in _MCFLY_KEYS else "ai")
    return {"key": key, "titulo": titulo.strip(), "opcoes": f.options if f else None, "fonte": fonte}


def default_columns(kind: str) -> list[dict]:
    return [_col(f.key, f.header, kind) for f in SHEETS[kind].fields]


def template_columns(data: bytes) -> dict[str, list[dict]]:
    """Colunas reconhecidas de cada aba do modelo, na ordem em que aparecem na planilha."""
    wb = load_workbook(io.BytesIO(data))
    out: dict[str, list[dict]] = {}
    for ws in wb.worksheets:
        kind = tpl.sheet_kind(ws.title)
        if kind not in SHEETS or kind in out:
            continue
        found = tpl._find_header_row(ws, tpl._header_map(kind))
        if found:
            hrow, cols, _ = found
            out[kind] = [_col(key, str(ws.cell(hrow, c).value), kind) for c, key in sorted(cols.items())]
    return out


def build(processes: list[dict], docs: list[dict], template_bytes: Optional[bytes], estado) -> dict:
    tcols = template_columns(template_bytes) if template_bytes else {}
    abas = [{"key": k, "titulo": t, "colunas": tcols.get(k) or (default_columns(k) if k in SHEETS else
            [{"key": a, "titulo": b, "opcoes": None, "fonte": "ai"} for a, b in (BENS_COLS if k == "bens" else FRAUDE_COLS)])}
            for k, t in ABAS]
    by_num: dict[str, list[dict]] = {}
    for d in docs:
        if d.get("numero_processo"):
            by_num.setdefault(d["numero_processo"], []).append(d)
    linhas, bens, fraudes = [], [], []
    for p in processes:
        res = p.get("result") or {}
        m = {k: v for k, v in (p.get("mcfly") or {}).items() if v is not None}
        valores = {**m, "numero": p["numero"]}
        if not valores.get("classe") and p.get("classe"):
            valores["classe"] = p["classe"]
        valores.update(res.get("fields") or {})
        aba = res.get("sheet") or classify(m.get("classe") or p.get("classe") or "")[0]
        linhas.append({
            "numero": p["numero"], "aba": aba, "estado": estado(p), "reviewed": bool(p.get("reviewed")), "error": p.get("error"),
            "valores": valores, "citacoes": res.get("citacoes") or {}, "citacoes_invalidas": res.get("citacoes_invalidas") or [],
            "sistema": res.get("sistema"), "modelo": res.get("prompt"), "paginas": res.get("paginas"), "modo": res.get("modo"),
            "medidas": res.get("medidas"), "cronologia": res.get("cronologia"),
            "n_eventos": sum((d.get("metadata") or {}).get("n_eventos") or 0 for d in by_num.get(p["numero"], [])),
            "docs": [{**{k: d.get(k) for k in ("id", "filename", "status", "pages_done", "pages_total", "pages_ocr", "error")},
                      "tempos": (d.get("metadata") or {}).get("tempos")} for d in by_num.get(p["numero"], [])]})
        bens += [{"numero": p["numero"], **b} for b in res.get("bens", [])]
        fraudes += [{"numero": p["numero"], **f} for f in res.get("fraudes", [])]
    for a in abas:
        a["n"] = len(bens) if a["key"] == "bens" else len(fraudes) if a["key"] == "fraude" else sum(1 for l in linhas if l["aba"] == a["key"])
    return {"abas": abas, "linhas": linhas, "bens": bens, "fraudes": fraudes,
            "documentos": [{k: d.get(k) for k in ("id", "filename", "status", "pages_done", "pages_total", "pages_ocr", "error", "numero_processo")}
                           for d in docs]}
