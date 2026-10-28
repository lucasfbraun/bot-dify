"""Sobe o app localmente com AD falso (MOCK) e Dify falso, para testar sem a rede da empresa.

    python tests/dev_server.py        -> app em http://127.0.0.1:8088 e Dify falso em :8099

Portas: APP_PORT e DIFY_PORT. A DIFY_API_KEY padrão é a do agente PU falso (vira o primeiro agente na migração).
"""
import os
import socket
import sys
import tempfile
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(AQUI, "..", "backend"), AQUI]
APP_PORT = int(os.getenv("APP_PORT", "8088"))
DIFY_PORT = int(os.getenv("DIFY_PORT", "8099"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="agente-pu-"))
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("SECRET_KEY", "chave-so-para-teste-local-com-32-bytes")
os.environ.setdefault("INITIAL_ADMINS", "admin.ti")
os.environ.setdefault("LDAP_BIND_USER", "svc.integracao@flexivel.local")
os.environ.setdefault("LDAP_BIND_PASSWORD", "Teste@123")
os.environ.setdefault("DIFY_API_URL", f"http://127.0.0.1:{DIFY_PORT}/v1")

import uvicorn  # noqa: E402
from ldap3 import MOCK_SYNC  # noqa: E402

import fake_dify  # noqa: E402
import mock_ldap  # noqa: E402

os.environ.setdefault("DIFY_API_KEY", fake_dify.CHAVE_PU)

from app import ldap_auth  # noqa: E402

srv = mock_ldap.criar_servidor()
ldap_auth._server_factory = lambda: srv
ldap_auth._conn_kwargs = {"client_strategy": MOCK_SYNC}

threading.Thread(target=lambda: uvicorn.run(fake_dify.app, host="127.0.0.1", port=DIFY_PORT, log_level="warning"),
                 daemon=True).start()
# a migração consulta o Dify na subida: espera o Dify falso aceitar conexões
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", DIFY_PORT), timeout=0.2).close()
        break
    except OSError:
        time.sleep(0.1)
uvicorn.run("app.main:app", host="127.0.0.1", port=APP_PORT, log_level="info")
