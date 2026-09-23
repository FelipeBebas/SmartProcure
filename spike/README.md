# Spike — Extração de Catálogo (de-risco)

Protótipo mínimo da **Fase 3** do template. Ataca **um** risco: *dá pra extrair
os produtos com acurácia suficiente e custo por run abaixo de 1 centavo?*
Se passar no gate, a fundação completa (Docker, migrations, RLS, grafo) se
justifica. Se não, ajustamos a premissa antes de investir.

## O que ele faz
1. **Triagem** por página (pdfplumber): tem texto extraível? → caminho **texto**; senão → caminho **imagem** (rasteriza com PyMuPDF).
2. **Extração** com modelo online (`gpt-4o-mini`) e structured output (Pydantic).
3. **Custo**: soma tokens e calcula custo/run e custo/página (trace opcional no LangSmith).
4. **Eval**: compara a extração com o gabarito e aplica o gate.

## Como rodar
```bash
pip install -r requirements.txt
cp .env.example .env          # e preencha OPENAI_API_KEY
python spike_extract.py catalogo.pdf --out results.json
python evaluate.py --truth ground_truth.json --pred results.json
```

## A métrica (por que não é "100% de acerto")
- **Recall de itens — gate duro, alvo ~100%.** Nenhum produto pode sumir: item que some = promoção que o lojista nunca vê = falha de produto.
- **Precisão de campos — alta, com HITL de rede.** Campo abaixo do limiar de confiança vai para revisão humana; erro não passa silencioso.
- **Erro silencioso — alvo 0.** Campo errado *com confiança alta* (passaria sem revisão). É o único erro inaceitável.
- **Custo/run — alvo < US$0,01.**

`evaluate.py` imprime um **VEREDITO DO GATE: GO / NO-GO** combinando os três.

## Gabarito (`ground_truth.json`)
É 100% dos produtos do catálogo rotulados à mão (veja `ground_truth.example.json`).
Cobertura de rótulo 100% ≠ meta de acurácia 100%. O gabarito é a régua; a meta é o recall.

## Gate de acurácia — nativo no LangSmith (E05)
O grupo de controle (dataset) e a medição do agente contra ele:
```bash
uv run python ls_upload_dataset.py                 # 1) cria o dataset "leitor-catalogos-gt" (2 exemplos: p5, p15)
uv run python ls_evaluate.py --experiment E05-leitura   # 2) roda o agente e mede contra o dataset
```
Abra o experimento em smith.langchain.com. Métricas: `recall_itens` (gate duro ~100%),
`precisao_campos`, `erros_silenciosos` (alvo 0). O PDF é lido de `../catalogo/LEHMOX 2026.04-completo.pdf`
(ou defina `CATALOGO_PDF`). Reaproveita `spike_extract.py` (leitura) e `evaluate.py` (matching).

> Se a sua versão do `langsmith` reclamar da assinatura dos evaluators `(run, example)`,
> troque para `(inputs, outputs, reference_outputs)` — as duas formas circulam nas docs.

## Notas
- No spike usamos `float` no preço (JSON não tem Decimal). No produto real a
  conversão para `Decimal` acontece na camada determinística.
- Preços podem ter mudado — confira a tabela `PRICES` em `spike_extract.py`.
