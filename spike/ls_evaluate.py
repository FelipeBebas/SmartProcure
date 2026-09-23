"""Etapa 2 do gate de acurácia: roda o AGENTE contra o grupo de controle e MEDE.

O agente (target) lê cada página do dataset e devolve os itens; os evaluators
comparam com a verdade e pontuam:
  - recall_itens     -> % dos itens do gabarito que o agente achou (gate duro ~100%)
  - precisao_campos  -> acerto em preco_oferta / quantidade_caixa / esgotado (entre casados)
  - erros_silenciosos-> campo errado COM confiança alta (alvo 0)

Uso:  uv run python ls_evaluate.py --experiment E05-leitura
Requer OPENAI_API_KEY e LANGCHAIN_API_KEY no .env. O PDF precisa estar acessível
localmente (default ../catalogo/LEHMOX 2026.04-completo.pdf; sobrescreva com CATALOGO_PDF).

Nota: a assinatura dos evaluators segue o formato (run, example). Se a sua versão
do SDK reclamar, troque para (inputs, outputs, reference_outputs) — ver README.
"""
import os, sys, argparse
from dotenv import load_dotenv
from langsmith import evaluate
from spike_extract import (_client, classify_pages, extract_text_page, extract_image_page,
                           page_png_b64, build_structured_text, extract_structured_page, MODEL)
from evaluate import norm_sku, campo_ok

load_dotenv()

PDF = os.getenv("CATALOGO_PDF", "../catalogo/LEHMOX 2026.04-completo.pdf")
DATASET = os.getenv("LS_DATASET", "leitor-catalogos-gt")
_oa = _client()


def target(inputs: dict) -> dict:
    """O agente: lê UMA página e devolve os itens extraídos."""
    pg = inputs["pagina"]
    metodo, texto = "imagem", None
    for i, m, t in classify_pages(PDF):
        if i == pg:
            metodo, texto = m, t
            break
    force_vision = os.getenv("FORCE_VISION", "").lower() == "true"
    mode = os.getenv("EXTRACT_MODE", "").lower()
    usar_visao = (metodo != "texto") or force_vision
    via = "VISAO" if usar_visao else ("TEXTO-ESTRUTURADO" if mode == "structured" else "TEXTO")
    print(f"[agente] pág {pg}: {via} | modelo={MODEL} "
          f"| DPI={os.getenv('RASTER_DPI', '180')} maxpx={os.getenv('MAX_IMG_PX', '2000')}", file=sys.stderr)
    if usar_visao:
        comp = extract_image_page(_oa, page_png_b64(PDF, pg), "jpeg")
    elif mode == "structured":
        comp = extract_structured_page(_oa, build_structured_text(PDF, pg))
    else:
        comp = extract_text_page(_oa, texto)
    return {"itens": [it.model_dump() for it in comp.choices[0].message.parsed.itens]}


def _idx(itens):
    d = {}
    for it in itens or []:
        d.setdefault(norm_sku(it.get("modelo_codigo", "")), it)
    return d


def recall_itens(run, example):
    pred, truth = _idx(run.outputs.get("itens")), _idx(example.outputs.get("itens"))
    achou = sum(1 for k in truth if k in pred)
    return {"key": "recall_itens", "score": achou / max(len(truth), 1)}


def precisao_campos(run, example):
    pred, truth = _idx(run.outputs.get("itens")), _idx(example.outputs.get("itens"))
    tot = ok = 0
    for k in truth:
        if k in pred:
            for c in ("preco_regular", "preco_oferta", "quantidade_caixa", "esgotado"):
                tot += 1
                ok += int(campo_ok(pred[k], truth[k], c))
    return {"key": "precisao_campos", "score": ok / max(tot, 1)}


def erros_silenciosos(run, example):
    pred, truth = _idx(run.outputs.get("itens")), _idx(example.outputs.get("itens"))
    n = 0
    for k in truth:
        if k in pred:
            for c in ("preco_regular", "preco_oferta", "quantidade_caixa", "esgotado"):
                if not campo_ok(pred[k], truth[k], c) and float(pred[k].get("confianca", 1.0)) >= 0.7:
                    n += 1
    return {"key": "erros_silenciosos", "score": n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default="E05-leitura")
    a = ap.parse_args()
    modo = "visao" if os.getenv("FORCE_VISION", "").lower() == "true" else (os.getenv("EXTRACT_MODE") or "texto")
    evaluate(target, data=DATASET,
             evaluators=[recall_itens, precisao_campos, erros_silenciosos],
             experiment_prefix=a.experiment,
             metadata={"modelo": MODEL, "modo": modo})
    print(f"[eval] experimento '{a.experiment}' enviado ao LangSmith (dataset '{DATASET}').")
