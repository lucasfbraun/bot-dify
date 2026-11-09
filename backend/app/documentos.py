"""Documentação de produto: entrega o boletim, a FISPQ etc. da pasta da rede para a pessoa abrir ou baixar.

Como funciona:
- A pasta da rede (ex.: //10.1.1.205/.../Documentação de Produto) é montada só para leitura no servidor e
  repassada ao container em DOCUMENTOS_DIR. Organização esperada: Família / Produto / arquivos.
- A plataforma indexa a pasta (de tempos em tempos), ignorando versões antigas (pastas "Obsoleto(s)",
  "Revisão anterior") e lixo do Windows/Office.
- O agente do Dify chama a ferramenta GET /api/ferramentas/documentos-produto (chave DOCUMENTOS_CHAVE) e recebe
  os arquivos atuais do produto com um link /api/documentos/<id>. O link só abre para quem está logado.
- O id é uma assinatura do caminho (HMAC com SECRET_KEY): o agente não consegue montar um caminho qualquer.
"""
import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from . import db
from .config import settings
from .security import exigir_admin, usuario_atual

log = logging.getLogger("agente.documentos")
router = APIRouter()

EXTENSOES = {"pdf": "application/pdf", "doc": "application/msword",
             "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "xls": "application/vnd.ms-excel",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
# pastas de versões antigas: nunca entregues
RX_PASTA_IGNORADA = re.compile(r"obsolet|revis[aã]o anterior|^backup|^lixeira|^old$", re.I)
RX_ARQUIVO_IGNORADO = re.compile(r"^~\$|^\.|^thumbs\.db$|^desktop\.ini$", re.I)
TIPOS = [  # (rótulo, padrão no nome do arquivo), na ordem de prioridade
    ("FISPQ", re.compile(r"\b(fispq|fds|sds|msds)\b")),
    ("Ficha de emergência", re.compile(r"ficha de emergencia|\bfe\b")),
    ("Boletim técnico", re.compile(r"boletim|ficha tecnica|\bbt\b|technical data|\btds\b")),
    ("Certificado / análise", re.compile(r"certificado|analise|laudo|\bcoa\b")),
    ("Laboratório", re.compile(r"laboratorio")),
]
MAX_RESULTADOS = 30
PALAVRAS_GENERICAS = {"flexx", "de", "do", "da", "o", "a", "boletim", "tecnico", "tecnica", "fispq", "fds", "sds",
                      "ficha", "seguranca", "dados", "documento", "documentos", "produto", "arquivo", "pdf", "word"}
# nomes que as pessoas usam para cada tipo: ficha técnica = boletim; FDS/SDS/ficha de segurança = FISPQ
SINONIMOS_TIPO = [
    (re.compile(r"ficha tecnica|boletim|technical data|\btds\b"), "boletim"),
    (re.compile(r"\b(sds|msds)\b|safety data sheet|\bingles\b|english"), "sds"),
    (re.compile(r"\b(fispq|fds)\b|ficha (com dados )?de seguranca|seguranca"), "fispq"),
    (re.compile(r"emergencia"), "ficha de emergencia"),
]


def tipo_canonico(tipo: str) -> str:
    t = normalizar(tipo)
    for rx, canonico in SINONIMOS_TIPO:
        if rx.search(t):
            return canonico
    return t

_indice: dict = {"t": 0.0, "itens": [], "por_id": {}, "erro": ""}
_trava = asyncio.Lock()


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, sem ® e sem pontuação: "FLEXX® AG_2068" -> "flexx ag 2068"."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tipo_do_arquivo(nome: str) -> str:
    n = normalizar(nome)
    for rotulo, rx in TIPOS:
        if rx.search(n):
            return rotulo
    return "Outro"


def _id(relativo: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), relativo.encode(), hashlib.sha256).hexdigest()[:32]


def _varrer(raiz: str) -> list[dict]:
    itens = []
    for pasta, subpastas, arquivos in os.walk(raiz):
        subpastas[:] = sorted(s for s in subpastas if not RX_PASTA_IGNORADA.search(s) and not s.startswith("."))
        rel_pasta = os.path.relpath(pasta, raiz)
        partes = [] if rel_pasta == "." else rel_pasta.split(os.sep)
        for nome in sorted(arquivos):
            ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
            if ext not in EXTENSOES or RX_ARQUIVO_IGNORADO.search(nome):
                continue
            relativo = "/".join(partes + [nome])
            produto = partes[-1] if partes else ""
            itens.append({
                "id": _id(relativo), "relativo": relativo, "arquivo": nome, "formato": ext,
                "familia": partes[0] if partes else "", "produto": produto,
                "tipo": tipo_do_arquivo(nome),
                "_busca_produto": normalizar(produto), "_busca_arquivo": normalizar(nome),
            })
    return itens


def ativo() -> bool:
    return bool(settings.DOCUMENTOS_DIR)


async def indice(forcar: bool = False) -> list[dict]:
    """Índice da pasta, refeito a cada DOCUMENTOS_INDICE_MIN minutos (a pasta é de rede: varrer custa)."""
    if not ativo():
        return []
    validade = settings.DOCUMENTOS_INDICE_MIN * 60
    if not forcar and _indice["itens"] and time.time() - _indice["t"] < validade:
        return _indice["itens"]
    async with _trava:
        if not forcar and _indice["itens"] and time.time() - _indice["t"] < validade:
            return _indice["itens"]
        inicio = time.time()
        try:
            if not os.path.isdir(settings.DOCUMENTOS_DIR):
                raise FileNotFoundError(settings.DOCUMENTOS_DIR)
            itens = await asyncio.to_thread(_varrer, settings.DOCUMENTOS_DIR)
        except OSError as e:
            log.warning("pasta de documentos indisponível (%s): %s", settings.DOCUMENTOS_DIR, e)
            _indice["erro"] = "A pasta de documentos não está acessível no servidor."
            return _indice["itens"]  # mantém o último índice bom
        _indice.update(t=time.time(), itens=itens, por_id={i["id"]: i for i in itens}, erro="")
        log.info("documentos indexados: %d arquivos em %.1f s", len(itens), time.time() - inicio)
        return itens


def buscar(itens: list[dict], produto: str, tipo: str = "") -> list[dict]:
    """Acha os arquivos do produto. Todas as palavras da busca precisam aparecer no nome da pasta do produto
    (ou, na falta disso, no nome do arquivo). Nome de pasta igual ao pedido vem primeiro."""
    termos = normalizar(produto).split()
    # o agente às vezes manda "boletim do AG 2032": fica só o que identifica o produto
    termos = [t for t in termos if t not in PALAVRAS_GENERICAS] or termos
    if not termos:
        return []
    tipo_n = tipo_canonico(tipo)
    achados = []
    for i in itens:
        palavras_prod = set(i["_busca_produto"].split())
        if all(t in palavras_prod for t in termos):
            nota = 0 if i["_busca_produto"] in (normalizar(produto), normalizar("flexx " + produto)) else 1
        elif all(t in set(i["_busca_arquivo"].split()) for t in termos):
            nota = 2
        else:
            continue
        if tipo_n and tipo_n not in normalizar(i["tipo"]) and tipo_n not in i["_busca_arquivo"]:
            continue
        achados.append((nota, i["produto"], i["tipo"], i["formato"] != "pdf", i["arquivo"], i))
    if not achados:
        return []
    melhor = min(a[0] for a in achados)
    achados = [a for a in achados if a[0] == melhor]
    achados.sort(key=lambda a: a[1:5])
    return [a[-1] for a in achados[:MAX_RESULTADOS]]


def _sem_marca(nome_normalizado: str) -> str:
    return re.sub(r"^flexx ", "", nome_normalizado)


def parecidos(itens: list[dict], produto: str, limite: int = 8) -> list[str]:
    """Produtos com alguma palavra em comum, para o agente sugerir quando não acha o pedido."""
    termos = set(normalizar(produto).split()) - {"flexx"}
    nomes = {}
    for i in itens:
        comum = len(termos & set(i["_busca_produto"].split()))
        if comum and i["produto"]:
            nomes[i["produto"]] = max(nomes.get(i["produto"], 0), comum)
    return [n for n, _ in sorted(nomes.items(), key=lambda x: (-x[1], x[0]))[:limite]]


def _publico(i: dict) -> dict:
    link = f"/api/documentos/{i['id']}"
    return {"produto": i["produto"], "familia": i["familia"], "tipo": i["tipo"], "arquivo": i["arquivo"],
            "formato": i["formato"].upper(), "link": link,
            "link_markdown": f"[📄 {i['arquivo']}]({link})"}


def _checar_chave(request: Request) -> None:
    chave = settings.DOCUMENTOS_CHAVE
    auth = request.headers.get("authorization", "")
    enviada = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not chave or not enviada or not secrets.compare_digest(enviada, chave):
        raise HTTPException(401, "Chave da ferramenta inválida.")


# ------------------------------------------------------------------ ferramenta para o agente (Dify)
@router.get("/api/ferramentas/documentos-produto")
async def ferramenta_documentos(request: Request, produto: str = "", tipo: str = ""):
    """Chamada pelo agente do Dify. Devolve os arquivos ATUAIS do produto, cada um com o link para a pessoa
    abrir/baixar na plataforma."""
    _checar_chave(request)
    if not ativo():
        raise HTTPException(503, "A pasta de documentos não está configurada na plataforma.")
    produto, tipo = produto.strip()[:120], tipo.strip()[:60]
    if not normalizar(produto):
        raise HTTPException(400, "Informe o produto (ex.: AG 2047).")
    itens = await indice()
    achados = [_publico(i) for i in buscar(itens, produto, tipo)]
    # SafetyChem é a fonte oficial: para os produtos que estão lá, a FISPQ e a ficha de emergência antigas da
    # pasta saem e entram os links oficiais
    from . import safetychem  # import tardio: safetychem depende deste módulo
    oficiais = safetychem.documentos_oficiais(produto, tipo)
    if oficiais:
        substituidos = {(_sem_marca(o["_chave"]), o["tipo"]) for o in oficiais}
        achados = [d for d in achados if (_sem_marca(normalizar(d["produto"])), d["tipo"]) not in substituidos]
        achados = [{k: v for k, v in o.items() if not k.startswith("_")} for o in oficiais] + achados
    if achados:
        return {"encontrados": len(achados), "documentos": achados[:MAX_RESULTADOS],
                "orientacao": "Mostre ao usuário cada documento pedido usando exatamente o link_markdown "
                              "(é por ele que o usuário abre ou baixa o arquivo). Não invente links. Documentos com "
                              "fonte \"SafetyChem (oficial, atualizada)\" substituem as versões antigas."}
    sugestoes = parecidos(itens, produto)
    return {"encontrados": 0, "documentos": [], "produtos_parecidos": sugestoes,
            "orientacao": (_indice["erro"] or "Nenhum documento atual encontrado para esse produto"
                           + (f" com o tipo \"{tipo}\"" if tipo else "") + ".")
            + (" Pergunte ao usuário se ele quis dizer um destes produtos." if sugestoes else "")}


@router.get("/api/ferramentas/openapi.json")
async def ferramenta_openapi(request: Request):
    """Esquema para cadastrar a ferramenta no Dify (Ferramentas > Ferramenta personalizada > importar da URL)."""
    servidor = settings.DOCUMENTOS_URL_FERRAMENTA or str(request.base_url).rstrip("/")
    return {
        "openapi": "3.1.0",
        "info": {"title": "Documentação de produto", "version": "1.0.0",
                 "description": "Boletins técnicos, FISPQs e demais documentos atuais dos produtos, "
                                "com link para o usuário abrir ou baixar."},
        "servers": [{"url": servidor}],
        "paths": {"/api/ferramentas/documentos-produto": {"get": {
            "operationId": "documentos_para_baixar",
            "summary": "Arquivos atuais (PDF/Word) de um produto para o usuário abrir ou baixar. Use quando o "
                       "usuário pedir a ficha técnica (boletim), a FDS/FISPQ ou outro documento de um produto. "
                       "Responda com o link_markdown de cada arquivo.",
            "parameters": [
                {"name": "produto", "in": "query", "required": True, "schema": {"type": "string"},
                 "description": "Nome ou código do produto, ex.: AG 2047, FLEXX HR 3530"},
                {"name": "tipo", "in": "query", "required": False, "schema": {"type": "string"},
                 "description": "Opcional: boletim (= ficha técnica), fds (= FISPQ/ficha de segurança), sds (ficha "
                                "de segurança em inglês), ficha de emergencia, certificado. Vazio = todos"},
            ],
            "responses": {"200": {"description": "Documentos encontrados"}},
        }}, "/api/ferramentas/safetychem": {"get": {
            "operationId": "dados_oficiais_safetychem",
            "summary": "Conteúdo OFICIAL e atualizado do produto no SafetyChem (Intertox), separado por documento: "
                       "ficha_tecnica (aplicação e propriedades de uso), fds (segurança, antiga FISPQ: as 16 seções "
                       "da ABNT NBR 14725 - perigos, composição publicada, primeiros socorros, incêndio, vazamento, "
                       "manuseio, EPI, propriedades, toxicologia, transporte...), sds (a mesma ficha de segurança em "
                       "inglês, para clientes do exterior) e ficha_emergencia. Prevalece sobre a documentação antiga "
                       "do catálogo. Use para responder perguntas sobre o produto.",
            "parameters": [
                {"name": "produto", "in": "query", "required": True, "schema": {"type": "string"},
                 "description": "Nome do produto, ex.: AG 2032, FLEXX HR 3530"},
                {"name": "documento", "in": "query", "required": False, "schema": {"type": "string"},
                 "description": "Opcional: ficha_tecnica, fds, sds ou ficha_emergencia. Vazio = todos"},
            ],
            "responses": {"200": {"description": "Documentação oficial do produto"}},
        }}},
        "components": {"securitySchemes": {"chave": {"type": "http", "scheme": "bearer"}}},
        "security": [{"chave": []}],
    }


# ------------------------------------------------------------------ download (pessoa logada)
@router.get("/api/documentos/{doc_id}")
async def baixar_documento(doc_id: str, user: dict = Depends(usuario_atual)):
    if not ativo() or not re.fullmatch(r"[0-9a-f]{32}", doc_id):
        raise HTTPException(404, "Documento não encontrado.")
    item = _indice["por_id"].get(doc_id)
    if not item:
        await indice(forcar=time.time() - _indice["t"] > 60)  # pode ser um arquivo novo
        item = _indice["por_id"].get(doc_id)
    if not item:
        raise HTTPException(404, "Documento não encontrado. Ele pode ter sido movido ou ficado obsoleto.")
    raiz = os.path.realpath(settings.DOCUMENTOS_DIR)
    caminho = os.path.realpath(os.path.join(raiz, *item["relativo"].split("/")))
    if os.path.commonpath([raiz, caminho]) != raiz or not os.path.isfile(caminho):
        raise HTTPException(404, "Documento não encontrado. Ele pode ter sido movido ou renomeado.")
    db.auditar(user["username"], "baixar_documento", item["produto"], item["relativo"])
    # PDF abre no navegador; Word/Excel baixam
    modo = "inline" if item["formato"] == "pdf" else "attachment"
    return FileResponse(caminho, media_type=EXTENSOES[item["formato"]], headers={
        "Content-Disposition": f"{modo}; filename*=UTF-8''{quote(item['arquivo'])}",
        "Cache-Control": "private, no-store"})


@router.get("/api/admin/documentos")
async def admin_documentos(admin: dict = Depends(exigir_admin)):
    """Situação do índice (para o admin conferir se a pasta está visível)."""
    itens = await indice()
    return {"ativo": ativo(), "ferramenta": bool(settings.DOCUMENTOS_CHAVE), "arquivos": len(itens),
            "produtos": len({i["produto"] for i in itens}), "erro": _indice["erro"],
            "indexado_em": _indice["t"] or None}
