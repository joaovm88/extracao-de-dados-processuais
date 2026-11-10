"""Importa o export 'Mcfly' (cadastro dos processos do devedor, gerado no SAJ) de um .xlsx.

Além de ler as linhas, `analisar` prepara a conferência antes de importar: junta as linhas repetidas do mesmo processo,
aponta valores divergentes e marca os processos que parecem não ser do devedor do caso."""
import re
import unicodedata
from collections import Counter
from typing import BinaryIO, Optional

from openpyxl import load_workbook

from .schemas import MCFLY_MAP, fmt_moeda_br

_VALORES = ("somatorio", "valor_causa")  # colunas em R$
_GENERICAS = {"grupo", "ltda", "eireli", "epp", "empresa", "empresas", "comercio", "industria", "servicos", "holding", "participacoes",
              "sociedade", "companhia", "brasil", "nacional", "fazenda", "uniao", "federal", "intermediacao", "negocios", "caso"}
_FAZENDA = ("fazenda nacional", "uniao", "pgfn", "procuradoria", "ministerio publico", "inss", "ibama", "caixa economica")


def _n(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def _fmt(v):
    if v is None or v == "-":
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.2f}"
    return str(v).strip() or None


def _linhas(f: BinaryIO) -> list[dict]:
    """Todas as linhas com nº de processo, sem juntar repetidas. Cabeçalhos são reconhecidos sem depender de acento/caixa."""
    wb = load_workbook(f, read_only=True, data_only=True)
    ws = wb["Mcfly"] if "Mcfly" in wb.sheetnames else wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    idx: dict = {}
    for cab in rows:  # o cabeçalho pode não estar na 1ª linha
        nomes = [_n(h) for h in cab]
        idx = {k: nomes.index(_n(v)) for k, v in MCFLY_MAP.items() if _n(v) in nomes}
        if "numero" in idx:
            break
    if "numero" not in idx:
        raise ValueError("Cabeçalho 'Número do processo CNJ' não encontrado.")
    out = []
    for r in rows:
        rec = {k: _fmt(r[i]) for k, i in idx.items() if i < len(r)}
        if not rec.get("numero"):
            continue
        for k in _VALORES:
            if rec.get(k):
                rec[k] = fmt_moeda_br(rec[k])
        out.append(rec)
    return out


def _juntar(linhas: list[dict]) -> tuple[dict, dict]:
    """Várias linhas do mesmo processo -> um registro. Inscrições diferentes são reunidas; para os demais campos vale o
    primeiro valor, e as divergências são devolvidas para o analista conferir."""
    rec: dict = {}
    conflitos: dict = {}
    for k in {k for l in linhas for k in l}:
        vals = list(dict.fromkeys(l[k] for l in linhas if l.get(k)))  # distintos, na ordem
        if not vals:
            continue
        if k == "inscricoes":
            rec[k] = "\n".join(vals)
        else:
            rec[k] = vals[0]
            if len(vals) > 1:
                conflitos[k] = vals
    return rec, conflitos


def read_mcfly(f: BinaryIO) -> list[dict]:
    """Um registro por processo (linhas repetidas já reunidas)."""
    grupos: dict[str, list[dict]] = {}
    for l in _linhas(f):
        grupos.setdefault(l["numero"], []).append(l)
    return [_juntar(ls)[0] for ls in grupos.values()]


def _tokens(nome: str) -> set:
    return {t for t in _n(nome).split() if len(t) >= 4 and t not in _GENERICAS and not t.isdigit()}


def _partes(rec: dict) -> str:
    return _n(f"{rec.get('polo_ativo') or ''} {rec.get('polo_passivo') or ''}")


def analisar(f: BinaryIO, nome_caso: str, existentes: Optional[dict] = None) -> dict:
    """Conferência antes de importar. `existentes`: nº -> {'oculto': bool} dos processos que já estão no caso.

    Um processo é 'suspeito' (pode ter vindo por engano) quando nenhum polo cita o devedor do caso. O devedor é reconhecido
    pelas palavras do nome do caso; se o nome não ajudar, pela parte (fora a Fazenda) que mais aparece na planilha."""
    existentes = existentes or {}
    linhas = _linhas(f)
    grupos: dict[str, list[dict]] = {}
    for l in linhas:
        grupos.setdefault(l["numero"], []).append(l)
    recs = {n: _juntar(ls) for n, ls in grupos.items()}

    chave = _tokens(nome_caso)
    if not any(chave & set(_partes(r).split()) for r, _ in recs.values()):  # o nome do caso não bate com nada: usa a parte mais comum
        cont: Counter = Counter()
        for r, _ in recs.values():
            for polo in ("polo_ativo", "polo_passivo"):
                p = _n(r.get(polo))
                if p and not any(fz in p for fz in _FAZENDA):
                    cont.update({t for t in p.split() if len(t) >= 4 and t not in _GENERICAS and not t.isdigit()})
        chave = {cont.most_common(1)[0][0]} if cont else set()

    procs = []
    for n, (rec, conflitos) in recs.items():
        ex = existentes.get(n)
        suspeito = bool(chave) and not (chave & set(_partes(rec).split()))
        procs.append({**{k: rec.get(k) for k in ("numero", "polo_ativo", "polo_passivo", "classe", "inscricoes", "somatorio")},
                      "linhas": len(grupos[n]), "conflitos": conflitos, "no_caso": ex is not None, "oculto": bool(ex and ex.get("oculto")),
                      "suspeito": suspeito,
                      # sugestão: importa o que já está no caso; o que é novo E suspeito fica desmarcado
                      "marcar": not (suspeito and ex is None) and not (ex and ex.get("oculto"))})
    procs.sort(key=lambda p: (not p["suspeito"], not p["conflitos"], p["no_caso"], p["numero"]))
    return {"linhas": len(linhas), "processos": procs, "repetidos": sum(1 for p in procs if p["linhas"] > 1),
            "conflitos": sum(1 for p in procs if p["conflitos"]), "suspeitos": sum(1 for p in procs if p["suspeito"]),
            "novos": sum(1 for p in procs if not p["no_caso"]), "devedor": sorted(chave),
            "fora_do_mcfly": sorted(n for n in existentes if n not in recs)}
