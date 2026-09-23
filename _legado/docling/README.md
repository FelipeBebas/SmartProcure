# Avaliação do Docling — 1 página

Objetivo: ver se o Docling reconstrói a **grade** (associação produto↔preço) e
**generaliza** (LEHMOX e KNUP), sem hardcode de layout — batendo o baseline do `det_parse`.

## Rodar (na sua máquina, internet aberta)
```bat
cd "E:\@Vault\[02] Projetos & Entregas\[03] Produtos & SaaS\LeitorCatalogos\docling"
uv init
uv add docling pymupdf
uv run python run_docling.py "..\catalogo\LEHMOX 2026.04-completo.pdf" 5
```
Gera `saida.md` e `saida.json`. Me manda o `saida.md`.

## Se a instalação falhar no `antlr4-python3-runtime`
É um conflito conhecido (setuptools novo removeu `install_layout`). Fix:
```bat
uv pip install "setuptools==65.5.1"
uv pip install --no-build-isolation "antlr4-python3-runtime==4.9.3"
uv add docling pymupdf
```
Se travar mesmo assim, me avisa que eu ajusto — **não fica remendando sozinho**.

## O que eu vou avaliar no `saida.md`
1. Reconstruiu a tabela/grade (cada produto com seu preço)?
2. Pegou os preços (regular e oferta)?
3. Deu pra distinguir a promoção (o Docling pode não trazer cor — nesse caso a gente
   cruza o bbox do JSON com a cor do char, o truque que já provamos).
4. Depois: rodar a mesma coisa numa página do KNUP e ver se generaliza sem reprogramar.
