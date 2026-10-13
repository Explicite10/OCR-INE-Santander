FROM python:3.12-slim

# Librerías del sistema que necesitan OpenCV / PaddlePaddle
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PADDLE_PDX_CACHE_HOME=/opt/paddlex

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Descarga los modelos de PaddleOCR dentro de la imagen (el contenedor no necesita internet para el OCR)
RUN python -c "from app.ocr import engine; engine.warmup()" \
    && useradd --create-home --uid 1000 ine \
    && chown -R ine:ine /opt/paddlex /app

USER ine
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8).status == 200 else 1)"

# 1 worker: cada worker carga su propio PaddleOCR; la concurrencia se controla con MAX_CONCURRENT_JOBS
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
