"""Gera a planilha preenchida (.xlsx) com os dados estruturados do processo."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HEAD = PatternFill("solid", fgColor="0B6A94")


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list], widths: list[int], first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), HEAD
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    return ws


def build_xlsx(doc: dict) -> bytes:
    m = doc["metadata"] or {}
    wb = Workbook()
    _sheet(wb, "Resumo", ["Campo", "Valor"], [
        ["Arquivo", doc["filename"]],
        ["Nº do processo", m.get("numero_processo") or "não identificado"],
        ["Vara/Juízo", "; ".join(v["nome"] for v in m.get("varas", [])) or "não identificado"],
        ["Total de páginas", doc["pages_total"]],
        ["Páginas com OCR", doc["pages_ocr"]],
        ["Páginas sem texto recuperável", ", ".join(map(str, m.get("paginas_sem_texto", []))) or "nenhuma"],
        *[[f"Total – {k}", v] for k, v in (m.get("total_por_categoria") or {}).items()],
    ], [34, 70], first=True)
    ws = wb["Resumo"]
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value).startswith("Total –"):
            row[1].number_format = '"R$" #,##0.00'
    _sheet(wb, "Partes", ["Papel", "Nome", "Página"],
           [[p["papel"], p["nome"], p["pagina"]] for p in m.get("partes", [])], [20, 60, 10])
    ids = [["CNPJ", x["valor"], ", ".join(map(str, x["paginas"])), "sim" if x["valido"] else "NÃO (dígito inválido/OCR?)"]
           for x in m.get("cnpjs", [])]
    ids += [["CPF", x["valor"], ", ".join(map(str, x["paginas"])), "sim" if x["valido"] else "NÃO (dígito inválido/OCR?)"]
            for x in m.get("cpfs", [])]
    ids += [["Nº processo", x["valor"], ", ".join(map(str, x["paginas"])), "-"] for x in m.get("numeros_processo", [])]
    _sheet(wb, "Documentos", ["Tipo", "Número", "Páginas", "Válido"], ids, [14, 30, 24, 30])
    ws = _sheet(wb, "Valores", ["Categoria", "Valor (R$)", "Página", "Contexto"],
                [[v["categoria"], v["valor"], v["pagina"], v["contexto"]] for v in m.get("valores", [])],
                [24, 16, 10, 100])
    for row in ws.iter_rows(min_row=2, min_col=2, max_col=2):
        row[0].number_format = '"R$" #,##0.00'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
