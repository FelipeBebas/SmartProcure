"""As 4 ferramentas determinísticas do Agente 2 (Analista) — CERCA 3.

Funções puras sobre a lista de itens normalizados. NADA de LLM aqui: é a
matemática que o agente orquestra e que ele tem permissão de afirmar "com
certeza". Cada uma devolve um dict com o resultado + a conta (pra fundamentação).

Item normalizado (do Agente 1):
  {modelo_codigo, descricao, quantidade_caixa, preco_regular, preco_oferta,
   esgotado, confianca, preco_base?}   # preco_base = preço efetivo do baseline
"""
from __future__ import annotations
from typing import Optional


def _eff(it: dict) -> Optional[float]:
    """Preço efetivo: oferta se houver, senão regular."""
    return it["preco_oferta"] if it.get("preco_oferta") is not None else it.get("preco_regular")


def _disponivel(it: dict) -> bool:
    return not it.get("esgotado") and _eff(it) is not None


# 1) ------------------------------------------------------------------
def variacao_preco(itens: list[dict]) -> dict:
    """Variação % do preço efetivo vs preco_base (baseline). Só itens que têm os dois."""
    linhas = []
    for it in itens:
        base, atual = it.get("preco_base"), _eff(it)
        if base and atual:
            var = (atual - base) / base * 100
            linhas.append({"sku": it["modelo_codigo"], "descricao": it["descricao"],
                           "de": base, "para": atual, "variacao_pct": round(var, 1)})
    linhas.sort(key=lambda x: x["variacao_pct"])
    return {"tool": "variacao_preco", "n": len(linhas), "itens": linhas}


# 2) ------------------------------------------------------------------
def ranking_oportunidades(itens: list[dict], top: int = 10) -> dict:
    """Ranqueia por desconto DENTRO do catálogo (regular -> oferta)."""
    op = []
    for it in itens:
        reg, off = it.get("preco_regular"), it.get("preco_oferta")
        if reg and off and off < reg and _disponivel(it):
            desc_pct = (reg - off) / reg * 100
            op.append({"sku": it["modelo_codigo"], "descricao": it["descricao"],
                       "regular": reg, "oferta": off, "desconto_pct": round(desc_pct, 1)})
    op.sort(key=lambda x: -x["desconto_pct"])
    return {"tool": "ranking_oportunidades", "n": len(op), "top": op[:top]}


# 3) ------------------------------------------------------------------
def otimizar_compra(itens: list[dict], orcamento: float, top: int = 20) -> dict:
    """Dado um orçamento, escolhe caixas que MAXIMIZAM a economia vs preço regular.
    Custo de 1 caixa = oferta * quantidade_caixa. Economia = (regular-oferta)*qtd.
    Heurística gulosa por economia/custo (não é knapsack exato — declarado)."""
    cand = []
    for it in itens:
        reg, off, qtd = it.get("preco_regular"), it.get("preco_oferta"), it.get("quantidade_caixa", 1)
        if reg and off and off < reg and _disponivel(it):
            custo = off * qtd
            econ = (reg - off) * qtd
            if custo > 0:
                cand.append({"sku": it["modelo_codigo"], "descricao": it["descricao"],
                             "qtd_caixa": qtd, "custo_caixa": round(custo, 2),
                             "economia": round(econ, 2), "ratio": econ / custo})
    cand.sort(key=lambda x: -x["ratio"])
    escolhidos, gasto, economia = [], 0.0, 0.0
    for c in cand:
        if gasto + c["custo_caixa"] <= orcamento:
            escolhidos.append(c); gasto += c["custo_caixa"]; economia += c["economia"]
    return {"tool": "otimizar_compra", "orcamento": orcamento,
            "gasto": round(gasto, 2), "sobra": round(orcamento - gasto, 2),
            "economia_total": round(economia, 2), "n_caixas": len(escolhidos),
            "compra": escolhidos[:top]}


# 4) ------------------------------------------------------------------
def cobertura_categoria(itens: list[dict], termos: list[str]) -> dict:
    """Filtra itens cuja descrição casa com as categorias do lojista."""
    termos_l = [t.lower() for t in termos]
    dentro = []
    for it in itens:
        d = it["descricao"].lower()
        cat = next((t for t in termos_l if t in d), None)
        if cat:
            dentro.append({"sku": it["modelo_codigo"], "descricao": it["descricao"],
                           "categoria": cat, "preco": _eff(it)})
    return {"tool": "cobertura_categoria", "n_no_escopo": len(dentro),
            "n_total": len(itens), "itens": dentro}


TOOLBOX = {
    "variacao_preco": variacao_preco,
    "ranking_oportunidades": ranking_oportunidades,
    "otimizar_compra": otimizar_compra,
    "cobertura_categoria": cobertura_categoria,
}
