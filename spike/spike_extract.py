"""Spike de extração — cascata heurística (texto vs imagem) com modelo online.

Objetivo do spike: de-riscar a suposição #1 — dá pra extrair os itens com
acurácia suficiente e custo por run < 1 centavo?

Uso:
    python spike_extract.py /caminho/catalogo.pdf --out results.json

Requer OPENAI_API_KEY no ambiente (.env). Se LANGCHAIN_TRACING_V2=true e
LANGCHAIN_API_KEY estiverem setados, as chamadas são rastreadas no LangSmith.
"""
from __future__ import annotations
import argparse, base64, io, json, os, re, sys, time
from dataclasses import dataclass, field

import pdfplumber
import pymupdf
from PIL import Image
from dotenv import load_dotenv

from schemas import LLMPage, ItemExtraido

load_dotenv()

try:
    from langsmith import traceable
except Exception:  # langsmith não instalado — vira no-op
    def traceable(*dargs, **dkwargs):
        if dargs and callable(dargs[0]) and not dkwargs:
            return dargs[0]
        return lambda fn: fn

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Preços por 1M de tokens (USD). CONFERIR os valores atuais antes de confiar no número.
PRICES = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
}

# Custos/página medidos (E01-E03) — usados só na estimativa do --dry-run
COST_TEXT_PAG = float(os.getenv("COST_TEXT_PAG", "0.00065"))
COST_IMG_PAG = float(os.getenv("COST_IMG_PAG", "0.0061"))

# Filtro de relevância: página de texto sem preço NEM SKU provavelmente não tem produto
_RE_PRECO = re.compile(r"R\$\s*\d|(?<!\d)\d{1,4},\d{2}(?!\d)")  # preço BR usa vírgula; evita casar data "2026.04"
_RE_SKU = re.compile(r"\b[A-Z]{2,}[-\s]?\d{2,}\b")


def page_relevante(texto):
    t = texto or ""
    if _RE_PRECO.search(t) or _RE_SKU.search(t):
        return True, ""
    return False, "sem preço nem SKU (provável capa/índice/termos)"

# Limiar de triagem: menos que isso de texto extraível => tratar página como imagem.
TEXT_THRESHOLD_CHARS = int(os.getenv("TEXT_THRESHOLD_CHARS", "50"))
RASTER_DPI = int(os.getenv("RASTER_DPI", "180"))
MAX_IMG_PX = int(os.getenv("MAX_IMG_PX", "2000"))   # maior lado da imagem enviada à visão
Image.MAX_IMAGE_PIXELS = None                        # arquivos locais confiáveis

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_MIME = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".gif": "gif", ".bmp": "bmp"}


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMG_EXTS


def image_file_b64(path):
    with open(path, "rb") as f:
        return _encode(f.read())


def mime_for(path):
    return _MIME.get(os.path.splitext(path)[1].lower(), "png")

SYSTEM_PROMPT = (
    "Você extrai produtos de catálogos atacadistas. Para CADA produto visível, "
    "retorne código/SKU, descrição, peças por caixa, preço regular e preço de "
    "oferta (se houver preço promocional/destaque ou preço riscado, o preço "
    "válido é o mais baixo/destacado), e se está esgotado. Use confianca<0.7 "
    "quando o valor estiver ambíguo. NÃO invente itens e NÃO omita nenhum "
    "produto da página."
)


def _client():
    from openai import OpenAI
    base = OpenAI()
    if os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true":
        try:
            from langsmith.wrappers import wrap_openai
            print(f"[langsmith] tracing ON -> projeto '{os.getenv('LANGCHAIN_PROJECT', 'default')}'", file=sys.stderr)
            return wrap_openai(base)
        except Exception as e:
            print(f"[langsmith] tracing PEDIDO mas FALHOU: {e} — rode: pip install langsmith", file=sys.stderr)
    else:
        print("[langsmith] tracing OFF — defina LANGCHAIN_TRACING_V2=true no .env", file=sys.stderr)
    return base


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0

    def add(self, u):
        self.prompt += getattr(u, "prompt_tokens", 0) or 0
        self.completion += getattr(u, "completion_tokens", 0) or 0

    def cost(self, model):
        p = PRICES.get(model, PRICES["gpt-4o-mini"])
        return self.prompt / 1e6 * p["in"] + self.completion / 1e6 * p["out"]


def classify_pages(pdf_path):
    """Retorna [(pagina_idx, 'texto'|'imagem', texto_ou_None)]."""
    out = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            txt = page.extract_text() or ""
            metodo = "texto" if len(txt.strip()) >= TEXT_THRESHOLD_CHARS else "imagem"
            out.append((i, metodo, txt if metodo == "texto" else None))
    return out


def _encode(raw):
    """Downscale + recompressão JPEG: cabe no payload e barateia tokens de visão."""
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_IMG_PX, MAX_IMG_PX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def _with_retry(fn, tries=3, wait=2):
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(wait * (i + 1))


def page_png_b64(pdf_path, page_idx):
    doc = pymupdf.open(pdf_path)
    raw = doc[page_idx - 1].get_pixmap(dpi=RASTER_DPI).tobytes("png")
    doc.close()
    return _encode(raw)


def extract_text_page(client, texto):
    return client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Texto da página do catálogo:\n\n{texto}"},
        ],
        response_format=LLMPage,
    )


def extract_image_page(client, b64, mime="png"):
    return client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "Extraia todos os produtos desta página do catálogo."},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
            ]},
        ],
        response_format=LLMPage,
    )


STRUCT_PROMPT = (SYSTEM_PROMPT +
    " O texto abaixo vem MARCADO: «OFERTA:x» é preço promocional em vermelho (vira preco_oferta); "
    "«RISCADO:x» é o preço regular riscado (vira preco_regular). Havendo «OFERTA», o preco_regular "
    "é o preto/riscado e o preco_oferta é o vermelho. Não invente preços.")


def _is_red(col):
    try:
        r, g, b = (list(col) + [0, 0, 0])[:3]
        return r > 0.8 and g < 0.35 and b < 0.35
    except Exception:
        return False


def _has_strike(w, lines):
    for ln in lines:
        if abs(ln.get("top", 0) - ln.get("bottom", 0)) < 2:  # linha horizontal
            ly = ln.get("top", 0)
            if w["top"] < ly < w["bottom"] and not (ln["x1"] < w["x0"] or ln["x0"] > w["x1"]):
                return True
    return False


def build_structured_text(pdf_path, page_idx):
    """Reconstrói a página por coordenadas e marca preço vermelho (oferta) e riscado (regular).
    É o caminho barato que dá à IA um texto LIMPO em vez do extract_text() achatado."""
    with pdfplumber.open(pdf_path) as pdf:
        p = pdf.pages[page_idx - 1]
        words = p.extract_words(extra_attrs=["non_stroking_color"])
        lines = p.lines
        rows = {}
        for w in words:
            rows.setdefault(round(w["top"] / 8), []).append(w)
        out = []
        for key in sorted(rows):
            toks = []
            for w in sorted(rows[key], key=lambda ww: ww["x0"]):
                t = w["text"]
                if _is_red(w.get("non_stroking_color")):
                    t = f"«OFERTA:{t}»"
                elif _has_strike(w, lines):
                    t = f"«RISCADO:{t}»"
                toks.append(t)
            out.append(" ".join(toks))
        return "\n".join(out)


def extract_structured_page(client, stext):
    return client.beta.chat.completions.parse(
        model=MODEL,
        messages=[{"role": "system", "content": STRUCT_PROMPT},
                  {"role": "user", "content": f"Texto estruturado da página:\n\n{stext}"}],
        response_format=LLMPage,
    )


@traceable(run_type="chain", name="spike_catalogo")
def run(pdf_path, out_path, pages_filter=None, use_filter=True, dry_run=False):
    imagem_direta = is_image(pdf_path)
    if imagem_direta:
        pages = [(1, "imagem", None)]
    else:
        pages = classify_pages(pdf_path)
        if pages_filter:
            pages = [p for p in pages if p[0] in pages_filter]

    descartadas = []
    if use_filter:
        mantidas = []
        for idx, metodo, texto in pages:
            if metodo == "texto":
                ok, motivo = page_relevante(texto)
                if not ok:
                    descartadas.append({"pagina": idx, "motivo": motivo})
                    continue
            mantidas.append((idx, metodo, texto))
        pages = mantidas

    n_txt = sum(1 for _, m, _ in pages if m == "texto")
    n_img = len(pages) - n_txt
    print(f"[triagem] processar={len(pages)} (texto={n_txt}, imagem={n_img}) | descartadas={len(descartadas)}", file=sys.stderr)
    for d in descartadas:
        print(f"  descartada pág {d['pagina']}: {d['motivo']}", file=sys.stderr)

    if dry_run:
        est = n_txt * COST_TEXT_PAG + n_img * COST_IMG_PAG
        print(f"[dry-run] estimativa custo/run = US${est:.6f} "
              f"({n_txt}×{COST_TEXT_PAG} texto + {n_img}×{COST_IMG_PAG} imagem) — SEM chamar LLM", file=sys.stderr)
        print(f"[dry-run] gate {'PASSA' if est < 0.01 else 'ESTOURA'} (< US$0.01/run)", file=sys.stderr)
        return {"meta": {"dry_run": True, "estimativa_custo_run_usd": round(est, 6),
                         "paginas_processar": len(pages), "texto": n_txt, "imagem": n_img,
                         "descartadas": descartadas}}

    client = _client()
    usage = Usage()
    itens, por_pagina = [], []
    t0 = time.time()
    for idx, metodo, texto in pages:
        try:
            if metodo == "texto":
                comp = _with_retry(lambda: extract_text_page(client, texto))
                metodo_ex = "texto"
            else:
                b64 = image_file_b64(pdf_path) if imagem_direta else page_png_b64(pdf_path, idx)
                comp = _with_retry(lambda b=b64: extract_image_page(client, b, "jpeg"))
                metodo_ex = "visao_online"
            usage.add(comp.usage)
            parsed = comp.choices[0].message.parsed
            for it in parsed.itens:
                itens.append(ItemExtraido(**it.model_dump(), pagina_origem=idx, metodo_extracao=metodo_ex))
            por_pagina.append({"pagina": idx, "metodo": metodo_ex, "n_itens": len(parsed.itens)})
            print(f"  pág {idx} [{metodo_ex}]: {len(parsed.itens)} itens", file=sys.stderr)
        except Exception as e:
            por_pagina.append({"pagina": idx, "metodo": metodo, "erro": str(e)})
            print(f"  pág {idx}: ERRO {e}", file=sys.stderr)

    custo = usage.cost(MODEL)
    resultado = {
        "meta": {
            "pdf": os.path.basename(pdf_path),
            "modelo": MODEL,
            "paginas": len(pages),
            "n_itens": len(itens),
            "tokens_in": usage.prompt,
            "tokens_out": usage.completion,
            "custo_run_usd": round(custo, 6),
            "custo_por_pagina_usd": round(custo / max(len(pages), 1), 6),
            "segundos": round(time.time() - t0, 1),
            "descartadas": descartadas,
        },
        "por_pagina": por_pagina,
        "itens": [it.model_dump() for it in itens],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    m = resultado["meta"]
    print(f"\n[resultado] {m['n_itens']} itens | custo/run US${m['custo_run_usd']} "
          f"| US${m['custo_por_pagina_usd']}/página | {m['segundos']}s", file=sys.stderr)
    print(f"[gate custo] {'PASSOU' if custo < 0.01 else 'ESTOUROU'} (alvo < US$0.01/run)", file=sys.stderr)
    return resultado


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--pages", default=None,
                    help="Subconjunto de páginas 1-indexadas, ex: 1,5,12,18. Vazio = todas.")
    ap.add_argument("--project", default=None,
                    help="Projeto do LangSmith deste experimento, ex: leitor-custo-E04")
    ap.add_argument("--dry-run", action="store_true",
                    help="Só triagem+filtro+estimativa de custo, sem chamar LLM (zero token)")
    ap.add_argument("--no-filter", action="store_true",
                    help="Não descartar páginas sem preço/SKU (desliga o filtro de relevância)")
    a = ap.parse_args()
    if a.project:
        os.environ["LANGCHAIN_PROJECT"] = a.project   # cada experimento no seu próprio projeto
    pf = {int(x) for x in a.pages.split(",")} if a.pages else None
    run(a.pdf, a.out, pf, use_filter=not a.no_filter, dry_run=a.dry_run)
