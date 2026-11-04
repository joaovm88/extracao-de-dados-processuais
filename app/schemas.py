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


@dataclass
class Sheet:
    key: str
    name: str
    prompt: str
    fields: list[Field] = field(default_factory=list)

    @property
    def ai_fields(self) -> list[Field]:
        return [f for f in self.fields if f.source == "ai"]


_OBS_EF = "Texto do relatório de status: eventos cronológicos no formato 'Evento N, VOL N, pg. N, ID N - descrição'."
_HON = "Valor da condenação em honorários (R$), se houver; senão null."
_BEN = "Beneficiário dos honorários (ex.: União/PGFN, parte contrária), se houver; senão null."

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
        Field("valor_garantia", "VALOR AVALIAÇÃO OU DEPÓSITO", "Valor da avaliação do bem ou do depósito/bloqueio (R$), o mais recente."),
        Field("data_garantia", "DATA AVAL. OU DEP.", "Data (AAAA-MM-DD) da avaliação/depósito a que se refere o valor."),
        Field("tent_alien", "TENT. ALIEN.", "Houve tentativa de alienação (hasta/leilão)?", TENT_ALIEN),
        Field("excecao_pendente", "EXCEÇÃO PRÉ-EXECUTIVIDADE pendente julgamento?", "", SIM_NAO),
        Field("arquivada_40", "Arquivada 40 LEF?", "Execução arquivada nos termos do art. 40 da LEF?", SIM_NAO),
        Field("redirecionamento", "Há pedido de redirecionamento? (Se sim preencher a próxima coluna)", "", SIM_NAO),
        Field("corresponsaveis", "Corresponsáveis (Nome e CNPJ/CPF)", "Somente se houver pedido de redirecionamento."),
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
        Field("honorarios", "VALOR CONDENAÇÃO HONORÁRIOS ", _HON),
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
        Field("honorarios", "VALOR CONDENAÇÃO HONORÁRIOS ", _HON),
        Field("beneficiario", "Beneficiário dos honorários", _BEN),
        Field("observacoes", "Observações relavantes sobre o atual estágio", _OBS_EF),
    ]),
}

RELATORIO_HEADERS = ["Número do Processo CNJ", "Polo Ativo", "Polo Passivo", "Classe", "Juízo",
                     "Procuradoria Responsável", "Data do Protocolo"]
BENS_HEADERS = ["Matrícula", "Características do Imóvel", "Proprietário", "Ato (Nº R./Av.)", "Gravames",
                "Resumo ", "Situação (Ativo/Cancelado pela Av. X)"]
FRAUDE_HEADERS = ["NÚMERO DO PROCESSO", "CLASSE PROCESSUAL", "TIPO DE FRAUDE ALEGADA", "ANÁLISE DO PEDIDO", "VALOR DA CAUSA"]
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
