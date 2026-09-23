"""Contratos do spike.

Nota: no boundary de extração usamos float (JSON não tem Decimal). A conversão
para Decimal acontece na camada determinística do produto real — aqui o foco é
medir acurácia e custo da extração, não a aritmética financeira.
"""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class LLMItem(BaseModel):
    """Um produto como o modelo o extrai de uma página."""
    modelo_codigo: str = Field(description="SKU/código do fornecedor, ex: LEF-366C")
    descricao: str = Field(description="Descrição textual do produto")
    quantidade_caixa: int = Field(default=1, description="Peças por caixa / múltiplo de compra")
    preco_regular: Optional[float] = Field(default=None, description="Preço padrão em reais")
    preco_oferta: Optional[float] = Field(default=None, description="Preço promocional/destaque, se houver")
    esgotado: bool = Field(default=False, description="True se houver tarja de esgotado")
    confianca: float = Field(default=1.0, ge=0.0, le=1.0, description="Confiança da extração deste item")


class LLMPage(BaseModel):
    """O que o modelo retorna por página."""
    itens: List[LLMItem] = Field(default_factory=list)


class ItemExtraido(LLMItem):
    """Item enriquecido pelo pipeline (proveniência)."""
    pagina_origem: int
    metodo_extracao: str  # "texto" | "visao_online"
