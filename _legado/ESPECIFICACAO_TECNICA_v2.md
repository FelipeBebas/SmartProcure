# Especificação Técnica de PoC — v2.1
**Projeto:** SmartProcure AI (Agente de Triagem e Otimização de Compras B2B)
**Repositório:** LeitorCatalogos
**Objetivo:** Ingerir, extrair de forma estruturada e analisar oportunidade de compra a partir de catálogos atacadistas em PDF de alta complexidade visual, aplicando regras determinísticas de negócio e persistência multi-tenant — com stack **portável e não dependente de Oracle**.

> **Sobre esta versão.** A v2.1 mantém a arquitetura central (heurística → extração → motor determinístico, orquestrado em LangGraph com HITL) e consolida duas decisões: (a) **extração por modelos online** (ex.: GPT-4o mini) tanto no caminho texto quanto no de imagem, para poder **rastrear custo no LangSmith** e perseguir a meta de **< US$0,01 por run**; (b) **métrica financeira ancorada no custo** (variação de custo como sinal primário, markup como portão de rentabilidade). Cada mudança vem anotada com o *porquê*, para ser defendida de forma independente.

---

## 0. Changelog v1 → v2.1 (todas as alterações)

| # | Mudança | Motivo (defesa) |
| --- | --- | --- |
| C1 | **`Decimal` em todo valor monetário** (Python) e **`NUMERIC` no Postgres**; proibido `float`/`double` para dinheiro | `float` binário garante divergência centesimal — contradizia a própria meta de eval "nenhuma divergência centesimal". |
| C2 | **Cascata de ingestão** que só manda ao modelo caro as páginas relevantes, e desvia para **visão online** quando o PDF não tem texto extraível | Sem a triagem, catálogos escaneados fariam tudo cair no modelo de visão e estourariam a meta de custo. A cascata é o que viabiliza o run sub-centavo. |
| C3 | **Multi-tenancy com Row-Level Security (RLS)** no Postgres, não só coluna `tenant_id` | Coluna sozinha não isola nada — depende de todo `WHERE` estar certo. RLS força o isolamento na camada de dados. |
| C4 | **Schema fecha com as regras**: novas tabelas `fornecedores` e `lotes`; colunas `estoque_atual`, `preco_venda_referencia`, procedência e confiança | A fórmula de margem usava preço de venda que não existia; o roteador usava "estoque baixo" sem coluna de estoque; faltavam fornecedor e metadado do lote. |
| C5 | **Provider de LLM abstraído** (camada única), default **online** (OpenAI), trocável sem tocar no grafo | Permite trocar de modelo/fornecedor depois sem reescrever a orquestração; hoje online por causa do rastreio de custo. |
| C6 | **Métrica financeira ancorada no custo**: sinal primário = variação de custo; markup sobre custo como portão de rentabilidade | Custo é o dado de maior confiança; preço de venda é frágil. Margem e markup são interconversíveis — o que importa é depender do dado mais seguro. |
| C7 | **Detecção de preço riscado por geometria** (linhas/retângulos do pdfplumber sobre a bbox do preço), determinística | "Preço riscado" é sinal visual; resolver por geometria evita gastar token e é auditável. |
| C8 | **Idempotência e proveniência**: hash do PDF no lote, constraint única por item, página de origem e método de extração | Reprocessar o mesmo PDF não pode duplicar histórico; auditoria precisa saber de onde veio cada número. |
| C9 | **Guardas de laço e estado de erro**: máximo de retentativas no validador, nó de *dead-letter* que roteia falhas ao HITL em vez de descartar | Evita loop infinito de correção e perda silenciosa de itens que falharam na extração. |
| C10 | **`Enum` para `status_acao`** e tipos estritos no Pydantic | String mágica convida a typo silencioso; `Enum` dá segurança de tipo e vira documentação. |
| C11 | **Migrations versionadas (Alembic)** em vez de SQL solto | Schema evolui com histórico e rollback; não se sobe produção com `CREATE TABLE` ad hoc. |
| C12 | **Evals expandidos**: acurácia de casamento de SKU, acurácia do roteador, `assert` de custo por run, dataset separado texto vs imagem | A v1 só testava extração; o valor de negócio mora na decisão e na resolução de entidade. |
| C13 | **Resolução de entidade em cascata** (EAN exato → trigrama `pg_trgm` → embedding pgvector como fallback) | Casamento de produto é majoritariamente léxico; embedding em todo item é caro e menos preciso que trigrama para códigos/descrições curtas. |
| C14 | **HITL com mecanismo concreto de retomada** (endpoint/CLI de resume), não "pela UI" | A v1 assumia uma UI fora de escopo; o PoC precisa de um caminho real de retomada do `interrupt()`. |

---

## 1. Declaração do Problema e Proposta de Valor

**Problema.** Varejistas recebem tabelas e catálogos semanais de distribuidores por canais informais — PDFs caóticos com grids, imagens, preços riscados e múltiplos de caixa. A conferência manual item a item contra histórico e margem consome horas e leva a compra "no sentimento", capital imobilizado ou perda de oferta de alto giro.

**Solução.** Pipeline híbrido — heurística local + extração resiliente (texto ou visão, ambos online) + motor determinístico em Python — orquestrado por máquina de estados no LangGraph, com pausa para revisão humana em ambiguidades e persistência relacional multi-tenant. O LLM lê; o Python decide.

---

## 2. Princípios de Blindagem (invariantes do projeto)

1. **Dinheiro nunca é `float`.** `Decimal` no domínio, `NUMERIC` no banco, arredondamento explícito (`ROUND_HALF_UP`, `quantize` a 2 casas) no único ponto de saída.
2. **O LLM não faz conta.** Toda aritmética financeira vive em código determinístico e testável.
3. **Isolamento é enforçado no banco.** RLS, não confiança no código de aplicação.
4. **Nada é descartado em silêncio.** Falha de extração vira exceção rastreável e vai ao HITL.
5. **O provider de LLM é plugável.** A camada de inferência é abstraída; hoje online (OpenAI) por causa do rastreio de custo, trocável sem reescrever o grafo.
6. **Toda saída é auditável.** Cada item carrega página de origem, método de extração e confiança.
7. **Custo é métrica de primeira classe.** Cada run é rastreado no LangSmith; a meta de custo é um `assert`, não uma promessa.

---

## 3. Arquitetura e Stack

| Camada | Tecnologia | Papel | Nota |
| --- | --- | --- | --- |
| Orquestração | **LangGraph** | Grafo cíclico com nós, arestas condicionais e `interrupt()` | Open source, roda local |
| Persistência de estado | **PostgresSaver** (checkpointer) | Snapshots por `thread_id` | Mesmo Postgres, schema `checkpoints` isolado |
| Banco | **PostgreSQL 16 + pgvector** | Relacional multi-tenant + busca vetorial de fallback | Open source, portável para qualquer nuvem |
| Migrations | **Alembic** | Versionamento de schema com rollback | — |
| Contratos | **Pydantic v2** (`with_structured_output`) | Schema estrito, tipos `Decimal`, `Enum` | — |
| Parsing texto | **pdfplumber** (texto, `lines`/`rects`) + **PyMuPDF/fitz** (rasterização) | Fatiamento, densidade textual, geometria de preço riscado, render p/ visão | Pillow **não** rasteriza PDF — trocado por PyMuPDF |
| Extração — caminho texto | **LLM online plugável** via camada única (default GPT-4o mini) | Estruturação do recorte textual já fatiado | Modelo barato sobre pouco token = base do custo sub-centavo |
| Extração — caminho imagem | **Visão online plugável** (GPT-4o / GPT-4o mini vision) | Detecta layout, separa o relevante, extrai texto de páginas-imagem | Acionada só nas páginas sem texto; visão local fica como opção futura |
| Cálculo financeiro | **Python puro + `Decimal`** | Variação de custo, markup, desembolso, cobertura de estoque | — |
| Observabilidade / Custos | **LangSmith** (`LANGCHAIN_TRACING_V2=true`) | Tracing de latência, tokens/página, custo por run, datasets de eval | Acoplamento SaaS assumido; camada de LLM abstraída permite trocar depois |
| Execução | **Docker + Docker Compose** | Isolamento e volumes persistentes | — |
| Dev / Debug | **`langgraph dev` + LangGraph Studio** | Rodar e depurar o grafo local, inspecionar estado por nó, testar o `interrupt()` com time-travel | Sandbox de desenvolvimento; conecta ao LangSmith; requer `langgraph.json` |

---

## 4. Pipeline de Ingestão — Cascata Heurística

A decisão-chave da arquitetura. Uma triagem por página decide o caminho, protegendo a meta de custo: o modelo de visão (caro) só é acionado nas páginas que realmente são imagem.

```text
                    ┌─────────────────────────────┐
                    │  Página do PDF (loop)        │
                    └──────────────┬──────────────┘
                                   │
                 "A página tem texto extraível?"
        (pdfplumber: densidade de caracteres ≥ limiar)
                                   │
                 ┌─────────────────┴─────────────────┐
              SIM │                                   │ NÃO
                  ▼                                   ▼
   ┌──────────────────────────────┐   ┌──────────────────────────────────┐
   │ CAMINHO TEXTO (barato)       │   │ CAMINHO IMAGEM                   │
   │ 1. Filtro heurístico:        │   │ 1. Rasteriza a página (PyMuPDF,  │
   │    descarta capa/políticas   │   │    ~200–300 DPI)                 │
   │    e categorias fora das     │   │ 2. Visão ONLINE (GPT-4o mini):   │
   │    regras do tenant          │   │    - detecta layout/tabelas      │
   │ 2. Fatia a região relevante  │   │    - separa o que é relevante    │
   │ 3. Geometria: detecta preço  │   │    - extrai texto                │
   │    riscado (linha/rect sobre │   │ 3. Normaliza o texto extraído    │
   │    a bbox do preço)          │   │                                  │
   │ 4. LLM online estrutura o    │   │                                  │
   │    recorte (poucos tokens)   │   │                                  │
   └───────────────┬──────────────┘   └───────────────┬──────────────────┘
                   │                                   │
                   └──────────────┬────────────────────┘
                                  ▼
                    Texto/estrutura normalizada
                    → validação Pydantic → resto do grafo (§7)
```

**Limiar de decisão.** `pdfplumber` extrai os caracteres da página; se caracteres úteis por página `< LIMIAR` (ou cobertura de área textual baixa), a página é tratada como **imagem**. O limiar é configurável e faz parte do dataset de eval (páginas de borda).

**Por que isso segura o custo sub-centavo.** O caminho texto é o barato: o Python entrega só o recorte relevante, o "preço riscado" sai por geometria (sem token), e o LLM online recebe pouquíssimo contexto. A visão online — mais cara — só roda nas páginas que de fato são imagem, e ainda assim só nas relevantes. Cada centavo aparece no LangSmith taggeado por `lote_id`, então a otimização vira evidência, não alegação. Os dois caminhos convergem no mesmo contrato Pydantic; tudo a jusante é idêntico.

---

## 5. Modelagem de Dados e Multi-Tenancy

Isolamento **lógico enforçado por RLS**: um a milhares de lojistas no mesmo banco, sem vazamento — e sem depender de o código lembrar do `WHERE tenant_id`.

**Tabelas de negócio (PostgreSQL 16):**

- `tenants` — `id`, `nome_loja`, `data_criacao`.
- `fornecedores` *(nova)* — `id`, `tenant_id`, `nome`, `identificador_externo`.
- `regras_compra` — `tenant_id`, `markup_minimo_alvo NUMERIC(7,4)`, `teto_orcamento NUMERIC(14,2)`, `categorias_alvo`, `marcas_bloqueadas`, `cobertura_estoque_baixa_dias INT` *(define "estoque baixo")*.
- `catalogo_interno` — `tenant_id`, `sku_interno`, `ean`, `descricao`, `ultimo_preco_pago NUMERIC(14,2)` *(custo)*, `preco_venda_referencia NUMERIC(14,2)` *(nova — usada no portão de markup)*, `estoque_atual_qtd INT` *(nova)*, `giro_medio_dias INT`. Embedding de descrição em coluna `vector` (pgvector) apenas para fallback.
- `depara_produtos` — `tenant_id`, `fornecedor_id`, `sku_fornecedor`, `sku_interno`, `validado_por_humano BOOL`.
- `lotes` *(nova)* — `id`, `tenant_id`, `fornecedor_id`, `hash_pdf` *(idempotência)*, `num_paginas`, `custo_inferencia_run NUMERIC(14,4)` *(custo real do run, do LangSmith)*, `status`, `data_criacao`.
- `historico_ofertas` — `tenant_id`, `lote_id`, `sku_fornecedor`, `preco_unitario NUMERIC(14,2)`, `status_decisao`, `pagina_origem INT` *(nova)*, `metodo_extracao` *(nova: `texto` | `visao_online`)*, `confianca_extracao NUMERIC(5,4)` *(nova)*, `data_extracao`.
  - **Constraint única** `(tenant_id, lote_id, sku_fornecedor)` — reprocessar não duplica.

**RLS (esqueleto).**

```sql
ALTER TABLE historico_ofertas ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON historico_ofertas
  USING (tenant_id = current_setting('app.current_tenant')::int);
-- a aplicação faz SET LOCAL app.current_tenant = '<id>' por transação/thread
```

> **Defesa.** "Isolamento lógico com RLS enforçando na camada de dados" é resposta de arquiteto; "coloco `tenant_id` e filtro no código" é resposta de júnior. O checkpointer do LangGraph não tem coluna de tenant — o isolamento ali é a convenção de `thread_id` (§7), documentada como a única barreira daquele componente.

---

## 6. Contratos Pydantic (tipos estritos)

```python
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, condecimal

Money = condecimal(max_digits=14, decimal_places=2)

class MetodoExtracao(str, Enum):
    TEXTO = "texto"
    VISAO_ONLINE = "visao_online"

class StatusAcao(str, Enum):
    COMPRA_APROVADA = "COMPRA_APROVADA"
    IGNORAR = "IGNORAR"
    REVISAO_MANUAL = "REVISAO_MANUAL"

class ItemExtraido(BaseModel):
    modelo_codigo: str = Field(description="SKU do fornecedor, ex: LEF-366C")
    descricao: str
    quantidade_caixa: int = Field(default=1, ge=1, description="Múltiplo de compra / peças por caixa")
    preco_regular: Optional[Money] = None
    preco_oferta: Optional[Money] = None
    esgotado: bool = False
    pagina_origem: int
    metodo_extracao: MetodoExtracao
    confianca: condecimal(max_digits=5, decimal_places=4) = Decimal("1.0")

class RecomendacaoItem(BaseModel):
    sku_fornecedor: str
    sku_interno: Optional[str] = None
    preco_unitario_final: Money
    desembolso_minimo_lote: Money
    variacao_custo_percentual: condecimal(max_digits=7, decimal_places=4)     # sinal primário
    markup_estimado_percentual: Optional[condecimal(max_digits=7, decimal_places=4)] = None  # secundário
    status_acao: StatusAcao
    justificativa: str
```

**Output final do grafo.** Relatório consolidado: itens aprovados, descarte justificado e lista de exceções pendentes no `interrupt()` — cada linha com proveniência e confiança.

---

## 7. Topologia do LangGraph

1. **`triagem_paginas`** (Python) — classifica cada página em `TEXTO` ou `IMAGEM` pela densidade de caracteres (pdfplumber).
2. **`caminho_texto`** — filtro heurístico (descarta capa/políticas/categorias fora das regras), fatia a região relevante, detecta preço riscado por geometria (`lines`/`rects` sobre a bbox), e chama o **LLM online barato** para estruturar o recorte em `List[ItemExtraido]`.
3. **`caminho_imagem`** — rasteriza (PyMuPDF), roda o **modelo de visão online** (layout + extração de texto), normaliza para o mesmo `List[ItemExtraido]`.
4. **`extrator_batch`** (Send API, paralelo com **semáforo/limite de concorrência**) — dispara as páginas relevantes; o limite protege contra rate limit e picos de custo.
5. **`validador_contrato`** (Pydantic) — valida tipos. Em falha, correção cirúrgica com **`max_retries`**; esgotado, roteia ao dead-letter (nó 9).
6. **`resolucao_entidades`** (cascata) — **EAN exato → `pg_trgm` (trigrama) → embedding pgvector (fallback)**. Registra o método que casou.
7. **`motor_regras_financeiras`** (Python + `Decimal`, §8).
8. **Aresta condicional `roteador_incerteza`:**
   - `variação de custo favorável` **e** `cobertura ≤ regra` (estoque baixo) **e** (`markup ≥ meta` quando há preço de venda) → `aprovar_compra`.
   - `esgotado`, **ou** oferta acima do histórico sem giro que justifique, **ou** `markup < piso` → `descartar`.
   - `sem histórico de custo` (produto novo), **ou** `preço de venda ausente/suspeito`, **ou** `match indefinido`, **ou** `confiança < limiar` → `pausa_hitl`.
9. **`pausa_hitl`** (`interrupt()`) — congela o estado com `thread_id = f"tenant_{tenant_id}_lote_{lote_id}"`. **Retomada concreta:** endpoint `POST /resume/{thread_id}` (ou CLI `resume`) que injeta a decisão humana e continua o grafo. O dead-letter também desemboca aqui.
10. **`persistir_auditoria`** — grava os registros estruturados no Postgres (respeitando a constraint de idempotência) e finaliza.

---

## 8. Motor Financeiro Determinístico

Toda conta em `Decimal`, `quantize(Decimal("0.01"), ROUND_HALF_UP)` no ponto de saída.

**Decisão de métrica (margem vs markup).** As duas são a mesma informação — `markup = margem / (1 − margem)` — então nenhuma é "mais determinística" no cálculo. O que muda é de qual dado dependem. O dado de maior confiança é o **custo** (preço de oferta extraído e `ultimo_preco_pago`); o preço de venda é o mais frágil (velho ou ausente). Por isso a lógica ancora no custo:

```
# Sinal PRIMÁRIO (100% determinístico — não precisa de preço de venda):
Variação de custo (%) = (ultimo_preco_pago − preco_oferta) / ultimo_preco_pago × 100
  → responde direto "é boa oferta? estou pagando menos que de costume?"

# Desembolso:
Desembolso mínimo do lote = preco_oferta × quantidade_caixa

# Sinal SECUNDÁRIO de rentabilidade (só quando há preço de venda confiável):
Markup potencial (%) = (preco_venda_referencia − preco_oferta) / preco_oferta × 100
  → o lojista raciocina em markup ("quanto marco em cima do custo"); o custo é o número seguro

# Estoque:
Cobertura (dias) = estoque_atual_qtd × giro_medio_dias
  → "estoque baixo" quando cobertura ≤ regras_compra.cobertura_estoque_baixa_dias
```

**Regra de decisão** prioriza os dados de maior confiança (variação de custo e giro), e usa o markup como portão só quando o preço de venda existe:

- **Aprovar:** oferta abaixo do histórico de custo **e** estoque baixo (giro justifica) **e**, havendo preço de venda, markup ≥ meta.
- **Ignorar:** esgotado, ou oferta acima do histórico sem giro que justifique, ou markup < piso.
- **Revisão HITL:** produto novo (sem histórico de custo), preço de venda ausente/suspeito, ou alta atratividade sem dado suficiente — **nunca auto-rejeita por falta de dado**.

> **Defesa.** Guardamos custo e venda separados no schema. A meta em `regras_compra` fica como **markup sobre custo** (linguagem do lojista, dado mais confiável); se algum tenant pensar em margem sobre venda, convertemos internamente. O gatilho de "boa oferta" é a variação de custo — o único sinal que nunca depende de dado frágil.

---

## 9. Observabilidade, Custos e Evals

**Tracing.** Instrumentação nativa por **LangSmith** (`LANGCHAIN_TRACING_V2=true`), escolhido deliberadamente para rastrear custo e latência por execução — é o painel que transforma "otimizei custo" em evidência. Trade-off assumido: LangSmith é SaaS proprietário; como a camada de LLM está abstraída, trocar o backend de tracing depois não mexe no grafo. Tagging por `tenant_id`, `fornecedor` e `lote_id` para rateio de despesa de inferência.

**Meta de custo.** Objetivo: **< US$0,01 por run** (um run = um catálogo/lote). Alavancas que tornam isso viável: (1) a triagem manda ao modelo caro só as páginas relevantes; (2) o caminho texto usa modelo online barato (GPT-4o mini) sobre o recorte já fatiado; (3) preço riscado por geometria, sem token; (4) visão online só nas páginas que são de fato imagem. Métricas de acompanhamento no LangSmith: `Total Tokens / Página Útil` e custo acumulado por `lote_id`.

> **Honestidade de escopo.** Sub-US$0,01/run é realista para catálogos majoritariamente textuais; catálogos longos e 100% imagem vão estourar. Nesse caso a métrica honesta é **custo por página relevante** com um **teto de orçamento por run**. Ambas viram `assert` no eval.

**Suíte de Evals:**

- **Golden Dataset dividido:** páginas com texto extraível **e** páginas escaneadas/imagem (grids densos, itens esgotados, preços promocionais/riscados — ex.: catálogo de fones Lehmox). As de borda testam o limiar de triagem.
- **Eval de extração (pytest):** acurácia de `quantidade_caixa`, `preco_oferta`, flag `esgotado` — reportada por caminho (texto vs visão online).
- **Eval de resolução de entidade:** % de casamento correto de SKU interno, por método (EAN/trigrama/embedding).
- **Eval do roteador:** dado input conhecido, a decisão (`APROVADA`/`IGNORAR`/`REVISAO`) bate com o gabarito.
- **Eval de resiliência numérica:** nenhuma divergência centesimal — garantida pelo uso de `Decimal`.
- **Eval de custo:** `assert custo_por_run < 0.01` (e custo por página relevante abaixo do teto).

---

## 10. Robustez Operacional

- **Idempotência:** `hash_pdf` no lote + constraint única por item; reprocessar o mesmo catálogo não duplica histórico.
- **Retentativas com backoff** e timeout nas chamadas de LLM/visão; **semáforo** de concorrência no batch.
- **Dead-letter:** item que falha extração após `max_retries` não é descartado — vira exceção rastreável e vai ao HITL.
- **Migrations Alembic** para todo o schema; nada de `CREATE TABLE` ad hoc em produção.
- **Config via `pydantic-settings`**; segredos em `.env` no PoC, com nota de cofre (secret manager) para produção.
- **Logging estruturado** (JSON) com `tenant_id`/`lote_id` em todo evento.

---

## 11. Estrutura Inicial do Repositório

```text
leitor-catalogos/
├── docker-compose.yml        # PostgreSQL 16 + pgvector + volumes persistentes
├── pyproject.toml            # langgraph, langchain, langsmith, openai, pydantic, pdfplumber, pymupdf, alembic
├── .env.example              # OPENAI_API_KEY, LANGCHAIN_API_KEY, LANGCHAIN_TRACING_V2, DATABASE_URL
├── alembic/                  # migrations versionadas
├── src/
│   ├── core/
│   │   ├── config.py         # pydantic-settings
│   │   ├── database.py       # SQLAlchemy + session; SET LOCAL app.current_tenant (RLS)
│   │   └── llm.py            # camada única de provider (online, plugável)
│   ├── models/
│   │   ├── domain.py         # tabelas relacionais + RLS
│   │   └── schemas.py        # Pydantic (Decimal, Enum, proveniência)
│   ├── ingestion/
│   │   ├── triagem.py        # classifica página texto vs imagem
│   │   ├── caminho_texto.py  # heurística, fatiamento, geometria de preço riscado
│   │   └── caminho_imagem.py # rasterização (PyMuPDF) + visão online
│   ├── services/
│   │   ├── resolucao.py      # EAN → trigrama → embedding
│   │   └── finance.py        # aritmética determinística (Decimal)
│   └── graph/
│       ├── state.py          # AgentState
│       ├── nodes.py          # nós do pipeline
│       └── workflow.py       # montagem do grafo, arestas, checkpointer, interrupt
├── tests/
│   ├── test_triagem.py
│   ├── test_finance.py       # resiliência numérica (Decimal)
│   ├── test_resolucao.py
│   ├── test_router.py
│   └── eval_pipeline.py      # runner de evals + assert de custo por run
└── README.md
```

---

## 12. Ambiente de Execução e Ferramentas de Dev

**PoC roda 100% local.** Docker Compose sobe Postgres 16 + pgvector; o grafo roda sob **`langgraph dev`** (servidor de desenvolvimento local) e é depurado no **LangGraph Studio** — visualização do grafo, execução passo a passo, inspeção de estado por nó e, principalmente, teste do `interrupt()`/HITL com *time-travel* (rebobinar e re-executar um ponto de falha sem recomeçar). Requer um `langgraph.json` na raiz apontando para o grafo. Traces e custo por run vão para o **LangSmith**.

**Graduação para produção.** O caminho é o **LangGraph Platform** (renomeado **"LangSmith Deployment"** em out/2025), com quatro níveis: **Developer** (free, ~100k execuções de nó/mês — suficiente para hospedar o PoC), **Cloud SaaS** (Plus/Enterprise), **Hybrid** (control plane gerenciado, dados no próprio VPC — Enterprise) e **Fully Self-Hosted** (Enterprise). O PoC **não** depende do Platform; ele é a opção de deploy gerenciado quando o produto escalar.

---

## 13. Onboarding do Tenant e Origem dos Dados

O grafo das §4–§10 é o **fluxo de run** (processa um PDF). Ele pressupõe que a configuração e o catálogo interno do tenant **já existem** — o que exige um **fluxo de onboarding** separado. A regra de ouro: o tipo de dado decide o canal de entrada.

**A) Regras de compra → linguagem natural.** Poucas variáveis, alto valor de UX. O lojista descreve o negócio em texto livre e um LLM com structured output preenche `regras_compra`.

*Exemplo (e-commerce de eletrônicos):*
> "Vendo eletrônicos — fones, caixas de som, carregadores e cabos. Só compro com markup mínimo de 40% sobre o custo. Não trabalho com a marca Xtrad. Teto de R$ 5.000 por pedido. Estoque baixo = menos de 15 dias de cobertura."

```python
class RegrasCompra(BaseModel):
    categorias_alvo: list[str]                                        # ["fones", "caixas de som", "carregadores", "cabos"]
    markup_minimo_alvo: condecimal(max_digits=7, decimal_places=4)    # 40.0 (%)
    teto_orcamento: Money                                            # 5000.00
    marcas_bloqueadas: list[str] = []                                # ["Xtrad"]
    cobertura_estoque_baixa_dias: int = 15
```

**B) Catálogo interno → import estruturado (não é NL).** Estoque e histórico têm milhares de SKUs e não cabem em prompt. Origem: CSV/Excel exportado do ERP ou marketplace do lojista (Bling, Tiny, Mercado Livre, Shopify) ou integração por API, mapeado para `catalogo_interno`. A NL, se entrar aqui, é só para configurar o **mapeamento de colunas** — nunca para inserir linha a linha.

**Timing dos fluxos:**

- **Onboarding** (uma vez): regras via NL + carga inicial do catálogo interno. Regras podem ser reeditadas por NL a qualquer momento.
- **Sync** (periódico): atualização do catálogo interno (preços, estoque, giro).
- **Run** (a cada PDF de fornecedor): o grafo das §4–§10 dispara, lendo regras + catálogo já persistidos. No run, o lojista idealmente só sobe o PDF e escolhe o fornecedor.
- **Override opcional por lote** (NL): instrução pontual ("nesse lote, foca em fones e ignora o resto") mesclada às regras salvas.

> No repositório, isso vive em um `src/onboarding/` separado do `src/graph/` — dois entrypoints (configurar tenant vs. processar catálogo), mesmo banco.

---

## 14. Riscos Residuais e Próximo Passo

- **Custo em catálogos-imagem longos:** o run sub-centavo vale para catálogos textuais; medir no LangSmith e, se estourar, adotar teto de orçamento por run e/ou reconsiderar visão local para volume.
- **Custo do embedding de fallback:** medir quanto do casamento resolve só por EAN/trigrama antes de ligar o pgvector em volume.
- **Limiar de triagem texto/imagem:** calibrar com páginas de borda reais.

**Próximo passo prático:** subir `docker-compose.yml` (Postgres 16 + pgvector), criar a migration inicial (Alembic) com as tabelas e políticas RLS, configurar o LangSmith, e codificar o `triagem.py` + `caminho_texto.py` com o fatiador heurístico e a detecção geométrica de preço riscado.
