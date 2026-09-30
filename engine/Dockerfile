# Build from the repo root: docker build -f engine/Dockerfile .
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 HF_HOME=/models/hf
WORKDIR /app

# CPU-only torch first, so the models extra does not pull CUDA wheels.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

# Dependencies only (cached across code changes): install the packages from
# their pyproject files, then drop them; code is installed below with --no-deps.
COPY common/pyproject.toml /tmp/common/pyproject.toml
COPY engine/pyproject.toml /tmp/engine/pyproject.toml
ARG ENGINE_EXTRAS=models,laya
RUN mkdir -p /tmp/common/guardrail_common /tmp/engine/guardrail_engine \
 && pip install /tmp/common "/tmp/engine[${ENGINE_EXTRAS}]" \
 && pip uninstall -y guardrail-common guardrail-engine \
 && python -m spacy download ja_core_news_trf \
 && rm -rf /tmp/common /tmp/engine

COPY common /app/common
COPY engine/pyproject.toml /app/engine/pyproject.toml
COPY engine/guardrail_engine /app/engine/guardrail_engine
RUN pip install --no-deps /app/common /app/engine

EXPOSE 8080
CMD ["uvicorn", "guardrail_engine.main:app", "--host", "0.0.0.0", "--port", "8080"]
