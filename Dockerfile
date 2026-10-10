FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
# HuggingFace local embeddings (free, no API key): cache + quiet tokenizers
ENV HF_HOME=/app/.hf_cache
ENV TOKENIZERS_PARALLELISM=false

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure runtime directories exist (audit.db, uploads, HF model cache)
RUN mkdir -p data uploads data/sample_kb .hf_cache

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8080}/api/health')" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]