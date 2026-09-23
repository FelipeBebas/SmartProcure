"""Agente 2 — Analista (v3). 
Evolução do v2 com:
  1. Reconhecimento robusto de margem direta (ex.: '1.5', '2', '1.6x', '50%').
  2. Memória contextual de pergunta pendente (_pergunta_ativa): se o usuário passou
     o markup em resposta à abertura, o agente anota o perfil e responde à pergunta
     anterior sem exigir que o usuário digite duas vezes.
  3. Memória de cálculo matemática explícita no narrador de otimização (fórmulas abertas,
     saldo restante em caixa, custo por caixa e descrição do produto).
  4. Suporte a termos em inglês e português no PlanejadorMock e PlanejadorLLM.
"""
from __future__ import annotations
import os, re, sys, json, math
from typing import Optional, TypedDict, Annotated, Literal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # p/ achar consulta/tools_analista

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field

import consulta as Q                       # contrato + executor da query estruturada
import tools_analista as T                 # otimizar_compra (tool dedicada)

CATEGORIAS_LOJISTA = ["fone", "mouse", "teclado", "carregador", "cabo",
                      "caixa de som", "hub", "adaptador"]


# ── RECURSO: catálogo carregado 1x, FORA do estado ──────────────────
_ITENS_CACHE: Optional[list[dict]] = None

def _itens(path: Optional[str] = None) -> list[dict]:
    global _ITENS_CACHE
    if _ITENS_CACHE is None:
        path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados_norm.json")
        _ITENS_CACHE = json.load(open(path, encoding="utf-8"))
    return _ITENS_CACHE

def _df(perfil: Q.PerfilSessao):
    return Q.preparar_catalogo(_itens(), perfil)


# ── ESTADO (pequeno, checkpointado) ─────────────────────────────────
class EstadoV3(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    perfil: dict          # {"margem": float|None} — perfil de sessão
    saudou: bool          # abertura já rodou?
    acao: str
    query: dict
    resultado: dict
    status: str


# ── PLANO (o que o planejador decide) ───────────────────────────────
class PlanoV3(BaseModel):
    acao: Literal["consulta", "otimizar", "esclarecer", "fora_escopo"]
    query: Optional[Q.Query] = None
    orcamento: Optional[float] = None
    pergunta_esclarece: Optional[str] = None


# ── PLANEJADOR MOCK (andaime offline — roteia por palavra-chave PT + EN) ────
class PlanejadorMock:
    """Suporta termos em português e inglês para testes locais e demo."""

    def planejar(self, pergunta: str, perfil: Q.PerfilSessao, historico: str = "") -> PlanoV3:
        p = pergunta.lower()

        # orçamento / budget -> tool de otimização
        termos_orcamento = ["orçamento", "orcamento", "comprar", "investir", "gastar",
                            "r$", "mil", "reais", "budget", "buy", "spend"]
        if any(k in p for k in termos_orcamento):
            m = re.search(r"(\d[\d.\s]*)", p.replace(",", "."))
            orc = float(re.sub(r"[.\s]", "", m.group(1))) if m else 5000.0
            return PlanoV3(acao="otimizar", orcamento=orc)

        # lucro/markup/profit -> query com campos que exigem margem
        if any(k in p for k in ["lucro", "markup", "vender", "venda", "pdv", "revender", "profit", "margin"]):
            return PlanoV3(acao="consulta", query=Q.Query(
                selecionar=[Q.Campo.descricao, Q.Campo.preco_efetivo,
                            Q.Campo.preco_venda_est, Q.Campo.lucro_unit],
                ordenar_por=Q.Ordenacao(campo=Q.Campo.lucro_unit, direcao="desc"),
                limite=10))

        # ofertas/desconto/discount -> ranking por desconto
        if any(k in p for k in ["oferta", "desconto", "promo", "barat", "discount", "offer", "cheap"]):
            return PlanoV3(acao="consulta", query=Q.Query(
                filtros=[Q.Filtro(campo=Q.Campo.em_oferta, op=Q.Operador.eq, valor=True)],
                ordenar_por=Q.Ordenacao(campo=Q.Campo.desconto_pct, direcao="desc"),
                selecionar=[Q.Campo.descricao, Q.Campo.preco_regular,
                            Q.Campo.preco_oferta, Q.Campo.desconto_pct],
                limite=10))

        # variação temporal / price trend
        if any(k in p for k in ["caiu", "subiu", "variação", "variacao", "reajuste", "2023", "aumentou", "trend", "increased"]):
            return PlanoV3(acao="consulta", query=Q.Query(
                ordenar_por=Q.Ordenacao(campo=Q.Campo.variacao_pct, direcao="desc"),
                selecionar=[Q.Campo.descricao, Q.Campo.preco_base,
                            Q.Campo.preco_regular, Q.Campo.variacao_pct],
                limite=10))

        # ambígua do domínio -> esclarecer
        if any(k in p for k in ["melhor", "pior", "vale a pena", "bom", "best", "worst"]):
            return PlanoV3(acao="esclarecer", pergunta_esclarece=(
                "'Melhor' em qual sentido: maior desconto %, menor preço unitário, ou maior margem de lucro com seu markup?"))

        return PlanoV3(acao="fora_escopo")


# ── PLANEJADOR LLM ──────────────────────────────────────────────────
class FiltroLLM(BaseModel):
    campo: Q.Campo
    op: Q.Operador
    valor: str = Field(description="Valor como texto. Número: '50' ou '0.2' (20%=0.2). "
                                   "Booleano: 'true'/'false'. Lista (op 'em'): 'a,b,c'.")

class PlanoLLM(BaseModel):
    acao: Literal["consulta", "otimizar", "esclarecer", "fora_escopo"]
    selecionar: list[Q.Campo] = Field(default_factory=list)
    filtros: list[FiltroLLM] = Field(default_factory=list)
    ordenar_campo: Optional[Q.Campo] = None
    ordenar_direcao: Optional[Literal["asc", "desc"]] = None
    agrupar_por: Optional[Q.Campo] = None
    agregacao_func: Optional[Q.FuncAgg] = None
    agregacao_campo: Optional[Q.Campo] = None
    limite: Optional[int] = None
    limite_por_grupo: Optional[int] = None
    orcamento: Optional[float] = None
    pergunta_esclarece: Optional[str] = None

_SYS_PLAN_V3 = (
    "Você roteia a pergunta de um lojista sobre um catálogo de fornecedor. Decida a AÇÃO e, "
    "quando for consulta, MONTE a query estruturada (não escreva código). Entenda inglês e português.\n"
    "Campos disponíveis:\n"
    "- base: modelo_codigo, descricao, quantidade_caixa, preco_regular, preco_oferta(pode faltar), "
    "esgotado(bool), preco_base(preço de 2023, pode faltar).\n"
    "- derivados: preco_efetivo(oferta se houver, senão regular), em_oferta(bool), "
    "desconto_pct(desconto da oferta em %), desconto_abs(desconto da oferta em R$), "
    "variacao_pct(tendência 2023→hoje), custo_caixa(preço×qtd da caixa).\n"
    "- lucro (SÓ com margem do lojista): preco_venda_est, lucro_unit.\n"
    "O catálogo NÃO mede qualidade subjetiva, durabilidade ou marca. "
    "Se pedirem 'qual o melhor' sem critério, use 'esclarecer' UMA vez oferecendo os critérios "
    "concretos existentes (preço, desconto, lucro).\n"
    "AÇÕES:\n"
    "- consulta: filtrar/rankear/agregar.\n"
    "- otimizar: SÓ quando há ORÇAMENTO em dinheiro (ex: 'R$ 5000', 'budget of 5000', '3 mil').\n"
    "- esclarecer: pergunta ambígua.\n"
    "- fora_escopo: tema fora de catálogo/preços."
)


def _coerce_scalar(campo: Q.Campo, v: str):
    v = v.strip()
    if campo in (Q.Campo.esgotado, Q.Campo.em_oferta):
        return v.lower() in ("true", "1", "sim", "verdadeiro")
    if campo in Q.CAMPOS_NUMERICOS:
        return float(v)
    return v

def _filtro_llm_para_interno(f: FiltroLLM) -> Q.Filtro:
    if f.op == Q.Operador.em:
        valor = [_coerce_scalar(f.campo, x) for x in f.valor.split(",")]
    else:
        valor = _coerce_scalar(f.campo, f.valor)
    return Q.Filtro(campo=f.campo, op=f.op, valor=valor)

def _plano_llm_para_v3(pl: PlanoLLM) -> PlanoV3:
    if pl.acao == "otimizar":
        return PlanoV3(acao="otimizar", orcamento=pl.orcamento or 5000.0)
    if pl.acao == "esclarecer":
        return PlanoV3(acao="esclarecer",
                       pergunta_esclarece=pl.pergunta_esclarece or "Pode detalhar sua pergunta?")
    if pl.acao == "fora_escopo":
        return PlanoV3(acao="fora_escopo")
    ordenar = (Q.Ordenacao(campo=pl.ordenar_campo, direcao=pl.ordenar_direcao or "desc")
               if pl.ordenar_campo else None)
    agreg = (Q.Agregacao(func=pl.agregacao_func, campo=pl.agregacao_campo)
             if (pl.agregacao_func and pl.agregacao_campo) else None)
    query = Q.Query(
        selecionar=pl.selecionar,
        filtros=[_filtro_llm_para_interno(f) for f in pl.filtros],
        ordenar_por=ordenar, agrupar_por=pl.agrupar_por, agregacao=agreg,
        limite=pl.limite, limite_por_grupo=pl.limite_por_grupo)
    return PlanoV3(acao="consulta", query=query)


class PlanejadorLLM:
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

    def planejar(self, pergunta: str, perfil: Q.PerfilSessao, historico: str = "") -> PlanoV3:
        try:
            ctx = f"(margem do lojista: {'não informada' if perfil.margem is None else perfil.margem})"
            user = (f"{ctx}\nHistórico recente da conversa:\n{historico or '(vazio)'}\n\n"
                    f"Pergunta/resposta atual do lojista: {pergunta}")
            comp = self._client().beta.chat.completions.parse(
                model=self.model,
                messages=[{"role": "system", "content": _SYS_PLAN_V3},
                          {"role": "user", "content": user}],
                response_format=PlanoLLM)
            return _plano_llm_para_v3(comp.choices[0].message.parsed)
        except Exception:
            # Fallback gracioso offline p/ testes locais ou sem rede
            return PlanejadorMock().planejar(pergunta, perfil, historico)


# ── PARSERS ROBUSTOS (margem e perguntas contextuais) ───────────────
def extrair_margem(texto: str) -> Optional[float]:
    """Reconhece: '1.5', '2', 'markup 1.5', '1.5x', '50%', 'margem de 40%'."""
    t = texto.strip().lower().replace(",", ".")
    # Número decimal ou inteiro solto direto (ex: "1.5", "2", "1.6")
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

    if any(k in t for k in ["margem", "markup", "margin"]):
        m = re.search(r"(\d+(?:\.\d+)?)", t)
        if m:
            val = float(m.group(1))
            return val if val >= 1.0 else round(1.0 + val, 4)

    return None

def tem_pergunta(texto: str, margem_extraida: Optional[float] = None) -> bool:
    """Detecta se há intenção de consulta além da simples declaração do número de markup."""
    if "?" in texto:
        return True
    resto = re.sub(r"\d+(?:[.,]\d+)?\s*[x%]?", "", texto.lower())
    palavras = [w for w in re.findall(r"[a-zà-ú]+", resto)
                if w not in {"margem", "markup", "margin", "de", "e", "a", "o", "minha",
                             "meu", "é", "uso", "trabalho", "com", "the", "my", "is"}]
    return len(palavras) >= 2

def _todas_humanas(st: EstadoV3) -> list[str]:
    res = []
    for m in (st.get("messages") or []):
        if isinstance(m, HumanMessage):
            c = m.content
            txt = c if isinstance(c, str) else " ".join(
                (b.get("text", "") if isinstance(b, dict) else str(b)) for b in c)
            res.append(txt.strip())
    return res

def _pergunta_ativa(st: EstadoV3) -> tuple[str, bool]:
    """Retorna (pergunta_a_responder, foi_retomada_do_historico).
    Se a última mensagem foi só o markup, busca a pergunta anterior não respondida."""
    humanas = _todas_humanas(st)
    if not humanas:
        return ("", False)
    ultima = humanas[-1]
    m = extrair_margem(ultima)
    if tem_pergunta(ultima, m):
        return (ultima, False)

    # A última mensagem é apenas o número/markup: procura no histórico anterior
    for h in reversed(humanas[:-1]):
        if tem_pergunta(h, extrair_margem(h)):
            return (h, True)
    return (ultima, False)


# ── STATS DE ABERTURA ───────────────────────────────────────────────
def stats_abertura() -> str:
    df = _df(Q.PerfilSessao())
    n = len(df)
    em_oferta = int(df["em_oferta"].sum())
    com_base = int(df["preco_base"].notna().sum())
    return (f"Catálogo carregado: {n} produtos, {em_oferta} em oferta agora e "
            f"{com_base} com preço de 2023 pra comparar tendência.")


# ── NARRADOR DETERMINÍSTICO COM MATEMÁTICA À MOSTRA ─────────────────
_COLNAME = {"modelo_codigo": "Código", "descricao": "Produto", "quantidade_caixa": "Un/caixa",
            "preco_regular": "Regular", "preco_oferta": "Oferta", "preco_efetivo": "Preço",
            "preco_base": "2023", "desconto_pct": "Desconto", "desconto_abs": "Desconto R$",
            "variacao_pct": "Variação", "custo_caixa": "Custo caixa", "em_oferta": "Em oferta",
            "esgotado": "Esgotado", "preco_venda_est": "Venda est.", "lucro_unit": "Lucro/un"}
_PCT_COLS = {"desconto_pct", "variacao_pct"}
_BRL_COLS = {"preco_regular", "preco_oferta", "preco_efetivo", "preco_base", "desconto_abs",
             "custo_caixa", "preco_venda_est", "lucro_unit"}

def _brl(v: float) -> str:
    return f"R${v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")

def _fmt(col: str, v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if col in _PCT_COLS:
        return f"{v * 100:.1f}%".replace(".", ",")
    if col in _BRL_COLS:
        return _brl(v)
    if col == "descricao" or col == "Produto":
        return str(v)[:32]
    if isinstance(v, bool):
        return "sim" if v else "não"
    return str(v)

def _tabela(linhas: list[dict], cols: list[str] | None = None) -> str:
    cols = cols or list(linhas[0].keys())
    head = "| " + " | ".join(_COLNAME.get(c, c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join("| " + " | ".join(_fmt(c, row.get(c)) for c in cols) + " |" for row in linhas)
    return f"{head}\n{sep}\n{body}"

def narrar_resultado(plano: PlanoV3, resultado: dict, perfil: Q.PerfilSessao) -> str:
    if plano.acao == "otimizar":
        r = resultado
        retomada = r.get("_retomada", False)
        m = perfil.margem
        prefixo = ""
        if retomada and m is not None:
            prefixo = f"✅ **Margem anotada: markup {m}x**\n\nRecuperei sua pergunta anterior do histórico. Aqui está o planejamento da compra com a matemática à mostra:\n\n"

        orc = _brl(r["orcamento"])
        gasto = _brl(r["gasto"])
        sobra = _brl(r["sobra"])
        econ = _brl(r["economia_total"])
        caixas = r["n_caixas"]

        resumo = (
            f"| 💰 Orçamento | 📦 Caixas | 💵 Total Gasto | 💸 Economia Total | 🪙 Saldo Restante |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"| {orc} | {caixas} caixas | {gasto} | {econ} | **{sobra}** |"
        )

        linhas_compra = []
        for c in r["compra"][:8]:
            p_unit = round(c["custo_caixa"] / max(c["qtd_caixa"], 1), 2)
            linhas_compra.append({
                "Código": c["sku"],
                "Produto": c.get("descricao") or "—",
                "Un/Cx": c["qtd_caixa"],
                "Preço Unit.": _brl(p_unit),
                "Custo Caixa": _brl(c["custo_caixa"]),
                "Economia": _brl(c["economia"]),
            })

        cols = ["Código", "Produto", "Un/Cx", "Preço Unit.", "Custo Caixa", "Economia"]
        prod = _tabela(linhas_compra, cols=cols)

        passo_a_passo = (
            f"### 🧮 Memória de Cálculo Passo a Passo:\n"
            f"1. **Múltiplos de Caixa Fechada:** No atacado, a compra respeita o lote mínimo por caixa (`Un/Cx`).\n"
            f"2. **Custo da Caixa:** `Preço de Oferta × Unidades por Caixa`\n"
            f"3. **Economia Gerada:** `(Preço Regular − Preço de Oferta) × Unidades por Caixa`\n"
            f"4. **Otimização:** O algoritmo priorizou as caixas que geram a maior economia absoluta dentro do teto de {orc}.\n"
            f"5. **Fechamento de Caixa:** {orc} (Orçamento) − {gasto} (Gasto) = **{sobra}** preservados no caixa da loja."
        )

        return (f"{prefixo}{resumo}\n\n"
                f"{passo_a_passo}\n\n"
                f"**Produtos recomendados para compra:**\n\n{prod}\n\n"
                f"_Cálculo 100% determinístico; você decide a compra._")

    # consulta com agregação -> 1 número
    if resultado.get("escalar") is not None:
        return f"**{_brl(resultado['escalar'])}** — total sobre {resultado['n']} itens.\n\n_Você decide._"

    linhas = resultado.get("linhas", [])
    if not linhas:
        return "Não encontrei itens que atendam a esses filtros no catálogo atual."
    cols = list(linhas[0].keys())
    return (f"**{resultado['n']} itens identificados** (a conta à mostra):\n\n{_tabela(linhas, cols)}\n\n_Você decide._")


# ── GRAFO ANALISTA V3 ───────────────────────────────────────────────
def _ultima_humana(st: EstadoV3) -> str:
    for m in reversed(st.get("messages") or []):
        if isinstance(m, HumanMessage):
            c = m.content
            return c if isinstance(c, str) else " ".join(
                (b.get("text", "") if isinstance(b, dict) else str(b)) for b in c)
    return ""

def _msg_txt(m) -> str:
    c = m.content
    return c if isinstance(c, str) else " ".join(
        (b.get("text", "") if isinstance(b, dict) else str(b)) for b in c)

def _historico(st: EstadoV3, n: int = 8) -> str:
    linhas = []
    for m in (st.get("messages") or [])[-n:]:
        papel = "Lojista" if isinstance(m, HumanMessage) else "Assistente"
        linhas.append(f"{papel}: {_msg_txt(m)}")
    return "\n".join(linhas)

def _perfil(st: EstadoV3) -> Q.PerfilSessao:
    return Q.PerfilSessao(**(st.get("perfil") or {}))

def _ai(texto: str, **extra):
    return {"messages": [AIMessage(content=texto)], **extra}


def construir_analista_v3(planejador):
    def n_abertura(st: EstadoV3) -> EstadoV3:
        txt = (stats_abertura() +
               "\n\nPra começar, qual markup você costuma aplicar? "
               "(ex.: 1.5 ou 2 = dobra o preço de custo). Pode pular e me perguntar direto também.")
        return _ai(txt, saudou=True, status="abertura")

    def n_guarda_entrada(st: EstadoV3) -> EstadoV3:
        p = _ultima_humana(st).strip()
        if not p or len(p) > 500:
            return _ai("Me faça uma pergunta sobre preços, ofertas ou orçamento de compra do catálogo.",
                       status="recusa_entrada")
        return {"status": "entrada_ok"}

    def n_captura_perfil(st: EstadoV3) -> EstadoV3:
        p = _ultima_humana(st)
        margem = extrair_margem(p)
        out = {}
        if margem is not None:
            out["perfil"] = {**(st.get("perfil") or {}), "margem": margem}
        return out or {"status": "sem_perfil_novo"}

    def n_planejar(st: EstadoV3) -> EstadoV3:
        pergunta, retomada = _pergunta_ativa(st)
        plano = planejador.planejar(pergunta, _perfil(st), _historico(st))
        return {"acao": plano.acao,
                "query": plano.query.model_dump(mode="json") if plano.query else None,
                "resultado": {"_orcamento": plano.orcamento,
                              "_esclarece": plano.pergunta_esclarece,
                              "_retomada": retomada}}

    def n_executar(st: EstadoV3) -> EstadoV3:
        perfil = _perfil(st)
        retomada = (st.get("resultado") or {}).get("_retomada", False)
        if st["acao"] == "otimizar":
            orc = (st.get("resultado") or {}).get("_orcamento") or 5000.0
            r = T.otimizar_compra(_itens(), orcamento=orc)
            r["_retomada"] = retomada
            return {"resultado": r, "status": "executado"}

        query = Q.Query(**st["query"])
        res = Q.executar(query, _df(perfil), perfil)
        if isinstance(res, Q.Esclarecer):
            return {"status": "falta_margem", "resultado": {"_esclarece": res.pergunta, "_retomada": retomada}}
        d = res.model_dump()
        d["_retomada"] = retomada
        return {"resultado": d, "status": "executado"}

    def n_fundamentar(st: EstadoV3) -> EstadoV3:
        if st.get("status") == "executado":
            return {"status": "fundamentado"}
        return _ai("Não tenho como calcular isso com os dados atuais.", status="recusa_fundamentacao")

    def n_narrar(st: EstadoV3) -> EstadoV3:
        plano = PlanoV3(acao=st["acao"], query=Q.Query(**st["query"]) if st.get("query") else None)
        return _ai(narrar_resultado(plano, st["resultado"], _perfil(st)), status="ok")

    def n_esclarecer(st: EstadoV3) -> EstadoV3:
        q = (st.get("resultado") or {}).get("_esclarece") or "Pode detalhar sua pergunta?"
        return _ai(q, status="esclarecer")

    def n_convidar(st: EstadoV3) -> EstadoV3:
        m = (st.get("perfil") or {}).get("margem")
        m_txt = f"markup {m}x" if m is not None else "—"
        return _ai(f"✅ Margem anotada: {m_txt}\n\n"
                   "Agora posso te ajudar com: maiores ofertas/descontos, variação de preço vs. 2023, "
                   "desembolso por caixa, e lucro estimado. O que você quer ver?", status="convidar")

    def n_recusa_escopo(st: EstadoV3) -> EstadoV3:
        return _ai("Isso foge do que eu faço. Eu respondo sobre preços, ofertas, variação e "
                   "compra do seu catálogo. Como posso ajudar por aí?", status="recusa_escopo")

    # -- roteadores --
    def r_start(st):
        return "guarda_entrada" if st.get("saudou") else "abertura"

    def r_entrada(st):
        return "captura_perfil" if st.get("status") == "entrada_ok" else END

    def r_pos_perfil(st):
        pergunta, _ = _pergunta_ativa(st)
        return "planejar" if tem_pergunta(pergunta, extrair_margem(pergunta)) else "convidar"

    def r_plano(st):
        return {"consulta": "executar", "otimizar": "executar",
                "esclarecer": "esclarecer", "fora_escopo": "recusa_escopo"}[st["acao"]]

    def r_exec(st):
        return "esclarecer" if st.get("status") == "falta_margem" else "fundamentar"

    def r_fund(st):
        return "narrar" if st.get("status") == "fundamentado" else END

    g = StateGraph(EstadoV3)
    for nome, fn in [("abertura", n_abertura), ("guarda_entrada", n_guarda_entrada),
                     ("captura_perfil", n_captura_perfil), ("planejar", n_planejar),
                     ("executar", n_executar), ("fundamentar", n_fundamentar),
                     ("narrar", n_narrar), ("esclarecer", n_esclarecer),
                     ("convidar", n_convidar), ("recusa_escopo", n_recusa_escopo)]:
        g.add_node(nome, fn)

    g.add_conditional_edges(START, r_start, {"abertura": "abertura", "guarda_entrada": "guarda_entrada"})
    g.add_edge("abertura", END)
    g.add_conditional_edges("guarda_entrada", r_entrada, {"captura_perfil": "captura_perfil", END: END})
    g.add_conditional_edges("captura_perfil", r_pos_perfil, {"planejar": "planejar", "convidar": "convidar"})
    g.add_conditional_edges("planejar", r_plano,
                            {"executar": "executar", "esclarecer": "esclarecer", "recusa_escopo": "recusa_escopo"})
    g.add_conditional_edges("executar", r_exec, {"fundamentar": "fundamentar", "esclarecer": "esclarecer"})
    g.add_conditional_edges("fundamentar", r_fund, {"narrar": "narrar", END: END})
    for terminal in ("narrar", "esclarecer", "convidar", "recusa_escopo"):
        g.add_edge(terminal, END)
    return g


_usar_llm = os.getenv("USE_LLM", "").lower() == "true"
_planejador = PlanejadorLLM() if _usar_llm else PlanejadorMock()
graph_analista_v3 = construir_analista_v3(_planejador).compile()
