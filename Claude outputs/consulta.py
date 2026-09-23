"""
consulta.py — Contrato + executor da CONSULTA ESTRUTURADA do Analista (v2).

Princípio: o LLM emite DADOS (um objeto Query dentro da whitelist), NUNCA código.
Um executor determinístico traduz Query -> pandas sobre o catálogo (recurso).
Número só sai daqui; o LLM só orquestra e narra.

Camadas:
  1. WHITELIST (Enums)         -> a "cerca 4": Pydantic rejeita qualquer campo/op fora dela.
  2. PerfilSessao              -> margem do lojista (vive no ESTADO do grafo, não na Query).
  3. Query                     -> o que o planejador LLM emite (recuperação pura).
  4. preparar_catalogo(df)     -> calcula os campos derivados (fórmula FIXA).
  5. executar(query, df, perfil) -> aplica a Query; dispara ESCLARECER quando falta margem.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional, Any
import pandas as pd
from pydantic import BaseModel, Field


# ── 1. WHITELIST: só isto a cerca 4 aceita ──────────────────────────

class Campo(str, Enum):
    # base (vêm do dado)
    modelo_codigo    = "modelo_codigo"
    descricao        = "descricao"
    quantidade_caixa = "quantidade_caixa"
    preco_regular    = "preco_regular"
    preco_oferta     = "preco_oferta"
    esgotado         = "esgotado"
    preco_base       = "preco_base"        # 2023 (pode ser null)
    # secao          = "secao"             # ligar quando o Agente 1 extrair
    # derivados (fórmula FIXA)
    preco_efetivo    = "preco_efetivo"     # oferta ?? regular
    em_oferta        = "em_oferta"         # oferta is not null
    desconto_pct     = "desconto_pct"      # (regular-oferta)/regular (só se em oferta)
    variacao_pct     = "variacao_pct"      # (regular-base)/base      (só se tem base)
    custo_caixa      = "custo_caixa"       # preco_efetivo * quantidade_caixa
    # derivados que dependem do PERFIL (margem)
    preco_venda_est  = "preco_venda_est"   # preco_efetivo * margem
    lucro_unit       = "lucro_unit"        # preco_venda_est - preco_efetivo

CAMPOS_QUE_EXIGEM_MARGEM = {Campo.preco_venda_est, Campo.lucro_unit}
CAMPOS_NUMERICOS = {  # texto só em descricao/modelo_codigo
    Campo.quantidade_caixa, Campo.preco_regular, Campo.preco_oferta, Campo.preco_base,
    Campo.preco_efetivo, Campo.desconto_pct, Campo.variacao_pct, Campo.custo_caixa,
    Campo.preco_venda_est, Campo.lucro_unit,
}

class Operador(str, Enum):
    eq = "=="; ne = "!="; lt = "<"; le = "<="; gt = ">"; ge = ">="
    em = "em"          # isin (valor é lista)
    contem = "contem"  # texto contém

class FuncAgg(str, Enum):
    soma = "soma"; media = "media"; contagem = "contagem"
    min = "min"; max = "max"; mediana = "mediana"

_AGG = {FuncAgg.soma: "sum", FuncAgg.media: "mean", FuncAgg.contagem: "count",
        FuncAgg.min: "min", FuncAgg.max: "max", FuncAgg.mediana: "median"}


# ── 2. PERFIL DE SESSÃO (vive no estado do grafo) ───────────────────

class PerfilSessao(BaseModel):
    margem: Optional[float] = None   # ex.: 2.5 = markup 2,5x. None = ainda não informado


# ── 3. A QUERY (o que o LLM emite; recuperação pura) ────────────────

class Filtro(BaseModel):
    campo: Campo
    op: Operador
    valor: Any                       # número, bool, string ou lista (para 'em')

class Ordenacao(BaseModel):
    campo: Campo
    direcao: str = Field("desc", pattern="^(asc|desc)$")

class Agregacao(BaseModel):
    func: FuncAgg
    campo: Campo

class Query(BaseModel):
    selecionar: list[Campo] = Field(default_factory=list)
    filtros: list[Filtro] = Field(default_factory=list)   # E lógico entre eles
    ordenar_por: Optional[Ordenacao] = None
    agrupar_por: Optional[Campo] = None
    agregacao: Optional[Agregacao] = None
    limite: Optional[int] = None
    limite_por_grupo: Optional[int] = None


# ── Sinais de saída (o executor nunca inventa; ou entrega, ou pede) ──

class Esclarecer(BaseModel):
    motivo: str
    pergunta: str

class Resultado(BaseModel):
    query: Query
    linhas: list[dict] = Field(default_factory=list)   # quando devolve itens
    escalar: Optional[float] = None                    # quando devolve 1 número (agregação)
    n: int = 0


# ── 4. preparar_catalogo: campos derivados de fórmula FIXA ──────────

def preparar_catalogo(itens: list[dict], perfil: PerfilSessao) -> pd.DataFrame:
    df = pd.DataFrame(itens)
    # dedup por SKU (extração às vezes repete, ex. LEY-1575) — senão dupla contagem
    df = df.drop_duplicates(subset="modelo_codigo", keep="first").reset_index(drop=True)
    for c in ("preco_regular", "preco_oferta", "preco_base", "quantidade_caixa"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["esgotado"] = df.get("esgotado", False).astype(bool)

    df["em_oferta"]     = df["preco_oferta"].notna()
    df["preco_efetivo"] = df["preco_oferta"].where(df["em_oferta"], df["preco_regular"])
    df["desconto_pct"]  = ((df["preco_regular"] - df["preco_oferta"]) / df["preco_regular"]).where(df["em_oferta"])
    df["variacao_pct"]  = ((df["preco_regular"] - df["preco_base"]) / df["preco_base"]).where(df["preco_base"].notna())
    df["custo_caixa"]   = df["preco_efetivo"] * df["quantidade_caixa"]
    if perfil.margem is not None:
        df["preco_venda_est"] = df["preco_efetivo"] * perfil.margem
        df["lucro_unit"]      = df["preco_venda_est"] - df["preco_efetivo"]
    return df


# ── 5. executar: Query -> pandas, com a cerca do esclarecer ─────────

def _aplica_filtro(df: pd.DataFrame, f: Filtro) -> pd.Series:
    col = df[f.campo.value]
    op = f.op
    if op == Operador.contem:
        if f.campo in CAMPOS_NUMERICOS:
            raise ValueError(f"'contem' não se aplica a campo numérico ({f.campo.value}).")
        return col.astype(str).str.contains(str(f.valor), case=False, na=False)
    if op == Operador.em:
        return col.isin(f.valor if isinstance(f.valor, list) else [f.valor])
    cmp = {Operador.eq: col == f.valor, Operador.ne: col != f.valor,
           Operador.lt: col < f.valor, Operador.le: col <= f.valor,
           Operador.gt: col > f.valor, Operador.ge: col >= f.valor}
    return cmp[op]

def _campos_usados(q: Query) -> set[Campo]:
    u = set(q.selecionar) | {f.campo for f in q.filtros}
    if q.ordenar_por: u.add(q.ordenar_por.campo)
    if q.agrupar_por: u.add(q.agrupar_por)
    if q.agregacao:   u.add(q.agregacao.campo)
    return u

def executar(query: Query, df: pd.DataFrame, perfil: PerfilSessao):
    # cerca do esclarecer: pediu lucro/venda sem margem no perfil?
    if perfil.margem is None and (_campos_usados(query) & CAMPOS_QUE_EXIGEM_MARGEM):
        return Esclarecer(
            motivo="campo de lucro pedido sem margem no perfil",
            pergunta="Qual markup você costuma aplicar? (ex.: 2 = dobra o preço de custo)",
        )

    d = df
    for f in query.filtros:                       # filtros: E lógico
        d = d[_aplica_filtro(d, f)]

    # agregação -> 1 número (com ou sem groupby)
    if query.agregacao:
        func = _AGG[query.agregacao.func]
        alvo = query.agregacao.campo.value
        if query.agrupar_por:
            g = d.groupby(query.agrupar_por.value)[alvo].agg(func).reset_index()
            return Resultado(query=query, linhas=g.to_dict("records"), n=len(g))
        val = getattr(d[alvo], func)()
        return Resultado(query=query, escalar=None if pd.isna(val) else float(val), n=len(d))

    # ordenação + limites
    if query.ordenar_por:
        d = d.sort_values(query.ordenar_por.campo.value,
                          ascending=(query.ordenar_por.direcao == "asc"),
                          na_position="last")
    if query.agrupar_por and query.limite_por_grupo:   # top-N por grupo
        d = d.groupby(query.agrupar_por.value, sort=False).head(query.limite_por_grupo)
    if query.limite:
        d = d.head(query.limite)

    cols = [c.value for c in query.selecionar] or list(df.columns)
    return Resultado(query=query, linhas=d[cols].to_dict("records"), n=len(d))
