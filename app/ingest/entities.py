"""Extração de dados estruturados (CNPJ, CPF, nº CNJ, valores, eventos) com contexto e página."""
import re
import unicodedata
from collections import defaultdict
from typing import Iterable

RE_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
RE_CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
RE_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
RE_MONEY = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})|\d+,\d{2})")
RE_PARTE = re.compile(
    r"^\s*(Autor(?:a)?|Réu|Ré|Requerente|Requerido(?:a)?|Exequente|Executado(?:a)?|Impetrante|Impetrado(?:a)?|"
    r"Apelante|Apelado(?:a)?|Agravante|Agravado(?:a)?|Embargante|Embargado(?:a)?|Reclamante|Reclamad[oa])\s*[:\-]\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
RE_VARA = re.compile(r"^\s*(?:\d+[ªaº]?\s*)?(?:VARA|Vara|JUÍZO|Juízo)[^\n]{3,100}$", re.MULTILINE)

# categoria -> palavras-chave (sem acento, minúsculas)
CATEGORIES = [
    ("Bloqueio SISBAJUD", ("sisbajud", "bacenjud", "bloqueio", "bloqueado", "bloqueada", "constricao")),
    ("DARF / Tributo", ("darf", "receita federal", "tributo", "recolhimento", "recolhido")),
    ("Custas", ("custas", "preparo", "gru")),
    ("Honorários", ("honorarios", "sucumbencia")),
    ("Valor da causa", ("valor da causa", "da causa")),
    ("Condenação / Dívida", ("condenacao", "condenado", "debito", "divida", "principal", "execucao")),
    ("Depósito / Pagamento", ("deposito", "depositado", "pagamento", "comprovante", "alvara")),
]


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    return strip_accents(s).lower()


def parse_brl(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def fmt_brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def categorize(context: str) -> str:
    c = norm(context)
    for name, kws in CATEGORIES:
        if any(k in c for k in kws):
            return name
    return "Outros"


def valid_cnpj(c: str) -> bool:
    d = [int(x) for x in re.sub(r"\D", "", c)]
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for n in (12, 13):
        w = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
        s = sum(a * b for a, b in zip(d[:n], w))
        if d[n] != (0 if s % 11 < 2 else 11 - s % 11):
            return False
    return True


def valid_cpf(c: str) -> bool:
    d = [int(x) for x in re.sub(r"\D", "", c)]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        s = sum(a * b for a, b in zip(d[:n], range(n + 1, 1, -1)))
        if d[n] != (0 if s % 11 < 2 else 11 - s % 11):
            return False
    return True


def extract(pages: Iterable[tuple[int, str]]) -> dict:
    """pages: iterável de (número, texto). Retorna o dicionário de metadados do processo."""
    numeros: dict[str, list[int]] = defaultdict(list)
    cnpjs: dict[str, list[int]] = defaultdict(list)
    cpfs: dict[str, list[int]] = defaultdict(list)
    partes: list[dict] = []
    valores: list[dict] = []
    varas: dict[str, int] = {}

    for num, text in pages:
        for m in RE_CNJ.finditer(text):
            numeros[m.group()].append(num)
        for m in RE_CNPJ.finditer(text):
            cnpjs[m.group()].append(num)
        for m in RE_CPF.finditer(text):
            cpfs[m.group()].append(num)
        for m in RE_PARTE.finditer(text):
            papel, nome = m.group(1).strip().title(), m.group(2).strip()
            if len(nome) > 3 and not any(p["papel"] == papel and p["nome"] == nome for p in partes):
                partes.append({"papel": papel, "nome": nome, "pagina": num})
        for m in RE_VARA.finditer(text):
            varas.setdefault(m.group().strip(), num)
        for m in RE_MONEY.finditer(text):
            ctx = " ".join(text[max(0, m.start() - 120): m.end() + 60].split())
            valores.append(
                {
                    "valor": parse_brl(m.group(1)),
                    "valor_formatado": fmt_brl(parse_brl(m.group(1))),
                    "categoria": categorize(ctx),
                    "pagina": num,
                    "contexto": ctx,
                }
            )

    def listing(d: dict, validator=None) -> list[dict]:
        return [
            {"valor": k, "paginas": sorted(set(v)), "valido": validator(k) if validator else True}
            for k, v in d.items()
        ]

    por_categoria: dict[str, float] = defaultdict(float)
    for v in valores:
        por_categoria[v["categoria"]] += v["valor"]

    # número do processo "principal" = o mais citado
    principal = max(numeros, key=lambda k: len(numeros[k])) if numeros else None
    return {
        "numero_processo": principal,
        "numeros_processo": listing(numeros),
        "varas": [{"nome": k, "pagina": v} for k, v in varas.items()],
        "partes": partes,
        "cnpjs": listing(cnpjs, valid_cnpj),
        "cpfs": listing(cpfs, valid_cpf),
        "valores": valores,
        "total_por_categoria": dict(por_categoria),
    }
