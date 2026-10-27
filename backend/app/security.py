"""Sessão (cookie HttpOnly com JWT), proteção CSRF e limite de tentativas de login."""
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request

from . import db
from .config import settings

COOKIE = "agente_sessao"
ALG = "HS256"


def criar_token(username: str) -> str:
    agora = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": agora, "exp": agora + timedelta(hours=settings.SESSION_HOURS)}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALG)


def definir_cookie(resp, token: str) -> None:
    resp.set_cookie(COOKIE, token, httponly=True, secure=settings.COOKIE_SECURE, samesite="strict",
                    max_age=settings.SESSION_HOURS * 3600, path="/")


def limpar_cookie(resp) -> None:
    resp.delete_cookie(COOKIE, path="/")


def usuario_atual(request: Request) -> dict:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Sessão expirada. Entre novamente.")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALG])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sessão expirada. Entre novamente.")
    user = db.get_user(payload.get("sub", ""))
    # o cadastro é conferido a cada requisição: desativar no admin corta o acesso na hora
    if not user or not user["ativo"]:
        raise HTTPException(401, "Seu acesso a esta aplicação foi removido.")
    return user


def exigir_admin(user: dict = Depends(usuario_atual)) -> dict:
    if user["perfil"] != "admin":
        raise HTTPException(403, "Somente administradores.")
    return user


def checar_csrf(request: Request) -> None:
    """Requisições que alteram dados precisam do cabeçalho enviado pelo próprio frontend."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-requested-with") != "agente-pu":
        raise HTTPException(403, "Requisição recusada.")


class LimiteLogin:
    """Bloqueia temporariamente após várias senhas erradas (por usuário e por IP)."""

    def __init__(self):
        self.falhas: dict[str, list[float]] = {}

    def _chaves(self, username: str, ip: str):
        return [f"u:{username}", f"ip:{ip}"]

    def bloqueado(self, username: str, ip: str) -> int:
        janela = settings.LOGIN_LOCK_MINUTES * 60
        agora = time.time()
        restante = 0
        for k in self._chaves(username, ip):
            lst = [t for t in self.falhas.get(k, []) if agora - t < janela]
            self.falhas[k] = lst
            limite = settings.LOGIN_MAX_ATTEMPTS * (4 if k.startswith("ip:") else 1)
            if len(lst) >= limite:
                restante = max(restante, int(janela - (agora - lst[0])))
        return restante

    def falhou(self, username: str, ip: str) -> None:
        for k in self._chaves(username, ip):
            self.falhas.setdefault(k, []).append(time.time())

    def sucesso(self, username: str) -> None:
        self.falhas.pop(f"u:{username}", None)


limite_login = LimiteLogin()
