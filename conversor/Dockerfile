# Conversor isolado: LibreOffice (Office -> PDF) + OCRmyPDF/Tesseract (OCR em português e inglês).
# Não recebe segredos nem volume; roda só na rede interna do docker compose.
FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    OMP_THREAD_LIMIT=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-impress-nogui \
      ocrmypdf tesseract-ocr-por tesseract-ocr-eng \
      fonts-dejavu fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi==0.162.0 uvicorn==0.54.0 pypdfium2==4.30.0

WORKDIR /app
COPY app.py .
RUN useradd --system --uid 10002 --no-create-home conversor
USER conversor
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/saude', timeout=4)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
