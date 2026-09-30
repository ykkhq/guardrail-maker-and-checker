#!/usr/bin/env bash
# Start the stack. Secrets are only passed to docker compose through the
# environment, never written to disk:
#   OpenAI key   ~/.openai/creds  (override: OPENAI_CREDS_FILE) -> kong-dp
#   Konnect PAT  ~/.kong/kpat     (override: KONNECT_PAT_FILE)  -> studio-api
#   DP cert/key  kong/certs/                                     -> kong-dp
set -euo pipefail
cd "$(dirname "$0")/.."

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
exec docker compose up -d "$@"
