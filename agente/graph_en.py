"""LangGraph graph for the Onboarding Agent (English version for IBM Demo).

Evaluator-optimizer loop:
  perceive -> bootstrap -> propose -> run -> evaluate -> GATE
  GATE: passed      -> register -> END
        failed < N  -> propose (with feedback)   [self-correction]
        failed == N -> hitl (interrupt) -> register -> END

The graph is backend-agnostic: it accepts any Cerebro and Motor implementation.
HITL uses real interrupt() (requires a checkpointer on compile).
"""
from __future__ import annotations
from typing import Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from config import ExtractionConfig
from tools import Cerebro, Motor, Percepcao, Avaliacao, avaliar

MAX_TENTATIVAS = 3


class Estado(TypedDict, total=False):
    pdf: str
    pagina: int
    percepcao: Percepcao
    gabarito: list[dict]
    config: ExtractionConfig
    itens: list[dict]
    avaliacao: Avaliacao
    feedback: Optional[str]
    tentativas: int
    status: str                # "rodando" | "aprovado" | "aprovado_hitl"
    log: list[str]


def construir_grafo_en(cerebro: Cerebro, motor: Motor, input_schema=None):
    """Returns the compiled graph with English node names.
    input_schema (optional): Pydantic/TypedDict specifying input schema for Studio.
    """

    def _log(st: Estado, msg: str) -> list[str]:
        return (st.get("log") or []) + [msg]

    def n_perceive(st: Estado) -> Estado:
        p = cerebro.perceber(st["pdf"], st["pagina"])
        return {"percepcao": p, "tentativas": 0, "feedback": None,
                "status": "rodando",
                "log": _log(st, f"[perceive] {p.espaco_cor}, qty='{p.rotulo_qtd}', "
                                f"{p.n_colunas} col — {p.nota}")}

    def n_bootstrap(st: Estado) -> Estado:
        g = cerebro.bootstrap_gabarito(st["pdf"], st["pagina"])
        return {"gabarito": g,
                "log": _log(st, f"[bootstrap] baseline with {len(g)} items (vision)")}

    def n_propose(st: Estado) -> Estado:
        cfg = cerebro.propor_config(st["percepcao"], st.get("feedback"))
        t = st.get("tentativas", 0) + 1
        cfg.tentativas_ate_gate = t
        return {"config": cfg, "tentativas": t,
                "log": _log(st, f"[propose #{t}] offer={cfg.regra_oferta.tipo}/"
                                f"{cfg.regra_oferta.espaco_cor}, qty={cfg.qty_regex!r}")}

    def n_run(st: Estado) -> Estado:
        itens = motor.rodar_extracao(st["pdf"], st["pagina"], st["config"])
        return {"itens": itens,
                "log": _log(st, f"[run] deterministic engine -> {len(itens)} items")}

    def n_evaluate(st: Estado) -> Estado:
        av = avaliar(st["itens"], st["gabarito"])
        fb = None
        if not av.passou:
            if av.precisao < 0.98:
                fb = ("low precision on price fields: offer color rule "
                      "may be in the wrong color space — use perceived color space")
            else:
                fb = "low recall: review sku_regex / n_colunas"
        return {"avaliacao": av, "feedback": fb,
                "log": _log(st, f"[evaluate] recall={av.recall:.0%} prec={av.precisao:.0%} "
                                f"silent_err={av.erros_silenciosos} -> "
                                f"{'PASSED' if av.passou else 'FAILED'}")}

    def gate(st: Estado) -> str:
        if st["avaliacao"].passou:
            return "register"
        if st.get("tentativas", 0) >= MAX_TENTATIVAS:
            return "hitl"
        return "propose"

    def n_hitl(st: Estado) -> Estado:
        decisao = interrupt({
            "reason": "agent did not converge at gate",
            "current_config": st["config"].model_dump(),
            "evaluation": st["avaliacao"].model_dump(),
            "action": "confirm current config or provide corrected one (dict)",
        })
        cfg = st["config"]
        if isinstance(decisao, dict) and decisao.get("config"):
            cfg = ExtractionConfig(**decisao["config"])
        cfg.origem = "confirmada_hitl"
        return {"config": cfg, "status": "aprovado_hitl",
                "log": _log(st, "[hitl] human operator confirmed/adjusted config")}

    def n_register(st: Estado) -> Estado:
        cfg = st["config"]
        via_hitl = st.get("status") == "aprovado_hitl"
        if not via_hitl:
            cfg.origem = "proposta_agente"
        return {"config": cfg,
                "status": "aprovado_hitl" if via_hitl else "aprovado",
                "log": _log(st, f"[register] config for '{cfg.fornecedor}' registered "
                                f"({cfg.origem}); future extractions run for free")}

    g = StateGraph(Estado, input_schema=input_schema) if input_schema else StateGraph(Estado)
    for name, fn in [("perceive", n_perceive), ("bootstrap", n_bootstrap),
                     ("propose", n_propose), ("run", n_run), ("evaluate", n_evaluate),
                     ("hitl", n_hitl), ("register", n_register)]:
        g.add_node(name, fn)

    g.add_edge(START, "perceive")
    g.add_edge("perceive", "bootstrap")
    g.add_edge("bootstrap", "propose")
    g.add_edge("propose", "run")
    g.add_edge("run", "evaluate")
    g.add_conditional_edges("evaluate", gate,
                            {"register": "register", "propose": "propose", "hitl": "hitl"})
    g.add_edge("hitl", "register")
    g.add_edge("register", END)
    return g

# Alias in English
build_graph_en = construir_grafo_en
