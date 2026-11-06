# app/template.py

from typing import Dict, Any, List, Tuple

# Usamos uma Tupla de Dicionários para garantir imutabilidade na ordem das colunas
CORPORATE_COLUMNS: Tuple[Dict[str, str], ...] = (
    {"key": "numero_processo", "label": "Nº do Processo (CNJ)"},
    {"key": "tipo_acao", "label": "Tipo / Categoria"},
    {"key": "parte_autora", "label": "Exequente / Autor"},
    {"key": "parte_re", "label": "Executado / Réu"},
    {"key": "cpf_cnpj", "label": "CPF / CNPJ"},
    {"key": "valor_causa_divida", "label": "Valor da Dívida / Causa"},
    {"key": "sisbajud_bloqueado", "label": "Bloqueio SISBAJUD?"},
    {"key": "valor_sisbajud", "label": "Valor Bloqueado (R$)"},
    {"key": "darf_recolhido", "label": "DARF / Garantia"},
    {"key": "resumo_status", "label": "Status / Última Decisão"},
)


def get_column_headers() -> List[str]:
    """Retorna apenas os títulos legíveis para o cabeçalho do Excel/CSV."""
    return [col["label"] for col in CORPORATE_COLUMNS]


def get_column_keys() -> List[str]:
    """Retorna as chaves técnicas para mapeamento O(1) no dicionário."""
    return [col["key"] for col in CORPORATE_COLUMNS]


def map_extraction_to_row(process_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recebe o dicionário extraído do PDF (via entities.py + llm.py) e
    normaliza em uma linha pronta para a planilha corporativa e para o Front-end.
    """
    extracted = process_data.get("extracted_fields", {})
    citations = process_data.get("citations", {})

    row: Dict[str, Any] = {
        "id": process_data.get("id"),
        "filename": process_data.get("filename"),
        "status": process_data.get("status", "concluido"),
        "fields": {},
        "citations": citations,  # Ex: {"valor_sisbajud": "Pág. 38"}
    }

    # Complexidade O(K), onde K é o número fixo de colunas corporativas
    for col in CORPORATE_COLUMNS:
        key = col["key"]
        row["fields"][key] = extracted.get(key, "Não identificado")

    return row