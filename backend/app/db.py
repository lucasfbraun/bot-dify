"""Banco local (SQLite) com o cadastro de quem pode acessar e o registro de acessos."""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import settings

_lock = threading.Lock()
DB_PATH = os.path.join(settings.DATA_DIR, "agente.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    username      TEXT PRIMARY KEY,          -- sAMAccountName em minúsculas
    nome          TEXT,
    email         TEXT,
    departamento  TEXT,
    cargo         TEXT,
    perfil        TEXT NOT NULL DEFAULT 'usuario',   -- 'usuario' ou 'admin'
    ativo         INTEGER NOT NULL DEFAULT 1,
    criado_em     TEXT NOT NULL,
    criado_por    TEXT,
    ultimo_acesso TEXT,
    qtd_acessos   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS acessos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    username  TEXT,
    quando    TEXT NOT NULL,
    ip        TEXT,
    resultado TEXT NOT NULL,                 -- ok, senha_invalida, nao_cadastrado, inativo, bloqueado, erro_ldap
    detalhe   TEXT
);
CREATE TABLE IF NOT EXISTS auditoria (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    quando   TEXT NOT NULL,
    autor    TEXT NOT NULL,
    acao     TEXT NOT NULL,
    alvo     TEXT,
    detalhe  TEXT
);
CREATE INDEX IF NOT EXISTS ix_acessos_quando ON acessos(quando);
"""


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db() -> None:
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def get_user(username: str):
    with conn() as c:
        r = c.execute("SELECT * FROM usuarios WHERE username = ?", (username.lower(),)).fetchone()
        return dict(r) if r else None


def list_users():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM usuarios ORDER BY nome COLLATE NOCASE, username")]


def count_admins() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM usuarios WHERE perfil='admin' AND ativo=1").fetchone()[0]


def upsert_user(username: str, nome: str, email: str, departamento: str, cargo: str,
                perfil: str, ativo: bool, autor: str) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO usuarios (username, nome, email, departamento, cargo, perfil, ativo, criado_em, criado_por)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(username) DO UPDATE SET nome=excluded.nome, email=excluded.email,
                 departamento=excluded.departamento, cargo=excluded.cargo,
                 perfil=excluded.perfil, ativo=excluded.ativo""",
            (username.lower(), nome, email, departamento, cargo, perfil, int(ativo), agora(), autor),
        )


def update_user(username: str, **campos) -> None:
    permitidos = {k: v for k, v in campos.items() if k in ("perfil", "ativo", "nome", "email", "departamento", "cargo")}
    if not permitidos:
        return
    sets = ", ".join(f"{k} = ?" for k in permitidos)
    with conn() as c:
        c.execute(f"UPDATE usuarios SET {sets} WHERE username = ?", (*permitidos.values(), username.lower()))


def delete_user(username: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM usuarios WHERE username = ?", (username.lower(),))


def registrar_login_ok(username: str) -> None:
    with conn() as c:
        c.execute("UPDATE usuarios SET ultimo_acesso = ?, qtd_acessos = qtd_acessos + 1 WHERE username = ?",
                  (agora(), username.lower()))


def registrar_acesso(username: str, ip: str, resultado: str, detalhe: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO acessos (username, quando, ip, resultado, detalhe) VALUES (?,?,?,?,?)",
                  (username.lower() if username else None, agora(), ip, resultado, detalhe[:300]))


def listar_acessos(limite: int = 200):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM acessos ORDER BY id DESC LIMIT ?", (limite,))]


def auditar(autor: str, acao: str, alvo: str = "", detalhe: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO auditoria (quando, autor, acao, alvo, detalhe) VALUES (?,?,?,?,?)",
                  (agora(), autor, acao, alvo, detalhe[:500]))
