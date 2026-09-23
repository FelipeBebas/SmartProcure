"""Agente 2 — Analista (LangGraph). Implementa o diagrama das 6 cercas.

Fluxo: guarda_entrada -> guarda_escopo -> planejar -> executar -> fundamentar -> responder
Cada recusa (R1/R2/R3) é uma saída para END. Input aberto, ação fechada às 4 tools.

Cérebro (planejador + narrador) é pluggável: CerebroMock roda offline (roteia por
palavra-chave) pra ver o grafo sem gastar API; a costura de LLM real fica marcada.
"""
from __future__ import annotations
import json, os, re
from typing import Optional, TypedDict, Annotated

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage
import tools_analista as T

MAX_ITER = 3
CATEGORIAS_LOJISTA = ["fone", "mouse", "teclado", "carregador", "cabo", "caixa de som", "hub", "adaptador"]


# ---------- ponte de dados (com dedupe — caveat conhecido) ----------
def carregar_dados(path: Optional[str] = None) -> list[dict]:
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados_norm.json")
    itens = json.load(open(path, encoding="utf-8"))
    vistos, limpo = set(), []
    for it in itens:                       # dedupe por SKU (1º vence)
        k = it["modelo_codigo"].upper().replace(" ", "")
        if k not in vistos:
            vistos.add(k); limpo.append(it)
    return limpo


# ---------- estado ----------
class Estado(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # usado no modo Chat
    pergunta: str
    dados: list[dict]
    intent: str
    args: dict
    resultados: list[dict]
    resposta: str
    status: str          # ok_* | recusa_entrada | recusa_escopo | recusa_fundamentacao
    log: list[str]


# ---------- cérebro mock (planejador + narrador) ----------
INTENTS = {
    "otimizar_compra":      ["orçamento", "orcamento", "reais", "r$", "comprar", "compro", "gastar", "investir"],
    "variacao_preco":       ["caiu", "subiu", "variação", "variacao", "comparar", "antes", "mudou de preço"],
    "ranking_oportunidades":["oferta", "ofertas", "desconto", "oportunidade", "promoção", "promocao",
                             "mais barato", "ranquear", "rankear", "ranking", "ordenar", "melhores"],
    "cobertura_categoria":  ["categoria", "informática", "informatica", "meu ramo", "trabalho com"],
}


class CerebroMock:
    def detectar_intent(self, pergunta: str) -> Optional[str]:
        p = pergunta.lower()
        for intent, kws in INTENTS.items():
            if any(k in p for k in kws):
                return intent
        return None

    def extrair_args(self, pergunta: str, intent: str) -> dict:
        if intent == "otimizar_compra":
            m = re.search(r'(\d[\d.\s]{2,})', pergunta.replace(",", "."))
            orc = float(re.sub(r'[.\s]', '', m.group(1))) if m else 5000.0
            return {"orcamento": orc}
        if intent == "ranking_oportunidades":
            m = re.search(r'\b(\d{1,2})\b', pergunta)   # "3 melhores" -> top 3
            return {"top": int(m.group(1)) if m else 5}
        if intent == "cobertura_categoria":
            return {"termos": CATEGORIAS_LOJISTA}
        return {}

    def narrar(self, intent: str, r: dict, pergunta: str = "") -> str:
        if intent == "otimizar_compra":
            destaques = "; ".join(f"{c['sku']} ({c['qtd_caixa']}un, economia R${c['economia']:.0f})"
                                  for c in r["compra"][:3])
            return (f"Com R${r['orcamento']:.0f}, a compra mais lógica gasta R${r['gasto']:.2f} "
                    f"e economiza R${r['economia_total']:.2f} vs. o preço regular, em {r['n_caixas']} caixas. "
                    f"Destaques: {destaques}. (cálculo determinístico; você decide.)")
        if intent == "ranking_oportunidades":
            top = r["top"]
            if not top:
                return "Nenhum item em oferta encontrado."
            linhas = "\n".join(
                f"{i}. {t['sku']}: R${t['regular']:.2f}→R${t['oferta']:.2f} (-{t['desconto_pct']:.0f}%) — {t['descricao'][:30]}"
                for i, t in enumerate(top, 1))
            return f"Top {len(top)} ofertas (de {r['n']} em oferta):\n{linhas}"
        if intent == "variacao_preco":
            itens = r["itens"][:5]
            if not itens:
                return "Nenhum item com histórico para comparar."
            linhas = "\n".join(
                f"{i}. {q['sku']}: R${q['de']:.2f}→R${q['para']:.2f} ({q['variacao_pct']:+.0f}%)"
                for i, q in enumerate(itens, 1))
            return f"{r['n']} itens comparáveis. Maiores variações:\n{linhas}"
        if intent == "cobertura_categoria":
            return f"{r['n_no_escopo']} de {r['n_total']} itens caem nas suas categorias."
        return "Sem resposta."


# ---------- cérebro REAL (LLM planejador; narrador herdado = determinístico) ----------
from pydantic import BaseModel, Field
from typing import Literal

class Plano(BaseModel):
    """O que o LLM decide a partir da pergunta (roteamento + args)."""
    intent: Literal["otimizar_compra", "variacao_preco", "ranking_oportunidades",
                    "cobertura_categoria", "fora_do_escopo"] = Field(
        description="Qual ferramenta responde a pergunta, ou fora_do_escopo se nenhuma serve.")
    orcamento: Optional[float] = Field(
        default=None, description="Valor em reais quando a pergunta cita orçamento. "
                                  "Interprete linguagem natural: '3 mil'=3000, 'uns 3k'=3000.")
    n: Optional[int] = Field(
        default=None, description="Quantos itens listar, quando a pergunta pede uma quantidade "
                                  "(ex.: 'as 3 melhores'=3, 'top 5'=5).")

_SYS_PLAN = (
    "Você roteia a pergunta de um lojista para UMA de 4 ferramentas sobre um catálogo. "
    "Escolha pela INTENÇÃO, mesmo com sinônimos ou linguagem informal:\n"
    "- otimizar_compra: melhor compra dado um ORÇAMENTO em R$. "
    "Ex.: 'com 5 mil o que compro', 'onde investir meu dinheiro'.\n"
    "- ranking_oportunidades: listar/ordenar as maiores OFERTAS ou descontos. "
    "Ex.: 'ranquear ofertas', 'rankear as promoções', 'quais os maiores descontos', "
    "'as melhores oportunidades', 'ordena as ofertas'.\n"
    "- variacao_preco: como o preço MUDOU vs. histórico. Ex.: 'o que caiu de preço', 'subiu?'.\n"
    "- cobertura_categoria: quais itens caem nas CATEGORIAS do lojista.\n"
    "Use fora_do_escopo SÓ se a pergunta realmente não for sobre preço/compra/ofertas/"
    "categorias do catálogo. Havendo orçamento, extraia o número (entenda '3 mil'=3000, 'uns 3k'=3000)."
)


class CerebroLLM(CerebroMock):
    """Planejador via gpt-4o-mini (herda narrar determinístico do CerebroMock —
    números da resposta vêm SÓ da tool, nunca do LLM)."""
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self._oa = None
        self._cache: dict = {}

    def _client(self):
        if self._oa is None:
            from openai import OpenAI
            base = OpenAI()
            if os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true":
                try:
                    from langsmith.wrappers import wrap_openai
                    base = wrap_openai(base)   # <- faz token/custo aparecer no LangSmith
                except Exception:
                    pass
            self._oa = base
        return self._oa

    def _plan(self, pergunta: str) -> Plano:
        if pergunta not in self._cache:
            comp = self._client().beta.chat.completions.parse(
                model=self.model,
                messages=[{"role": "system", "content": _SYS_PLAN},
                          {"role": "user", "content": pergunta}],
                response_format=Plano)
            self._cache[pergunta] = comp.choices[0].message.parsed
        return self._cache[pergunta]

    def detectar_intent(self, pergunta: str) -> Optional[str]:
        p = self._plan(pergunta)
        return None if p.intent == "fora_do_escopo" else p.intent

    def extrair_args(self, pergunta: str, intent: str) -> dict:
        p = self._plan(pergunta)
        if intent == "otimizar_compra":
            return {"orcamento": p.orcamento or 5000.0}
        if intent == "ranking_oportunidades":
            return {"top": p.n or 5}
        if intent == "cobertura_categoria":
            return {"termos": CATEGORIAS_LOJISTA}
        return {}

    def narrar(self, intent: str, r: dict, pergunta: str = "") -> str:
        """Narrador LLM: redige em linguagem natural usando SÓ os números da tool."""
        import json
        fatos = _fatos(intent, r)
        sys = ("Você é o assistente de compras de um lojista de informática. Responda em português, "
               "natural e conciso, à PERGUNTA do lojista. Use SOMENTE os números e itens do RESULTADO "
               "(JSON) — nunca invente, altere ou arredonde valores, nem cite item fora do resultado. "
               "Respeite o formato que o lojista pediu (ex.: 'liste 3', 'só os nomes'). "
               "Termine lembrando, em poucas palavras, que a decisão é dele.")
        comp = self._client().chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": f"PERGUNTA: {pergunta}\nferramenta: {intent}\n"
                                                   f"RESULTADO: {json.dumps(fatos, ensure_ascii=False)}"}])
        return comp.choices[0].message.content.strip()


def _fatos(intent: str, r: dict) -> dict:
    """Recorte compacto do resultado p/ alimentar o narrador (controla custo e foco)."""
    if intent == "otimizar_compra":
        return {k: r[k] for k in ("orcamento", "gasto", "economia_total", "n_caixas")} | {"itens": r["compra"][:8]}
    if intent == "ranking_oportunidades":
        return {"n_em_oferta": r["n"], "top": r["top"]}
    if intent == "variacao_preco":
        return {"n": r["n"], "itens": r["itens"][:8]}
    if intent == "cobertura_categoria":
        return {"n_no_escopo": r["n_no_escopo"], "n_total": r["n_total"], "amostra": r["itens"][:8]}
    return r


# ---------- schema de ENTRADA (Studio pede só isto) ----------
class EntradaAnalista(BaseModel):
    pergunta: str = Field(description="Pergunta do lojista sobre preços/compra "
                                      "(ex.: 'com R$5.000, o que compro?').")


# ---------- helpers ----------
def _texto(content) -> str:
    """Normaliza content de mensagem para string. O chat do Studio às vezes manda
    lista de blocos [{'type':'text','text':...}] em vez de string pura."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        partes = []
        for c in content:
            if isinstance(c, dict):
                partes.append(c.get("text") or c.get("content") or "")
            elif isinstance(c, str):
                partes.append(c)
        return " ".join(partes)
    return str(content or "")


# ---------- grafo ----------
def _resposta(st, texto, **extra):
    """Saída padrão: preenche 'resposta' (modo form) E anexa AIMessage (modo chat)."""
    return {"resposta": texto, "messages": [AIMessage(content=texto)], **extra}


def construir_analista(cerebro: CerebroMock, chat: bool = False):
    def _log(st, m): return (st.get("log") or []) + [m]

    def n_guarda_entrada(st: Estado) -> Estado:
        dados = st.get("dados") or carregar_dados()
        p = _texto(st.get("pergunta")).strip()
        if not p:                                   # modo chat: lê a última HumanMessage
            for m in reversed(st.get("messages") or []):
                if isinstance(m, HumanMessage):
                    p = _texto(m.content).strip(); break
        if not p or len(p) > 500:
            return _resposta(st, "Me faça uma pergunta sobre preços/compra (ex.: 'com R$5.000, o que compro?').",
                             status="recusa_entrada", dados=dados, log=_log(st, "[CERCA 1] entrada inválida"))
        return {"pergunta": p, "dados": dados, "log": _log(st, "[CERCA 1] entrada ok")}

    def n_guarda_escopo(st: Estado) -> Estado:
        intent = cerebro.detectar_intent(st["pergunta"])
        if not intent:
            return _resposta(st, "Não consigo responder isso. Eu sei: (1) otimizar compra por orçamento, "
                             "(2) ranquear ofertas, (3) comparar preço com o histórico, (4) cobertura por categoria.",
                             status="recusa_escopo", log=_log(st, "[CERCA 2] fora do escopo"))
        return {"intent": intent, "log": _log(st, f"[CERCA 2] no escopo -> {intent}")}

    def n_planejar(st: Estado) -> Estado:
        args = cerebro.extrair_args(st["pergunta"], st["intent"])
        return {"args": args, "log": _log(st, f"[planejar] {st['intent']}({args})")}

    def n_executar(st: Estado) -> Estado:  # CERCA 3: só chama tool do TOOLBOX
        fn = T.TOOLBOX[st["intent"]]
        r = fn(st["dados"], **st["args"])
        return {"resultados": [r], "log": _log(st, f"[CERCA 3] executou {st['intent']}")}

    def n_fundamentar(st: Estado) -> Estado:  # CERCA 5
        if not st.get("resultados"):
            return _resposta(st, "Não tenho como calcular isso com os dados atuais.",
                             status="recusa_fundamentacao",
                             log=_log(st, "[CERCA 5] sem resultado de tool -> não afirma"))
        return {"log": _log(st, "[CERCA 5] fundamentado por tool")}

    def n_responder(st: Estado) -> Estado:
        txt = cerebro.narrar(st["intent"], st["resultados"][0], st.get("pergunta", ""))
        return _resposta(st, txt, status="ok",
                         log=_log(st, "[responder] narrado a partir do resultado da tool"))

    def rota_entrada(st): return END if st.get("status") == "recusa_entrada" else "guarda_escopo"
    def rota_escopo(st): return END if st.get("status") == "recusa_escopo" else "planejar"
    def rota_fund(st): return END if st.get("status") == "recusa_fundamentacao" else "responder"

    # modo form: input_schema limpo (só 'pergunta'). modo chat: sem restrição (usa 'messages').
    g = StateGraph(Estado) if chat else StateGraph(Estado, input_schema=EntradaAnalista)
    g.add_node("guarda_entrada", n_guarda_entrada)
    g.add_node("guarda_escopo", n_guarda_escopo)
    g.add_node("planejar", n_planejar)
    g.add_node("executar", n_executar)
    g.add_node("fundamentar", n_fundamentar)
    g.add_node("responder", n_responder)

    g.add_edge(START, "guarda_entrada")
    g.add_conditional_edges("guarda_entrada", rota_entrada, {"guarda_escopo": "guarda_escopo", END: END})
    g.add_conditional_edges("guarda_escopo", rota_escopo, {"planejar": "planejar", END: END})
    g.add_edge("planejar", "executar")
    g.add_edge("executar", "fundamentar")
    g.add_conditional_edges("fundamentar", rota_fund, {"responder": "responder", END: END})
    g.add_edge("responder", END)
    return g
