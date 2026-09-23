"""Entrypoint do harness — roda o grafo do agente de onboarding.

Offline (padrão): backends MOCK, sem API. Você VÊ o loop errar (RGB), levar
feedback do gate, corrigir (CMYK) e convergir; e vê o HITL disparar se forçar.

    uv run python run.py                 # cenário que converge sozinho
    uv run python run.py --forcar-hitl   # trava o cérebro -> cai no HITL

REAL (quando engine.det_parse_cfg e a visão estiverem prontos):
    trocar CerebroMock/MotorMock por CerebroOpenAI/MotorDet aqui embaixo.
"""
from __future__ import annotations
import argparse

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.types import Command

from graph import construir_grafo, MAX_TENTATIVAS
from tools import CerebroMock, MotorMock, Percepcao
from config import ExtractionConfig, RegraOferta


class CerebroTravado(CerebroMock):
    """Nunca aprende com o feedback -> força o caminho do HITL (pra demonstrar
    o limite: agente não empurra config ruim, devolve pro humano)."""
    def propor_config(self, p: Percepcao, feedback):
        return ExtractionConfig(
            fornecedor="KNUP", sku_regex=r"^[A-Z]{2,4}[-/]?\w*\d\w*$",
            qty_regex=r"(\d+)\s*pçs/cx",
            regra_oferta=RegraOferta(tipo="cor", espaco_cor="RGB"),  # sempre errado
            n_colunas_hint=p.n_colunas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forcar-hitl", action="store_true")
    a = ap.parse_args()

    cerebro = CerebroTravado() if a.forcar_hitl else CerebroMock()
    # serde: registra nossos tipos p/ o checkpointer não reclamar ao retomar o HITL
    serde = JsonPlusSerializer(allowed_msgpack_modules=[
        ("tools", "Percepcao"), ("tools", "Avaliacao"), ("config", "ExtractionConfig"),
        ("config", "RegraOferta")])
    grafo = construir_grafo(cerebro, MotorMock()).compile(
        checkpointer=MemorySaver(serde=serde))
    cfg_run = {"configurable": {"thread_id": "demo-1"}}
    entrada = {"pdf": "catalogo/KNUP-exemplo.pdf", "pagina": 3}

    print(f"\n=== HARNESS: onboarding de fornecedor (max {MAX_TENTATIVAS} tentativas) ===")
    estado = grafo.invoke(entrada, cfg_run)

    # HITL: se o grafo interrompeu, simula o humano confirmando uma config boa.
    if "__interrupt__" in estado:
        it = estado["__interrupt__"][0]
        print("\n--- INTERRUPT (HITL) ---")
        print("  motivo:", it.value["motivo"])
        print("  agente parou em:", it.value["avaliacao"])
        corrigida = dict(it.value["config_atual"])
        corrigida["regra_oferta"]["espaco_cor"] = "CMYK"  # o humano corrige
        print("  humano -> corrige espaco_cor p/ CMYK e confirma")
        estado = grafo.invoke(Command(resume={"config": corrigida}), cfg_run)

    print("\n--- LOG DO GRAFO ---")
    for linha in estado["log"]:
        print(" ", linha)
    cfg = estado["config"]
    print("\n--- RESULTADO ---")
    print(f"  status ...... {estado['status']}")
    print(f"  fornecedor .. {cfg.fornecedor}")
    print(f"  oferta ...... {cfg.regra_oferta.tipo}/{cfg.regra_oferta.espaco_cor}")
    print(f"  origem ...... {cfg.origem}")
    print(f"  convergiu em  {cfg.tentativas_ate_gate} tentativa(s)")
    av = estado["avaliacao"]
    print(f"  gate ........ recall={av.recall:.0%} prec={av.precisao:.0%} "
          f"silenc={av.erros_silenciosos}")


if __name__ == "__main__":
    main()
