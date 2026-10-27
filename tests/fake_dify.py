"""Dify falso (só para testes locais): imita a API de chat com streaming de um app Agente."""
import asyncio
import json
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
CONVERSAS: dict[str, dict] = {}
MENSAGENS: dict[str, list] = {}

RESPOSTA = """## FLEXX AG 2032 — visão geral

O **FLEXX AG 2032** é um sistema de espuma flexível convencional. Abaixo, o resumo das últimas inspeções:

| Lote | Data | Densidade (kg/m³) | Resiliência (%) | Resultado |
|---|---|---|---|---|
| 24-1187 | 12/09/2026 | 32,1 | 41 | Aprovado |
| 24-1201 | 19/09/2026 | 31,8 | 40 | Aprovado |
| 24-1233 | 02/10/2026 | 33,4 | 38 | Aprovado com desvio |

### Pontos de atenção
- A resiliência do lote **24-1233** ficou perto do limite inferior (38 %).
- O TDI 80/20 usado veio do fornecedor habitual; nenhuma NC aberta.

> Recomendo acompanhar os próximos 3 lotes antes de alterar a formulação.
"""


def _auth(req: Request):
    return req.headers.get("authorization") == "Bearer app-teste"


@app.get("/v1/parameters")
async def parametros(req: Request):
    return {"opening_statement": "", "suggested_questions": [], "suggested_questions_after_answer": {"enabled": False}}


@app.get("/v1/conversations")
async def conversas(user: str, limit: int = 20, last_id: str | None = None):
    itens = sorted([c for c in CONVERSAS.values() if c["user"] == user], key=lambda c: -c["updated_at"])
    return {"data": [{k: c[k] for k in ("id", "name", "created_at", "updated_at")} for c in itens], "has_more": False}


@app.get("/v1/messages")
async def mensagens(user: str, conversation_id: str, limit: int = 20, first_id: str | None = None):
    if conversation_id not in CONVERSAS or CONVERSAS[conversation_id]["user"] != user:
        return JSONResponse({"code": "not_found", "message": "Conversation Not Exists."}, 404)
    return {"data": MENSAGENS.get(conversation_id, []), "has_more": False}


@app.post("/v1/conversations/{cid}/name")
async def nome(cid: str, req: Request):
    b = await req.json()
    CONVERSAS[cid]["name"] = b.get("name") or "Conversa"
    return {"id": cid, "name": CONVERSAS[cid]["name"]}


@app.delete("/v1/conversations/{cid}")
async def apagar(cid: str):
    CONVERSAS.pop(cid, None)
    return {"result": "success"}


@app.post("/v1/messages/{mid}/feedbacks")
async def feedback(mid: str, req: Request):
    b = await req.json()
    for lst in MENSAGENS.values():
        for m in lst:
            if m["id"] == mid:
                m["feedback"] = {"rating": b.get("rating")} if b.get("rating") else None
    return {"result": "success"}


@app.post("/v1/chat-messages/{task}/stop")
async def parar(task: str):
    return {"result": "success"}


@app.post("/v1/chat-messages")
async def chat(req: Request):
    if not _auth(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    b = await req.json()
    cid = b.get("conversation_id") or str(uuid.uuid4())
    novo = cid not in CONVERSAS
    if novo:
        CONVERSAS[cid] = {"id": cid, "name": "New conversation", "user": b["user"], "created_at": int(time.time()),
                          "updated_at": int(time.time())}
    mid, task = str(uuid.uuid4()), str(uuid.uuid4())
    pergunta = b["query"]

    async def gerar():
        base = {"conversation_id": cid, "message_id": mid, "task_id": task}
        def ev(d):
            return f"data: {json.dumps({**base, **d}, ensure_ascii=False)}\n\n"
        yield ev({"event": "agent_thought", "thought": "", "tool": "qlik_get_chart_data", "position": 1})
        await asyncio.sleep(0.6)
        yield "event: ping\n\n"
        yield ev({"event": "agent_thought", "thought": "", "tool": "buscar_documentos", "position": 2})
        await asyncio.sleep(0.6)
        for i in range(0, len(RESPOSTA), 12):
            yield ev({"event": "agent_message", "answer": RESPOSTA[i:i + 12]})
            await asyncio.sleep(0.015)
        yield ev({"event": "message_end", "id": mid, "metadata": {"retriever_resources": [
            {"document_name": "BT_FLEXX_AG_2032.pdf", "dataset_name": "Boletins técnicos"},
            {"document_name": "NBR 9178 - Espumas flexíveis.pdf", "dataset_name": "Normas"}]}})
        MENSAGENS.setdefault(cid, []).append({"id": mid, "conversation_id": cid, "query": pergunta, "answer": RESPOSTA,
                                              "created_at": int(time.time()), "feedback": None,
                                              "retriever_resources": [{"document_name": "BT_FLEXX_AG_2032.pdf",
                                                                       "dataset_name": "Boletins técnicos"}]})
        CONVERSAS[cid]["updated_at"] = int(time.time())
        if novo:
            CONVERSAS[cid]["name"] = pergunta[:40]

    return StreamingResponse(gerar(), media_type="text/event-stream")
