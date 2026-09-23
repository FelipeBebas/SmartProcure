# SmartProcure — Arquitetura dos Agentes

Dois agentes, duas cadências. O Agente 1 **aprende a ler** uma fonte (episódico); o Agente 2 **responde perguntas** sobre os dados (recorrente). O Agente 1 alimenta o Agente 2.

---

## Agente 1 — Onboarding (auto-aprendizado, episódico)

Loop evaluator-optimizer. Aprende a **config de extração** de um fornecedor, registra, e só re-dispara em fornecedor novo ou layout mudado. O ciclo de auto-aprendizado é `propor → rodar → avaliar → corrigir` até um gate.

```mermaid
flowchart TD
    PDF["PDF de um fornecedor"] --> ID{{"identificar<br/>fornecedor conhecido?"}}
    ID -->|conhecido + config válida| FAST["usa config registrada<br/>(sem LLM, ~grátis)"]
    ID -->|desconhecido / layout mudou| PERC["perceber<br/>(visão lê o layout)"]

    PERC --> BOOT["bootstrap_gabarito<br/>(visão: régua de ~5 itens)"]
    BOOT --> PROP["propor config<br/>(LLM: observações -> ExtractionConfig)"]
    PROP --> RUN["rodar det_parse_cfg<br/>(motor determinístico)"]
    RUN --> EVAL["avaliar<br/>(saída vs gabarito)"]
    EVAL --> GATE{{"gate<br/>recall/precisão OK?"}}

    GATE -->|falhou & tentativas < N| PROP
    GATE -->|falhou & esgotou N| HITL["HITL · interrupt<br/>humano confirma/ajusta"]
    GATE -->|passou| REG["registrar config do fornecedor"]
    HITL --> REG

    FAST --> OUT["itens normalizados"]
    REG --> OUT
    OUT --> BRIDGE[("dados normalizados")]
```

O **auto-aprendizado** é a aresta `GATE --falhou--> PROP`: o agente lê o próprio erro (gap entre saída do motor e gabarito) e re-propõe a config. Registra ao passar. Uma vez registrada, o `identificar` manda pelo caminho rápido (sem LLM) — mas **valida em 1 página** antes de confiar (se falhar = layout mudou → volta ao loop). Status: motor (`det_parse_cfg`) validado 100% no LEHMOX real.

---

## Agente 2 — Analista (recorrente, com cercas)

Roda a cada pergunta do lojista. **Input aberto, espaço de ação fechado.** 6 cercas; as únicas saídas com número passam obrigatoriamente por uma ferramenta determinística.

```mermaid
flowchart TD
    CHIPS["chips de exemplo<br/>(só preenchem a caixa)"] -.-> U
    U["lojista — pergunta em TEXTO LIVRE"] --> G1

    G1{{"CERCA 1 · guarda de entrada<br/>vazio / longo / lixo?"}}
    G1 -->|inválida| R1["recusa elegante"]
    G1 -->|ok| G2

    G2{{"CERCA 2 · guarda de escopo<br/>cabe nas 4 ferramentas?"}}
    G2 -->|fora| R2["recusa + lista o que consigo fazer"]
    G2 -->|no escopo| PLAN["agente PLANEJA<br/>(LLM decide quais tools)"]

    subgraph ACAO["CERCA 3 · ação FECHADA — só estas 4"]
        T1["variacao_preco"]
        T2["ranking_oportunidades"]
        T3["otimizar_compra · orçamento"]
        T4["cobertura_categoria"]
    end

    BRIDGE[("dados normalizados<br/>do Agente 1")] --> ACAO
    PLAN --> ACAO
    ACAO --> LOOP{{"CERCA 4 · teto de iterações"}}
    LOOP -->|precisa mais| PLAN
    LOOP -->|suficiente| GROUND

    GROUND{{"CERCA 5 · guarda de fundamentação<br/>toda afirmação veio de uma tool?"}}
    GROUND -->|número sem tool| R3["não afirma"]
    GROUND -->|fundamentado| OUT2["resposta + a conta à mostra + incerteza"]
    OUT2 --> DEC["CERCA 6 · o LOJISTA decide"]
```

As 6 cercas: (1) entrada, (2) escopo, (3) ação fechada às 4 tools, (4) teto de iterações, (5) fundamentação (só afirma o que uma tool calculou), (6) decisão humana. Princípio: **limita-se a AÇÃO, não o INPUT** — o pior caso de qualquer entrada ruim é uma recusa elegante, nunca um número inventado.

---

## A ponte
`dados normalizados` é o contrato entre os dois: o Agente 1 (onboarding) enche; o Agente 2 (analista) consome. Nenhum dos dois é teatro — um aprende fontes novas, o outro entrega valor toda vez.
