"""SafetyChem falso (só para testes): login, listagem de documentos publicados e download público por GUID,
no mesmo formato do de verdade (Documentos > Documentos publicados)."""
import hashlib
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

app = FastAPI()
USUARIO, SENHA = "robo.teste", "senha-teste"
ESTADO = {"senha": SENHA, "downloads": 0}
DOCS: list[dict] = []


PREFIXO = {49: "Ficha Técnica", 1: "FDS NBR 14725 (PT)", 2: "SDS GHS (EN)"}


def publicar(nome: str, versao: str, carimbo: str, quebrado: bool = False, tipo: int = 49) -> None:
    """Publica (ou republica com versão nova) um documento do produto: 49 ficha técnica, 1 FDS, 2 SDS."""
    DOCS[:] = [d for d in DOCS if not (d["nome"] == nome and d["tipo"] == tipo)]
    DOCS.append({"nome": nome, "versao": versao, "carimbo": carimbo, "guid": str(uuid.uuid4()), "quebrado": quebrado,
                 "tipo": tipo})


publicar("FLEXX AG 2047", "03", "20250224110300")   # já importada pela tela nos testes: não deve baixar de novo
publicar("FLEXX TS 100", "01", "20260101090000")
publicar("FLEXX CL 50", "02", "20260315141500")
publicar("FLEXX QUEBRADO 1", "01", "20260401080000", quebrado=True)  # o download falha
publicar("FLEXX TS 100", "03", "20260102090000", tipo=1)
publicar("FLEXX AG 2047", "05", "20260203090000", tipo=1)
publicar("FLEXX TS 100", "03", "20260102090500", tipo=2)
publicar("FLEXX CL 20115  Demo", "01", "20210930163318", tipo=1)  # demonstração: o robô ignora


def _arquivo(d: dict) -> str:
    return f"{PREFIXO[d['tipo']]}_{d['nome']}_V{d['versao']}_{d['carimbo']}.pdf"


@app.post("/Login/Login")
async def login(req: Request):
    b = await req.json()
    ok = b.get("Username") == USUARIO and b.get("Password") == hashlib.md5(ESTADO["senha"].encode()).hexdigest()
    r = JSONResponse({"LoginValido": ok, "Url": "/NewSafetyChem" if ok else ""})
    if ok:
        r.set_cookie("SafetyChem_Session", "sessao-teste")
    return r


@app.get("/NewSafetyChem/DocumentoPublicado/GetModel")
async def modelo(req: Request):
    if req.cookies.get("SafetyChem_Session") != "sessao-teste":
        return JSONResponse({"erro": "não logado"}, 401)
    return {"model": {"EmpresaLogadaId": 684, "UsuarioColunasGrid": [{"ColunaGridId": 1}],
                      "EmpresaDocumentos": [], "DadosPesquisa": {"Paginacao": {"page": 1, "rowsPerPage": 10}}}}


@app.post("/NewSafetyChem/DocumentoPublicado/GetDocumentosPublicados")
async def documentos(req: Request):
    if req.cookies.get("SafetyChem_Session") != "sessao-teste":
        return JSONResponse({"erro": "não logado"}, 401)
    dp = (await req.json())["DadosPesquisa"]
    if dp.get("TipoDocumento") not in PREFIXO or not dp.get("UsuarioColunasGrid"):
        return {"model": {"DocumentosPublicados": []}}  # como o de verdade: sem esses campos, vem vazio
    do_tipo = [d for d in DOCS if d["tipo"] == dp["TipoDocumento"]]
    pag = dp["Paginacao"]
    ini = (pag["page"] - 1) * pag["rowsPerPage"]
    itens = [{"DocumentoPublicadoGUID": d["guid"], "NomeArquivo": _arquivo(d), "Ativo": True, "UltimaVersao": True,
              "DataPublicacao": f"{d['carimbo'][:4]}-{d['carimbo'][4:6]}-{d['carimbo'][6:8]}T10:00:00",
              "Produto": {"Versao": d["versao"], "ProdutoInformacaoIdioma": {"NomeComercial": d["nome"]}}}
             for d in sorted(do_tipo, key=lambda x: x["nome"])[ini:ini + pag["rowsPerPage"]]]
    return {"model": {"DocumentosPublicados": itens,
                      "DadosPesquisa": {"Paginacao": {**pag, "totalRegistros": len(do_tipo)}}}}


@app.get("/Doc")
async def doc(req: Request):
    guid = req.url.query
    d = next((x for x in DOCS if x["guid"] == guid), None)
    if not d or d["quebrado"]:
        return Response("não encontrado", 404)
    ESTADO["downloads"] += 1
    if d["tipo"] in (1, 2):
        return Response(b"%PDF-1.4\n%FICHA-TESTE\n" + _texto_seguranca(d).encode("utf-8"), media_type="application/pdf")
    texto = (f"Versão: {d['versao']}\nData: {d['carimbo'][6:8]}/{d['carimbo'][4:6]}/{d['carimbo'][:4]}\nFICHA TÉCNICA\n"
             f"Produto: {d['nome']}\nAPLICAÇÃO\nAplicação de {d['nome']} versão {d['versao']}.\n"
             f"ESPECIFICAÇÕES TÉCNICAS\nViscosidade cP 100 - 200\nEMBALAGENS\nTambor 220 Kg")
    return Response(b"%PDF-1.4\n%FICHA-TESTE\n" + texto.encode("utf-8"), media_type="application/pdf")


def _texto_seguranca(d: dict) -> str:
    """Como sai o texto de uma FDS/SDS de verdade: cabeçalho repetido em cada página e as 16 seções."""
    if d["tipo"] == 1:
        cab = (f"FDS\nEm conformidade com ABNT NBR 14725\nFICHA COM DADOS DE SEGURANÇA\nProduto: {d['nome']}\n"
               f"Versão: {d['versao']} Data: {d['carimbo'][6:8]}/{d['carimbo'][4:6]}/{d['carimbo'][:4]} Página: {{p}}/2\n")
        titulos = ["IDENTIFICAÇÃO", "IDENTIFICAÇÃO DE PERIGOS", "COMPOSIÇÃO E INFORMAÇÕES SOBRE OS INGREDIENTES",
                   "MEDIDAS DE PRIMEIROS-SOCORROS", "MEDIDAS DE COMBATE A INCÊNDIO",
                   "MEDIDAS DE CONTROLE PARA DERRAMAMENTO OU VAZAMENTO", "MANUSEIO E ARMAZENAMENTO",
                   "CONTROLE DE EXPOSIÇÃO E PROTEÇÃO INDIVIDUAL", "PROPRIEDADES FÍSICAS E QUÍMICAS",
                   "ESTABILIDADE E REATIVIDADE", "INFORMAÇÕES TOXICOLÓGICAS", "INFORMAÇÕES ECOLÓGICAS",
                   "CONSIDERAÇÕES SOBRE DESTINAÇÃO FINAL", "INFORMAÇÕES SOBRE TRANSPORTE",
                   "INFORMAÇÕES SOBRE REGULAMENTAÇÕES", "OUTRAS INFORMAÇÕES"]
        secao = lambda n, t: f"{n} - {t}"  # noqa: E731
        corpo = {3: "Segredo industrial 1 1 NA 18,75 - 31,25\n2 Classificação de perigos: Sensibilização da pele",
                 14: "Número ONU: Não classificado como perigoso para o transporte terrestre."}
    else:
        cab = (f"SDS\nIn accordance with Globally Harmonized System of\nClassification and Labelling of Chemicals (GHS)-\n"
               f"Chapter 1.5 and Annex 4\nSAFETY DATA SHEET\nProduct: {d['nome']}\n"
               f"Revision: {d['versao']} Date: {d['carimbo'][4:6]}/{d['carimbo'][6:8]}/{d['carimbo'][:4]} Pages:{{p}}/2\n")
        titulos = ["IDENTIFICATION", "HAZARD IDENTIFICATION", "COMPOSITION/INFORMATION ON INGREDIENTS",
                   "FIRST-AID MEASURES", "FIRE-FIGHTING MEASURES", "ACCIDENTAL RELEASE MEASURES",
                   "HANDLING AND STORAGE", "EXPOSURE CONTROLS/PERSONAL PROTECTION", "PHYSICAL AND CHEMICAL PROPERTIES",
                   "STABILITY AND REACTIVITY", "TOXICOLOGICAL INFORMATION", "ECOLOGICAL INFORMATION",
                   "DISPOSAL CONSIDERATIONS", "TRANSPORT INFORMATION", "REGULATORY INFORMATION", "OTHER INFORMATION"]
        secao = lambda n, t: f"SECTION {n}: {t}"  # noqa: E731
        corpo = {3: "Industrial secret 1 1 NA 18.75 - 31.25", 14: "UN number: Not classified as hazardous."}
    partes, nome = [cab.format(p=1)], d["nome"]
    for n, t in enumerate(titulos, 1):
        if n == 9:
            partes.append(cab.format(p=2))
        partes.append(secao(n, t) + "\n" + corpo.get(n, f"Conteúdo da seção {n} de {nome}."))
    return "\n".join(partes)


@app.post("/teste/publicar")
async def teste_publicar(req: Request):
    b = await req.json()
    publicar(b["nome"], b["versao"], b["carimbo"], tipo=b.get("tipo", 49))
    return {"ok": True}


@app.post("/teste/senha")
async def teste_senha(req: Request):
    ESTADO["senha"] = (await req.json())["senha"]
    return {"ok": True}


@app.get("/teste/estado")
async def teste_estado():
    return {"downloads": ESTADO["downloads"], "docs": len(DOCS)}
