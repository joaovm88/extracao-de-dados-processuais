"""Importa o export 'Mcfly' (cadastro dos processos do devedor) de um .xlsx."""
from typing import BinaryIO

from openpyxl import load_workbook

from .schemas import MCFLY_MAP


def _fmt(v):
    if v is None or v == "-":
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_mcfly(f: BinaryIO) -> list[dict]:
    wb = load_workbook(f, read_only=True, data_only=True)
    ws = wb["Mcfly"] if "Mcfly" in wb.sheetnames else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h else "" for h in next(rows)]
    idx = {k: header.index(v) for k, v in MCFLY_MAP.items() if v in header}
    if "numero" not in idx:
        raise ValueError("Cabeçalho 'Número do processo CNJ' não encontrado.")
    out = []
    for r in rows:
        rec = {k: _fmt(r[i]) for k, i in idx.items() if i < len(r)}
        if rec.get("numero"):
            out.append(rec)
    return out
