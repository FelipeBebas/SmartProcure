"""Avaliador do spike.

Mede o que importa para o negócio:
  - RECALL DE ITENS (gate duro, alvo ~100%): nenhum produto do catálogo some.
  - PRECISÃO DE CAMPOS (alta, com HITL de rede): acerto em preço/qtd/esgotado
    entre os itens casados.
  - ERRO SILENCIOSO (alvo ~0): campo errado COM confiança alta (passaria sem
    cair no HITL). É o pecado que o produto não pode cometer.
  - CUSTO/RUN: do results.json.

Uso:
    python evaluate.py --truth ground_truth.json --pred results.json
"""
from __future__ import annotations
import argparse, json, re, unicodedata

PRECO_TOL = 0.01          # tolerância de centavo
CONF_HITL = 0.7           # abaixo disso o item iria para revisão humana


def norm_sku(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def index_by_sku(itens):
    idx = {}
    for it in itens:
        idx.setdefault(norm_sku(it.get("modelo_codigo", "")), it)
    return idx


def campo_ok(pred, truth, campo):
    pv, tv = pred.get(campo), truth.get(campo)
    if campo in ("preco_regular", "preco_oferta"):
        if tv is None:
            return pv is None
        if pv is None:
            return False
        return abs(float(pv) - float(tv)) <= PRECO_TOL
    return pv == tv


def main(truth_path, pred_path):
    truth = load(truth_path)
    pred = load(pred_path)
    truth_itens = truth["itens"] if isinstance(truth, dict) else truth
    pred_itens = pred["itens"]

    t_idx = index_by_sku(truth_itens)
    p_idx = index_by_sku(pred_itens)

    encontrados = [sku for sku in t_idx if sku in p_idx]
    perdidos = [t_idx[sku] for sku in t_idx if sku not in p_idx]     # falsos negativos = promoção perdida
    fantasmas = [p_idx[sku] for sku in p_idx if sku not in t_idx]    # alucinações
    recall = len(encontrados) / max(len(t_idx), 1)

    campos = ["preco_regular", "preco_oferta", "quantidade_caixa", "esgotado"]
    acertos = {c: 0 for c in campos}
    silenciosos = []
    for sku in encontrados:
        p, t = p_idx[sku], t_idx[sku]
        for c in campos:
            ok = campo_ok(p, t, c)
            acertos[c] += int(ok)
            if not ok and float(p.get("confianca", 1.0)) >= CONF_HITL:
                silenciosos.append({"sku": p.get("modelo_codigo"), "campo": c,
                                    "pred": p.get(c), "truth": t.get(c),
                                    "confianca": p.get("confianca")})
    n = max(len(encontrados), 1)
    precisao = {c: acertos[c] / n for c in campos}

    print("=" * 56)
    print("EVAL DO SPIKE")
    print("=" * 56)
    print(f"Itens no gabarito : {len(t_idx)}")
    print(f"Itens extraídos   : {len(p_idx)}")
    print(f"\n[GATE DURO] Recall de itens: {recall:6.1%}  (alvo ~100%)")
    if perdidos:
        print(f"  !! {len(perdidos)} PERDIDOS (promoção perdida):")
        for it in perdidos[:20]:
            print(f"     - {it.get('modelo_codigo')} | {it.get('descricao','')[:50]}")
    print(f"\nPrecisão de campos (entre {len(encontrados)} casados):")
    for c in campos:
        print(f"  {c:18}: {precisao[c]:6.1%}")
    print(f"\n[GATE] Erros silenciosos (campo errado + confiança alta): {len(silenciosos)}  (alvo 0)")
    for s in silenciosos[:20]:
        print(f"     - {s['sku']} | {s['campo']}: pred={s['pred']} vs truth={s['truth']} (conf={s['confianca']})")
    if fantasmas:
        print(f"\nItens fantasma (não existem no gabarito): {len(fantasmas)}")
    m = pred.get("meta", {})
    print(f"\nCusto/run: US${m.get('custo_run_usd','?')} | US${m.get('custo_por_pagina_usd','?')}/página")
    print("=" * 56)

    ok_recall = recall >= 0.999
    ok_silent = len(silenciosos) == 0
    ok_custo = float(m.get("custo_run_usd", 1)) < 0.01
    veredito = "GO" if (ok_recall and ok_silent and ok_custo) else "NO-GO / ajustar"
    print(f"VEREDITO DO GATE: {veredito}")
    print(f"  recall~100%={ok_recall} | zero_erro_silencioso={ok_silent} | custo<1c={ok_custo}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default="ground_truth.json")
    ap.add_argument("--pred", default="results.json")
    a = ap.parse_args()
    main(a.truth, a.pred)
