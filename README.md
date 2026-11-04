# Sistema de Análise e Ingestão de Processos Judiciais

Plataforma RAG com processamento assíncrono que preenche a **planilha de relatório do analista** a partir dos PDFs dos processos.

```
Front-end (casos, upload, revisão, chat) → API (FastAPI) → Fila (Redis) → Worker
   OCR + texto nato-digital + quebra semântica → Elasticsearch (lexical + vetorial) + metadados
   → extração estruturada por LLM (prompts versionados em prompts/) → revisão humana → .xlsx no layout do modelo
```

## Fluxo de trabalho
1. **Criar um caso** (um devedor / grupo econômico).
2. **Importar o Mcfly** (`.xlsx`): preenche a capa (polos, classe, juízo, procuradoria, inscrições, data do protocolo) e define, pela *classe*, qual aba e qual prompt valem para cada processo.
3. **Enviar os PDFs.** O nº do processo, o volume e a página inicial vêm do nome do arquivo (`0341609-70.2005.8.13.0261_V001_001-151-180.pdf` → VOL 001, a partir da pg. 151). Os trechos são citados como `Evento N, VOL N, pg. N, ID N` (evento/ID quando o PDF os traz).
4. **Revisar** cada extração (campos com lista fechada são validados; fora da lista o campo fica vazio e é sinalizado).
5. **Baixar a planilha**: abas Relatório, DJE-SDJ, DJE-SIEF, Execuções Fiscais, Outras Ações, Embargos à Execução, Outros Bens, Alegações de Fraude, com os mesmos cabeçalhos do modelo. Linhas ainda não revisadas saem em amarelo.

| Classe do processo | Aba | Prompt |
|---|---|---|
| Execução Fiscal | Execuções Fiscais | `prompts/execucoes_fiscais.md` |
| Embargos à Execução | Embargos à Execução | `prompts/outras_acoes.md` |
| Apelação, Agravo, Remessa… | Outras Ações | `prompts/recursos.md` |
| Demais (MS, anulatória, cautelar…) | Outras Ações | `prompts/outras_acoes.md` |

- **DJE-SDJ / DJE-SIEF**: criadas só com cabeçalho; são preenchidas por outro setor.
- **Outros Bens**: imóveis/veículos dados em garantia nas execuções (extraídos por IA).
- **Alegações de Fraude**: só quando o procurador alegou e o juiz deferiu/analisou.
- Os prompts são arquivos `.md` editáveis; o layout das abas e as **listas fechadas** ficam em `app/schemas.py`.

## Pontos importantes
- **PDFs digitalizados antigos** trazem uma camada de texto embutida corrompida (letras cirílicas, "R$5.000.00"). O sistema detecta isso (`app/ingest/quality.py`) e refaz o OCR da imagem com Tesseract. **Sem Tesseract** o texto é só parcialmente reparado e as páginas são sinalizadas: use a imagem Docker (já inclui `tesseract-ocr-por`).
- Processos grandes: acima de ~400 mil caracteres, só os trechos mais relevantes (busca híbrida por campo) mais as páginas finais vão ao LLM.
- Sem `ANTHROPIC_API_KEY` o sistema ingere e indexa, mas a extração fica "indisponível" (nunca inventa).
- O front segue a identidade visual do gov.br (cores, tipografia, componentes) em CSS próprio, sem usar brasão/marcas oficiais.

## Execução
Produção (Redis + Elasticsearch + 2 workers + Tesseract):
```bash
ANTHROPIC_API_KEY=sk-... docker compose up --build
```
Acesse http://localhost:8000. Desenvolvimento local (fila e busca em memória):
```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
python -m pytest
```

## Limitações / pendências
- Lista fechada de `EXIT. GARANTIA`: só IMÓVEL e BACEN C/DJE vieram do modelo; o resto (VEÍCULO, FIANÇA/SEGURO GARANTIA, OUTROS, NÃO HÁ) é suposição em `app/schemas.py`.
- Marcadores de Evento/ID foram escritos para PJe/eproc sem amostra real; só foi testado um PDF digitalizado de processo físico. Valide com PDFs eletrônicos.
- Embeddings por hashing local (trocar `embed()` em `app/search/base.py` por modelo real melhora a busca).
- Sem autenticação: adicione SSO/proxy antes de expor (LGPD). OCR real, Redis, Elasticsearch e o LLM não foram exercitados no ambiente de testes (cobertos por simulação).
