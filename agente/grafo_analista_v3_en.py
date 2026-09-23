"""Agent 2 — Analyst (v3 English Version for IBM Demo).
Full English variant with:
  1. English node names in LangGraph Studio:
     greeting -> input_guard -> profile_capture -> plan -> execute -> ground -> narrate
     (with branch routes: clarify, invite, scope_refusal)
  2. Native English messages, guidance, and clarification prompts.
  3. Robust markup parsing (e.g. '1.5', '2', '1.6x', '50%').
  4. Contextual memory for pending questions: answering follow-ups without double entry.
  5. Clean markdown step-by-step mathematical memory with clear formulas.
"""
from __future__ import annotations
import os, re, sys, json, math
from typing import Optional, TypedDict, Annotated, Literal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field

import consulta as Q
import tools_analista as T

_ITENS_CACHE: Optional[list[dict]] = None

def _itens(path: Optional[str] = None) -> list[dict]:
    global _ITENS_CACHE
    if _ITENS_CACHE is None:
        path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados_norm.json")
        _ITENS_CACHE = json.load(open(path, encoding="utf-8"))
    return _ITENS_CACHE

def _df(perfil: Q.PerfilSessao):
    return Q.preparar_catalogo(_itens(), perfil)


# ── STATE ───────────────────────────────────────────────────────────
class StateV3EN(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    perfil: dict          # {"margem": float|None}
    saudou: bool          # greeting executed?
    acao: str
    query: dict
    resultado: dict
    status: str


# ── PLAN ────────────────────────────────────────────────────────────
class PlanV3EN(BaseModel):
    acao: Literal["consulta", "otimizar", "esclarecer", "fora_escopo"]
    query: Optional[Q.Query] = None
    orcamento: Optional[float] = None
    pergunta_esclarece: Optional[str] = None


# ── MOCK PLANNER (Offline english + portuguese keyword router) ──────
class MockPlannerEN:
    def planejar(self, question: str, perfil: Q.PerfilSessao, history: str = "") -> PlanV3EN:
        p = question.lower()

        # budget / optimization
        budget_terms = ["budget", "buy", "spend", "invest", "orçamento", "orcamento", "comprar", "r$", "thousand", "mil"]
        if any(k in p for k in budget_terms):
            m = re.search(r"(\d[\d.\s]*)", p.replace(",", "."))
            orc = float(re.sub(r"[.\s]", "", m.group(1))) if m else 5000.0
            return PlanV3EN(acao="otimizar", orcamento=orc)

        # profit / markup
        if any(k in p for k in ["profit", "margin", "markup", "retail", "lucro", "vender"]):
            return PlanV3EN(acao="consulta", query=Q.Query(
                selecionar=[Q.Campo.descricao, Q.Campo.preco_efetivo,
                            Q.Campo.preco_venda_est, Q.Campo.lucro_unit],
                ordenar_por=Q.Ordenacao(campo=Q.Campo.lucro_unit, direcao="desc"),
                limite=10))

        # discount / deals / offers
        if any(k in p for k in ["discount", "offer", "deal", "promo", "cheap", "oferta", "desconto", "barat"]):
            return PlanV3EN(acao="consulta", query=Q.Query(
                filtros=[Q.Filtro(campo=Q.Campo.em_oferta, op=Q.Operador.eq, valor=True)],
                ordenar_por=Q.Ordenacao(campo=Q.Campo.desconto_pct, direcao="desc"),
                selecionar=[Q.Campo.descricao, Q.Campo.preco_regular,
                            Q.Campo.preco_oferta, Q.Campo.desconto_pct],
                limite=10))

        # price trends / variation vs 2023
        if any(k in p for k in ["trend", "increased", "dropped", "variation", "2023", "subiu", "caiu", "variação"]):
            return PlanV3EN(acao="consulta", query=Q.Query(
                ordenar_por=Q.Ordenacao(campo=Q.Campo.variacao_pct, direcao="desc"),
                selecionar=[Q.Campo.descricao, Q.Campo.preco_base,
                            Q.Campo.preco_regular, Q.Campo.variacao_pct],
                limite=10))

        # ambiguous / clarifying request
        if any(k in p for k in ["best", "worst", "worth", "good", "melhor", "pior"]):
            return PlanV3EN(acao="esclarecer", pergunta_esclarece=(
                "'Best' by which criterion: highest percentage discount, lowest unit cost, or highest profit margin with your markup?"))

        return PlanV3EN(acao="fora_escopo")


# ── LLM PLANNER ─────────────────────────────────────────────────────
class LLMFilter(BaseModel):
    campo: Q.Campo
    op: Q.Operador
    valor: str = Field(description="Value as string. Number: '50' or '0.2' (20%=0.2). Boolean: 'true'/'false'.")

class LLMPlan(BaseModel):
    acao: Literal["consulta", "otimizar", "esclarecer", "fora_escopo"]
    selecionar: list[Q.Campo] = Field(default_factory=list)
    filtros: list[LLMFilter] = Field(default_factory=list)
    ordenar_campo: Optional[Q.Campo] = None
    ordenar_direcao: Optional[Literal["asc", "desc"]] = None
    agrupar_por: Optional[Q.Campo] = None
    agregacao_func: Optional[Q.FuncAgg] = None
    agregacao_campo: Optional[Q.Campo] = None
    limite: Optional[int] = None
    limite_por_grupo: Optional[int] = None
    orcamento: Optional[float] = None
    pergunta_esclarece: Optional[str] = None

_SYS_PLAN_EN = (
    "You route a retail merchant's question regarding a wholesale supplier catalog. "
    "Decide the ACTION and, when consultation, COMPOSE a structured Query (do not write code).\n"
    "Available catalog fields:\n"
    "- base: modelo_codigo, descricao, quantidade_caixa, preco_regular, preco_oferta, esgotado, preco_base.\n"
    "- derived: preco_efetivo, em_oferta, desconto_pct, desconto_abs, variacao_pct, custo_caixa.\n"
    "- profit (ONLY with merchant markup): preco_venda_est, lucro_unit.\n"
    "The catalog does NOT measure subjective quality or brand prestige. If asked for 'best product' "
    "without a measurable criterion, use 'esclarecer' to clarify between price, discount, or profit margin.\n"
    "ACTIONS:\n"
    "- consulta: filter/rank/aggregate structured query.\n"
    "- otimizar: ONLY when a monetary budget cap is provided (e.g. 'R$ 5,000', 'budget of 5000').\n"
    "- esclarecer: ambiguous question.\n"
    "- fora_escopo: unrelated topic."
)


def _coerce_scalar(campo: Q.Campo, v: str):
    v = v.strip()
    if campo in (Q.Campo.esgotado, Q.Campo.em_oferta):
        return v.lower() in ("true", "1", "sim", "verdadeiro")
    if campo in Q.CAMPOS_NUMERICOS:
        return float(v)
    return v

def _llm_filter_to_internal(f: LLMFilter) -> Q.Filtro:
    if f.op == Q.Operador.em:
        valor = [_coerce_scalar(f.campo, x) for x in f.valor.split(",")]
    else:
        valor = _coerce_scalar(f.campo, f.valor)
    return Q.Filtro(campo=f.campo, op=f.op, valor=valor)

def _llm_plan_to_v3(pl: LLMPlan) -> PlanV3EN:
    if pl.acao == "otimizar":
        return PlanV3EN(acao="otimizar", orcamento=pl.orcamento or 5000.0)
    if pl.acao == "esclarecer":
        return PlanV3EN(acao="esclarecer",
                        pergunta_esclarece=pl.pergunta_esclarece or "Could you clarify your question?")
    if pl.acao == "fora_escopo":
        return PlanV3EN(acao="fora_escopo")
    ordenar = (Q.Ordenacao(campo=pl.ordenar_campo, direcao=pl.ordenar_direcao or "desc")
               if pl.ordenar_campo else None)
    agreg = (Q.Agregacao(func=pl.agregacao_func, campo=pl.agregacao_campo)
             if (pl.agregacao_func and pl.agregacao_campo) else None)
    query = Q.Query(
        selecionar=pl.selecionar,
        filtros=[_llm_filter_to_internal(f) for f in pl.filtros],
        ordenar_por=ordenar, agrupar_por=pl.agrupar_por, agregacao=agreg,
        limite=pl.limite, limite_por_grupo=pl.limite_por_grupo)
    return PlanV3EN(acao="consulta", query=query)


class LLMPlannerEN:
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self._oa = None

    def _client(self):
        if self._oa is None:
            from openai import OpenAI
            base = OpenAI()
            if os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true":
                try:
                    from langsmith.wrappers import wrap_openai
                    base = wrap_openai(base)
                except Exception:
                    pass
            self._oa = base
        return self._oa

    def planejar(self, question: str, perfil: Q.PerfilSessao, history: str = "") -> PlanV3EN:
        try:
            ctx = f"(merchant markup: {'not provided' if perfil.margem is None else perfil.margem})"
            user = (f"{ctx}\nConversation history:\n{history or '(empty)'}\n\n"
                    f"Merchant question/response: {question}")
            comp = self._client().beta.chat.completions.parse(
                model=self.model,
                messages=[{"role": "system", "content": _SYS_PLAN_EN},
                          {"role": "user", "content": user}],
                response_format=LLMPlan)
            return _llm_plan_to_v3(comp.choices[0].message.parsed)
        except Exception:
            return MockPlannerEN().planejar(question, perfil, history)


# ── PARSERS ─────────────────────────────────────────────────────────
def extract_markup(text: str) -> Optional[float]:
    t = text.strip().lower().replace(",", ".")
    m_puro = re.match(r"^(\d+(?:\.\d+)?)$", t)
    if m_puro:
        val = float(m_puro.group(1))
        return val if val >= 1.0 else round(1.0 + val, 4)

    m = re.search(r"(\d+(?:\.\d+)?)\s*x", t)
    if m:
        return float(m.group(1))

    m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return round(1.0 + float(m.group(1)) / 100, 4)

    if any(k in t for k in ["markup", "margin", "margem"]):
        m = re.search(r"(\d+(?:\.\d+)?)", t)
        if m:
            val = float(m.group(1))
            return val if val >= 1.0 else round(1.0 + val, 4)

    return None

def has_question(text: str, markup_val: Optional[float] = None) -> bool:
    if "?" in text:
        return True
    resto = re.sub(r"\d+(?:[.,]\d+)?\s*[x%]?", "", text.lower())
    words = [w for w in re.findall(r"[a-zà-ú]+", resto)
             if w not in {"markup", "margin", "margem", "my", "is", "the", "a", "of", "with", "de", "e"}]
    return len(words) >= 2

def _all_human_messages(st: StateV3EN) -> list[str]:
    res = []
    for m in (st.get("messages") or []):
        if isinstance(m, HumanMessage):
            c = m.content
            txt = c if isinstance(c, str) else " ".join(
                (b.get("text", "") if isinstance(b, dict) else str(b)) for b in c)
            res.append(txt.strip())
    return res

def _active_question(st: StateV3EN) -> tuple[str, bool]:
    humans = _all_human_messages(st)
    if not humans:
        return ("", False)
    latest = humans[-1]
    m = extract_markup(latest)
    if has_question(latest, m):
        return (latest, False)

    for h in reversed(humans[:-1]):
        if has_question(h, extract_markup(h)):
            return (h, True)
    return (latest, False)


# ── NARRATOR WITH STEP-BY-STEP MATH ─────────────────────────────────
_COLNAME_EN = {
    "modelo_codigo": "SKU", "descricao": "Product", "quantidade_caixa": "Units/Box",
    "preco_regular": "Regular", "preco_oferta": "Offer", "preco_efetivo": "Price",
    "preco_base": "2023 Price", "desconto_pct": "Discount %", "desconto_abs": "Savings R$",
    "variacao_pct": "Trend %", "custo_caixa": "Box Cost", "em_oferta": "On Sale",
    "esgotado": "Out of Stock", "preco_venda_est": "Est. Retail", "lucro_unit": "Profit/Unit"
}
_PCT_COLS = {"desconto_pct", "variacao_pct"}
_BRL_COLS = {"preco_regular", "preco_oferta", "preco_efetivo", "preco_base", "desconto_abs",
             "custo_caixa", "preco_venda_est", "lucro_unit"}

def _brl(v: float) -> str:
    return f"R${v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")

def _fmt(col: str, v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if col in _PCT_COLS:
        return f"{v * 100:.1f}%"
    if col in _BRL_COLS:
        return _brl(v)
    if col in ("descricao", "Product"):
        return str(v)[:32]
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)

def _table(rows: list[dict], cols: list[str] | None = None) -> str:
    cols = cols or list(rows[0].keys())
    head = "| " + " | ".join(_COLNAME_EN.get(c, c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join("| " + " | ".join(_fmt(c, r.get(c)) for c in cols) + " |" for r in rows)
    return f"{head}\n{sep}\n{body}"

def narrate_result_en(plan: PlanV3EN, result: dict, perfil: Q.PerfilSessao) -> str:
    if plan.acao == "otimizar":
        r = result
        retrieved = r.get("_retrieved", False)
        m = perfil.margem
        prefix = ""
        if retrieved and m is not None:
            prefix = f"✅ **Markup recorded: {m}x**\n\nRetrieved your previous question from conversation history. Here is your purchasing plan with the math fully shown:\n\n"

        orc = _brl(r["orcamento"])
        gasto = _brl(r["gasto"])
        sobra = _brl(r["sobra"])
        econ = _brl(r["economia_total"])
        caixas = r["n_caixas"]

        summary = (
            f"| 💰 Budget | 📦 Boxes | 💵 Total Spent | 💸 Total Savings | 🪙 Cash Preserved |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"| {orc} | {caixas} boxes | {gasto} | {econ} | **{sobra}** |"
        )

        rows = []
        for c in r["compra"][:8]:
            p_unit = round(c["custo_caixa"] / max(c["qtd_caixa"], 1), 2)
            rows.append({
                "SKU": c["sku"],
                "Product": c.get("descricao") or "—",
                "Units/Box": c["qtd_caixa"],
                "Unit Price": _brl(p_unit),
                "Box Cost": _brl(c["custo_caixa"]),
                "Savings": _brl(c["economia"]),
            })

        cols = ["SKU", "Product", "Units/Box", "Unit Price", "Box Cost", "Savings"]
        prod_table = _table(rows, cols=cols)

        math_breakdown = (
            f"### 🧮 Step-by-Step Mathematical Memory:\n"
            f"1. **Wholesale Box Minimums:** Purchases respect the supplier's minimum pack per box (`Units/Box`).\n"
            f"2. **Box Cost:** `Offer Price × Units per Box`\n"
            f"3. **Savings Generated:** `(Regular Price − Offer Price) × Units per Box`\n"
            f"4. **Greedy Optimization:** The algorithm prioritized items yielding the highest absolute savings per real spent up to your {orc} ceiling.\n"
            f"5. **Cash Closure:** {orc} (Budget) − {gasto} (Total Spent) = **{sobra}** preserved in your store cash."
        )

        return (f"{prefix}{summary}\n\n"
                f"{math_breakdown}\n\n"
                f"**Recommended products for purchase:**\n\n{prod_table}\n\n"
                f"_100% deterministic calculation; the final purchase decision is yours._")

    if result.get("escalar") is not None:
        return f"**{_brl(result['escalar'])}** — total across {result['n']} items.\n\n_You decide._"

    lines = result.get("linhas", [])
    if not lines:
        return "No items matched your criteria in the current catalog."
    cols = list(lines[0].keys())
    return (f"**{result['n']} items identified** (math shown):\n\n{_table(lines, cols)}\n\n_You decide._")


# ── LANGGRAPH ANALYST V3 (ENGLISH NODES) ────────────────────────────
def _latest_human(st: StateV3EN) -> str:
    for m in reversed(st.get("messages") or []):
        if isinstance(m, HumanMessage):
            c = m.content
            return c if isinstance(c, str) else " ".join(
                (b.get("text", "") if isinstance(b, dict) else str(b)) for b in c)
    return ""

def _msg_str(m) -> str:
    c = m.content
    return c if isinstance(c, str) else " ".join(
        (b.get("text", "") if isinstance(b, dict) else str(b)) for b in c)

def _history_str(st: StateV3EN, n: int = 8) -> str:
    lines = []
    for m in (st.get("messages") or [])[-n:]:
        role = "Merchant" if isinstance(m, HumanMessage) else "Assistant"
        lines.append(f"{role}: {_msg_str(m)}")
    return "\n".join(lines)

def _perfil_session(st: StateV3EN) -> Q.PerfilSessao:
    return Q.PerfilSessao(**(st.get("perfil") or {}))

def _ai(text: str, **extra):
    return {"messages": [AIMessage(content=text)], **extra}


def build_analyst_v3_en(planner):
    def n_greeting(st: StateV3EN) -> StateV3EN:
        df = _df(Q.PerfilSessao())
        n = len(df)
        on_sale = int(df["em_oferta"].sum())
        with_2023 = int(df["preco_base"].notna().sum())
        txt = (f"Catalog loaded: {n} products, {on_sale} currently on sale, and {with_2023} with 2023 baseline prices for trend comparison.\n\n"
               f"To get started, what markup do you usually apply? (e.g. 1.5 or 2 = doubles cost price). You can also skip and ask me directly.")
        return _ai(txt, saudou=True, status="greeting")

    def n_input_guard(st: StateV3EN) -> StateV3EN:
        p = _latest_human(st).strip()
        if not p or len(p) > 500:
            return _ai("Please ask a question regarding catalog prices, discounts, or purchasing budgets.",
                       status="input_rejected")
        return {"status": "input_ok"}

    def n_profile_capture(st: StateV3EN) -> StateV3EN:
        p = _latest_human(st)
        m = extract_markup(p)
        out = {}
        if m is not None:
            out["perfil"] = {**(st.get("perfil") or {}), "margem": m}
        return out or {"status": "no_new_profile"}

    def n_plan(st: StateV3EN) -> StateV3EN:
        q, retrieved = _active_question(st)
        plan = planner.planejar(q, _perfil_session(st), _history_str(st))
        return {"acao": plan.acao,
                "query": plan.query.model_dump(mode="json") if plan.query else None,
                "resultado": {"_orcamento": plan.orcamento,
                              "_esclarece": plan.pergunta_esclarece,
                              "_retrieved": retrieved}}

    def n_execute(st: StateV3EN) -> StateV3EN:
        perfil = _perfil_session(st)
        retrieved = (st.get("resultado") or {}).get("_retrieved", False)
        if st["acao"] == "otimizar":
            orc = (st.get("resultado") or {}).get("_orcamento") or 5000.0
            r = T.otimizar_compra(_itens(), orcamento=orc)
            r["_retrieved"] = retrieved
            return {"resultado": r, "status": "executed"}

        query = Q.Query(**st["query"])
        res = Q.executar(query, _df(perfil), perfil)
        if isinstance(res, Q.Esclarecer):
            return {"status": "missing_margin", "resultado": {"_esclarece": res.pergunta, "_retrieved": retrieved}}
        d = res.model_dump()
        d["_retrieved"] = retrieved
        return {"resultado": d, "status": "executed"}

    def n_ground(st: StateV3EN) -> StateV3EN:
        if st.get("status") == "executed":
            return {"status": "grounded"}
        return _ai("I cannot compute that with current catalog data.", status="grounding_refusal")

    def n_narrate(st: StateV3EN) -> StateV3EN:
        plan = PlanV3EN(acao=st["acao"], query=Q.Query(**st["query"]) if st.get("query") else None)
        return _ai(narrate_result_en(plan, st["resultado"], _perfil_session(st)), status="ok")

    def n_clarify(st: StateV3EN) -> StateV3EN:
        q = (st.get("resultado") or {}).get("_esclarece") or "Could you clarify your question?"
        return _ai(q, status="clarifying")

    def n_invite(st: StateV3EN) -> StateV3EN:
        m = (st.get("perfil") or {}).get("margem")
        m_txt = f"markup {m}x" if m is not None else "—"
        return _ai(f"✅ Markup recorded: {m_txt}\n\n"
                   "Now I can help you with: top deals/discounts, price trends vs 2023, cost per box, and estimated profit. What would you like to see?",
                   status="inviting")

    def n_scope_refusal(st: StateV3EN) -> StateV3EN:
        return _ai("That falls outside what I do. I answer questions about prices, discounts, trends, and wholesale purchasing for your catalog. How can I assist you there?",
                   status="scope_refusal")

    # -- Routers --
    def r_start(st):
        return "input_guard" if st.get("saudou") else "greeting"

    def r_input(st):
        return "profile_capture" if st.get("status") == "input_ok" else END

    def r_after_profile(st):
        q, _ = _active_question(st)
        return "plan" if has_question(q, extract_markup(q)) else "invite"

    def r_plan(st):
        return {"consulta": "execute", "otimizar": "execute",
                "esclarecer": "clarify", "fora_escopo": "scope_refusal"}[st["acao"]]

    def r_exec(st):
        return "clarify" if st.get("status") == "missing_margin" else "ground"

    def r_ground(st):
        return "narrate" if st.get("status") == "grounded" else END

    g = StateGraph(StateV3EN)
    for name, fn in [("greeting", n_greeting), ("input_guard", n_input_guard),
                     ("profile_capture", n_profile_capture), ("plan", n_plan),
                     ("execute", n_execute), ("ground", n_ground),
                     ("narrate", n_narrate), ("clarify", n_clarify),
                     ("invite", n_invite), ("scope_refusal", n_scope_refusal)]:
        g.add_node(name, fn)

    g.add_conditional_edges(START, r_start, {"greeting": "greeting", "input_guard": "input_guard"})
    g.add_edge("greeting", END)
    g.add_conditional_edges("input_guard", r_input, {"profile_capture": "profile_capture", END: END})
    g.add_conditional_edges("profile_capture", r_after_profile, {"plan": "plan", "invite": "invite"})
    g.add_conditional_edges("plan", r_plan,
                            {"execute": "execute", "clarify": "clarify", "scope_refusal": "scope_refusal"})
    g.add_conditional_edges("execute", r_exec, {"ground": "ground", "clarify": "clarify"})
    g.add_conditional_edges("ground", r_ground, {"narrate": "narrate", END: END})
    for term in ("narrate", "clarify", "invite", "scope_refusal"):
        g.add_edge(term, END)
    return g


_usar_llm = os.getenv("USE_LLM", "").lower() == "true"
_planner_en = LLMPlannerEN() if _usar_llm else MockPlannerEN()
graph_analista_v3_en = build_analyst_v3_en(_planner_en).compile()
