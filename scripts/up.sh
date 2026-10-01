#!/usr/bin/env bash
# Start the stack. Secrets are only passed to docker compose through the
# environment, never written to disk.
#
# Konnect mode (default, GUARDRAIL_DEPLOY_TARGET=konnect):
#   OpenAI key   ~/.openai/creds  (override: OPENAI_CREDS_FILE) -> kong-dp
#   Konnect PAT  ~/.kong/kpat     (override: KONNECT_PAT_FILE)  -> studio-api
#   DP cert/key  kong/certs/                                     -> kong-dp
#   Prerequisite: guardrail_studio.bootstrap
#
# Gateway mode (GUARDRAIL_DEPLOY_TARGET=gateway):
#   Uses kong-enterprise/docker Admin/Proxy (no local kong-dp).
#   KONG_ADMIN_TOKEN (default kongadmin). LM Studio for Llama Guard + chat LLM.
set -euo pipefail
cd "$(dirname "$0")/.."

target="${GUARDRAIL_DEPLOY_TARGET:-konnect}"

if [[ "$target" == "gateway" ]]; then
  export GUARDRAIL_DEPLOY_TARGET=gateway
  export GUARDRAIL_ENGINE_URL="${GUARDRAIL_ENGINE_URL:-http://host.docker.internal:18080}"
  export ENGINE_URL="${ENGINE_URL:-http://guardrail-engine:8080}"
  export KONG_PROXY_URL="${KONG_PROXY_URL:-http://host.docker.internal:8000}"
  export KONG_PUBLIC_URL="${KONG_PUBLIC_URL:-http://localhost:8000}"
  export KONG_ADMIN_URL="${KONG_ADMIN_URL:-http://host.docker.internal:8001}"
  export KONG_ADMIN_TOKEN="${KONG_ADMIN_TOKEN:-kongadmin}"
  export KONG_WORKSPACE="${KONG_WORKSPACE:-guardrail}"
  export KONG_SERVICE_URL="${KONG_SERVICE_URL:-http://mockbin:8080}"
  export LLAMAGUARD_BACKEND="${LLAMAGUARD_BACKEND:-openai_completions}"
  export LLAMAGUARD_BASE_URL="${LLAMAGUARD_BASE_URL:-http://host.docker.internal:1234}"
  export LLAMAGUARD_MODEL="${LLAMAGUARD_MODEL:-llama-guard-3-8b-imat}"
  # LM Studio API key: LM_STUDIO_API_KEY or one-line file ~/.lmstudio/creds
  lms_creds="${LM_STUDIO_CREDS_FILE:-$HOME/.lmstudio/creds}"
  if [[ -z "${LM_STUDIO_API_KEY:-}" && -r "$lms_creds" ]]; then
    LM_STUDIO_API_KEY="$(tr -d '[:space:]' < "$lms_creds")"
  fi
  if [[ -n "${LM_STUDIO_API_KEY:-}" ]]; then
    export LLAMAGUARD_API_KEY="$LM_STUDIO_API_KEY"
    export LM_STUDIO_AUTH_HEADER="Bearer ${LM_STUDIO_API_KEY}"
  fi
  export KONNECT_PAT="${KONNECT_PAT:-}"
  exec docker compose up -d "$@"
fi

creds="${OPENAI_CREDS_FILE:-$HOME/.openai/creds}"
[[ -f kong/konnect.env && -f kong/certs/tls.crt ]] || {
  echo "missing kong/konnect.env or certs: run guardrail_studio.bootstrap first" >&2; exit 1; }
[[ -r "$creds" ]] || { echo "cannot read $creds" >&2; exit 1; }

OPENAI_AUTH_HEADER="Bearer $(tr -d '[:space:]' < "$creds")"
KONG_CLUSTER_CERT="$(cat kong/certs/tls.crt)"
KONG_CLUSTER_CERT_KEY="$(cat kong/certs/tls.key)"
pat="${KONNECT_PAT_FILE:-$HOME/.kong/kpat}"
[[ -r "$pat" ]] || { echo "cannot read $pat (Konnect PAT for studio-api)" >&2; exit 1; }
KONNECT_PAT="$(tr -d '[:space:]' < "$pat")"
export OPENAI_AUTH_HEADER KONG_CLUSTER_CERT KONG_CLUSTER_CERT_KEY KONNECT_PAT
export GUARDRAIL_DEPLOY_TARGET=konnect
exec docker compose --profile konnect-dp up -d "$@"
