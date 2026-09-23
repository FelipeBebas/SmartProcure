"""Etapa 1 do gate de acurácia: cria o GRUPO DE CONTROLE (dataset) no LangSmith.

Lê ground_truth_p5_p15.json e sobe 1 exemplo por PÁGINA:
  inputs  = {pdf, pagina}        -> o que o agente deve ler
  outputs = {itens: [...]}       -> a verdade (a régua)

Uso:  uv run python ls_upload_dataset.py
Requer LANGCHAIN_API_KEY (ou LANGSMITH_API_KEY) no .env.
"""
import json, os
from dotenv import load_dotenv
from langsmith import Client

load_dotenv()

DATASET = os.getenv("LS_DATASET", "leitor-catalogos-gt")
GT_FILE = os.getenv("GT_FILE", "ground_truth_p5_p15.json")
CAMPOS = ("modelo_codigo", "descricao", "quantidade_caixa", "preco_regular", "preco_oferta", "esgotado")


def main():
    gt = json.load(open(GT_FILE, encoding="utf-8"))
    by_page = {}
    for it in gt["itens"]:
        by_page.setdefault(it["pagina"], []).append(it)

    client = Client()
    if client.has_dataset(dataset_name=DATASET):
        ds = client.read_dataset(dataset_name=DATASET)
        print(f"[dataset] '{DATASET}' já existe (id={ds.id}). Apague no LangSmith para recriar.")
        return
    ds = client.create_dataset(dataset_name=DATASET,
                               description="Gabarito LEHMOX p5 (FONES) e p15 (TECLADO MOUSE) — extração de itens")
    inputs, outputs = [], []
    for pg, its in sorted(by_page.items()):
        inputs.append({"pdf": "LEHMOX 2026.04-completo.pdf", "pagina": pg})
        outputs.append({"itens": [{k: it[k] for k in CAMPOS} for it in its]})
    client.create_examples(dataset_id=ds.id, inputs=inputs, outputs=outputs)
    print(f"[dataset] criado '{DATASET}' (id={ds.id}) com {len(inputs)} exemplos — páginas {sorted(by_page)}")


if __name__ == "__main__":
    main()
