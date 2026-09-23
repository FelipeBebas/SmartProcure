# Agente de Onboarding de Fornecedor — harness (Fase 4)

Scaffold **rodável** do loop evaluator-optimizer. Roda offline com backends mock
(sem API) pra você ver o agente errar, levar feedback do gate, corrigir e
convergir — e ver o HITL disparar quando ele não converge.

## Rodar

```bash
# deps: langgraph (+ pydantic, já no spike)
uv add langgraph        # ou: pip install langgraph

uv run python run.py                # cenário que converge sozinho (2 tentativas)
uv run python run.py --forcar-hitl  # cérebro travado -> cai no HITL (interrupt)
```

## O grafo

```
perceber -> bootstrap_gabarito -> propor -> rodar -> avaliar -> GATE
                                     ^                             |
                                     |          falhou & tent<N    |
                                     +-----------------------------+
   GATE: passou   -> registrar -> END
         esgotou  -> hitl (interrupt) -> registrar -> END
```

## As tools

| Tool | Tipo | Custo | Papel |
|---|---|---|---|
| `perceber` | visão (cérebro) | LLM | observa layout: cor RGB/CMYK, rótulo qtd, SKU, nº colunas |
| `bootstrap_gabarito` | visão (cérebro) | LLM | levanta ~5 itens de régua (resolve "como sei que acertei") |
| `propor_config` | LLM (cérebro) | LLM | observações + feedback -> `ExtractionConfig` (não código) |
| `rodar_extracao` | motor `det_parse_cfg` | **US$0** | extração determinística consumindo a config |
| `avaliar` | função pura | US$0 | saída vs gabarito -> recall / precisão / erros silenciosos |

Gate: `recall>=98% AND precisao>=98% AND erros_silenciosos==0`.

## Limites do agente (de propósito)

1. Não escreve/executa código — só emite `ExtractionConfig` validada por Pydantic.
2. Não lê preço nem casa preço↔produto — isso é o motor determinístico.
3. Não decide compra — fora de escopo (regra determinística; lojista decide).
4. Não avança no incerto — abaixo do gate por N tentativas, para e chama HITL.
5. Não confia na auto-confiança do LLM (E05/E08) — o gate usa a régua bootstrapada.
6. Um fornecedor por vez; config registrada roda de graça nos catálogos futuros dele.

## Arquivos

- `config.py`   — `ExtractionConfig` (o contrato) + configs de exemplo (passo 1 ✔)
- `tools.py`    — tools + backends mock + costuras reais (`>>> REAL <<<`)
- `graph.py`    — grafo LangGraph, gate e HITL via `interrupt()` (passo 3 ✔)
- `run.py`      — entrypoint (demo offline)

## O que falta pra virar real (próximos gates)

- **`engine.py` = `det_parse_cfg`** — generalizar `spike/det_parse.py` p/ ler a
  config (incl. cor CMYK). Gate: validar contra LEHMOX **e** KNUP com 2 configs. (passo 2)
- **`CerebroOpenAI`** — ligar visão/LLM reais (reaproveita `spike/spike_extract.py`).
- **Trace LangSmith** — `@traceable` nos nós; o gate e o custo vivem lá. (passo 4)
- **Persistência** — `registrar` grava a config por fornecedor (JSON/Postgres).
