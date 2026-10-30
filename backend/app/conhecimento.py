"""Envio de conhecimento com aprovação.

Fluxo: o usuário envia um arquivo para um agente -> o arquivo é validado, convertido para PDF com OCR quando
preciso (máxima extração) e vai para a QUARENTENA (base do Dify que nenhum agente consulta, em modo econômico,
sem custo de embeddings) -> a plataforma lê os trechos e gera alertas (instruções suspeitas, dados pessoais,
pouco texto) -> um aprovador do agente aprova (o arquivo vai para a base colaborativa do agente) ou rejeita.
Só administradores desativam, reativam ou excluem um conhecimento aprovado. Tudo fica no registro de aprovação.
"""
import asyncio
import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
import shutil
import time
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import db, dify, ldap_auth, tempo
from .config import settings
from .security import exigir_admin, usuario_atual

log = logging.getLogger("agente.conhecimento")
router = APIRouter()

# extensão -> (tipo MIME, tratamento). "conversor": PDF + OCR; "auto": DOCX vai pelo conversor se tiver imagens.
TIPOS = {
    "pdf": ("application/pdf", "conversor"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "auto"),
    "doc": ("application/msword", "conversor"),
    "rtf": ("application/rtf", "conversor"),
    "odt": ("application/vnd.oasis.opendocument.text", "conversor"),
    "ppt": ("application/vnd.ms-powerpoint", "conversor"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "conversor"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "direto"),
    "xls": ("application/vnd.ms-excel", "direto"),
    "csv": ("text/csv", "direto"),
    "txt": ("text/plain", "direto"),
    "md": ("text/markdown", "direto"),
    "jpg": ("image/jpeg", "conversor"),
    "jpeg": ("image/jpeg", "conversor"),
    "png": ("image/png", "conversor"),
}
PRECISA_CONVERSOR = {e for e, (_, t) in TIPOS.items() if t == "conversor" and e != "pdf"}
STATUS_ABERTOS = ("processando", "pendente", "erro")
INTERVALO_STATUS = float(os.getenv("CONHECIMENTO_INTERVALO_STATUS", "2"))  # segundos entre consultas ao Dify
ESPERA_MAXIMA = int(os.getenv("CONHECIMENTO_ESPERA_MAXIMA", "1200"))       # processamento no Dify (s)
ENVIOS_POR_HORA = int(os.getenv("CONHECIMENTO_ENVIOS_POR_HORA", "30"))
RX_ID_DIFY = re.compile(r"[A-Za-z0-9-]{8,64}")


class ErroProcessamento(Exception):
    pass


# ------------------------------------------------------------------ validação do arquivo
def nome_seguro(nome: str) -> str:
    """Só o nome (sem pastas), sem caracteres de controle ou reservados, até 150 caracteres."""
    nome = unquote(nome or "").replace("\\", "/").split("/")[-1]
    nome = re.sub(r'[\x00-\x1f<>:"|?*]', "", nome).strip(" .")
    if "." not in nome:
        return ""
    base, ext = nome.rsplit(".", 1)
    return f"{base[:140].strip() or 'arquivo'}.{ext.lower()[:5]}"


def _zip(dados: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        raise HTTPException(415, "O arquivo está corrompido ou não é do tipo indicado pela extensão.")
    infos = z.infolist()
    if len(infos) > 10000 or sum(i.file_size for i in infos) > 500 * 1024 * 1024:
        raise HTTPException(415, "Arquivo recusado: conteúdo compactado grande demais (possível bomba de compressão).")
    if any(i.filename.lower().endswith("vbaproject.bin") for i in infos):
        raise HTTPException(415, "Arquivo recusado: contém macros. Salve sem macros (por exemplo, .docx em vez de .docm).")
    return z


def validar_conteudo(dados: bytes, ext: str) -> dict:
    """Confere se o conteúdo bate com a extensão (não só o nome). Devolve detalhes úteis (ex.: DOCX com imagens)."""
    errado = HTTPException(415, "O conteúdo do arquivo não corresponde à extensão .%s." % ext)
    cab = dados[:8]
    if ext == "pdf":
        if b"%PDF-" not in dados[:1024]:
            raise errado
    elif ext in ("docx", "xlsx", "pptx"):
        marcador = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}[ext]
        nomes = _zip(dados).namelist()
        if marcador not in nomes:
            raise errado
        return {"imagens": sum(1 for n in nomes if re.match(r"(word|ppt)/media/", n))}
    elif ext == "odt":
        z = _zip(dados)
        try:
            if z.read("mimetype").strip() != b"application/vnd.oasis.opendocument.text":
                raise errado
        except KeyError:
            raise errado
    elif ext in ("doc", "xls", "ppt"):
        if cab != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise errado
    elif ext == "rtf":
        if not dados.lstrip()[:5] == b"{\\rtf":
            raise errado
    elif ext == "png":
        if not dados.startswith(b"\x89PNG\r\n\x1a\n"):
            raise errado
    elif ext in ("jpg", "jpeg"):
        if not dados.startswith(b"\xff\xd8\xff"):
            raise errado
    elif ext in ("txt", "csv", "md"):
        if b"\x00" in dados[:65536]:
            raise errado
    return {}


# ------------------------------------------------------------------ análise dos trechos (alertas para o aprovador)
_INJECAO = [re.compile(p, re.I) for p in (
    r"(ignore|ignorar|desconsidere|desconsiderar|esque[cç]a)\s+(todas\s+)?(as\s+|suas\s+)?(instru[cç][oõ]es|regras|orienta[cç][oõ]es|diretrizes)",
    r"(ignore|disregard|forget)\s+(all\s+)?(the\s+)?(previous|prior|above|your)\s+(instructions|rules|prompts?)",
    r"\b(system\s*prompt|prompt\s+do\s+sistema|instru[cç][oõ]es\s+do\s+sistema)\b",
    r"\byou\s+are\s+now\b|\bvoc[eê]\s+agora\s+[eé]\b|\ba\s+partir\s+de\s+agora\s+voc[eê]\b",
    r"\b(aja|atue|comporte-se)\s+como\b|\bact\s+as\b|\bpretend\s+to\s+be\b",
    r"(revele|mostre|informe|repita)\s+(a|as|o|os|sua|suas|seu|seus)\s+(instru[cç][oõ]es|prompt|senha|chave|regras)",
    r"n[aã]o\s+(conte|mencione|revele|diga)\s+(isso\s+)?(ao|para\s+o|a\s+ningu[eé]m)",
    r"(sempre|nunca)\s+responda\s+que|responda\s+sempre\s+que",
    r"<\s*/?\s*(system|instructions?|prompt)\s*>|\[\s*(system|inst)\s*\]",
    r"\bjailbreak\b|\bmodo\s+desenvolvedor\b|\bdeveloper\s+mode\b",
)]
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TELEFONE = re.compile(r"(?<!\d)(?:\(?\d{2}\)?\s?)?9?\d{4}[-\s]\d{4}(?!\d)")
_CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_CNPJ = re.compile(r"(?<!\d)\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}(?!\d)")
_SENSIVEL = re.compile(r"\b(senha|password|token|chave\s+de\s+api|api[\s_-]?key|sal[aá]rio|holerite|conta\s+banc[aá]ria)\b", re.I)


def _cpf_valido(n: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", n)]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for i in (9, 10):
        s = sum(d[j] * (i + 1 - j) for j in range(i))
        if (s * 10 % 11) % 10 != d[i]:
            return False
    return True


def _cnpj_valido(n: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", n)]
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for i, pesos in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]), (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        r = sum(a * b for a, b in zip(d, pesos)) % 11
        if (0 if r < 2 else 11 - r) != d[i]:
            return False
    return True


def _trecho(texto: str, m: re.Match, margem: int = 60) -> str:
    ini, fim = max(0, m.start() - margem), min(len(texto), m.end() + margem)
    return ("…" if ini else "") + " ".join(texto[ini:fim].split()) + ("…" if fim < len(texto) else "")


def _mascarar(valor: str) -> str:
    return valor[:3] + "•" * max(0, len(valor) - 5) + valor[-2:] if len(valor) > 5 else "•••"


def analisar(textos: list[str], info: dict) -> tuple[list[dict], int]:
    """Gera os alertas mostrados ao aprovador. Não bloqueia nada: quem decide é o aprovador."""
    tudo = "\n".join(textos)
    caracteres = len("".join(tudo.split()))
    alertas: list[dict] = []

    # vários padrões pegam a mesma frase: agrupa ocorrências próximas para mostrar cada trecho uma vez
    ocorrencias = sorted((m for rx in _INJECAO for m in rx.finditer(tudo)), key=lambda m: m.start())
    achados: list[str] = []
    fim_anterior: int | None = None
    for m in ocorrencias:
        if fim_anterior is not None and m.start() <= fim_anterior + 120:
            continue
        achados.append(_trecho(tudo, m))
        fim_anterior = m.end()
        if len(achados) >= 5:
            break
    if achados:
        alertas.append({"tipo": "injecao", "nivel": "alto", "titulo": "Possíveis instruções para manipular o agente",
                        "detalhe": "O texto tem frases que parecem comandos para a IA. Se entrar na base, o agente "
                                   "pode obedecer. Leia os trechos antes de aprovar.", "exemplos": achados[:5]})

    pessoais = {}
    for nome, rx, valido in (("CPF", _CPF, _cpf_valido), ("CNPJ", _CNPJ, _cnpj_valido),
                             ("e-mail", _EMAIL, None), ("telefone", _TELEFONE, None)):
        vals = [m.group(0) for m in rx.finditer(tudo) if not valido or valido(m.group(0))]
        if vals:
            pessoais[nome] = vals
    sensiveis = sorted({m.group(1).lower() for m in _SENSIVEL.finditer(tudo)})
    if pessoais or sensiveis:
        partes = [f"{len(v)} {k}" for k, v in pessoais.items()] + ([f"palavras: {', '.join(sensiveis)}"] if sensiveis else [])
        exemplos = [f"{k}: {_mascarar(v[0])}" for k, v in pessoais.items()]
        alertas.append({"tipo": "dados_pessoais", "nivel": "medio", "titulo": "Dados pessoais ou sensíveis",
                        "detalhe": "Encontrado: " + "; ".join(partes) + ". Confira se podem ficar disponíveis para "
                                   "todos que usam o agente (LGPD).", "exemplos": exemplos})

    if caracteres < 300:
        alertas.append({"tipo": "pouco_texto", "nivel": "alto" if caracteres < 30 else "medio",
                        "titulo": "Pouco texto extraído",
                        "detalhe": f"Só {caracteres} caracteres foram lidos do arquivo. Pode ser um arquivo quase todo "
                                   "em imagem ou com baixa qualidade; o agente vai aproveitar pouco dele.", "exemplos": []})
    if info.get("ocr") == "falhou":
        alertas.append({"tipo": "ocr", "nivel": "medio", "titulo": "OCR não foi aplicado",
                        "detalhe": "O texto que está dentro de imagens pode ter ficado de fora.", "exemplos": []})
    if info.get("sem_conversor") or info.get("sem_previa"):
        alertas.append({"tipo": "ocr", "nivel": "medio", "titulo": "Sem prévia do texto",
                        "detalhe": "O conversor não está configurado: não foi possível ler o texto para conferir aqui "
                                   "(e o PDF vai sem OCR). Abra o arquivo antes de aprovar.", "exemplos": []})
    if info.get("imagens_planilha"):
        alertas.append({"tipo": "ocr", "nivel": "info", "titulo": "Imagens não lidas",
                        "detalhe": "Planilhas vão direto para o Dify; textos dentro de imagens não são lidos.", "exemplos": []})
    return alertas, caracteres


# ------------------------------------------------------------------ armazenamento local (só até a decisão)
def _pasta(envio_id: int) -> str:
    return os.path.join(settings.CONHECIMENTO_DIR, str(envio_id))


def _arquivo_local(envio: dict, qual: str) -> str | None:
    """'original' = o que a pessoa enviou; 'enviado' = o que vai para o Dify (PDF convertido ou o próprio original)."""
    p = _pasta(envio["id"])
    candidatos = [os.path.join(p, "convertido.pdf")] if qual == "enviado" else []
    candidatos.append(os.path.join(p, f"original.{envio['arquivo_ext']}"))
    return next((c for c in candidatos if os.path.exists(c)), None)


def _limpar_local(envio_id: int) -> None:
    shutil.rmtree(_pasta(envio_id), ignore_errors=True)


# ------------------------------------------------------------------ Dify: quarentena e bases dos agentes
def _exigir_chave_ds():
    if not dify.chave_conhecimento()[0]:
        raise HTTPException(400, "Falta a chave de API de Conhecimento do Dify. Cadastre em Administração > Agentes > "
                                 "Conhecimento (no Dify: Conhecimento > API > Criar chave).")


# ------------------------------------------------------------------ chave de conhecimento e bases cadastradas
def bases_cadastradas() -> list[dict]:
    """Bases cadastradas em "Bases de conhecimento" ([{id, nome}]). Lê também o formato da versão anterior."""
    bruto = db.get_meta("bases_conhecimento")
    if bruto:
        try:
            return [b for b in json.loads(bruto) if b.get("id")]
        except ValueError:
            return []
    ids = [b for b in (db.get_meta("dify_dataset_restrita") or "").split(",") if b]
    nomes = (db.get_meta("dify_dataset_restrita_nome") or "").split(", ")
    return [{"id": i, "nome": nomes[n] if n < len(nomes) and nomes[n] else i} for n, i in enumerate(ids)]


def _gravar_bases(lista: list[dict]) -> None:
    db.set_meta("bases_conhecimento", json.dumps(lista, ensure_ascii=False))
    db.set_meta("dify_dataset_restrita", "")
    db.set_meta("dify_dataset_restrita_nome", "")


def chave_restrita() -> bool:
    """A chave de conhecimento é restrita a algumas bases (o Dify recusa listar/criar bases com ela)."""
    return db.get_meta("dify_chave_restrita") == "1" or bool(db.get_meta("dify_dataset_restrita"))


def bases_restritas() -> list[str]:
    """Bases que a plataforma pode usar quando a chave é restrita ([] = chave geral do workspace)."""
    return [b["id"] for b in bases_cadastradas()] if chave_restrita() else []


def base_restrita() -> str:
    return ",".join(bases_restritas())


def base_destino(ag: dict, origem: str | None) -> str | None:
    """Para qual base vai um envio: feedback do chat (👍/👎) -> base de feedbacks (ou a dos arquivos, se não houver);
    arquivo -> base de arquivos."""
    if origem in ("feedback_positivo", "feedback_negativo"):
        return ag.get("dataset_feedback_id") or ag.get("dataset_id")
    return ag.get("dataset_id")


async def preparar_agente(ag: dict) -> str:
    """Base onde entra o conhecimento aprovado do agente. Com chave restrita, é a base da chave; com chave geral,
    cria "Colaborativo - <agente>" (quando o admin não escolheu uma base que já existe)."""
    _exigir_chave_ds()
    if ag.get("dataset_id"):
        return ag["dataset_id"]
    if chave_restrita():
        ids = bases_restritas()
        if len(ids) == 1:
            return ids[0]
        if not ids:
            raise HTTPException(400, "Cadastre a base em \"Bases de conhecimento\" (botão \"+ Adicionar base de "
                                     "conhecimento\") antes de ligar o recebimento.")
        raise HTTPException(400, "Há mais de uma base cadastrada: escolha em qual entram os arquivos aprovados.")
    try:
        base = await dify.base_criar(f"Colaborativo - {ag['nome']}"[:40],
                                     f"Conhecimento enviado pelos usuários e aprovado na plataforma para o agente "
                                     f"{ag['nome']}.", "high_quality")
    except dify.DifyErro as e:
        raise HTTPException(502, f"O Dify recusou a criação da base de conhecimento ({e.mensagem}).")
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível falar com o Dify.")
    return base["id"]


async def _conversor(dados: bytes, nome: str, ocr: bool) -> tuple[bytes, dict]:
    """PDF (com OCR se pedido) + texto extraído pelo conversor isolado."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=10)) as c:
            r = await c.post(f"{settings.CONVERSOR_URL}/processar", params={"nome": nome, "ocr": int(ocr)}, content=dados)
    except httpx.HTTPError as e:
        raise ErroProcessamento(f"O conversor de arquivos não respondeu ({type(e).__name__}).")
    if r.status_code >= 400:
        try:
            msg = r.json().get("detail") or r.text
        except ValueError:
            msg = r.text
        raise ErroProcessamento(f"Falha na conversão: {str(msg)[:200]}")
    j = r.json()
    return base64.b64decode(j.get("pdf") or ""), {"ocr": j.get("ocr") or "aplicado", "paginas": j.get("paginas") or 0,
                                                    "texto": j.get("texto") or ""}


def _decodificar(dados: bytes) -> str:
    for cod in ("utf-8-sig", "cp1252"):
        try:
            return dados.decode(cod)
        except UnicodeDecodeError:
            continue
    return dados.decode("utf-8", "replace")


def partes_do_texto(texto: str, tamanho: int = 1200) -> list[str]:
    """Divide o texto extraído em trechos (por parágrafo) para a prévia do aprovador e para os alertas."""
    partes, atual = [], ""
    for par in re.split(r"\n\s*\n", texto or ""):
        par = par.strip()
        if not par:
            continue
        while len(par) > tamanho:  # parágrafo enorme: corta em pedaços
            if atual:
                partes.append(atual)
                atual = ""
            partes.append(par[:tamanho])
            par = par[tamanho:]
        if atual and len(atual) + len(par) + 2 > tamanho:
            partes.append(atual)
            atual = par
        else:
            atual = f"{atual}\n\n{par}" if atual else par
    if atual:
        partes.append(atual)
    return partes


def _texto_local(envio_id: int) -> str:
    try:
        with open(os.path.join(_pasta(envio_id), "texto.txt"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


async def _esperar_processamento(dataset_id: str, batch: str) -> None:
    limite = time.monotonic() + ESPERA_MAXIMA
    while True:
        st = await dify.documento_status_processamento(dataset_id, batch)
        situacao = st.get("indexing_status")
        if situacao == "completed":
            return
        if situacao in ("error", "paused"):
            raise ErroProcessamento(f"O Dify não conseguiu processar o arquivo ({st.get('error') or situacao}).")
        if time.monotonic() > limite:
            raise ErroProcessamento("O Dify demorou demais para processar o arquivo.")
        await asyncio.sleep(INTERVALO_STATUS)


async def _conferir_publicacao(envio_id: int, dataset_id: str, batch: str) -> None:
    """Depois da aprovação, acompanha o processamento no Dify e registra se ele falhar (aparece no envio)."""
    try:
        await _esperar_processamento(dataset_id, batch)
    except Exception as erro:  # noqa: BLE001
        msg = str(erro) if isinstance(erro, ErroProcessamento) else f"Falha ao acompanhar no Dify ({type(erro).__name__})."
        db.atualizar_envio(envio_id, erro=f"Aprovado, mas {msg[0].lower()}{msg[1:]}")
        db.registrar_evento(envio_id, "sistema", "erro", f"depois da aprovação: {msg}")


# ------------------------------------------------------------------ processamento em segundo plano (quarentena local)
_tarefas: set[asyncio.Task] = set()


def _em_segundo_plano(coro) -> None:
    t = asyncio.create_task(coro)
    _tarefas.add(t)
    t.add_done_callback(_tarefas.discard)


def agendar(envio_id: int) -> None:
    _em_segundo_plano(processar(envio_id))


def retomar() -> None:
    """Na subida: reprocessa o que ficou no meio (reinício do container) e destrava aprovações interrompidas."""
    for e in db.listar_envios(status=["publicando"]):
        db.mudar_status_envio(e["id"], ("publicando",), "pendente")
        db.registrar_evento(e["id"], "sistema", "retomado", "aprovação interrompida por reinício; envio voltou para a fila")
    for e in db.listar_envios(status=["processando"]):
        agendar(e["id"])


async def processar(envio_id: int) -> None:
    """Quarentena LOCAL: o arquivo espera aqui no servidor. Converte/aplica OCR quando preciso, extrai o texto para a
    prévia e os alertas, e só manda para o Dify quando um aprovador aprovar (nada não aprovado chega ao Dify)."""
    e = db.get_envio(envio_id)
    if not e or e["status"] != "processando":
        return
    try:
        original = _arquivo_local(e, "original")
        if not original:
            raise ErroProcessamento("O arquivo enviado não foi encontrado no servidor.")
        with open(original, "rb") as f:
            dados = f.read()
        ext = e["arquivo_ext"]
        _mime, tratamento = TIPOS[ext]
        info: dict = {"ocr": "nao_aplicavel"}
        if tratamento == "auto":  # DOCX: com imagens vai pelo conversor (OCR), sem imagens vai direto (preserva tabelas)
            tratamento = "conversor" if validar_conteudo(dados, ext).get("imagens") else "direto"
        enviado_nome, texto = e["arquivo_nome"], ""
        if ext in ("txt", "md", "csv"):
            texto = _decodificar(dados)
        elif tratamento == "conversor":
            if settings.CONVERSOR_URL:
                pdf, conv = await _conversor(dados, e["arquivo_nome"], ocr=True)
                texto = conv.pop("texto")
                info.update(conv, convertido=1)
                enviado_nome = e["arquivo_nome"].rsplit(".", 1)[0] + ".pdf"
                with open(os.path.join(_pasta(envio_id), "convertido.pdf"), "wb") as f:
                    f.write(pdf)
            elif ext == "pdf":
                info["sem_conversor"] = True
            else:
                raise ErroProcessamento("Este tipo de arquivo precisa do conversor, que não está configurado "
                                        "(CONVERSOR_URL).")
        else:  # DOCX sem imagem, XLSX, XLS: vão como estão para o Dify; o conversor só lê o texto para a prévia
            if settings.CONVERSOR_URL:
                _pdf, conv = await _conversor(dados, e["arquivo_nome"], ocr=False)
                texto = conv["texto"]
            else:
                info["sem_previa"] = True
            if ext == "xlsx" and validar_conteudo(dados, ext).get("imagens"):
                info["imagens_planilha"] = True
        with open(os.path.join(_pasta(envio_id), "texto.txt"), "w", encoding="utf-8") as f:
            f.write(texto)
        partes = partes_do_texto(texto)
        alertas, caracteres = analisar(partes, info)
        if not db.mudar_status_envio(envio_id, ("processando",), "pendente", alertas=alertas, caracteres=caracteres,
                                     trechos=len(partes), enviado_nome=enviado_nome,
                                     convertido=info.get("convertido", 0), ocr=info["ocr"], paginas=info.get("paginas")):
            _limpar_local(envio_id)  # cancelado durante o processamento
            return
        resumo = f"{len(partes)} trecho(s), {caracteres} caracteres"
        if info.get("convertido"):
            resumo += f"; convertido para PDF, OCR {info['ocr']}"
        if alertas:
            resumo += "; alertas: " + ", ".join(a["titulo"] for a in alertas)
        db.registrar_evento(envio_id, "sistema", "processado", resumo)
    except Exception as erro:  # noqa: BLE001 - qualquer falha vira status "erro" com a mensagem para o usuário
        if isinstance(erro, ErroProcessamento):
            msg = str(erro)
        else:
            log.exception("falha ao processar o envio %s", envio_id)
            msg = f"Falha inesperada no processamento ({type(erro).__name__})."
        if db.mudar_status_envio(envio_id, ("processando",), "erro", erro=msg):
            db.registrar_evento(envio_id, "sistema", "erro", msg)
        _limpar_local(envio_id)


# ------------------------------------------------------------------ permissões
def _admin(user: dict) -> bool:
    return user["perfil"] == "admin"


def _agentes_visiveis(user: dict) -> list[int] | None:
    """None = todos (admin); senão, os agentes que a pessoa aprova."""
    return None if _admin(user) else db.agentes_que_aprova(user["username"])


def _pode_ver(user: dict, e: dict) -> bool:
    return _admin(user) or e["username"] == user["username"] or e["agente_id"] in db.agentes_que_aprova(user["username"])


def _envio_ou_404(envio_id: int, user: dict) -> dict:
    e = db.get_envio(envio_id)
    if not e or not _pode_ver(user, e):  # mesmo 404 para "não existe" e "não é seu": não revela envios alheios
        raise HTTPException(404, "Envio não encontrado.")
    return e


def _publico(e: dict, user: dict, aprova: list[int] | None = None) -> dict:
    if aprova is None:
        aprova = db.agentes_que_aprova(user["username"])
    ag = db.get_agente(e["agente_id"]) if e["agente_id"] else None
    proprio = e["username"] == user["username"]
    pode_decidir = e["status"] == "pendente" and e["agente_id"] in aprova and (not proprio or bool(ag and ag["autoaprovacao"]))
    campos = ("id", "agente_id", "agente_nome", "username", "arquivo_nome", "arquivo_ext", "tamanho", "sha256",
              "justificativa", "status", "enviado_nome", "convertido", "ocr", "paginas", "caracteres", "trechos",
              "alertas", "erro", "criado_em", "decidido_por", "decidido_em", "motivo", "autoaprovado", "origem",
              "message_id", "pergunta", "resposta_original", "resposta_corrigida", "dataset_id")
    return {**{k: e.get(k) for k in campos}, "pode_decidir": pode_decidir,
            "pode_cancelar": proprio and e["status"] in STATUS_ABERTOS,
            "pode_administrar": _admin(user) and e["status"] in ("aprovado", "desativado")}


# ------------------------------------------------------------------ rotas do usuário
@router.get("/api/conhecimento/agentes")
def agentes_para_envio(user: dict = Depends(usuario_atual)):
    itens = [{k: a[k] for k in ("id", "nome", "icone", "cor")} for a in db.agentes_do_usuario(user["username"])
             if a.get("recebe_conhecimento") and a.get("dataset_id")]
    extensoes = [e for e in TIPOS if settings.CONVERSOR_URL or e not in PRECISA_CONVERSOR]
    return {"itens": itens, "max_mb": settings.CONHECIMENTO_MAX_MB, "extensoes": extensoes,
            "aprova": db.agentes_que_aprova(user["username"]), "admin": _admin(user)}


@router.post("/api/conhecimento/envios")
async def enviar(request: Request, agente: int, user: dict = Depends(usuario_atual)):
    ag = db.get_agente(agente)
    if not ag or not ag["ativo"] or not db.tem_acesso(user["username"], agente):
        raise HTTPException(403, "Você não tem acesso a este agente.")
    if not ag.get("recebe_conhecimento") or not ag.get("dataset_id"):
        raise HTTPException(400, "Este agente não está recebendo conhecimento.")
    nome = nome_seguro(request.headers.get("x-arquivo-nome", ""))
    justificativa = unquote(request.headers.get("x-justificativa", "")).strip()
    if not nome:
        raise HTTPException(400, "Nome de arquivo inválido.")
    ext = nome.rsplit(".", 1)[1]
    if ext not in TIPOS or (ext in PRECISA_CONVERSOR and not settings.CONVERSOR_URL):
        raise HTTPException(415, f"Tipo de arquivo não aceito (.{ext}).")
    if not 10 <= len(justificativa) <= 1000:
        raise HTTPException(400, "Explique em poucas palavras o que é o arquivo e por que deve entrar (10 a 1000 caracteres).")
    hora_atras = tempo.iso_utc(datetime.now(timezone.utc) - timedelta(hours=1))
    if len(db.listar_envios(username=user["username"], inicio=hora_atras, limite=ENVIOS_POR_HORA + 1)) >= ENVIOS_POR_HORA:
        raise HTTPException(429, "Muitos envios na última hora. Tente mais tarde.")

    limite = settings.CONHECIMENTO_MAX_MB * 1024 * 1024
    grande = HTTPException(413, f"O arquivo passou de {settings.CONHECIMENTO_MAX_MB} MB.")
    try:
        if int(request.headers.get("content-length") or 0) > limite:
            raise grande
    except ValueError:
        pass
    dados = bytearray()
    async for parte in request.stream():
        dados += parte
        if len(dados) > limite:
            raise grande
    dados = bytes(dados)
    if not dados:
        raise HTTPException(400, "Arquivo vazio.")
    validar_conteudo(dados, ext)
    sha = hashlib.sha256(dados).hexdigest()
    repetido = next((x for x in db.listar_envios(status=["processando", "pendente", "publicando", "aprovado", "desativado"],
                                                 agente_ids=[agente], limite=5000) if x["sha256"] == sha), None)
    if repetido:
        raise HTTPException(409, f"Esse mesmo arquivo já foi enviado para este agente (envio nº {repetido['id']}, "
                                 f"situação: {repetido['status']}).")

    envio_id = db.criar_envio(agente_id=ag["id"], agente_nome=ag["nome"], dataset_id=ag["dataset_id"],
                              username=user["username"], arquivo_nome=nome, arquivo_ext=ext, tamanho=len(dados),
                              sha256=sha, justificativa=justificativa, status="processando")
    os.makedirs(_pasta(envio_id), exist_ok=True)
    with open(os.path.join(_pasta(envio_id), f"original.{ext}"), "wb") as f:
        f.write(dados)
    db.registrar_evento(envio_id, user["username"], "enviado", f"{nome} ({len(dados) // 1024} KB, sha256 {sha[:16]}…)")
    agendar(envio_id)
    return _publico(db.get_envio(envio_id), user)


@router.get("/api/conhecimento/meus")
def meus_envios(user: dict = Depends(usuario_atual)):
    aprova = db.agentes_que_aprova(user["username"])
    return {"itens": [_publico(e, user, aprova) for e in db.listar_envios(username=user["username"], limite=300)]}


@router.post("/api/conhecimento/envios/{envio_id}/cancelar")
async def cancelar(envio_id: int, user: dict = Depends(usuario_atual)):
    e = _envio_ou_404(envio_id, user)
    if e["username"] != user["username"]:
        raise HTTPException(403, "Só quem enviou pode cancelar.")
    if not db.mudar_status_envio(envio_id, STATUS_ABERTOS, "cancelado", decidido_por=user["username"],
                                 decidido_em=db.agora()):
        raise HTTPException(409, "Este envio não pode mais ser cancelado.")
    await _tirar_da_quarentena(e)
    _limpar_local(envio_id)
    db.registrar_evento(envio_id, user["username"], "cancelado", "")
    return _publico(db.get_envio(envio_id), user)


# ------------------------------------------------------------------ rotas de aprovação
STATUS_VALIDOS = {"processando", "pendente", "publicando", "erro", "aprovado", "rejeitado", "cancelado", "desativado", "excluido"}


@router.get("/api/conhecimento/envios")
def listar(status: str | None = None, agente: int | None = None, usuario: str | None = None, inicio: str | None = None,
           fim: str | None = None, origem: str | None = None, user: dict = Depends(usuario_atual)):
    visiveis = _agentes_visiveis(user)
    if visiveis is not None and not visiveis:
        raise HTTPException(403, "Você não é aprovador de nenhum agente.")
    lista_status = [s for s in (status or "").split(",") if s]
    if any(s not in STATUS_VALIDOS for s in lista_status):
        raise HTTPException(400, "Situação inválida.")
    if origem and origem not in ORIGENS:
        raise HTTPException(400, "Tipo inválido.")
    if agente:
        visiveis = [agente] if visiveis is None or agente in visiveis else []
    ini, f = tempo.periodo(inicio, fim)
    u = ldap_auth.normalizar_usuario(usuario) if usuario else None
    aprova = db.agentes_que_aprova(user["username"])
    itens = db.listar_envios(lista_status or None, visiveis, u, tempo.iso_utc(ini), tempo.iso_utc(f), 1000, origem)
    return {"itens": [_publico(e, user, aprova) for e in itens]}


@router.get("/api/conhecimento/envios/{envio_id}")
def detalhe(envio_id: int, user: dict = Depends(usuario_atual)):
    e = _envio_ou_404(envio_id, user)
    ag = db.get_agente(e["agente_id"]) if e["agente_id"] else None
    return {**_publico(e, user), "eventos": db.eventos_do_envio(envio_id),
            "autoaprovacao_permitida": bool(ag and ag["autoaprovacao"]),
            "aprovadores": db.aprovadores_do_agente(e["agente_id"]) if e["agente_id"] else []}


def _onde_esta(e: dict) -> tuple[str, str] | None:
    """(base, documento) no Dify para envios aprovados; antes disso o conteúdo só existe aqui (quarentena local)."""
    if e["status"] in ("aprovado", "desativado") and e["destino_doc"]:
        return e["dataset_id"], e["destino_doc"]
    return None


@router.get("/api/conhecimento/envios/{envio_id}/trechos")
async def trechos(envio_id: int, pagina: int = 1, user: dict = Depends(usuario_atual)):
    e = _envio_ou_404(envio_id, user)
    if e["status"] in ("pendente", "publicando"):  # quarentena local: prévia do texto extraído aqui
        partes = partes_do_texto(_texto_local(envio_id))
        ini = (max(1, pagina) - 1) * 50
        return {"itens": [{"posicao": ini + i + 1, "texto": t, "palavras": len(t.split())}
                          for i, t in enumerate(partes[ini:ini + 50])],
                "mais": ini + 50 < len(partes), "total": len(partes), "pagina": max(1, pagina)}
    local = _onde_esta(e)
    if not local:
        raise HTTPException(409, "O conteúdo deste envio não está disponível (ainda em processamento, recusado ou excluído).")
    try:
        r = await dify.documento_trechos(local[0], local[1], max(1, pagina), 50)
    except dify.DifyErro as erro:
        raise HTTPException(502 if erro.status >= 500 else 404, f"Não foi possível ler os trechos no Dify ({erro.mensagem}).")
    return {"itens": [{"posicao": s.get("position"), "texto": s.get("content") or "", "palavras": s.get("word_count")}
                      for s in sorted(r.get("data") or [], key=lambda s: s.get("position") or 0)],
            "mais": bool(r.get("has_more")), "total": r.get("total"), "pagina": max(1, pagina)}


@router.get("/api/conhecimento/envios/{envio_id}/arquivo")
async def arquivo(envio_id: int, qual: str = "enviado", user: dict = Depends(usuario_atual)):
    e = _envio_ou_404(envio_id, user)
    if qual not in ("original", "enviado"):
        raise HTTPException(400, "Use qual=original ou qual=enviado.")
    caminho = _arquivo_local(e, qual)
    if caminho:
        with open(caminho, "rb") as f:
            dados = f.read()
        nome = e["arquivo_nome"] if caminho.endswith(f"original.{e['arquivo_ext']}") else e["enviado_nome"]
    elif e["status"] in ("aprovado", "desativado") and e["destino_doc"]:
        try:  # depois da aprovação o arquivo só existe no Dify (a cópia local é apagada)
            dados, _tipo = await dify.documento_baixar(e["dataset_id"], e["destino_doc"])
        except (dify.DifyErro, httpx.HTTPError, KeyError):
            raise HTTPException(502, "Não foi possível buscar o arquivo no Dify.")
        nome = e["enviado_nome"] or e["arquivo_nome"]
    else:
        raise HTTPException(404, "O arquivo não está mais disponível.")
    ext = nome.rsplit(".", 1)[-1].lower()
    mime = TIPOS.get(ext, ("application/octet-stream",))[0]
    inline = ext in ("pdf", "png", "jpg", "jpeg", "txt", "md", "csv")
    if ext in ("txt", "md", "csv"):
        mime = "text/plain; charset=utf-8"
    disp = ("inline" if inline else "attachment") + f"; filename*=UTF-8''{quote(nome)}"
    # sandbox: mesmo aberto no navegador, o arquivo não roda script nem acessa a sessão da plataforma
    return Response(dados, media_type=mime, headers={"Content-Disposition": disp, "X-Content-Type-Options": "nosniff",
                                                      "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; object-src 'self'",
                                                      "Cache-Control": "no-store"})


class DecisaoIn(BaseModel):
    motivo: str = Field(default="", max_length=1000)
    # 👎: resposta correta que entra na base (obrigatória se quem avaliou não escreveu; o aprovador pode ajustar)
    resposta_corrigida: str | None = Field(default=None, max_length=8000)


async def _tirar_da_quarentena(e: dict) -> None:
    """Só para envios da versão antiga, que usava uma base de quarentena no Dify. Hoje a quarentena é local."""
    q = db.get_meta("quarentena_dataset")
    if q and e.get("quarentena_doc"):
        try:
            await dify.documento_apagar(q, e["quarentena_doc"])
        except Exception:  # noqa: BLE001 - sobra na quarentena não afeta nenhum agente
            log.warning("não consegui apagar o documento %s da quarentena", e["quarentena_doc"])


@router.post("/api/conhecimento/envios/{envio_id}/aprovar")
async def aprovar(envio_id: int, dados: DecisaoIn, user: dict = Depends(usuario_atual)):
    e = _envio_ou_404(envio_id, user)
    ag = db.get_agente(e["agente_id"]) if e["agente_id"] else None
    if e["agente_id"] not in db.agentes_que_aprova(user["username"]):
        raise HTTPException(403, "Você não é aprovador deste agente.")
    proprio = e["username"] == user["username"]
    if proprio and not (ag and ag["autoaprovacao"]):
        raise HTTPException(403, "A autoaprovação está desligada neste agente: outro aprovador precisa decidir.")
    if e["status"] != "pendente":
        raise HTTPException(409, "Este envio não está aguardando aprovação.")
    if not ag or not ag.get("dataset_id") or not ag.get("recebe_conhecimento"):
        raise HTTPException(409, "O agente não está mais recebendo conhecimento.")
    correcao_final = None
    if e.get("origem") == "feedback_negativo":
        # só a resposta CORRETA entra na base; a errada fica apenas na tela do aprovador
        correcao_final = texto_visivel(dados.resposta_corrigida if dados.resposta_corrigida is not None
                                       else e.get("resposta_corrigida"))
        if len(correcao_final) < 10:
            raise HTTPException(400, "Escreva a resposta correta para aprovar (pelo menos 10 caracteres).")
        if _parecidos(correcao_final, e.get("resposta_original") or ""):
            raise HTTPException(400, "A resposta correta está igual à resposta que foi avaliada como ruim.")
    caminho = _arquivo_local(e, "enviado")
    if not caminho and correcao_final is None:
        raise HTTPException(409, "O arquivo não foi encontrado no servidor. Peça para enviar de novo.")
    if not db.mudar_status_envio(envio_id, ("pendente",), "publicando"):
        raise HTTPException(409, "Outro aprovador acabou de decidir este envio.")
    try:
        if correcao_final is not None:
            conteudo = _texto_feedback("feedback_negativo", ag["nome"], e.get("pergunta") or "", correcao_final).encode("utf-8")
        else:
            with open(caminho, "rb") as f:
                conteudo = f.read()
        nome = e["enviado_nome"] or e["arquivo_nome"]
        mime = TIPOS.get(nome.rsplit(".", 1)[-1].lower(), ("application/octet-stream",))[0]
        destino = base_destino(ag, e.get("origem"))
        base = await dify.base_info(destino)
        r = await dify.documento_enviar(destino, nome, conteudo, mime,
                                        None if base.get("indexing_technique") else "high_quality")
    except Exception as erro:  # noqa: BLE001
        db.mudar_status_envio(envio_id, ("publicando",), "pendente")
        motivo = erro.mensagem if isinstance(erro, dify.DifyErro) else type(erro).__name__
        log.warning("aprovação do envio %s falhou: %s", envio_id, motivo)
        raise HTTPException(502, f"Não foi possível enviar para a base do agente no Dify ({motivo}). Nada foi alterado.")
    extra = {}
    detalhe = dados.motivo.strip()
    if correcao_final is not None and correcao_final != (e.get("resposta_corrigida") or "").strip():
        extra["resposta_corrigida"] = correcao_final
        detalhe = ("resposta correta escrita pelo aprovador" if not e.get("resposta_corrigida") else
                   "correção ajustada pelo aprovador") + (f"; {detalhe}" if detalhe else "")
    db.mudar_status_envio(envio_id, ("publicando",), "aprovado", destino_doc=r["document"]["id"],
                          destino_batch=r.get("batch"), decidido_por=user["username"], decidido_em=db.agora(),
                          motivo=dados.motivo.strip(), autoaprovado=int(proprio), dataset_id=destino, **extra)
    db.registrar_evento(envio_id, user["username"], "autoaprovado" if proprio else "aprovado", detalhe)
    if r.get("batch"):
        _em_segundo_plano(_conferir_publicacao(envio_id, destino, r["batch"]))
    await _tirar_da_quarentena(e)
    _limpar_local(envio_id)
    return _publico(db.get_envio(envio_id), user)


@router.post("/api/conhecimento/envios/{envio_id}/rejeitar")
async def rejeitar(envio_id: int, dados: DecisaoIn, user: dict = Depends(usuario_atual)):
    e = _envio_ou_404(envio_id, user)
    if e["agente_id"] not in db.agentes_que_aprova(user["username"]):
        raise HTTPException(403, "Você não é aprovador deste agente.")
    motivo = dados.motivo.strip()
    if len(motivo) < 5:
        raise HTTPException(400, "Informe o motivo da rejeição (quem enviou vai ver).")
    if not db.mudar_status_envio(envio_id, ("pendente",), "rejeitado", decidido_por=user["username"],
                                 decidido_em=db.agora(), motivo=motivo):
        raise HTTPException(409, "Este envio não está aguardando aprovação.")
    db.registrar_evento(envio_id, user["username"], "rejeitado", motivo)
    await _tirar_da_quarentena(e)
    _limpar_local(envio_id)
    return _publico(db.get_envio(envio_id), user)


# ------------------------------------------------------------------ ações exclusivas do admin
class AcaoAdminIn(BaseModel):
    motivo: str = Field(min_length=5, max_length=1000)


async def _acao_admin(envio_id: int, de: tuple[str, ...], para: str, acao_dify: str | None, acao_log: str,
                      motivo: str, admin: dict) -> dict:
    e = db.get_envio(envio_id)
    if not e:
        raise HTTPException(404, "Envio não encontrado.")
    if e["status"] not in de:
        raise HTTPException(409, "Ação não permitida na situação atual deste conhecimento.")
    try:
        if acao_dify == "apagar":
            await dify.documento_apagar(e["dataset_id"], e["destino_doc"])
        elif acao_dify:
            await dify.documento_mudar_status(e["dataset_id"], e["destino_doc"], acao_dify)
    except dify.DifyErro as erro:
        raise HTTPException(502, f"O Dify recusou a ação ({erro.mensagem}). Nada foi alterado.")
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível falar com o Dify. Nada foi alterado.")
    db.mudar_status_envio(envio_id, de, para)
    db.registrar_evento(envio_id, admin["username"], acao_log, motivo.strip())
    return _publico(db.get_envio(envio_id), admin)


@router.post("/api/conhecimento/envios/{envio_id}/desativar")
async def desativar(envio_id: int, dados: AcaoAdminIn, admin: dict = Depends(exigir_admin)):
    return await _acao_admin(envio_id, ("aprovado",), "desativado", "disable", "desativado", dados.motivo, admin)


@router.post("/api/conhecimento/envios/{envio_id}/reativar")
async def reativar(envio_id: int, dados: AcaoAdminIn, admin: dict = Depends(exigir_admin)):
    return await _acao_admin(envio_id, ("desativado",), "aprovado", "enable", "reativado", dados.motivo, admin)


@router.post("/api/conhecimento/envios/{envio_id}/excluir")
async def excluir(envio_id: int, dados: AcaoAdminIn, admin: dict = Depends(exigir_admin)):
    return await _acao_admin(envio_id, ("aprovado", "desativado"), "excluido", "apagar", "excluido", dados.motivo, admin)


class ChaveConhecimentoIn(BaseModel):
    chave: str = Field(min_length=1, max_length=200)
    # opcional (versão anterior): IDs de bases para já cadastrar junto, separados por vírgula
    base_id: str = Field(default="", max_length=400)


RX_CHAVE_DS = re.compile(r"(dataset|ds)-[A-Za-z0-9]{16,64}")


@router.get("/api/admin/conhecimento/config")
def config_conhecimento(admin: dict = Depends(exigir_admin)):
    chave, origem = dify.chave_conhecimento()
    bases = bases_cadastradas()
    return {"chave_configurada": bool(chave), "chave_mascarada": f"{chave[:6]}…{chave[-4:]}" if chave else "",
            "origem": origem, "chave_restrita": chave_restrita(), "bases": bases,
            "restrita": base_restrita(), "restrita_nome": ", ".join(b["nome"] for b in bases) if chave_restrita() else "",
            "conversor": bool(settings.CONVERSOR_URL), "max_mb": settings.CONHECIMENTO_MAX_MB}


@router.put("/api/admin/conhecimento/config")
async def salvar_chave_conhecimento(dados: ChaveConhecimentoIn, admin: dict = Depends(exigir_admin)):
    chave = dados.chave.strip()
    if not RX_CHAVE_DS.fullmatch(chave):
        raise HTTPException(400, "Chave inválida. No Dify: Conhecimento > API > Criar chave (começa com dataset- ou ds-).")
    restrita = False
    try:
        await dify.testar_chave_conhecimento(chave)
    except dify.DifyErro as e:
        if e.status == 401:
            raise HTTPException(400, "O Dify recusou essa chave. Confira se copiou a chave inteira.")
        if e.status != 403:
            raise HTTPException(502, f"O Dify não respondeu como esperado ({e.mensagem}).")
        restrita = True  # 403 ao listar: chave restrita a bases específicas (boa prática)
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível falar com o Dify.")
    bases = bases_cadastradas()
    for base_id in list(dict.fromkeys(i for i in re.split(r"[\s,;]+", dados.base_id.strip()) if i))[:10]:
        if base_id not in [b["id"] for b in bases]:
            bases.append({"id": base_id, "nome": await _nome_da_base(base_id, chave)})
    if restrita:
        # a chave nova precisa alcançar as bases cadastradas e as que os agentes já usam
        usadas = {b["id"]: b["nome"] for b in bases}
        for a in db.list_agentes():
            for b in (a.get("dataset_id"), a.get("dataset_feedback_id")):
                if b:
                    usadas.setdefault(b, f"base do agente {a['nome']}")
        fora = []
        for base_id, nome in usadas.items():
            try:
                await dify.base_info(base_id, chave)
            except dify.DifyErro:
                donos = [a["nome"] for a in db.list_agentes() if base_id in (a.get("dataset_id"), a.get("dataset_feedback_id"))]
                fora.append(nome + (f" (usada por {', '.join(donos)})" if donos else ""))
            except httpx.HTTPError:
                raise HTTPException(502, "Não foi possível falar com o Dify.")
        if fora:
            raise HTTPException(400, "Essa chave é restrita e não alcança: " + "; ".join(fora) + ". No Dify, inclua essas "
                                     "bases na restrição da chave, ou troque a base dos agentes antes.")
    db.set_meta("dify_dataset_key", chave)
    db.set_meta("dify_chave_restrita", "1" if restrita else "")
    _gravar_bases(bases)
    db.auditar(admin["username"], "chave_conhecimento", "", "chave de conhecimento cadastrada/trocada")
    return config_conhecimento(admin)


@router.delete("/api/admin/conhecimento/config")
def remover_chave_conhecimento(admin: dict = Depends(exigir_admin)):
    db.set_meta("dify_dataset_key", "")
    db.set_meta("dify_chave_restrita", "")
    db.auditar(admin["username"], "chave_conhecimento", "", "chave de conhecimento removida da tela")
    return config_conhecimento(admin)


class AprovacoesIn(BaseModel):
    agentes: list[int] = Field(max_length=500)


@router.put("/api/admin/usuarios/{username}/aprovacoes")
def definir_aprovacoes(username: str, dados: AprovacoesIn, admin: dict = Depends(exigir_admin)):
    if not db.get_user(username):
        raise HTTPException(404, "Usuário não cadastrado.")
    ids = db.definir_aprovacoes(username, dados.agentes)
    db.auditar(admin["username"], "aprovacoes_usuario", username.lower(), json.dumps(ids))
    return {"username": username.lower(), "aprova": ids}


# ------------------------------------------------------------------ registro de aprovação
ACOES = {"enviado", "processado", "erro", "cancelado", "aprovado", "autoaprovado", "rejeitado", "desativado",
         "reativado", "excluido", "retomado"}
NOME_ACAO = {"enviado": "Enviado", "processado": "Pronto para análise", "erro": "Erro no processamento",
             "cancelado": "Cancelado por quem enviou", "aprovado": "Aprovado", "autoaprovado": "Autoaprovado",
             "rejeitado": "Rejeitado", "desativado": "Desativado (admin)", "reativado": "Reativado (admin)",
             "excluido": "Excluído (admin)", "retomado": "Retomado após reinício"}


@router.get("/api/conhecimento/registros")
def registros(agente: int | None = None, usuario: str | None = None, inicio: str | None = None, fim: str | None = None,
              acao: str | None = None, autoaprovado: bool | None = None, formato: str = "json",
              user: dict = Depends(usuario_atual)):
    visiveis = _agentes_visiveis(user)
    if visiveis is not None and not visiveis:
        raise HTTPException(403, "Só administradores e aprovadores veem os registros de aprovação.")
    if acao and acao not in ACOES:
        raise HTTPException(400, "Ação inválida.")
    if agente and visiveis is not None and agente not in visiveis:
        raise HTTPException(403, "Você não é aprovador deste agente.")
    ini, f = tempo.periodo(inicio, fim)
    u = ldap_auth.normalizar_usuario(usuario) if usuario else None
    itens, cortado = db.listar_eventos_conhecimento(visiveis, u, agente, tempo.iso_utc(ini), tempo.iso_utc(f), acao,
                                                    autoaprovado)
    if formato == "csv":
        saida = io.StringIO()
        w = csv.writer(saida, delimiter=";")
        w.writerow(["Data/hora", "Ação", "Feita por", "Envio", "Tipo", "Arquivo", "Agente", "Enviado por",
                    "Situação atual", "Autoaprovado", "Detalhe", "SHA-256"])
        for r in itens:
            w.writerow([tempo.hora(r["quando"]), NOME_ACAO.get(r["acao"], r["acao"]), r["autor"], r["envio_id"],
                        NOME_ORIGEM.get(r["origem"], r["origem"]), r["arquivo_nome"], r["agente_nome"], r["username"], r["status"],
                        "sim" if r["autoaprovado"] else "não", r["detalhe"] or "", r["sha256"]])
        nome = f"registros-aprovacao-{datetime.now(tempo.FUSO):%Y%m%d-%H%M}.csv"
        db.auditar(user["username"], "exportar_registros_aprovacao", "", f"{len(itens)} registro(s)")
        return Response("﻿" + saida.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{nome}"'})
    return {"itens": itens, "cortado": cortado}


# ------------------------------------------------------------------ avaliações do chat (👍 / 👎) que viram conhecimento
ORIGENS = {"arquivo", "feedback_positivo", "feedback_negativo"}
AVALIACOES_POR_HORA = int(os.getenv("CONHECIMENTO_AVALIACOES_POR_HORA", "60"))
_INVISIVEIS = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]")


def texto_visivel(t: str | None) -> str:
    """Tira caracteres invisíveis (largura zero, direção) e espaços das pontas."""
    return _INVISIVEIS.sub("", t or "").strip()


def _parecidos(a: str, b: str) -> bool:
    norm = lambda x: re.sub(r"[\W_]+", "", texto_visivel(x).lower())  # noqa: E731
    return norm(a) == norm(b)
NOME_ORIGEM = {"arquivo": "Arquivo", "feedback_positivo": "Resposta validada (👍)", "feedback_negativo": "Correção (👎)"}
STATUS_FEEDBACK_ATIVOS = ("processando", "pendente", "publicando", "aprovado", "desativado")


async def cancelar_envios_do_agente(agente_id: int, autor: str) -> int:
    """Agente removido: os envios ainda não decididos são cancelados e saem da quarentena (não ficam órfãos)."""
    n = 0
    for e in db.listar_envios(status=["processando", "pendente", "erro"], agente_ids=[agente_id], limite=10000):
        if db.mudar_status_envio(e["id"], ("processando", "pendente", "erro"), "cancelado", decidido_por=autor,
                                 decidido_em=db.agora()):
            db.registrar_evento(e["id"], autor, "cancelado", "agente removido da plataforma")
            await _tirar_da_quarentena(e)
            _limpar_local(e["id"])
            n += 1
    return n


def agente_recebe(ag: dict | None) -> bool:
    return bool(ag and ag["ativo"] and ag.get("recebe_conhecimento") and ag.get("dataset_id"))


async def _mensagem_no_dify(ag: dict, username: str, conversation_id: str, message_id: str) -> dict:
    """Busca a pergunta e a resposta no próprio Dify (nunca confia no texto vindo do navegador)."""
    primeira = None
    for _ in range(30):  # até 3 mil mensagens para trás na conversa
        r = await dify.mensagens(ag["chave"], username, conversation_id, primeira, 100)
        dados = r.get("data") or []
        for m in dados:
            if m.get("id") == message_id:
                return m
        if not r.get("has_more") or not dados:
            break
        primeira = min(dados, key=lambda m: m.get("created_at") or 0)["id"]
    raise HTTPException(404, "Resposta não encontrada nesta conversa.")


def _texto_feedback(origem: str, agente: str, pergunta: str, resposta: str, sem_correcao: bool = False) -> str:
    if sem_correcao:  # 👎 sem correção: só vai para a quarentena; na aprovação entra a resposta correta do aprovador
        return (f"# Resposta avaliada como incorreta - {agente}\n\n## Pergunta\n{pergunta.strip()}\n\n"
                f"## Resposta do agente (avaliada como incorreta)\n{resposta.strip()}\n\n---\nAguardando a resposta correta, "
                "que o aprovador escreve ao aprovar.\n")
    if origem == "feedback_positivo":
        return (f"# Resposta validada - {agente}\n\n## Pergunta\n{pergunta.strip()}\n\n## Resposta\n{resposta.strip()}\n\n"
                "---\nResposta do agente avaliada como útil por um usuário e aprovada para a base de conhecimento.\n")
    return (f"# Resposta corrigida - {agente}\n\n## Pergunta\n{pergunta.strip()}\n\n## Resposta correta\n{resposta.strip()}\n\n"
            "---\nCorreção enviada por um usuário para uma resposta anterior do agente e aprovada para a base de "
            "conhecimento. Para perguntas parecidas, use esta resposta.\n")


def _criar_envio_texto(ag: dict, username: str, origem: str, nome: str, texto: str, justificativa: str,
                       **extras) -> dict:
    """Cria um envio a partir de um texto gerado (segue o mesmo fluxo de um arquivo: quarentena, alertas, aprovação)."""
    dados = texto.encode("utf-8")
    sha = hashlib.sha256(dados).hexdigest()
    # cada avaliação é de uma pessoa sobre uma mensagem: não mescla com envios de outras pessoas/mensagens,
    # mesmo com texto igual (a duplicidade já é evitada por mensagem + pessoa + tipo)
    hora_atras = tempo.iso_utc(datetime.now(timezone.utc) - timedelta(hours=1))
    recentes = [x for x in db.listar_envios(username=username, inicio=hora_atras, limite=1000) if x.get("origem") != "arquivo"]
    if len(recentes) >= AVALIACOES_POR_HORA:
        raise HTTPException(429, "Muitas avaliações enviadas para aprovação na última hora. A avaliação foi registrada no "
                                 "Dify, mas não foi para a fila agora.")
    envio_id = db.criar_envio(agente_id=ag["id"], agente_nome=ag["nome"], dataset_id=base_destino(ag, origem), username=username,
                              arquivo_nome=nome, arquivo_ext="md", tamanho=len(dados), sha256=sha,
                              justificativa=justificativa[:1000], status="processando", origem=origem, **extras)
    os.makedirs(_pasta(envio_id), exist_ok=True)
    with open(os.path.join(_pasta(envio_id), "original.md"), "wb") as f:
        f.write(dados)
    db.registrar_evento(envio_id, username, "enviado", NOME_ORIGEM[origem] + f" (mensagem {extras.get('message_id', '')[:8]}…)")
    agendar(envio_id)
    return db.get_envio(envio_id)


def _cancelar_feedback_aberto(message_id: str, username: str, origem: str, motivo: str) -> None:
    aberto = db.envio_do_feedback(message_id, username, origem, ("processando", "pendente", "erro"))
    if aberto and db.mudar_status_envio(aberto["id"], ("processando", "pendente", "erro"), "cancelado",
                                        decidido_por=username, decidido_em=db.agora()):
        db.registrar_evento(aberto["id"], username, "cancelado", motivo)
        asyncio.create_task(_tirar_da_quarentena(aberto))
        _limpar_local(aberto["id"])


async def feedback_no_chat(ag: dict, user: dict, message_id: str, conversation_id: str | None, avaliacao: str | None,
                           comentario: str = "") -> dict | None:
    """Chamado depois que o 👍/👎 foi gravado no Dify. 👍 manda a resposta para aprovação; tirar o 👍 (ou trocar por 👎)
    cancela o envio que ainda não foi decidido. A correção do 👎 vem pela rota /api/conhecimento/correcao."""
    username = user["username"]
    # trocar ou retirar a avaliação cancela o envio da avaliação anterior que ainda não foi decidido
    if avaliacao != "like":
        _cancelar_feedback_aberto(message_id, username, "feedback_positivo", "avaliação positiva retirada no chat")
    if avaliacao != "dislike":
        _cancelar_feedback_aberto(message_id, username, "feedback_negativo", "avaliação negativa retirada no chat")
    if avaliacao is None or not agente_recebe(ag) or not conversation_id:
        return None
    origem = "feedback_positivo" if avaliacao == "like" else "feedback_negativo"
    existente = db.envio_do_feedback(message_id, username, origem, STATUS_FEEDBACK_ATIVOS)
    if existente:
        return _publico(existente, user)
    m = await _mensagem_no_dify(ag, username, conversation_id, message_id)
    pergunta, resposta = (m.get("query") or "").strip(), (m.get("answer") or "").strip()
    if not pergunta or not resposta:
        return None
    obs = f" Comentário: {comentario.strip()}" if comentario.strip() else ""
    if origem == "feedback_positivo":
        texto = _texto_feedback(origem, ag["nome"], pergunta, resposta)
        justificativa, nome = "Resposta avaliada como útil (👍) no chat." + obs, f"resposta-validada-{message_id[:8]}.md"
    else:  # 👎 entra na fila mesmo sem correção: o aprovador escreve a resposta correta ao aprovar
        texto = _texto_feedback(origem, ag["nome"], pergunta, resposta, sem_correcao=True)
        justificativa, nome = "Resposta avaliada como ruim (👎) no chat, sem correção." + obs, f"correcao-{message_id[:8]}.md"
    e = _criar_envio_texto(ag, username, origem, nome, texto, justificativa, message_id=message_id,
                           conversation_id=conversation_id, pergunta=pergunta[:8000], resposta_original=resposta[:20000])
    return _publico(e, user)


class CorrecaoIn(BaseModel):
    agente_id: int
    message_id: str = Field(min_length=8, max_length=64)
    conversation_id: str = Field(min_length=8, max_length=64)
    correcao: str = Field(min_length=1, max_length=8000)
    comentario: str = Field(default="", max_length=1000)


@router.post("/api/conhecimento/correcao")
async def corrigir_resposta(dados: CorrecaoIn, user: dict = Depends(usuario_atual)):
    """👎 com correção: a resposta correta escrita pela pessoa vai para aprovação."""
    ag = db.get_agente(dados.agente_id)
    if not ag or not ag["ativo"] or not db.tem_acesso(user["username"], dados.agente_id):
        raise HTTPException(403, "Você não tem acesso a este agente.")
    if not agente_recebe(ag):
        raise HTTPException(400, "Este agente não está recebendo conhecimento.")
    if not RX_ID_DIFY.fullmatch(dados.message_id) or not RX_ID_DIFY.fullmatch(dados.conversation_id):
        raise HTTPException(400, "Identificador inválido.")
    correcao = texto_visivel(dados.correcao)
    if len(correcao) < 10:
        raise HTTPException(400, "Escreva a resposta correta (pelo menos 10 caracteres).")
    try:
        m = await _mensagem_no_dify(ag, user["username"], dados.conversation_id, dados.message_id)
    except dify.DifyErro as e:
        raise HTTPException(404 if e.status == 404 else 502, "Não foi possível localizar a resposta no Dify.")
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível falar com o Dify.")
    pergunta, original = (m.get("query") or "").strip(), (m.get("answer") or "").strip()
    if _parecidos(correcao, original):
        raise HTTPException(400, "A correção está igual à resposta do agente.")
    # 2) corrigir implica avaliar como ruim: registra o 👎 no Dify também (se ainda não estava)
    try:
        await dify.feedback(ag["chave"], user["username"], dados.message_id, "dislike", "")
    except (dify.DifyErro, httpx.HTTPError) as erro:
        log.warning("não registrei o 👎 da correção no Dify: %s", erro)
    _cancelar_feedback_aberto(dados.message_id, user["username"], "feedback_positivo", "trocada por uma correção")
    # uma correção por resposta: mandar outra substitui a que ainda não foi decidida
    _cancelar_feedback_aberto(dados.message_id, user["username"], "feedback_negativo", "substituída por uma correção nova")
    justificativa = "Correção enviada após avaliar a resposta como ruim (👎) no chat." + (
        f" Comentário: {dados.comentario.strip()}" if dados.comentario.strip() else "")
    e = _criar_envio_texto(ag, user["username"], "feedback_negativo", f"correcao-{dados.message_id[:8]}.md",
                           _texto_feedback("feedback_negativo", ag["nome"], pergunta, correcao), justificativa,
                           message_id=dados.message_id, conversation_id=dados.conversation_id, pergunta=pergunta[:8000],
                           resposta_original=original[:20000], resposta_corrigida=correcao)
    return _publico(e, user)


# ------------------------------------------------------------------ escolher uma base que já existe no Dify
RX_UUID = re.compile(r"[0-9a-fA-F-]{32,36}")


async def _nome_da_base(base_id: str, chave: str | None = None) -> str:
    """Confere a base no Dify (com a chave informada ou a cadastrada) e devolve o nome."""
    if not RX_UUID.fullmatch(base_id):
        raise HTTPException(400, f"ID de base inválido: {base_id[:40]} (copie o código do endereço da base no Dify, "
                                 "logo depois de /datasets/).")
    try:
        base = await dify.base_info(base_id, chave)
    except dify.DifyErro as e:
        if e.status in (401, 403, 404):
            raise HTTPException(400, "A chave não alcança essa base, ou o ID está errado. Se a chave for restrita, inclua "
                                     "a base na restrição da chave no Dify.")
        raise HTTPException(502, f"O Dify não respondeu como esperado ({e.mensagem}).")
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível falar com o Dify.")
    if base.get("provider") == "external":
        raise HTTPException(400, "Bases externas não recebem arquivos.")
    return str(base.get("name") or base_id)


def _em_uso() -> dict[str, list[str]]:
    em_uso: dict[str, list[str]] = {}
    for a in db.list_agentes():
        if a.get("dataset_id"):
            em_uso.setdefault(a["dataset_id"], []).append(f"{a['nome']} (arquivos)" if a.get("dataset_feedback_id")
                                                          and a["dataset_feedback_id"] != a["dataset_id"] else a["nome"])
        if a.get("dataset_feedback_id") and a["dataset_feedback_id"] != a.get("dataset_id"):
            em_uso.setdefault(a["dataset_feedback_id"], []).append(f"{a['nome']} (feedbacks)")
    return em_uso


class BaseIn(BaseModel):
    base_id: str = Field(min_length=1, max_length=64)


@router.post("/api/admin/conhecimento/bases")
async def adicionar_base(dados: BaseIn, admin: dict = Depends(exigir_admin)):
    """+ Adicionar base de conhecimento: confere no Dify e cadastra (fica disponível para escolher nos agentes)."""
    _exigir_chave_ds()
    base_id = dados.base_id.strip()
    bases = bases_cadastradas()
    if base_id in [b["id"] for b in bases]:
        raise HTTPException(409, "Essa base já está cadastrada.")
    nome = await _nome_da_base(base_id)
    bases.append({"id": base_id, "nome": nome})
    _gravar_bases(bases)
    db.auditar(admin["username"], "adicionar_base", base_id, nome)
    return {"id": base_id, "nome": nome}


@router.delete("/api/admin/conhecimento/bases/{base_id}")
def remover_base(base_id: str, admin: dict = Depends(exigir_admin)):
    bases = bases_cadastradas()
    alvo = next((b for b in bases if b["id"] == base_id), None)
    if not alvo:
        raise HTTPException(404, "Base não cadastrada.")
    usada = _em_uso().get(base_id)
    if usada:
        raise HTTPException(400, f"A base está em uso por: {', '.join(usada)}. Troque a base desses agentes antes.")
    _gravar_bases([b for b in bases if b["id"] != base_id])
    db.auditar(admin["username"], "remover_base", base_id, alvo["nome"])
    return {"ok": True}


@router.get("/api/admin/conhecimento/bases")
async def bases_do_dify(cadastradas: int = 0, admin: dict = Depends(exigir_admin)):
    """Bases para escolher nos agentes: as cadastradas em "Bases de conhecimento"; com chave geral e nada cadastrado,
    todas as do Dify. cadastradas=1: só as cadastradas (para a lista da tela)."""
    _exigir_chave_ds()
    salvas = bases_cadastradas()
    try:
        if salvas or cadastradas or chave_restrita():
            bases = []
            for b in salvas:
                try:
                    bases.append(await dify.base_info(b["id"]))
                except dify.DifyErro:  # a chave não alcança mais: mostra assim mesmo, para o admin resolver
                    bases.append({"id": b["id"], "name": b["nome"], "indisponivel": True})
        else:
            bases = await dify.bases_listar()
    except dify.DifyErro as e:
        raise HTTPException(502, f"O Dify não listou as bases ({e.mensagem}).")
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível falar com o Dify.")
    quarentena = db.get_meta("quarentena_dataset")
    em_uso = _em_uso()
    return {"pode_criar": not chave_restrita(), "itens": [{"id": b["id"], "nome": b.get("name") or "", "documentos": b.get("document_count"),
                       "tecnica": b.get("indexing_technique"), "agentes": em_uso.get(b["id"], []),
                       "indisponivel": bool(b.get("indisponivel"))}
                      for b in bases if b.get("id") != quarentena and b.get("provider", "vendor") != "external"]}


async def validar_base(dataset_id: str) -> str:
    """Confere se a base escolhida existe no Dify e pode receber conhecimento (nunca a quarentena)."""
    _exigir_chave_ds()
    dataset_id = dataset_id.strip()
    if not RX_UUID.fullmatch(dataset_id):
        raise HTTPException(400, "Base de conhecimento inválida.")
    if dataset_id == db.get_meta("quarentena_dataset"):
        raise HTTPException(400, "A base de quarentena não pode receber conhecimento aprovado.")
    if chave_restrita() and dataset_id not in bases_restritas():
        raise HTTPException(400, "Essa base não está cadastrada. Cadastre em \"Bases de conhecimento\" (botão "
                                 "\"+ Adicionar base de conhecimento\") antes de escolher no agente.")
    try:
        base = await dify.base_info(dataset_id)
    except dify.DifyErro as e:
        raise HTTPException(400, "Essa base não foi encontrada no Dify (ou a chave não tem acesso a ela)."
                            if e.status in (403, 404) else f"O Dify não respondeu como esperado ({e.mensagem}).")
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível falar com o Dify.")
    if base.get("provider") == "external":
        raise HTTPException(400, "Bases externas não recebem arquivos.")
    return dataset_id
