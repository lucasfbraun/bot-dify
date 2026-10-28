"""Testa a atualização de um banco da versão de agente único (sem servidor rodando).

    python tests/test_migracao.py

Cria um agente.db no formato antigo, com usuários, e sobe o app por cima dele (como no servidor depois do git pull).
"""
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(AQUI, "..", "backend"), AQUI]
DIFY_PORT = 8199
DATA_DIR = tempfile.mkdtemp(prefix="agente-migracao-")
os.environ.update(DATA_DIR=DATA_DIR, COOKIE_SECURE="false", SECRET_KEY="chave-so-para-teste-local-com-32-bytes", INITIAL_ADMINS="admin.ti",
                  LDAP_BIND_USER="svc.integracao@flexivel.local", LDAP_BIND_PASSWORD="Teste@123",
                  DIFY_API_URL=f"http://127.0.0.1:{DIFY_PORT}/v1")

import fake_dify  # noqa: E402

os.environ["DIFY_API_KEY"] = fake_dify.CHAVE_PU

import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ldap3 import MOCK_SYNC  # noqa: E402

import mock_ldap  # noqa: E402
from app import db, ldap_auth  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

srv = mock_ldap.criar_servidor()
ldap_auth._server_factory = lambda: srv
ldap_auth._conn_kwargs = {"client_strategy": MOCK_SYNC}
threading.Thread(target=lambda: uvicorn.run(fake_dify.app, host="127.0.0.1", port=DIFY_PORT, log_level="warning"),
                 daemon=True).start()
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", DIFY_PORT), timeout=0.2).close()
        break
    except OSError:
        time.sleep(0.1)

# banco exatamente como a versão de agente único criava
SCHEMA_ANTIGO = """
CREATE TABLE usuarios (username TEXT PRIMARY KEY, nome TEXT, email TEXT, departamento TEXT, cargo TEXT,
    perfil TEXT NOT NULL DEFAULT 'usuario', ativo INTEGER NOT NULL DEFAULT 1, criado_em TEXT NOT NULL, criado_por TEXT,
    ultimo_acesso TEXT, qtd_acessos INTEGER NOT NULL DEFAULT 0);
CREATE TABLE acessos (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, quando TEXT NOT NULL, ip TEXT,
    resultado TEXT NOT NULL, detalhe TEXT);
CREATE TABLE auditoria (id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT NOT NULL, autor TEXT NOT NULL,
    acao TEXT NOT NULL, alvo TEXT, detalhe TEXT);
CREATE INDEX ix_acessos_quando ON acessos(quando);
"""
H = {"X-Requested-With": "agente-pu"}
falhas = 0


def ok(cond, msg):
    global falhas
    print(("  OK  " if cond else "  FALHA ") + msg)
    falhas += 0 if cond else 1


def banco_antigo(caminho):
    c = sqlite3.connect(caminho)
    c.executescript(SCHEMA_ANTIGO)
    for u, perfil, ativo in [("admin.ti", "admin", 1), ("maria.silva", "usuario", 1), ("carlos.souza", "usuario", 0)]:
        c.execute("INSERT INTO usuarios (username, nome, perfil, ativo, criado_em) VALUES (?,?,?,?,?)",
                  (u, u, perfil, ativo, "2026-10-01T00:00:00+00:00"))
    c.commit()
    c.close()


def entrar(cliente, usuario):
    r = cliente.post("/api/auth/login", json={"usuario": usuario, "senha": "Teste@123"}, headers=H)
    return r.status_code


print("== banco antigo com 3 usuários")
banco_antigo(db.DB_PATH)
with TestClient(app) as c:
    ok(entrar(c, "admin.ti") == 200, "admin antigo entra")
    agentes = c.get("/api/admin/agentes").json()["itens"]
    ok(len(agentes) == 1 and agentes[0]["nome"] == "Agente Técnico PU(qlik)", "PU cadastrado com o nome do Dify")
    ok(agentes[0]["qtd_usuarios"] == 3, "liberado para os 3 usuários que já existiam (inclusive o inativo)")
    PU = agentes[0]["id"]
    usuarios = {u["username"]: u for u in c.get("/api/admin/usuarios").json()["itens"]}
    ok(all(u["agentes"] == [PU] for u in usuarios.values()), "cada usuário antigo tem o PU")
    ok(usuarios["maria.silva"]["perfil"] == "usuario" and usuarios["carlos.souza"]["ativo"] == 0,
       "perfis e status antigos preservados")
with TestClient(app) as c:
    ok(entrar(c, "maria.silva") == 200, "usuária antiga entra")
    ok([a["id"] for a in c.get("/api/agentes").json()["itens"]] == [PU], "e continua usando o PU")
    with c.stream("POST", f"/api/agentes/{PU}/chat", json={"pergunta": "oi"}, headers=H) as s:
        corpo = "".join(s.iter_text())
    ok('"t": "fim"' in corpo, "conversa com o PU funciona depois da migração")

print("== reinícios")
with TestClient(app) as c:
    entrar(c, "admin.ti")
    ok(len(c.get("/api/admin/agentes").json()["itens"]) == 1, "reiniciar não duplica o agente")
    ok(c.delete(f"/api/admin/agentes/{PU}", headers=H).status_code == 200, "admin remove o PU")
with TestClient(app) as c:
    entrar(c, "admin.ti")
    ok(c.get("/api/admin/agentes").json()["itens"] == [], "agente removido não volta sozinho no reinício")
    ok(c.get("/api/agentes").json()["itens"] == [], "sem agentes, a lista fica vazia (sem erro)")

print("== Dify fora do ar durante a migração")
db.DB_PATH = os.path.join(DATA_DIR, "offline.db")
banco_antigo(db.DB_PATH)
url_original = settings.DIFY_API_URL
settings.DIFY_API_URL = "http://127.0.0.1:1/v1"  # porta fechada
with TestClient(app) as c:
    entrar(c, "admin.ti")
    agentes = c.get("/api/admin/agentes").json()["itens"]
    ok(len(agentes) == 1 and agentes[0]["nome"] == settings.APP_NAME and agentes[0]["qtd_usuarios"] == 3,
       "cadastra com o nome padrão e mantém o acesso de todos")
settings.DIFY_API_URL = url_original
with TestClient(app) as c:
    entrar(c, "maria.silva")
    aid = c.get("/api/agentes").json()["itens"][0]["id"]
    with c.stream("POST", f"/api/agentes/{aid}/chat", json={"pergunta": "oi"}, headers=H) as s:
        ok('"t": "fim"' in "".join(s.iter_text()), "quando o Dify volta, o chat funciona")

print("== instalação nova sem DIFY_API_KEY")
db.DB_PATH = os.path.join(DATA_DIR, "nova.db")
settings.DIFY_API_KEY = ""
with TestClient(app) as c:
    ok(entrar(c, "admin.ti") == 200, "admin inicial entra")
    ok(c.get("/api/admin/agentes").json()["itens"] == [], "nenhum agente cadastrado")
    r = c.post("/api/admin/agentes", json={"chave": fake_dify.CHAVE_COMERCIAL}, headers=H)
    ok(r.status_code == 200 and [a["id"] for a in c.get("/api/agentes").json()["itens"]] == [r.json()["id"]],
       "admin cadastra o primeiro agente e já pode usar")
ok(db.get_meta("migracao_agentes") is not None, "migração marcada como feita")

print("== banco da versão com vários agentes (sem as colunas de conhecimento)")
db.DB_PATH = os.path.join(DATA_DIR, "v2.db")
c = sqlite3.connect(db.DB_PATH)
c.executescript(SCHEMA_ANTIGO + """
CREATE TABLE agentes (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, descricao TEXT NOT NULL DEFAULT '',
    icone TEXT NOT NULL DEFAULT '', cor TEXT NOT NULL DEFAULT '', modo TEXT NOT NULL DEFAULT '', chave TEXT NOT NULL UNIQUE,
    sugestoes TEXT NOT NULL DEFAULT '[]', ativo INTEGER NOT NULL DEFAULT 1, criado_em TEXT NOT NULL, criado_por TEXT);
CREATE TABLE usuario_agentes (username TEXT NOT NULL, agente_id INTEGER NOT NULL, PRIMARY KEY (username, agente_id));
CREATE TABLE meta (chave TEXT PRIMARY KEY, valor TEXT);
""")
c.execute("INSERT INTO usuarios (username, nome, perfil, ativo, criado_em) VALUES ('admin.ti','Admin','admin',1,'2026-10-01')")
c.execute("INSERT INTO agentes (nome, chave, criado_em) VALUES ('Agente Comercial', ?, '2026-10-01')", (fake_dify.CHAVE_COMERCIAL,))
c.execute("INSERT INTO usuario_agentes VALUES ('admin.ti', 1)")
c.execute("INSERT INTO meta VALUES ('migracao_agentes', '2026-10-01')")
c.commit()
c.close()
with TestClient(app) as c:
    entrar(c, "admin.ti")
    a = c.get("/api/admin/agentes").json()["itens"]
    ok(len(a) == 1 and a[0]["recebe_conhecimento"] == 0 and a[0]["autoaprovacao"] == 1 and a[0]["dataset_id"] is None,
       "colunas novas acrescentadas (não recebe conhecimento até ligar; autoaprovação ligada)")
    ok([x["id"] for x in c.get("/api/agentes").json()["itens"]] == [1], "agente e liberações antigas intactos")
    ok(c.get("/api/conhecimento/meus").json()["itens"] == [], "tabelas de conhecimento criadas")

print("\nfalhas:", falhas)
sys.exit(1 if falhas else 0)
