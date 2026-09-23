"""Teste de avaliação do Docling — converte UMA página de catálogo e exporta.

Uso:
    uv run python run_docling.py "..\\catalogo\\LEHMOX 2026.04-completo.pdf" 5
    (2º arg = número da página, 1-indexado. Default: página 5 do LEHMOX, que tem gabarito.)

Gera saida.md e saida.json. Na 1ª vez o Docling baixa os modelos do HuggingFace
(precisa de internet). Manda o saida.md que a gente compara com o gabarito.
"""
import sys, json, time
from pathlib import Path
import pymupdf
from docling.document_converter import DocumentConverter

SRC = sys.argv[1] if len(sys.argv) > 1 else "../catalogo/LEHMOX 2026.04-completo.pdf"
PAGE = int(sys.argv[2]) if len(sys.argv) > 2 else 5

tmp = "pagina_unica.pdf"
d = pymupdf.open(SRC); o = pymupdf.open()
o.insert_pdf(d, from_page=PAGE - 1, to_page=PAGE - 1); o.save(tmp)
print(f"página {PAGE} extraída de {Path(SRC).name}")

t0 = time.time()
res = DocumentConverter().convert(tmp)
doc = res.document
Path("saida.md").write_text(doc.export_to_markdown(), encoding="utf-8")
Path("saida.json").write_text(json.dumps(doc.export_to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
print(f"convertido em {time.time() - t0:.0f}s → saida.md / saida.json")
print("=" * 60)
print(doc.export_to_markdown())
