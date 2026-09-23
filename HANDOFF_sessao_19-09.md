# HANDOFF — Sessão 19/09/2026

Complementa (não substitui) o `HANDOFF.md` de 16/09 — aquele documenta o estado do POC (DoD fechada). Este cobre **duas frentes novas** abertas nesta sessão: (1) faxina do repo pré-GitHub, (2) preparação do vídeo demo pra entrevista da IBM.

## Contexto — por que essa sessão começou
Felipe percebeu que as conversas antigas do Project "LeitorCatalogos" sumiram do claude.ai — causa provável: mudança/expiração de plano (relato consistente de outros usuários com o mesmo sintoma). Recomendação dada: contatar suporte da Anthropic pedindo export de dados; enquanto isso, esta sessão retomou o trabalho a partir dos `.md` do Project (fonte de verdade sobrevive independente do chat).

---

## FRENTE 1 — Faxina do repo (pré-GitHub)

**Objetivo:** base limpa pra subir no git, sem excessos. Projeto **nunca foi versionado** (sem `.git` na raiz) — faxina é só higienização de arquivos, sem comandos git.

### Feito nesta sessão
- `agente/analista.py` (v1, legado) → **arquivado em `_legado/analista_v1.py`**, com cabeçalho marcando como legado (substituído por `grafo_analista_v2.py`).
- `agente/__pycache__/`, `spike/__pycache__/`, `spike/.langgraph_api/` — removidos (regeneráveis).
- `.git` aninhado dentro de `_legado/docling/` (resquício do experimento Docling descartado) — removido, sem afetar os arquivos do experimento em si.
- `spike/requirements.txt` — arquivado (resquício pré-`uv`; fonte de verdade é `pyproject.toml` + `uv.lock`).
- Checagem de chave vazada: **nenhum vazamento** encontrado nos arquivos do projeto (as ocorrências de "sk-" eram todas dentro de `.venv/Lib/site-packages`, código de bibliotecas).
- Dependências (`uv sync` + `uv pip list` dentro de `spike/`) conferidas: `pandas`, `pdfplumber`, `langsmith`, `colorama`, `langgraph`, `pydantic`, `openai` — todas presentes.
- **Decisão do Felipe:** `catalogo/` (PDFs grandes) fica dentro da pasta do projeto — nada a fazer aí por ora; decisão de longo prazo (Git LFS vs. fora do repo) fica pra quando o git for de fato inicializado.
- **Decisão do Felipe:** pasta `Claude outputs/` (rascunhos antigos de artifacts, ex. `consulta.py`/`grafo_analista_v2.py` desatualizados) **não deve ser apagada** — fica arquivada como está.

### Pendente
- **`.gitignore` ainda não foi criado.** Conteúdo já definido, só falta escrever o arquivo quando o `git init` for de fato rodado:
  ```
  .venv/
  .env
  __pycache__/
  *.pyc
  .langgraph_api/
  ```
- **`pymupdf` no `pyproject.toml`** — apareceu na lista de dependências instaladas mas não foi encontrado sendo usado em nenhum lugar do código (o motor real usa `pdfplumber`). Verificar com `findstr /s /i "fitz\|pymupdf" *.py` e remover se for dependência morta.
- Árvore de arquivos já revisada e classificada (manter / arquivar / apagar) — a faxina de arquivos está fechada, exceto os dois itens acima.

---

## FRENTE 2 — Vídeo demo para entrevista IBM

**Contexto:** Felipe teve entrevista com a IBM pra vaga de **Technical Coach Application (ELL Cohorts Brazil)** — coach técnico pra turmas no Brasil. Pediram um **vídeo demo de 10-15 min, em inglês, apresentando o projeto por inteiro** (motivos, arquitetura, funcionando ao vivo, traces, evals). Enquadramento acertado: a vaga avalia capacidade de **explicar algo técnico com clareza pra alguém cético/leigo** — Felipe já faz isso no onboarding com clientes reais ("IA não é caixa mágica"), então o vídeo é uma extensão natural desse discurso, não uma peça nova.

### Roteiro — FECHADO, não reabrir estrutura
Publicado em: **https://claude.ai/artifact/UgEcD6WxGYaFiyXyKrD6LQ**

- Duração-alvo: **~10 min** (elástico — combinado que passar um pouco não é problema; o que importa é cada bloco ter ideia clara e objetiva).
- Divisão: **Bloco 1 = 25% · Bloco 2 = 50% · Bloco 3 = 25%** (do tempo restante) **+ Bloco 4 = 0:30 fixo** de fechamento.
- Nível de roteiro: **Bloco 1 e 4 = verbatim** (texto pronto pra ler/decorar) · **Bloco 2 e 3 = bullets** com frases-chave marcadas ⚑ (fala livre, mas não reformula essas).
- **Bloco 1 (problema):** história do lojista com catálogos semanais em PDF; peso maior em NotebookLM e LLM puro (evidências reais testadas por ele, documentadas em `COMPARACAO.md`) e peso menor em DIY/funcionário (cenários hipotéticos). Usa duas imagens estilo sketch: foto do catálogo + diagrama das tentativas falhas (mind-map horizontal, não vertical).
- **Bloco 2 (solução, 50% do tempo):** arquitetura resumida (~50s) + demo ao vivo do `analista_v2` com 3 perguntas já testadas e com resultado conhecido — incluindo uma pergunta ambígua ("what's the best product?") desenhada pra disparar esclarecimento em vez de resposta inventada — **esse é o momento mais importante do vídeo** ("não é caixa mágica" provado ao vivo, não só discursado).
- **Bloco 3 (resultados técnicos):** Agente 1 contado em 1 frase + print (não roda ao vivo, sem tempo) + trace do LangSmith + suíte de eval, tudo traduzido pra linguagem de cliente, não de engenheiro.
- **Decidido:** não mostrar terminal/setup de ambiente rodando (`cd`, `uv run langgraph dev`, etc.) — é tempo morto que não avança nem a história nem a prova técnica. Studio deve estar aberto e rodando **antes** de gravar.
- Legendas: pós-produção, padrão INPUT grande no centro → OUTPUT abaixo/menor, em inglês (a demo em si roda em português).

### Pendente
- Ensaiar com cronômetro — Bloco 2 é o mais longo e o que mais tende a estourar; Bloco 3 é o mais apertado.
- Gerar/preparar as duas imagens estilo sketch em alta resolução (catálogo + diagrama de tentativas) — Felipe já tem uma versão rascunho de cada, não confirmado se são as finais.
- Print do `onboarding_real` já rodado, salvo e pronto pra mostrar no Bloco 3 sem precisar rodar ao vivo.
- Gravar, revisar timing real, cortar Blocos 3+ (mais "cortáveis" sem perder a tese central) se passar muito do combinado.

---

## FRENTE 3 — Variante em inglês do onboarding (`onboarding_real_english`)

**Motivo:** Felipe vai narrar a demo do Agente 1 em inglês por cima da gravação; nós dos grafos com nomes em português (`perceber`, `propor`, etc.) atrapalham. Criada uma variante só com os rótulos dos nós traduzidos — lógica interna idêntica.

### Feito
- Arquivo novo **`agente/graph_en.py`** gerado e entregue (mesma lógica de `graph.py`, nomes de nó traduzidos: `perceive`, `bootstrap`, `propose`, `run`, `evaluate`, `hitl` (mantido, sigla já em inglês), `register`).
- Instruções passadas pra editar `spike/studio_graph.py` (adicionar import de `graph_en` + `graph_onboarding_real_en`) e `spike/langgraph.json` (adicionar chave `"onboarding_real_english"`).

### ⚠️ Pendente — bloqueante, confirmar antes de rodar
- **`studio_graph.py` (versão que o Felipe mandou) ainda tem o import `from analista import ...` apontando pro arquivo que já foi arquivado em `_legado/`.** Isso quebra o carregamento do módulo inteiro no Studio (não só o analista — o onboarding também para de carregar). Instrução dada: remover essas 4 linhas do fim do arquivo. **Não confirmado se Felipe já aplicou essa correção localmente.**
- Ao editar o `langgraph.json`, as chaves `"analista"` e `"analista_chat"` (v1, já removidas do import) devem sair também. Não ficou definido se o `analista_v2` (o grafo em uso de fato) está registrado no `langgraph.json` sob algum outro nome — vale conferir.
- Depois de aplicar os 3 arquivos, testar `uv run langgraph dev --port 8123` numa thread nova e confirmar que `onboarding_real_english` aparece no Studio com as caixas em inglês.

---

## Objetivo geral (não mudou)
POC flagship pra Oracle (`ESCOPO_FLAGSHIP.md`) segue como estava — DoD fechada, faxina em andamento. A frente da IBM é paralela e usa o mesmo projeto como prova, mas com um objetivo distinto: demonstrar capacidade de ensinar/comunicar, não só de construir.

## Ordem sugerida pra próxima sessão
1. Confirmar e testar a correção do `studio_graph.py` (Frente 3 — é o mais rápido de fechar).
2. Resolver `.gitignore` + `pymupdf` (Frente 1 — pequeno, fecha a faxina de vez).
3. Ensaiar o roteiro com cronômetro (Frente 2 — é o que tem mais trabalho pela frente).
