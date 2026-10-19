FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Usuario sin privilegios: el contenedor nunca corre como root.
RUN useradd --system --uid 10001 --no-create-home mhia \
    && mkdir -p /app/data /app/credentials \
    && chown -R mhia /app/data

COPY pipeline_ai ./pipeline_ai
COPY eval ./eval

USER mhia
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "pipeline_ai.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
