"""Layout das abas da planilha de relatório (cabeçalhos copiados do modelo) e campos extraídos por IA."""
from dataclasses import dataclass, field
from typing import Optional

NAO_ENCONTRADO = "Informação não encontrada no documento"

SIM_NAO = ["SIM", "NÃO"]
# Listas fechadas. Os valores de EXIT. GARANTIA além de IMÓVEL e BACEN C/DJE são suposição: ajuste aqui.
EXIST_GARANTIA = ["IMÓVEL", "VEÍCULO", "BACEN C/DJE", "FIANÇA/SEGURO GARANTIA", "OUTROS", "NÃO HÁ"]
TENT_ALIEN = ["SIM", "NÃO", "NÃO SE APLICA"]


@dataclass
class Field:
    key: str
    header: str
    desc: str = ""
    options: Optional[list] = None  # lista fechada
    source: str = "ai"  # "ai" | "mcfly" | "system"
    fmt: str = ""  # "data" (dd/mm/aaaa) | "moeda" (R$ 1.234,56): padronizado pelo código, não pela IA


@dataclass
class Sheet:
    key: str
    name: str
    prompt: str
    fields: list[Field] = field(default_factory=list)

    @property
    def ai_fields(self) -> list[Field]:
        return [f for f in self.fields if f.source == "ai"]


_OBS_EF = ("Cronologia dos atos processuais: UMA LINHA POR ATO, em ordem cronológica crescente, no formato "
           "'<rótulo do trecho> - dd/mm/aaaa - descrição' (ex.: 'Evento 12, pg. 1 - 05/10/2006 - Auto de avaliação do imóvel…'). Só a lista.")
_HON = "Valor da condenação em honorários, em reais (ex.: R$ 5.000,00), se houver; senão null."
_BEN = "Beneficiário dos honorários (ex.: União/PGFN, parte contrária), se houver; senão null."

ARQUIVADA_40 = ["Não", "< 3 anos", "> 3 anos"]  # coluna "Arquivada 40 LEF?"

SHEETS: dict[str, Sheet] = {
    "execucoes_fiscais": Sheet("execucoes_fiscais", "Execuções Fiscais", "execucoes_fiscais", [
        Field("numero", "Número do processo judicial", source="mcfly"),
        Field("polo_passivo", "Polo Passivo", source="mcfly"),
        Field("classe", "Classe", source="mcfly"),
        Field("juizo", "Juízo", source="mcfly"),
        Field("procuradoria", "Procuradoria Responsável", source="mcfly"),
        Field("inscricoes", "Inscrições processo principal", source="mcfly"),
        Field("somatorio", "Somatório inscrições (processo principal e vinculados)", source="mcfly"),
        Field("digitalizado", "PROCESSO DIGITALIZADO", options=SIM_NAO, source="system"),
        Field("garantia", "EXIT. GARANTIA", "Tipo de garantia existente nos autos.", EXIST_GARANTIA),
        Field("valor_garantia", "VALOR AVALIAÇÃO OU DEPÓSITO", "Valor da avaliação do bem ou do depósito/bloqueio, em reais (ex.: R$ 746.019,60), o mais recente.", fmt="moeda"),
        Field("data_garantia", "DATA AVAL. OU DEP.", "Data (dd/mm/aaaa) da avaliação/depósito a que se refere o valor.", fmt="data"),
        Field("tent_alien", "TENT. ALIEN.", "Houve tentativa de alienação (hasta/leilão)?", TENT_ALIEN),
        Field("excecao_pendente", "EXCEÇÃO PRÉ-EXECUTIVIDADE pendente julgamento?", "", SIM_NAO),
        Field("arquivada_40", "Arquivada 40 LEF?",
              "Execução suspensa/arquivada nos termos do art. 40 da LEF? 'Não' = não está; '< 3 anos' = está, e a decisão de "
              "suspensão/arquivamento tem menos de 3 anos; '> 3 anos' = está há mais de 3 anos. Conte da data da decisão até a data de hoje.",
              ARQUIVADA_40),
        Field("redirecionamento", "Há pedido de redirecionamento? (Se sim preencher a próxima coluna)", "", SIM_NAO),
        Field("corresponsaveis", "Corresponsáveis (Nome e CNPJ/CPF)",
              "Somente se houver pedido de redirecionamento. UMA pessoa ou empresa POR LINHA, no formato 'NOME - CPF: 000.000.000-00' "
              "ou 'NOME - CNPJ: 00.000.000/0000-00'. Sem documento nos autos: 'NOME - documento não informado'."),
        Field("observacoes", "Observações relavantes sobre o atual estágio da EF", _OBS_EF),
    ]),
    "outras_acoes": Sheet("outras_acoes", "Outras Ações", "outras_acoes", [
        Field("numero", "Número do processo judicial", source="mcfly"),
        Field("polo_ativo", "Polo Ativo", source="mcfly"),
        Field("polo_passivo", "Polo Passivo", source="mcfly"),
        Field("classe", "Classe", source="mcfly"),
        Field("juizo", "Juízo", source="mcfly"),
        Field("procuradoria", "Procuradoria Responsável", source="mcfly"),
        Field("digitalizado", "Processo Digitalizado", options=SIM_NAO, source="system"),
        Field("debito_discutido", "Débito (PA) ou inscrição discutida", "A ação discute débito/inscrição específico?", SIM_NAO),
        Field("transito", "Há decisão transitada em julgado", "", SIM_NAO),
        Field("honorarios", "VALOR CONDENAÇÃO HONORÁRIOS ", _HON, fmt="moeda"),
        Field("beneficiario", "Beneficiário dos honorários", _BEN),
        Field("observacoes", "Observações relavantes sobre o atual estágio", _OBS_EF),
    ]),
    "embargos": Sheet("embargos", "Embargos à Execução", "outras_acoes", [
        Field("numero", "Número do processo judicial", source="mcfly"),
        Field("polo_ativo", "Polo Ativo", source="mcfly"),
        Field("polo_passivo", "Polo Passivo", source="mcfly"),
        Field("classe", "Classe", source="mcfly"),
        Field("juizo", "Juízo", source="mcfly"),
        Field("procuradoria", "Procuradoria Responsável", source="mcfly"),
        Field("digitalizado", "Processo digitalizado?", options=SIM_NAO, source="system"),
        Field("transito", "Há decisão transitada em julgado?", "", SIM_NAO),
        Field("honorarios", "VALOR CONDENAÇÃO HONORÁRIOS ", _HON, fmt="moeda"),
        Field("beneficiario", "Beneficiário dos honorários", _BEN),
        Field("observacoes", "Observações relavantes sobre o atual estágio", _OBS_EF),
    ]),
}

RELATORIO_HEADERS = ["Número do Processo CNJ", "Polo Ativo", "Polo Passivo", "Classe", "Juízo",
                     "Procuradoria Responsável", "Data do Protocolo"]
BENS_HEADERS = ["Matrícula", "Características do Imóvel", "Proprietário", "Ato (Nº R./Av.)", "Gravames",
                "Resumo ", "Situação (Ativo/Cancelado pela Av. X)"]
FRAUDE_HEADERS = ["NÚMERO DO PROCESSO", "CLASSE PROCESSUAL", "TIPO DE FRAUDE ALEGADA", "ANÁLISE DO PEDIDO", "VALOR DA CAUSA"]
# Listas fechadas da aba "Alegações de Fraude" (iguais às da planilha do analista)
FRAUDE_TIPOS = ["FRAUDE À EXECUÇÃO", "SIMULAÇÃO", "PATRIMONIAL", "SUCESSÃO DE FATO", "OUTRAS FRAUDES"]
FRAUDE_ANALISES = ["DEFERIDO", "INDEFERIDO", "PARCIALMENTE DEFERIDO", "PENDENTE DE ANÁLISE"]
# Colunas opcionais que o modelo do analista pode ter nas abas de processos (cabeçalho normalizado -> chave)
EXTRA_COLS = {"n": "ordem", "no": "ordem", "n o": "ordem", "ordem": "ordem", "item": "ordem", "seq": "ordem", "sequencia": "ordem",
              "numero de ordem": "ordem", "n de ordem": "ordem", "no de ordem": "ordem",
              "situacao da analise": "situacao_analise", "status da analise": "situacao_analise", "analise manual": "situacao_analise",
              "feito manualmente": "situacao_analise", "forma de analise": "situacao_analise"}
ORDEM_HEADER, SITUACAO_HEADER = "Nº de ordem", "Situação da análise"
DJE_SIEF_HEADERS = ["DEVEDOR", "CPF/CNPJ", "N. PROCESSO JUDICIAL", "REFERÊNCIA", "CÓDIGO DE RECEITA",
                    "VALOR DISPONÍVEL", "Valor total no processo"]

# Mapeamento Mcfly (cabeçalho do export) -> chave interna
MCFLY_MAP = {
    "numero": "Número do processo CNJ", "polo_ativo": "Polo ativo", "polo_passivo": "Polo passivo",
    "classe": "Classe SAJ", "juizo": "Juízo", "procuradoria": "Procuradoria responsável",
    "data_protocolo": "Data do protocolo", "inscricoes": "Inscrições processo principal",
    "somatorio": "Somatório inscrições (processo principal e vinculados)", "valor_causa": "Valor da causa",
}


def classify(classe: str) -> tuple[str, str]:
    """Classe do processo -> (aba, prompt)."""
    c = (classe or "").lower()
    if "embargos" in c:
        return "embargos", "outras_acoes"
    if "execução fiscal" in c or "execucao fiscal" in c:
        return "execucoes_fiscais", "execucoes_fiscais"
    if any(w in c for w in ("apela", "agravo", "recurso", "remessa", "acórdão")):
        return "outras_acoes", "recursos"
    return "outras_acoes", "outras_acoes"


# Tipo escolhido pelo analista no upload -> (rótulo, aba, prompt). Tem prioridade sobre a classe do Mcfly.
TIPOS = {
    "execucao_fiscal": ("Execução Fiscal", "execucoes_fiscais", "execucoes_fiscais"),
    "embargos": ("Embargos à Execução", "embargos", "outras_acoes"),
    "apelacao": ("Apelação / Recurso", "outras_acoes", "recursos"),
    "agravo": ("Agravo de Instrumento", "outras_acoes", "recursos"),
    "outras_acoes": ("Outras ações (MS, anulatória, cautelar…)", "outras_acoes", "outras_acoes"),
}
SISTEMAS = {"eproc": "eproc", "pje": "PJe"}


def resolve_tipo(tipo: str | None, classe: str) -> tuple[str, str]:
    """(aba, prompt): tipo escolhido > classe do cadastro > padrão."""
    if tipo in TIPOS:
        return TIPOS[tipo][1], TIPOS[tipo][2]
    return classify(classe)


def manual_text(proc: dict) -> str:
    """Texto que a planilha mostra no campo de observações de um processo feito manualmente."""
    nota = (proc.get("manual_note") or "").strip()
    return "Processo analisado manualmente." + (f" {nota}" if nota else "")


def situacao_analise(proc: dict) -> str:
    """Texto da coluna 'Situação da análise' na planilha exportada."""
    if proc.get("manual"):
        return manual_text(proc).replace("Processo analisado manualmente", "Feito manualmente")
    if not proc.get("result"):
        return ""
    return "Revisado" if proc.get("reviewed") else "A revisar (extraído pela IA)"


def item_status(item: dict) -> str:
    """Itens de Outros Bens / Alegações de Fraude: 'sugerido' (IA), 'autorizado' (analista) ou 'descartado'."""
    st = item.get("_st")
    return st if st in ("sugerido", "autorizado", "descartado") else "sugerido"


def itens_autorizados(proc: dict, lista: str) -> list[dict]:
    """Só o que o analista autorizou vai para as abas da planilha. Processo feito manualmente ou oculto não leva itens."""
    if proc.get("manual") or proc.get("hidden"):
        return []
    return [i for i in ((proc.get("result") or {}).get(lista) or []) if item_status(i) == "autorizado"]


# ---------- padronização de valores (feita pelo código): datas dd/mm/aaaa, valores em R$, lista do art. 40 ----------
import re as _re
import unicodedata as _ud

_MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}


def fmt_data_br(v) -> Optional[str]:
    """Qualquer data reconhecível -> dd/mm/aaaa (2018-09-28, 28.09.2018, 28-9-18, 28 de setembro de 2018). O resto fica como está."""
    if v in (None, ""):
        return None
    t = str(v).strip()
    m = _re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", t)
    if m:
        a, mes, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _re.fullmatch(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2}|\d{4})", t)
        if m:
            d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
            a += (2000 if a < 50 else 1900) if a < 100 else 0
        else:
            m = _re.fullmatch(r"(\d{1,2})º?\s+de\s+([a-zç]+)\.?\s+de\s+(\d{4})", t.lower())
            if not m or m.group(2)[:3] not in _MESES:
                return t
            d, mes, a = int(m.group(1)), _MESES[m.group(2)[:3]], int(m.group(3))
    return f"{d:02d}/{mes:02d}/{a:04d}" if 1 <= d <= 31 and 1 <= mes <= 12 else t


def fmt_moeda_br(v) -> Optional[str]:
    """Valor só com número (com ou sem R$) -> 'R$ 1.234,56'. Texto com outras palavras fica como está."""
    if v in (None, ""):
        return None
    t = str(v).strip()
    n = _re.sub(r"(?i)^r\$\s*", "", t).strip()
    if not _re.fullmatch(r"\d[\d.,]*", n):
        return t
    n = n.rstrip(".,")
    if "," in n and "." in n:  # o último separador é o decimal
        dec = "," if n.rfind(",") > n.rfind(".") else "."
        inteiro, frac = n.rsplit(dec, 1)
    elif "," in n:
        inteiro, frac = n.rsplit(",", 1) if len(n.rsplit(",", 1)[1]) <= 2 and n.count(",") == 1 else (n, "")
    elif "." in n:
        inteiro, frac = n.rsplit(".", 1) if len(n.rsplit(".", 1)[1]) <= 2 and n.count(".") == 1 else (n, "")
    else:
        inteiro, frac = n, ""
    inteiro = _re.sub(r"\D", "", inteiro) or "0"
    grupos = f"{int(inteiro):,}".replace(",", ".")
    return f"R$ {grupos},{(frac + '00')[:2]}"


def _plano(t) -> str:
    return "".join(c for c in _ud.normalize("NFD", str(t or "")) if _ud.category(c) != "Mn").lower().strip()


def fmt_arquivada(v, estrito: bool = False) -> Optional[str]:
    """Encaixa a resposta na lista 'Não' / '< 3 anos' / '> 3 anos'. Um 'Sim' sem o prazo não cabe: com `estrito`, vira vazio."""
    if v in (None, ""):
        return None
    t = _plano(v)
    if t in ("nao", "n", "nao ha", "nao arquivada"):
        return "Não"
    if "<" in t or "menos" in t or "inferior" in t:
        return "< 3 anos"
    if ">" in t or "mais" in t or "superior" in t:
        return "> 3 anos"
    return None if estrito else str(v)


def normalize_fields(sheet: Sheet, fields: dict, estrito: bool = False) -> dict:
    """Devolve os campos com datas, valores e a lista do art. 40 no formato da planilha."""
    out = dict(fields)
    for f in sheet.fields:
        v = out.get(f.key)
        if v in (None, ""):
            continue
        if f.fmt == "data":
            out[f.key] = fmt_data_br(v)
        elif f.fmt == "moeda":
            out[f.key] = fmt_moeda_br(v)
        elif f.key == "arquivada_40":
            out[f.key] = fmt_arquivada(v, estrito)
    return out
