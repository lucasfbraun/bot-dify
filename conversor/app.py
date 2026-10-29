"""Conversor isolado da plataforma: Office/imagem -> PDF (LibreOffice) e OCR (OCRmyPDF + Tesseract).

Roda em container próprio, sem segredos, sem acesso ao banco e sem saída para a internet (rede interna do
docker compose). Recebe o arquivo no corpo da requisição e devolve um PDF com camada de texto, para o Dify
conseguir ler também o texto que está em imagens (PDF escaneado, prints em slides, fotos de documentos).
"""
import asyncio
import base64
import logging
import os
import subprocess
import tempfile

import pypdfium2 as pdfium
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

log = logging.getLogger("conversor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MAX_MB = int(os.getenv("CONVERSOR_MAX_MB", "60"))
TIMEOUT_OFFICE = int(os.getenv("CONVERSOR_TIMEOUT_OFFICE", "180"))
TIMEOUT_OCR = int(os.getenv("CONVERSOR_TIMEOUT_OCR", "900"))
IDIOMAS = os.getenv("OCR_IDIOMAS", "por+eng")
OFFICE = {"doc", "docx", "ppt", "pptx", "odt", "odp", "rtf", "xlsx", "xls"}  # planilhas: só para a prévia do texto
IMAGEM = {"jpg", "jpeg", "png"}
ACEITOS = OFFICE | IMAGEM | {"pdf"}
_fila = asyncio.Semaphore(int(os.getenv("CONVERSOES_SIMULTANEAS", "2")))  # LibreOffice/OCR pesam na CPU

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class ErroConversao(Exception):
    def __init__(self, status: int, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem


def _rodar(cmd: list[str], timeout: int, cwd: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        raise ErroConversao(504, f"A conversão passou de {timeout} s.")


def _ocr(entrada: str, saida: str, imagem: bool, pasta: str) -> str:
    """Aplica OCR. Em PDF digital, --redo-ocr lê só o que é imagem e mantém o texto que já existe.
    Devolve 'aplicado', 'parcial' (fallback --skip-text) ou 'falhou' (fica o PDF sem OCR)."""
    base = ["ocrmypdf", "-l", IDIOMAS, "--output-type", "pdf", "--optimize", "1", "--jobs", "2", "--quiet"]
    tentativas = [base + ["--image-dpi", "300"]] if imagem else [base + ["--redo-ocr"], base + ["--skip-text"]]
    for i, cmd in enumerate(tentativas):
        r = _rodar(cmd + [entrada, saida], TIMEOUT_OCR, pasta)
        if r.returncode == 0:
            return "aplicado" if i == 0 else "parcial"
        if r.returncode == 8:
            raise ErroConversao(422, "O PDF está protegido por senha. Envie uma versão sem senha.")
        log.warning("ocrmypdf saiu com %s: %s", r.returncode, (r.stderr or "")[-400:])
    return "falhou"


def _ler_texto(pdf: str) -> tuple[int, str]:
    """(páginas, texto) do PDF, uma página por bloco."""
    doc = pdfium.PdfDocument(pdf)
    try:
        return len(doc), "\n\n".join(p.get_textpage().get_text_bounded().strip() for p in doc)
    finally:
        doc.close()


def _contar_texto(pdf: str) -> tuple[int, int]:
    paginas, texto = _ler_texto(pdf)
    return paginas, len("".join(texto.split()))


ASSINATURAS = {  # segunda barreira (a plataforma já confere antes): o conteúdo tem que bater com a extensão
    "docx": b"PK\x03\x04", "pptx": b"PK\x03\x04", "odt": b"PK\x03\x04", "odp": b"PK\x03\x04",
    "doc": b"\xd0\xcf\x11\xe0", "ppt": b"\xd0\xcf\x11\xe0", "rtf": b"{\\rtf",
    "png": b"\x89PNG", "jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff",
    "xlsx": b"PK\x03\x04", "xls": b"\xd0\xcf\x11\xe0",
}


def converter_arquivo(dados: bytes, ext: str, ocr: bool = True) -> tuple[bytes, dict]:
    if ext in ASSINATURAS and not dados.lstrip()[:8].startswith(ASSINATURAS[ext]):
        raise ErroConversao(422, "O conteúdo do arquivo não corresponde à extensão.")
    if ext == "pdf" and b"%PDF-" not in dados[:1024]:
        raise ErroConversao(422, "O conteúdo do arquivo não corresponde à extensão.")
    with tempfile.TemporaryDirectory(prefix="conv-") as pasta:
        entrada = os.path.join(pasta, f"entrada.{ext}")
        with open(entrada, "wb") as f:
            f.write(dados)
        pdf = entrada
        if ext in OFFICE:
            # perfil próprio por conversão: permite conversões em paralelo e não deixa resto entre arquivos
            r = _rodar(["soffice", f"-env:UserInstallation=file://{pasta}/perfil", "--headless", "--norestore",
                        "--nolockcheck", "--convert-to", "pdf", "--outdir", pasta, entrada], TIMEOUT_OFFICE, pasta)
            pdf = os.path.join(pasta, "entrada.pdf")
            if r.returncode != 0 or not os.path.exists(pdf):
                log.warning("soffice saiu com %s: %s", r.returncode, (r.stderr or r.stdout or "")[-400:])
                raise ErroConversao(422, "Não foi possível abrir o arquivo (corrompido ou formato não suportado).")
        saida = os.path.join(pasta, "saida.pdf")
        if ocr or ext in IMAGEM:
            situacao = _ocr(pdf, saida, ext in IMAGEM, pasta)
        else:
            situacao = "nao_aplicado"  # só a prévia do texto (planilha/DOCX sem imagem vão como estão para o Dify)
        final = saida if situacao not in ("falhou", "nao_aplicado") else pdf
        if ext in IMAGEM and situacao == "falhou":
            raise ErroConversao(422, "Não foi possível ler a imagem.")
        try:
            paginas, texto = _ler_texto(final)
        except Exception:  # noqa: BLE001
            raise ErroConversao(422, "O PDF gerado está inválido.")
        with open(final, "rb") as f:
            return f.read(), {"ocr": situacao, "paginas": paginas, "caracteres": len("".join(texto.split())), "texto": texto}


@app.get("/saude")
def saude():
    return {"ok": True}


async def _receber(request: Request, nome: str) -> tuple[bytes, str]:
    ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if ext not in ACEITOS:
        raise HTTPException(415, "Tipo de arquivo não convertido aqui.")
    limite = MAX_MB * 1024 * 1024
    dados = bytearray()
    async for parte in request.stream():
        dados += parte
        if len(dados) > limite:
            raise HTTPException(413, "Arquivo grande demais para converter.")
    if not dados:
        raise HTTPException(400, "Arquivo vazio.")
    return bytes(dados), ext


@app.post("/processar")
async def processar(request: Request, nome: str, ocr: int = 1):
    """PDF com OCR (base64) + o texto extraído, numa chamada só. ocr=0: só converte para ler o texto."""
    dados, ext = await _receber(request, nome)
    async with _fila:
        try:
            pdf, info = await asyncio.to_thread(converter_arquivo, dados, ext, bool(ocr))
        except ErroConversao as e:
            raise HTTPException(e.status, e.mensagem)
    log.info("processado .%s (%d KB) -> %d pág., %d caracteres, OCR %s", ext, len(dados) // 1024, info["paginas"],
             info["caracteres"], info["ocr"])
    return {**info, "pdf": base64.b64encode(pdf).decode()}


@app.post("/converter")
async def converter(request: Request, nome: str):
    ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if ext not in ACEITOS:
        raise HTTPException(415, "Tipo de arquivo não convertido aqui.")
    limite = MAX_MB * 1024 * 1024
    dados = bytearray()
    async for parte in request.stream():
        dados += parte
        if len(dados) > limite:
            raise HTTPException(413, "Arquivo grande demais para converter.")
    if not dados:
        raise HTTPException(400, "Arquivo vazio.")
    async with _fila:
        try:
            pdf, info = await asyncio.to_thread(converter_arquivo, bytes(dados), ext)
        except ErroConversao as e:
            raise HTTPException(e.status, e.mensagem)
    log.info("convertido .%s (%d KB) -> PDF %d KB, %d pág., %d caracteres, OCR %s", ext, len(dados) // 1024,
             len(pdf) // 1024, info["paginas"], info["caracteres"], info["ocr"])
    return Response(pdf, media_type="application/pdf", headers={
        "X-OCR": info["ocr"], "X-Paginas": str(info["paginas"]), "X-Caracteres": str(info["caracteres"])})
