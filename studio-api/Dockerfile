# Build from the repo root: docker build -f studio-api/Dockerfile .
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY common /app/common
COPY studio-api/pyproject.toml /app/studio-api/pyproject.toml
COPY studio-api/guardrail_studio /app/studio-api/guardrail_studio
RUN pip install /app/common /app/studio-api
COPY samples /app/samples

ENV STUDIO_DB=/data/studio.db STUDIO_SAMPLES_DIR=/app/samples
EXPOSE 8000
CMD ["uvicorn", "guardrail_studio.main:app", "--host", "0.0.0.0", "--port", "8000"]
