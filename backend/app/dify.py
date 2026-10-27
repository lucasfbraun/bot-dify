"""Cliente da API do Dify (app de chat do Agente Técnico PU).

A chave do app (app-...) fica só no servidor; o navegador nunca a vê.
Cada pessoa é identificada no Dify como "ad:<login>", então cada um vê só as próprias conversas.
"""
import json

import httpx

from .config import settings


def ident(username: str) -> str:
    return f"ad:{username}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.DIFY_API_KEY}"}


def _client(timeout: float | None = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.DIFY_API_URL, headers=_headers(), timeout=timeout,
                             verify=settings.DIFY_VERIFY_TLS)


class DifyErro(Exception):
    def __init__(self, status: int, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def _erro(r: httpx.Response) -> DifyErro:
    try:
        corpo = r.json()
        msg = corpo.get("message") or corpo.get("code") or r.text
    except ValueError:
        msg = r.text
    return DifyErro(r.status_code, msg[:300])


async def _get(path: str, params: dict) -> dict:
    async with _client() as c:
        r = await c.get(path, params=params)
    if r.status_code >= 400:
        raise _erro(r)
    return r.json()


async def _send(method: str, path: str, body: dict) -> dict:
    async with _client() as c:
        r = await c.request(method, path, json=body)
    if r.status_code >= 400:
        raise _erro(r)
    return r.json() if r.content else {}


async def parametros() -> dict:
    """Mensagem de abertura e perguntas sugeridas configuradas no app do Dify."""
    return await _get("/parameters", {})


async def conversas(username: str, last_id: str | None = None, limite: int = 50) -> dict:
    p = {"user": ident(username), "limit": limite, "sort_by": "-updated_at"}
    if last_id:
        p["last_id"] = last_id
    return await _get("/conversations", p)


async def mensagens(username: str, conversation_id: str, first_id: str | None = None, limite: int = 50) -> dict:
    p = {"user": ident(username), "conversation_id": conversation_id, "limit": limite}
    if first_id:
        p["first_id"] = first_id
    return await _get("/messages", p)


async def renomear(username: str, conversation_id: str, nome: str | None) -> dict:
    body = {"user": ident(username), "auto_generate": not nome}
    if nome:
        body["name"] = nome
    return await _send("POST", f"/conversations/{conversation_id}/name", body)


async def apagar(username: str, conversation_id: str) -> dict:
    return await _send("DELETE", f"/conversations/{conversation_id}", {"user": ident(username)})


async def feedback(username: str, message_id: str, rating: str | None, conteudo: str = "") -> dict:
    body = {"user": ident(username), "rating": rating}
    if conteudo:
        body["content"] = conteudo
    return await _send("POST", f"/messages/{message_id}/feedbacks", body)


async def parar(username: str, task_id: str) -> dict:
    return await _send("POST", f"/chat-messages/{task_id}/stop", {"user": ident(username)})


async def stream_chat(username: str, pergunta: str, conversation_id: str | None):
    """Repassa o streaming (SSE) do Dify linha a linha, já como eventos JSON."""
    body = {"inputs": {}, "query": pergunta, "response_mode": "streaming", "user": ident(username),
            "auto_generate_name": True}
    if conversation_id:
        body["conversation_id"] = conversation_id
    timeout = httpx.Timeout(settings.DIFY_TIMEOUT, connect=15)
    async with _client(timeout) as c:
        async with c.stream("POST", "/chat-messages", json=body) as r:
            if r.status_code >= 400:
                await r.aread()
                raise _erro(r)
            async for linha in r.aiter_lines():
                if not linha.startswith("data:"):
                    continue
                dado = linha[5:].strip()
                if not dado:
                    continue
                try:
                    yield json.loads(dado)
                except ValueError:
                    continue
