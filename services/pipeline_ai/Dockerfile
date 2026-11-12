# Imagen base fijada por digest (multi-arquitectura: amd64 en la laptop, arm64 en la Mac).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Parches de seguridad de Debian publicados después de la imagen base (Trivy en CI
# los exige): la imagen oficial tarda días en reconstruirse tras un aviso.
RUN apt-get update --error-on=any \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Dependencias exactas con hash (requirements.lock): la misma versión de cada
# paquete en la laptop, en el CI y en la Mac. Regenerar: make lock / dev.ps1 lock
COPY requirements.lock .
RUN pip install --no-deps --require-hashes -r requirements.lock

# Usuario sin privilegios: el contenedor nunca corre como root.
RUN useradd --system --uid 10001 --no-create-home mhia \
    && mkdir -p /app/data /app/credentials /app/backups \
    && chown -R mhia /app/data /app/backups

COPY pipeline_ai ./pipeline_ai
COPY eval ./eval

USER mhia
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "pipeline_ai.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
