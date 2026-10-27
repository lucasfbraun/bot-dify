"""Autenticação no Active Directory (LDAP) e busca de usuários para o cadastro."""
import ssl

from ldap3 import NONE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars

from .config import settings

ATRIBUTOS = ["sAMAccountName", "displayName", "givenName", "sn", "mail", "department", "title",
             "userAccountControl", "userPrincipalName", "distinguishedName", "lockoutTime"]

# Permite trocar a fábrica de conexões nos testes (ldap3 MOCK_SYNC)
_server_factory = None
_conn_kwargs: dict = {}


class LdapErro(Exception):
    """Falha de comunicação com o AD (não é senha errada)."""


def normalizar_usuario(login: str) -> str:
    """Aceita 'usuario', 'FLEXIVEL\\usuario' ou 'usuario@flexivel.local' e devolve o sAMAccountName."""
    u = (login or "").strip()
    if "\\" in u:
        u = u.split("\\", 1)[1]
    if "@" in u:
        u = u.split("@", 1)[0]
    return u.lower()


def _server() -> Server:
    if _server_factory:
        return _server_factory()
    tls = None
    if settings.LDAP_USE_SSL or settings.LDAP_START_TLS:
        tls = Tls(validate=ssl.CERT_REQUIRED if settings.LDAP_TLS_VERIFY else ssl.CERT_NONE)
    return Server(settings.LDAP_SERVER, port=settings.LDAP_PORT, use_ssl=settings.LDAP_USE_SSL,
                  tls=tls, get_info=NONE, connect_timeout=settings.LDAP_TIMEOUT)


def _bind(user: str, password: str) -> Connection:
    c = Connection(_server(), user=user, password=password, receive_timeout=settings.LDAP_TIMEOUT,
                   raise_exceptions=False, **_conn_kwargs)
    c.open()
    if settings.LDAP_START_TLS and not settings.LDAP_USE_SSL and not _server_factory:
        c.start_tls()
    if not c.bind():
        raise LDAPBindError(str(c.result.get("description", "bind falhou")))
    return c


def _conta_servico() -> Connection:
    if not settings.LDAP_BIND_USER:
        raise LdapErro("LDAP_BIND_USER não configurado")
    try:
        return _bind(settings.LDAP_BIND_USER, settings.LDAP_BIND_PASSWORD)
    except LDAPBindError as e:
        raise LdapErro(f"conta de serviço do LDAP recusada: {e}") from e
    except LDAPException as e:
        raise LdapErro(f"sem comunicação com o AD: {e}") from e


def _val(entry, attr):
    # o ldap3 devolve CaseInsensitiveDict (não é subclasse de dict)
    v = entry.get(attr) if hasattr(entry, "get") else None
    if isinstance(v, list):
        v = v[0] if v else None
    return v


def _para_dict(attrs: dict) -> dict:
    uac = _val(attrs, "userAccountControl")
    try:
        uac = int(uac or 0)
    except (TypeError, ValueError):
        uac = 0
    lockout = _val(attrs, "lockoutTime")
    bloqueado = False
    try:
        bloqueado = bool(lockout) and int(lockout.timestamp() if hasattr(lockout, "timestamp") else lockout) > 0
    except (TypeError, ValueError, OSError):
        bloqueado = False
    nome = _val(attrs, "displayName") or " ".join(filter(None, [_val(attrs, "givenName"), _val(attrs, "sn")]))
    return {
        "username": (_val(attrs, "sAMAccountName") or "").lower(),
        "nome": nome or (_val(attrs, "sAMAccountName") or ""),
        "email": _val(attrs, "mail") or "",
        "departamento": _val(attrs, "department") or "",
        "cargo": _val(attrs, "title") or "",
        "dn": _val(attrs, "distinguishedName") or "",
        "upn": _val(attrs, "userPrincipalName") or "",
        "desabilitado": bool(uac & 2),
        "bloqueado": bloqueado,
    }


def buscar_usuario(username: str) -> dict | None:
    """Procura um usuário do AD pelo sAMAccountName (usando a conta de serviço)."""
    sam = escape_filter_chars(normalizar_usuario(username))
    c = _conta_servico()
    try:
        c.search(settings.LDAP_BASE_DN, f"(&(objectCategory=person)(objectClass=user)(sAMAccountName={sam}))",
                 SUBTREE, attributes=ATRIBUTOS, size_limit=2)
        if not c.entries and not c.response:
            return None
        for r in c.response or []:
            if r.get("type") == "searchResEntry" or "attributes" in r:
                attrs = r.get("attributes") or {}
                if attrs:
                    d = _para_dict(attrs)
                    d["dn"] = d["dn"] or r.get("dn", "")
                    return d
        return None
    finally:
        c.unbind()


def pesquisar(termo: str, limite: int = 20) -> list[dict]:
    """Pesquisa usuários ativos do AD por login, nome ou e-mail (para o cadastro)."""
    t = escape_filter_chars((termo or "").strip())
    if len(t) < 2:
        return []
    filtro = (f"(&(objectCategory=person)(objectClass=user)"
              f"(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
              f"(|(sAMAccountName={t}*)(displayName=*{t}*)(mail={t}*)(givenName={t}*)(sn={t}*)))")
    c = _conta_servico()
    try:
        c.search(settings.LDAP_BASE_DN, filtro, SUBTREE, attributes=ATRIBUTOS, size_limit=limite)
        out = []
        for r in c.response or []:
            attrs = r.get("attributes") or {}
            if attrs:
                d = _para_dict(attrs)
                if d["username"] and not d["desabilitado"]:
                    out.append(d)
        return sorted(out, key=lambda x: x["nome"].lower())[:limite]
    finally:
        c.unbind()


def autenticar(username: str, senha: str) -> dict:
    """Valida usuário e senha no AD.

    Retorna os dados do usuário. Lança PermissionError com o motivo
    ('senha_invalida', 'desabilitado', 'bloqueado', 'nao_encontrado') ou LdapErro.
    """
    if not senha:  # senha vazia faria um bind anônimo "com sucesso"
        raise PermissionError("senha_invalida")
    dados = buscar_usuario(username)
    if not dados:
        raise PermissionError("nao_encontrado")
    if dados["desabilitado"]:
        raise PermissionError("desabilitado")
    login_bind = dados["upn"] or f"{dados['username']}@{settings.LDAP_DOMAIN}"
    try:
        c = _bind(login_bind, senha)
        c.unbind()
    except LDAPBindError:
        raise PermissionError("senha_invalida")
    except LDAPException as e:
        raise LdapErro(f"sem comunicação com o AD: {e}") from e
    return dados
