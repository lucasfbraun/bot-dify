"""Banco local (SQLite) com o cadastro de quem pode acessar, os agentes do Dify e o registro de acessos."""
import json
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
-- agentes (apps de chat do Dify); a chave app-... nunca sai do servidor
CREATE TABLE IF NOT EXISTS agentes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nome          TEXT NOT NULL,             -- nome exibido (começa com o nome do app no Dify)
    descricao     TEXT NOT NULL DEFAULT '',
    icone         TEXT NOT NULL DEFAULT '',  -- emoji configurado no Dify
    cor           TEXT NOT NULL DEFAULT '',  -- fundo do emoji
    modo          TEXT NOT NULL DEFAULT '',  -- chat, agent-chat ou advanced-chat
    chave         TEXT NOT NULL UNIQUE,
    sugestoes     TEXT NOT NULL DEFAULT '[]', -- JSON [{cat, txt}], usadas quando o Dify não tem perguntas sugeridas
    ativo         INTEGER NOT NULL DEFAULT 1,
    criado_em     TEXT NOT NULL,
    criado_por    TEXT
);
-- quais agentes cada usuário pode usar
CREATE TABLE IF NOT EXISTS usuario_agentes (
    username   TEXT NOT NULL REFERENCES usuarios(username) ON DELETE CASCADE,
    agente_id  INTEGER NOT NULL REFERENCES agentes(id) ON DELETE CASCADE,
    PRIMARY KEY (username, agente_id)
);
CREATE TABLE IF NOT EXISTS meta (
    chave  TEXT PRIMARY KEY,
    valor  TEXT
);
-- uso dos agentes (só metadados: o conteúdo das conversas fica no Dify)
CREATE TABLE IF NOT EXISTS uso (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    quando           TEXT NOT NULL,
    username         TEXT NOT NULL,
    agente_id        INTEGER,               -- sem FK: o registro fica mesmo se o agente for removido
    agente_nome      TEXT,                  -- nome na época
    evento           TEXT NOT NULL,         -- pergunta, erro, audio
    conversation_id  TEXT,
    detalhe          TEXT
);
CREATE INDEX IF NOT EXISTS ix_uso_quando ON uso(quando);
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
        c.execute("PRAGMA foreign_keys = ON")
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


def registrar_uso(username: str, agente_id: int, agente_nome: str, evento: str, conversation_id: str | None = None,
                  detalhe: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO uso (quando, username, agente_id, agente_nome, evento, conversation_id, detalhe) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (agora(), username.lower(), agente_id, agente_nome, evento, conversation_id, (detalhe or "")[:300]))


def listar_registros(usuario: str | None = None, agente_id: int | None = None, inicio: str | None = None,
                     fim: str | None = None, tipo: str = "todos", limite: int = 500) -> tuple[list[dict], bool]:
    """Logins (tabela acessos) e uso dos agentes (tabela uso) juntos, do mais novo para o mais antigo.
    inicio/fim são datas ISO em UTC (mesmo formato gravado, então a comparação de texto funciona).
    Com filtro de agente só entra o uso, porque login não pertence a nenhum agente.
    Devolve (itens, cortado) — cortado indica que havia mais registros que o limite."""
    def filtros(col_agente: bool):
        cond, args = [], []
        if usuario:
            cond.append("username = ?")
            args.append(usuario.lower())
        if inicio:
            cond.append("quando >= ?")
            args.append(inicio)
        if fim:
            cond.append("quando <= ?")
            args.append(fim)
        if col_agente and agente_id:
            cond.append("agente_id = ?")
            args.append(agente_id)
        return (" WHERE " + " AND ".join(cond)) if cond else "", args

    partes, args = [], []
    if tipo in ("todos", "acessos") and not agente_id:
        w, a = filtros(False)
        partes.append("SELECT 'acesso' AS tipo, quando, username, NULL AS agente_id, NULL AS agente_nome, "
                      f"resultado AS evento, NULL AS conversation_id, ip, detalhe FROM acessos{w}")
        args += a
    if tipo in ("todos", "uso"):
        w, a = filtros(True)
        partes.append("SELECT 'uso' AS tipo, quando, username, agente_id, agente_nome, evento, conversation_id, "
                      f"NULL AS ip, detalhe FROM uso{w}")
        args += a
    if not partes:
        return [], False
    sql = " UNION ALL ".join(partes) + " ORDER BY quando DESC LIMIT ?"
    with conn() as c:
        itens = [dict(r) for r in c.execute(sql, (*args, limite + 1))]
    return itens[:limite], len(itens) > limite


def usuarios_conhecidos() -> list[str]:
    """Cadastrados hoje + quem já usou algum agente (as conversas de quem foi removido continuam no Dify)."""
    with conn() as c:
        return [r[0] for r in c.execute("SELECT username FROM usuarios UNION SELECT username FROM uso ORDER BY 1")]


def auditar(autor: str, acao: str, alvo: str = "", detalhe: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO auditoria (quando, autor, acao, alvo, detalhe) VALUES (?,?,?,?,?)",
                  (agora(), autor, acao, alvo, detalhe[:500]))


# ------------------------------------------------------------------ agentes
CAMPOS_AGENTE = ("nome", "descricao", "icone", "cor", "modo", "chave", "sugestoes", "ativo")


def _agente(r) -> dict | None:
    if not r:
        return None
    a = dict(r)
    try:
        a["sugestoes"] = json.loads(a.get("sugestoes") or "[]")
    except ValueError:
        a["sugestoes"] = []
    return a


def get_meta(chave: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT valor FROM meta WHERE chave = ?", (chave,)).fetchone()
        return r[0] if r else None


def set_meta(chave: str, valor: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO meta (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                  (chave, valor))


def get_agente(agente_id: int) -> dict | None:
    with conn() as c:
        return _agente(c.execute("SELECT * FROM agentes WHERE id = ?", (agente_id,)).fetchone())


def get_agente_por_chave(chave: str) -> dict | None:
    with conn() as c:
        return _agente(c.execute("SELECT * FROM agentes WHERE chave = ?", (chave,)).fetchone())


def list_agentes() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """SELECT a.*, (SELECT COUNT(*) FROM usuario_agentes ua WHERE ua.agente_id = a.id) AS qtd_usuarios
               FROM agentes a ORDER BY a.nome COLLATE NOCASE, a.id""").fetchall()
        return [_agente(r) for r in rows]


def criar_agente(nome: str, descricao: str, icone: str, cor: str, modo: str, chave: str, autor: str,
                 sugestoes: list | None = None) -> int:
    """Cadastra o agente e já libera para todos os administradores (eles podem desmarcar depois)."""
    with conn() as c:
        cur = c.execute(
            """INSERT INTO agentes (nome, descricao, icone, cor, modo, chave, sugestoes, criado_em, criado_por)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (nome, descricao, icone, cor, modo, chave, json.dumps(sugestoes or [], ensure_ascii=False), agora(), autor))
        aid = cur.lastrowid
        c.execute("INSERT OR IGNORE INTO usuario_agentes (username, agente_id) SELECT username, ? FROM usuarios "
                  "WHERE perfil = 'admin'", (aid,))
        return aid


def update_agente(agente_id: int, **campos) -> None:
    permitidos = {k: v for k, v in campos.items() if k in CAMPOS_AGENTE}
    if "sugestoes" in permitidos:
        permitidos["sugestoes"] = json.dumps(permitidos["sugestoes"] or [], ensure_ascii=False)
    if "ativo" in permitidos:
        permitidos["ativo"] = int(bool(permitidos["ativo"]))
    if not permitidos:
        return
    sets = ", ".join(f"{k} = ?" for k in permitidos)
    with conn() as c:
        c.execute(f"UPDATE agentes SET {sets} WHERE id = ?", (*permitidos.values(), agente_id))


def delete_agente(agente_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM agentes WHERE id = ?", (agente_id,))


def agentes_do_usuario(username: str, so_ativos: bool = True) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            f"""SELECT a.* FROM agentes a JOIN usuario_agentes ua ON ua.agente_id = a.id
                WHERE ua.username = ? {"AND a.ativo = 1" if so_ativos else ""}
                ORDER BY a.nome COLLATE NOCASE, a.id""", (username.lower(),)).fetchall()
        return [_agente(r) for r in rows]


def ids_agentes_por_usuario() -> dict[str, list[int]]:
    with conn() as c:
        out: dict[str, list[int]] = {}
        for r in c.execute("SELECT username, agente_id FROM usuario_agentes ORDER BY agente_id"):
            out.setdefault(r[0], []).append(r[1])
        return out


def tem_acesso(username: str, agente_id: int) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM usuario_agentes WHERE username = ? AND agente_id = ?",
                         (username.lower(), agente_id)).fetchone() is not None


def definir_agentes_usuario(username: str, agente_ids: list[int]) -> list[int]:
    """Troca a lista de agentes do usuário; ids que não existem são ignorados. Devolve a lista gravada."""
    with conn() as c:
        validos = {r[0] for r in c.execute("SELECT id FROM agentes")}
        ids = sorted({int(i) for i in agente_ids} & validos)
        c.execute("DELETE FROM usuario_agentes WHERE username = ?", (username.lower(),))
        c.executemany("INSERT INTO usuario_agentes (username, agente_id) VALUES (?, ?)",
                      [(username.lower(), i) for i in ids])
        return ids


def liberar_todos_agentes(username: str) -> None:
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO usuario_agentes (username, agente_id) SELECT ?, id FROM agentes",
                  (username.lower(),))


def liberar_agente_para_todos(agente_id: int) -> int:
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO usuario_agentes (username, agente_id) SELECT username, ? FROM usuarios",
                        (agente_id,))
        return cur.rowcount
