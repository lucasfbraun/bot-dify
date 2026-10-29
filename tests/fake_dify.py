"""Dify falso (só para testes locais): imita a API de vários apps de chat, cada um com a sua chave.

Como no Dify de verdade, as conversas pertencem ao app: a chave de um app não enxerga as conversas de outro.
"""
import asyncio
import io
import json
import time
import uuid
import wave

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

CHAVE_PU = "app-PUqlik0000000000000000"
CHAVE_COMERCIAL = "app-Comercial0000000000000"
CHAVE_WORKFLOW = "app-Fluxo00000000000000000"

APPS = {
    CHAVE_PU: {"name": "Agente Técnico PU(qlik)", "description": "Especialista em poliuretano com acesso ao Qlik.",
               "mode": "agent-chat", "icon": "🧪", "icon_background": "#E4FBCC", "ferramentas": True,
               "voz_para_texto": True},
    CHAVE_COMERCIAL: {"name": "Agente Comercial", "description": "Tira dúvidas de catálogo, sem acesso ao ERP.",
                      "mode": "advanced-chat", "icon": "💼", "icon_background": "#D1E9FF", "ferramentas": False,
                      "sugestoes": ["Quais espumas temos para colchões?"]},
    CHAVE_WORKFLOW: {"name": "Fluxo de relatórios", "description": "", "mode": "workflow", "icon": "⚙️",
                     "icon_background": "#FFEAD5", "ferramentas": False},
}
CONVERSAS: dict[str, dict] = {}
MENSAGENS: dict[str, list] = {}
ULTIMO_AUDIO: dict = {}

RESPOSTA_PU = """## FLEXX AG 2032 — visão geral

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

RESPOSTA_COMERCIAL = """Sou o **Agente Comercial**. Para colchões, o catálogo tem as linhas *FLEXX Conforto* e *FLEXX HR*.

Não tenho acesso ao ERP, então não consigo informar estoque ou custo."""


def _app(req: Request):
    """Devolve (chave, app) pelo cabeçalho Authorization, ou (None, None) se a chave não existe."""
    auth = req.headers.get("authorization", "")
    chave = auth[7:] if auth.startswith("Bearer ") else ""
    return (chave, APPS[chave]) if chave in APPS else (None, None)


def _negado():
    return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)


def _conversa_do(chave: str, user: str, cid: str):
    c = CONVERSAS.get(cid)
    return c if c and c["app"] == chave and c["user"] == user else None


@app.get("/v1/info")
async def info(req: Request):
    chave, a = _app(req)
    if not a:
        return _negado()
    return {"name": a["name"], "description": a["description"], "tags": [], "mode": a["mode"], "author_name": "TI"}


@app.get("/v1/site")
async def site(req: Request):
    chave, a = _app(req)
    if not a:
        return _negado()
    return {"title": a["name"], "icon_type": "emoji", "icon": a["icon"], "icon_background": a["icon_background"],
            "description": a["description"], "chat_color_theme_inverted": False, "default_language": "pt-BR",
            "show_workflow_steps": False, "use_icon_as_answer_icon": False}


@app.get("/v1/parameters")
async def parametros(req: Request):
    chave, a = _app(req)
    if not a:
        return _negado()
    return {"opening_statement": "", "suggested_questions": a.get("sugestoes", []),
            "suggested_questions_after_answer": {"enabled": False},
            "speech_to_text": {"enabled": a.get("voz_para_texto", False)}}


@app.post("/v1/audio-to-text")
async def audio_para_texto(req: Request):
    """Imita o Dify: só aceita os tipos dele e só com "Voz para texto" ligado. Lê o WAV para o teste
    conferir a conversão feita no navegador (taxa, canais e duração)."""
    chave, a = _app(req)
    if not a:
        return _negado()
    if not a.get("voz_para_texto"):
        return JSONResponse({"code": "speech_to_text_disabled", "message": "Speech to text is disabled.", "status": 400}, 400)
    form = await req.form()
    arquivo = form.get("file")
    if not form.get("user") or arquivo is None:
        return JSONResponse({"code": "no_audio_uploaded", "message": "Please upload your audio."}, 400)
    if arquivo.content_type not in ("audio/wav", "audio/mp3", "audio/m4a", "audio/mpga", "audio/amr"):
        return JSONResponse({"code": "unsupported_audio_type", "message": "Audio type not allowed."}, 415)
    dados = await arquivo.read()
    ULTIMO_AUDIO.update(tamanho=len(dados), tipo=arquivo.content_type, nome=arquivo.filename, user=form.get("user"))
    if arquivo.content_type != "audio/wav":
        return {"text": "Áudio recebido."}
    try:
        with wave.open(io.BytesIO(dados)) as w:
            taxa, canais, quadros = w.getframerate(), w.getnchannels(), w.getnframes()
            amostras = w.readframes(quadros)
    except (wave.Error, EOFError):
        return JSONResponse({"code": "unsupported_audio_type", "message": "Audio type not allowed."}, 415)
    if not any(amostras):  # silêncio total: o modelo não reconhece fala
        return {"text": ""}
    return {"text": f"Qual o custo do FLEXX AG 2032? [áudio {quadros / taxa:.1f} s, {taxa} Hz, {canais} canal]"}


@app.get("/v1/conversations")
async def conversas(req: Request, user: str, limit: int = 20, last_id: str | None = None):
    chave, a = _app(req)
    if not a:
        return _negado()
    itens = sorted([c for c in CONVERSAS.values() if c["user"] == user and c["app"] == chave],
                   key=lambda c: -c["updated_at"])
    if last_id:  # paginação como no Dify: continua depois da última conversa da página anterior
        ids = [c["id"] for c in itens]
        itens = itens[ids.index(last_id) + 1:] if last_id in ids else []
    pagina = itens[:limit]
    return {"data": [{k: c[k] for k in ("id", "name", "created_at", "updated_at")} for c in pagina],
            "has_more": len(itens) > limit}


@app.get("/v1/messages")
async def mensagens(req: Request, user: str, conversation_id: str, limit: int = 20, first_id: str | None = None):
    chave, a = _app(req)
    if not a:
        return _negado()
    if not _conversa_do(chave, user, conversation_id):
        return JSONResponse({"code": "not_found", "message": "Conversation Not Exists."}, 404)
    lst = MENSAGENS.get(conversation_id, [])  # em ordem de criação
    if first_id:  # como no Dify: as mensagens anteriores a first_id
        ids = [m["id"] for m in lst]
        lst = lst[:ids.index(first_id)] if first_id in ids else []
    return {"data": lst[-limit:], "has_more": len(lst) > limit}


@app.post("/teste/conversa")
async def teste_conversa(req: Request):
    """Só para testes: cria direto uma conversa com N mensagens e datas no passado."""
    b = await req.json()
    cid = str(uuid.uuid4())
    quando = int(time.time()) - int(b.get("dias_atras", 0) * 86400)
    CONVERSAS[cid] = {"id": cid, "name": b.get("nome", "Conversa de teste"), "user": b["user"], "app": b["chave"],
                      "created_at": quando, "updated_at": quando + 60}
    MENSAGENS[cid] = [{"id": str(uuid.uuid4()), "conversation_id": cid, "query": f"Pergunta {i + 1}",
                       "answer": f"Resposta {i + 1}", "created_at": quando + i, "feedback": None,
                       "retriever_resources": []} for i in range(b.get("mensagens", 1))]
    return {"id": cid}


@app.post("/v1/conversations/{cid}/name")
async def nome(cid: str, req: Request):
    chave, a = _app(req)
    if not a:
        return _negado()
    b = await req.json()
    c = _conversa_do(chave, b.get("user"), cid)
    if not c:
        return JSONResponse({"code": "not_found", "message": "Conversation Not Exists."}, 404)
    c["name"] = b.get("name") or "Conversa"
    return {"id": cid, "name": c["name"]}


@app.delete("/v1/conversations/{cid}")
async def apagar(cid: str, req: Request):
    chave, a = _app(req)
    if not a:
        return _negado()
    b = await req.json()
    if not _conversa_do(chave, b.get("user"), cid):
        return JSONResponse({"code": "not_found", "message": "Conversation Not Exists."}, 404)
    CONVERSAS.pop(cid, None)
    return {"result": "success"}


@app.post("/v1/messages/{mid}/feedbacks")
async def feedback(mid: str, req: Request):
    chave, a = _app(req)
    if not a:
        return _negado()
    b = await req.json()
    for cid, lst in MENSAGENS.items():
        if CONVERSAS.get(cid, {}).get("app") != chave:
            continue
        for m in lst:
            if m["id"] == mid:
                m["feedback"] = {"rating": b.get("rating")} if b.get("rating") else None
                return {"result": "success"}
    return JSONResponse({"code": "not_found", "message": "Message Not Exists."}, 404)


@app.post("/v1/chat-messages/{task}/stop")
async def parar(task: str, req: Request):
    chave, a = _app(req)
    if not a:
        return _negado()
    return {"result": "success"}


@app.post("/v1/chat-messages")
async def chat(req: Request):
    chave, a = _app(req)
    if not a:
        return _negado()
    if a["mode"] not in ("chat", "agent-chat", "advanced-chat"):
        return JSONResponse({"code": "not_chat_app", "message": "App unavailable"}, 400)
    b = await req.json()
    cid = b.get("conversation_id") or str(uuid.uuid4())
    if b.get("conversation_id") and not _conversa_do(chave, b["user"], cid):
        return JSONResponse({"code": "not_found", "message": "Conversation Not Exists."}, 404)
    novo = cid not in CONVERSAS
    if novo:
        CONVERSAS[cid] = {"id": cid, "name": "New conversation", "user": b["user"], "app": chave,
                          "created_at": int(time.time()), "updated_at": int(time.time())}
    mid, task = str(uuid.uuid4()), str(uuid.uuid4())
    pergunta = b["query"]
    resposta = RESPOSTA_PU if a["ferramentas"] else RESPOSTA_COMERCIAL
    fontes = ([{"document_name": "BT_FLEXX_AG_2032.pdf", "dataset_name": "Boletins técnicos"},
               {"document_name": "NBR 9178 - Espumas flexíveis.pdf", "dataset_name": "Normas"}]
              if a["ferramentas"] else [])

    async def gerar():
        base = {"conversation_id": cid, "message_id": mid, "task_id": task}

        def ev(d):
            return f"data: {json.dumps({**base, **d}, ensure_ascii=False)}\n\n"
        if a["ferramentas"]:
            yield ev({"event": "agent_thought", "thought": "", "tool": "qlik_get_chart_data", "position": 1})
            await asyncio.sleep(0.6)
            yield "event: ping\n\n"
            yield ev({"event": "agent_thought", "thought": "", "tool": "buscar_documentos", "position": 2})
            await asyncio.sleep(0.6)
        for i in range(0, len(resposta), 12):
            yield ev({"event": "agent_message" if a["mode"] == "agent-chat" else "message",
                      "answer": resposta[i:i + 12]})
            await asyncio.sleep(0.015)
        yield ev({"event": "message_end", "id": mid, "metadata": {"retriever_resources": fontes}})
        MENSAGENS.setdefault(cid, []).append({"id": mid, "conversation_id": cid, "query": pergunta, "answer": resposta,
                                              "created_at": int(time.time()), "feedback": None,
                                              "retriever_resources": fontes[:1]})
        CONVERSAS[cid]["updated_at"] = int(time.time())
        if novo:
            CONVERSAS[cid]["name"] = pergunta[:40]

    return StreamingResponse(gerar(), media_type="text/event-stream")


# ------------------------------------------------------------------ API de conhecimento (chave ds-/dataset-)
CHAVE_CONHECIMENTO = "dataset-Conhecimento00000000"
CHAVE_CONHECIMENTO_RESTRITA = "dataset-Restrita0000000000000"  # imita chave presa a bases: não lista nem cria
BASES: dict[str, dict] = {}
DOCS: dict[str, dict] = {}


def _ds(req: Request):
    auth = req.headers.get("authorization", "")
    return auth[7:] == CHAVE_CONHECIMENTO


def _texto_do_arquivo(nome: str, dados: bytes) -> str:
    """Imita a leitura do Dify. O conversor falso gera '%PDF-FALSO' + texto; PDF real vira um texto genérico."""
    if dados.startswith(b"%PDF-FALSO"):
        return dados.split(b"\n", 1)[1].decode("utf-8", "replace")
    if nome.lower().endswith((".txt", ".md", ".csv")):
        return dados.decode("utf-8", "replace")
    return f"Conteúdo extraído de {nome}. " * 40


@app.get("/v1/datasets")
async def bases_listar(req: Request):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    page, limit = int(req.query_params.get("page", 1)), min(int(req.query_params.get("limit", 20)), 100)
    todas = [{**b, "document_count": sum(1 for d in DOCS.values() if d["base"] == b["id"]), "provider": "vendor"}
             for b in BASES.values()]
    ini = (page - 1) * limit
    return {"data": todas[ini:ini + limit], "has_more": ini + limit < len(todas), "total": len(todas), "page": page}


@app.post("/v1/datasets")
async def base_criar(req: Request):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    b = await req.json()
    bid = str(uuid.uuid4())
    BASES[bid] = {"id": bid, "name": b["name"], "indexing_technique": b.get("indexing_technique"),
                  "description": b.get("description", "")}
    return BASES[bid]


@app.get("/v1/datasets/{bid}")
async def base_info(bid: str, req: Request):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    if bid not in BASES:
        return JSONResponse({"code": "not_found", "message": "Dataset not found."}, 404)
    return BASES[bid]


@app.post("/v1/datasets/{bid}/document/create-by-file")
async def doc_criar(bid: str, req: Request):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    if bid not in BASES:
        return JSONResponse({"code": "not_found", "message": "Dataset not found."}, 404)
    form = await req.form()
    cfg = json.loads(form.get("data") or "{}")
    if not cfg.get("indexing_technique") and not BASES[bid].get("indexing_technique"):
        return JSONResponse({"code": "invalid_param", "message": "indexing_technique is required."}, 400)
    arq = form["file"]
    dados = await arq.read()
    if len(dados) > 15 * 1024 * 1024:
        return JSONResponse({"code": "file_too_large", "message": "File size exceeded."}, 413)
    did, batch = str(uuid.uuid4()), uuid.uuid4().hex
    texto = _texto_do_arquivo(arq.filename, dados)
    trechos = [texto[i:i + 400] for i in range(0, len(texto), 400)] or [""]
    DOCS[did] = {"id": did, "base": bid, "name": arq.filename, "dados": dados, "tipo": arq.content_type, "batch": batch,
                 "trechos": trechos, "consultas": 0, "enabled": True, "erro": "ERRO" in arq.filename.upper()}
    return {"document": {"id": did, "name": arq.filename, "indexing_status": "waiting"}, "batch": batch}


@app.get("/v1/datasets/{bid}/documents/{batch}/indexing-status")
async def doc_status(bid: str, batch: str, req: Request):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    d = next((x for x in DOCS.values() if x["batch"] == batch and x["base"] == bid), None)
    if not d:
        return {"data": []}
    d["consultas"] += 1  # primeira consulta: "indexing"; depois: "completed" (ou "error")
    situacao = "indexing" if d["consultas"] == 1 else ("error" if d["erro"] else "completed")
    return {"data": [{"id": d["id"], "indexing_status": situacao, "error": "Falha simulada" if situacao == "error" else None,
                      "completed_segments": len(d["trechos"]), "total_segments": len(d["trechos"])}]}


@app.get("/v1/datasets/{bid}/documents/{did}/segments")
async def doc_trechos(bid: str, did: str, req: Request, page: int = 1, limit: int = 20):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    d = DOCS.get(did)
    if not d or d["base"] != bid:
        return JSONResponse({"code": "not_found", "message": "Document not found."}, 404)
    ini = (page - 1) * limit
    pagina = d["trechos"][ini:ini + limit]
    return {"data": [{"id": f"{did}-{ini + i}", "position": ini + i + 1, "content": t, "word_count": len(t.split())}
                     for i, t in enumerate(pagina)], "has_more": ini + limit < len(d["trechos"]),
            "total": len(d["trechos"]), "page": page, "limit": limit}


@app.delete("/v1/datasets/{bid}/documents/{did}")
async def doc_apagar(bid: str, did: str, req: Request):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    if did not in DOCS or DOCS[did]["base"] != bid:
        return JSONResponse({"code": "not_found", "message": "Document not found."}, 404)
    DOCS.pop(did)
    from fastapi.responses import Response as R
    return R(status_code=204)


@app.patch("/v1/datasets/{bid}/documents/status/{acao}")
async def doc_mudar_status(bid: str, acao: str, req: Request):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    b = await req.json()
    for did in b.get("document_ids", []):
        if did in DOCS:
            DOCS[did]["enabled"] = acao == "enable"
    return {"result": "success"}


@app.get("/v1/datasets/{bid}/documents/{did}/download")
async def doc_link(bid: str, did: str, req: Request):
    if not _ds(req):
        return JSONResponse({"code": "unauthorized", "message": "Access token is invalid"}, 401)
    if did not in DOCS:
        return JSONResponse({"code": "not_found", "message": "Document not found."}, 404)
    return {"url": f"{str(req.base_url).rstrip('/')}/files/{did}?sign=teste"}


@app.get("/files/{did}")
async def doc_baixar(did: str, sign: str = ""):
    if sign != "teste" or did not in DOCS:
        return JSONResponse({"message": "invalid"}, 403)
    from fastapi.responses import Response as R
    return R(DOCS[did]["dados"], media_type=DOCS[did]["tipo"] or "application/octet-stream")


@app.get("/teste/docs")
async def teste_docs():
    """Só para testes: estado das bases e documentos."""
    return {"bases": BASES, "docs": {k: {kk: vv for kk, vv in v.items() if kk != "dados"} for k, v in DOCS.items()}}
