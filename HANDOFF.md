# HANDOFF — SmartProcure / LeitorCatalogos (16/09/2026)

Substitui o handoff de 11/09. Ao retomar, ler nesta ordem: `ESCOPO_FLAGSHIP.md` → `ARQUITETURA.md` → `ROADMAP.md` → `COMPARACAO.md` → este arquivo. Rodar o Studio de `spike/`: `uv run langgraph dev --port 8123` (Windows: `cd /d` p/ trocar de drive; se faltar dep, `uv add`).

## Estado: POC COMPLETO na Definition of Done ✅
Os dois agentes rodam no Studio, com motor real e trace no LangSmith. Falta só **acabamento** (faxina) e o que está de propósito **parqueado** (abaixo).

## Como rodar / demonstrar (Studio)
- **Agente 1 — extração real:** grafo `onboarding_real`. Numa **thread nova** (não clicar "Continue"), input em dois campos: `pdf = ../catalogo/LEHMOX 2026.04-completo.pdf`, `pagina = 5` (ou 15). Roda perceber→bootstrap→propor→**rodar (motor real)**→avaliar (100%/100%)→registrar. NÃO é chat — é extração; a saída aparece no painel State.
- **Agente 2 — o radar (chat):** grafo `analista_v2`. Conversa com os dados. Abertura pede a margem; depois pergunte "maiores ofertas?", "com R$5000 o que compro?", etc. Respostas em tabela markdown, com a conta à mostra. `USE_LLM=true` no `.env` liga o planejador gpt-4o-mini (custo/trace no LangSmith).

## Mapa de arquivos (o que importa)
- `agente/config.py` — contrato `ExtractionConfig` + `CONFIG_LEHMOX` (config validada).
- `agente/colors.py` — normaliza cor (RGB/CMYK), `is_offer_color`.
- `agente/engine.py` — motor determinístico `det_parse_cfg(pdf, cfg, pages)`.
- `agente/tools.py` — Agente 1: `CerebroMock`/`MotorMock` (offline), `CerebroOpenAI` (visão, FUTURO/stub), **`CerebroLehmox` + `MotorDet` (caminho real "fornecedor conhecido")**, `avaliar`.
- `agente/graph.py` — grafo do onboarding (aceita `input_schema`).
- `agente/consulta.py` — Agente 2 v2: contrato `Query` + `PerfilSessao` + executor determinístico (whitelist Enum = cerca 4; campos derivados de fórmula fixa).
- `agente/grafo_analista_v2.py` — grafo v2 compartimentado + `PlanejadorLLM` (structured output → Query) + narrador em tabelas.
- `agente/tools_analista.py` — 4 tools determinísticas (usadas pela otimização e pelo gabarito).
- `agente/analista.py` — **v1 (LEGADO)**: as 6 cercas antigas com 4 tools fixas. Substituído pelo v2. Decidir se aposenta.
- `agente/dados_norm.json` — ponte 2026 (356) + `preco_base` 2023 (regenerável pelo motor).
- `spike/studio_graph.py` — expõe os 5 grafos; `langgraph.json` — registra.
- `spike/eval_roteamento.py` — suíte de regressão do planejador.
- `spike/ground_truth_p5_p15.json` — gabarito (32 itens).
- Raiz/Project: `COMPARACAO.md`, `ROADMAP.md`, `ARQUITETURA.md`, `ESCOPO_FLAGSHIP.md`.

## O que está pronto (evidência)
- **Agente 1 real:** `onboarding_real` extrai LEHMOX 2026 p5/p15 → 16 itens/pág, recall 100%, precisão 100%, gate PASSOU.
- **Agente 2 v2:** query estruturada (LLM emite dados, executor traduz p/ pandas), esclarecer-em-vez-de-recusar, histórico no planejador, narrador em tabelas, número só de ferramenta determinística.
- **Suíte de regressão:** `eval_roteamento.py` — 14 casos pergunta→ação/campo; `--local` (terminal) ou LangSmith (dataset `analista-roteamento`, compara prompt v1 vs v2).
- **Benchmark honesto:** `COMPARACAO.md` — Excel × NotebookLM × LeitorCatalogos. NotebookLM 1/5 (exibit: pergunta 3, achou 38 itens buscando texto "折后/特价" e perdeu 22 marcados só pela cor → total confiantemente errado).

## Caveats conhecidos (não bloqueiam)
- **Convenção de margem AGUARDA o lojista.** Hoje "60%" é interpretado como markup 1,6×. Se o lojista disser "margem sobre venda", trocar em `grafo_analista_v2.py` o `extrair_margem` (% → fração) e em `consulta.py` as fórmulas `preco_venda_est = preco_efetivo/(1−margem)` e `lucro_unit`.
- **Aviso msgpack** no checkpointer do `onboarding_real` (serializa `Percepcao`/`ExtractionConfig`/`Avaliacao`) — é deprecation, não quebra.
- **Ruído CJK** em algumas descrições (蓝牙/塑封) — cosmético.
- **`onboarding_real` = caminho rápido "fornecedor conhecido"** (config registrada, sem visão). O loop de visão p/ fornecedor NOVO (`CerebroOpenAI`) é futuro.

## FAXINA / limpeza pendente (antes de postar no GitHub)
- **Aposentar ou rotular o v1:** decidir o destino de `agente/analista.py` e dos grafos `analista`/`analista_chat` no `studio_graph.py`/`langgraph.json` (o v2 os substitui). Ou remove, ou marca claramente como legado.
- **Revisar a árvore do repo** e remover arquivos mortos/temporários/experimentais antes do commit público.
- **`.gitignore`:** garantir `.venv/`, `.env`, `__pycache__/`, e decidir sobre os PDFs grandes de `catalogo/` (LFS ou fora do repo).
- **Dependências:** confirmar no `pyproject` — `pandas`, `pdfplumber`, `langsmith`, `colorama` (Windows).
- **Limpeza CJK** no loader (opcional, cosmético).
- **README raiz:** decidir se `COMPARACAO.md` vira o README ou entra em `docs/`; adicionar prints do Studio + NotebookLM.
- **Segurança:** conferir que nenhuma chave (OPENAI/LANGSMITH) vazou em arquivo commitado.

## Parqueado (futuro nomeado — fora do POC)
- Loop de visão p/ fornecedor NOVO (`CerebroOpenAI`: perceber/bootstrap/propor com gpt-4o vision).
- Motor de precificação (fórmula-mãe, MC, breakeven, ROI, imposto) — depende de dados do lojista.
- Finalização: login/sessão, persistência de state, reconsulta, upload de catálogos novos.
- Categoria confiável (lista de negócio + match semântico); entity resolution entre fornecedores.

## Próxima sessão (sugestão)
Fazer a faxina acima → capturar os prints/GIF do Studio → postar no GitHub. Se o lojista respondeu a margem, aplicar a convenção. Só então, se quiser, encostar num item parqueado.
