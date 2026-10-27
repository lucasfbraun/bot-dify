"""Sobe o app localmente com AD falso (MOCK) e Dify falso, para testar sem a rede da empresa.

    python tests/dev_server.py        -> app em http://127.0.0.1:8088 e Dify falso em :8099
"""
import os
import sys
import tempfile
import threading

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(AQUI, "..", "backend"), AQUI]
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="agente-pu-"))
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("SECRET_KEY", "chave-so-para-teste-local")
os.environ.setdefault("INITIAL_ADMINS", "admin.ti")
os.environ.setdefault("LDAP_BIND_USER", "svc.integracao@flexivel.local")
os.environ.setdefault("LDAP_BIND_PASSWORD", "Teste@123")
os.environ.setdefault("DIFY_API_URL", "http://127.0.0.1:8099/v1")
os.environ.setdefault("DIFY_API_KEY", "app-teste")

import uvicorn  # noqa: E402
from ldap3 import MOCK_SYNC  # noqa: E402

import mock_ldap  # noqa: E402
from app import ldap_auth  # noqa: E402

srv = mock_ldap.criar_servidor()
ldap_auth._server_factory = lambda: srv
ldap_auth._conn_kwargs = {"client_strategy": MOCK_SYNC}

threading.Thread(target=lambda: uvicorn.run("fake_dify:app", host="127.0.0.1", port=8099, log_level="warning"),
                 daemon=True).start()
uvicorn.run("app.main:app", host="127.0.0.1", port=8088, log_level="info")
