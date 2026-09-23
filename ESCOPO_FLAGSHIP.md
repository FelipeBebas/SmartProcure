# SmartProcure — Agente de Onboarding + Analista
**Escopo do POC — CONGELADO (Fase 1).** Fonte de verdade. Toda tentativa de inflar volta aqui.

## Objetivo (não muda)
Flagship **genuinamente agêntico, defensável e impressionante** para candidatura **AI Engineer / Cloud Solutions Architect (Oracle)**. Critério: defender cada decisão com evidência, e a agência ser **inevitável, não decorativa**. NÃO é pra vender/servir o lojista agora.

## A história que o POC conta (a frase do Felipe pro lojista)
O lojista sobe os PDFs dos fornecedores → o sistema lê e normaliza os dados → e responde, em linguagem natural e **com a matemática à mostra**: *"esses itens caíram de preço; com R$ X, a compra mais lógica é esta."* Quem decide é o lojista.

## Dois agentes — duas cadências (é isto que mata o "teatro")
**Agente 1 — Onboarding (episódico).** PDF de um fornecedor → percebe o layout → propõe uma **CONFIG de extração** (dados, não código) → roda o motor determinístico → se auto-avalia contra um gabarito → corrige e itera até um gate → HITL se não converge → **registra a config**. Roda raro: só quando entra fornecedor/layout novo. Depois, a extração daquele fornecedor roda determinística e de graça.

**Agente 2 — Analista (recorrente).** Roda **toda vez** que o lojista faz uma pergunta sobre os dados. Recebe a pergunta + os dados normalizados → **planeja quais ferramentas determinísticas chamar** → compõe a resposta fundamentada, mostra a conta e explicita a incerteza. É o que justifica "ter um agente" continuamente: a **pergunta é aberta**, então o plano é variável — não é relatório de template fixo.

### Regra de ouro (segurança + honestidade — e fala de entrevista)
O agente só afirma **com certeza** o que uma **ferramenta determinística calculou**. O LLM nunca inventa número: ele **orquestra ferramentas e narra**. A matemática decide; o lojista decide a compra.

### Toolbox determinístico do Analista (FECHADO na v1 — 4 ferramentas)
1. `variacao_preco` — variação % de um item vs baseline (preço anterior).
2. `ranking_oportunidades` — ordena por maior queda / melhor oportunidade.
3. `otimizar_compra` — dado um orçamento, melhor uso de R$ X (o "cabe R$ 5 mil").
4. `cobertura_categoria` — o que cai nas categorias que o lojista trabalha.

O relatório é a **síntese** que o agente monta escolhendo entre essas 4 — não um PDF fixo.

## Fonte de dados do POC
**LEHMOX** (grade com preço; `det_parse` já dá 100% — E09). Preferir **dois catálogos LEHMOX de datas diferentes** → delta de preço **real**. Sem o segundo, usar **baseline semente** (JSON) rotulado como semente.

## Fluxo
```
PDF fornecedor → [AGENTE 1: onboarding → config + itens normalizados]  (episódico)
                                     │
pergunta do lojista + itens ─────────┴──→ [AGENTE 2: analista → escolhe tools → resposta + conta]  (todo run)
                                                                      │
                                                          "com R$X, a compra mais lógica é..."
```
- Orquestração: **LangGraph** (grafos visíveis no **LangGraph Studio**; HITL via `interrupt()`).
- Motor determinístico: `det_parse_cfg` (geometria + cor; cor normalizada por `colors.py`, agnóstica de RGB/CMYK).
- Observabilidade/eval: **LangSmith** (extração medida contra gabarito; passos e custo do analista rastreados).

## Definition of Done (quando o POC ACABOU)
1. `langgraph dev` sobe; os grafos aparecem no Studio.
2. Jogar o PDF do LEHMOX → Agente 1 extrai os itens normalizados (visível no grafo).
3. Fazer uma pergunta de orçamento → Agente 2 escolhe ferramentas e devolve a recomendação em linguagem natural, com a conta à mostra e a incerteza explícita.
4. Tudo rastreado no LangSmith (com custo por etapa). **Acabou.**

## FORA do POC — congelado. Trabalho futuro nomeado (frases de entrevista, não código agora)
- Casar o mesmo produto **entre fornecedores** (entity resolution fuzzy/semântico).
- **Join de preço por SKU** de fonte externa (ex. site/tabela da KNUP) — o caso "catálogo de spec sem preço".
- **Visão** para sinais que só existem em pixel (carimbo "ESGOTADO" como imagem).
- Suportar **todo layout** / N fornecedores; histórico real de preço em banco.

## Defesa de entrevista (respostas prontas)
- *"Por que agente e não script?"* → Duas agências reais: aprender uma **fonte inédita** (layout imprevisível) e responder uma **pergunta aberta** escolhendo ferramentas. Onde o dado é estruturado, uso **determinismo** e provo com evals.
- *"Como sei que a resposta é confiável?"* → O LLM só afirma o que uma ferramenta calculou; extração passa por gate contra gabarito; tudo no LangSmith.
- *"Roda uma vez e pronto?"* → Não: onboarding é episódico (fornecedor novo), analista é recorrente (toda pergunta).

## Método (gates SDD — mantém)
Fase → gate; de-risco antes de construir. Gate humano (validar com lojista real + sênior). Gate de fatos: afirmação sobre "o mercado/o melhor" é hipótese até verificada em fonte/teste.
