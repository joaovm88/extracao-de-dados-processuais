# Sistema de Análise e Ingestão de Processos Judiciais

Plataforma RAG com processamento assíncrono que preenche a **planilha de relatório do analista** a partir dos PDFs dos processos.

```
Front-end (casos, upload, revisão, chat) → API (FastAPI) → Fila (Redis) → Worker
   OCR + texto nato-digital + quebra semântica → Elasticsearch (lexical + vetorial) + metadados
   → extração estruturada por LLM — Google Gemini (AI Studio) ou Anthropic (prompts versionados em prompts/) → revisão humana → .xlsx no layout do modelo
```

## A tela: planilha viva + assistente (tela única)
- **Barra superior:** caso, *Planilha-modelo*, **Upload de Processo (PDF)** e **Exportar planilha (.xlsx)**.
- **Esquerda (~70%):** a **planilha viva**, uma aba por tipo (Execuções Fiscais, Outras Ações, Embargos, Outros Bens, Fraudes), uma linha por processo, com as colunas da planilha-modelo do caso (ou o layout padrão). Edite direto na célula: salva sozinho, listas fechadas viram menus, observações abrem numa janela. A capa (Mcfly) é somente leitura. Passe o mouse numa célula para ver a origem (Evento/ID e página). Linhas em amarelo estão *a revisar*, verde = revisado, vermelho = erro. Arraste PDFs para a tabela para enviar. Colunas de texto longo (**Observações** e **Corresponsáveis**) abrem numa janela para ler e editar tudo. **Corresponsáveis** é uma lista, uma pessoa ou empresa por linha (`Nome - CPF: 000.000.000-00` / `Nome - CNPJ: 00.000.000/0000-00`): a IA é instruída assim e o sistema arruma o que ela devolver fora do padrão (marca documentos com dígito inválido como "conferir dígitos"); o botão **Padronizar lista** da janela faz o mesmo em linhas já extraídas ou coladas à mão, sem gastar IA. **Filtro** por texto (nº, parte, juízo, valor; atalho `/`) e por situação (a revisar, revisados, com erro, em andamento, sem PDF). **Tamanhos ajustáveis:** arraste a borda direita do cabeçalho para mudar a **largura** de uma coluna (mín. 70 px) e a borda inferior da primeira célula da linha para mudar a **altura**; duplo clique na alça restaura aquele tamanho. A densidade **Compacto / Normal / Amplo** muda todas as linhas de uma vez e **↺ Tamanhos** restaura tudo. As escolhas ficam salvas por caso, neste navegador. O botão ☀/🌙 alterna o **tema claro/escuro** (lembra a escolha; começa pelo tema do sistema).
- **Direita (~30%, retrátil):** o **assistente** do processo selecionado, com a situação, os erros (com *Tentar novamente*), os PDFs do processo (reprocessar, excluir), *Refazer extração* e o chat com perguntas sugeridas e **Adicionar às observações**.
- **📊 Medições** (botão na barra da tabela): por processo, quantas páginas foram enviadas à IA (e se o processo foi **cortado** por não caber no limite), tokens de entrada/saída, tempo da IA, tempo de leitura dos PDFs e do OCR. Mostra também **quantas tentativas** a IA precisou, quanto tempo foi de **espera** entre elas (sobrecarga/limite) e qual modelo respondeu, o que explica extrações demoradas. Serve para decidir com dados se vale investir em busca melhor (ex.: EmbeddingGemma 2) ou em mais cota. Processos extraídos antes desta versão não têm medição (use *Refazer extração*).
- **Fila que se recupera sozinha:** um vigia devolve à fila, a cada minuto, PDFs "na fila" há mais de 90 s cujo pedido se perdeu (ex.: Docker reiniciado) e PDFs "processando" sem andamento há 10 min. O Redis agora guarda a fila em disco (`appendonly`). Um PDF parado só segura o **próprio processo**, não o caso inteiro.
- Se a IA estiver sobrecarregada (503), o chat tenta 2 vezes rápido, usa o modelo reserva (se houver) e mostra uma mensagem clara com **Tentar de novo**; a extração em segundo plano é mais paciente (4 tentativas).
- Em telas pequenas o assistente abre como painel sobre a planilha.

## Uma planilha por caso, uma linha por processo
O relatório é **por caso** (devedor/grupo): uma única planilha com todos os processos. Um processo pode vir em **vários PDFs** (volumes `V001_001-1-50`, `V001_001-51-90`…, ou `PARTE_1`, `PARTE_2`): cada PDF é lido isoladamente (OCR/indexação), mas a extração por IA roda **uma vez por processo**, juntando todas as partes na ordem dos autos, e gera **uma linha** em cada aba. A extração só começa quando todos os PDFs enviados do caso terminam, para não gastar LLM a cada parte.
- Reenviar a mesma parte (mesmo processo/volume/página inicial) substitui a anterior.
- Chegou parte nova num processo já **revisado**: ele fica "desatualizado" e suas edições são preservadas; "Refazer extração" reprocessa tudo (descarta as edições).
- Validado com os 5 PDFs de um processo físico (204 páginas): 1 processo, 1 chamada ao LLM, contexto com as 204 páginas em ordem.

## Planilha-modelo do caso (opcional)
Em vez do layout padrão, importe a **planilha do próprio caso** (.xlsx). O relatório final é uma **cópia dela, preenchida**:
- Abas reconhecidas (Relatório, Execuções Fiscais, Outras Ações, Embargos, Outros Bens, Alegações de Fraude) são preenchidas pelo texto do cabeçalho; as demais (DJE-SDJ/SIEF, Mcfly, Recuperação Judicial, Filtros…) voltam **intactas**.
- Processo que já tem linha: só as **células vazias** são preenchidas. Processo novo: linha acrescentada ao final.
- A tela avisa colunas não reconhecidas (ficam em branco), abas com layout diferente (ex.: Outros Bens em blocos por matrícula) e abas que o modelo não tem.
- Não há mais um passo separado de "cadastro Mcfly": a própria planilha-modelo registra os processos. (O endpoint `POST /api/cases/{id}/mcfly` continua na API, sem botão na tela.) Ao salvar, o openpyxl pode perder elementos avançados (gráficos, imagens); fórmulas e formatação comuns são mantidas.

## Fluxo de trabalho
1. **Criar um caso** (um devedor / grupo econômico).
2. **Importar a planilha-modelo** do caso (`.xlsx`): os processos são lidos da aba **Mcfly** (export do SAJ) ou, se ela estiver vazia, da aba **Relatório**. Isso preenche a capa (polos, classe, juízo, procuradoria, inscrições, data do protocolo) e define, pela *classe*, qual aba e qual prompt valem para cada processo.
3. **Enviar os PDFs** escolhendo o **tipo** (Execução Fiscal, Embargos, Apelação/Recurso, Agravo, Outras ações), que seleciona o prompt, e o **sistema** (eproc/PJe, ou automático). O nº do processo, o volume e a página inicial vêm do nome do arquivo (`0341609-70.2005.8.13.0261_V001_001-151-180.pdf` → VOL 001, a partir da pg. 151). Os trechos são citados como `Evento N, VOL N, pg. N, ID N` (evento/ID quando o PDF os traz).
4. **Revisar** a extração de cada processo (campos com lista fechada são validados; fora da lista o campo fica vazio e é sinalizado).
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
- **Linha do tempo fixa (eproc):** nos PDFs do eproc, o sistema lê as *páginas de separação* de cada evento (nº, data, movimento, usuário; formatos de 2º grau e de 1ª instância) e os códigos de documento dos rodapés. Com isso monta, **sem IA**, o índice completo dos eventos (botão **📅 Eventos** no painel: filtro, copiar e *Usar como observações*). A IA recebe esse índice inteiro + o texto integral só dos atos **relevantes** (mero expediente e anexos como estatutos ficam só no índice), e a cronologia que ela escrever é **conferida**: a data é trocada pela do eproc, a lista é posta em ordem e eventos inexistentes são apontados. 
- **Linha do tempo do PJe:** o sistema lê a tabela *Documentos* da capa (ID, data de assinatura, nome, tipo; IDs quebrados em duas linhas são juntados) e cruza com os rodapés `Num. <ID> - Pág. <n>`. A IA recebe o índice completo + o texto dos documentos relevantes; o *Volume* (autos físicos digitalizados num único documento) usa o orçamento restante, priorizando as páginas mais recentes. A cronologia sai como `ID N, pg. P - dd/mm/aaaa - ato` e é conferida (ID inexistente apontado, datas dos documentos comuns corrigidas, ordem por data). Páginas com OCR corrompido são refeitas com Tesseract. Autos digitalizados sem sistema continuam com o contexto por páginas.
- **Execução Fiscal** gera só a **cronologia dos atos** (uma linha por ato: origem, data e descrição). Os prompts ficam em `prompts/*.md` e, no Docker, a pasta é montada: **edite o arquivo e use "Refazer extração"**, sem rebuild. Os prompts são arquivos `.md` editáveis; o layout das abas e as **listas fechadas** ficam em `app/schemas.py`.

## Citações: Evento (eproc) x ID (PJe)
- **eproc** cita `Evento N, pg. P`; **PJe** cita `ID N, pg. P` (ID do documento e página *dentro* dele, lida do rodapé `Num. X - Pág. N`). O sistema é detectado pelos marcadores do PDF ou informado no envio.
- Os rótulos são calculados pelo código a partir do PDF, não pelo modelo: o LLM só copia o rótulo de cada trecho, e o prompt proíbe o formato do outro sistema.
- Depois da extração, toda citação do texto (Evento, ID, pg.) é conferida com o PDF. Citação inexistente, ou `ID` em processo eproc / `Evento` em PJe, aparece em vermelho na tela de revisão.
- **eproc, PDF completo** (validado com um PDF real do TRF6): o ato vem de `PÁGINA DE SEPARAÇÃO … Evento N` e do rodapé `Processo …/TRF6, Evento 14, DESPADEC1, Página 1`; `pg.` é a página **dentro do ato** (não a do PDF). A classe vem da capa (`Classe da ação:`), então o prompt certo é escolhido mesmo sem o Mcfly. Menções a "evento X" no corpo das peças não viram marcador.
- `Evento N, pg. P` é conferido como par: a página P tem de existir dentro do ato N.
- Páginas sem marcador herdam o ato anterior.

## Pontos importantes
- **PDFs digitalizados antigos** trazem uma camada de texto embutida corrompida (letras cirílicas, "R$5.000.00"). O sistema detecta isso (`app/ingest/quality.py`) e refaz o OCR da imagem com Tesseract. **Sem Tesseract** o texto é só parcialmente reparado e as páginas são sinalizadas: use a imagem Docker (já inclui `tesseract-ocr-por`).
- Processos grandes: acima de ~400 mil caracteres, só os trechos mais relevantes (busca híbrida por campo) mais as páginas finais vão ao LLM.
- **LLM (`app/llm.py`)**: `LLM_PROVIDER=auto` usa **Google Gemini** se houver `GEMINI_API_KEY` (chave do Google AI Studio), senão Anthropic. A extração usa saída JSON restrita ao schema da aba (`response_json_schema`); se a API recusar o schema, cai para JSON livre e a validação local (listas fechadas) continua valendo. O modelo é configurável em `GEMINI_MODEL`. Sem nenhuma chave o sistema ingere e indexa, mas a extração fica "indisponível" (nunca inventa).
- **Confidencialidade (LGPD/sigilo):** não use a camada gratuita do AI Studio com processos reais. Confirme nos termos da parceria que a camada paga/Vertex AI **não usa os dados para treinar** e que o tratamento é permitido.
- **PDFs do eproc** (um evento por arquivo): o nº do evento vem do nome (`Evento_14.pdf`), o ID do rodapé (`:: 60000006508 - eproc - ::`) ou do código verificador, e o nº do processo do texto (priorizando os do Mcfly do caso). Citação: `Evento 14, pg. 1, ID 60000006508`.
- O front segue a identidade visual do gov.br (cores, tipografia, componentes) em CSS próprio, sem usar brasão/marcas oficiais.

## Execução
Produção (Redis + Elasticsearch + 2 workers + Tesseract):
```bash
GEMINI_API_KEY=... docker compose up --build
```
Acesse http://localhost:8000. Desenvolvimento local (fila e busca em memória):
```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
python -m pytest
```

## Escolher o modelo do Gemini
O Google retira modelos antigos para contas novas (ex.: `gemini-2.5-pro` respondeu "no longer available to new users"). O padrão do projeto é `gemini-3.1-pro-preview`; mude em `GEMINI_MODEL` no `.env`. Para listar os modelos que a **sua** chave enxerga (com o sistema parado):
```bash
docker compose run --rm --no-deps api python -c "import os; from google import genai; c = genai.Client(api_key=os.environ['GEMINI_API_KEY']); [print(m.name) for m in c.models.list()]"
```
Use um nome da lista sem o prefixo `models/`. Modelos "preview" têm limites menores no nível gratuito.

**Nível gratuito: modelos Pro têm cota zero.** Em https://aistudio.google.com/rate-limit cada modelo mostra RPM / TPM / RPD. Um modelo com `0 / 0 / 0` (ex.: Gemini 3.1 Pro, 2.5 Pro) recusa qualquer pedido com 429. Use um **Flash ou Flash-Lite** com cota maior que zero. Cada extração é **1 pedido** (RPD) e ~25 a 100 mil tokens de entrada (TPM).

**Erro 503 "high demand"/indisponível:** sobrecarga momentânea do Google. O sistema tenta de novo sozinho (esperas de 10, 25 e 50 s) e, se `GEMINI_FALLBACK_MODEL` estiver definido, usa esse modelo reserva; senão, use "Tentar novamente" em alguns minutos.

**Erro 429 / "Limite de uso do Gemini atingido"** (comum no nível gratuito com processos de centenas de páginas):
1. Use um modelo "flash" da lista (limites maiores).
2. Reduza o texto enviado por processo com `CONTEXT_BUDGET_CHARS` no `.env` (4 caracteres ≈ 1 token; `100000` ≈ 25 mil tokens). O sistema então manda só os trechos mais relevantes e as páginas finais; confira o resultado na revisão.
3. Limite por minuto com prazo ("retry in 30s"): o sistema espera e tenta de novo sozinho (até 2 vezes). Cota diária esgotada ou plano sem cota: só esperar o dia seguinte ou ativar faturamento.

## Limitações / pendências
- Lista fechada de `EXIT. GARANTIA`: só IMÓVEL e BACEN C/DJE vieram do modelo; o resto (VEÍCULO, FIANÇA/SEGURO GARANTIA, OUTROS, NÃO HÁ) é suposição em `app/schemas.py`.
- Marcadores de Evento/ID foram escritos para PJe/eproc sem amostra real; só foi testado um PDF digitalizado de processo físico. Valide com PDFs eletrônicos.
- Embeddings por hashing local (trocar `embed()` em `app/search/base.py` por modelo real melhora a busca).
- Sem autenticação: adicione SSO/proxy antes de expor (LGPD). OCR real, Redis, Elasticsearch e o LLM não foram exercitados no ambiente de testes (cobertos por simulação).
