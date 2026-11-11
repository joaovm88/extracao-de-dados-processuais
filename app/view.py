"""Dados da 'planilha viva': colunas por aba (da planilha-modelo, se houver) e uma linha por processo."""
import io
from typing import Optional

from openpyxl import load_workbook

from . import template as tpl
from .schemas import FRAUDE_ANALISES, FRAUDE_TIPOS, SHEETS, classify, item_status, manual_text, normalize_fields

ABAS = [("execucoes_fiscais", "Execuções Fiscais"), ("outras_acoes", "Outras Ações"), ("embargos", "Embargos"),
        ("bens", "Outros Bens"), ("fraude", "Fraudes"), ("rj", "Recuperação Judicial")]
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


def resumo_rj(res: dict) -> dict:
    """O que a aba Recuperação Judicial mostra de cada processo (o relatório completo abre numa janela própria)."""
    from . import rj

    rel = res["relatorio"]
    c = rel.get("campos") or {}
    v = lambda k: (c.get(k) or {}).get("valor")
    calc = rj.calcular(rel)
    return {"numero_rj": v("numero_rj"), "vara": v("vara"), "administrador": v("administrador_judicial"),
            "empresas": [e.get("nome") for e in rel.get("empresas") or [] if e.get("nome")],
            "ajuizamento": v("data_ajuizamento"), "deferimento": v("data_deferimento"), "concessao": v("data_concessao"),
            "aprovado": v("aprovado_agc"), "homologado": v("homologado"), "encerrada": v("encerrada"),
            "situacao": v("situacao_atual"), "desagio": calc.get("desagio"), "avisos": len(calc["avisos"]), "nao_consta": len(calc["nao_consta"])}


def build(processes: list[dict], docs: list[dict], template_bytes: Optional[bytes], estado) -> dict:
    tcols = template_columns(template_bytes) if template_bytes else {}
    abas = [{"key": k, "titulo": t, "colunas": [] if k == "rj" else tcols.get(k) or (default_columns(k) if k in SHEETS else
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
        manual = bool(p.get("manual"))
        if manual:  # feito à mão: a planilha mostra só a marcação (o texto da IA, se houver, fica guardado e volta ao desfazer)
            valores["observacoes"] = manual_text(p)
        else:
            campos = res.get("fields") or {}
            valores.update(normalize_fields(SHEETS[res["sheet"]], campos) if res.get("sheet") in SHEETS else campos)
        if p.get("digitalizado"):  # escolha do analista (NÃO = processo físico) vale mais que o automático
            valores["digitalizado"] = p["digitalizado"]
        elif not res and not by_num.get(p["numero"]):
            valores["digitalizado"] = "NÃO"  # sem PDF: o processo não foi digitalizado
        aba = res.get("sheet") or ("rj" if any(d.get("tipo") == "recuperacao_judicial" for d in by_num.get(p["numero"], []))
                                   else classify(m.get("classe") or p.get("classe") or "")[0])
        linhas.append({
            "rj": resumo_rj(res) if aba == "rj" and res.get("relatorio") else None,
            "numero": p["numero"], "aba": aba, "estado": estado(p), "reviewed": bool(p.get("reviewed")), "error": p.get("error"),
            "valores": valores, "citacoes": {} if manual else res.get("citacoes") or {}, "citacoes_invalidas": [] if manual else res.get("citacoes_invalidas") or [],
            "sistema": res.get("sistema"), "modelo": res.get("prompt"), "paginas": res.get("paginas"), "modo": res.get("modo"),
            "medidas": res.get("medidas"), "cronologia": res.get("cronologia"),
            "manual": {"ativo": bool(p.get("manual")), "nota": p.get("manual_note"), "em": p.get("manual_at")},
            "oculto": bool(p.get("hidden")),
            "sugestoes": 0 if manual else sum(1 for k in ("bens", "fraudes") for i in res.get(k) or [] if item_status(i) == "sugerido"),
            "ext": {"inicio": p.get("ext_started_at"), "fim": p.get("ext_finished_at"), "status": p.get("ext_status")},
            "n_eventos": sum((d.get("metadata") or {}).get("n_eventos") or 0 for d in by_num.get(p["numero"], [])),
            "docs": [{**{k: d.get(k) for k in ("id", "filename", "status", "pages_done", "pages_total", "pages_ocr", "error", "started_at", "finished_at")},
                      "tempos": (d.get("metadata") or {}).get("tempos")} for d in by_num.get(p["numero"], [])]})
        if not manual and not p.get("hidden"):  # itens das abas Outros Bens / Fraudes: sugeridos pela IA ou já autorizados
            m_ = p.get("mcfly") or {}
            bens += [{**b, "numero": p["numero"], "_i": i, "_st": item_status(b)} for i, b in enumerate(res.get("bens") or [])
                     if item_status(b) != "descartado"]
            fraudes += [{**f, "numero": p["numero"], "classe": m_.get("classe") or p.get("classe"),
                         "valor_causa": f.get("valor_causa") or m_.get("valor_causa"), "_i": i, "_st": item_status(f)}
                        for i, f in enumerate(res.get("fraudes") or []) if item_status(f) != "descartado"]
    for a in abas:
        itens = bens if a["key"] == "bens" else fraudes if a["key"] == "fraude" else None
        if itens is not None:
            a["n"] = sum(1 for i in itens if i["_st"] == "autorizado")
            a["pendentes"] = sum(1 for i in itens if i["_st"] == "sugerido")
        else:
            a["n"] = sum(1 for l in linhas if l["aba"] == a["key"] and not l["oculto"])
    return {"abas": abas, "linhas": linhas, "bens": bens, "fraudes": fraudes,
            "listas": {"fraude_tipo": FRAUDE_TIPOS, "fraude_analise": FRAUDE_ANALISES},
            "documentos": [{k: d.get(k) for k in ("id", "filename", "status", "pages_done", "pages_total", "pages_ocr", "error", "numero_processo",
                                                       "started_at", "finished_at")}
                           for d in docs]}
