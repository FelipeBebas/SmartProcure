"""Contrato da CONFIG de extração (Fase 4, passo 1).

O AGENTE de onboarding NÃO escreve código: ele emite um objeto ExtractionConfig
validado por Pydantic, e o motor determinístico (engine.det_parse_cfg) consome
esse objeto. Isso mantém a extração determinística/segura (sem exec arbitrário)
e torna a config auditável e persistível por fornecedor.

Os knobs abaixo saíram das DIFERENÇAS REAIS que quebraram o det_parse do LEHMOX
no KNUP (memória do projeto / EXPERIMENTOS): rótulo de quantidade ("PC/CX" vs
"pçs/cx"), espaço de cor da oferta (RGB vs CMYK), formato de SKU (com/sem barra),
nº de colunas (4 vs 3). Cada diferença virou um parâmetro — não um novo parser.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class RegraOferta(BaseModel):
    """Como o motor decide qual preço é oferta (promocional) vs regular."""
    tipo: Literal["cor", "riscado"] = Field(
        default="cor",
        description="'cor' = oferta é o preço colorido (ex. vermelho); "
                    "'riscado' = regular é o preço com linha por cima.",
    )
    espaco_cor: Literal["RGB", "CMYK"] = Field(
        default="RGB",
        description="Espaço de cor do PDF. LEHMOX=RGB, KNUP=CMYK. "
                    "Só usado quando tipo='cor'.",
    )
    # Limiares do teste 'é a cor de oferta?'. Interpretados no espaco_cor acima.
    canal_alvo_min: float = Field(
        default=0.8, description="Mínimo do canal dominante da oferta (ex. R em RGB).")
    canais_outros_max: float = Field(
        default=0.35, description="Máximo dos demais canais (ex. G,B em RGB).")


class ExtractionConfig(BaseModel):
    """Tudo que o motor determinístico precisa para ler UM fornecedor.

    É isto que o agente propõe, corrige e — ao passar no gate — registra.
    """
    fornecedor: str = Field(description="Identificador do fornecedor, ex: 'LEHMOX'.")

    sku_regex: str = Field(
        description=r"Regex que reconhece um token de SKU. "
                    r"LEHMOX: ^[A-Z]{2,3}-\w*\d\w*$  |  KNUP pode ter barra.")
    qty_regex: str = Field(
        description=r"Regex com 1 grupo = peças por caixa. "
                    r"LEHMOX: (\d+)\s*PC/CX  |  KNUP: (\d+)\s*pçs/cx")
    money_regex: str = Field(
        default=r"(\d{1,4}[.,]\d{2})",
        description="Regex de valor monetário (1 grupo).")

    regra_oferta: RegraOferta = Field(default_factory=RegraOferta)

    esgotado_tokens: list[str] = Field(
        default_factory=lambda: ["RS", "RS$"],
        description="Tokens que, sem número, indicam item esgotado.")

    band_y_tol: float = Field(
        default=20.0, description="Tolerância em y (pt) p/ agrupar SKUs na mesma linha.")
    n_colunas_hint: Optional[int] = Field(
        default=None,
        description="Nº de colunas da grade, se conhecido (LEHMOX=4, KNUP=3). "
                    "None = auto-detecta pelos SKUs da faixa.")

    # Proveniência / gate
    origem: Literal["proposta_agente", "confirmada_hitl"] = "proposta_agente"
    tentativas_ate_gate: int = Field(
        default=0, description="Quantas iterações o agente levou até passar.")


# --- Configs de exemplo (semente / regressão) --------------------------------
# LEHMOX = a config que HOJE já dá 100% no det_parse (E09). Serve de âncora.
CONFIG_LEHMOX = ExtractionConfig(
    fornecedor="LEHMOX",
    sku_regex=r"^[A-Z]{2,3}-\w*\d\w*$",
    qty_regex=r"(\d+)\s*PC/CX",
    regra_oferta=RegraOferta(tipo="cor", espaco_cor="RGB"),
    n_colunas_hint=4,
)

# KNUP = HIPÓTESE de config (baseada nas diferenças observadas). NÃO validada
# ainda contra gabarito — validar é o gate do passo 2.
CONFIG_KNUP_HIPOTESE = ExtractionConfig(
    fornecedor="KNUP",
    sku_regex=r"^[A-Z]{2,4}[-/]?\w*\d\w*$",
    qty_regex=r"(\d+)\s*pçs/cx",
    regra_oferta=RegraOferta(tipo="cor", espaco_cor="CMYK"),
    n_colunas_hint=3,
)
