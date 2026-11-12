# Roteiro de estudo no PyCharm (7 exercícios)

Objetivo: entender o código rodando-o, e não só lendo. Cada exercício leva de 15 a 30 minutos.
Faça na ordem. Antes de cada um, tente **explicar com as suas palavras** o que a função deve fazer.

## 0. Preparação (uma vez)
1. `Git → Update Project` (ou `git pull origin claude/great-cori-lknaw6`) para ter a versão mais recente.
2. Interpretador: `File → Settings → Project → Python Interpreter` deve apontar para o `.venv` do projeto.
   Se faltar pacote: `pip install -r requirements.txt -r requirements-dev.txt` no terminal do PyCharm.
3. Rode todos os testes: clique com o botão direito na pasta `tests` → **Run 'pytest in tests'**. Devem passar (80).
4. **Como depurar:** clique na margem esquerda ao lado de uma linha (bolinha vermelha = ponto de parada) e rode o teste
   com o ícone do **inseto** (Debug). O programa para na linha. Na aba *Debug* veja as variáveis; `F8` avança uma linha,
   `F7` entra na função, `F9` continua.

---

## Exercício 1: como o texto de uma página vira um rótulo de citação
**Arquivo:** `app/ingest/citation.py` · **Teste:** `tests/test_cases.py::test_filename_and_citation`
1. Ponto de parada em `page_refs` (linha de `def page_refs`) e rode o teste em Debug.
2. Observe o texto recebido e o dicionário `out` que sai (`id`, `pg`, `pje`, `evento`).
3. Entre em `label` (`F7`) e veja como o mesmo dado vira `Evento N, pg. P` (eproc) ou `ID N, pg. P` (PJe).
**Perguntas:** por que a IA nunca monta a citação? O que muda no rótulo quando `sistema == "pje"`?
**Desafio:** escreva um teste novo que chame `label(1, 5, {"evento": "7", "pg": "2"}, "eproc")` e confira o resultado.

## Exercício 2: a linha do tempo do PJe (índice da capa)
**Arquivo:** `app/ingest/pje.py` · **Teste:** `tests/test_pje.py::test_indice_junta_id_quebrado`
1. Ponto de parada em `parse_indice`. Rode o teste em Debug.
2. Veja a lista `lines` e a lista `starts`: o ID aparece quebrado (`78623` + `2960`) e é juntado.
3. Avance até o `return` e observe os documentos (id, data, nome, tipo).
**Perguntas:** o que acontece se o nome do documento quebrar em duas linhas? Por que o ID não pode ser lido de uma linha só?
**Desafio:** no teste, adicione na `CAPA` um documento de nome longo em duas linhas e confira o resultado.

## Exercício 3: cruzar o índice com os rodapés
**Arquivo:** `app/ingest/pje.py` (`extract_docs`, `merge_docs`) · **Teste:** `test_docs_cruzam_indice_e_rodape`
1. Parada em `extract_docs`. Veja como páginas **sem** rodapé (a capa) alimentam o índice e as **com** rodapé alimentam `paginas`.
2. Em `merge_docs`, veja como duas partes do mesmo processo viram um registro por ID.
**Perguntas:** por que o "Volume" é tratado à parte (`e_volume`)? Onde a relevância (alta, média, baixa) é decidida?

## Exercício 4: a conferência da cronologia (a "rede de segurança")
**Arquivos:** `app/ingest/pje.py` (`validar_cronologia`) e `app/ingest/eventos.py` · **Testes:** `test_validar_cronologia_pje`, `tests/test_eventos.py::test_validate_chronology_...`
1. Parada em `validar_cronologia`. Rode o teste e acompanhe linha por linha da cronologia que a "IA" escreveu.
2. Veja onde um ID inexistente é apontado e onde a data é corrigida.
3. Compare: dentro de um *Volume*, por que a data **não** é trocada?
**Desafio:** altere a data de uma linha no teste e veja `stats["datas_corrigidas"]` mudar.

## Exercício 5: o que a IA realmente recebe (contexto)
**Arquivo:** `app/extract.py` (`select_context_pje`, `select_context_eventos`) · **Teste:** `tests/test_eventos.py::test_event_context_has_full_index_but_text_only_for_relevant_acts`
1. Parada no `return ctx, info`. Em Debug, clique com o botão direito em `ctx` → *View as Text* (ou copie o valor).
2. Leia o texto: o **índice completo** vem primeiro, o texto integral só dos atos "relevantes".
3. Veja `info`: `eventos_com_texto`, `paginas_enviadas`, `cortado`. É o que aparece na tela **Medições**.
**Perguntas:** o que acontece quando o orçamento (`budget`) é pequeno? Como isso muda a precisão?
**Desafio:** rode com `budget=60000` e com `budget=800000` e compare `paginas_enviadas`.

## Exercício 6: a extração e as listas fechadas
**Arquivo:** `app/extract.py` (`extract_with_llm`, `validate`) · **Teste:** `tests/test_cases.py::test_validate_rejects_value_outside_closed_list`
1. Parada em `validate`. Dê a ela um valor fora da lista (ex.: garantia = "CARRO") e veja o campo ir para `revisar`.
2. Abra `prompts/execucoes_fiscais.md` e leia o prompt. Edite uma frase, rode um teste e veja o que muda no `system` enviado.
**Pergunta:** por que a lista fechada é checada por código e não só pedida no prompt?

## Exercício 7: o caminho de um PDF até a planilha (visão geral)
**Arquivos:** `app/main.py` (upload) → `app/queue.py` → `app/worker.py` (`process_document`, `extract_process`)
**Teste:** `tests/test_cases.py` (os que usam `wait(...)`).
1. Parada em `process_document` e em `extract_process`. Rode um teste de ponta a ponta em Debug.
2. Siga a ordem: upload → fila → leitura do PDF → `pages` → índice de eventos → contexto → IA (simulada) → resultado salvo.
3. Veja `result["medidas"]` sendo montado.
**Entrega do exercício:** desenhe o fluxo em uma folha, com os nomes das funções. Esse desenho é o seu diagrama de portfólio.

---

## Hábitos que ajudam
- **Um ponto de parada por vez.** Mais que isso confunde.
- **Quebre algo de propósito** e veja qual teste falha: é a melhor forma de entender o que cada teste protege.
- **Escreva 1 teste por exercício** com um caso seu. Isso prova autoria e vai para o portfólio.
- **Anote o "porquê"** de cada módulo em 2 linhas (um arquivo `docs/DECISOES.md`). É o seu registro de decisões.
