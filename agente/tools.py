"""As TOOLS do agente de onboarding + os backends (cérebro/motor).

Duas famílias, separadas de propósito:

  CÉREBRO  (usa LLM/visão, custa dinheiro, pode errar) ......... perceber,
           propor_config, corrigir_config, bootstrap_gabarito
  MOTOR    (determinístico, US$0, não erra além da config) ...... rodar_extracao

  AVALIAR  é função pura (compara saída vs gabarito) ........... avaliar

Cada família tem um backend MOCK (offline, sem API — pra rodar o grafo e ver o
loop convergir) e uma COSTURA REAL marcada com >>> REAL <<< (visão OpenAI via
spike/spike_extract.py; motor via engine.det_parse_cfg). Trocar mock->real é
trocar o backend passado ao grafo — o grafo não muda.
"""
from __future__ import annotations
import os, re, json
from typing import Optional, Protocol
from pydantic import BaseModel

from config import ExtractionConfig, RegraOferta


# ---------- Tipos que trafegam no grafo --------------------------------------
class Percepcao(BaseModel):
    espaco_cor: str                 # "RGB" | "CMYK"
    rotulo_qtd: str                 # ex. "PC/CX" | "pçs/cx"
    exemplos_sku: list[str]
    n_colunas: int
    oferta_por: str                 # "cor" | "riscado"
    nota: str = ""


class Avaliacao(BaseModel):
    recall: float
    precisao: float
    erros_silenciosos: int
    passou: bool
    detalhe: str = ""


# ---------- AVALIAR (função pura, determinística) ----------------------------
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _campo_ok(p: dict, t: dict, c: str, tol=0.01) -> bool:
    pv, tv = p.get(c), t.get(c)
    if c.startswith("preco"):
        if tv is None:
            return pv is None
        return pv is not None and abs(float(pv) - float(tv)) <= tol
    return pv == tv


GATE_RECALL = 0.98
GATE_PRECISAO = 0.98
CAMPOS = ("preco_regular", "preco_oferta", "quantidade_caixa", "esgotado")


def avaliar(itens: list[dict], gabarito: list[dict]) -> Avaliacao:
    """Régua idêntica à do ls_evaluate: recall de itens, precisão de campos entre
    casados, e erros silenciosos (campo errado com confianca alta)."""
    pred = {_norm(i.get("modelo_codigo", "")): i for i in itens}
    truth = {_norm(t.get("modelo_codigo", "")): t for t in gabarito}
    achou = sum(1 for k in truth if k in pred)
    recall = achou / max(len(truth), 1)
    tot = ok = silenc = 0
    for k in truth:
        if k in pred:
            for c in CAMPOS:
                tot += 1
                bom = _campo_ok(pred[k], truth[k], c)
                ok += int(bom)
                if not bom and float(pred[k].get("confianca", 1.0)) >= 0.7:
                    silenc += 1
    precisao = ok / max(tot, 1)
    passou = recall >= GATE_RECALL and precisao >= GATE_PRECISAO and silenc == 0
    return Avaliacao(recall=recall, precisao=precisao, erros_silenciosos=silenc,
                     passou=passou,
                     detalhe=f"casou {achou}/{len(truth)} itens; campos {ok}/{tot}")


# ---------- Protocolos dos backends ------------------------------------------
class Cerebro(Protocol):
    def perceber(self, pdf: str, pag: int) -> Percepcao: ...
    def bootstrap_gabarito(self, pdf: str, pag: int) -> list[dict]: ...
    def propor_config(self, p: Percepcao, feedback: Optional[str]) -> ExtractionConfig: ...


class Motor(Protocol):
    def rodar_extracao(self, pdf: str, pag: int, cfg: ExtractionConfig) -> list[dict]: ...


# ============================================================================
#  BACKENDS MOCK  — offline, determinísticos, pra ver o grafo funcionando
# ============================================================================
# Cenário simulado: fornecedor CMYK (tipo KNUP). O cérebro erra na 1ª proposta
# (chuta RGB, herança do LEHMOX) e só acerta CMYK depois do feedback do gate.
# Assim o loop evaluator-optimizer converge de forma VISÍVEL, sem chamar API.

_GABARITO_FAKE = [
    {"modelo_codigo": "KN-101", "quantidade_caixa": 12, "preco_regular": 20.0,
     "preco_oferta": 15.0, "esgotado": False, "confianca": 1.0},
    {"modelo_codigo": "KN-102", "quantidade_caixa": 12, "preco_regular": 30.0,
     "preco_oferta": None, "esgotado": False, "confianca": 1.0},
    {"modelo_codigo": "KN-103", "quantidade_caixa": 24, "preco_regular": None,
     "preco_oferta": None, "esgotado": True, "confianca": 1.0},
]


class CerebroMock:
    def perceber(self, pdf: str, pag: int) -> Percepcao:
        return Percepcao(espaco_cor="CMYK", rotulo_qtd="pçs/cx",
                         exemplos_sku=["KN-101", "KN-102"], n_colunas=3,
                         oferta_por="cor",
                         nota="[mock] fornecedor tipo KNUP (CMYK)")

    def bootstrap_gabarito(self, pdf: str, pag: int) -> list[dict]:
        return [dict(x) for x in _GABARITO_FAKE]

    def propor_config(self, p: Percepcao, feedback: Optional[str]) -> ExtractionConfig:
        # Sem feedback -> erra o espaço de cor (RGB). Com feedback -> corrige.
        espaco = "RGB"
        if feedback and "cor" in feedback.lower():
            espaco = p.espaco_cor  # aprende do gate: usar o espaço percebido
        return ExtractionConfig(
            fornecedor="KNUP",
            sku_regex=r"^[A-Z]{2,4}[-/]?\w*\d\w*$",
            qty_regex=r"(\d+)\s*pçs/cx",
            regra_oferta=RegraOferta(tipo="cor", espaco_cor=espaco),
            n_colunas_hint=p.n_colunas,
        )


class MotorMock:
    """Extração fake cuja QUALIDADE depende da config estar certa.
    Modela o achado real: se o espaço de cor da oferta está errado, o motor
    NÃO reconhece o preço vermelho -> perde preco_oferta (precisão despenca)."""
    def rodar_extracao(self, pdf: str, pag: int, cfg: ExtractionConfig) -> list[dict]:
        itens = [dict(x) for x in _GABARITO_FAKE]
        if cfg.regra_oferta.espaco_cor != "CMYK":  # config errada
            for it in itens:
                if it["preco_oferta"] is not None:
                    it["preco_oferta"] = None      # não detectou a oferta
        return itens


# ============================================================================
#  COSTURAS REAIS  — trocar os mocks por isto quando for pra valer
# ============================================================================
class CerebroOpenAI:
    """>>> REAL <<<  Reaproveita spike/spike_extract.py (page_png_b64,
    extract_image_page, _client) para a visão. Requer OPENAI_API_KEY.
    NÃO ligado por padrão — é a costura do passo 2/visão."""
    def perceber(self, pdf: str, pag: int) -> Percepcao:
        raise NotImplementedError(
            "REAL: chamar visão (spike_extract.page_png_b64 + prompt de layout) "
            "e parsear as observações num Percepcao. Costura do passo 2.")

    def bootstrap_gabarito(self, pdf: str, pag: int) -> list[dict]:
        raise NotImplementedError(
            "REAL: spike_extract.extract_image_page numa amostra da página -> "
            "~5 itens de régua. Custo ~US$0,006/página (E07).")

    def propor_config(self, p: Percepcao, feedback: Optional[str]) -> ExtractionConfig:
        raise NotImplementedError(
            "REAL: LLM structured-output (response_format=ExtractionConfig) "
            "recebendo Percepcao + feedback do gate.")


class MotorDet:
    """>>> REAL <<<  O motor determinístico generalizado (engine.det_parse_cfg),
    validado 100%/100% no LEHMOX 2026 p5+p15 (reproduz E09)."""
    def rodar_extracao(self, pdf: str, pag: int, cfg: ExtractionConfig) -> list[dict]:
        from engine import det_parse_cfg  # import tardio: só quando for real
        # det_parse_cfg(pdf_path, cfg, pages) -> {pagina: [itens]}
        return det_parse_cfg(pdf, cfg, pages=[pag]).get(pag, [])


# ---------- Caminho rápido "fornecedor conhecido" (sem visão/LLM) -------------
_GT_LEHMOX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "spike", "ground_truth_p5_p15.json")


class CerebroLehmox:
    """Caminho rápido do 'identificar -> fornecedor conhecido': NÃO usa visão/LLM
    (custo US$0). Devolve a percepção e a CONFIG_LEHMOX já registradas, e o
    gabarito REAL da página (p/ o gate validar em 1 página antes de confiar).
    Usado com MotorDet -> extração real, determinística, visível no Studio.
    (O CerebroOpenAI, com o loop de visão, é o caminho do fornecedor NOVO.)"""

    def __init__(self, gt_path: Optional[str] = None):
        self.gt_path = gt_path or _GT_LEHMOX

    def perceber(self, pdf: str, pag: int) -> Percepcao:
        return Percepcao(espaco_cor="RGB", rotulo_qtd="PC/CX",
                         exemplos_sku=["LEF-1078", "LEY-1572"], n_colunas=4,
                         oferta_por="cor",
                         nota="[conhecido] LEHMOX — config já registrada (sem LLM)")

    def bootstrap_gabarito(self, pdf: str, pag: int) -> list[dict]:
        data = json.load(open(self.gt_path, encoding="utf-8"))
        itens = data.get("itens", data) if isinstance(data, dict) else data
        return [it for it in itens if it.get("pagina") == pag]

    def propor_config(self, p: Percepcao, feedback: Optional[str]) -> ExtractionConfig:
        from config import CONFIG_LEHMOX
        return CONFIG_LEHMOX.model_copy(deep=True)  # não mutar o singleton
