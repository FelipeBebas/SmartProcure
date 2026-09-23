"""Suíte de regressão do PLANEJADOR do Analista (Agente 2) no LangSmith.

Testa o único nó não-determinístico: o planejador LLM que roteia a pergunta e
monta a Query. Afirma o ESSENCIAL semântico (ação + campo-chave), não o objeto
inteiro — o LLM não precisa cuspir JSON byte a byte igual.

Uso:
  uv run python eval_roteamento.py --local   # roda offline (sem LangSmith), imprime acurácia
  uv run python eval_roteamento.py           # sobe/atualiza o dataset e roda o experimento no LangSmith

Roda daqui (spike/, onde vivem .env com as chaves OPENAI+LANGSMITH) e importa o
agente de ../agente. QUANDO rodar: mudou o prompt (_SYS_PLAN_V2), o modelo, ou
adicionou campo/tipo de pergunta. NÃO em produção nem a cada save (custa chamadas).
"""
from __future__ import annotations
import os, sys, argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agente"))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from consulta import PerfilSessao

DATASET_NAME = "analista-roteamento"

# ── O DATASET: pergunta (+margem) -> ação/campo esperado do PLANEJADOR ─────────
# 'campo' = campo de ordenação esperado quando há um óbvio (senão None = não checa).
CASOS = [
    # consulta — ranking por oferta/desconto
    {"pergunta": "quais as maiores ofertas de hoje?",            "margem": 2.0, "acao": "consulta", "campo": "desconto_pct"},
    {"pergunta": "me dá os 3 produtos com maior desconto em reais", "margem": 2.0, "acao": "consulta", "campo": "desconto_abs"},
    {"pergunta": "top 10 ofertas de informática",                "margem": 2.0, "acao": "consulta", "campo": "desconto_pct"},  # 'informática' é redundante, não filtra
    # consulta — tendência temporal
    {"pergunta": "o que mais subiu de preço desde 2023?",        "margem": None, "acao": "consulta", "campo": "variacao_pct"},
    {"pergunta": "quais produtos o fornecedor congelou o preço?", "margem": None, "acao": "consulta", "campo": "variacao_pct"},
    # consulta — agregação/desembolso (sem campo de ordenação óbvio)
    {"pergunta": "quanto gasto pra trazer uma caixa de cada item em oferta?", "margem": None, "acao": "consulta", "campo": None},
    # consulta — lucro (planejador emite consulta; esclarecer-por-margem é DOWNSTREAM)
    {"pergunta": "qual produto me dá mais lucro por unidade?",   "margem": 2.0, "acao": "consulta", "campo": "lucro_unit"},
    {"pergunta": "quais itens passam de R$50 no meu preço de venda?", "margem": 2.0, "acao": "consulta", "campo": None},
    # otimizar — tem orçamento em R$
    {"pergunta": "com R$5000 o que eu compro?",                  "margem": None, "acao": "otimizar", "campo": None},
    {"pergunta": "com uns 3 mil, qual a melhor compra?",         "margem": None, "acao": "otimizar", "campo": None},
    # esclarecer — ambígua de verdade (sem critério computável)
    {"pergunta": "qual o melhor produto?",                       "margem": None, "acao": "esclarecer", "campo": None},
    {"pergunta": "qual o pior negócio da lista?",                "margem": None, "acao": "esclarecer", "campo": None},
    # fora de escopo
    {"pergunta": "qual a capital da França?",                    "margem": None, "acao": "fora_escopo", "campo": None},
    {"pergunta": "que horas são?",                               "margem": None, "acao": "fora_escopo", "campo": None},
]


def _saida_do_plano(plano) -> dict:
    """Recorta a saída do planejador no que a suíte afirma: ação + campo de ordenação."""
    q = getattr(plano, "query", None)
    campo = q.ordenar_por.campo.value if (q and q.ordenar_por) else None
    return {"acao": plano.acao, "campo_ordenacao": campo}


def alvo(inputs: dict, planejador) -> dict:
    """Roda o planejador num caso do dataset (o 'target' do experimento)."""
    perfil = PerfilSessao(margem=inputs.get("margem"))
    plano = planejador.planejar(inputs["pergunta"], perfil, "")
    return _saida_do_plano(plano)


# ── AVALIADORES (determinísticos) ─────────────────────────────────────────────
def ev_acao(outputs: dict, reference_outputs: dict) -> dict:
    """Métrica principal: o roteamento (ação) está correto?"""
    return {"key": "acao_correta",
            "score": int(outputs.get("acao") == reference_outputs.get("acao"))}

def ev_campo(outputs: dict, reference_outputs: dict) -> dict:
    """Secundária: quando há um campo de ordenação esperado, o planejador acertou?
    Score None = não aplicável (não penaliza casos sem campo esperado)."""
    esperado = reference_outputs.get("campo_ordenacao")
    if not esperado:
        return {"key": "campo_ordenacao_ok", "score": None}
    return {"key": "campo_ordenacao_ok",
            "score": int(outputs.get("campo_ordenacao") == esperado)}


# ── MODO LOCAL: smoke test offline, sem LangSmith ─────────────────────────────
def rodar_local(planejador):
    print(f"Rodando {len(CASOS)} casos (modo local, sem LangSmith)...\n")
    acao_ok = campo_ok = campo_tot = 0
    for c in CASOS:
        out = alvo({"pergunta": c["pergunta"], "margem": c["margem"]}, planejador)
        a = out["acao"] == c["acao"]
        acao_ok += int(a)
        marca = "✓" if a else "✗"
        extra = ""
        if c["campo"]:
            campo_tot += 1
            ce = out["campo_ordenacao"] == c["campo"]
            campo_ok += int(ce)
            extra = f" | campo: {out['campo_ordenacao']} vs {c['campo']} {'✓' if ce else '✗'}"
        print(f"  {marca} [{out['acao']:11s} vs {c['acao']:11s}] {c['pergunta'][:45]}{extra}")
    print(f"\nAção: {acao_ok}/{len(CASOS)} = {acao_ok/len(CASOS):.0%}"
          + (f" | Campo: {campo_ok}/{campo_tot}" if campo_tot else ""))


# ── MODO LANGSMITH: dataset + experimento ─────────────────────────────────────
def rodar_langsmith():
    from langsmith import Client, evaluate
    from grafo_analista_v2 import PlanejadorLLM

    client = Client()
    # cria o dataset 1x; se já existir, reusa (idempotente)
    if not client.has_dataset(dataset_name=DATASET_NAME):
        ds = client.create_dataset(DATASET_NAME,
                                   description="Roteamento do analista: pergunta -> ação/campo esperado")
        client.create_examples(
            dataset_id=ds.id,
            inputs=[{"pergunta": c["pergunta"], "margem": c["margem"]} for c in CASOS],
            outputs=[{"acao": c["acao"], "campo_ordenacao": c["campo"]} for c in CASOS])
        print(f"Dataset '{DATASET_NAME}' criado com {len(CASOS)} casos.")
    else:
        print(f"Dataset '{DATASET_NAME}' já existe — reusando.")

    planejador = PlanejadorLLM()
    resultados = evaluate(
        lambda inputs: alvo(inputs, planejador),
        data=DATASET_NAME,
        evaluators=[ev_acao, ev_campo],
        experiment_prefix="roteamento",
        client=client)
    print("\nExperimento enviado ao LangSmith. Abra o dataset "
          f"'{DATASET_NAME}' lá pra ver a nota por caso e comparar com runs anteriores.")
    return resultados


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="roda offline (sem LangSmith)")
    args = ap.parse_args()
    if args.local:
        from grafo_analista_v2 import PlanejadorLLM
        rodar_local(PlanejadorLLM())   # troque por PlanejadorMock() p/ testar sem API
    else:
        rodar_langsmith()
