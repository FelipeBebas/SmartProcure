# Tabela de Controle de Experimentos — Spike LeitorCatalogos

**Hipótese central:** *É possível ficar abaixo de US$0,01 por catálogo lido (por run) usando o SaaS (OpenAI + LangSmith)?*

**Disciplina (pra não torrar token):** todo experimento é (1) um run delimitado, (2) com um `--project` próprio no LangSmith, (3) registrado aqui **antes** de rodar (pergunta + input) e **depois** (números). Nada de rodar no escuro.

## Como rodar um experimento
```bat
cd "E:\@Vault\[02] Projetos & Entregas\[03] Produtos & SaaS\LeitorCatalogos\spike"
uv sync                                   # cria .venv e instala (1ª vez)
uv run python spike_extract.py "..\catalogo\LEHMOX 2026.04-completo.pdf" --project leitor-custo-E04 --out results_E04.json
uv run python evaluate.py --truth ground_truth.json --pred results_E04.json   # quando houver gabarito
```

## Definição das colunas
- **exp** — id (E01, E02...). **caminho** — texto / imagem / misto. **projeto** — tag do LangSmith.
- **custo/run** — custo do catálogo inteiro (o número que testa a hipótese). **custo/pág** — custo médio por página.
- **recall** — % de itens do gabarito encontrados (gate duro ~100%). **err.sil.** — campos errados com confiança alta (alvo 0).
- **veredito** — GO / NO-GO / parcial.

## Registro

| exp | data | pergunta | input | págs | caminho | modelo | projeto | custo/run | custo/pág | recall | err.sil. | veredito | notas |
|-----|------|----------|-------|------|---------|--------|---------|-----------|-----------|--------|----------|----------|-------|
| E01 | 2026-09-08 | página de texto é barata? | LEHMOX p9.pdf | 1 | texto | gpt-4o-mini | (sem trace) | 0.000653 | 0.000653 | — | — | custo OK | 16 itens; trace não capturado (env off) |
| E02 | 2026-09-08 | custo do caminho imagem | LEHMOX 2026.04-4.jpg | 1 | imagem | gpt-4o-mini | (sem trace) | 0.006108 | 0.006108 | — | — | custo OK/pág | 16 itens |
| E03 | 2026-09-08 | custo do caminho imagem (2) | produtos-mistos.jpg | 1 | imagem | gpt-4o-mini | (sem trace) | 0.006103 | 0.006103 | — | — | custo OK/pág | 16 itens |
| E04a | 2026-09-09 | mix real + estimativa (dry-run, 0 token) | LEHMOX completo.pdf | 29 | 28 txt + 1 img | gpt-4o-mini | (dry-run) | ~0.0243 (est.) | — | — | — | **ESTOURA 1¢** | 0 descartadas; tem camada de texto; custo dominado pela **quantidade de páginas**, não pela imagem |
| E04b | | confirmar E04a com LLM + trace | LEHMOX completo.pdf | 29 | misto | gpt-4o-mini | leitor-custo-E04b | | | — | — | | opcional — a estimativa já é sólida (~2,4¢) |
| E05 | | recall/precisão num subconjunto difícil | subset (a definir) | ~5 | misto | gpt-4o-mini | leitor-acuracia-E05 | | | | | | precisa do ground_truth.json |

## Leitura até aqui
- Texto ≈ **US$0,00065/página**; imagem ≈ **US$0,0061/página** (~9x).
- Catálogo real (E04a): **29 págs, 28 texto + 1 imagem**, estimativa **~US$0,0243/run**. A hipótese literal *< 1¢/run* **NÃO se sustenta** para um catálogo de ~30 págs — e o motivo aqui **não é a imagem** (só 1), é a **quantidade de páginas de texto** (28 × 0,00065 ≈ 1,8¢ sozinho).
- Reframe: ~2,4¢ por catálogo é economicamente **trivial**. O alvo de 1¢/run estava mal-calibrado. Duas saídas defensáveis: (a) **relaxar o gate** para um número realista por catálogo (ex.: < 5¢/run); ou (b) **baixar o custo do texto** — agrupar várias páginas de texto numa só chamada (menos overhead) ou parsear tabela limpa sem LLM.
- Alavancas de custo, revisadas: **quantidade de páginas** (batching) **e** proporção de imagem. A cascata heurística continua válida, mas para ESTE catálogo o gargalo é volume de texto, não visão.

## Resultados do gate de acurácia (E05–E09)

| exp | config | recall | erros silenciosos | precisão campos* | custo/run |
|-----|--------|--------|-------------------|------------------|-----------|
| E05 | texto cru + gpt-4o-mini | 100% | ~10 | baixa (preço embaralhado) | ~sub-cent |
| E06 | visão + gpt-4o-mini (img reduzida) | 100% | ~10 | igual ao texto (não resolveu) | ~1,2¢ (2 pág) |
| E07 | visão + gpt-4o full + img nítida | 100% | 1 | ~99% (3 deslizes de preço regular) | ~US$0,44 (29 pág) |
| E08 | texto ESTRUTURADO + mini | 100% | pior | pior (grade quebrada por linha) | ~sub-cent |
| **E09** | **Python determinístico (geometria + cor)** | **100%** | **0** | **100%** | **~US$0 (sem LLM)** |

*inclui preco_regular.

### Veredito
O caminho mais barato **é o melhor**: para PDF com texto vetorial, um parser determinístico em Python (agrupa por célula via coordenadas, oferta = preço vermelho, regular = preto/riscado, esgotado = "RS" sem número) resolve 100% a custo zero. Toda config de LLM (texto/visão, mini/full) erra a *associação* preço↔produto nessa grade densa; a visão-full só chega perto e custa ~US$0,44/run. Conclusão: **extração determinística é a via principal para PDFs legíveis; a visão fica reservada a PDFs-imagem e a fallback** quando o parser sair inconsistente.

### Ressalvas (defensável)
- Validado em 2 páginas de UM catálogo (LEHMOX). O parser tem heurísticas do layout (regex de SKU, 4 colunas, formato "RS"/"特价", vermelho=oferta) — outros fornecedores terão layouts diferentes e exigirão generalização ou template por fornecedor.
- Desenho robusto: Python determinístico como via rápida + **fallback para visão-full** quando a saída ficar inconsistente (preço faltando, colunas desalinhadas).
- Custo total gasto para fechar TODO o gate de acurácia: alguns centavos (E05–E08); E09 foi US$0.
