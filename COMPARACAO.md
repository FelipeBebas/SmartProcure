# Excel vs NotebookLM vs LeitorCatalogos

Comparação honesta de três formas de responder às mesmas perguntas sobre o **mesmo catálogo** de fornecedor (LEHMOX 2023, 207 SKUs após dedup, PDF de texto vetorial com grade de cards, preços de oferta em vermelho e múltiplos de caixa).

**A tese não é "eu extraio melhor que um LLM".** Um LLM moderno (ex.: ChatGPT via web) extrai uma ou duas páginas corretamente. A tese é: **transformar isso num fluxo semanal repetível, verificável e operável pelo lojista é o produto** — e é isso que o LeitorCatalogos faz. Não reinventamos a roda (a extração é a roda); montamos o carro em volta dela.

---

## As 5 perguntas e o gabarito

O gabarito é a computação determinística sobre o catálogo extraído por geometria+cor (o mesmo que o LeitorCatalogos produz). Qualquer pessoa verifica os números no PDF.

| # | Pergunta | Resposta correta |
|---|---|---|
| 1 | Quantos produtos estão em oferta? | **60** |
| 2 | Top 5 por desconto % | LEY-1529 (−36,4%), LE-356, LEY-1550, LEY-1530 (−33,3%), LEY-1527 (−30,0%) |
| 3 | Custo de 1 caixa de cada item em oferta | **R$ 135.518,50** |
| 4 | LE-357: preço regular / oferta / qtd por caixa | **R$ 23,00 / R$ 18,00 / 300 un** |
| 5 | Melhor cesta com R$ 3.000 (maximizar economia) | gasta R$ 2.818,50, economiza R$ 1.513,50, 9 caixas |

O spread é proposital: contagem exaustiva, ranking global, aritmética pesada, consulta pontual (caso fácil) e otimização.

---

## Resultado — NotebookLM (RAG sobre texto)

| # | NotebookLM | Veredito |
|---|---|---|
| 1 | "o catálogo não especifica um número consolidado" | ❌ recusou — RAG não varre o todo |
| 2 | os 5 corretos, % corretos (ordem dos empates trocada) | ✅ **acertou** |
| 3 | R$ 105.159,20, sobre **38** itens | ❌ subcontou (38 de 60) e errou o total |
| 4 | R$ 23,00 / R$ 18,00 / **omitiu a quantidade por caixa** | 🟡 parcial |
| 5 | várias "opções", nenhuma ótima; sugeriu comprar avulso | ❌ não otimizou + violou a regra (compra é por caixa) |

**Placar: 1 acerto limpo, 1 parcial, 3 erros.** Mas o *como* ele erra é o mais revelador:

- **Exibit 1 (pergunta 3):** o NotebookLM achou **38** itens porque foi atrás dos tokens de texto "折后/特价" (chinês: "com desconto / preço especial") e **perdeu os 22 marcados só pela cor vermelha**. É a falha estrutural do RAG-sobre-texto: não enxerga a associação preço↔cor. Resultado: um total **confiantemente errado** (R$ 105k vs R$ 135k), com citação do lado dando aparência de rigor.
- **Exibit 2 (pergunta 5):** não só não otimizou — recomendou comprar 750 **unidades avulsas**, violando a realidade do domínio (compra é por caixa fechada). Conselho ativamente errado.
- **Exibit 3 (pergunta 4):** acertou os preços (retrieval simples, onde RAG vai bem) mas **largou a quantidade por caixa** — o número que decide a compra. Até no caso fácil, derrubou o campo operacional.

---

## Resultado — LLM → Google Sheets

Aqui a acurácia de extração **não é o gargalo** (um LLM moderno extrai uma página bem). O problema é outro, e é honesto:

**Verificabilidade em escala.** Extrair 2 páginas certo ≠ extrair 29 páginas / 356 itens certo — e você **não tem como saber**. Sem gabarito, se o LLM pular 10 itens ou trocar um preço no meio, o erro é **silencioso**: entra na planilha, gera um total errado com cara de certo, e ninguém pega. O LeitorCatalogos tem um **gate** (avalia a saída contra um mini-gabarito antes de confiar) — ele avisa quando não confia.

**Atrito e repetição.** Cada catálogo, toda semana, vira um pipeline manual:

| Passo | LLM → Google Sheets | LeitorCatalogos |
|---|---|---|
| Extrair | sobe PDF, prompt, copia/baixa CSV | sobe PDF (config já registrada) |
| Montar | importa no Sheets + **cria as fórmulas** | — (já responde) |
| Repetir na semana seguinte | **refaz tudo** (o chat é one-off) | roda de graça (aprendeu a config 1×) |
| Nova pergunta | edita fórmula na mão | pergunta em português |
| Quem opera | alguém que sabe promptar + Excel | o lojista |

O chat do LLM é **efêmero**: catálogo novo = recomeça do zero. O agente de onboarding do LeitorCatalogos **aprende a config do fornecedor uma vez** e todo catálogo futuro daquele fornecedor roda automático. É a diferença entre "fiz um truque hoje" e "montei um sistema que repete".

---

## Os eixos que importam

| Eixo | LLM → Excel | NotebookLM | LeitorCatalogos |
|---|---|---|---|
| Extração | LLM, boa num tiro, **não verificável** em escala | RAG sobre texto (perde a cor da oferta) | geometria+cor, **100% no gabarito**, com gate |
| Cálculo | Excel (trivial, você monta a fórmula) | LLM (erra agregação/otimização) | determinístico, exaustivo |
| Erro | **silencioso** na célula | confiante, com citação | recusa/gate — nunca inventa número |
| Exaustividade | sim, se a planilha estiver certa | não (top-k) | sim (todas as linhas) |
| Otimização (orçamento) | Solver manual | não faz | ferramenta dedicada |
| Repetição | refaz por catálogo | refaz por pergunta | aprende fornecedor 1× |
| Operável pelo lojista | não | em parte | sim (linguagem natural) |
| A conta à mostra | você montou | não | sim |

---

## Veredito

O NotebookLM responde "o que o documento diz" e acerta o pontual; erra — com confiança — tudo que exige varrer todas as linhas, entender a cor da oferta, ou calcular. O caminho LLM→Excel extrai bem num tiro, mas te deixa com um pipeline manual, não verificável e refeito toda semana. O LeitorCatalogos acerta as cinco porque **extrai por geometria+cor com gate de confiança, calcula de forma determinística, aprende o fornecedor uma vez e responde em português com a conta à mostra**.

## Honestidade / limitações

- **Hoje funciona para o fornecedor conhecido (LEHMOX).** Generalizar para um fornecedor novo é o papel do agente de onboarding (aprende a config por visão), que existe no desenho mas ainda não está ligado em produção.
- **Um LLM consegue extrair** — não afirmamos o contrário. O ponto é o fluxo (verificável, repetível, operável), não a esperteza de um tiro.
- **2 páginas extraídas certo não provam 29.** A ausência de um gate é justamente o risco que o LeitorCatalogos remove.

## Como reproduzir

Mesmo PDF (LEHMOX 2023), mesmas 5 perguntas, nas três ferramentas. Marque certo/errado contra o gabarito acima e cronometre PDF → resposta (e o custo de repetir na semana seguinte). Os números do gabarito saem do motor determinístico (`engine.det_parse_cfg` + `tools_analista`) sobre o PDF.
