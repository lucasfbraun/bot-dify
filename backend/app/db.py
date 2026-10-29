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
-- quem aprova conhecimento enviado para cada agente
CREATE TABLE IF NOT EXISTS aprovadores (
    username   TEXT NOT NULL REFERENCES usuarios(username) ON DELETE CASCADE,
    agente_id  INTEGER NOT NULL REFERENCES agentes(id) ON DELETE CASCADE,
    PRIMARY KEY (username, agente_id)
);
-- arquivos enviados para virar conhecimento (o arquivo em si fica no disco só até a decisão)
CREATE TABLE IF NOT EXISTS envios (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agente_id        INTEGER,                -- sem FK: o histórico fica mesmo se o agente for removido
    agente_nome      TEXT,
    dataset_id       TEXT,                   -- base de destino no momento do envio
    username         TEXT NOT NULL,
    arquivo_nome     TEXT NOT NULL,
    arquivo_ext      TEXT NOT NULL,
    tamanho          INTEGER NOT NULL,
    sha256           TEXT NOT NULL,
    justificativa    TEXT NOT NULL,
    status           TEXT NOT NULL,          -- processando, pendente, erro, aprovado, rejeitado, cancelado, desativado, excluido
    enviado_nome     TEXT,                   -- nome do arquivo que vai para o Dify (PDF quando convertido)
    convertido       INTEGER NOT NULL DEFAULT 0,
    ocr              TEXT,                   -- aplicado, parcial, falhou, nao_aplicavel
    paginas          INTEGER,
    caracteres       INTEGER,
    trechos          INTEGER,
    alertas          TEXT NOT NULL DEFAULT '[]',
    quarentena_doc   TEXT,
    destino_doc      TEXT,
    destino_batch    TEXT,
    erro             TEXT,
    criado_em        TEXT NOT NULL,
    decidido_por     TEXT,
    decidido_em      TEXT,
    motivo           TEXT,
    autoaprovado     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_envios_status ON envios(status);
-- registro de aprovação (enviado, processado, aprovado, rejeitado, cancelado, desativado, reativado, excluido, erro)
CREATE TABLE IF NOT EXISTS eventos_conhecimento (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    envio_id   INTEGER NOT NULL,
    quando     TEXT NOT NULL,
    autor      TEXT NOT NULL,
    acao       TEXT NOT NULL,
    detalhe    TEXT
);
CREATE INDEX IF NOT EXISTS ix_eventos_conh_quando ON eventos_conhecimento(quando);
"""

# colunas acrescentadas em tabelas que já existiam em versões anteriores (o CREATE IF NOT EXISTS não as cria)
COLUNAS_NOVAS = {
    "agentes": {
        "recebe_conhecimento": "INTEGER NOT NULL DEFAULT 0",
        "autoaprovacao": "INTEGER NOT NULL DEFAULT 1",
        "dataset_id": "TEXT",           # base para os arquivos enviados
        "dataset_feedback_id": "TEXT",  # base para os feedbacks (👍/👎); vazio = a mesma dos arquivos
    },
    # envios que nascem de uma avaliação no chat (👍 resposta validada / 👎 correção)
    "envios": {
        "origem": "TEXT NOT NULL DEFAULT 'arquivo'",   # arquivo, feedback_positivo, feedback_negativo
        "message_id": "TEXT",
        "conversation_id": "TEXT",
        "pergunta": "TEXT",
        "resposta_original": "TEXT",
        "resposta_corrigida": "TEXT",
    },
}


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db() -> None:
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        for tabela, colunas in COLUNAS_NOVAS.items():
            existentes = {r[1] for r in c.execute(f"PRAGMA table_info({tabela})")}
            for nome, tipo in colunas.items():
                if nome not in existentes:
                    c.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")


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
CAMPOS_AGENTE = ("nome", "descricao", "icone", "cor", "modo", "chave", "sugestoes", "ativo", "recebe_conhecimento",
                 "autoaprovacao", "dataset_id", "dataset_feedback_id")


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
    for k in ("recebe_conhecimento", "autoaprovacao"):
        if k in permitidos:
            permitidos[k] = int(bool(permitidos[k]))
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


# ------------------------------------------------------------------ conhecimento (envios com aprovação)
def _envio(r) -> dict | None:
    if not r:
        return None
    e = dict(r)
    try:
        e["alertas"] = json.loads(e.get("alertas") or "[]")
    except ValueError:
        e["alertas"] = []
    return e


def aprovadores_por_usuario() -> dict[str, list[int]]:
    with conn() as c:
        out: dict[str, list[int]] = {}
        for r in c.execute("SELECT username, agente_id FROM aprovadores ORDER BY agente_id"):
            out.setdefault(r[0], []).append(r[1])
        return out


def agentes_que_aprova(username: str) -> list[int]:
    with conn() as c:
        return [r[0] for r in c.execute("SELECT agente_id FROM aprovadores WHERE username = ?", (username.lower(),))]


def aprovadores_do_agente(agente_id: int) -> list[str]:
    with conn() as c:
        return [r[0] for r in c.execute(
            "SELECT a.username FROM aprovadores a JOIN usuarios u ON u.username = a.username "
            "WHERE a.agente_id = ? AND u.ativo = 1 ORDER BY 1", (agente_id,))]


def definir_aprovacoes(username: str, agente_ids: list[int]) -> list[int]:
    with conn() as c:
        validos = {r[0] for r in c.execute("SELECT id FROM agentes")}
        ids = sorted({int(i) for i in agente_ids} & validos)
        c.execute("DELETE FROM aprovadores WHERE username = ?", (username.lower(),))
        c.executemany("INSERT INTO aprovadores (username, agente_id) VALUES (?, ?)", [(username.lower(), i) for i in ids])
        return ids


def criar_envio(**campos) -> int:
    campos = {**campos, "criado_em": agora(), "alertas": json.dumps(campos.get("alertas") or [], ensure_ascii=False)}
    cols = ", ".join(campos)
    with conn() as c:
        return c.execute(f"INSERT INTO envios ({cols}) VALUES ({', '.join('?' * len(campos))})",
                         tuple(campos.values())).lastrowid


CAMPOS_ENVIO = {"status", "enviado_nome", "convertido", "ocr", "paginas", "caracteres", "trechos", "alertas",
                "quarentena_doc", "destino_doc", "destino_batch", "erro", "decidido_por", "decidido_em", "motivo",
                "autoaprovado", "resposta_corrigida", "dataset_id"}


def atualizar_envio(envio_id: int, **campos) -> None:
    campos = {k: v for k, v in campos.items() if k in CAMPOS_ENVIO}
    if "alertas" in campos:
        campos["alertas"] = json.dumps(campos["alertas"] or [], ensure_ascii=False)
    if not campos:
        return
    with conn() as c:
        c.execute(f"UPDATE envios SET {', '.join(f'{k} = ?' for k in campos)} WHERE id = ?", (*campos.values(), envio_id))


def mudar_status_envio(envio_id: int, de: tuple[str, ...], para: str, **campos) -> bool:
    """Troca o status só se ele ainda estiver em `de` (evita dois aprovadores decidindo o mesmo envio)."""
    campos = {k: v for k, v in campos.items() if k in CAMPOS_ENVIO}
    if "alertas" in campos:
        campos["alertas"] = json.dumps(campos["alertas"] or [], ensure_ascii=False)
    sets = ", ".join(["status = ?"] + [f"{k} = ?" for k in campos])
    with conn() as c:
        cur = c.execute(f"UPDATE envios SET {sets} WHERE id = ? AND status IN ({', '.join('?' * len(de))})",
                        (para, *campos.values(), envio_id, *de))
        return cur.rowcount == 1


def get_envio(envio_id: int) -> dict | None:
    with conn() as c:
        return _envio(c.execute("SELECT * FROM envios WHERE id = ?", (envio_id,)).fetchone())


def envio_do_feedback(message_id: str, username: str, origem: str, status: tuple[str, ...]) -> dict | None:
    """Envio da mesma resposta, da mesma pessoa e do mesmo tipo, numa das situações informadas."""
    with conn() as c:
        return _envio(c.execute(
            f"SELECT * FROM envios WHERE message_id = ? AND username = ? AND origem = ? "
            f"AND status IN ({', '.join('?' * len(status))}) ORDER BY id DESC LIMIT 1",
            (message_id, username.lower(), origem, *status)).fetchone())


def listar_envios(status: list[str] | None = None, agente_ids: list[int] | None = None, username: str | None = None,
                  inicio: str | None = None, fim: str | None = None, limite: int = 500,
                  origem: str | None = None) -> list[dict]:
    """agente_ids=None: todos os agentes; lista vazia: nenhum (aprovador sem agentes)."""
    cond, args = [], []
    if origem:
        cond.append("origem = ?")
        args.append(origem)
    if status:
        cond.append(f"status IN ({', '.join('?' * len(status))})")
        args += status
    if agente_ids is not None:
        if not agente_ids:
            return []
        cond.append(f"agente_id IN ({', '.join('?' * len(agente_ids))})")
        args += agente_ids
    if username:
        cond.append("username = ?")
        args.append(username.lower())
    if inicio:
        cond.append("criado_em >= ?")
        args.append(inicio)
    if fim:
        cond.append("criado_em <= ?")
        args.append(fim)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    with conn() as c:
        return [_envio(r) for r in c.execute(f"SELECT * FROM envios{where} ORDER BY id DESC LIMIT ?", (*args, limite))]


def registrar_evento(envio_id: int, autor: str, acao: str, detalhe: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO eventos_conhecimento (envio_id, quando, autor, acao, detalhe) VALUES (?,?,?,?,?)",
                  (envio_id, agora(), autor, acao, (detalhe or "")[:1000]))


def eventos_do_envio(envio_id: int) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM eventos_conhecimento WHERE envio_id = ? ORDER BY id",
                                           (envio_id,))]


def listar_eventos_conhecimento(agente_ids: list[int] | None = None, usuario: str | None = None,
                                agente_id: int | None = None, inicio: str | None = None, fim: str | None = None,
                                acao: str | None = None, autoaprovado: bool | None = None,
                                limite: int = 2000) -> tuple[list[dict], bool]:
    """Registro de aprovação com os dados do envio. `usuario` casa com quem enviou OU com quem fez a ação."""
    cond, args = [], []
    if agente_ids is not None:
        if not agente_ids:
            return [], False
        cond.append(f"e.agente_id IN ({', '.join('?' * len(agente_ids))})")
        args += agente_ids
    if agente_id:
        cond.append("e.agente_id = ?")
        args.append(agente_id)
    if usuario:
        cond.append("(e.username = ? OR v.autor = ?)")
        args += [usuario.lower(), usuario.lower()]
    if inicio:
        cond.append("v.quando >= ?")
        args.append(inicio)
    if fim:
        cond.append("v.quando <= ?")
        args.append(fim)
    if acao:
        cond.append("v.acao = ?")
        args.append(acao)
    if autoaprovado is not None:
        cond.append("e.autoaprovado = ?")
        args.append(int(autoaprovado))
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    sql = (f"SELECT v.id, v.envio_id, v.quando, v.autor, v.acao, v.detalhe, e.agente_id, e.agente_nome, e.username, "
           f"e.arquivo_nome, e.status, e.autoaprovado, e.sha256, e.origem FROM eventos_conhecimento v "
           f"JOIN envios e ON e.id = v.envio_id{where} ORDER BY v.id DESC LIMIT ?")
    with conn() as c:
        itens = [dict(r) for r in c.execute(sql, (*args, limite + 1))]
    return itens[:limite], len(itens) > limite
