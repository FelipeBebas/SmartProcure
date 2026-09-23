# Template — Planejamento de Aplicações Agênticas (SDD-Agentic)

> Estrutura reutilizável para planejar apps agênticos (LLM + ferramentas + orquestração), inspirada na disciplina do **Medallion Architecture**: cada fase tem um papel claro, um **entregável** e um **gate** (portão de saída). Uma fase só libera a próxima quando cumpre o critério. É o `/spec` + `/gate` do SDD aplicado ao projeto inteiro.

## Princípio central

O que dá robustez não são as fases — é o **contrato entre elas**. Assim como bronze só vira prata quando cumpre um critério, cada fase aqui produz um artefato e um portão. O portão mais importante é o **go/no-go pós-spike**: se a premissa mais arriscada não se provar, você volta e ajusta (ou mata o projeto barato) antes de investir na build.

---

## As 4 fases (+ fase 0 opcional)

| Fase | Papel | Entregável | Gate (critério de saída) |
| --- | --- | --- | --- |
| **0. Descoberta** *(opcional/curta)* | Validar que o problema existe e vale a pena | Frase de problema + hipótese de valor + usuário | Vale investir tempo de planejamento? |
| **1. Planejar** | Definir *porquê*, *o quê* e *como* | **PRD** (problema, usuário, métricas de sucesso, não-objetivos) · **RFC/Design Doc** (arquitetura, stack, fluxos) · **ADRs** (decisões com contexto/alternativa/consequência) | Arquitetura coerente e cada decisão defensável de forma independente? |
| **2. Contratos & Critérios** | Congelar interfaces e definir a régua | **Data Contracts** (schemas de I/O + DDL) · **Plano de Evals** (golden dataset + métricas de sucesso + orçamento de custo/latência) | Sei exatamente o que "bom" significa, **em número**, antes de codar? |
| **3. Spike** *(baixo risco)* | De-riscar a suposição #1 | Protótipo mínimo que ataca só o maior risco, **medido contra os evals da fase 2** + observabilidade ligada | **Go/No-Go:** bateu o critério? Se não → volta à fase 1/2 ou encerra barato |
| **4. Vertical Slice** | Primeira build de valor real | Fatia mais fina ponta-a-ponta (walking skeleton) com tracing desde o commit 1 | Entrega valor de verdade e é observável? → **itera em fatias** |

**Regra de ouro dos gates:** nenhuma fase pula a anterior. O spike (3) só existe porque os evals (2) deram a régua; a build (4) só começa porque o spike passou no portão.

---

## Detalhe por fase

### Fase 1 — Planejar
- **PRD:** problema em uma frase, quem é o usuário, **métrica de sucesso** ("o que faz isso valer a pena?"), não-objetivos (o que fica de fora do escopo).
- **RFC/Design Doc:** arquitetura, stack, topologia do agente/grafo, fluxos (setup vs. run), origem dos dados.
- **ADRs:** uma linha por decisão importante — *decisão · alternativas · motivo · consequência*. É o artefato que sustenta "defendo cada escolha sozinho". Uma tabela "mudança / motivo" já é ADR.

### Fase 2 — Contratos & Critérios
- **Data Contracts:** schemas de entrada/saída (ex: Pydantic com tipos estritos, `Decimal` p/ dinheiro, `Enum` p/ estados) + DDL do banco. Tipos são a primeira linha de defesa.
- **Plano de Evals:** dataset de referência (casos fáceis **e** difíceis/de borda), métricas por dimensão (acurácia, decisão, custo), e o **orçamento** (custo/run, latência) como `assert`, não como promessa. "Evals são os novos testes unitários."

### Fase 3 — Spike
- Ataca **um** risco: o que, se falhar, derruba o projeto (extração? custo? latência? qualidade da ferramenta?).
- Rodado com observabilidade (tracing) desde já, pra medir o número real.
- Termina num **portão explícito**: passou → build; não passou → ajusta premissa/spec ou encerra. O valor do spike é dar a decisão barata.

### Fase 4 — Vertical Slice
- A menor fatia que atravessa todo o sistema e entrega valor (não a app inteira).
- Observabilidade, testes e evals de regressão desde o commit 1.
- Depois: engorda em fatias, cada uma passando pelos mesmos gates leves.

---

## Referências de mercado (vocabulário para defender)

- **"Building Effective Agents" (Anthropic):** padrões de orquestração — *routing*, *orchestrator-workers*, *evaluator-optimizer*, prompt chaining; distinção *workflow* vs. *agent*.
- **"12-Factor Agents":** manifesto de agentes em produção — seja dono dos prompts e do control flow, estado explícito, erro e human-in-the-loop como cidadãos de primeira classe.
- **ADR (Architecture Decision Records):** o formato padrão de registro de decisão.
- **Eval-driven development:** definir a régua antes de construir.
- **Walking skeleton / vertical slice:** primeira build fina ponta-a-ponta.

---

## Mapa para o seu SDD

| Comando SDD | Onde entra |
| --- | --- |
| `/spec` | Fases 1 e 2 (PRD, RFC, ADRs, contracts, evals) |
| `/gate` | Portão de saída de **cada** fase — em especial o go/no-go pós-spike |
| `/registrar` | Cada ADR e cada resultado de eval/spike |
| `/continuar` | Retomada entre fases |
| `/notebook` | Spike (fase 3) e experimentos de eval |
| `/mapa` | Visão do grafo/arquitetura (RFC) |

---

## Anti-padrões (o que evitar)

- Escrever a spec técnica completa **antes** de de-riscar a premissa central (foi o desvio corrigido no projeto LeitorCatalogos).
- Spike sem gate → vira teatro; sempre feche com go/no-go.
- Evals depois da build → você constrói sem saber o alvo.
- "Primeira build" = app inteira → prefira o vertical slice e itere.
- Observabilidade como fase futura → ligue tracing já no spike.
