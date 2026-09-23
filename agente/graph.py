"""O grafo LangGraph do Agente de Onboarding (Fase 4, passo 3).

Loop evaluator-optimizer:
  perceber -> bootstrap -> propor -> rodar -> avaliar -> GATE
  GATE: passou      -> registrar -> END
        falhou<N    -> propor (com feedback)   [auto-correção]
        falhou==N   -> hitl (interrupt) -> registrar -> END

O grafo NÃO conhece os backends: recebe um Cerebro e um Motor. Trocar mock->real
não toca aqui. HITL usa interrupt() real (precisa de checkpointer no compile).
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


def construir_grafo(cerebro: Cerebro, motor: Motor, input_schema=None):
    """Devolve o grafo compilado. Passe backends mock ou reais.
    input_schema (opcional): Pydantic/TypedDict com os campos de ENTRADA — o Studio
    renderiza um campo por chave (ex.: pdf + pagina) em vez de um blob só."""

    def _log(st: Estado, msg: str) -> list[str]:
        return (st.get("log") or []) + [msg]

    def n_perceber(st: Estado) -> Estado:
        p = cerebro.perceber(st["pdf"], st["pagina"])
        return {"percepcao": p, "tentativas": 0, "feedback": None,
                "status": "rodando",
                "log": _log(st, f"[perceber] {p.espaco_cor}, qtd='{p.rotulo_qtd}', "
                                f"{p.n_colunas} col — {p.nota}")}

    def n_bootstrap(st: Estado) -> Estado:
        g = cerebro.bootstrap_gabarito(st["pdf"], st["pagina"])
        return {"gabarito": g,
                "log": _log(st, f"[bootstrap] régua com {len(g)} itens (visão)")}

    def n_propor(st: Estado) -> Estado:
        cfg = cerebro.propor_config(st["percepcao"], st.get("feedback"))
        t = st.get("tentativas", 0) + 1
        cfg.tentativas_ate_gate = t
        return {"config": cfg, "tentativas": t,
                "log": _log(st, f"[propor #{t}] oferta={cfg.regra_oferta.tipo}/"
                                f"{cfg.regra_oferta.espaco_cor}, qty={cfg.qty_regex!r}")}

    def n_rodar(st: Estado) -> Estado:
        itens = motor.rodar_extracao(st["pdf"], st["pagina"], st["config"])
        return {"itens": itens,
                "log": _log(st, f"[rodar] motor determinístico -> {len(itens)} itens")}

    def n_avaliar(st: Estado) -> Estado:
        av = avaliar(st["itens"], st["gabarito"])
        fb = None
        if not av.passou:
            # feedback acionável pro próximo propor (é isto que fecha o loop)
            if av.precisao < 0.98:
                fb = ("precisao baixa em campos de preco: a regra de cor da oferta "
                      "pode estar no espaco de cor errado — usar o espaco percebido")
            else:
                fb = "recall baixo: revisar sku_regex / n_colunas"
        return {"avaliacao": av, "feedback": fb,
                "log": _log(st, f"[avaliar] recall={av.recall:.0%} prec={av.precisao:.0%} "
                                f"silenc={av.erros_silenciosos} -> "
                                f"{'PASSOU' if av.passou else 'FALHOU'}")}

    def gate(st: Estado) -> str:
        if st["avaliacao"].passou:
            return "registrar"
        if st.get("tentativas", 0) >= MAX_TENTATIVAS:
            return "hitl"
        return "propor"

    def n_hitl(st: Estado) -> Estado:
        # Ponto de agência humana: para e devolve o controle. O humano confirma
        # a config atual ou manda uma corrigida. Retomado com Command(resume=...).
        decisao = interrupt({
            "motivo": "agente nao convergiu no gate",
            "config_atual": st["config"].model_dump(),
            "avaliacao": st["avaliacao"].model_dump(),
            "acao": "confirme a config ou envie uma corrigida (dict)",
        })
        cfg = st["config"]
        if isinstance(decisao, dict) and decisao.get("config"):
            cfg = ExtractionConfig(**decisao["config"])
        cfg.origem = "confirmada_hitl"
        return {"config": cfg, "status": "aprovado_hitl",
                "log": _log(st, "[hitl] humano confirmou/ajustou a config")}

    def n_registrar(st: Estado) -> Estado:
        cfg = st["config"]
        via_hitl = st.get("status") == "aprovado_hitl"
        if not via_hitl:
            cfg.origem = "proposta_agente"
        # No real: persistir cfg em registro/fornecedor (Postgres/JSON).
        return {"config": cfg,
                "status": "aprovado_hitl" if via_hitl else "aprovado",
                "log": _log(st, f"[registrar] config de '{cfg.fornecedor}' registrada "
                                f"({cfg.origem}); extração futura roda de graça")}

    g = StateGraph(Estado, input_schema=input_schema) if input_schema else StateGraph(Estado)
    for name, fn in [("perceber", n_perceber), ("bootstrap", n_bootstrap),
                     ("propor", n_propor), ("rodar", n_rodar), ("avaliar", n_avaliar),
                     ("hitl", n_hitl), ("registrar", n_registrar)]:
        g.add_node(name, fn)

    g.add_edge(START, "perceber")
    g.add_edge("perceber", "bootstrap")
    g.add_edge("bootstrap", "propor")
    g.add_edge("propor", "rodar")
    g.add_edge("rodar", "avaliar")
    g.add_conditional_edges("avaliar", gate,
                            {"registrar": "registrar", "propor": "propor", "hitl": "hitl"})
    g.add_edge("hitl", "registrar")
    g.add_edge("registrar", END)
    return g
