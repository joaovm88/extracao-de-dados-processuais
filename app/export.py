"""Exporta o relatório do caso (uma planilha, vários processos) no layout do modelo do analista."""
import io
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .schemas import (BENS_HEADERS, DJE_SIEF_HEADERS, FRAUDE_HEADERS, ORDEM_HEADER, RELATORIO_HEADERS, SHEETS, SITUACAO_HEADER,
                      classify, is_rj, itens_autorizados, manual_text, normalize_fields, situacao_analise)
from .template import item_row

HEAD = PatternFill("solid", fgColor="1351B4")  # azul gov.br
PENDING = PatternFill("solid", fgColor="FFF3CD")  # linha ainda não revisada por analista


def _ws(wb: Workbook, title: str, headers: list[str], widths: Optional[list[int]] = None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, h in enumerate(headers):
        ws.column_dimensions[ws.cell(1, i + 1).column_letter].width = (widths[i] if widths and i < len(widths) else max(18, min(len(h), 40)))
    ws.freeze_panes = "A2"
    return ws


def _add(ws, row: list, pending: bool = False):
    ws.append(row)
    for c in ws[ws.max_row]:
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if pending:
            c.fill = PENDING


def build_case_xlsx(case: dict, processes: list[dict]) -> bytes:
    """processes: linhas da tabela `processes` (cadastro Mcfly + resultado da extração + revisão)."""
    processes = [p for p in processes if not p.get("hidden") and not is_rj(p)]  # ocultos e processos de RJ (relatório próprio) não entram na planilha
    wb = Workbook()
    wb.remove(wb.active)

    rel = _ws(wb, "Relatório", RELATORIO_HEADERS, [28, 40, 40, 26, 40, 22, 16])
    for p in processes:
        m = p.get("mcfly") or {}
        _add(rel, [p["numero"], m.get("polo_ativo"), m.get("polo_passivo"), m.get("classe") or p.get("classe"),
                   m.get("juizo"), m.get("procuradoria"), m.get("data_protocolo")])

    _ws(wb, "DJE-SDJ", ["Preenchida por outro setor"])  # depósitos: vêm de outro setor
    _ws(wb, "DJE-SIEF", DJE_SIEF_HEADERS)

    # as duas últimas colunas são do sistema: nº de ordem na aba e situação da análise (feito manualmente / revisado)
    sheets = {k: _ws(wb, s.name, [f.header for f in s.fields] + [ORDEM_HEADER, SITUACAO_HEADER],
                     [28 if f.key != "observacoes" else 90 for f in s.fields] + [12, 34]) for k, s in SHEETS.items()}
    bens_ws = _ws(wb, "Outros Bens", BENS_HEADERS, [26, 50, 34, 14, 28, 60, 28])
    fraude_ws = _ws(wb, "Alegações de Fraude", FRAUDE_HEADERS, [28, 24, 40, 70, 18])

    for p in processes:
        m = p.get("mcfly") or {}
        res = p.get("result")
        sheet_key = res["sheet"] if res else classify(m.get("classe") or "")[0]
        sheet = SHEETS[sheet_key]
        fields = normalize_fields(sheet, res["fields"]) if res else {"digitalizado": "NÃO"}
        if p.get("manual"):  # registrado na planilha: feito manualmente (nenhum valor da IA)
            fields = {"observacoes": manual_text(p)}
            res = None
        if p.get("digitalizado"):  # escolha do analista (NÃO = processo físico)
            fields["digitalizado"] = p["digitalizado"]
        row = [p["numero"] if f.key == "numero" else (m.get(f.key) if f.source == "mcfly" else fields.get(f.key))
               for f in sheet.fields]
        pend = bool(res) and not p.get("reviewed")
        ws = sheets[sheet_key]
        _add(ws, row + [ws.max_row, situacao_analise(p)], pend)
        anterior = None
        for b in itens_autorizados(p, "bens"):  # só o que o analista autorizou
            v = item_row("bens", p, b, anterior)
            anterior = b
            _add(bens_ws, [v.get(k) for k in ("matricula", "caracteristicas", "proprietario", "ato", "gravames", "resumo", "situacao")])
        for fr in itens_autorizados(p, "fraudes"):
            v = item_row("fraude", p, fr)
            _add(fraude_ws, [v.get(k) for k in ("numero", "classe", "tipo", "analise", "valor_causa")])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
