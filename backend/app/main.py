"""Agente Técnico PU - frontend do Grupo Flexível (API + arquivos do PWA)."""
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, dify, ldap_auth
from .config import settings
from .security import (checar_csrf, criar_token, definir_cookie, exigir_admin, limite_login, limpar_cookie,
                       usuario_atual)

log = logging.getLogger("agente")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONT = os.getenv("FRONTEND_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
FRONT = os.path.abspath(FRONT)

@asynccontextmanager
async def _ciclo(_app):
    db.init_db()
    if not settings.DIFY_API_KEY:
        log.warning("DIFY_API_KEY não configurada: o chat não vai funcionar.")
    if not os.getenv("SECRET_KEY"):
        log.warning("SECRET_KEY não definida: as sessões caem a cada reinício do container.")
    log.info("Pronto. Admins iniciais: %s", ", ".join(settings.INITIAL_ADMINS) or "(nenhum)")
    yield


app = FastAPI(title=settings.APP_NAME, docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(checar_csrf)], lifespan=_ciclo)


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; font-src 'self' data:; frame-ancestors 'none'; base-uri 'self'")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "")


def _erro_dify(e: dify.DifyErro):
    log.warning("Dify %s: %s", e.status, e.mensagem)
    status = 502 if e.status >= 500 or e.status == 401 else e.status
    return HTTPException(status, f"O agente não respondeu como esperado ({e.mensagem}).")


# ------------------------------------------------------------------ público
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def config_publica():
    return {"app_name": settings.APP_NAME, "short_name": settings.APP_SHORT_NAME, "dominio": settings.LDAP_DOMAIN}


class LoginIn(BaseModel):
    usuario: str = Field(min_length=1, max_length=120)
    senha: str = Field(min_length=1, max_length=256)


@app.post("/api/auth/login")
def login(dados: LoginIn, request: Request):
    username = ldap_auth.normalizar_usuario(dados.usuario)
    ip = _ip(request)
    if not re.fullmatch(r"[a-z0-9._\-]{1,64}", username):
        raise HTTPException(400, "Usuário inválido.")
    espera = limite_login.bloqueado(username, ip)
    if espera:
        db.registrar_acesso(username, ip, "bloqueado")
        raise HTTPException(429, f"Muitas tentativas. Tente de novo em {max(1, espera // 60)} min.")

    try:
        ad = ldap_auth.autenticar(username, dados.senha)
    except PermissionError as e:
        motivo = str(e)
        limite_login.falhou(username, ip)
        db.registrar_acesso(username, ip, motivo)
        if motivo == "desabilitado":
            raise HTTPException(403, "Sua conta está desabilitada no AD.")
        raise HTTPException(401, "Usuário ou senha inválidos.")
    except ldap_auth.LdapErro as e:
        log.error("LDAP: %s", e)
        db.registrar_acesso(username, ip, "erro_ldap", str(e))
        raise HTTPException(503, "Não foi possível falar com o servidor de autenticação. Tente novamente.")

    user = db.get_user(username)
    if not user and username in settings.INITIAL_ADMINS:
        db.upsert_user(username, ad["nome"], ad["email"], ad["departamento"], ad["cargo"], "admin", True, "sistema")
        db.auditar("sistema", "admin_inicial", username)
        user = db.get_user(username)
    if not user:
        db.registrar_acesso(username, ip, "nao_cadastrado")
        raise HTTPException(403, "Você ainda não tem acesso ao Agente Técnico PU. Peça a liberação ao administrador.")
    if not user["ativo"]:
        db.registrar_acesso(username, ip, "inativo")
        raise HTTPException(403, "Seu acesso está desativado. Fale com o administrador.")

    # mantém nome/e-mail/setor atualizados com o AD
    db.update_user(username, nome=ad["nome"], email=ad["email"], departamento=ad["departamento"], cargo=ad["cargo"])
    db.registrar_login_ok(username)
    db.registrar_acesso(username, ip, "ok")
    limite_login.sucesso(username)
    resp = JSONResponse({"ok": True, "usuario": _publico(db.get_user(username))})
    definir_cookie(resp, criar_token(username))
    return resp


@app.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    limpar_cookie(resp)
    return resp


def _publico(u: dict) -> dict:
    return {k: u.get(k) for k in ("username", "nome", "email", "departamento", "cargo", "perfil")}


@app.get("/api/me")
def me(user: dict = Depends(usuario_atual)):
    return _publico(user)


# ------------------------------------------------------------------ agente
_cache_params = {"t": 0, "v": None}


@app.get("/api/agente/info")
async def agente_info(user: dict = Depends(usuario_atual)):
    if time.time() - _cache_params["t"] > 300 or _cache_params["v"] is None:
        try:
            p = await dify.parametros()
            _cache_params.update(t=time.time(), v={
                "abertura": p.get("opening_statement") or "",
                "sugestoes": p.get("suggested_questions") or [],
                "sugestoes_apos_resposta": bool((p.get("suggested_questions_after_answer") or {}).get("enabled")),
            })
        except Exception as e:  # noqa: BLE001
            log.warning("parâmetros do Dify indisponíveis: %s", e)
            return {"abertura": "", "sugestoes": [], "sugestoes_apos_resposta": False}
    return _cache_params["v"]


@app.get("/api/conversas")
async def listar_conversas(last_id: str | None = None, user: dict = Depends(usuario_atual)):
    try:
        r = await dify.conversas(user["username"], last_id)
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"itens": [{"id": c["id"], "nome": c.get("name") or "Nova conversa",
                       "atualizado": c.get("updated_at") or c.get("created_at")} for c in r.get("data", [])],
            "mais": r.get("has_more", False)}


def _fontes(meta: dict | None) -> list[dict]:
    vistos, out = set(), []
    for r in (meta or {}).get("retriever_resources") or []:
        nome = r.get("document_name") or ""
        if nome and nome not in vistos:
            vistos.add(nome)
            out.append({"documento": nome, "base": r.get("dataset_name") or ""})
    return out


@app.get("/api/conversas/{cid}/mensagens")
async def listar_mensagens(cid: str, first_id: str | None = None, user: dict = Depends(usuario_atual)):
    try:
        r = await dify.mensagens(user["username"], cid, first_id)
    except dify.DifyErro as e:
        raise _erro_dify(e)
    itens = []
    for m in r.get("data", []):
        itens.append({
            "id": m["id"], "pergunta": m.get("query") or "", "resposta": m.get("answer") or "",
            "criado": m.get("created_at"),
            "avaliacao": (m.get("feedback") or {}).get("rating"),
            "fontes": _fontes({"retriever_resources": m.get("retriever_resources")}),
        })
    return {"itens": itens, "mais": r.get("has_more", False)}


class RenomearIn(BaseModel):
    nome: str = Field(default="", max_length=120)


@app.patch("/api/conversas/{cid}")
async def renomear_conversa(cid: str, dados: RenomearIn, user: dict = Depends(usuario_atual)):
    try:
        r = await dify.renomear(user["username"], cid, dados.nome.strip() or None)
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"id": cid, "nome": r.get("name") or dados.nome}


@app.delete("/api/conversas/{cid}")
async def apagar_conversa(cid: str, user: dict = Depends(usuario_atual)):
    try:
        await dify.apagar(user["username"], cid)
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"ok": True}


class FeedbackIn(BaseModel):
    avaliacao: str | None = Field(default=None, pattern="^(like|dislike)$")
    comentario: str = Field(default="", max_length=1000)


@app.post("/api/mensagens/{mid}/feedback")
async def avaliar(mid: str, dados: FeedbackIn, user: dict = Depends(usuario_atual)):
    try:
        await dify.feedback(user["username"], mid, dados.avaliacao, dados.comentario)
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"ok": True}


@app.post("/api/chat/{task_id}/parar")
async def parar(task_id: str, user: dict = Depends(usuario_atual)):
    try:
        await dify.parar(user["username"], task_id)
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"ok": True}


class ChatIn(BaseModel):
    pergunta: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


# Nomes amigáveis do que o agente está fazendo (ferramentas do Agente Técnico PU)
_STATUS = [
    (re.compile(r"qlik_", re.I), "Consultando o ERP no Qlik"),
    (re.compile(r"buscar_documentos", re.I), "Buscando certificados, especificações e normas"),
    (re.compile(r"documentos_do_produto|propriedades_disponiveis|buscar_por_propriedades|listar_produtos|resumo_catalogo", re.I),
     "Consultando boletins técnicos e FISPQs"),
    (re.compile(r"tavily", re.I), "Pesquisando na internet"),
    (re.compile(r"conhecimento|knowledge", re.I), "Consultando a literatura técnica"),
]


def _status_de(evento: dict) -> str | None:
    texto = json.dumps(evento.get("data") or {k: evento.get(k) for k in ("tool", "thought")}, ensure_ascii=False)[:4000]
    for rx, rotulo in _STATUS:
        if rx.search(texto):
            return rotulo
    if evento.get("event") == "node_started":
        tipo = (evento.get("data") or {}).get("node_type")
        if tipo in ("agent", "llm"):
            return "Analisando e redigindo a resposta"
    return None


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(dados: ChatIn, user: dict = Depends(usuario_atual)):
    username = user["username"]

    async def gerar():
        enviado_inicio = False
        ultimo_status = None
        try:
            async for ev in dify.stream_chat(username, dados.pergunta.strip(), dados.conversation_id):
                tipo = ev.get("event")
                if not enviado_inicio and ev.get("conversation_id"):
                    enviado_inicio = True
                    yield _sse({"t": "inicio", "conversation_id": ev.get("conversation_id"),
                                "message_id": ev.get("message_id"), "task_id": ev.get("task_id")})
                if tipo in ("message", "agent_message"):
                    if ev.get("answer"):
                        yield _sse({"t": "texto", "v": ev["answer"]})
                elif tipo == "message_replace":
                    yield _sse({"t": "substituir", "v": ev.get("answer", "")})
                elif tipo in ("agent_thought", "agent_log", "node_started"):
                    st = _status_de(ev)
                    if st and st != ultimo_status:
                        ultimo_status = st
                        yield _sse({"t": "status", "v": st})
                elif tipo == "message_end":
                    yield _sse({"t": "fim", "conversation_id": ev.get("conversation_id"),
                                "message_id": ev.get("message_id") or ev.get("id"),
                                "fontes": _fontes(ev.get("metadata"))})
                elif tipo == "error":
                    yield _sse({"t": "erro", "v": ev.get("message") or "Erro no agente."})
                elif tipo == "ping":
                    yield ": ping\n\n"
        except dify.DifyErro as e:
            log.warning("Dify %s: %s", e.status, e.mensagem)
            yield _sse({"t": "erro", "v": f"O agente não respondeu ({e.mensagem})."})
        except Exception as e:  # noqa: BLE001
            log.exception("falha no streaming")
            yield _sse({"t": "erro", "v": f"Falha na comunicação com o agente: {type(e).__name__}"})

    return StreamingResponse(gerar(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ administração
class NovoUsuarioIn(BaseModel):
    usuario: str = Field(min_length=1, max_length=120)
    perfil: str = Field(default="usuario", pattern="^(usuario|admin)$")


class AlterarUsuarioIn(BaseModel):
    perfil: str | None = Field(default=None, pattern="^(usuario|admin)$")
    ativo: bool | None = None


@app.get("/api/admin/usuarios")
def admin_listar(admin: dict = Depends(exigir_admin)):
    return {"itens": db.list_users()}


@app.get("/api/admin/ad")
def admin_pesquisar_ad(q: str, admin: dict = Depends(exigir_admin)):
    try:
        achados = ldap_auth.pesquisar(q)
    except ldap_auth.LdapErro as e:
        raise HTTPException(503, f"AD indisponível: {e}")
    cadastrados = {u["username"] for u in db.list_users()}
    return {"itens": [{**{k: a[k] for k in ("username", "nome", "email", "departamento", "cargo")},
                       "cadastrado": a["username"] in cadastrados} for a in achados]}


@app.post("/api/admin/usuarios")
def admin_incluir(dados: NovoUsuarioIn, admin: dict = Depends(exigir_admin)):
    try:
        ad = ldap_auth.buscar_usuario(dados.usuario)
    except ldap_auth.LdapErro as e:
        raise HTTPException(503, f"AD indisponível: {e}")
    if not ad:
        raise HTTPException(404, "Usuário não encontrado no AD.")
    if ad["desabilitado"]:
        raise HTTPException(400, "Essa conta está desabilitada no AD.")
    existente = db.get_user(ad["username"])
    db.upsert_user(ad["username"], ad["nome"], ad["email"], ad["departamento"], ad["cargo"],
                   dados.perfil, True, admin["username"])
    db.auditar(admin["username"], "reativar" if existente else "incluir", ad["username"], dados.perfil)
    return db.get_user(ad["username"])


@app.patch("/api/admin/usuarios/{username}")
def admin_alterar(username: str, dados: AlterarUsuarioIn, admin: dict = Depends(exigir_admin)):
    alvo = db.get_user(username)
    if not alvo:
        raise HTTPException(404, "Usuário não cadastrado.")
    perde_admin = alvo["perfil"] == "admin" and alvo["ativo"] and (
        (dados.perfil and dados.perfil != "admin") or dados.ativo is False)
    if perde_admin and db.count_admins() <= 1:
        raise HTTPException(400, "Precisa existir pelo menos um administrador ativo.")
    campos = {}
    if dados.perfil:
        campos["perfil"] = dados.perfil
    if dados.ativo is not None:
        campos["ativo"] = int(dados.ativo)
    db.update_user(username, **campos)
    db.auditar(admin["username"], "alterar", username.lower(), json.dumps(campos))
    return db.get_user(username)


@app.delete("/api/admin/usuarios/{username}")
def admin_remover(username: str, admin: dict = Depends(exigir_admin)):
    alvo = db.get_user(username)
    if not alvo:
        raise HTTPException(404, "Usuário não cadastrado.")
    if alvo["perfil"] == "admin" and alvo["ativo"] and db.count_admins() <= 1:
        raise HTTPException(400, "Precisa existir pelo menos um administrador ativo.")
    db.delete_user(username)
    db.auditar(admin["username"], "remover", username.lower())
    return {"ok": True}


@app.get("/api/admin/acessos")
def admin_acessos(admin: dict = Depends(exigir_admin)):
    return {"itens": db.listar_acessos(300)}


# ------------------------------------------------------------------ PWA / arquivos estáticos
@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": f"{settings.APP_NAME} · Grupo Flexível",
        "short_name": settings.APP_SHORT_NAME,
        "description": "Especialista em poliuretano do Grupo Flexível: produtos, engenharias, qualidade, custos e normas.",
        "lang": "pt-BR",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#003D3D",
        "theme_color": "#003D3D",
        "icons": [
            {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icons/icon-maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(FRONT, "sw.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


app.mount("/assets", StaticFiles(directory=os.path.join(FRONT, "assets")), name="assets")
app.mount("/icons", StaticFiles(directory=os.path.join(FRONT, "icons")), name="icons")


@app.get("/{caminho:path}")
def spa(caminho: str):
    if caminho.startswith("api/"):
        raise HTTPException(404, "Não encontrado.")
    return FileResponse(os.path.join(FRONT, "index.html"), headers={"Cache-Control": "no-cache"})
