"""Agentes do Grupo Flexível - frontend dos agentes do Dify (API + arquivos do PWA)."""
import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import conhecimento, db, dify, ldap_auth, tempo
from .config import settings
from .security import (checar_csrf, criar_token, definir_cookie, exigir_admin, limite_login, limpar_cookie,
                       usuario_atual)

log = logging.getLogger("agente")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONT = os.getenv("FRONTEND_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
FRONT = os.path.abspath(FRONT)

# Perguntas de exemplo do Agente Técnico PU, gravadas no agente que vem da versão de agente único
SUGESTOES_PU = [
    {"cat": "Produto", "txt": "Tudo sobre o FLEXX AG 2032"},
    {"cat": "Qualidade", "txt": "Analise a última reclamação de cliente e as inspeções do lote envolvido"},
    {"cat": "Engenharia e custo", "txt": "Formulação e custo do FLEXX TH T190AH1"},
    {"cat": "Compras", "txt": "De quem compramos o TDI 8020 nos últimos 12 meses e a que preço?"},
    {"cat": "Desenvolvimento", "txt": "Já fizemos desenvolvimento com poliol vegetal?"},
]

RX_CHAVE = re.compile(r"app-[A-Za-z0-9]{16,64}")
RX_ID = re.compile(r"[A-Za-z0-9-]{8,64}")       # ids de conversa, mensagem e tarefa do Dify (UUID)
RX_COR = re.compile(r"#[0-9A-Fa-f]{3,8}")


async def _migrar_agente_unico():
    """Até a versão de agente único, o app usava só a DIFY_API_KEY do .env. Na primeira subida desta versão
    essa chave vira o primeiro agente cadastrado e todos os usuários que já existiam continuam com acesso."""
    if db.get_meta("migracao_agentes"):
        return
    chave = settings.DIFY_API_KEY.strip()
    if chave and not db.get_agente_por_chave(chave):
        try:
            dados = await _ler_app_dify(chave)
        except HTTPException as e:
            # Dify fora do ar na subida: cadastra assim mesmo, o admin ajusta o nome depois
            log.warning("migração: não consegui ler o agente no Dify (%s); usando o nome padrão.", e.detail)
            dados = {"nome": settings.APP_NAME, "descricao": "", "modo": "", "icone": "", "cor": ""}
        aid = db.criar_agente(dados["nome"], dados["descricao"], dados["icone"], dados["cor"], dados["modo"], chave,
                              "sistema", SUGESTOES_PU)
        db.liberar_agente_para_todos(aid)
        # total com acesso (os admins já entram na criação do agente)
        n = next(a["qtd_usuarios"] for a in db.list_agentes() if a["id"] == aid)
        db.auditar("sistema", "migrar_agente", str(aid), f"{dados['nome']} liberado para {n} usuário(s)")
        log.info("migração: agente '%s' cadastrado e liberado para %d usuário(s).", dados["nome"], n)
    db.set_meta("migracao_agentes", db.agora())


@asynccontextmanager
async def _ciclo(_app):
    db.init_db()
    await _migrar_agente_unico()
    conhecimento.retomar()  # envios que ficaram no meio por reinício do container
    if not db.list_agentes():
        log.warning("Nenhum agente cadastrado: cadastre em Administração > Agentes.")
    if not os.getenv("SECRET_KEY"):
        log.warning("SECRET_KEY não definida: as sessões caem a cada reinício do container.")
    log.info("Pronto. Admins iniciais: %s", ", ".join(settings.INITIAL_ADMINS) or "(nenhum)")
    yield


app = FastAPI(title=settings.APP_NAME, docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(checar_csrf)], lifespan=_ciclo)
app.include_router(conhecimento.router)


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # microfone só para o próprio site (gravação de perguntas por áudio)
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=()")
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


def _id(valor: str | None, obrigatorio: bool = True) -> str | None:
    """Os ids vão na URL do Dify: só aceita o formato dele (evita montar caminhos inesperados)."""
    if valor is None and not obrigatorio:
        return None
    if not valor or not RX_ID.fullmatch(valor):
        raise HTTPException(400, "Identificador inválido.")
    return valor


async def _ler_app_dify(chave: str) -> dict:
    """Confere a chave no Dify e devolve nome, descrição, tipo e ícone do app."""
    try:
        inf = await dify.info(chave)
    except dify.DifyErro as e:
        if e.status in (401, 403, 404):
            raise HTTPException(400, "O Dify recusou essa chave. Confira se copiou a chave inteira (app-...) "
                                     "e se ela não foi apagada no Dify.")
        raise HTTPException(502, f"O Dify não respondeu como esperado ({e.mensagem}).")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Dify inacessível: %s", e)
        raise HTTPException(502, "Não foi possível falar com o Dify. Tente de novo em instantes.")
    modo = str(inf.get("mode") or "")
    if modo and modo not in dify.MODOS_CHAT:
        raise HTTPException(400, f"Esse app do Dify é do tipo \"{modo}\". Só apps de conversa "
                                 "(Chatbot, Agente ou Chatflow) podem ser usados aqui.")
    icone, cor = "", ""
    try:
        st = await dify.site(chave)
        if (st.get("icon_type") or "emoji") == "emoji":
            icone = str(st.get("icon") or "")[:16]
            cor = str(st.get("icon_background") or "")
    except (dify.DifyErro, httpx.HTTPError, ValueError):
        pass  # sem WebApp publicado: fica sem ícone
    return {"nome": str(inf.get("name") or "Agente").strip()[:120] or "Agente",
            "descricao": str(inf.get("description") or "").strip()[:1000],
            "modo": modo, "icone": icone, "cor": cor if RX_COR.fullmatch(cor) else ""}


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
        db.liberar_todos_agentes(username)
        db.auditar("sistema", "admin_inicial", username)
        user = db.get_user(username)
    if not user:
        db.registrar_acesso(username, ip, "nao_cadastrado")
        raise HTTPException(403, f"Você ainda não tem acesso ao {settings.APP_NAME}. Peça a liberação ao administrador.")
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


# ------------------------------------------------------------------ agentes
def agente_permitido(aid: int, user: dict = Depends(usuario_atual)) -> dict:
    """Confere a cada requisição se o agente está ativo e liberado para a pessoa (tirar o acesso corta na hora)."""
    a = db.get_agente(aid)
    if not a or not a["ativo"] or not db.tem_acesso(user["username"], aid):
        raise HTTPException(403, "Você não tem acesso a este agente.")
    return a


def _agente_publico(a: dict) -> dict:
    return {k: a[k] for k in ("id", "nome", "descricao", "icone", "cor")}


@app.get("/api/agentes")
def meus_agentes(user: dict = Depends(usuario_atual)):
    return {"itens": [_agente_publico(a) for a in db.agentes_do_usuario(user["username"])]}


_cache_params: dict[int, dict] = {}


@app.get("/api/agentes/{aid}/info")
async def agente_info(ag: dict = Depends(agente_permitido)):
    cache = _cache_params.get(ag["id"])
    if not cache or cache["chave"] != ag["chave"] or time.time() - cache["t"] > 300:
        try:
            p = await dify.parametros(ag["chave"])
            cache = {"t": time.time(), "chave": ag["chave"], "v": {
                "abertura": p.get("opening_statement") or "",
                "sugestoes": [{"cat": "", "txt": str(t)} for t in (p.get("suggested_questions") or []) if t],
                "sugestoes_apos_resposta": bool((p.get("suggested_questions_after_answer") or {}).get("enabled")),
                # recurso "Voz para texto" ligado no app: mostra o botão de microfone
                "audio": bool((p.get("speech_to_text") or {}).get("enabled")),
            }}
            _cache_params[ag["id"]] = cache
        except Exception as e:  # noqa: BLE001
            log.warning("parâmetros do Dify indisponíveis (agente %s): %s", ag["id"], e)
            cache = {"v": {"abertura": "", "sugestoes": [], "sugestoes_apos_resposta": False, "audio": False}}
    v = dict(cache["v"])
    # perguntas sugeridas do Dify têm prioridade; sem elas, usa as cadastradas no agente
    if not v["sugestoes"]:
        v["sugestoes"] = ag["sugestoes"]
    return v


@app.get("/api/agentes/{aid}/conversas")
async def listar_conversas(last_id: str | None = None, ag: dict = Depends(agente_permitido),
                           user: dict = Depends(usuario_atual)):
    try:
        r = await dify.conversas(ag["chave"], user["username"], _id(last_id, False))
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


@app.get("/api/agentes/{aid}/conversas/{cid}/mensagens")
async def listar_mensagens(cid: str, first_id: str | None = None, ag: dict = Depends(agente_permitido),
                           user: dict = Depends(usuario_atual)):
    try:
        r = await dify.mensagens(ag["chave"], user["username"], _id(cid), _id(first_id, False))
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


@app.patch("/api/agentes/{aid}/conversas/{cid}")
async def renomear_conversa(cid: str, dados: RenomearIn, ag: dict = Depends(agente_permitido),
                            user: dict = Depends(usuario_atual)):
    try:
        r = await dify.renomear(ag["chave"], user["username"], _id(cid), dados.nome.strip() or None)
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"id": cid, "nome": r.get("name") or dados.nome}


@app.delete("/api/agentes/{aid}/conversas/{cid}")
async def apagar_conversa(cid: str, ag: dict = Depends(agente_permitido), user: dict = Depends(usuario_atual)):
    try:
        await dify.apagar(ag["chave"], user["username"], _id(cid))
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"ok": True}


class FeedbackIn(BaseModel):
    avaliacao: str | None = Field(default=None, pattern="^(like|dislike)$")
    comentario: str = Field(default="", max_length=1000)


@app.post("/api/agentes/{aid}/mensagens/{mid}/feedback")
async def avaliar(mid: str, dados: FeedbackIn, ag: dict = Depends(agente_permitido),
                  user: dict = Depends(usuario_atual)):
    try:
        await dify.feedback(ag["chave"], user["username"], _id(mid), dados.avaliacao, dados.comentario)
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"ok": True}


@app.post("/api/agentes/{aid}/chat/{task_id}/parar")
async def parar(task_id: str, ag: dict = Depends(agente_permitido), user: dict = Depends(usuario_atual)):
    try:
        await dify.parar(ag["chave"], user["username"], _id(task_id))
    except dify.DifyErro as e:
        raise _erro_dify(e)
    return {"ok": True}


class ChatIn(BaseModel):
    pergunta: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


# formatos que o Dify transcreve (o frontend sempre manda WAV; os outros servem para clientes futuros)
TIPOS_AUDIO = {"audio/wav": ("wav", "audio/wav"), "audio/x-wav": ("wav", "audio/wav"), "audio/wave": ("wav", "audio/wav"),
               "audio/mpeg": ("mp3", "audio/mp3"), "audio/mp3": ("mp3", "audio/mp3"),
               "audio/m4a": ("m4a", "audio/m4a"), "audio/x-m4a": ("m4a", "audio/m4a"), "audio/mp4": ("m4a", "audio/m4a")}
_ERROS_AUDIO = {
    "speech_to_text_disabled": (400, "Este agente não está configurado para receber áudio. No Dify, ligue o recurso "
                                     "\"Voz para texto\" (Speech to Text) do app."),
    "provider_not_support_speech_to_text": (502, "O modelo de voz para texto do Dify não está disponível. "
                                                 "Configure um em Configurações > Provedor de modelos."),
    "provider_not_initialize": (502, "O modelo de voz para texto do Dify não está configurado. "
                                     "Configure um em Configurações > Provedor de modelos."),
    "provider_quota_exceeded": (502, "A cota do modelo de voz para texto acabou. Avise a TI."),
    "audio_too_large": (413, "O áudio ficou grande demais. Grave um trecho menor."),
    "unsupported_audio_type": (415, "Formato de áudio não aceito pelo Dify."),
}


@app.post("/api/agentes/{aid}/audio")
async def transcrever_audio(request: Request, ag: dict = Depends(agente_permitido), user: dict = Depends(usuario_atual)):
    """Recebe o áudio gravado no navegador (corpo bruto) e devolve o texto transcrito pelo Dify.
    O áudio não é guardado: vai direto para o Dify e é descartado."""
    tipo = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if tipo not in TIPOS_AUDIO:
        raise HTTPException(415, "Formato de áudio não suportado.")
    limite = settings.AUDIO_MAX_MB * 1024 * 1024
    muito_grande = HTTPException(413, f"O áudio passou de {settings.AUDIO_MAX_MB} MB. Grave um trecho menor.")
    try:
        declarado = int(request.headers.get("content-length") or 0)
    except ValueError:
        declarado = 0
    if declarado > limite:
        raise muito_grande
    dados = bytearray()
    async for parte in request.stream():
        dados += parte
        if len(dados) > limite:
            raise muito_grande
    if len(dados) < 1000:
        raise HTTPException(400, "Áudio vazio ou curto demais.")

    ext, mime = TIPOS_AUDIO[tipo]
    try:
        r = await dify.transcrever(ag["chave"], user["username"], bytes(dados), f"pergunta.{ext}", mime)
    except dify.DifyErro as e:
        log.warning("Dify audio-to-text %s %s: %s", e.status, e.codigo, e.mensagem)
        if e.codigo in _ERROS_AUDIO:
            status, msg = _ERROS_AUDIO[e.codigo]
            raise HTTPException(status, msg)
        raise _erro_dify(e)
    except httpx.HTTPError as e:
        log.warning("Dify inacessível na transcrição: %s", e)
        raise HTTPException(502, "Não foi possível falar com o Dify. Tente de novo em instantes.")
    texto = str(r.get("text") or "").strip()
    if not texto:
        raise HTTPException(422, "Não consegui entender o áudio. Fale mais perto do microfone e tente de novo.")
    db.registrar_uso(user["username"], ag["id"], ag["nome"], "audio", None, f"{len(dados) // 1024} KB")
    return {"texto": texto}


# Nomes amigáveis do que o agente está fazendo (ferramentas dos agentes do Dify)
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


@app.post("/api/agentes/{aid}/chat")
async def chat(dados: ChatIn, ag: dict = Depends(agente_permitido), user: dict = Depends(usuario_atual)):
    username = user["username"]
    chave = ag["chave"]
    conversa = _id(dados.conversation_id, False)

    async def gerar():
        enviado_inicio = False
        ultimo_status = None
        uso = {"conversa": conversa, "erro": "", "fim": False}  # vai para o registro de uso no final
        try:
            async for ev in dify.stream_chat(chave, username, dados.pergunta.strip(), conversa):
                tipo = ev.get("event")
                if not enviado_inicio and ev.get("conversation_id"):
                    enviado_inicio = True
                    uso["conversa"] = ev.get("conversation_id")
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
                    uso["fim"] = True
                    yield _sse({"t": "fim", "conversation_id": ev.get("conversation_id"),
                                "message_id": ev.get("message_id") or ev.get("id"),
                                "fontes": _fontes(ev.get("metadata"))})
                elif tipo == "error":
                    uso["erro"] = ev.get("message") or "Erro no agente."
                    yield _sse({"t": "erro", "v": uso["erro"]})
                elif tipo == "ping":
                    yield ": ping\n\n"
        except dify.DifyErro as e:
            log.warning("Dify %s: %s", e.status, e.mensagem)
            uso["erro"] = e.mensagem
            yield _sse({"t": "erro", "v": f"O agente não respondeu ({e.mensagem})."})
        except Exception as e:  # noqa: BLE001
            log.exception("falha no streaming")
            uso["erro"] = type(e).__name__
            yield _sse({"t": "erro", "v": f"Falha na comunicação com o agente: {type(e).__name__}"})
        finally:  # roda também quando a pessoa para a resposta ou fecha a página
            try:
                db.registrar_uso(username, ag["id"], ag["nome"], "erro" if uso["erro"] else "pergunta", uso["conversa"],
                                 uso["erro"] or ("" if uso["fim"] else "resposta interrompida"))
            except Exception:  # noqa: BLE001 - registro nunca derruba o chat
                log.exception("falha ao registrar uso")

    return StreamingResponse(gerar(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ administração: usuários
class NovoUsuarioIn(BaseModel):
    usuario: str = Field(min_length=1, max_length=120)
    perfil: str = Field(default="usuario", pattern="^(usuario|admin)$")
    # agentes liberados; administradores recebem todos (e podem desmarcar depois)
    agentes: list[int] | None = Field(default=None, max_length=500)


class AlterarUsuarioIn(BaseModel):
    perfil: str | None = Field(default=None, pattern="^(usuario|admin)$")
    ativo: bool | None = None


class AgentesUsuarioIn(BaseModel):
    agentes: list[int] = Field(max_length=500)


def _usuario_admin(u: dict, mapa: dict[str, list[int]] | None = None) -> dict:
    if mapa is None:
        mapa = db.ids_agentes_por_usuario()
    return {**u, "agentes": mapa.get(u["username"], [])}


@app.get("/api/admin/usuarios")
def admin_listar(admin: dict = Depends(exigir_admin)):
    mapa = db.ids_agentes_por_usuario()
    aprova = db.aprovadores_por_usuario()
    return {"itens": [{**_usuario_admin(u, mapa), "aprova": aprova.get(u["username"], [])} for u in db.list_users()]}


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
    if dados.perfil == "admin":
        db.liberar_todos_agentes(ad["username"])
    elif dados.agentes is not None or not existente:
        db.definir_agentes_usuario(ad["username"], dados.agentes or [])
    db.auditar(admin["username"], "reativar" if existente else "incluir", ad["username"],
               json.dumps({"perfil": dados.perfil, "agentes": dados.agentes}))
    return _usuario_admin(db.get_user(ad["username"]))


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
    if dados.perfil == "admin" and alvo["perfil"] != "admin":
        db.liberar_todos_agentes(username)  # quem vira admin enxerga todos os agentes
    db.auditar(admin["username"], "alterar", username.lower(), json.dumps(campos))
    return _usuario_admin(db.get_user(username))


@app.put("/api/admin/usuarios/{username}/agentes")
def admin_agentes_usuario(username: str, dados: AgentesUsuarioIn, admin: dict = Depends(exigir_admin)):
    if not db.get_user(username):
        raise HTTPException(404, "Usuário não cadastrado.")
    ids = db.definir_agentes_usuario(username, dados.agentes)
    db.auditar(admin["username"], "agentes_usuario", username.lower(), json.dumps(ids))
    return _usuario_admin(db.get_user(username))


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


# ------------------------------------------------------------------ administração: registros e conversas
RX_USUARIO = re.compile(r"[a-z0-9._\-]{1,64}")
_sem_dify = asyncio.Semaphore(6)  # no máximo 6 chamadas simultâneas ao Dify ao varrer conversas


_periodo = tempo.periodo
_iso_utc = tempo.iso_utc


def _usuario_param(usuario: str | None) -> str | None:
    if not usuario:
        return None
    u = ldap_auth.normalizar_usuario(usuario)
    if not RX_USUARIO.fullmatch(u):
        raise HTTPException(400, "Usuário inválido.")
    return u


@app.get("/api/admin/registros")
def admin_registros(usuario: str | None = None, agente: int | None = None, inicio: str | None = None,
                    fim: str | None = None, tipo: str = "todos", admin: dict = Depends(exigir_admin)):
    if tipo not in ("todos", "acessos", "uso"):
        raise HTTPException(400, "Tipo inválido.")
    ini, f = _periodo(inicio, fim)
    itens, cortado = db.listar_registros(_usuario_param(usuario), agente, _iso_utc(ini), _iso_utc(f), tipo, 1000)
    return {"itens": itens, "cortado": cortado}


async def _conversas_de(ag: dict, username: str, ini: float | None, fim: float | None) -> list[dict]:
    """Todas as conversas de uma pessoa num agente (o Dify só lista por pessoa), dentro do período."""
    itens, ultimo = [], None
    for _ in range(100):  # até 10 mil conversas por pessoa/agente
        async with _sem_dify:
            r = await dify.conversas(ag["chave"], username, ultimo, 100)
        dados = r.get("data") or []
        for c in dados:
            criado = c.get("created_at") or 0
            atualizado = c.get("updated_at") or criado
            if ini and atualizado < ini:
                return itens  # a lista vem da mais recente para a mais antiga: daqui para frente é tudo antes
            if fim and criado > fim:
                continue
            itens.append({"id": c["id"], "titulo": c.get("name") or "Nova conversa", "criado": criado,
                          "atualizado": atualizado})
        if not r.get("has_more") or not dados:
            break
        ultimo = dados[-1]["id"]
    return itens


async def _listar_conversas(usuario: str | None, agente_id: int | None, inicio: str | None,
                            fim: str | None) -> tuple[list[dict], list[str]]:
    ini, f = _periodo(inicio, fim)
    agentes = [a for a in db.list_agentes() if not agente_id or a["id"] == agente_id]
    if agente_id and not agentes:
        raise HTTPException(404, "Agente não encontrado.")
    pessoas = [usuario] if usuario else db.usuarios_conhecidos()
    nomes = {u["username"]: u["nome"] or u["username"] for u in db.list_users()}
    pares = [(a, p) for a in agentes for p in pessoas]
    resultados = await asyncio.gather(
        *[_conversas_de(a, p, ini.timestamp() if ini else None, f.timestamp() if f else None) for a, p in pares],
        return_exceptions=True)
    itens, avisos = [], set()
    for (a, p), r in zip(pares, resultados):
        if isinstance(r, BaseException):
            motivo = r.mensagem if isinstance(r, dify.DifyErro) else type(r).__name__
            avisos.add(f"Não consegui ler as conversas do agente \"{a['nome']}\" no Dify ({motivo}).")
            continue
        for c in r:
            itens.append({**c, "agente_id": a["id"], "agente": a["nome"], "username": p, "usuario": nomes.get(p, p)})
    itens.sort(key=lambda c: -(c["atualizado"] or 0))
    return itens, sorted(avisos)


@app.get("/api/admin/conversas")
async def admin_conversas(usuario: str | None = None, agente: int | None = None, inicio: str | None = None,
                          fim: str | None = None, admin: dict = Depends(exigir_admin)):
    itens, avisos = await _listar_conversas(_usuario_param(usuario), agente, inicio, fim)
    return {"itens": itens, "avisos": avisos}


async def _mensagens_completas(ag: dict, username: str, cid: str) -> list[dict]:
    """Histórico inteiro da conversa (o Dify entrega de 100 em 100, das mais novas para as mais antigas)."""
    todas, primeira = {}, None
    for _ in range(200):
        async with _sem_dify:
            r = await dify.mensagens(ag["chave"], username, cid, primeira, 100)
        dados = r.get("data") or []
        for m in dados:
            todas[m["id"]] = m
        if not r.get("has_more") or not dados:
            break
        primeira = min(dados, key=lambda m: m.get("created_at") or 0)["id"]
    out = []
    for m in sorted(todas.values(), key=lambda m: m.get("created_at") or 0):
        out.append({"id": m["id"], "pergunta": m.get("query") or "", "resposta": m.get("answer") or "",
                    "criado": m.get("created_at"), "avaliacao": (m.get("feedback") or {}).get("rating"),
                    "fontes": _fontes({"retriever_resources": m.get("retriever_resources")})})
    return out


@app.get("/api/admin/conversas/{agente_id}/{username}/{cid}")
async def admin_ver_conversa(agente_id: int, username: str, cid: str, admin: dict = Depends(exigir_admin)):
    ag = _agente_ou_404(agente_id)
    u = _usuario_param(username)
    try:
        mensagens = await _mensagens_completas(ag, u, _id(cid))
    except dify.DifyErro as e:
        raise _erro_dify(e)
    db.auditar(admin["username"], "ver_conversa", u, f"{ag['nome']} · {cid}")
    return {"mensagens": mensagens}


class ConversaRef(BaseModel):
    agente_id: int
    username: str = Field(min_length=1, max_length=64)
    conversation_id: str = Field(min_length=8, max_length=64)
    titulo: str = Field(default="", max_length=200)
    criado: float | None = None
    atualizado: float | None = None


class FiltroConversas(BaseModel):
    usuario: str | None = Field(default=None, max_length=120)
    agente: int | None = None
    inicio: str | None = Field(default=None, max_length=10)
    fim: str | None = Field(default=None, max_length=10)


class ExportarIn(BaseModel):
    # ou a lista escolhida na tela, ou "todas as conversas deste filtro"
    itens: list[ConversaRef] | None = Field(default=None, max_length=5000)
    filtro: FiltroConversas | None = None


_hora = tempo.hora


def _txt_conversa(meta: dict, mensagens: list[dict]) -> str:
    linha, fina = "=" * 72, "-" * 72
    nome_usuario = meta["usuario"] if meta["usuario"] == meta["username"] else f"{meta['usuario']} ({meta['username']})"
    partes = [linha, f"Conversa: {meta['titulo']}", f"Usuário:  {nome_usuario}", f"Agente:   {meta['agente']}",
              f"Período:  {_hora(meta.get('criado'))} até {_hora(meta.get('atualizado'))}",
              f"Mensagens: {len(mensagens)}   ·   ID: {meta['conversation_id']}", linha, ""]
    if not mensagens:
        partes += ["(conversa sem mensagens)", ""]
    for m in mensagens:
        partes += [f"[{_hora(m['criado'])}] {meta['usuario']}:", m["pergunta"].strip(), "",
                   f"[{_hora(m['criado'])}] {meta['agente']}:", m["resposta"].strip() or "(sem resposta)"]
        if m["fontes"]:
            partes.append("Fontes: " + "; ".join(f["documento"] for f in m["fontes"]))
        if m["avaliacao"]:
            partes.append("Avaliação: " + ("útil (👍)" if m["avaliacao"] == "like" else "ruim (👎)"))
        partes += ["", fina, ""]
    return "\r\n".join(partes) + "\r\n"


@app.post("/api/admin/conversas/exportar")
async def admin_exportar(dados: ExportarIn, admin: dict = Depends(exigir_admin)):
    nomes = {u["username"]: u["nome"] or u["username"] for u in db.list_users()}
    agentes = {a["id"]: a for a in db.list_agentes()}
    if dados.itens is not None:
        refs = []
        for i in dados.itens:
            a = agentes.get(i.agente_id)
            if not a:
                raise HTTPException(404, "Agente não encontrado.")
            u = _usuario_param(i.username)
            refs.append({"agente_id": a["id"], "agente": a["nome"], "username": u, "usuario": nomes.get(u, u),
                         "conversation_id": _id(i.conversation_id), "titulo": i.titulo or "Conversa",
                         "criado": i.criado, "atualizado": i.atualizado})
        descricao = f"{len(refs)} selecionada(s)"
    elif dados.filtro is not None:
        f = dados.filtro
        itens, _avisos = await _listar_conversas(_usuario_param(f.usuario), f.agente, f.inicio, f.fim)
        refs = [{**c, "conversation_id": c["id"]} for c in itens]
        descricao = "todas do filtro " + json.dumps(f.model_dump(exclude_none=True), ensure_ascii=False)
    else:
        raise HTTPException(400, "Informe as conversas ou o filtro.")
    if not refs:
        raise HTTPException(404, "Nenhuma conversa para exportar.")
    if len(refs) > settings.EXPORTAR_MAX_CONVERSAS:
        raise HTTPException(400, f"São {len(refs)} conversas; o limite por arquivo é {settings.EXPORTAR_MAX_CONVERSAS}. "
                                 "Use um período menor ou filtre por usuário/agente.")

    async def uma(ref):
        try:
            return _txt_conversa(ref, await _mensagens_completas(agentes[ref["agente_id"]], ref["username"],
                                                                 ref["conversation_id"]))
        except dify.DifyErro as e:
            return _txt_conversa(ref, []) + f"(não foi possível ler esta conversa no Dify: {e.mensagem})\r\n"

    blocos = await asyncio.gather(*[uma(r) for r in refs])
    agora = datetime.now(tempo.FUSO)
    cabecalho = (f"Exportação de conversas · {settings.APP_NAME}\r\nGerada em {agora:%d/%m/%Y %H:%M} por "
                 f"{admin['username']} · {len(refs)} conversa(s)\r\n\r\n")
    db.auditar(admin["username"], "exportar_conversas", "", f"{len(refs)} conversa(s): {descricao}")
    nome = f"conversas-{agora:%Y%m%d-%H%M}.txt"
    return Response("﻿" + cabecalho + "\r\n".join(blocos), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


# ------------------------------------------------------------------ administração: agentes
class SugestaoIn(BaseModel):
    cat: str = Field(default="", max_length=40)
    txt: str = Field(min_length=1, max_length=300)


class NovoAgenteIn(BaseModel):
    chave: str = Field(min_length=1, max_length=200)
    nome: str = Field(default="", max_length=120)


class AlterarAgenteIn(BaseModel):
    nome: str | None = Field(default=None, max_length=120)
    descricao: str | None = Field(default=None, max_length=1000)
    sugestoes: list[SugestaoIn] | None = Field(default=None, max_length=12)
    ativo: bool | None = None
    chave: str | None = Field(default=None, max_length=200)
    recebe_conhecimento: bool | None = None   # usuários podem enviar arquivos (com aprovação) para este agente
    autoaprovacao: bool | None = None         # aprovador pode aprovar o próprio envio


def _mascarar(chave: str) -> str:
    return f"{chave[:7]}…{chave[-4:]}" if len(chave) > 12 else "app-…"


def _agente_admin(a: dict) -> dict:
    out = {k: v for k, v in a.items() if k != "chave"}
    out["chave_mascarada"] = _mascarar(a["chave"])
    out["aprovadores"] = db.aprovadores_do_agente(a["id"])
    return out


def _validar_chave(chave: str, agente_id: int | None = None) -> str:
    chave = chave.strip()
    if not RX_CHAVE.fullmatch(chave):
        raise HTTPException(400, "Chave inválida. No Dify, abra o agente > Acesso à API > Chave da API e copie a "
                                 "chave inteira (começa com app-).")
    dono = db.get_agente_por_chave(chave)
    if dono and dono["id"] != agente_id:
        raise HTTPException(409, f"Essa chave já está cadastrada no agente \"{dono['nome']}\".")
    return chave


def _agente_ou_404(agente_id: int) -> dict:
    a = db.get_agente(agente_id)
    if not a:
        raise HTTPException(404, "Agente não encontrado.")
    return a


def _agente_com_qtd(agente_id: int) -> dict:
    return _agente_admin(next(a for a in db.list_agentes() if a["id"] == agente_id))


@app.get("/api/admin/agentes")
def admin_listar_agentes(admin: dict = Depends(exigir_admin)):
    return {"itens": [_agente_admin(a) for a in db.list_agentes()]}


@app.post("/api/admin/agentes")
async def admin_incluir_agente(dados: NovoAgenteIn, admin: dict = Depends(exigir_admin)):
    chave = _validar_chave(dados.chave)
    app_dify = await _ler_app_dify(chave)
    nome = dados.nome.strip() or app_dify["nome"]
    aid = db.criar_agente(nome, app_dify["descricao"], app_dify["icone"], app_dify["cor"], app_dify["modo"], chave,
                          admin["username"])
    db.auditar(admin["username"], "incluir_agente", str(aid), nome)
    return _agente_com_qtd(aid)


@app.patch("/api/admin/agentes/{agente_id}")
async def admin_alterar_agente(agente_id: int, dados: AlterarAgenteIn, admin: dict = Depends(exigir_admin)):
    atual = _agente_ou_404(agente_id)
    campos: dict = {}
    if dados.chave is not None and dados.chave.strip() and dados.chave.strip() != atual["chave"]:
        chave = _validar_chave(dados.chave, agente_id)
        app_dify = await _ler_app_dify(chave)
        campos.update(chave=chave, modo=app_dify["modo"], icone=app_dify["icone"], cor=app_dify["cor"])
    if dados.nome is not None:
        if not dados.nome.strip():
            raise HTTPException(400, "Informe o nome do agente.")
        campos["nome"] = dados.nome.strip()
    if dados.descricao is not None:
        campos["descricao"] = dados.descricao.strip()
    if dados.sugestoes is not None:
        campos["sugestoes"] = [{"cat": s.cat.strip(), "txt": s.txt.strip()} for s in dados.sugestoes if s.txt.strip()]
    if dados.ativo is not None:
        campos["ativo"] = dados.ativo
    if dados.autoaprovacao is not None:
        campos["autoaprovacao"] = dados.autoaprovacao
    if dados.recebe_conhecimento is not None:
        if dados.recebe_conhecimento and not atual.get("dataset_id"):
            campos["dataset_id"] = await conhecimento.preparar_agente(atual)  # cria a base colaborativa no Dify
        campos["recebe_conhecimento"] = dados.recebe_conhecimento
    db.update_agente(agente_id, **campos)
    _cache_params.pop(agente_id, None)
    resumo = {k: ("(nova chave)" if k == "chave" else v) for k, v in campos.items() if k not in ("icone", "cor", "modo")}
    db.auditar(admin["username"], "alterar_agente", str(agente_id), json.dumps(resumo, ensure_ascii=False))
    return _agente_com_qtd(agente_id)


@app.delete("/api/admin/agentes/{agente_id}")
def admin_remover_agente(agente_id: int, admin: dict = Depends(exigir_admin)):
    a = _agente_ou_404(agente_id)
    db.delete_agente(agente_id)
    _cache_params.pop(agente_id, None)
    db.auditar(admin["username"], "remover_agente", str(agente_id), a["nome"])
    return {"ok": True}


# ------------------------------------------------------------------ PWA / arquivos estáticos
@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": f"{settings.APP_NAME} · Grupo Flexível",
        "short_name": settings.APP_SHORT_NAME,
        "description": "Agentes de IA do Grupo Flexível: produtos, engenharias, qualidade, custos e normas.",
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
