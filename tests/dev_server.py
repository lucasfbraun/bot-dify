"""Sobe o app localmente com AD falso (MOCK) e Dify falso, para testar sem a rede da empresa.

    python tests/dev_server.py        -> app em http://127.0.0.1:8088 e Dify falso em :8099

Portas: APP_PORT, DIFY_PORT e CONVERSOR_PORT (conversor falso; o de verdade roda no Docker). A DIFY_API_KEY padrão é a do agente PU falso (vira o primeiro agente na migração).
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
CONVERSOR_PORT = int(os.getenv("CONVERSOR_PORT", "8098"))
SAFETYCHEM_PORT = int(os.getenv("SAFETYCHEM_PORT", "8096"))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="agente-pu-"))
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("SECRET_KEY", "chave-so-para-teste-local-com-32-bytes")
os.environ.setdefault("INITIAL_ADMINS", "admin.ti")
os.environ.setdefault("LDAP_BIND_USER", "svc.integracao@flexivel.local")
os.environ.setdefault("LDAP_BIND_PASSWORD", "Teste@123")
os.environ.setdefault("DIFY_API_URL", f"http://127.0.0.1:{DIFY_PORT}/v1")
os.environ.setdefault("CONVERSOR_URL", f"http://127.0.0.1:{CONVERSOR_PORT}")
os.environ.setdefault("CONHECIMENTO_INTERVALO_STATUS", "0.2")
os.environ.setdefault("DOCUMENTOS_CHAVE", "chave-da-ferramenta-de-teste")
os.environ.setdefault("SAFETYCHEM_ROBO_CHAVE", "chave-do-robo-de-teste")
os.environ.setdefault("SAFETYCHEM_URL", f"http://127.0.0.1:{SAFETYCHEM_PORT}")
os.environ.setdefault("SAFETYCHEM_USUARIO", "robo.teste")
os.environ.setdefault("SAFETYCHEM_SENHA", "senha-teste")
os.environ.setdefault("SAFETYCHEM_HORARIO", "")      # nos testes, só pelo botão
os.environ.setdefault("SAFETYCHEM_PAUSA", "0")
os.environ.setdefault("SAFETYCHEM_POR_PAGINA", "2")  # força a paginação com poucos documentos


def _pasta_documentos() -> str:
    """Imita a pasta da rede: Família / Produto / arquivos, com obsoletos e lixo do Windows para ignorar."""
    raiz = os.path.join(os.environ["DATA_DIR"], "doc_produto")
    arquivos = {
        "FLEXX® AG/FLEXX AG 2047/Boletim FLEXX AG 2047.pdf": b"%PDF-1.4 boletim ag 2047",
        "FLEXX® AG/FLEXX AG 2047/Boletim FLEXX AG 2047.doc": bytes.fromhex("d0cf11e0") + b" word",
        "FLEXX® AG/FLEXX AG 2047/FISPQ - FLEXX AG 2047.pdf": b"%PDF-1.4 fispq ag 2047",
        "FLEXX® AG/FLEXX AG 2047/Obsoletos/Boletim FLEXX AG 2047.pdf": b"%PDF-1.4 antigo",
        "FLEXX® AG/FLEXX AG 2047/Thumbs.db": b"x",
        "FLEXX® AG/FLEXX AG 2047/~$oletim FLEXX AG 2047.doc": b"x",
        "FLEXX® AG/FLEXX AG 2068/FLEXX_AG_2068_Boletim Técnico.pdf": b"%PDF-1.4 boletim ag 2068",
        "FLEXX® AG/FLEXX AG 2068/FLEXX_AG_2068_FISPQ.docx": b"PK word",
        "FLEXX® AG/FLEXX AG 2068/Obsoleto/FLEXX_AG_2068_Laboratório.pdf": b"%PDF-1.4 antigo",
        "FLEXX® AG/FLEXX AG 204600/Sumário do OneNote.onetoc2": b"x",
        "FLEXX® HR/FLEXX HR 3530/Boletim FLEXX HR 3530.pdf": b"%PDF-1.4 hr",
        "FLEXX® HR/FLEXX HR 3530/Revisão anterior/Boletim FLEXX HR 3530.pdf": b"%PDF-1.4 antigo",
    }
    for rel, dados in arquivos.items():
        caminho = os.path.join(raiz, *rel.split("/"))
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "wb") as f:
            f.write(dados)
    return raiz


os.environ.setdefault("DOCUMENTOS_DIR", _pasta_documentos())

import uvicorn  # noqa: E402
from ldap3 import MOCK_SYNC  # noqa: E402

import fake_conversor  # noqa: E402
import fake_dify  # noqa: E402
import fake_safetychem  # noqa: E402
import mock_ldap  # noqa: E402

os.environ.setdefault("DIFY_API_KEY", fake_dify.CHAVE_PU)

from app import ldap_auth  # noqa: E402

srv = mock_ldap.criar_servidor()
ldap_auth._server_factory = lambda: srv
ldap_auth._conn_kwargs = {"client_strategy": MOCK_SYNC}

threading.Thread(target=lambda: uvicorn.run(fake_dify.app, host="127.0.0.1", port=DIFY_PORT, log_level="warning"),
                 daemon=True).start()
threading.Thread(target=lambda: uvicorn.run(fake_conversor.app, host="127.0.0.1", port=CONVERSOR_PORT,
                                           log_level="warning"), daemon=True).start()
threading.Thread(target=lambda: uvicorn.run(fake_safetychem.app, host="127.0.0.1", port=SAFETYCHEM_PORT,
                                           log_level="warning"), daemon=True).start()
# a migração consulta o Dify na subida: espera o Dify falso aceitar conexões
for porta in (DIFY_PORT, CONVERSOR_PORT, SAFETYCHEM_PORT):
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", porta), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
uvicorn.run("app.main:app", host="127.0.0.1", port=APP_PORT, log_level="info")
