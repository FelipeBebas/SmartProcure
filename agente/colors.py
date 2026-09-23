"""Interpretação de cor do motor — UNIVERSAL, agnóstica de espaço de cor.

Separação de responsabilidades (pedido do Felipe):
  - to_rgb()          = encanamento do MOTOR. Lê o valor que o pdfplumber
                        devolve (non_stroking_color) — RGB, CMYK, cinza — e
                        normaliza p/ RGB. Não decide nada de negócio.
  - is_offer_color()  = aplica a REGRA da config do fornecedor (qual canal é o
                        da oferta e os limiares). "Avermelhado = oferta" é regra
                        DAQUELE catálogo, não lei universal.

Por que existe: o mesmo vermelho visual chega como (1,0,0) num PDF feito em
ferramenta de tela e como (0,1,1,0) num feito em ferramenta de impressão (CMYK).
O is_red antigo só entendia a tupla de 3 -> perdia a oferta do KNUP.
"""
from __future__ import annotations
from typing import Optional, Tuple


def _f(x) -> float:
    """Converte p/ float (trata Decimal, int, str)."""
    return float(x)


def to_rgb(color) -> Optional[Tuple[float, float, float]]:
    """Normaliza qualquer cor do pdfplumber p/ (r,g,b) em [0,1]. None se indefinível.

    Casos reais tratados:
      None                      -> None      (sem cor declarada)
      número                    -> cinza (DeviceGray)
      (g,)                      -> cinza
      (r,g,b)                   -> RGB (DeviceRGB / ICCBased 3 canais)
      (c,m,y,k)                 -> CMYK -> RGB
      ([...],) / aninhado       -> desembrulha 1 nível
      valores em 0..255         -> reduz p/ 0..1
    """
    if color is None:
        return None
    # número solto = cinza
    if isinstance(color, (int, float)):
        v = _clamp01(_f(color))
        return (v, v, v)
    try:
        seq = list(color)
    except TypeError:
        return None
    if not seq:
        return None
    # desembrulha 1 nível: ((r,g,b),) ou [[c,m,y,k]]
    if len(seq) == 1 and isinstance(seq[0], (list, tuple)):
        seq = list(seq[0])
    try:
        vals = [_f(x) for x in seq]
    except (TypeError, ValueError):
        return None
    # escala 0..255 -> 0..1 (só afeta RGB/cinza; CMYK já é 0..1)
    if len(vals) in (1, 3) and max(vals) > 1.0:
        vals = [v / 255.0 for v in vals]
    if len(vals) == 1:
        v = _clamp01(vals[0])
        return (v, v, v)
    if len(vals) == 3:
        return tuple(_clamp01(v) for v in vals)  # type: ignore
    if len(vals) == 4:
        c, m, y, k = (_clamp01(v) for v in vals)
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def is_offer_color(color, canal: str = "r",
                   alvo_min: float = 0.8, outros_max: float = 0.35) -> bool:
    """REGRA da config: a cor é a cor de oferta deste fornecedor?
    Normaliza p/ RGB (universal) e testa: canal dominante alto, os outros baixos.
    canal='r' cobre LEHMOX/KNUP (oferta vermelha). Trocar canal/limiares p/ outro
    fornecedor cuja oferta seja, digamos, verde ou laranja."""
    rgb = to_rgb(color)
    if rgb is None:
        return False
    idx = {"r": 0, "g": 1, "b": 2}.get(canal.lower(), 0)
    dom = rgb[idx]
    outros = [v for i, v in enumerate(rgb) if i != idx]
    return dom >= alvo_min and all(o <= outros_max for o in outros)
