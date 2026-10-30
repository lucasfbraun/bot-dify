"""Cliente da API do Dify. Cada agente é um app de chat do Dify com a própria chave (app-...).

As chaves ficam só no servidor; o navegador nunca as vê.
Cada pessoa é identificada no Dify como "ad:<login>", então cada um vê só as próprias conversas.
No Dify as conversas pertencem ao app, então cada agente tem o seu próprio histórico.
"""
import asyncio
import json

import httpx

from .config import settings

# só estes tipos de app conversam pela API /chat-messages
MODOS_CHAT = {"chat", "agent-chat", "advanced-chat"}


def ident(username: str) -> str:
    return f"ad:{username}"


def _client(chave: str, timeout: float | httpx.Timeout | None = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.DIFY_API_URL, headers={"Authorization": f"Bearer {chave}"},
                             timeout=timeout, verify=settings.DIFY_VERIFY_TLS)


_compartilhado: dict = {"loop": None, "cliente": None}


def _cliente_compartilhado() -> httpx.AsyncClient:
    """Um cliente só, com conexões reaproveitadas (abrir TLS a cada chamada custa caro ao varrer
    centenas de conversas). Recriado se o event loop mudar (acontece nos testes)."""
    loop = asyncio.get_running_loop()
    if _compartilhado["loop"] is not loop or _compartilhado["cliente"] is None:
        _compartilhado.update(loop=loop, cliente=httpx.AsyncClient(
            base_url=settings.DIFY_API_URL, timeout=30, verify=settings.DIFY_VERIFY_TLS,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)))
    return _compartilhado["cliente"]


def _auth(chave: str) -> dict:
    return {"Authorization": f"Bearer {chave}"}


class DifyErro(Exception):
    def __init__(self, status: int, mensagem: str, codigo: str = ""):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem
        self.codigo = codigo  # ex.: speech_to_text_disabled


def _erro(r: httpx.Response) -> DifyErro:
    codigo = ""
    try:
        corpo = r.json()
        codigo = str(corpo.get("code") or "")
        msg = corpo.get("message") or codigo or r.text
    except ValueError:
        msg = r.text
    return DifyErro(r.status_code, str(msg)[:300], codigo)


async def _get(chave: str, path: str, params: dict) -> dict:
    r = await _cliente_compartilhado().get(path, params=params, headers=_auth(chave))
    if r.status_code >= 400:
        raise _erro(r)
    return r.json()


async def _send(chave: str, method: str, path: str, body: dict) -> dict:
    r = await _cliente_compartilhado().request(method, path, json=body, headers=_auth(chave))
    if r.status_code >= 400:
        raise _erro(r)
    return r.json() if r.content else {}


async def info(chave: str) -> dict:
    """Nome, descrição e tipo (mode) do app dono da chave."""
    return await _get(chave, "/info", {})


async def site(chave: str) -> dict:
    """Configuração do WebApp: traz o ícone (emoji) e a cor de fundo."""
    return await _get(chave, "/site", {})


async def parametros(chave: str) -> dict:
    """Mensagem de abertura e perguntas sugeridas configuradas no app do Dify."""
    return await _get(chave, "/parameters", {})


async def conversas(chave: str, username: str, last_id: str | None = None, limite: int = 50) -> dict:
    p = {"user": ident(username), "limit": limite, "sort_by": "-updated_at"}
    if last_id:
        p["last_id"] = last_id
    return await _get(chave, "/conversations", p)


async def mensagens(chave: str, username: str, conversation_id: str, first_id: str | None = None,
                    limite: int = 50) -> dict:
    p = {"user": ident(username), "conversation_id": conversation_id, "limit": limite}
    if first_id:
        p["first_id"] = first_id
    return await _get(chave, "/messages", p)


async def renomear(chave: str, username: str, conversation_id: str, nome: str | None) -> dict:
    body = {"user": ident(username), "auto_generate": not nome}
    if nome:
        body["name"] = nome
    return await _send(chave, "POST", f"/conversations/{conversation_id}/name", body)


async def apagar(chave: str, username: str, conversation_id: str) -> dict:
    return await _send(chave, "DELETE", f"/conversations/{conversation_id}", {"user": ident(username)})


async def feedback(chave: str, username: str, message_id: str, rating: str | None, conteudo: str = "") -> dict:
    body = {"user": ident(username), "rating": rating}
    if conteudo:
        body["content"] = conteudo
    return await _send(chave, "POST", f"/messages/{message_id}/feedbacks", body)


async def parar(chave: str, username: str, task_id: str) -> dict:
    return await _send(chave, "POST", f"/chat-messages/{task_id}/stop", {"user": ident(username)})


async def transcrever(chave: str, username: str, dados: bytes, nome: str, mime: str) -> dict:
    """Voz para texto com o modelo configurado no app do Dify (recurso "Speech to Text" ligado)."""
    async with _client(chave, httpx.Timeout(120, connect=15)) as c:
        r = await c.post("/audio-to-text", files={"file": (nome, dados, mime)}, data={"user": ident(username)})
    if r.status_code >= 400:
        raise _erro(r)
    return r.json()


async def enviar_arquivo(chave: str, username: str, dados: bytes, nome: str, mime: str) -> dict:
    """Anexo do chat (recurso "Upload de Arquivo" ligado). Devolve o id para mandar junto com a pergunta."""
    async with _client(chave, httpx.Timeout(120, connect=15)) as c:
        r = await c.post("/files/upload", files={"file": (nome, dados, mime)}, data={"user": ident(username)})
    if r.status_code >= 400:
        raise _erro(r)
    return r.json()


async def stream_chat(chave: str, username: str, pergunta: str, conversation_id: str | None,
                      arquivos: list[dict] | None = None):
    """Repassa o streaming (SSE) do Dify linha a linha, já como eventos JSON.
    arquivos: [{"tipo": "image"|"document", "id": <id do /files/upload>}]"""
    body = {"inputs": {}, "query": pergunta, "response_mode": "streaming", "user": ident(username),
            "auto_generate_name": True}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if arquivos:
        body["files"] = [{"type": a["tipo"], "transfer_method": "local_file", "upload_file_id": a["id"]}
                         for a in arquivos]
    timeout = httpx.Timeout(settings.DIFY_TIMEOUT, connect=15)
    async with _client(chave, timeout) as c:
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


# ------------------------------------------------------------------ bases de conhecimento (chave ds-...)
def chave_conhecimento() -> tuple[str, str]:
    """(chave, origem): a cadastrada na tela de administração tem prioridade sobre a do .env."""
    from . import db  # import tardio: db não depende deste módulo
    tela = db.get_meta("dify_dataset_key") or ""
    if tela:
        return tela, "tela"
    return (settings.DIFY_DATASET_API_KEY, ".env") if settings.DIFY_DATASET_API_KEY else ("", "")


def _ds() -> str:
    return chave_conhecimento()[0]


async def testar_chave_conhecimento(chave: str) -> None:
    """Confere se a chave funciona e se pode listar/criar bases (chaves restritas a bases específicas não podem)."""
    await _get(chave, "/datasets", {"page": 1, "limit": 1})


async def base_criar(nome: str, descricao: str, tecnica: str) -> dict:
    """Cria uma base de conhecimento. tecnica: high_quality (embeddings) ou economy (sem custo de IA)."""
    return await _send(_ds(), "POST", "/datasets", {"name": nome[:40], "description": descricao[:400],
                                                    "indexing_technique": tecnica, "permission": "only_me"})


async def base_info(dataset_id: str, chave: str | None = None) -> dict:
    return await _get(chave or _ds(), f"/datasets/{dataset_id}", {})


async def documento_enviar(dataset_id: str, nome: str, dados: bytes, mime: str, tecnica: str | None = None) -> dict:
    """Envia um arquivo para a base; o Dify processa em segundo plano (acompanhar pelo batch)."""
    config = {"process_rule": {"mode": "automatic"}, "doc_form": "text_model", "doc_language": "Portuguese"}
    if tecnica:
        config["indexing_technique"] = tecnica
    r = await _cliente_compartilhado().post(
        f"/datasets/{dataset_id}/document/create-by-file", headers=_auth(_ds()), timeout=120,
        files={"file": (nome, dados, mime)}, data={"data": json.dumps(config)})
    if r.status_code >= 400:
        raise _erro(r)
    return r.json()


async def documento_status_processamento(dataset_id: str, batch: str) -> dict:
    r = await _get(_ds(), f"/datasets/{dataset_id}/documents/{batch}/indexing-status", {})
    dados = r.get("data") or [{}]
    return dados[0]


async def documento_trechos(dataset_id: str, documento_id: str, pagina: int = 1, limite: int = 100) -> dict:
    return await _get(_ds(), f"/datasets/{dataset_id}/documents/{documento_id}/segments",
                      {"page": pagina, "limit": limite})


async def documento_apagar(dataset_id: str, documento_id: str) -> None:
    r = await _cliente_compartilhado().delete(f"/datasets/{dataset_id}/documents/{documento_id}", headers=_auth(_ds()))
    if r.status_code >= 400 and r.status_code != 404:  # já apagado no Dify: segue
        raise _erro(r)


async def documento_mudar_status(dataset_id: str, documento_id: str, acao: str) -> None:
    """acao: enable ou disable (o documento desativado não é consultado pelos agentes)."""
    await _send(_ds(), "PATCH", f"/datasets/{dataset_id}/documents/status/{acao}", {"document_ids": [documento_id]})


async def documento_baixar(dataset_id: str, documento_id: str) -> tuple[bytes, str]:
    """O Dify devolve um link assinado do arquivo; o servidor baixa e repassa (o navegador não fala com o Dify)."""
    r = await _get(_ds(), f"/datasets/{dataset_id}/documents/{documento_id}/download", {})
    arq = await _cliente_compartilhado().get(r["url"], timeout=120, follow_redirects=True)
    if arq.status_code >= 400:
        raise DifyErro(arq.status_code, "Não foi possível baixar o arquivo do Dify.")
    return arq.content, arq.headers.get("content-type", "application/octet-stream")


async def bases_listar() -> list[dict]:
    """Todas as bases de conhecimento do workspace (include_all: também as criadas com permissão "só eu")."""
    bases, pagina = [], 1
    while pagina <= 50:
        r = await _get(_ds(), "/datasets", {"page": pagina, "limit": 100, "include_all": "true"})
        dados = r.get("data") or []
        bases += dados
        if not r.get("has_more") or not dados:
            break
        pagina += 1
    return bases
