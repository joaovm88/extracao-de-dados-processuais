"""Planilha-modelo do caso: reconhece abas e colunas pelo cabeçalho e preenche uma CÓPIA da própria planilha.

- Abas reconhecidas (Relatório, Execuções Fiscais, Outras Ações, Embargos, Outros Bens, Alegações de Fraude)
  são preenchidas; as demais (DJE-SDJ, DJE-SIEF, Mcfly, Recuperação Judicial, Filtros…) voltam intactas.
- Processo que já tem linha: só as células VAZIAS são preenchidas (nada digitado é sobrescrito).
- Processo novo: a linha é acrescentada ao final da tabela.
- Colunas e abas que não foram reconhecidas são devolvidas num relatório para o analista conferir."""
import io
import re
import unicodedata
from typing import Optional

from openpyxl import load_workbook
from openpyxl.styles import PatternFill

from .schemas import EXTRA_COLS, SHEETS, classify, itens_autorizados, manual_text, situacao_analise

PENDING = PatternFill("solid", fgColor="FFF3CD")  # linhas ainda não revisadas

BENS_KEYS = {"Matrícula": "matricula", "Características do Imóvel": "caracteristicas", "Proprietário": "proprietario",
             "Ato (Nº R./Av.)": "ato", "Gravames": "gravames", "Resumo": "resumo",
             "Situação (Ativo/Cancelado pela Av. X)": "situacao"}
FRAUDE_KEYS = {"NÚMERO DO PROCESSO": "numero", "CLASSE PROCESSUAL": "classe", "TIPO DE FRAUDE ALEGADA": "tipo",
               "ANÁLISE DO PEDIDO": "analise", "VALOR DA CAUSA": "valor_causa"}
# sinônimos de cabeçalhos do Mcfly aceitos em qualquer aba de processos
_MCFLY_SYN = {"numero do processo cnj": "numero", "numero do processo judicial": "numero", "numero do processo": "numero",
              "polo ativo": "polo_ativo", "polo passivo": "polo_passivo", "classe": "classe", "juizo": "juizo",
              "procuradoria responsavel": "procuradoria", "data do protocolo": "data_protocolo",
              "inscricoes processo principal": "inscricoes", "valor da causa": "valor_causa"}


def norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def _digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def sheet_kind(name: str) -> Optional[str]:
    n = norm(name)
    if n == "relatorio":
        return "relatorio"
    for key, label in (("execucoes_fiscais", "execucoes fiscais"), ("embargos", "embargos"),
                       ("outras_acoes", "outras acoes")):
        if label in n:
            return key
    if "outros bens" in n:
        return "bens"
    if "fraude" in n:
        return "fraude"
    return None


def _header_map(kind: str) -> dict[str, str]:
    """cabeçalho normalizado -> chave interna."""
    if kind == "relatorio":
        return dict(_MCFLY_SYN)
    if kind == "bens":
        return {norm(h): k for h, k in BENS_KEYS.items()}
    if kind == "fraude":
        return {norm(h): k for h, k in FRAUDE_KEYS.items()}
    m = dict(_MCFLY_SYN)
    m.update({norm(f.header): f.key for f in SHEETS[kind].fields})
    m.update(EXTRA_COLS)  # colunas opcionais: nº de ordem e situação da análise (feito manualmente / revisado)
    return m


def _match(header, hmap: dict[str, str]) -> Optional[str]:
    h = norm(header)
    if not h:
        return None
    if h in hmap:
        return hmap[h]
    for known, key in hmap.items():
        if key in ("ordem", "situacao_analise"):
            continue  # opcionais: só por cabeçalho exato  # cabeçalhos longos com complemento ("... (Se sim preencher a próxima coluna)")
        if len(known) >= 12 and (h.startswith(known) or known.startswith(h)):
            return key
    return None


def _find_header_row(ws, hmap) -> Optional[tuple[int, dict[int, str], list[str]]]:
    best = None
    for r in range(1, min(ws.max_row, 8) + 1):
        cols, unknown = {}, []
        for c in range(1, min(ws.max_column, 40) + 1):
            v = ws.cell(r, c).value
            if v in (None, ""):
                continue
            key = _match(v, hmap)
            if key:
                cols[c] = key
            else:
                unknown.append(str(v).strip())
        if len(cols) >= 2 and (best is None or len(cols) > len(best[1])):
            best = (r, cols, unknown)
    return best


def _last_data_row(ws, header_row: int) -> int:
    for r in range(ws.max_row, header_row, -1):
        if any(ws.cell(r, c).value not in (None, "") for c in range(1, min(ws.max_column, 40) + 1)):
            return r
    return header_row


def analyze(data: bytes) -> dict:
    """Quais abas e colunas serão preenchidas e o que não foi reconhecido (para avisar na tela)."""
    wb = load_workbook(io.BytesIO(data))
    out = {"abas": [], "ignoradas": [], "ausentes": [], "dicas": []}
    extras: set = set()
    kinds = {sheet_kind(ws.title) for ws in wb.worksheets}
    nomes = {"execucoes_fiscais": "Execuções Fiscais", "outras_acoes": "Outras Ações", "embargos": "Embargos à Execução",
             "bens": "Outros Bens", "fraude": "Alegações de Fraude"}
    out["ausentes"] = [n for k, n in nomes.items() if k not in kinds]  # processos desse tipo não terão onde entrar
    for ws in wb.worksheets:
        kind = sheet_kind(ws.title)
        if kind is None:
            out["ignoradas"].append(ws.title)  # voltam intactas
            continue
        found = _find_header_row(ws, _header_map(kind))
        if not found:
            out["abas"].append({"aba": ws.title, "preenchida": False,
                                "motivo": "layout diferente do esperado (cabeçalho não encontrado); a aba será mantida sem alterações",
                                "reconhecidas": [], "nao_reconhecidas": []})
            continue
        row, cols, unknown = found
        out["abas"].append({"aba": ws.title, "preenchida": True, "linha_cabecalho": row,
                            "reconhecidas": [str(ws.cell(row, c).value).strip() for c in cols], "nao_reconhecidas": unknown})
        if kind in SHEETS:
            extras.update(cols.values())
    faltam = [n for k, n in (("ordem", "Nº"), ("situacao_analise", "Situação da análise")) if k not in extras]
    if faltam and any(a["preenchida"] for a in out["abas"]):
        out["dicas"] = [f"Colunas opcionais que o modelo não tem: {', '.join(faltam)}. Se você criar uma coluna com esse título nas abas de "
                        "processos, o sistema preenche o nº de ordem e a marcação “Feito manualmente / Revisado”."]
    return out


def _values(kind: str, proc: dict) -> dict:
    m = dict(proc.get("mcfly") or {})
    res = proc.get("result")
    vals = {k: v for k, v in m.items() if v is not None}
    vals["numero"] = proc["numero"]
    if not vals.get("classe") and proc.get("classe"):
        vals["classe"] = proc["classe"]
    if kind in SHEETS:
        vals["situacao_analise"] = situacao_analise(proc)
        if proc.get("manual"):  # feito à mão: só a marcação vai para a planilha (nenhum valor da IA)
            vals["observacoes"] = manual_text(proc)
            return vals
        fields = res["fields"] if res else {"digitalizado": "NÃO"}
        vals.update({k: v for k, v in fields.items() if v not in (None, "")})
    return vals


def item_row(kind: str, proc: dict, item: dict, anterior: Optional[dict] = None) -> dict:
    """Linha de 'Outros Bens' ou 'Alegações de Fraude' a partir de um item autorizado. Em bens, matrícula, características e
    proprietário aparecem só na primeira linha de cada matrícula (como na planilha do analista)."""
    vals = {k: v for k, v in item.items() if not k.startswith("_") and k not in ("citacao",)}
    m = proc.get("mcfly") or {}
    if kind == "fraude":
        vals.pop("resumo", None)
        vals.update(numero=proc["numero"], classe=m.get("classe") or proc.get("classe"))
        vals["valor_causa"] = vals.get("valor_causa") or m.get("valor_causa")
    elif anterior is not None and norm(anterior.get("matricula")) == norm(item.get("matricula")) and item.get("matricula"):
        for k in ("matricula", "caracteristicas", "proprietario"):
            vals.pop(k, None)
    return vals


def fill(data: bytes, processes: list[dict]) -> bytes:
    processes = [p for p in processes if not p.get("hidden")]  # ocultos não entram na planilha
    wb = load_workbook(io.BytesIO(data))
    for ws in wb.worksheets:
        kind = sheet_kind(ws.title)
        if kind is None:
            continue
        hmap = _header_map(kind)
        found = _find_header_row(ws, hmap)
        if not found:
            continue
        hrow, cols, _ = found
        last = _last_data_row(ws, hrow)
        by_key = {k: c for c, k in cols.items()}

        def write(r: int, vals: dict, pend: bool, only_empty: bool) -> None:
            for key, col in by_key.items():
                v = vals.get(key)
                cell = ws.cell(r, col)
                if v in (None, "") or (only_empty and cell.value not in (None, "")):
                    continue
                cell.value = v
                if pend and key not in ("ordem", "situacao_analise"):
                    cell.fill = PENDING

        if kind in ("bens", "fraude"):  # um item por linha, sempre acrescentado
            for p in processes:
                anterior = None
                for item in itens_autorizados(p, "bens" if kind == "bens" else "fraudes"):  # só o que o analista autorizou
                    last += 1
                    vals = item_row(kind, p, item, anterior)
                    anterior = item
                    write(last, vals, False, only_empty=False)
            continue

        num_col = by_key.get("numero")
        existing = {}
        if num_col:
            for r in range(hrow + 1, last + 1):
                d = _digits(ws.cell(r, num_col).value)
                if d:
                    existing.setdefault(d, r)
        for p in processes:
            res = p.get("result")
            sk = res["sheet"] if res else classify((p.get("mcfly") or {}).get("classe") or "")[0]
            if kind not in ("relatorio",) and sk != kind:
                continue  # o processo pertence a outra aba (EF, embargos ou outras ações)
            vals = _values(kind, p)
            pend = bool(res) and not p.get("reviewed") and not p.get("manual")
            row = existing.get(_digits(p["numero"]))
            if row:
                vals["ordem"] = row - hrow
                write(row, vals, pend, only_empty=True)  # só células vazias: nada digitado é sobrescrito
            else:
                last += 1
                vals["ordem"] = last - hrow
                write(last, vals, pend, only_empty=False)
                existing[_digits(p["numero"])] = last
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def registry(data: bytes) -> list[dict]:
    """Processos do caso lidos da própria planilha-modelo: aba 'Mcfly' (export do SAJ) se existir;
    senão a aba 'Relatório' (nº, polos, classe, juízo…). Alimenta a capa e a escolha do prompt por classe."""
    from .mcfly import _fmt, read_mcfly

    wb = load_workbook(io.BytesIO(data), data_only=True)
    if "Mcfly" in wb.sheetnames:
        try:
            rows = read_mcfly(io.BytesIO(data))
            if rows:
                return rows
        except ValueError:  # aba Mcfly sem a coluna do nº do processo
            pass
    for ws in wb.worksheets:
        if sheet_kind(ws.title) != "relatorio":
            continue
        found = _find_header_row(ws, _header_map("relatorio"))
        if not found:
            break
        hrow, cols, _ = found
        out = []
        for r in range(hrow + 1, ws.max_row + 1):
            rec = {key: _fmt(ws.cell(r, c).value) for c, key in cols.items()}
            if rec.get("numero"):
                out.append(rec)
        return out
    return []
