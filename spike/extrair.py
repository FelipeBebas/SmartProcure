"""Script utilitario para demonstrar a extracao em producao usando a ExtractionConfig do Agente 1.

Uso:
    uv run python extrair.py --pagina 7
    uv run python extrair.py --pagina 12
    uv run python extrair.py --todas
    uv run python extrair.py --todas --limite 0    # Imprime todos os 394 itens na tela
    uv run python extrair.py --todas --csv catalogo_completo.csv
"""
import os, sys, argparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agente"))

import pandas as pd
from engine import det_parse_cfg
from config import CONFIG_LEHMOX


def main():
    parser = argparse.ArgumentParser(description="Extracao deterministica consumindo a ExtractionConfig.")
    parser.add_argument("--pagina", type=int, default=7, help="Pagina do catalogo a extrair (ex: 3, 7, 12, 20)")
    parser.add_argument("--todas", action="store_true", help="Extrai o catalogo inteiro de 29 paginas")
    parser.add_argument("--limite", type=int, default=16, help="Limite de linhas para exibir no terminal (0 = sem limite, exibe tudo)")
    parser.add_argument("--csv", type=str, default="", help="Caminho opcional para exportar os itens extraidos para CSV")
    parser.add_argument("--pdf", default="../catalogo/LEHMOX 2026.04-completo.pdf", help="Caminho do PDF")
    args = parser.parse_args()

    pages = None if args.todas else [args.pagina]
    
    print("\n" + "=" * 60)
    print(" Executando Motor com a Configuracao do Agente 1")
    print(f" Fornecedor: {CONFIG_LEHMOX.fornecedor}")
    print(f" Regra de Oferta: {CONFIG_LEHMOX.regra_oferta.tipo} ({CONFIG_LEHMOX.regra_oferta.espaco_cor})")
    print(f" Regex SKU: {CONFIG_LEHMOX.sku_regex}")
    print("=" * 60 + "\n")

    res = det_parse_cfg(args.pdf, CONFIG_LEHMOX, pages=pages)
    todos_itens = [it for p in res.values() for it in p]

    print("[OK] Extracao concluida com sucesso!")
    print(f"Paginas processadas: {len(res)} (pagina {list(res.keys())[0] if len(res) == 1 else '1 a 29'})")
    print(f"Total de itens extraidos: {len(todos_itens)}\n")

    if todos_itens:
        cols = ["modelo_codigo", "descricao", "quantidade_caixa", "preco_regular", "preco_oferta", "esgotado"]
        df = pd.DataFrame(todos_itens)[cols]
        df.columns = ["SKU", "Descricao", "Qtd/Cx", "R$ Normal", "R$ Oferta", "Esgotado"]
        
        if args.limite == 0 or len(df) <= args.limite:
            print(df.to_string(index=False))
        else:
            print(df.head(args.limite).to_string(index=False))
            print(f"\n... e mais {len(df) - args.limite} itens extraidos nesta execucao.")
            print("(Dica: use '--limite 0' para listar todos na tela, ou '--csv itens.csv' para exportar)")

        if args.csv:
            df.to_csv(args.csv, index=False, encoding="utf-8-sig")
            print(f"\n💾 Arquivo salvo com sucesso em: {args.csv} ({len(df)} produtos)")


if __name__ == "__main__":
    main()
