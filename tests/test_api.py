"""Teste ponta a ponta da API (com tests/dev_server.py rodando num DATA_DIR novo)."""
import io
import json
import math
import os
import re
import struct
import sys
import wave
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_dify import CHAVE_COMERCIAL, CHAVE_PU, CHAVE_WORKFLOW  # noqa: E402

B = f"http://127.0.0.1:{os.getenv('APP_PORT', '8088')}"
H = {"X-Requested-With": "agente-pu"}
falhas = 0


def ok(cond, msg):
    global falhas
    print(("  OK  " if cond else "  FALHA ") + msg)
    falhas += 0 if cond else 1


def secao(titulo):
    print(f"\n== {titulo}")


def conversar(cliente, aid, pergunta, cid=None):
    eventos = []
    with cliente.stream("POST", f"/api/agentes/{aid}/chat", json={"pergunta": pergunta, "conversation_id": cid},
                        timeout=30) as s:
        if s.status_code != 200:
            return s.status_code, []
        for linha in s.iter_lines():
            if linha.startswith("data:"):
                eventos.append(json.loads(linha[5:]))
    return 200, eventos


def ids(cliente):
    return [a["id"] for a in cliente.get("/api/agentes").json()["itens"]]


# ------------------------------------------------------------------ login
secao("login e sessão")
anon = httpx.Client(base_url=B)
ok(anon.get("/api/me").status_code == 401, "sem sessão -> 401")
ok(anon.get("/api/agentes").status_code == 401, "lista de agentes sem sessão -> 401")
ok(anon.post("/api/auth/login", json={"usuario": "admin.ti", "senha": "Teste@123"}).status_code == 403, "POST sem cabeçalho CSRF -> 403")

maria = httpx.Client(base_url=B, headers=H)
r = maria.post("/api/auth/login", json={"usuario": "maria.silva", "senha": "Teste@123"})
ok(r.status_code == 403 and "acesso" in r.json()["detail"], "AD ok mas não cadastrada -> 403")
r = maria.post("/api/auth/login", json={"usuario": "maria.silva", "senha": "errada"})
ok(r.status_code == 401, "senha errada -> 401")
r = maria.post("/api/auth/login", json={"usuario": "joao.desligado", "senha": "Teste@123"})
ok(r.status_code == 403 and "desabilitada" in r.json()["detail"], "conta desabilitada no AD -> 403")

adm = httpx.Client(base_url=B, headers=H)
r = adm.post("/api/auth/login", json={"usuario": "FLEXIVEL\\Admin.TI", "senha": "Teste@123"})
ok(r.status_code == 200 and r.json()["usuario"]["perfil"] == "admin", "admin inicial entra e vira admin")
ck = r.headers.get("set-cookie", "")
ok("httponly" in ck.lower() and "samesite=strict" in ck.lower(), "cookie HttpOnly + SameSite=Strict")
ok(adm.get("/api/me").json()["nome"] == "Admin da TI", "/api/me com nome do AD")

# ------------------------------------------------------------------ migração do agente único
secao("migração: a DIFY_API_KEY vira o primeiro agente")
lista = adm.get("/api/admin/agentes").json()["itens"]
ok(len(lista) == 1, "um agente cadastrado na subida")
pu = lista[0]
PU = pu["id"]
ok(pu["nome"] == "Agente Técnico PU(qlik)" and pu["modo"] == "agent-chat", "nome e tipo lidos do Dify")
ok(pu["icone"] == "🧪" and pu["cor"] == "#E4FBCC", "ícone e cor lidos do Dify")
ok(len(pu["sugestoes"]) == 5 and pu["sugestoes"][0]["cat"] == "Produto", "perguntas de exemplo do PU preservadas")
ok("chave" not in pu and pu["chave_mascarada"].startswith("app-PUq") and CHAVE_PU not in json.dumps(lista),
   "a chave nunca sai do servidor (só mascarada)")
ok(ids(adm) == [PU], "admin inicial recebe o agente")

# ------------------------------------------------------------------ cadastro de agentes
secao("cadastro de agentes")
r = adm.post("/api/admin/agentes", json={"chave": "abc"})
ok(r.status_code == 400 and "Chave inválida" in r.json()["detail"], "formato de chave inválido -> 400")
r = adm.post("/api/admin/agentes", json={"chave": "app-NaoExiste000000000000"})
ok(r.status_code == 400 and "recusou" in r.json()["detail"], "chave que o Dify não conhece -> 400")
r = adm.post("/api/admin/agentes", json={"chave": CHAVE_WORKFLOW})
ok(r.status_code == 400 and "workflow" in r.json()["detail"], "app do tipo workflow é recusado")
r = adm.post("/api/admin/agentes", json={"chave": f"  {CHAVE_PU} "})
ok(r.status_code == 409 and "Agente Técnico PU" in r.json()["detail"], "chave repetida -> 409 (com espaços em volta)")
ok(httpx.post(f"{B}/api/admin/agentes", json={"chave": CHAVE_COMERCIAL}, cookies=adm.cookies).status_code == 403,
   "cadastro sem cabeçalho CSRF -> 403")
r = adm.post("/api/admin/agentes", json={"chave": CHAVE_COMERCIAL})
com = r.json()
COM = com.get("id")
ok(r.status_code == 200 and com["nome"] == "Agente Comercial" and com["modo"] == "advanced-chat", "cadastra o agente comercial")
ok(com["qtd_usuarios"] == 1 and "chave" not in com, "novo agente já liberado para o admin; chave não volta")
ok(sorted(ids(adm)) == sorted([PU, COM]), "admin enxerga os dois agentes")

# ------------------------------------------------------------------ usuário com acesso só ao comercial
secao("usuário com acesso a um agente só")
r = adm.post("/api/admin/usuarios", json={"usuario": "maria.silva", "perfil": "usuario", "agentes": [COM]})
ok(r.status_code == 200 and r.json()["agentes"] == [COM], "admin libera maria só no comercial")
ok(adm.post("/api/admin/usuarios", json={"usuario": "joao.desligado"}).status_code == 400, "não libera desabilitado")
r = maria.post("/api/auth/login", json={"usuario": "maria.silva@flexivel.local", "senha": "Teste@123"})
ok(r.status_code == 200 and r.json()["usuario"]["perfil"] == "usuario", "maria entra depois de liberada")
ok(maria.get("/api/admin/usuarios").status_code == 403, "maria não acessa admin de usuários")
ok(maria.get("/api/admin/agentes").status_code == 403, "maria não acessa admin de agentes")
meus = maria.get("/api/agentes").json()["itens"]
ok([a["id"] for a in meus] == [COM], "maria só enxerga o comercial")
ok(set(meus[0]) == {"id", "nome", "descricao", "icone", "cor"}, "lista de agentes do usuário não traz chave")

negados = {
    "info": maria.get(f"/api/agentes/{PU}/info").status_code,
    "conversas": maria.get(f"/api/agentes/{PU}/conversas").status_code,
    "mensagens": maria.get(f"/api/agentes/{PU}/conversas/00000000-0000-0000-0000-000000000000/mensagens").status_code,
    "renomear": maria.patch(f"/api/agentes/{PU}/conversas/00000000-0000-0000-0000-000000000000", json={"nome": "x"}).status_code,
    "apagar": maria.delete(f"/api/agentes/{PU}/conversas/00000000-0000-0000-0000-000000000000").status_code,
    "feedback": maria.post(f"/api/agentes/{PU}/mensagens/00000000-0000-0000-0000-000000000000/feedback", json={"avaliacao": "like"}).status_code,
    "parar": maria.post(f"/api/agentes/{PU}/chat/00000000-0000-0000-0000-000000000000/parar").status_code,
    "chat": conversar(maria, PU, "me mostre o custo")[0],
    "inexistente": maria.get("/api/agentes/9999/conversas").status_code,
}
ok(all(v == 403 for v in negados.values()), f"todas as rotas do PU negadas para maria {negados}")

code, eventos = conversar(maria, COM, "Quais espumas temos para colchões?")
tipos = [e["t"] for e in eventos]
ok(code == 200 and tipos[0] == "inicio" and "fim" in tipos, f"maria conversa com o comercial ({len(eventos)} eventos)")
texto = "".join(e["v"] for e in eventos if e["t"] == "texto")
ok("Agente Comercial" in texto and "status" not in tipos, "resposta veio do app comercial (sem ferramentas do Qlik)")
cid_maria = eventos[0]["conversation_id"]
info_com = maria.get(f"/api/agentes/{COM}/info").json()
ok(info_com["sugestoes"] == [{"cat": "", "txt": "Quais espumas temos para colchões?"}], "perguntas sugeridas do Dify")
conv = maria.get(f"/api/agentes/{COM}/conversas").json()["itens"]
ok(len(conv) == 1 and conv[0]["id"] == cid_maria, "conversa aparece no histórico do comercial")
msgs = maria.get(f"/api/agentes/{COM}/conversas/{cid_maria}/mensagens").json()["itens"]
ok(len(msgs) == 1 and msgs[0]["pergunta"].startswith("Quais"), "histórico da conversa")
ok(maria.post(f"/api/agentes/{COM}/mensagens/{msgs[0]['id']}/feedback", json={"avaliacao": "like"}).status_code == 200, "feedback like")
ok(maria.patch(f"/api/agentes/{COM}/conversas/{cid_maria}", json={"nome": "Colchões"}).json()["nome"] == "Colchões", "renomear")

# ------------------------------------------------------------------ admin no PU
secao("admin usa o PU")
code, eventos = conversar(adm, PU, "Tudo sobre o FLEXX AG 2032")
ok(code == 200 and [e["v"] for e in eventos if e["t"] == "status"] == ["Consultando o ERP no Qlik", "Buscando certificados, especificações e normas"],
   "status amigáveis das ferramentas do PU")
texto = "".join(e["v"] for e in eventos if e["t"] == "texto")
ok("| Lote |" in texto and len([e for e in eventos if e["t"] == "fim"][0]["fontes"]) == 2, "texto e fontes do PU")
info_pu = adm.get(f"/api/agentes/{PU}/info").json()
ok(info_pu["sugestoes"][0] == {"cat": "Produto", "txt": "Tudo sobre o FLEXX AG 2032"}, "sem sugestões no Dify, usa as do agente")
ok(adm.get(f"/api/agentes/{COM}/conversas").json()["itens"] == [], "admin não vê conversa da maria")
ok(adm.get(f"/api/agentes/{COM}/conversas/{cid_maria}/mensagens").status_code == 404, "admin não lê mensagens da maria")

# ------------------------------------------------------------------ áudio
secao("perguntas por áudio (voz para texto)")


def wav(segundos=1.0, taxa=16000, silencio=False):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        n = int(segundos * taxa)
        w.writeframes(b"\x00\x00" * n if silencio else
                      b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(n)))
    return buf.getvalue()


AUDIO = {"Content-Type": "audio/wav"}
ok(adm.get(f"/api/agentes/{PU}/info").json()["audio"] is True, "PU com voz para texto: info.audio = true")
ok(maria.get(f"/api/agentes/{COM}/info").json()["audio"] is False, "comercial sem voz para texto: info.audio = false")
r = adm.post(f"/api/agentes/{PU}/audio", content=wav(2.0), headers=AUDIO)
ok(r.status_code == 200 and r.json()["texto"].startswith("Qual o custo") and "2.0 s, 16000 Hz, 1 canal" in r.json()["texto"],
   f"transcreve o WAV ({r.json()})")
r = maria.post(f"/api/agentes/{COM}/audio", content=wav(), headers=AUDIO)
ok(r.status_code == 400 and "Voz para texto" in r.json()["detail"], "agente sem voz para texto -> 400 explicando o que ligar")
ok(maria.post(f"/api/agentes/{PU}/audio", content=wav(), headers=AUDIO).status_code == 403, "agente não liberado -> 403")
ok(adm.post(f"/api/agentes/{PU}/audio", content=wav(), headers={"Content-Type": "audio/webm"}).status_code == 415,
   "webm (que o Dify recusa) -> 415")
ok(adm.post(f"/api/agentes/{PU}/audio", content=b"x" * 500, headers=AUDIO).status_code == 400, "áudio curto demais -> 400")
r = adm.post(f"/api/agentes/{PU}/audio", content=wav(1.0, silencio=True), headers=AUDIO)
ok(r.status_code == 422 and "entender" in r.json()["detail"], "silêncio (sem texto) -> 422")
r = adm.post(f"/api/agentes/{PU}/audio", content=b"\0" * (13 * 1024 * 1024), headers=AUDIO)
ok(r.status_code == 413 and "MB" in r.json()["detail"], "acima de AUDIO_MAX_MB -> 413")
ok(httpx.post(f"{B}/api/agentes/{PU}/audio", content=wav(), headers=AUDIO, cookies=adm.cookies).status_code == 403,
   "envio de áudio sem cabeçalho CSRF -> 403")
ok(anon.post(f"/api/agentes/{PU}/audio", content=wav(), headers={**AUDIO, **H}).status_code == 401, "sem sessão -> 401")
ok("microphone=(self)" in anon.get("/").headers["permissions-policy"], "Permissions-Policy libera o microfone só para o site")

# ------------------------------------------------------------------ liberar e revogar
secao("liberar e revogar agentes")
r = adm.put("/api/admin/usuarios/maria.silva/agentes", json={"agentes": [PU, COM, 9999]})
ok(r.status_code == 200 and r.json()["agentes"] == sorted([PU, COM]), "libera o PU (id inexistente é ignorado)")
ok(sorted(ids(maria)) == sorted([PU, COM]), "maria passa a enxergar os dois")
ok(maria.get(f"/api/agentes/{PU}/conversas").json()["itens"] == [], "cada agente tem o próprio histórico")
ok(maria.get(f"/api/agentes/{PU}/conversas/{cid_maria}/mensagens").status_code == 404,
   "conversa do comercial não abre pelo PU")
code, eventos = conversar(maria, PU, "continuação", cid_maria)
ok(code == 200 and [e["t"] for e in eventos] == ["erro"], "chat com conversa de outro agente não vaza (só erro no stream)")
usuarios = {u["username"]: u for u in adm.get("/api/admin/usuarios").json()["itens"]}
ok(usuarios["maria.silva"]["agentes"] == sorted([PU, COM]), "lista de usuários traz os agentes de cada um")
ok(adm.put("/api/admin/usuarios/maria.silva/agentes", json={"agentes": [COM]}).status_code == 200, "revoga o PU")
ok(conversar(maria, PU, "custo")[0] == 403 and maria.get(f"/api/agentes/{PU}/info").status_code == 403,
   "revogação corta o PU na hora")
ok(adm.put("/api/admin/usuarios/ninguem/agentes", json={"agentes": [COM]}).status_code == 404, "usuário inexistente -> 404")
ok(maria.put("/api/admin/usuarios/maria.silva/agentes", json={"agentes": [PU]}).status_code == 403,
   "usuário comum não se dá acesso")

# ------------------------------------------------------------------ desativar, editar e trocar chave
secao("desativar, editar e trocar a chave do agente")
ok(adm.patch(f"/api/admin/agentes/{COM}", json={"ativo": False}).json()["ativo"] == 0, "desativa o comercial")
ok(ids(maria) == [] and COM not in ids(adm), "agente desativado some para todos")
ok(conversar(maria, COM, "oi")[0] == 403, "chat em agente desativado -> 403")
ok(any(a["id"] == COM and not a["ativo"] for a in adm.get("/api/admin/agentes").json()["itens"]), "continua na administração")
ok(adm.patch(f"/api/admin/agentes/{COM}", json={"ativo": True}).json()["ativo"] == 1, "reativa")
ok(ids(maria) == [COM], "maria volta a enxergar (liberação mantida)")

r = adm.patch(f"/api/admin/agentes/{PU}", json={"nome": "PU (completo)", "descricao": "Acesso total",
                                                 "sugestoes": [{"cat": "Teste", "txt": "Pergunta nova"}, {"txt": "  "}]})
ok(r.status_code == 200, "edita nome/descrição/sugestões")
ok(r.json()["nome"] == "PU (completo)" and r.json()["descricao"] == "Acesso total", "nome e descrição salvos")
ok(adm.get(f"/api/agentes/{PU}/info").json()["sugestoes"] == [{"cat": "Teste", "txt": "Pergunta nova"}], "sugestões novas no chat")
ok(adm.patch(f"/api/admin/agentes/{PU}", json={"nome": "  "}).status_code == 400, "nome vazio -> 400")
ok(adm.patch(f"/api/admin/agentes/{PU}", json={"chave": CHAVE_WORKFLOW}).status_code == 400, "trocar para chave de workflow -> 400")
ok(adm.patch(f"/api/admin/agentes/{PU}", json={"chave": CHAVE_COMERCIAL}).status_code == 409, "trocar para chave de outro agente -> 409")
ok(adm.patch(f"/api/admin/agentes/{PU}", json={"chave": CHAVE_PU}).status_code == 200, "mesma chave: nada muda")
ok(adm.patch("/api/admin/agentes/9999", json={"nome": "x"}).status_code == 404, "agente inexistente -> 404")
ok(conversar(adm, PU, "ainda funciona?")[0] == 200, "PU segue funcionando")

# ------------------------------------------------------------------ ids maliciosos
secao("ids inválidos não chegam ao Dify")
ok(maria.get(f"/api/agentes/{COM}/conversas/abc/mensagens").status_code == 400, "id curto -> 400")
ok(maria.get(f"/api/agentes/{COM}/conversas/..%2F..%2Fparameters/mensagens").status_code in (400, 404), "id com ../ -> recusado")
ok(maria.get(f"/api/agentes/{COM}/conversas", params={"last_id": "x/../../y"}).status_code == 400, "last_id inválido -> 400")
ok(conversar(maria, COM, "oi", "../../info")[0] == 400, "conversation_id inválido no chat -> 400")
ok(maria.get("/api/agentes/abc/conversas").status_code == 422, "id de agente não numérico -> 422")

# ------------------------------------------------------------------ regra dos administradores
secao("administradores recebem todos os agentes e podem desmarcar")
r = adm.post("/api/admin/usuarios", json={"usuario": "carlos.souza", "perfil": "usuario"})
ok(r.status_code == 200 and r.json()["agentes"] == [], "usuário sem agentes marcados fica sem nenhum")
carlos = httpx.Client(base_url=B, headers=H)
ok(carlos.post("/api/auth/login", json={"usuario": "carlos.souza", "senha": "Teste@123"}).status_code == 200,
   "usuário sem agente consegue entrar")
ok(carlos.get("/api/agentes").json()["itens"] == [], "e vê a lista vazia")
r = adm.patch("/api/admin/usuarios/carlos.souza", json={"perfil": "admin"})
ok(sorted(r.json()["agentes"]) == sorted([PU, COM]), "promovido a admin recebe todos os agentes")
ok(adm.put("/api/admin/usuarios/admin.ti/agentes", json={"agentes": [PU]}).status_code == 200, "admin desmarca o comercial para si")
ok(ids(adm) == [PU], "admin passa a ver só o PU no chat")
ok(len(adm.get("/api/admin/agentes").json()["itens"]) == 2, "mas continua administrando os dois")
ok(adm.delete(f"/api/admin/agentes/{COM}").status_code == 200, "remove o comercial")
ok(ids(maria) == [] and ids(carlos) == [PU], "remoção tira o agente de todo mundo")
com2 = adm.post("/api/admin/agentes", json={"chave": CHAVE_COMERCIAL, "nome": "Comercial"}).json()
ok(com2["nome"] == "Comercial" and com2["qtd_usuarios"] == 2, "recadastrado com nome próprio: vai para os 2 admins")
ok(com2["id"] in ids(adm) and com2["id"] in ids(carlos) and ids(maria) == [], "admins sim, maria não")
ok(adm.delete("/api/admin/agentes/9999").status_code == 404, "remover inexistente -> 404")

ok(adm.delete("/api/admin/usuarios/carlos.souza").status_code == 200, "remove carlos")
r = adm.post("/api/admin/usuarios", json={"usuario": "carlos.souza", "perfil": "usuario"})
ok(r.json()["agentes"] == [], "ao voltar, carlos não herda acessos antigos")

# ------------------------------------------------------------------ registros
secao("registros: logins + uso dos agentes, com filtros")
FUSO = ZoneInfo("America/Sao_Paulo")
hoje = datetime.now(FUSO).date()
dia = lambda n: (hoje + timedelta(days=n)).isoformat()  # noqa: E731
COM2 = com2["id"]

regs = adm.get("/api/admin/registros").json()["itens"]
tipos_uso = {(r["username"], r["agente_nome"], r["evento"]) for r in regs if r["tipo"] == "uso"}
ok(("maria.silva", "Agente Comercial", "pergunta") in tipos_uso, "pergunta da maria ao comercial registrada")
ok(("admin.ti", "Agente Técnico PU(qlik)", "audio") in tipos_uso, "áudio transcrito registrado")
ok(("maria.silva", "Agente Técnico PU(qlik)", "erro") in tipos_uso, "pergunta com erro registrada como erro")
ok(any(r["tipo"] == "acesso" and r["evento"] == "senha_invalida" for r in regs), "logins continuam no registro")
ok(regs == sorted(regs, key=lambda r: r["quando"], reverse=True), "do mais novo para o mais antigo")
r = adm.get("/api/admin/registros", params={"usuario": "maria.silva"}).json()["itens"]
ok(r and all(x["username"] == "maria.silva" for x in r), "filtro por usuário")
r = adm.get("/api/admin/registros", params={"usuario": "FLEXIVEL\\Maria.Silva"}).json()["itens"]
ok(r and all(x["username"] == "maria.silva" for x in r), "filtro por usuário aceita DOMINIO\\login")
r = adm.get("/api/admin/registros", params={"agente": PU}).json()["itens"]
ok(r and all(x["tipo"] == "uso" and x["agente_id"] == PU for x in r), "filtro por agente traz só o uso daquele agente")
r = adm.get("/api/admin/registros", params={"tipo": "acessos"}).json()["itens"]
ok(r and all(x["tipo"] == "acesso" for x in r), "filtro só logins")
r = adm.get("/api/admin/registros", params={"tipo": "uso", "usuario": "admin.ti"}).json()["itens"]
ok(r and all(x["tipo"] == "uso" and x["username"] == "admin.ti" for x in r), "filtro só uso + usuário")
ok(len(adm.get("/api/admin/registros", params={"inicio": dia(0), "fim": dia(0)}).json()["itens"]) > 10, "período de hoje traz os registros")
ok(adm.get("/api/admin/registros", params={"inicio": dia(1)}).json()["itens"] == [], "início amanhã: nada")
ok(adm.get("/api/admin/registros", params={"fim": dia(-1)}).json()["itens"] == [], "fim ontem: nada")
ok(adm.get("/api/admin/registros", params={"inicio": "28/10/2026"}).status_code == 400, "data em formato errado -> 400")
ok(adm.get("/api/admin/registros", params={"inicio": dia(0), "fim": dia(-2)}).status_code == 400, "início depois do fim -> 400")
ok(adm.get("/api/admin/registros", params={"tipo": "x"}).status_code == 400, "tipo inválido -> 400")
ok(carlos.get("/api/admin/registros").status_code in (401, 403), "usuário comum não vê registros")

# ------------------------------------------------------------------ conversas de todos
secao("conversas de todos os usuários: listar, ver e exportar")
FAKE = f"http://127.0.0.1:{os.getenv('DIFY_PORT', '8099')}"
for i in range(120):  # passa de 100: obriga a paginar no Dify
    httpx.post(f"{FAKE}/teste/conversa", json={"chave": CHAVE_PU, "user": "ad:carlos.souza", "nome": f"Lote {i:03d}",
                                                "dias_atras": 2})
longa = httpx.post(f"{FAKE}/teste/conversa", json={"chave": CHAVE_PU, "user": "ad:carlos.souza", "nome": "Conversa longa",
                                                   "mensagens": 250, "dias_atras": 1}).json()["id"]
antiga = httpx.post(f"{FAKE}/teste/conversa", json={"chave": CHAVE_COMERCIAL, "user": "ad:maria.silva",
                                                    "nome": "Conversa antiga", "dias_atras": 40}).json()["id"]

todas = adm.get("/api/admin/conversas").json()
ok(todas["avisos"] == [], "sem avisos de erro")
itens = todas["itens"]
conv_maria = next((c for c in itens if c["id"] == cid_maria), None)
ok(conv_maria and conv_maria["usuario"] == "Maria Silva" and conv_maria["agente_id"] == COM2 and conv_maria["titulo"] == "Colchões",
   "admin enxerga a conversa da maria (com nome, agente e título)")
ok(any(c["username"] == "admin.ti" and c["agente_id"] == PU for c in itens), "e as do próprio admin no PU")
ok(itens == sorted(itens, key=lambda c: -c["atualizado"]), "mais recentes primeiro")
r = adm.get("/api/admin/conversas", params={"usuario": "carlos.souza", "agente": PU}).json()["itens"]
ok(len(r) == 121, f"paginação: as 121 conversas do carlos no PU ({len(r)})")
r = adm.get("/api/admin/conversas", params={"usuario": "maria.silva"}).json()["itens"]
ok(r and all(c["username"] == "maria.silva" for c in r), "filtro por usuário")
r = adm.get("/api/admin/conversas", params={"agente": COM2}).json()["itens"]
ok(r and all(c["agente_id"] == COM2 for c in r), "filtro por agente")
r = adm.get("/api/admin/conversas", params={"usuario": "maria.silva", "inicio": dia(-10)}).json()["itens"]
ok(antiga not in [c["id"] for c in r] and cid_maria in [c["id"] for c in r], "início há 10 dias esconde a conversa de 40 dias")
r = adm.get("/api/admin/conversas", params={"usuario": "maria.silva", "fim": dia(-30)}).json()["itens"]
ok([c["id"] for c in r] == [antiga], "fim há 30 dias traz só a antiga")
ok(adm.get("/api/admin/conversas", params={"agente": 9999}).status_code == 404, "agente inexistente -> 404")
ok(adm.get("/api/admin/conversas", params={"usuario": "x y"}).status_code == 400, "usuário inválido -> 400")
ok(carlos.get("/api/admin/conversas").status_code in (401, 403), "usuário comum não lista conversas")

r = adm.get(f"/api/admin/conversas/{COM2}/maria.silva/{cid_maria}").json()["mensagens"]
ok(len(r) == 1 and r[0]["pergunta"].startswith("Quais espumas") and r[0]["avaliacao"] == "like", "admin lê a conversa da maria")
r = adm.get(f"/api/admin/conversas/{PU}/carlos.souza/{longa}").json()["mensagens"]
ok(len(r) == 250 and r[0]["pergunta"] == "Pergunta 1" and r[-1]["pergunta"] == "Pergunta 250",
   "conversa de 250 mensagens chega inteira e em ordem")
ok(adm.get(f"/api/admin/conversas/{PU}/maria.silva/{cid_maria}").status_code == 404, "conversa em outro agente -> 404")
ok(adm.get(f"/api/admin/conversas/{PU}/carlos.souza/abc").status_code == 400, "id inválido -> 400")
ok(carlos.get(f"/api/admin/conversas/{COM2}/maria.silva/{cid_maria}").status_code in (401, 403), "usuário comum não lê")

r = adm.post("/api/admin/conversas/exportar", json={"itens": [
    {"agente_id": COM2, "username": "maria.silva", "conversation_id": cid_maria, "titulo": "Colchões",
     "criado": conv_maria["criado"], "atualizado": conv_maria["atualizado"]}]})
txt = r.text
ok(r.status_code == 200 and r.headers["content-type"].startswith("text/plain") and "charset=utf-8" in r.headers["content-type"],
   "exportar selecionada: TXT em UTF-8")
ok(re.search(r'attachment; filename="conversas-\d{8}-\d{4}\.txt"', r.headers.get("content-disposition", "")), "baixa como arquivo .txt")
ok(txt.startswith("﻿") and "\r\n" in txt, "BOM e quebras de linha do Windows (abre certo no Bloco de Notas)")
ok("Conversa: Colchões" in txt and "Usuário:  Maria Silva (maria.silva)" in txt and "Agente:   Comercial" in txt,
   "cabeçalho com título, usuário e agente")
ok("Quais espumas temos para colchões?" in txt and "Não tenho acesso ao ERP" in txt and "Avaliação: útil" in txt,
   "pergunta, resposta e avaliação no TXT")
ok(re.search(r"\[\d{2}/\d{2}/\d{4} \d{2}:\d{2}\] Maria Silva:", txt), "horário em dd/mm/aaaa hh:mm")
r = adm.post("/api/admin/conversas/exportar", json={"filtro": {"usuario": "carlos.souza", "agente": PU}})
ok(r.status_code == 200 and "121 conversa(s)" in r.text and r.text.count("Conversa: Lote ") == 120 and "Pergunta 250" in r.text,
   "exportar todas do filtro (121 conversas, inclusive a longa inteira)")
ok(adm.post("/api/admin/conversas/exportar", json={"filtro": {"inicio": dia(1)}}).status_code == 404, "filtro sem conversas -> 404")
ok(adm.post("/api/admin/conversas/exportar", json={}).status_code == 400, "sem itens nem filtro -> 400")
ok(adm.post("/api/admin/conversas/exportar", json={"itens": [{"agente_id": 9999, "username": "maria.silva",
                                                              "conversation_id": cid_maria}]}).status_code == 404, "agente inexistente -> 404")
ok(adm.post("/api/admin/conversas/exportar", json={"itens": [{"agente_id": COM2, "username": "maria.silva",
                                                              "conversation_id": "../../x/../y"}]}).status_code == 400, "id inválido -> 400")
ok(carlos.post("/api/admin/conversas/exportar", json={"filtro": {}}).status_code in (401, 403), "usuário comum não exporta")
ok(httpx.post(f"{B}/api/admin/conversas/exportar", json={"filtro": {}}, cookies=adm.cookies).status_code == 403,
   "exportar sem cabeçalho CSRF -> 403")

# ------------------------------------------------------------------ regras que já existiam
secao("regras de acesso existentes")
ok(adm.patch("/api/admin/usuarios/maria.silva", json={"ativo": False}).status_code == 200, "admin desativa maria")
ok(maria.get("/api/me").status_code == 401 and maria.get("/api/agentes").status_code == 401, "sessão da maria cai na hora")
ok(adm.patch("/api/admin/usuarios/admin.ti", json={"perfil": "usuario"}).status_code == 400, "não remove o último admin")
ok(adm.delete("/api/admin/usuarios/admin.ti").status_code == 400, "não apaga o último admin")
acessos = adm.get("/api/admin/acessos").json()["itens"]
ok({"ok", "senha_invalida", "nao_cadastrado", "desabilitado"} <= {a["resultado"] for a in acessos}, "registro de acessos")

x = httpx.Client(base_url=B, headers=H)
cods = [x.post("/api/auth/login", json={"usuario": "carlos.souza", "senha": "x"}).status_code for _ in range(6)]
ok(cods[-1] == 429, f"bloqueio após 5 erros {cods}")

ok(anon.get("/manifest.webmanifest").json()["display"] == "standalone", "manifest PWA")
ok(anon.get("/sw.js").status_code == 200, "sw.js servido")
ok(anon.get("/admin").status_code == 200 and "tela-login" in anon.get("/admin").text, "rota SPA")
ok("default-src 'self'" in anon.get("/").headers["content-security-policy"], "CSP presente")
ok(anon.get("/api/conversas").status_code == 404, "rotas antigas sem agente não existem mais")

secao("conversas de quem foi removido continuam visíveis")
ok(adm.delete("/api/admin/usuarios/maria.silva").status_code == 200, "remove a maria")
r = adm.get("/api/admin/conversas", params={"agente": COM2}).json()["itens"]
ok(any(c["id"] == cid_maria and c["usuario"] == "maria.silva" for c in r), "conversa dela ainda aparece (pelo histórico de uso)")
print("\nfalhas:", falhas)
sys.exit(1 if falhas else 0)
