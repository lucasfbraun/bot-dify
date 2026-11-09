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


def publicar(nome: str, versao: str, carimbo: str, quebrado: bool = False) -> None:
    """Publica (ou republica com versão nova) a ficha técnica de um produto."""
    DOCS[:] = [d for d in DOCS if d["nome"] != nome]
    DOCS.append({"nome": nome, "versao": versao, "carimbo": carimbo, "guid": str(uuid.uuid4()), "quebrado": quebrado})


publicar("FLEXX AG 2047", "03", "20250224110300")   # já importada pela tela nos testes: não deve baixar de novo
publicar("FLEXX TS 100", "01", "20260101090000")
publicar("FLEXX CL 50", "02", "20260315141500")
publicar("FLEXX QUEBRADO 1", "01", "20260401080000", quebrado=True)  # o download falha


def _arquivo(d: dict) -> str:
    return f"Ficha Técnica_{d['nome']}_V{d['versao']}_{d['carimbo']}.pdf"


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
    if dp.get("TipoDocumento") != 49 or not dp.get("UsuarioColunasGrid"):
        return {"model": {"DocumentosPublicados": []}}  # como o de verdade: sem esses campos, vem vazio
    pag = dp["Paginacao"]
    ini = (pag["page"] - 1) * pag["rowsPerPage"]
    itens = [{"DocumentoPublicadoGUID": d["guid"], "NomeArquivo": _arquivo(d), "Ativo": True, "UltimaVersao": True,
              "DataPublicacao": f"{d['carimbo'][:4]}-{d['carimbo'][4:6]}-{d['carimbo'][6:8]}T10:00:00",
              "Produto": {"Versao": d["versao"], "ProdutoInformacaoIdioma": {"NomeComercial": d["nome"]}}}
             for d in sorted(DOCS, key=lambda x: x["nome"])[ini:ini + pag["rowsPerPage"]]]
    return {"model": {"DocumentosPublicados": itens,
                      "DadosPesquisa": {"Paginacao": {**pag, "totalRegistros": len(DOCS)}}}}


@app.get("/Doc")
async def doc(req: Request):
    guid = req.url.query
    d = next((x for x in DOCS if x["guid"] == guid), None)
    if not d or d["quebrado"]:
        return Response("não encontrado", 404)
    ESTADO["downloads"] += 1
    texto = (f"Versão: {d['versao']}\nData: {d['carimbo'][6:8]}/{d['carimbo'][4:6]}/{d['carimbo'][:4]}\nFICHA TÉCNICA\n"
             f"Produto: {d['nome']}\nAPLICAÇÃO\nAplicação de {d['nome']} versão {d['versao']}.\n"
             f"ESPECIFICAÇÕES TÉCNICAS\nViscosidade cP 100 - 200\nEMBALAGENS\nTambor 220 Kg")
    return Response(b"%PDF-1.4\n%FICHA-TESTE\n" + texto.encode("utf-8"), media_type="application/pdf")


@app.post("/teste/publicar")
async def teste_publicar(req: Request):
    b = await req.json()
    publicar(b["nome"], b["versao"], b["carimbo"])
    return {"ok": True}


@app.post("/teste/senha")
async def teste_senha(req: Request):
    ESTADO["senha"] = (await req.json())["senha"]
    return {"ok": True}


@app.get("/teste/estado")
async def teste_estado():
    return {"downloads": ESTADO["downloads"], "docs": len(DOCS)}
