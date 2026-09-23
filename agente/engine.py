"""Motor determinístico generalizado — det_parse que CONSOME uma ExtractionConfig.

É o port do spike/det_parse.py (que tinha a config cozida por dentro) para ler
os parâmetros de uma ExtractionConfig. Mesmo algoritmo (agrupa palavras por
coordenada; oferta = preço na cor de oferta; qtd/sku por regex), agora
parametrizado. Cor é interpretada por colors.py (agnóstico de RGB/CMYK).

O agente propõe a config; ESTE motor a executa. Sem exec de código arbitrário.
"""
from __future__ import annotations
import re
import pdfplumber

from config import ExtractionConfig
from colors import is_offer_color


def _money(text: str, money_re: re.Pattern):
    m = money_re.search(text.replace(",", "."))
    return float(m.group(1)) if m else None


def _eh_oferta(color, ro) -> bool:
    """Aplica a regra de oferta da config. 'cor' usa colors.is_offer_color
    (normaliza espaço de cor sozinho). 'riscado' fica p/ quando um fornecedor
    exigir — não é o caso do LEHMOX."""
    if ro.tipo == "cor":
        return is_offer_color(color, canal="r",
                              alvo_min=ro.canal_alvo_min, outros_max=ro.canais_outros_max)
    return False  # 'riscado' ainda não implementado (nenhum fornecedor do POC usa)


def parse_page_cfg(page, cfg: ExtractionConfig) -> list[dict]:
    """Extrai os itens de UMA página do pdfplumber usando a config."""
    sku_re = re.compile(cfg.sku_regex)
    qty_re = re.compile(cfg.qty_regex, re.I)
    money_re = re.compile(cfg.money_regex)
    ro = cfg.regra_oferta

    words = page.extract_words(extra_attrs=["non_stroking_color"])
    skus = [w for w in words if sku_re.match(w["text"])]
    if not skus:
        return []

    skus.sort(key=lambda w: w["top"])
    bands: list = []
    for w in skus:
        if bands and abs(w["top"] - bands[-1][0]) < cfg.band_y_tol:
            bands[-1][1].append(w)
        else:
            bands.append([w["top"], [w]])

    itens = []
    for bi, (btop, brow) in enumerate(bands):
        brow.sort(key=lambda w: w["x0"])
        centers = [(w["x0"] + w["x1"]) / 2 for w in brow]
        bounds = [-1e9] + [(centers[i] + centers[i + 1]) / 2 for i in range(len(centers) - 1)] + [1e9]
        ybot = bands[bi + 1][0] - 5 if bi + 1 < len(bands) else 1e9
        blk = [w for w in words if btop - 5 <= w["top"] < ybot]
        for ci, sku in enumerate(brow):
            lo, hi = bounds[ci], bounds[ci + 1]
            cell = [w for w in blk if lo <= (w["x0"] + w["x1"]) / 2 < hi]
            qty = 1
            reg = off = None
            esg = False
            desc = []
            precos = []
            for w in cell:
                t = w["text"]
                if w is sku:
                    continue
                mq = qty_re.search(t)
                if mq:
                    try:
                        qty = int(mq.group(1))
                    except (ValueError, IndexError):
                        pass
                    continue
                if t.strip() in cfg.esgotado_tokens:
                    esg = True
                    continue
                val = _money(t, money_re)
                if val is not None:
                    precos.append((val, _eh_oferta(w.get("non_stroking_color"), ro)))
                    continue
                if re.search(r"[A-Za-z]", t) and not t.startswith("特"):
                    desc.append(t)
            reds = [v for v, r in precos if r]
            blacks = [v for v, r in precos if not r]
            if reds:
                off = reds[0]
            if blacks:
                reg = blacks[0]
            if esg:
                reg = off = None
            itens.append({
                "modelo_codigo": sku["text"],
                "descricao": " ".join(desc)[:60],
                "quantidade_caixa": qty,
                "preco_regular": reg,
                "preco_oferta": off,
                "esgotado": esg,
                "confianca": 1.0,
            })
    return itens


def det_parse_cfg(pdf_path: str, cfg: ExtractionConfig, pages=None) -> dict[int, list[dict]]:
    """Roda o motor no PDF. pages = iterável 1-indexado, ou None p/ todas."""
    out = {}
    with pdfplumber.open(pdf_path) as pdf:
        idxs = pages if pages is not None else range(1, len(pdf.pages) + 1)
        for pg in idxs:
            out[pg] = parse_page_cfg(pdf.pages[pg - 1], cfg)
    return out
