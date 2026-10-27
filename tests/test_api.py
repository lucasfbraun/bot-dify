"""Teste ponta a ponta da API (com tests/dev_server.py rodando)."""
import json
import sys

import httpx

B = "http://127.0.0.1:8088"
H = {"X-Requested-With": "agente-pu"}
falhas = 0


def ok(cond, msg):
    global falhas
    print(("  OK  " if cond else "  FALHA ") + msg)
    falhas += 0 if cond else 1


anon = httpx.Client(base_url=B)
ok(anon.get("/api/me").status_code == 401, "sem sessão -> 401")
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

r = adm.get("/api/admin/ad", params={"q": "ma"})
ok(any(i["username"] == "maria.silva" for i in r.json()["itens"]), "busca no AD acha maria")
ok(all(i["username"] != "joao.desligado" for i in adm.get("/api/admin/ad", params={"q": "jo"}).json()["itens"]), "busca no AD esconde desabilitados")
r = adm.post("/api/admin/usuarios", json={"usuario": "maria.silva", "perfil": "usuario"})
ok(r.status_code == 200 and r.json()["departamento"] == "Engenharia de Processos", "admin libera maria")
ok(adm.post("/api/admin/usuarios", json={"usuario": "joao.desligado"}).status_code == 400, "não libera desabilitado")

r = maria.post("/api/auth/login", json={"usuario": "maria.silva@flexivel.local", "senha": "Teste@123"})
ok(r.status_code == 200 and r.json()["usuario"]["perfil"] == "usuario", "maria entra depois de liberada")
ok(maria.get("/api/admin/usuarios").status_code == 403, "maria não acessa admin")

# chat com streaming
eventos = []
with maria.stream("POST", "/api/chat", json={"pergunta": "Tudo sobre o FLEXX AG 2032"}, timeout=30) as s:
    for linha in s.iter_lines():
        if linha.startswith("data:"):
            eventos.append(json.loads(linha[5:]))
tipos = [e["t"] for e in eventos]
ok(tipos[0] == "inicio" and "fim" in tipos, f"stream: inicio ... fim ({len(eventos)} eventos)")
ok([e["v"] for e in eventos if e["t"] == "status"] == ["Consultando o ERP no Qlik", "Buscando certificados, especificações e normas"], "status amigáveis das ferramentas")
texto = "".join(e["v"] for e in eventos if e["t"] == "texto")
ok("| Lote |" in texto, "texto completo chegou")
fim = [e for e in eventos if e["t"] == "fim"][0]
ok(len(fim["fontes"]) == 2, "fontes no fim")
cid = eventos[0]["conversation_id"]

conv = maria.get("/api/conversas").json()["itens"]
ok(len(conv) == 1 and conv[0]["id"] == cid, "conversa aparece na lista da maria")
ok(adm.get("/api/conversas").json()["itens"] == [], "admin não vê conversa da maria")
msgs = maria.get(f"/api/conversas/{cid}/mensagens").json()["itens"]
ok(len(msgs) == 1 and msgs[0]["pergunta"].startswith("Tudo"), "histórico da conversa")
ok(adm.get(f"/api/conversas/{cid}/mensagens").status_code == 404, "admin não lê mensagens da maria")
ok(maria.post(f"/api/mensagens/{msgs[0]['id']}/feedback", json={"avaliacao": "like"}).status_code == 200, "feedback like")
ok(maria.patch(f"/api/conversas/{cid}", json={"nome": "AG 2032"}).json()["nome"] == "AG 2032", "renomear")

# desativar corta na hora
ok(adm.patch("/api/admin/usuarios/maria.silva", json={"ativo": False}).status_code == 200, "admin desativa maria")
ok(maria.get("/api/me").status_code == 401, "sessão da maria cai na hora")
ok(adm.patch("/api/admin/usuarios/admin.ti", json={"perfil": "usuario"}).status_code == 400, "não remove o último admin")
ok(adm.delete("/api/admin/usuarios/admin.ti").status_code == 400, "não apaga o último admin")
acessos = adm.get("/api/admin/acessos").json()["itens"]
ok({"ok", "senha_invalida", "nao_cadastrado", "desabilitado"} <= {a["resultado"] for a in acessos}, "registro de acessos")

# limite de tentativas
x = httpx.Client(base_url=B, headers=H)
cods = [x.post("/api/auth/login", json={"usuario": "carlos.souza", "senha": "x"}).status_code for _ in range(6)]
ok(cods[-1] == 429, f"bloqueio após 5 erros {cods}")

ok(anon.get("/manifest.webmanifest").json()["display"] == "standalone", "manifest PWA")
ok("Service-Worker" in anon.get("/sw.js").text or anon.get("/sw.js").status_code == 200, "sw.js servido")
ok(anon.get("/admin").status_code == 200 and "tela-login" in anon.get("/admin").text, "rota SPA")
ok("default-src 'self'" in anon.get("/").headers["content-security-policy"], "CSP presente")
print("\nfalhas:", falhas)
sys.exit(1 if falhas else 0)
