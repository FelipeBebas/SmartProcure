"""Exposição do grafo para o LangGraph Studio (langgraph dev).

Roda daqui (spike/, onde vivem .venv e .env com as chaves LangSmith) e importa
o código do agente de ../agente. Backends MOCK: o Studio mostra o grafo, os
states e o HITL sem gastar API. Custo real aparece quando ligarmos a visão.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agente"))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from graph import construir_grafo
from tools import CerebroMock, MotorMock, CerebroLehmox, MotorDet, Percepcao
from config import ExtractionConfig, RegraOferta
from pydantic import BaseModel, Field


class CerebroTravado(CerebroMock):
    """Nunca aprende -> força o caminho do HITL (pra demonstrar o limite)."""
    def propor_config(self, p: Percepcao, feedback):
        return ExtractionConfig(
            fornecedor="KNUP", sku_regex=r"^[A-Z]{2,4}[-/]?\w*\d\w*$",
            qty_regex=r"(\d+)\s*pçs/cx",
            regra_oferta=RegraOferta(tipo="cor", espaco_cor="RGB"),
            n_colunas_hint=p.n_colunas)


# Agente 1 (onboarding). NÃO passar checkpointer: o Studio injeta persistência.
graph = construir_grafo(CerebroMock(), MotorMock()).compile()
graph_hitl = construir_grafo(CerebroTravado(), MotorMock()).compile()

# Agente 1 REAL (caminho rápido "fornecedor conhecido"): CerebroLehmox (config
# registrada, sem LLM) + MotorDet (engine.det_parse_cfg real). Extração de verdade, US$0.
# input_schema -> o Studio mostra DOIS campos separados (pdf, pagina), não um blob.
class EntradaOnboarding(BaseModel):
    pdf: str = Field(description="Caminho do PDF do fornecedor "
                                 "(ex.: ../catalogo/LEHMOX 2026.04-completo.pdf).")
    pagina: int = Field(default=5, description="Página a extrair (1-indexada). LEHMOX: 5 ou 15.")

graph_onboarding_real = construir_grafo(
    CerebroLehmox(), MotorDet(), input_schema=EntradaOnboarding).compile()

# Agente 1 REAL (English variant para a demo da IBM): nós em inglês (perceive, etc.)
from graph_en import construir_grafo_en

class OnboardingInput(BaseModel):
    pdf: str = Field(default="../catalogo/LEHMOX 2026.04-completo.pdf",
                     description="Path to supplier PDF catalog (e.g. ../catalogo/LEHMOX 2026.04-completo.pdf).")
    pagina: int = Field(default=5, description="Page to extract (1-indexed). LEHMOX: 5 or 15.")

graph_onboarding_real_en = construir_grafo_en(
    CerebroLehmox(), MotorDet(), input_schema=OnboardingInput).compile()

# ==============================================================================
# [LEGADO v1 - Comentado por segurança em 19/09/2026]:
# O analista.py (v1) foi substituído pelo novo grafo_analista_v2.py (v2).
# Mantemos as linhas abaixo comentadas em vez de apagadas para preservar o histórico,
# evitando erros de importação quando analista.py for movido para _legado/.
#
# from analista import construir_analista, CerebroMock as CerebroAnalista, CerebroLLM
# _usar_llm = os.getenv("USE_LLM", "").lower() == "true"
# _cerebro_analista = CerebroLLM() if _usar_llm else CerebroAnalista()
# graph_analista = construir_analista(_cerebro_analista).compile()              # modo form (input limpo)
# graph_analista_chat = construir_analista(_cerebro_analista, chat=True).compile()  # modo Chat (messages)
# ==============================================================================

# Agente 2 REAL (v2 - consulta estruturada + esclarecer):
from grafo_analista_v2 import graph_analista_v2

# Agente 2 REAL (v3 - com memoria contextual de abertura e memoria de calculo passo a passo):
from grafo_analista_v3 import graph_analista_v3

# Agente 2 REAL (v3 English - nós em inglês e saída em inglês para a demo da IBM):
from grafo_analista_v3_en import graph_analista_v3_en


