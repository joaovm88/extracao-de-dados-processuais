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
_FAZENDA = ("fazenda nacional", "ministerio da fazenda", "ministerio da economia", "uniao", "pgfn", "procuradoria", "ministerio publico",
            "inss", "instituto nacional do seguro", "ibama", "caixa economica")


def _n(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def _fmt(v):
    if v is None or (isinstance(v, str) and v.strip() in ("", "-")):  # o Mcfly usa " - " para campo vazio
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.2f}"
    return str(v).strip() or None


def _linhas(f: BinaryIO) -> list[dict]:
    """Todas as linhas com nº de processo, sem juntar repetidas. Cabeçalhos são reconhecidos sem depender de acento/caixa."""
    wb = load_workbook(f, data_only=True)  # sem read_only: o export do SAJ não declara o tamanho da aba e viria só 1 célula
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


_DOC = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/\d{4}-?\d{2}|\d{3}\.?\d{3}\.?\d{3}-?\d{2}")


def _partes(rec: dict) -> dict:
    """Partes do processo fora a Fazenda: chave (CNPJ/CPF só com dígitos, ou o nome) -> nome para exibir."""
    out = {}
    for polo in ("polo_ativo", "polo_passivo"):
        for linha in str(rec.get(polo) or "").split("\n"):
            linha = linha.strip(" ;")
            if not linha or any(fz in _n(linha) for fz in _FAZENDA):
                continue
            m = _DOC.search(linha)
            nome = re.sub(r"\s*-\s*$", "", linha[:m.start()] if m else linha).strip() or linha
            out[re.sub(r"\D", "", m.group(0)) if m else _n(nome)] = nome
    return out


def digitos(numero) -> str:
    return re.sub(r"\D", "", str(numero or ""))


def analisar(f: BinaryIO, nome_caso: str, existentes: Optional[dict] = None) -> dict:
    """Conferência antes de importar. `existentes`: nº -> {'oculto': bool} dos processos que já estão no caso.

    Os processos são reunidos em GRUPOS DE PARTES: dois processos ficam no mesmo grupo quando têm alguma parte em comum
    (mesmo CNPJ/CPF), direta ou indiretamente. Um grupo 'é do caso' quando alguma parte cita o nome do caso ou quando algum
    processo dele já está no caso. Processos de grupos sem essa ligação são os candidatos a 'vieram por engano'."""
    originais = {digitos(k): k for k in (existentes or {})}
    existentes = {digitos(k): v for k, v in (existentes or {}).items()}
    linhas = _linhas(f)
    grupos: dict[str, list[dict]] = {}
    for l in linhas:
        grupos.setdefault(l["numero"], []).append(l)
    recs = {n: _juntar(ls) for n, ls in grupos.items()}
    partes = {n: _partes(r) for n, (r, _) in recs.items()}

    # componentes conexos por parte em comum
    pai = {n: n for n in recs}

    def raiz(x):
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x
    dono: dict = {}
    for n, ps in partes.items():
        for chave in ps:
            if chave in dono:
                pai[raiz(n)] = raiz(dono[chave])
            else:
                dono[chave] = n
    membros: dict[str, list[str]] = {}
    for n in recs:
        membros.setdefault(raiz(n), []).append(n)

    chave_nome = _tokens(nome_caso)
    tem_caso = bool(existentes)
    clusters = []
    for i, (_, ns) in enumerate(sorted(membros.items(), key=lambda kv: -len(kv[1])), start=1):
        cont: Counter = Counter()
        nomes: dict = {}
        for n in ns:
            cont.update(partes[n].keys())
            nomes.update(partes[n])
        cita = any(chave_nome & set(_n(nm).split()) for nm in nomes.values())
        no_caso = sum(1 for n in ns if digitos(n) in existentes)
        clusters.append({"id": i, "processos": len(ns), "numeros": ns, "partes": len(cont),
                         "principais": list(dict.fromkeys(nomes[k] for k, _ in cont.most_common(8)))[:4], "cita_caso": cita, "no_caso": no_caso,
                         "do_caso": cita or no_caso > 0})
    if not any(c["do_caso"] for c in clusters) and clusters:  # nada liga ao caso (caso novo, nome diferente): vale o maior grupo
        clusters[0]["do_caso"] = True
    de = {n: c for c in clusters for n in c["numeros"]}

    procs = []
    for n, (rec, conflitos) in recs.items():
        ex = existentes.get(digitos(n))
        c = de[n]
        suspeito = not c["do_caso"]
        ps = list(dict.fromkeys(partes[n].values()))  # matriz e filial têm o mesmo nome: mostra uma vez
        procs.append({**{k: rec.get(k) for k in ("numero", "classe", "inscricoes", "somatorio")},
                      "partes": ps[:2], "partes_mais": max(len(ps) - 2, 0), "grupo": c["id"],
                      "linhas": len(grupos[n]), "conflitos": conflitos, "no_caso": ex is not None, "oculto": bool(ex and ex.get("oculto")),
                      "suspeito": suspeito,
                      # sugestão: entra o que já está no caso e o que é de um grupo ligado ao caso; oculto e sem ligação ficam desmarcados
                      "marcar": (ex is not None or not suspeito) and not (ex and ex.get("oculto"))})
    procs.sort(key=lambda p: (not p["suspeito"], not p["conflitos"], p["grupo"], p["numero"]))
    for c in clusters:
        c.pop("numeros")
    return {"linhas": len(linhas), "processos": procs, "repetidos": sum(1 for p in procs if p["linhas"] > 1),
            "conflitos": sum(1 for p in procs if p["conflitos"]), "suspeitos": sum(1 for p in procs if p["suspeito"]),
            "novos": sum(1 for p in procs if not p["no_caso"]), "devedor": sorted(chave_nome), "grupos": clusters, "caso_vazio": not tem_caso,
            "fora_do_mcfly": sorted(originais[k] for k in existentes if k not in {digitos(n) for n in recs})}
