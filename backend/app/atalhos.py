"""Atalhos pessoais: cada pessoa monta os próprios cards de pergunta, por agente, na tela inicial do chat.

- Os atalhos da pessoa aparecem antes das sugestões padrão do agente (as do Dify ou as cadastradas pelo admin);
  ela pode ocultar as padrão.
- Texto com lacuna entre colchetes (ex.: "Tudo sobre o produto [produto]") não é enviado direto: vai para a caixa
  com a lacuna selecionada, para a pessoa completar.
- Só a própria pessoa vê e mexe nos seus atalhos (e só nos agentes liberados para ela).
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db
from .security import usuario_atual

router = APIRouter()
MAX_ATALHOS = 12
RX_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _agente_liberado(aid: int, user: dict = Depends(usuario_atual)) -> dict:
    ag = db.get_agente(aid)
    if not ag or not ag["ativo"] or not db.tem_acesso(user["username"], aid):
        raise HTTPException(403, "Você não tem acesso a este agente.")
    return ag


class AtalhoIn(BaseModel):
    categoria: str = Field(default="", max_length=40)
    texto: str = Field(min_length=1, max_length=1000)


class OrdemIn(BaseModel):
    ids: list[int] = Field(max_length=MAX_ATALHOS)


class PreferenciasIn(BaseModel):
    ocultar_padrao: bool


def _limpo(texto: str, limite: int) -> str:
    return re.sub(r"[ \t]+", " ", RX_CONTROLE.sub("", texto or "")).strip()[:limite]


def _listar(username: str, aid: int) -> dict:
    with db.conn() as c:
        itens = [{"id": r["id"], "cat": r["categoria"] or "", "txt": r["texto"]} for r in c.execute(
            "SELECT id, categoria, texto FROM usuario_atalhos WHERE username=? AND agente_id=? ORDER BY ordem, id",
            (username, aid))]
        pref = c.execute("SELECT ocultar_padrao FROM usuario_preferencias WHERE username=? AND agente_id=?",
                         (username, aid)).fetchone()
    return {"itens": itens, "ocultar_padrao": bool(pref and pref["ocultar_padrao"]), "maximo": MAX_ATALHOS}


@router.get("/api/agentes/{aid}/atalhos")
def listar_atalhos(ag: dict = Depends(_agente_liberado), user: dict = Depends(usuario_atual)):
    return _listar(user["username"], ag["id"])


@router.post("/api/agentes/{aid}/atalhos")
def criar_atalho(dados: AtalhoIn, ag: dict = Depends(_agente_liberado), user: dict = Depends(usuario_atual)):
    texto, cat = _limpo(dados.texto, 1000), _limpo(dados.categoria, 40)
    if len(texto) < 3:
        raise HTTPException(400, "Escreva a pergunta do atalho.")
    with db.conn() as c:
        qtd, ordem = c.execute("SELECT COUNT(*), COALESCE(MAX(ordem), 0) FROM usuario_atalhos WHERE username=? AND agente_id=?",
                               (user["username"], ag["id"])).fetchone()
        if qtd >= MAX_ATALHOS:
            raise HTTPException(400, f"Você já tem {MAX_ATALHOS} atalhos neste agente. Apague um para criar outro.")
        c.execute("INSERT INTO usuario_atalhos (username, agente_id, categoria, texto, ordem, criado_em) VALUES (?,?,?,?,?,?)",
                  (user["username"], ag["id"], cat, texto, ordem + 1, db.agora()))
    return _listar(user["username"], ag["id"])


@router.put("/api/agentes/{aid}/atalhos/ordem")
def ordenar_atalhos(dados: OrdemIn, ag: dict = Depends(_agente_liberado), user: dict = Depends(usuario_atual)):
    with db.conn() as c:
        meus = {r[0] for r in c.execute("SELECT id FROM usuario_atalhos WHERE username=? AND agente_id=?",
                                        (user["username"], ag["id"]))}
        if set(dados.ids) != meus or len(dados.ids) != len(meus):
            raise HTTPException(400, "A lista precisa ter todos os seus atalhos deste agente.")
        for i, aid_atalho in enumerate(dados.ids, 1):
            c.execute("UPDATE usuario_atalhos SET ordem=? WHERE id=?", (i, aid_atalho))
    return _listar(user["username"], ag["id"])


@router.put("/api/agentes/{aid}/atalhos/preferencias")
def preferencias(dados: PreferenciasIn, ag: dict = Depends(_agente_liberado), user: dict = Depends(usuario_atual)):
    with db.conn() as c:
        c.execute("INSERT INTO usuario_preferencias (username, agente_id, ocultar_padrao) VALUES (?,?,?) "
                  "ON CONFLICT(username, agente_id) DO UPDATE SET ocultar_padrao=excluded.ocultar_padrao",
                  (user["username"], ag["id"], int(dados.ocultar_padrao)))
    return _listar(user["username"], ag["id"])


def _do_usuario(c, atalho_id: int, username: str, aid: int):
    r = c.execute("SELECT id FROM usuario_atalhos WHERE id=? AND username=? AND agente_id=?",
                  (atalho_id, username, aid)).fetchone()
    if not r:
        raise HTTPException(404, "Atalho não encontrado.")


@router.put("/api/agentes/{aid}/atalhos/{atalho_id}")
def editar_atalho(atalho_id: int, dados: AtalhoIn, ag: dict = Depends(_agente_liberado),
                  user: dict = Depends(usuario_atual)):
    texto, cat = _limpo(dados.texto, 1000), _limpo(dados.categoria, 40)
    if len(texto) < 3:
        raise HTTPException(400, "Escreva a pergunta do atalho.")
    with db.conn() as c:
        _do_usuario(c, atalho_id, user["username"], ag["id"])
        c.execute("UPDATE usuario_atalhos SET categoria=?, texto=? WHERE id=?", (cat, texto, atalho_id))
    return _listar(user["username"], ag["id"])


@router.delete("/api/agentes/{aid}/atalhos/{atalho_id}")
def apagar_atalho(atalho_id: int, ag: dict = Depends(_agente_liberado), user: dict = Depends(usuario_atual)):
    with db.conn() as c:
        _do_usuario(c, atalho_id, user["username"], ag["id"])
        c.execute("DELETE FROM usuario_atalhos WHERE id=?", (atalho_id,))
    return _listar(user["username"], ag["id"])
