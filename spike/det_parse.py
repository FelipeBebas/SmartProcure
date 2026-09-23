import re, json, pdfplumber
PDF="/mnt/user-data/uploads/LeitorCatalogos/catalogo/LEHMOX 2026.04-completo.pdf"
SKU_RE=re.compile(r'^[A-Z]{2,3}-\w*\d\w*$')      # LEF-1078, LEY-35, ST-963...
QTY_RE=re.compile(r'(\d+)\s*PC/CX', re.I)
MONEY_RE=re.compile(r'(\d{1,4}[.,]\d{2})')

def is_red(col):
    try:
        r,g,b=(list(col)+[0,0,0])[:3]; return r>0.8 and g<0.35 and b<0.35
    except: return False

def money(t):
    m=MONEY_RE.search(t.replace(',','.'))
    return float(m.group(1)) if m else None

def parse_page(page):
    words=page.extract_words(extra_attrs=["non_stroking_color"])
    skus=[w for w in words if SKU_RE.match(w["text"])]
    if not skus: return []
    # agrupa SKUs em faixas-linha por y
    skus.sort(key=lambda w:w["top"])
    bands=[]; 
    for w in skus:
        if bands and abs(w["top"]-bands[-1][0])<20: bands[-1][1].append(w)
        else: bands.append([w["top"],[w]])
    itens=[]
    for bi,(btop,brow) in enumerate(bands):
        brow.sort(key=lambda w:w["x0"])
        centers=[(w["x0"]+w["x1"])/2 for w in brow]
        # limites de coluna = pontos médios entre centros
        bounds=[-1e9]+[ (centers[i]+centers[i+1])/2 for i in range(len(centers)-1)]+[1e9]
        ybot = bands[bi+1][0]-5 if bi+1<len(bands) else 1e9
        # palavras do bloco vertical desta faixa
        blk=[w for w in words if btop-5 <= w["top"] < ybot]
        for ci,sku in enumerate(brow):
            lo,hi=bounds[ci],bounds[ci+1]
            cell=[w for w in blk if lo<=(w["x0"]+w["x1"])/2<hi]
            qty=1; reg=off=None; esg=False; desc=[]
            precos=[]
            for w in cell:
                t=w["text"]
                if w is sku: continue
                mq=QTY_RE.search(t)
                if mq: qty=int(mq.group(1)); continue
                if t.strip() in ("RS","RS$"):  # esgotado: RS sem número
                    esg=True; continue
                val=money(t)
                if val is not None:
                    precos.append((val, is_red(w.get("non_stroking_color"))))
                    continue
                # descrição: texto alfabético
                if re.search(r'[A-Za-z]', t) and not t.startswith('特'):
                    desc.append(t)
            reds=[v for v,r in precos if r]; blacks=[v for v,r in precos if not r]
            if reds: off=reds[0]
            if blacks: reg=blacks[0]
            elif len(reds)>1: reg=None
            if esg: reg=off=None
            itens.append({"modelo_codigo":sku["text"],"descricao":" ".join(desc)[:60],
                          "quantidade_caixa":qty,"preco_regular":reg,"preco_oferta":off,"esgotado":esg})
    return itens

def norm(s): return re.sub(r'[^a-z0-9]','',str(s).lower())
gt=json.load(open("ground_truth_p5_p15.json"))["itens"]
gtidx={(it["pagina"],norm(it["modelo_codigo"])):it for it in gt}
pdf=pdfplumber.open(PDF)
res={}
for pg in (5,15):
    res[pg]=parse_page(pdf.pages[pg-1])
pdf.close()

# eval local
def ok(p,t,c,tol=0.01):
    pv,tv=p.get(c),t.get(c)
    if c.startswith("preco"):
        if tv is None: return pv is None
        return pv is not None and abs(float(pv)-float(tv))<=tol
    return pv==tv
tot_recall=tot_found=0; campos=["preco_regular","preco_oferta","quantidade_caixa","esgotado"]; cok=ctot=0; miss=[]
for pg in (5,15):
    pidx={norm(i["modelo_codigo"]):i for i in res[pg]}
    truth=[it for it in gt if it["pagina"]==pg]
    for t in truth:
        tot_recall+=1
        k=norm(t["modelo_codigo"])
        if k in pidx:
            tot_found+=1; p=pidx[k]
            for c in campos:
                ctot+=1; 
                if ok(p,t,c): cok+=1
                else: miss.append(f"p{pg} {t['modelo_codigo']} {c}: pred={p.get(c)} vs truth={t.get(c)}")
        else: miss.append(f"p{pg} {t['modelo_codigo']} NAO ENCONTRADO")
print(f"RECALL itens: {tot_found}/{tot_recall} = {tot_found/tot_recall:.0%}")
print(f"PRECISAO campos (reg/oferta/qtd/esgotado): {cok}/{ctot} = {cok/ctot:.0%}")
print("\nERROS:")
for m in miss: print("  ",m)
