"""Configuração lida das variáveis de ambiente (arquivo .env no docker compose)."""
import os
import secrets


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "sim", "yes", "on")


class Settings:
    # Aplicação
    APP_NAME = os.getenv("APP_NAME", "Agente Técnico PU")
    APP_SHORT_NAME = os.getenv("APP_SHORT_NAME", "Agente PU")
    DATA_DIR = os.getenv("DATA_DIR", "/data")
    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)
    SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
    COOKIE_SECURE = _bool("COOKIE_SECURE", True)
    # Usuários (sAMAccountName) que viram administradores automaticamente no primeiro login
    INITIAL_ADMINS = [u.strip().lower() for u in os.getenv("INITIAL_ADMINS", "").split(",") if u.strip()]

    # LDAP / Active Directory
    LDAP_SERVER = os.getenv("LDAP_SERVER", "10.1.1.205")
    LDAP_PORT = int(os.getenv("LDAP_PORT", "389"))
    LDAP_USE_SSL = _bool("LDAP_USE_SSL", False)          # LDAPS (porta 636)
    LDAP_START_TLS = _bool("LDAP_START_TLS", False)      # StartTLS na porta 389
    LDAP_TLS_VERIFY = _bool("LDAP_TLS_VERIFY", False)    # validar certificado do AD
    LDAP_DOMAIN = os.getenv("LDAP_DOMAIN", "flexivel.local")
    LDAP_BASE_DN = os.getenv("LDAP_BASE_DN", "DC=flexivel,DC=local")
    LDAP_BIND_USER = os.getenv("LDAP_BIND_USER", "")
    LDAP_BIND_PASSWORD = os.getenv("LDAP_BIND_PASSWORD", "")
    LDAP_TIMEOUT = int(os.getenv("LDAP_TIMEOUT", "8"))

    # Dify
    DIFY_API_URL = os.getenv("DIFY_API_URL", "https://dify.grupoflexivel.com.br/v1").rstrip("/")
    DIFY_API_KEY = os.getenv("DIFY_API_KEY", "")
    DIFY_TIMEOUT = int(os.getenv("DIFY_TIMEOUT", "600"))
    DIFY_VERIFY_TLS = _bool("DIFY_VERIFY_TLS", True)
    # Tamanho máximo do áudio enviado para transcrição (o Dify aceita até 30 MB).
    # O navegador grava em WAV 16 kHz mono: ~1,9 MB por minuto, e limita a gravação a 5 min (~9,6 MB).
    AUDIO_MAX_MB = int(os.getenv("AUDIO_MAX_MB", "12"))
    # Anexos no chat (recurso "Upload de Arquivo" do app no Dify): tamanho máximo e envios por pessoa por hora.
    # Vale o menor entre este limite e o do Dify (15 MB para documentos e 10 MB para imagens, por padrão).
    ANEXO_MAX_MB = int(os.getenv("ANEXO_MAX_MB", "15"))
    ANEXOS_POR_HORA = int(os.getenv("ANEXOS_POR_HORA", "60"))

    # Envio de conhecimento com aprovação
    # Chave de API de Conhecimento do Dify (ds-...): cria as bases colaborativas e envia/apaga documentos
    DIFY_DATASET_API_KEY = os.getenv("DIFY_DATASET_API_KEY", "")
    CONHECIMENTO_MAX_MB = int(os.getenv("CONHECIMENTO_MAX_MB", "15"))   # o Dify aceita 15 MB por padrão
    # conversor isolado (LibreOffice + OCR); vazio = sem conversão (PPT/DOC/imagens ficam indisponíveis)
    CONVERSOR_URL = os.getenv("CONVERSOR_URL", "").rstrip("/")
    CONHECIMENTO_DIR = os.path.join(DATA_DIR, "conhecimento")           # arquivos só até a decisão

    # Registros e exportação de conversas
    FUSO_HORARIO = os.getenv("FUSO_HORARIO", "America/Sao_Paulo")   # filtros por data e horários do TXT
    EXPORTAR_MAX_CONVERSAS = int(os.getenv("EXPORTAR_MAX_CONVERSAS", "1000"))

    # Proteção de login
    LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCK_MINUTES = int(os.getenv("LOGIN_LOCK_MINUTES", "10"))


settings = Settings()
