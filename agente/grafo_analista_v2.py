"""Agente 2 — Analista (v2). Grafo compartimentado + query estruturada.

Diferenças frente ao analista.py (v1):
  - DADO COMO RECURSO: o catálogo é carregado 1x fora do estado (cache de processo);
    o estado carrega só messages + perfil + flags. `guarda_entrada` volta a SÓ validar.
  - AÇÃO = QUERY ESTRUTURADA (consulta.py): o planejador emite uma Query (dados),
    o executor determinístico traduz p/ pandas. Substitui as 4 tools fixas.
    (otimização continua como TOOL dedicada — outra natureza, fora da query.)
  - ABERTURA: 1x por sessão — carrega recurso, dá stats de boas-vindas, pede a MARGEM,
    que vira PERFIL DE SESSÃO no estado (não se repete por pergunta).
  - ESCLARECER em vez de recusar: pergunta ambígua, ou lucro sem margem -> pergunta de volta.

Cérebro pluggável (mesmo padrão do v1): PlanejadorMock roda offline (andaime, roteia por
palavra-chave); PlanejadorLLM (gpt-4o-mini, structured output -> Query) é o PRÓXIMO passo.
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
        _ITENS_CACHE = json.load(open(path, encoding="utf-8"))   # dedup acontece em preparar_catalogo
    return _ITENS_CACHE

def _df(perfil: Q.PerfilSessao):
    """DataFrame derivado (barato) a partir do recurso + perfil atual. Nunca vai pro estado."""
    return Q.preparar_catalogo(_itens(), perfil)


# ── ESTADO (pequeno, checkpointado) ─────────────────────────────────
class EstadoV2(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    perfil: dict          # {"margem": float|None} — perfil de sessão
    saudou: bool          # abertura já rodou?
    # trace/debug (não é dado de negócio):
    acao: str
    query: dict
    resultado: dict
    status: str


# ── PLANO (o que o planejador decide) ───────────────────────────────
class PlanoV2(BaseModel):
    acao: Literal["consulta", "otimizar", "esclarecer", "fora_escopo"]
    query: Optional[Q.Query] = None
    orcamento: Optional[float] = None
    pergunta_esclarece: Optional[str] = None


# ── PLANEJADOR MOCK (andaime offline — roteia por palavra-chave) ────
class PlanejadorMock:
    """Só existe p/ exercitar TODOS os caminhos do grafo sem gastar API.
    NÃO é o planejador de verdade — o LLM que emite Query é o próximo passo."""

    def planejar(self, pergunta: str, perfil: Q.PerfilSessao, historico: str = "") -> PlanoV2:
        p = pergunta.lower()

        # orçamento -> tool de otimização (fora da query)
        if any(k in p for k in ["orçamento", "orcamento", "comprar", "investir", "gastar", "r$", "mil", "reais"]):
            m = re.search(r"(\d[\d.\s]*)", p.replace(",", "."))
            orc = float(re.sub(r"[.\s]", "", m.group(1))) if m else 5000.0
            return PlanoV2(acao="otimizar", orcamento=orc)

        # lucro/markup -> query com campos que exigem margem (dispara esclarecer se faltar)
        if any(k in p for k in ["lucro", "markup", "vender", "venda", "pdv", "revender"]):
            return PlanoV2(acao="consulta", query=Q.Query(
                selecionar=[Q.Campo.descricao, Q.Campo.preco_efetivo,
                            Q.Campo.preco_venda_est, Q.Campo.lucro_unit],
                ordenar_por=Q.Ordenacao(campo=Q.Campo.lucro_unit, direcao="desc"),
                limite=10))

        # ofertas/desconto -> ranking por desconto
        if any(k in p for k in ["oferta", "desconto", "promo", "barat"]):
            return PlanoV2(acao="consulta", query=Q.Query(
                filtros=[Q.Filtro(campo=Q.Campo.em_oferta, op=Q.Operador.eq, valor=True)],
                ordenar_por=Q.Ordenacao(campo=Q.Campo.desconto_pct, direcao="desc"),
                selecionar=[Q.Campo.descricao, Q.Campo.preco_regular,
                            Q.Campo.preco_oferta, Q.Campo.desconto_pct],
                limite=10))

        # variação temporal
        if any(k in p for k in ["caiu", "subiu", "variação", "variacao", "reajuste", "2023", "aumentou"]):
            return PlanoV2(acao="consulta", query=Q.Query(
                ordenar_por=Q.Ordenacao(campo=Q.Campo.variacao_pct, direcao="desc"),
                selecionar=[Q.Campo.descricao, Q.Campo.preco_base,
                            Q.Campo.preco_regular, Q.Campo.variacao_pct],
                limite=10))

        # ambígua do domínio -> esclarecer (o "melhor/pior" sem critério)
        if any(k in p for k in ["melhor", "pior", "vale a pena", "bom"]):
            return PlanoV2(acao="esclarecer", pergunta_esclarece=(
                "'Melhor' em qual sentido: maior desconto, menor preço, ou maior lucro (com sua margem)?"))

        return PlanoV2(acao="fora_escopo")


# ── PLANEJADOR LLM ──────────────────────────────────────────────────
# O structured output da OpenAI não aceita `Any`; por isso o LLM emite um
# PlanoLLM (valor sempre string), e um conversor determinístico o traduz p/
# o Query interno, convertendo cada valor pelo TIPO do campo.

class FiltroLLM(BaseModel):
    campo: Q.Campo
    op: Q.Operador
    valor: str = Field(description="Valor como texto. Número: '50' ou '0.2' (20%=0.2). "
                                   "Booleano: 'true'/'false'. Lista (op 'em'): 'a,b,c'.")

class PlanoLLM(BaseModel):
    """O que o LLM emite. Achatado (sem objetos aninhados) p/ o structured output."""
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
    orcamento: Optional[float] = None            # só p/ acao='otimizar'
    pergunta_esclarece: Optional[str] = None     # só p/ acao='esclarecer'

_SYS_PLAN_V2 = (
    "Você roteia a pergunta de um lojista sobre um catálogo de fornecedor. Decida a AÇÃO e, "
    "quando for consulta, MONTE a query estruturada (não escreva código). Campos disponíveis:\n"
    "- base: modelo_codigo, descricao, quantidade_caixa, preco_regular, preco_oferta(pode faltar), "
    "esgotado(bool), preco_base(preço de 2023, pode faltar).\n"
    "- derivados: preco_efetivo(oferta se houver, senão regular), em_oferta(bool), "
    "desconto_pct(desconto da oferta em %), desconto_abs(desconto da oferta em R$), "
    "variacao_pct(tendência 2023→hoje), custo_caixa(preço×qtd da caixa).\n"
    "- lucro (SÓ com margem do lojista): preco_venda_est, lucro_unit.\n"
    "O catálogo NÃO mede qualidade, durabilidade, marca ou popularidade — não há esses campos. "
    "Se pedirem por eles, use 'esclarecer' UMA vez oferecendo os critérios que existem "
    "(preço, desconto, variação, lucro) — nunca fique perguntando em círculo.\n"
    "O lojista vende SÓ informática — o catálogo JÁ é o ramo dele. Trate 'de informática', "
    "'do meu ramo/setor', 'que eu trabalho' como REDUNDANTES: NÃO crie filtro por isso e NÃO "
    "filtre por categoria (não existe esse campo). Filtre apenas por campos concretos da lista.\n"
    "IMPORTANTE — HISTÓRICO: você recebe as últimas mensagens. Se você já pediu um esclarecimento e "
    "o lojista respondeu (mesmo em várias mensagens seguidas: 'qualidade' → 'melhor preço' → 'maior "
    "desconto' → 'valor absoluto'), COMBINE as respostas e monte a CONSULTA agora — não repita a pergunta. "
    "Use 'esclarecer' no máximo uma vez por ambiguidade.\n"
    "AÇÕES:\n"
    "- consulta: filtrar/rankear/agregar. Ex.: 'maiores ofertas' → filtro em_oferta=true, "
    "ordenar desconto_pct desc. 'desembolso de 1 caixa de cada em oferta' → filtro em_oferta=true, "
    "agregacao soma de custo_caixa. 'o que subiu de 2023' → ordenar variacao_pct desc.\n"
    "- otimizar: SÓ quando há ORÇAMENTO em R$ (extraia o número; '3 mil'=3000).\n"
    "- esclarecer: pergunta do domínio mas AMBÍGUA (ex.: 'melhor produto?' sem dizer melhor em quê) "
    "→ devolva pergunta_esclarece.\n"
    "- fora_escopo: não é sobre preço/oferta/compra/catálogo.\n"
    "Regras de valor: percentual vira decimal (20% → '0.2'); booleano 'true'/'false'; "
    "contem só em descricao. Selecione colunas úteis pra resposta. Use limite ~10 em rankings."
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

def _plano_llm_para_v2(pl: PlanoLLM) -> PlanoV2:
    if pl.acao == "otimizar":
        return PlanoV2(acao="otimizar", orcamento=pl.orcamento or 5000.0)
    if pl.acao == "esclarecer":
        return PlanoV2(acao="esclarecer",
                       pergunta_esclarece=pl.pergunta_esclarece or "Pode detalhar sua pergunta?")
    if pl.acao == "fora_escopo":
        return PlanoV2(acao="fora_escopo")
    # consulta -> monta o Query interno
    ordenar = (Q.Ordenacao(campo=pl.ordenar_campo, direcao=pl.ordenar_direcao or "desc")
               if pl.ordenar_campo else None)
    agreg = (Q.Agregacao(func=pl.agregacao_func, campo=pl.agregacao_campo)
             if (pl.agregacao_func and pl.agregacao_campo) else None)
    query = Q.Query(
        selecionar=pl.selecionar,
        filtros=[_filtro_llm_para_interno(f) for f in pl.filtros],
        ordenar_por=ordenar, agrupar_por=pl.agrupar_por, agregacao=agreg,
        limite=pl.limite, limite_por_grupo=pl.limite_por_grupo)
    return PlanoV2(acao="consulta", query=query)


class PlanejadorLLM:
    """gpt-4o-mini com structured output (PlanoLLM). O LLM emite a query como DADOS;
    o conversor traduz p/ o Query interno. Números vêm SÓ do executor determinístico."""
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
                    base = wrap_openai(base)   # custo/token no LangSmith
                except Exception:
                    pass
            self._oa = base
        return self._oa

    def planejar(self, pergunta: str, perfil: Q.PerfilSessao, historico: str = "") -> PlanoV2:
        ctx = f"(margem do lojista: {'não informada' if perfil.margem is None else perfil.margem})"
        user = (f"{ctx}\nHistórico recente da conversa:\n{historico or '(vazio)'}\n\n"
                f"Pergunta/resposta atual do lojista: {pergunta}")
        comp = self._client().beta.chat.completions.parse(
            model=self.model,
            messages=[{"role": "system", "content": _SYS_PLAN_V2},
                      {"role": "user", "content": user}],
            response_format=PlanoLLM)
        return _plano_llm_para_v2(comp.choices[0].message.parsed)


# ── PARSERS DETERMINÍSTICOS (margem/pergunta) ───────────────────────
def extrair_margem(texto: str) -> Optional[float]:
    """Heurística: 'markup 2x'/'2x' -> 2.0 ; '40%' -> 1.4 ; número solto >=1 -> markup.
    (No LLM depois isso fica mais robusto; aqui é determinístico p/ o andaime.)"""
    t = texto.lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*x", t)
    if m: return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if m: return round(1 + float(m.group(1)) / 100, 4)
    if any(k in t for k in ["margem", "markup"]):
        m = re.search(r"(\d+(?:\.\d+)?)", t)
        if m: return float(m.group(1))
    return None

def tem_pergunta(texto: str, margem_extraida: Optional[float]) -> bool:
    """Sobrou pergunta além da margem? '?' ou conteúdo significativo além do número."""
    if "?" in texto:
        return True
    resto = re.sub(r"\d+(?:[.,]\d+)?\s*[x%]?", "", texto.lower())
    palavras = [w for w in re.findall(r"[a-zà-ú]+", resto)
                if w not in {"margem", "markup", "de", "e", "a", "o", "minha", "meu", "é", "uso", "trabalho", "com"}]
    return len(palavras) >= 2


# ── STATS DE ABERTURA (só o que dá p/ computar de verdade) ──────────
def stats_abertura() -> str:
    df = _df(Q.PerfilSessao())
    n = len(df)
    em_oferta = int(df["em_oferta"].sum())
    com_base = int(df["preco_base"].notna().sum())
    return (f"Catálogo carregado: {n} produtos, {em_oferta} em oferta agora e "
            f"{com_base} com preço de 2023 pra comparar tendência.")


# ── NARRADOR DETERMINÍSTICO (v2 — tabelas markdown; números só do resultado) ─────
_COLNAME = {"modelo_codigo": "Código", "descricao": "Produto", "quantidade_caixa": "Un/caixa",
            "preco_regular": "Regular", "preco_oferta": "Oferta", "preco_efetivo": "Preço",
            "preco_base": "2023", "desconto_pct": "Desconto", "desconto_abs": "Desconto R$",
            "variacao_pct": "Variação", "custo_caixa": "Custo caixa", "em_oferta": "Em oferta",
            "esgotado": "Esgotado", "preco_venda_est": "Venda est.", "lucro_unit": "Lucro/un"}
_PCT_COLS = {"desconto_pct", "variacao_pct"}
_BRL_COLS = {"preco_regular", "preco_oferta", "preco_efetivo", "preco_base", "desconto_abs",
             "custo_caixa", "preco_venda_est", "lucro_unit"}

def _brl(v: float) -> str:  # 4608.9 -> "4.608,90"
    return f"R${v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")

def _fmt(col: str, v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if col in _PCT_COLS:
        return f"{v * 100:.1f}%".replace(".", ",")
    if col in _BRL_COLS:
        return _brl(v)
    if col == "descricao":
        return str(v)[:34]
    if isinstance(v, bool):
        return "sim" if v else "não"
    return str(v)

def _tabela(linhas: list[dict], cols: list[str] | None = None) -> str:
    cols = cols or list(linhas[0].keys())
    head = "| " + " | ".join(_COLNAME.get(c, c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = "\n".join("| " + " | ".join(_fmt(c, row.get(c)) for c in cols) + " |" for row in linhas)
    return f"{head}\n{sep}\n{body}"

def narrar_resultado(plano: PlanoV2, resultado: dict, perfil: Q.PerfilSessao) -> str:
    if plano.acao == "otimizar":
        r = resultado
        resumo = _tabela([{ "orc": _brl(r["orcamento"]), "cx": r["n_caixas"],
                            "gasto": _brl(r["gasto"]), "econ": _brl(r["economia_total"]) }],
                         cols=["orc", "cx", "gasto", "econ"])
        resumo = resumo.replace("| orc | cx | gasto | econ |",
                                "| 💰 Orçamento | 📦 Caixas | 💵 Gasto | 💸 Economia |")
        itens = [{"Código": c["sku"], "Unidades": c["qtd_caixa"], "Economia": _brl(c["economia"])}
                 for c in r["compra"][:8]]
        prod = _tabela(itens, cols=["Código", "Unidades", "Economia"])
        return (f"{resumo}\n\n**Produtos alinhados ao seu objetivo:**\n\n{prod}\n\n"
                f"_Cálculo determinístico; você decide._")

    # consulta com agregação -> 1 número
    if resultado.get("escalar") is not None:
        return f"**{_brl(resultado['escalar'])}** — total sobre {resultado['n']} itens.\n\n_Você decide._"

    linhas = resultado.get("linhas", [])
    if not linhas:
        return "Não encontrei itens que atendam a isso no catálogo atual."
    cols = list(linhas[0].keys())
    return (f"**{resultado['n']} itens** (a conta à mostra):\n\n{_tabela(linhas, cols)}\n\n_Você decide._")


# ── GRAFO ───────────────────────────────────────────────────────────
def _ultima_humana(st: EstadoV2) -> str:
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

def _historico(st: EstadoV2, n: int = 8) -> str:
    """Últimas n mensagens como transcrição — dá ao planejador a memória da conversa."""
    linhas = []
    for m in (st.get("messages") or [])[-n:]:
        papel = "Lojista" if isinstance(m, HumanMessage) else "Assistente"
        linhas.append(f"{papel}: {_msg_txt(m)}")
    return "\n".join(linhas)

def _perfil(st: EstadoV2) -> Q.PerfilSessao:
    return Q.PerfilSessao(**(st.get("perfil") or {}))

def _ai(texto: str, **extra):
    return {"messages": [AIMessage(content=texto)], **extra}


def construir_analista_v2(planejador):
    # -- nós --
    def n_abertura(st: EstadoV2) -> EstadoV2:
        txt = (stats_abertura() +
               "\n\nPra começar, qual markup você costuma aplicar? "
               "(ex.: 2 = dobra o preço de custo). Pode pular e me perguntar direto também.")
        return _ai(txt, saudou=True, status="abertura")

    def n_guarda_entrada(st: EstadoV2) -> EstadoV2:  # CERCA 1 — só valida
        p = _ultima_humana(st).strip()
        if not p or len(p) > 500:
            return _ai("Me faça uma pergunta sobre preços/ofertas/compra do catálogo.",
                       status="recusa_entrada")
        return {"status": "entrada_ok"}

    def n_captura_perfil(st: EstadoV2) -> EstadoV2:
        p = _ultima_humana(st)
        margem = extrair_margem(p)
        out = {}
        if margem is not None:
            out["perfil"] = {**(st.get("perfil") or {}), "margem": margem}
        return out or {"status": "sem_perfil_novo"}

    def n_planejar(st: EstadoV2) -> EstadoV2:  # CERCA 2 (fora_escopo) + CERCA 4 (Pydantic valida a Query)
        plano = planejador.planejar(_ultima_humana(st), _perfil(st), _historico(st))
        # guardo só dados serializáveis no estado (nada de objeto Pydantic no state)
        return {"acao": plano.acao,
                # mode="json" serializa os Enum p/ string -> estado limpo p/ o checkpointer
                "query": plano.query.model_dump(mode="json") if plano.query else None,
                "resultado": {"_orcamento": plano.orcamento, "_esclarece": plano.pergunta_esclarece}}

    def n_executar(st: EstadoV2) -> EstadoV2:
        perfil = _perfil(st)
        if st["acao"] == "otimizar":
            orc = (st.get("resultado") or {}).get("_orcamento") or 5000.0
            r = T.otimizar_compra(_itens(), orcamento=orc)
            return {"resultado": r, "status": "executado"}
        # consulta
        query = Q.Query(**st["query"])
        res = Q.executar(query, _df(perfil), perfil)
        if isinstance(res, Q.Esclarecer):
            return {"status": "falta_margem", "resultado": {"_esclarece": res.pergunta}}
        return {"resultado": res.model_dump(), "status": "executado"}

    def n_fundamentar(st: EstadoV2) -> EstadoV2:  # CERCA 6 (grounding)
        # O executor rodou? Então está fundamentado — inclusive resultado VAZIO
        # ("nenhum item bate o filtro" é resposta válida, não falha; o narrador diz isso).
        # Como o narrador é determinístico (números só do resultado), o grounding é garantido.
        if st.get("status") == "executado":
            return {"status": "fundamentado"}
        return _ai("Não tenho como calcular isso com os dados atuais.", status="recusa_fundamentacao")

    def n_narrar(st: EstadoV2) -> EstadoV2:
        plano = PlanoV2(acao=st["acao"], query=Q.Query(**st["query"]) if st.get("query") else None)
        return _ai(narrar_resultado(plano, st["resultado"], _perfil(st)), status="ok")

    def n_esclarecer(st: EstadoV2) -> EstadoV2:  # CERCA 3
        q = (st.get("resultado") or {}).get("_esclarece") or "Pode detalhar sua pergunta?"
        return _ai(q, status="esclarecer")

    def n_convidar(st: EstadoV2) -> EstadoV2:
        m = (st.get("perfil") or {}).get("margem")
        m_txt = f"markup {m}x" if m is not None else "—"
        return _ai(f"✅ Margem anotada: {m_txt}\n\n"
                   "Agora posso te ajudar com: maiores ofertas/descontos, variação de preço vs. 2023, "
                   "desembolso por caixa, e lucro estimado. O que você quer ver?", status="convidar")

    def n_recusa_escopo(st: EstadoV2) -> EstadoV2:  # CERCA 2
        return _ai("Isso foge do que eu faço. Eu respondo sobre preços, ofertas, variação e "
                   "compra do seu catálogo. Como posso ajudar por aí?", status="recusa_escopo")

    # -- roteadores --
    def r_start(st):        return "guarda_entrada" if st.get("saudou") else "abertura"
    def r_entrada(st):      return "captura_perfil" if st.get("status") == "entrada_ok" else END
    def r_pos_perfil(st):
        p = _ultima_humana(st)
        return "planejar" if tem_pergunta(p, extrair_margem(p)) else "convidar"
    def r_plano(st):
        return {"consulta": "executar", "otimizar": "executar",
                "esclarecer": "esclarecer", "fora_escopo": "recusa_escopo"}[st["acao"]]
    def r_exec(st):         return "esclarecer" if st.get("status") == "falta_margem" else "fundamentar"
    def r_fund(st):         return "narrar" if st.get("status") == "fundamentado" else END

    g = StateGraph(EstadoV2)
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


# ── Exposição p/ o Studio (offline com o mock; troque por PlanejadorLLM quando pronto) ──
_usar_llm = os.getenv("USE_LLM", "").lower() == "true"
_planejador = PlanejadorLLM() if _usar_llm else PlanejadorMock()
graph_analista_v2 = construir_analista_v2(_planejador).compile()
